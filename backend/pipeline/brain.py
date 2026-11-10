"""
BimaVani — Brain Layer: Staged Indian Helpline Conversation (+ optional LLM)
===========================================================================
Stage [4] Tools + Memory of the 8-stage pipeline.

Conducts a natural multi-turn Indian RSA/Insurance conversation in 3 stages,
always replying in <= 2 short spoken Hinglish clauses:
  STAGE 1 — Injury & Emergency Triage (112 Ambulance + Police + 1033 NHAI)
  STAGE 2 — Indian Road Type, Drivability & Smart RSA Dispatch (A / B / C)
  STAGE 3 — Policy Number, IRDAI FNOL Draft & Voice Confirmation

Optional OpenAI-compatible LLM assist (.env): falls back instantly to the
100% deterministic Hinglish engine (zero hallucination, zero LLM required).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.rsa_and_claim_rules import (
    IRDAI,
    PARTS,
    POLICIES,
    calculate_irdai_claim_draft,
    compose_agent_reply,
    confirm_detection,
    detect_drivability,
    dispatch_smart_rsa,
    extract_negated_parts,
    extract_parts,
    extract_policy_number,
    extract_road_type,
    spoken_part,
    triage_injury_and_safety,
)
from backend.pipeline.clause_buffer import ClauseBatch, ClauseBuffer, split_into_clauses
from backend.translit import normalize_for_matching

_MEMORY_PATH = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "data" / "call_history.json"
_HISTORY_LOCK = threading.Lock()

GREETING_CLAUSE_1 = "Namaste! Main BimaVani Roadside Helpline se bol rahi hoon."
GREETING_CLAUSE_2 = "Sabse pehle batayein—kya aap aur gaadi mein baithe sabhi log safe hain, ya kisi ko chot lagi hai?"

# Stage tokens
ST_SAFETY_TRIAGE = "safety_triage"
ST_INCIDENT = "incident"
ST_POLICY = "policy_fnol"
ST_CONFIRM = "confirm"
ST_EMERGENCY = "emergency_handoff"
ST_DONE = "done"

SAFE_ACK = re.compile(
    r"\b(?:safe|sabhi thik|sabhi theek|sab thik|sab theek|kisi ko .*nahi|nahi hai chot|unhurt|haan safe)\b",
    re.I,
)

_PROBLEM_SIGNALS = (
    "problem", "dikkat", "takleef", "madad", "help", "kharab", "kharaab",
    "band", "start nahi", "chal nahi", "ruk gayi", "breakdown", "accident",
    "crash", "tow", "smoke", "leak", "awaz", "awaaz", "issue",
    "phat", "toot", "tut", "gir", "stuck", "hang", "fail", "dead", "khatam",
)

_ROAD_HINT_RE = re.compile(
    r"\b(?:nh[-\s]?\d+|nh|national highway|highway|haaiway|haaive|haiwe|"
    r"expressway|expresway|gt road|kmp|ring road|city|sheher|shahar|"
    r"ga[ao]nv?|gaav|village|link road|local road|rural|kaccha|kachcha|"
    r"delhi|new delhi|dilli|mumbai|karnal|panipat|chennai|meerut|"
    r"noida|gurgaon|gurugram|faridabad|chandigarh|ambala|sonipat|jaipur|lucknow|pune)\b",
    re.I,
)
_EXPLICIT_NON_NH_RE = re.compile(
    r"\b(?:city|sheher|shahar|ga[ao]nv?|gaav|village|link road|local road|rural|kaccha|kachcha)\b",
    re.I,
)
_NH_TERM_RE = re.compile(
    r"\b(?:nh[-\s]?\d*|national highway|highway|haaiway|haaive|haiwe|expressway|expresway|gt road|kmp|ring road)\b",
    re.I,
)
_PAUSE_RE = re.compile(r"\b(?:ruko|ruk ja|rukja|hold on|pause|ek second|ek minute)\b", re.I)
_INTERRUPT_CORRECTION_RE = re.compile(r"\b(?:galat|wrong|nahi|nai|badlo|change|correct|sahi|actually|asli)\b", re.I)
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def _looks_like_vehicle_problem(low: str, parts: List[str],
                                triage: Dict[str, Any]) -> bool:
    if parts or triage.get("injured"):
        return True
    return any(re.search(r"(?<![a-z])" + re.escape(k), low) for k in _PROBLEM_SIGNALS)


# ---------------------------------------------------------------------------
# Memory: Short-term (per call) + Long-term (call_history.json)
# ---------------------------------------------------------------------------

@dataclass
class CallMemory:
    call_id: str
    utterances: List[Dict[str, Any]] = field(default_factory=list)
    entities: Dict[str, Any] = field(default_factory=dict)
    ticket_status: str = "DRAFT"            # DRAFT -> CONFIRM_PENDING -> ✅ APPLIED & DISPATCHED
    incident_speakable: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    @property
    def stage(self) -> str:
        return self.entities.get("stage", "")

    def set_stage(self, s: str) -> None:
        self.entities["stage"] = s

    def push_utterance(self, role: str, text: str, meta: Optional[Dict[str, Any]] = None) -> None:
        self.utterances.append({"role": role, "text": text,
                                "at": time.time(), **(meta or {})})
        if len(self.utterances) > 60:
            self.utterances = self.utterances[-60:]

    def mark_last_agent_interrupted(self, spoken_prefix: str = "", full_reply: str = "") -> Optional[str]:
        """Tag the interrupted agent turn in conversation history with [interrupted]."""
        for idx in range(len(self.utterances) - 1, -1, -1):
            u = self.utterances[idx]
            if str(u.get("role", "")).startswith("agent"):
                orig = str(u.get("text") or full_reply or "").strip()
                if u.get("interrupted"):
                    return orig
                prefix = (spoken_prefix or "").strip()
                if prefix and orig and len(prefix) < len(orig):
                    tagged = f"[interrupted] {prefix} ... (cut off before finishing: {orig[len(prefix):].strip()})"
                else:
                    tagged = f"[interrupted] {orig or '(agent output cut off)'}"
                u["text"] = tagged
                u["interrupted"] = True
                u["interrupted_at"] = time.time()
                self.entities["interrupted_agent_text"] = orig
                return orig
        if full_reply:
            tagged = f"[interrupted] {full_reply.strip()}"
            self.push_utterance("agent", tagged, {"interrupted": True, "interrupted_at": time.time()})
            self.entities["interrupted_agent_text"] = full_reply.strip()
            return full_reply.strip()
        return None

    def dump_mic_interruption(
        self,
        mic_text: str = "",
        spoken_prefix: str = "",
        full_reply: str = "",
        audio_stats: Optional[Dict[str, Any]] = None,
        source: str = "mic_barge_in",
    ) -> Dict[str, Any]:
        """Dump detected mic input / speech onset into model context tagged [interrupted]."""
        agent_cut = self.mark_last_agent_interrupted(spoken_prefix=spoken_prefix, full_reply=full_reply)
        clean_mic = (mic_text or "").strip()
        stats = dict(audio_stats or {})
        if clean_mic:
            dump_text = f"[interrupted] {clean_mic}"
        else:
            dur = stats.get("duration_ms", 0)
            db = stats.get("peak_dbfs", -120)
            dump_text = f"[interrupted] (mic activity detected — {dur}ms speech dumped at {db} dBFS)"
        self.push_utterance("driver", dump_text, {
            "interrupted": True,
            "source": source,
            "audio_stats": stats,
        })
        entry = {
            "tag": "[interrupted]",
            "at": time.time(),
            "source": source,
            "agent_cut": agent_cut,
            "mic_dump": dump_text,
            "raw_transcript": clean_mic,
            "audio_stats": stats,
        }
        log = list(self.entities.get("interruption_log") or [])
        log.append(entry)
        self.entities["interruption_log"] = log[-10:]
        self.entities["last_interruption"] = entry
        self.entities["was_interrupted"] = True
        return entry

    def snapshot(self) -> Dict[str, Any]:
        return {
            "call_id": self.call_id,
            "stage": self.stage,
            "turns": len(self.utterances),
            "entities": self.entities,
            "ticket_status": self.ticket_status,
            "incident_speakable": self.incident_speakable,
        }


def load_long_term() -> List[Dict[str, Any]]:
    try:
        with _HISTORY_LOCK:
            if _MEMORY_PATH.exists() and _MEMORY_PATH.stat().st_size > 2:
                with open(_MEMORY_PATH, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def save_long_term(records: List[Dict[str, Any]]) -> None:
    try:
        with _HISTORY_LOCK:
            _MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_MEMORY_PATH, "w", encoding="utf-8") as fh:
                json.dump(records[-200:], fh, indent=2, ensure_ascii=False)
    except Exception:
        pass


def append_long_term(record: Dict[str, Any]) -> None:
    with _HISTORY_LOCK:
        recs: List[Dict[str, Any]] = []
        try:
            if _MEMORY_PATH.exists() and _MEMORY_PATH.stat().st_size > 2:
                with open(_MEMORY_PATH, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                    if isinstance(loaded, list):
                        recs = loaded
        except Exception:
            recs = []
        recs.append(record)
        try:
            _MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_MEMORY_PATH, "w", encoding="utf-8") as fh:
                json.dump(recs[-200:], fh, indent=2, ensure_ascii=False)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# LLM adapter — OpenAI-compatible with circuit breaker & deterministic fallback
# ---------------------------------------------------------------------------

def _llm_env() -> tuple[Optional[str], str, str]:
    key = os.environ.get("LLM_API_KEY", "").strip()
    base = (os.environ.get("LLM_BASE_URL") or "https://tokenharbor.ai/v1").strip().rstrip("/")
    model = (os.environ.get("BIMAVANI_LLM_MODEL") or "gnani/gnani-evon-v3.3-30B-A3B").strip()
    return (key or None), base, model


def llm_status() -> Dict[str, Any]:
    """Honest brain status for the UI: LLM phrasing on, or deterministic."""
    key, _, model = _llm_env()
    left = max(0, int(_LLM_COOLDOWN_UNTIL - time.time()))
    return {
        "configured": bool(key),
        "model": model,
        "active": bool(key) and left == 0,
        "cooldown_s": left,
        "last_error": _LLM_LAST_ERROR,
        "fail_streak": _LLM_FAIL_STREAK,
        "engine": "llm+deterministic" if (key and left == 0) else "deterministic",
    }


_AGENT_SYSTEM = (
    "SITUATION\n"
    "You are BimaVani, the voice agent on a 24x7 Indian roadside-assistance and "
    "motor-insurance helpline (1-800-BIMA-VANI). The caller is a driver, usually "
    "stranded, stressed and speaking Hinglish, sometimes from a noisy highway or "
    "a village road. You are the calm, experienced person on the other end.\n"
    "\n"
    "YOUR JOB, IN ORDER\n"
    "1. Make sure people are safe (injury first, always).\n"
    "2. Understand the vehicle problem well enough to act.\n"
    "3. Get help moving — on-spot mechanic, tow, or garage — or file the claim.\n"
    "4. Say exactly what happens next and when.\n"
    "\n"
    "HOW YOU SPEAK\n"
    "- Roman Hinglish only (never Devanagari script). Warm, plain, human.\n"
    "- AT MOST 2 short spoken clauses — this is read aloud by TTS.\n"
    "- ONE question per turn. Never re-ask what CONTEXT already answers.\n"
    "- Vary your acknowledgements; never repeat the same opener twice in a row.\n"
    "- No filler, no policy jargon, no numbers you were not given.\n"
    "\n"
    "GROUND TRUTH — HARD GUARDRAILS\n"
    "- Use ONLY facts present in CONTEXT (policy summary, tool results, catalog "
    "  metadata, conversation history). Never invent a policy number, ETA, "
    "  amount, coverage, vendor or vehicle detail.\n"
    "- Workflow A (minor, drivable, on-spot fix): RSA covers it free. Do NOT "
    "  file a claim and do NOT ask for a policy number — that protects the "
    "  caller's 20% No-Claim Bonus.\n"
    "- Workflow B/C (tow, garage, insurance claim): follow recommended_action. "
    "  Ask for the policy number only if it is unknown; otherwise confirm before "
    "  filing.\n"
    "- Injury or emergency: 112 ambulance + police + human handoff. Ask nothing "
    "  else, promise nothing else.\n"
    "- If recommended_action is ask_problem / ask_location / ask_policy / "
    "  ask_confirm, ask exactly that, in your own natural words.\n"
    "- If CONTEXT is thin, ask one clarifying question instead of guessing.\n"
    "- INTERRUPTION RULE: Entries in conversation_history tagged `[interrupted]` "
    "  mean the caller spoke over you while you were speaking/thinking (possibly "
    "  because an assumption, policy, road, or dispatch was wrong). Never repeat "
    "  or defend the cut-off output; trust the caller's latest input/correction.\n"
    "\n"
    "OUTPUT\n"
    "Return ONLY compact JSON: {\"reply\": \"<spoken text>\", \"action\": \"<one of "
    "emergency|ask_safety|ask_problem|ask_location|ask_policy|ask_confirm|"
    "dispatch|close|clarify>\"}"
)


def _parse_llm_json(txt: str) -> Optional[Dict[str, Any]]:
    """Pull {reply, action} out of an LLM response."""
    raw = (txt or "").strip().strip("`").strip()
    if raw.lower().startswith("json"):
        raw = raw[4:].strip()
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and obj.get("reply"):
            return {"reply": str(obj["reply"]).strip(),
                    "action": str(obj.get("action") or "").strip().lower()}
    except Exception:
        pass
    line = raw.splitlines()[0].strip().strip("\"' ") if raw else ""
    return {"reply": line, "action": ""} if line else None


_LLM_COOLDOWN_UNTIL = 0.0
_LLM_LAST_ERROR = ""
_LLM_FAIL_STREAK = 0


async def llm_assist(transcript: str, context: Dict[str, Any],
                     timeout_s: float = 2.5) -> Optional[Dict[str, Any]]:
    """Ask the .env LLM to phrase the turn using full CONTEXT."""
    import httpx
    global _LLM_COOLDOWN_UNTIL, _LLM_LAST_ERROR, _LLM_FAIL_STREAK
    key, base, model = _llm_env()
    if not key or time.time() < _LLM_COOLDOWN_UNTIL:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _AGENT_SYSTEM},
                        {"role": "user",
                         "content": "CONTEXT (ground truth):\n"
                                    f"{json.dumps(context, ensure_ascii=False, default=str)}\n\n"
                                    f'DRIVER SAID: "{transcript}"\n'
                                    "Decide the single best next spoken turn and return JSON."},
                    ],
                    "temperature": 0.4,
                    "max_tokens": 160,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            if isinstance(payload, dict) and payload.get("error"):
                _LLM_LAST_ERROR = str(payload["error"])[:80]
                _LLM_FAIL_STREAK += 1
                _LLM_COOLDOWN_UNTIL = time.time() + 180
                return None
            out = _parse_llm_json(payload["choices"][0]["message"]["content"])
            if out:
                _LLM_FAIL_STREAK = 0
                _LLM_LAST_ERROR = ""
            return out
    except httpx.HTTPStatusError as e:
        code = e.response.status_code if e.response is not None else 0
        _LLM_LAST_ERROR = f"http_{code}"
        _LLM_FAIL_STREAK += 1
        _LLM_COOLDOWN_UNTIL = time.time() + (300 if code in (401, 402, 403) else 120)
        return None
    except Exception as e:
        _LLM_LAST_ERROR = type(e).__name__
        _LLM_FAIL_STREAK += 1
        _LLM_COOLDOWN_UNTIL = time.time() + 120
        return None


async def llm_warmup(timeout_s: float = 3.0) -> None:
    """Cheap boot probe so the first turn never pays a dead-provider timeout."""
    import httpx
    global _LLM_COOLDOWN_UNTIL, _LLM_LAST_ERROR, _LLM_FAIL_STREAK
    key, base, model = _llm_env()
    if not key:
        return
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json={"model": model,
                      "messages": [{"role": "user", "content": "ping"}],
                      "max_tokens": 1},
            )
        if resp.status_code >= 400:
            _LLM_LAST_ERROR = f"http_{resp.status_code}"
            _LLM_FAIL_STREAK += 1
            _LLM_COOLDOWN_UNTIL = time.time() + (300 if resp.status_code in (401, 402, 403) else 120)
            return
        try:
            body = resp.json()
        except Exception:
            body = {}
        if isinstance(body, dict) and body.get("error"):
            _LLM_LAST_ERROR = str(body["error"])[:80]
            _LLM_FAIL_STREAK += 1
            _LLM_COOLDOWN_UNTIL = time.time() + 180
    except Exception as e:
        _LLM_LAST_ERROR = type(e).__name__
        _LLM_FAIL_STREAK += 1
        _LLM_COOLDOWN_UNTIL = time.time() + 120


def _agent_context(mem: "CallMemory", transcript: str,
                   triage: Dict[str, Any], tools: List[Dict[str, Any]],
                   deterministic_reply: str,
                   recommended_action: str) -> Dict[str, Any]:
    parts = list(mem.entities.get("parts") or [])
    catalog = []
    for p in parts:
        meta = PARTS.get(p, {})
        catalog.append({
            "part": p,
            "spoken_name": spoken_part([p]),
            "safety": meta.get("safety"),
            "workflow": meta.get("workflow"),
            "do_not_claim": meta.get("workflow") == "A",
            "ncb_protected": meta.get("workflow") == "A",
        })
    policy_no = mem.entities.get("policy_number")
    policy = POLICIES.get(str(policy_no or "")) or {}
    history = [
        {
            "role": u.get("role"),
            "text": u.get("text"),
            **({"interrupted": True} if u.get("interrupted") else {}),
        }
        for u in mem.utterances[-10:]
    ]
    tool_facts = {}
    for t in tools:
        if t.get("tool") == "triage_injury_and_safety":
            tool_facts["triage"] = {"injured": t.get("injured"),
                                    "safety": t.get("safety_status")}
        elif t.get("tool") == "dispatch_smart_rsa":
            tool_facts["dispatch"] = {
                "workflow": t.get("workflow"),
                "vendor": t.get("dispatch_vendor") or t.get("dispatch_vendor_type"),
                "eta_mins": t.get("eta_mins"),
                "ncb_protected": t.get("ncb_protected"),
                "do_not_start_engine": t.get("do_not_start_engine"),
                "nhai_patrol_alerted": t.get("nhai_patrol_alerted"),
            }
        elif t.get("tool") == "calculate_irdai_claim_draft":
            tool_facts["claim_draft"] = {
                "claim_id": t.get("claim_id"),
                "estimated_range": t.get("estimated_range_display"),
                "status": t.get("status_label"),
            }
    return {
        "persona": "BimaVani — 24x7 Indian roadside assistance + insurance helpline",
        "language": "Roman Hinglish, female voice, max 2 short clauses",
        "conversation_history": history,
        "interrupted_context": bool(mem.entities.get("was_interrupted")),
        "last_interruption": mem.entities.get("last_interruption"),
        "driver_said": transcript,
        "state": {
            "stage": mem.stage,
            "ticket_status": mem.ticket_status,
            "injured": bool(triage.get("injured")),
            "parts": parts,
            "parts_catalog": catalog,
            "road_type": mem.entities.get("road_type"),
            "road_explicitly_known": bool(mem.entities.get("road_explicit")),
            "is_drivable": mem.entities.get("is_drivable"),
            "policy_number": policy_no,
            "policy_known": bool(policy_no),
            "policy_summary": {k: policy.get(k) for k in
                               ("vehicle_make", "vehicle_model", "plan", "idv")
                               if policy.get(k)} if policy else None,
            "confirm_intent": mem.entities.get("confirm_intent"),
        },
        "workflow_semantics": {
            "A": "minor/on-spot; RSA pays; never file insurance; protect 20% NCB",
            "B": "non-drivable; flatbed tow to network garage; insurance claim applies",
            "C": "drivable body damage; self-drive with photos; claim applies",
        },
        "tool_facts": tool_facts,
        "recommended_action": recommended_action,
        "deterministic_reply": deterministic_reply,
        "hard_rules": [
            "Never invent numbers, policies, ETAs, amounts or coverage.",
            "Never ask two questions in one turn.",
            "Never re-ask an answered question (see conversation_history/state).",
            "Workflow A: do not ask for policy number and do not file a claim.",
            "Injury: 112 ambulance + police + human handoff only.",
        ],
    }


def _linguist_fallback_entities(transcript: str, base_entities: Dict[str, Any]) -> Dict[str, Any]:
    return dict(base_entities)


# ---------------------------------------------------------------------------
# Dynamic policy record (accept ANY spoken 4-digit policy number)
# ---------------------------------------------------------------------------

def _dynamic_policy(policy_number: str) -> Dict[str, Any]:
    return {
        "policy_number": str(policy_number),
        "owner": "Caller (voice-verified)",
        "vehicle_make": "Insured Vehicle (dynamic)",
        "vehicle_model": "Voice-Verified Record",
        "vehicle_class": "four_wheeler",
        "fuel": "petrol",
        "registration": "SYN-DYNAMIC",
        "plan": "Dynamic Verified Policy",
        "policy_type": "comprehensive",
        "idv": 600000,
        "ncb_percent": 20,
        "zero_depreciation": False,
        "rsa_plan": "Standard RSA (dynamic)",
        "home_city": "Caller Location",
        "preferred_garage": None,
        "dynamic": True,
    }


# ---------------------------------------------------------------------------
# The Brain
# ---------------------------------------------------------------------------

class Brain:
    """Deterministic staged-conversation orchestrator for stage [4]."""

    def __init__(self, persist_history: bool = True) -> None:
        self.persist_history = persist_history
        self.clause_buffer = ClauseBuffer()
        self.batches: Dict[str, ClauseBatch] = {}
        self.tool_log: List[Dict[str, Any]] = []

    def get_or_create_call(self, call_id: str) -> CallMemory:
        now = time.time()
        if len(_CALLS) > 500:
            stale = [cid for cid, m in _CALLS.items() if (now - m.created_at) > 3600]
            for cid in stale:
                _CALLS.pop(cid, None)
            if len(_CALLS) > 500:
                oldest = sorted(_CALLS.items(), key=lambda kv: kv[1].created_at)[:len(_CALLS) - 450]
                for cid, _ in oldest:
                    _CALLS.pop(cid, None)
        return _CALLS.setdefault(call_id, CallMemory(call_id=call_id))

    def reset_call(self, call_id: str) -> None:
        _CALLS.pop(call_id, None)
        self.batches.pop(call_id, None)
        self.clause_buffer.flush(call_id)

    def record_interruption(
        self,
        call_id: str,
        mic_text: str = "",
        spoken_prefix: str = "",
        full_reply: str = "",
        audio_stats: Optional[Dict[str, Any]] = None,
        source: str = "mic_barge_in",
    ) -> Dict[str, Any]:
        """Mark the interrupted agent output + dump detected mic speech into
        model context tagged with [interrupted]."""
        mem = self.get_or_create_call(call_id)
        entry = mem.dump_mic_interruption(
            mic_text=mic_text,
            spoken_prefix=spoken_prefix,
            full_reply=full_reply or (mem.incident_speakable or ""),
            audio_stats=audio_stats,
            source=source,
        )
        return entry

    async def start_call(self, call_id: str) -> Dict[str, Any]:
        """STAGE 1 opener: marks the call active and returns the 2 greeting clauses."""
        mem = self.get_or_create_call(call_id)
        mem.entities["call_started"] = True
        mem.set_stage(ST_SAFETY_TRIAGE)
        mem.ticket_status = "CALL ACTIVE — STAGE 1 SAFETY TRIAGE"
        reply = f"{GREETING_CLAUSE_1} {GREETING_CLAUSE_2}"
        mem.incident_speakable = reply
        mem.push_utterance("agent(greeting)", reply)
        cl1 = {"index": 1, "text": GREETING_CLAUSE_1,
               "priority": "clause1_fast_hook", "tts_order": 1}
        cl2 = {"index": 2, "text": GREETING_CLAUSE_2,
               "priority": "clause2_details", "tts_order": 2}
        batch = ClauseBatch(turn_id=call_id + "-greet", clauses=[cl1, cl2])
        self.batches[call_id] = batch
        self.clause_buffer.batches[call_id + "-greet"] = batch
        return {
            "call_id": call_id,
            "reply_text": reply,
            "clauses": batch.as_list(),
            "ticket_status": mem.ticket_status,
            "memory": mem.snapshot(),
        }

    async def process_turn(self, call_id: str, transcript: str,
                           stt_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        stt_meta = stt_meta or {}
        match_text = normalize_for_matching(transcript or "")
        low = match_text.lower().strip()
        mem = self.get_or_create_call(call_id)
        mem.push_utterance("driver", transcript, {"stt": stt_meta})

        prior_stage = mem.stage or (ST_SAFETY_TRIAGE if mem.entities.get("call_started") else "")
        prior_policy = mem.entities.get("policy_number")
        prior_parts = list(mem.entities.get("parts") or [])
        prior_road = mem.entities.get("road_type")

        # 1. Policy extraction (accept bare 4-digit reply when in ST_POLICY)
        extracted_pol = extract_policy_number(
            transcript, expecting_policy=(prior_stage == ST_POLICY)
        )
        policy_changed = bool(
            extracted_pol and prior_policy and str(extracted_pol) != str(prior_policy)
        )
        policy_number = extracted_pol or prior_policy
        policy = POLICIES.get(str(policy_number or "")) or (
            _dynamic_policy(policy_number) if policy_number else None
        )

        # 2. Road type extraction (never overwrite an earlier explicit NH with a city mention
        # unless driver explicitly gives non-NH road type or corrects location)
        this_road = bool(_ROAD_HINT_RE.search(low))
        road_explicit = this_road or bool(mem.entities.get("road_explicit"))
        road_type = prior_road
        if this_road:
            candidate_road = extract_road_type(transcript, policy)
            if prior_road == "NH" and candidate_road == "CITY" and not _EXPLICIT_NON_NH_RE.search(low):
                road_type = "NH"
            else:
                road_type = candidate_road
        road_changed = bool(
            this_road and mem.entities.get("last_road_type") and road_type != mem.entities.get("last_road_type")
        )

        # 3. Part extraction + retraction support
        new_parts = extract_parts(transcript)
        retracted_parts = extract_negated_parts(transcript)
        merged_parts = (set(prior_parts) - set(retracted_parts)) | set(new_parts)
        parts = sorted(merged_parts)
        parts_changed = bool(
            (set(parts) != set(prior_parts)) and mem.entities.get("last_parts") is not None
        )

        is_drivable, drivable_reason = detect_drivability(parts, transcript)
        triage = triage_injury_and_safety(transcript)
        confirm = confirm_detection(transcript, prior_policy=prior_policy)
        was_interrupted = bool(mem.entities.get("was_interrupted"))
        has_real_change = policy_changed or road_changed or parts_changed
        explicit_correction = (
            (confirm == "correct")
            or bool(_PAUSE_RE.search(low))
            or (was_interrupted and (has_real_change or bool(_INTERRUPT_CORRECTION_RE.search(low))))
        )

        mem.entities.update({
            "policy_number": policy_number,
            "road_type": road_type,
            "road_explicit": road_explicit,
            "parts": parts,
            "is_drivable": is_drivable,
            "drivable_reason": drivable_reason,
            "injured": triage["injured"],
            "confirm_intent": confirm,
        })

        tools: List[Dict[str, Any]] = [triage]
        draft = None
        dispatch = None

        # ---------------- STAGE 1: EMERGENCY (never skipped) ----------------
        stay_in_emergency = (
            prior_stage == ST_EMERGENCY
            and not (SAFE_ACK.search(low) and not triage["injured"] and bool(parts))
        )
        if triage["injured"] or stay_in_emergency:
            mem.set_stage(ST_EMERGENCY)
            mem.entities["injured"] = True
            mem.ticket_status = "EMERGENCY — 112 AMBULANCE & POLICE DISPATCHED"
            if road_type == "NH" or bool(_NH_TERM_RE.search(low)):
                mem.entities["nhai_patrol"] = True
            dispatch = {
                "tool": "dispatch_smart_rsa", "workflow": "EMERGENCY",
                "workflow_label": "Emergency Medical Escalation",
                "dispatch_vendor": "112 Ambulance + 112 Police + Human Emergency Officer",
                "policy_number": policy_number, "road_type": road_type,
                "parts": parts, "emergency": True,
                "nhai_patrol_alerted": bool(mem.entities.get("nhai_patrol")),
            }
            tools.append(dispatch)
            reply = self._emergency_reply(mem)

        # ---------------- Barge-in / mid-call correction ---------------------
        elif explicit_correction and prior_stage not in ("", ST_EMERGENCY):
            if has_real_change and (parts or mem.entities.get("last_draft")):
                dispatch, draft, reply = self._dispatch_and_readback(mem, low, tools)
                mem.set_stage(ST_CONFIRM)
                mem.ticket_status = "CONFIRM_PENDING (corrected)"
            elif _PAUSE_RE.search(low) and not has_real_change:
                # Caller just asked us to hold on ("ek second ruko") — do NOT mint a new FNOL
                dispatch = mem.entities.get("last_dispatch")
                draft = mem.entities.get("last_draft")
                reply = "Haan ji, main ruki hoon. Jab aap ready hon ya kuch badalna ho toh batayein."
            elif mem.entities.get("last_draft") or (policy_number and parts):
                dispatch, draft, reply = self._dispatch_and_readback(mem, low, tools)
                mem.set_stage(ST_CONFIRM)
                mem.ticket_status = "CONFIRM_PENDING (corrected)"
            else:
                reply = "Ji bilkul, main sun rahi hoon — sahi details batayein, kya badalna hai?"

        # ---------------- STAGE 3 close: confirm ONLY when draft/dispatch ready
        elif confirm == "confirm" and (
            prior_stage == ST_CONFIRM
            or (mem.entities.get("last_draft") is not None and bool(parts))
            or (bool(policy_number) and bool(parts) and road_explicit)
        ):
            minor_a = bool(parts) and all(PARTS.get(p, {}).get("workflow") == "A" for p in parts) and is_drivable
            if minor_a:
                if not mem.entities.get("last_dispatch"):
                    dispatch, _d, reply = self._dispatch_and_readback(mem, low, tools, ask_confirm=False)
                else:
                    dispatch = mem.entities.get("last_dispatch")
                    reply = self._already_done_reply(mem)
                mem.set_stage(ST_DONE)
                mem.ticket_status = "✅ RSA DISPATCHED — NO CLAIM FILED (NCB PROTECTED)"
            else:
                if not mem.entities.get("last_draft") and policy_number and parts:
                    dispatch, draft, _r = self._dispatch_and_readback(mem, low, tools, ask_confirm=False)
                dispatch = dispatch or mem.entities.get("last_dispatch")
                draft = draft or mem.entities.get("last_draft")
                mem.set_stage(ST_DONE)
                mem.ticket_status = "✅ APPLIED & DISPATCHED"
                reply = self._closing_reply(dispatch, draft)

        # ---------------- Follow-up after ST_DONE ----------------------------
        elif prior_stage == ST_DONE and not has_real_change:
            dispatch = mem.entities.get("last_dispatch")
            draft = mem.entities.get("last_draft")
            reply = self._already_done_reply(mem)

        # ---------------- STAGE 2/3 staged progression -----------------------
        else:
            reply, dispatch, draft, mem = self._staged_turn(mem, low, tools, triage)

        recommended = self._recommended_action(mem, triage)
        ctx = _agent_context(mem, transcript, triage, tools, reply, recommended)
        llm = await llm_assist(transcript, ctx)
        if llm and llm.get("reply"):
            candidate = llm["reply"].strip()
            if self._llm_reply_is_safe(candidate, mem, triage, llm.get("action", ""), recommended):
                reply = candidate

        mem.entities["was_interrupted"] = False
        mem.incident_speakable = reply
        mem.push_utterance("agent", reply, {"ticket_status": mem.ticket_status,
                                            "stage": mem.stage})

        batch = self.clause_buffer.ingest(call_id, reply)
        self.batches[call_id] = batch

        if self.persist_history:
            record = {
                "call_id": call_id,
                "at": time.time(),
                "stage": mem.stage,
                "transcript": transcript,
                "entities": mem.entities,
                "ticket_status": mem.ticket_status,
                "reply": reply,
            }
            await asyncio.to_thread(append_long_term, record)

        return {
            "call_id": call_id,
            "stage": mem.stage,
            "transcript": transcript,
            "entities": mem.entities,
            "tools_invoked": tools,
            "memory": mem.snapshot(),
            "reply_text": reply,
            "clauses": batch.as_list(),
            "ticket_status": mem.ticket_status,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _dispatch_and_readback(self, mem: CallMemory, low: str,
                               tools: List[Dict[str, Any]],
                               ask_confirm: bool = True) -> tuple:
        entities = mem.entities
        policy_number = entities.get("policy_number")
        policy = POLICIES.get(str(policy_number or "")) or (
            _dynamic_policy(policy_number) if policy_number else None
        )
        road = entities.get("road_type") or "CITY"
        parts = entities.get("parts", [])
        is_drivable = entities.get("is_drivable", True)
        dispatch = dispatch_smart_rsa(road, parts, is_drivable, policy)
        tools.append(dispatch)
        mem.entities["last_dispatch"] = dispatch
        mem.entities["last_road_type"] = road
        mem.entities["last_parts"] = list(parts)
        mem.entities["last_drivable"] = is_drivable
        draft = None
        if policy_number and dispatch.get("workflow") != "A" and parts:
            draft = calculate_irdai_claim_draft(str(policy_number), parts, policy=policy)
            tools.append(draft)
            mem.entities["last_draft"] = draft
        elif dispatch.get("workflow") == "A":
            mem.entities.pop("last_draft", None)
        reply = compose_agent_reply(
            {"triage": {"injured": False, "spoken_empathy_hi": ""},
             "dispatch": dispatch, "claim_draft": draft},
            ack=self._ack(mem),
            ask_confirm=ask_confirm,
        )
        if not ask_confirm and draft:
            reply += " Dono tickets lock kar diye gaye hain."
        return dispatch, draft, reply

    def _ack(self, mem: CallMemory) -> str:
        driver_turns = sum(1 for u in mem.utterances if u.get("role") == "driver")
        if driver_turns <= 1:
            return "Shukar hai aap safe hain!"
        variants = [
            "Theek hai ji, note kar liya.",
            "Achha ji, samajh gayi.",
            "Haan ji, bilkul.",
            "Samajh gayi ji, aage badhte hain.",
            "Theek hai, main samajh gayi.",
            "Boliye, main sun rahi hoon.",
            "Achha, aage badhte hain.",
            "Haan ji, note kar liya hai.",
        ]
        idx = (driver_turns - 2 + sum(ord(c) for c in mem.call_id)) % len(variants)
        return variants[idx]

    def _recommended_action(self, mem: CallMemory,
                            triage: Dict[str, Any]) -> str:
        e = mem.entities
        if triage.get("injured") or mem.stage == ST_EMERGENCY:
            return "emergency"
        parts = e.get("parts") or []
        if mem.stage == ST_DONE:
            return "close"
        if not parts:
            return "ask_problem"
        if not e.get("road_explicit"):
            return "ask_location"
        minor = (
            bool(parts)
            and all(PARTS.get(p, {}).get("workflow") == "A" for p in parts)
            and e.get("is_drivable", True)
        )
        if minor:
            return "dispatch"
        if not e.get("policy_number"):
            return "ask_policy"
        if mem.stage == ST_CONFIRM:
            return "ask_confirm"
        return "dispatch"

    def _llm_reply_is_safe(
        self,
        text: str,
        mem: CallMemory,
        triage: Dict[str, Any],
        llm_action: str = "",
        recommended_action: str = "",
    ) -> bool:
        """Reject LLM output that drifts from ground truth or violates TTS constraints."""
        if not text or len(text) > 320:
            return False
        if _DEVANAGARI_RE.search(text):
            return False
        if len(split_into_clauses(text).clauses) > 2:
            return False
        # Reject conflicting action override (e.g. closing when policy/problem is still needed)
        if recommended_action in ("ask_problem", "ask_location", "ask_policy") and llm_action in ("close", "dispatch"):
            return False
        low = normalize_for_matching(text).lower()
        e = mem.entities
        parts = e.get("parts") or []
        minor = (
            bool(parts)
            and all(PARTS.get(p, {}).get("workflow") == "A" for p in parts)
            and e.get("is_drivable", True)
        )
        if minor and ("claim file" in low or "claim apply" in low
                      or "policy number" in low or "policy batayein" in low):
            return False
        if not e.get("policy_number") and extract_policy_number(text):
            return False
        if not triage.get("injured") and mem.stage != ST_EMERGENCY and ("112" in low and "ambulance" in low):
            return False
        # Reject hallucinated ₹ figures when there is no claim draft or rural reimbursement
        draft = e.get("last_draft") or {}
        disp = e.get("last_dispatch") or {}
        has_money = bool(draft.get("estimated_range_display") or (disp.get("rural_reimbursement") or {}).get("eligible"))
        if ("₹" in text or re.search(r"\brupaye\b", low)) and not has_money:
            return False
        return True

    def _staged_turn(self, mem: CallMemory, low: str,
                     tools: List[Dict[str, Any]], triage: Dict[str, Any]):
        stage = mem.stage or ST_SAFETY_TRIAGE
        entities = mem.entities
        parts = entities.get("parts", []) or []
        minor_only = (
            bool(parts)
            and all(PARTS.get(p, {}).get("workflow") == "A" for p in parts)
            and entities.get("is_drivable", True)
        )
        part_word = spoken_part(parts)

        has_problem = _looks_like_vehicle_problem(low, parts, triage)

        if stage == ST_SAFETY_TRIAGE and not has_problem:
            mem.set_stage(ST_INCIDENT)
            if entities.get("policy_number"):
                reply = (
                    f"Bahut shukriya ji — policy {entities['policy_number']} note kar li hai. "
                    "Ab bataiye, gaadi ko kya hua?"
                )
            else:
                reply = (
                    "Bahut shukriya ji — aap sab safe hain, yahi sabse badi baat hai. "
                    "Ab bataiye, gaadi ko kya hua?"
                )
            mem.ticket_status = "STAGE 2 — INCIDENT DETAILS IN PROGRESS"
            return reply, None, None, mem

        if has_problem or parts:
            # Generic problem reported ("gaadi band ho gayi" / "kuch issue") without any identified part yet
            if not parts:
                mem.set_stage(ST_INCIDENT)
                mem.ticket_status = "STAGE 2 — PART / DAMAGE DETAILS NEEDED"
                pol_note = f"Policy {entities['policy_number']} note hai. " if entities.get("policy_number") else ""
                reply = (
                    f"{pol_note}Samajh gayi ji — gaadi mein exact kya problem aa rahi hai? "
                    "Kya radiator ya engine ka issue hai, tyre puncture hai, battery dead hai, ya bahar ka dent?"
                )
                return reply, None, None, mem

            if minor_only:
                if stage == ST_DONE and entities.get("last_dispatch"):
                    return self._already_done_reply(mem), entities["last_dispatch"], None, mem
                if not entities.get("road_explicit"):
                    mem.set_stage(ST_INCIDENT)
                    mem.ticket_status = "STAGE 2 — LOCATION NEEDED (minor repair)"
                    reply = (
                        f"{part_word.capitalize() if part_word else 'Achha'}? "
                        "Koi baat nahi ji. Aap abhi city ke andar hain, ya highway par?"
                    )
                    return reply, None, None, mem
                dispatch, _draft, reply = self._dispatch_and_readback(
                    mem, low, tools, ask_confirm=False
                )
                mem.set_stage(ST_DONE)
                mem.ticket_status = "✅ RSA DISPATCHED — NO CLAIM FILED (NCB PROTECTED)"
                return reply, dispatch, None, mem

            if not entities.get("road_type") or not entities.get("road_explicit"):
                mem.set_stage(ST_INCIDENT)
                mem.ticket_status = "STAGE 2 — ROAD TYPE NEEDED"
                road_variants = [
                    "Aap abhi National Highway par hain, sheher ke road par, ya gaon wali link road par?",
                    "Aap kahan hain — highway par, sheher ke andar, ya gaon ki link road par?",
                    "Kahan par hua ye sab — NH par, city road par, ya rural link road par?",
                ]
                dt = sum(1 for u in mem.utterances if u.get("role") == "driver")
                road_q = road_variants[dt % len(road_variants)]
                reply = (
                    f"{part_word.capitalize() + ' ka issue? ' if part_word else ''}"
                    "Achha, samajh gayi. " + road_q
                )
                return reply, None, None, mem

            dispatch, draft, reply = self._dispatch_and_readback(
                mem, low, tools, ask_confirm=bool(entities.get("policy_number"))
            )
            if entities.get("policy_number") and draft:
                mem.set_stage(ST_CONFIRM)
                mem.ticket_status = "CONFIRM_PENDING"
                if not (entities.get("confirm_intent") == "confirm") and not reply.rstrip().endswith("?"):
                    reply += " Kya main aapka insurance claim apply kar doon?"
            else:
                mem.set_stage(ST_POLICY)
                mem.ticket_status = "RSA DISPATCH-PENDING — FNOL AWAITING POLICY NUMBER"
                policy_variants = [
                    " Insurance claim lagane aur dispatch lock karne ke liye apna 4-digit policy number batayein.",
                    " Claim aur dispatch lock karane ke liye apna char anka policy number de dijiye.",
                    " Policy number batayein taaki claim aur dispatch dono confirm ho sakein.",
                ]
                p_idx = sum(1 for u in mem.utterances if u.get("role") == "driver") % len(policy_variants)
                reply += policy_variants[p_idx]
            return reply, dispatch, draft, mem

        mem.set_stage(ST_INCIDENT)
        mem.ticket_status = "STAGE 2 — INCIDENT DETAILS IN PROGRESS"
        reply = "Ji bataiye, gaadi ke saath kya hua hai — kya takleef aa rahi hai?"
        return reply, None, None, mem

    def _already_done_reply(self, mem: CallMemory) -> str:
        d = mem.entities.get("last_dispatch") or {}
        draft = mem.entities.get("last_draft") or {}
        if d.get("workflow") == "A":
            return (
                f"Dispatch already active hai ji — mechanic lagbhag {d.get('eta_mins', 22)} minute mein pahunch raha hai. "
                "Claim file karne ki zaroorat nahi, aapka 20 percent No-Claim Bonus bilkul safe hai."
            )
        claim_ref = f"Claim {draft['claim_id']} " if draft.get("claim_id") else "Aapka claim "
        eta_ref = f"aur help lagbhag {d['eta_mins']} minute mein pahunch rahi hai" if d.get("eta_mins") else "aur cashless garage details shared hain"
        return (
            f"{claim_ref}already lock ho chuka hai, {eta_ref}. "
            "Kya aapko koi detail change karni hai ya aur madad chahiye?"
        )

    def _emergency_reply(self, mem: CallMemory) -> str:
        nh = bool(mem.entities.get("nhai_patrol"))
        c1 = ("Aapki jaan sabse pehle hai — main turant 112 ambulance aur 112 police "
              "bhej rahi hoon, kripya phone na kaatein.")
        c2 = ("Ek emergency officer aapko directly connect kar rahi hoon"
              + (", aur 1033 highway patrol bhi aapke location par aa raha hai." if nh else ".")
              + " Surakshit jagah par rahein, line par bane rahiye.")
        mem.entities["emergency_calls"] = [
            {"line": "112", "label": "🚨 112 AMBULANCE DISPATCHED"},
            {"line": "112-POLICE", "label": "👮 112 POLICE DISPATCHED"},
            *([{"line": "1033", "label": "🛣️ 1033 NHAI HIGHWAY PATROL"}] if nh else []),
        ]
        return f"{c1} {c2}"

    def _closing_reply(self, dispatch: Optional[Dict[str, Any]],
                       draft: Optional[Dict[str, Any]]) -> str:
        c1 = ("Confirm ho gaya ji — aapka insurance claim APPLY ho gaya aur roadside "
              "help DISPATCH ho gaya hai.")
        c2 = "Aapka RSA ticket aur FNOL claim card dono lock ho chuke hain."
        if dispatch and dispatch.get("eta_mins"):
            c2 += f" Help lagbhag {dispatch['eta_mins']} minute mein aayegi. Aap chinta mat kijiye, safe rahiye ji."
        elif draft and draft.get("estimated_range_display"):
            c2 += f" Estimated repair {draft['estimated_range_display']} — final approval surveyor inspection ke baad hoga."
        return f"{c1} {c2}"


_CALLS: Dict[str, CallMemory] = {}
