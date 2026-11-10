"""
BimaVani — Brain Layer: Staged Indian Helpline Conversation (+ optional LLM)
===========================================================================
Stage [4] Tools + Memory of the 8-stage pipeline.

The Brain conducts a natural, human-like multi-turn Indian RSA/Insurance
conversation in THREE STAGES, always replying in **2 short spoken Hinglish
clauses** (Clause Buffer -> Timbre TTS -> Jitter Buffer feel fast):

  STAGE 1 — Injury & Emergency Triage (first priority)
      asks safe/injured; injury -> 112 Ambulance + 112 Police (+1033 NHAI
      if highway), UI emergency card -> 🚨 112 AMBULANCE & POLICE DISPATCHED,
      human officer handoff. Safe -> Stage 2.
  STAGE 2 — Indian Road Type & RSA Dispatch
      NH + non-drivable -> "gaadi start mat kijiye", 1033 patrolling cones +
      free flatbed tow <=50km to cashless garage (Case A);
      City minor breakdown (puncture/battery/keys/fuel) -> on-spot bike
      mechanic ETA 22 min + 20% NCB protection warning (Case B);
      Rural/village link road -> IRDAI ₹1,500 local crane reimbursement +
      tehsil workshop (Case C);
      Drivable minor crash -> 4 photos with plate + cashless garage (Case D).
  STAGE 3 — Policy Number, FNOL & Voice Confirmation
      asks 4-digit policy number; auto-fills the FNOL claim card; reads
      back the summary in 2 clauses; on "haan kar do" -> both cards
      ✅ APPLIED & DISPATCHED with the closing confirmation.

LLM (optional assist, .env): LLM_API_KEY + LLM_BASE_URL (default
https://tokenharbor.ai/v1) + BIMAVANI_LLM_MODEL (default deepseek-v4.1-flash).
httpx POST {base}/chat/completions with a timeout — ANY failure (timeout,
parse error, no key) falls back to the 100% deterministic Hinglish script
engine below, which by itself produces the exact same facts (zero
hallucination, zero LLM required).

Greeting (from /api/call/start) also flows through Clause Buffer -> Timbre.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.rsa_and_claim_rules import (
    confirm_detection,
    compose_agent_reply,
    dispatch_smart_rsa,
    extract_parts,
    extract_policy_number,
    extract_road_type,
    detect_drivability,
    calculate_irdai_claim_draft,
    triage_injury_and_safety,
    spoken_part,
    PARTS,
    POLICIES,
    IRDAI,
)
from backend.pipeline.clause_buffer import split_into_clauses, ClauseBatch
from backend.translit import normalize_for_matching

_MEMORY_PATH = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "data" / "call_history.json"

GREETING_CLAUSE_1 = "Namaste! Main BimaVani Roadside Helpline se bol rahi hoon."
GREETING_CLAUSE_2 = "Sabse pehle batayein—kya aap aur gaadi mein baithe sabhi log safe hain, ya kisi ko chot lagi hai?"

# stage tokens
ST_SAFETY_TRIAGE = "safety_triage"
ST_INCIDENT = "incident"
ST_POLICY = "policy_fnol"
ST_CONFIRM = "confirm"
ST_EMERGENCY = "emergency_handoff"
ST_DONE = "done"

SAFE_ACK = re.compile(r"(safe|sabhi thik|sabhi theek|sab thik|kisi ko .*nahi|nahi hai chot|unhurt|haan safe)", re.I)

# Generic "something is wrong with the vehicle" signals. Deliberately NOT a
# list of parts — part detection comes from the catalog; this only decides
# whether the driver has described any problem at all.
_PROBLEM_SIGNALS = (
    "problem", "dikkat", "takleef", "madad", "help", "kharab", "kharaab",
    "band", "start nahi", "chal nahi", "ruk gayi", "breakdown", "accident",
    "crash", "tow", "towel", "smoke", "leak", "awaz", "awaaz", "issue",
    "phat", "toot", "tut", "gir", "stuck", "hang", "fail", "dead", "khatam",
)


def _looks_like_vehicle_problem(low: str, parts: List[str],
                                triage: Dict[str, Any]) -> bool:
    if parts or triage.get("injured"):
        return True
    return any(k in low for k in _PROBLEM_SIGNALS)


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
        if _MEMORY_PATH.exists() and _MEMORY_PATH.stat().st_size > 2:
            with open(_MEMORY_PATH, "r", encoding="utf-8") as fh:
                return json.load(fh)
    except Exception:
        pass
    return []


def save_long_term(records: List[Dict[str, Any]]) -> None:
    try:
        _MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_MEMORY_PATH, "w", encoding="utf-8") as fh:
            json.dump(records[-200:], fh, indent=2, ensure_ascii=False)
    except Exception:
        pass


def append_long_term(record: Dict[str, Any]) -> None:
    recs = load_long_term()
    recs.append(record)
    save_long_term(recs)


# ---------------------------------------------------------------------------
# LLM adapter — OpenAI-compatible (Token Harbor) with hard fallback
# ---------------------------------------------------------------------------

def _llm_env() -> tuple[Optional[str], str, str]:
    key = os.environ.get("LLM_API_KEY", "").strip()
    base = (os.environ.get("LLM_BASE_URL") or "https://tokenharbor.ai/v1").rstrip("/")
    model = os.environ.get("BIMAVANI_LLM_MODEL") or "deepseek-v4.1-flash"
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
    "\n"
    "OUTPUT\n"
    "Return ONLY compact JSON: {\"reply\": \"<spoken text>\", \"action\": \"<one of "
    "emergency|ask_safety|ask_problem|ask_location|ask_policy|ask_confirm|"
    "dispatch|close|clarify>\"}"
)


def _parse_llm_json(txt: str) -> Optional[Dict[str, Any]]:
    """Robustly pull {reply, action} out of an LLM response."""
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
    # fallback: model returned plain prose, keep it as the reply
    line = raw.splitlines()[0].strip().strip("\"' ") if raw else ""
    return {"reply": line, "action": ""} if line else None


_LLM_COOLDOWN_UNTIL = 0.0     # circuit breaker: don't retry a dead key every turn
_LLM_LAST_ERROR = ""          # surfaced in /api/health for honest demo debugging
_LLM_FAIL_STREAK = 0


async def llm_assist(transcript: str, context: Dict[str, Any],
                     timeout_s: float = 2.5) -> Optional[Dict[str, Any]]:
    """Ask the .env LLM to act as a pro agent using the full CONTEXT.

    Returns {"reply", "action"} or None on any failure. The deterministic
    rules engine remains the source of truth; the LLM may only phrase the
    next turn and pick from actions already legal for this state."""
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
            out = _parse_llm_json(
                resp.json()["choices"][0]["message"]["content"])
            if out:
                _LLM_FAIL_STREAK = 0
                _LLM_LAST_ERROR = ""
            return out
    except httpx.HTTPStatusError as e:
        # dead key / exhausted wallet: stop paying this latency every turn
        code = e.response.status_code if e.response is not None else 0
        _LLM_LAST_ERROR = f"http_{code}"
        _LLM_FAIL_STREAK += 1
        _LLM_COOLDOWN_UNTIL = time.time() + (300 if code in (401, 402, 403) else 120)
        return None
    except Exception as e:
        # unreachable host / timeout / DNS: the deterministic brain answers
        # instantly instead of adding seconds to every single turn
        _LLM_LAST_ERROR = type(e).__name__
        _LLM_FAIL_STREAK += 1
        _LLM_COOLDOWN_UNTIL = time.time() + 120
        return None


async def llm_warmup(timeout_s: float = 3.0) -> None:
    """Cheap boot probe so the FIRST user turn never pays the dead-provider
    timeout. Sends a 1-token completion: an exhausted wallet/plan answers 402
    here, at boot, instead of mid-demo."""
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
    except Exception as e:
        _LLM_LAST_ERROR = type(e).__name__
        _LLM_FAIL_STREAK += 1
        _LLM_COOLDOWN_UNTIL = time.time() + 120


def _agent_context(mem: "CallMemory", transcript: str,
                   triage: Dict[str, Any], tools: List[Dict[str, Any]],
                   deterministic_reply: str,
                   recommended_action: str) -> Dict[str, Any]:
    """Everything a professional agent would know at this moment.

    Generic by design: no per-issue branches — the catalog metadata tells the
    model what each detected part means (workflow A/B/C, drivable, NCB)."""
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
        {"role": u.get("role"), "text": u.get("text")}
        for u in mem.utterances[-8:]
    ]
    tool_facts = {}
    for t in tools:
        if t.get("tool") == "triage_injury_and_safety":
            tool_facts["triage"] = {"injured": t.get("injured"),
                                    "safety": t.get("safety")}
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
                               ("vehicle_make", "vehicle_model", "plan", "idv_inr")
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
    """Conservative entity merge hook — the deterministic extractor always
    wins; LLM output never removes facts."""
    return dict(base_entities)


# ---------------------------------------------------------------------------
# Dynamic policy record (Issue 3: accept ANY spoken policy number)
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

    def __init__(self) -> None:
        self.clause_buffer = None  # per-call batches live in clause_buffer below
        self.batches: Dict[str, Any] = {}
        self.tool_log: List[Dict[str, Any]] = []

    # -- session -----------------------------------------------------------
    def get_or_create_call(self, call_id: str) -> CallMemory:
        return _CALLS.setdefault(call_id, CallMemory(call_id=call_id))

    def reset_call(self, call_id: str) -> None:
        _CALLS.pop(call_id, None)

    # -- greeting (called by /api/call/start) ------------------------------
    async def start_call(self, call_id: str) -> Dict[str, Any]:
        """STAGE 1 opener: marks the call active and returns the 2 greeting
        clauses (sent back verbatim for Clause Buffer -> Timbre -> Jitter)."""
        mem = self.get_or_create_call(call_id)
        mem.set_stage(ST_SAFETY_TRIAGE)
        mem.ticket_status = "CALL ACTIVE — STAGE 1 SAFETY TRIAGE"
        reply = f"{GREETING_CLAUSE_1} {GREETING_CLAUSE_2}"
        mem.push_utterance("agent(greeting)", reply)
        # Greeting must be EXACTLY the 2 operator clauses — no generic splitter
        cl1 = {"index": 1, "text": GREETING_CLAUSE_1,
               "priority": "clause1_fast_hook", "tts_order": 1}
        cl2 = {"index": 2, "text": GREETING_CLAUSE_2,
               "priority": "clause2_details", "tts_order": 2}
        batch = ClauseBatch(turn_id=call_id + "-greet", clauses=[cl1, cl2])
        self.batches[call_id] = batch
        return {
            "call_id": call_id,
            "reply_text": reply,
            "clauses": batch.as_list(),
            "ticket_status": mem.ticket_status,
            "memory": mem.snapshot(),
        }

    # -- main turn ---------------------------------------------------------
    async def process_turn(self, call_id: str, transcript: str,
                           stt_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        stt_meta = stt_meta or {}
        # Prisma v3.0 returns Hindi words in Devanagari ("गाड़ी खराब", "चोट"),
        # while every keyword/numeral table is Roman Hinglish. Match against the
        # transliterated view, but keep the original transcript for display.
        match_text = normalize_for_matching(transcript or "")
        low = match_text.lower().strip()
        mem = self.get_or_create_call(call_id)
        mem.push_utterance("driver", transcript, {"stt": stt_meta})

        entities: Dict[str, Any] = {}
        policy_number = extract_policy_number(match_text) or mem.entities.get("policy_number")
        policy = POLICIES.get(str(policy_number or "")) or (
            _dynamic_policy(policy_number) if policy_number and extract_parts(match_text) else None)
        this_road = bool(re.search(
            r"\b(nh[-\s]?\d+\b|nh\b|highway|haaiway|haaive|haiwe|expresway|city|sheher|shahar|gaon|"
            r"village|link road|local road|rural|delhi|mumbai|karnal|panipat|chennai|meerut)\b",
            low))
        road_explicit = this_road or bool(mem.entities.get("road_explicit"))
        road_type = mem.entities.get("road_type")
        if this_road:
            road_type = extract_road_type(match_text, policy)
        new_parts = extract_parts(match_text)
        if new_parts:
            parts = sorted(set(new_parts) | set(mem.entities.get("parts", []) or []))
        else:
            parts = list(mem.entities.get("parts", []) or [])
        mem.entities["parts"] = parts
        is_drivable, drivable_reason = detect_drivability(parts, low)
        _ = drivable_reason
        triage = triage_injury_and_safety(match_text)
        confirm = confirm_detection(match_text)
        explicit_correction = any(
            k in low for k in ("ruko", "ruk ja", "rukja", "galat", "wrong",
                               "nahi nahi", "arre nahi", "matlab"))

        entities.update({
            "policy_number": policy_number or mem.entities.get("policy_number"),
            "road_type": road_type,
            "road_explicit": road_explicit,
            "parts": parts,
            "is_drivable": is_drivable,
            "drivable_reason": drivable_reason,
            "injured": triage["injured"],
            "confirm_intent": confirm,
        })
        mem.entities = {**mem.entities, **entities}
        stage = mem.stage or (ST_SAFETY_TRIAGE if mem.entities.get("call_started") else "")

        tools: List[Dict[str, Any]] = [triage]
        draft = None
        dispatch = None

        # ---------------- STAGE 1: EMERGENCY (never skipped) ----------------
        if triage["injured"]:
            mem.set_stage(ST_EMERGENCY)
            mem.ticket_status = "EMERGENCY — 112 AMBULANCE & POLICE DISPATCHED"
            if road_type == "NH" or "highway" in low or "nh" in low.replace(" ", ""):
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

        # ---------------- barge-in corrections (any stage) -------------------
        elif explicit_correction and (policy_number or mem.entities.get("last_draft")) and stage not in ("", ST_EMERGENCY):
            dispatch, draft, reply = self._dispatch_and_readback(mem, low, tools)
            mem.set_stage(ST_CONFIRM)
            mem.ticket_status = "CONFIRM_PENDING (corrected)"

        elif confirm == "confirm" and (mem.entities.get("last_draft") or stage == ST_CONFIRM or policy_number):
            # ---------------- STAGE 3 close: apply FNOL + dispatch ------------
            if not mem.entities.get("last_draft") and policy_number and parts:
                dispatch, draft, _r = self._dispatch_and_readback(mem, low, tools, ask_confirm=False)
            dispatch = dispatch or mem.entities.get("last_dispatch")
            draft = draft or mem.entities.get("last_draft")
            mem.set_stage(ST_DONE)
            mem.ticket_status = "✅ APPLIED & DISPATCHED"
            reply = self._closing_reply(dispatch, draft)

        else:
            # ---------------- STAGE 2/3 staged progression --------------------
            reply, dispatch, draft, mem = self._staged_turn(mem, low, tools, triage)

        # LLM as the conversational front-brain: it gets the FULL context
        # (history, catalog semantics, tool facts, legal next actions) and
        # decides how a pro agent would say the next turn. Facts and tool
        # execution stay deterministic — the LLM may only phrase/choose.
        recommended = self._recommended_action(mem, triage)
        ctx = _agent_context(mem, transcript, triage, tools, reply, recommended)
        llm = await llm_assist(transcript, ctx)
        if llm and llm.get("reply"):
            candidate = llm["reply"].strip()
            if self._llm_reply_is_safe(candidate, mem, triage):
                reply = candidate

        mem.incident_speakable = reply
        mem.push_utterance("agent", reply, {"ticket_status": mem.ticket_status,
                                            "stage": mem.stage})

        from backend.pipeline.clause_buffer import ClauseBatch
        batch = split_into_clauses(reply, call_id)
        self.batches[call_id] = batch

        record = {
            "call_id": call_id,
            "at": time.time(),
            "stage": mem.stage,
            "transcript": transcript,
            "entities": mem.entities,
            "ticket_status": mem.ticket_status,
            "reply": reply,
        }
        append_long_term(record)

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
    # helpers
    # ------------------------------------------------------------------
    def _dispatch_and_readback(self, mem: CallMemory, low: str,
                               tools: List[Dict[str, Any]],
                               ask_confirm: bool = True) -> tuple:
        entities = mem.entities
        policy_number = entities.get("policy_number")
        policy = POLICIES.get(str(policy_number or "")) or (
            _dynamic_policy(policy_number) if policy_number else None)
        road = entities.get("road_type") or "CITY"
        parts = entities.get("parts", [])
        is_drivable = entities.get("is_drivable", True)
        dispatch = dispatch_smart_rsa(road, parts, is_drivable, policy)
        tools.append(dispatch)
        mem.entities["last_dispatch"] = dispatch
        mem.entities["last_road_type"] = road
        mem.entities["last_parts"] = parts
        mem.entities["last_drivable"] = is_drivable
        draft = None
        # Workflow A = minor on-spot job: filing a claim would cost the driver
        # their 20% NCB, so no FNOL draft is ever created for it.
        if policy_number and dispatch.get("workflow") != "A":
            draft = calculate_irdai_claim_draft(str(policy_number), parts, policy=policy)
            tools.append(draft)
            mem.entities["last_draft"] = draft
        reply = compose_agent_reply(
            {"triage": {"injured": False, "spoken_empathy_hi": ""},
             "dispatch": dispatch, "claim_draft": draft},
            ack=self._ack(mem, parts),
            ask_confirm=ask_confirm,
        )
        if not ask_confirm and draft:
            reply += " Dono tickets lock kar diye gaye hain."
        return dispatch, draft, reply

    def _ack(self, mem: CallMemory, parts: List[str]) -> str:
        """A short, varied acknowledgement so the agent doesn't say the same
        sentence every turn (deterministic: depends on driver turn count +
        call id, so the same driver doesn't hear the exact same word twice)."""
        driver_turns = sum(1 for u in mem.utterances if u.get("role") == "driver")
        if driver_turns <= 1:
            return "Shukar hai aap safe hain!"
        variants = [
            "Theek hai ji, note kar liya.",
            "Achha ji, samajh gayi.",
            "Haan ji, bilkul.",
            "Samajh gayi ji, aage badhte hain.",
            "Thik hai, main samajh gaya.",
            "Boliye, main sun raha hoon.",
            "Achha, aage badhte hain.",
            "Haan ji, note kar liya hai.",
        ]
        # Deterministic but varied: mix call_id + turn so repetitions differ
        # across calls and across turns.
        idx = (driver_turns - 2 + sum(ord(c) for c in mem.call_id)) % len(variants)
        return variants[idx]

    def _recommended_action(self, mem: CallMemory,
                            triage: Dict[str, Any]) -> str:
        """The single legal next action for this state (advisory to the LLM).
        Derived from catalog metadata, never from hardcoded part names."""
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
        minor = (bool(parts)
                 and all(PARTS.get(p, {}).get("workflow") == "A" for p in parts)
                 and e.get("is_drivable", True))
        if minor:
            return "dispatch"
        if not e.get("policy_number"):
            return "ask_policy"
        if mem.stage == ST_CONFIRM:
            return "ask_confirm"
        return "dispatch"

    def _llm_reply_is_safe(self, text: str, mem: CallMemory,
                           triage: Dict[str, Any]) -> bool:
        """Reject LLM output that drifts from ground truth."""
        if not text or len(text) > 320:
            return False
        low = normalize_for_matching(text).lower()
        e = mem.entities
        parts = e.get("parts") or []
        minor = (bool(parts)
                 and all(PARTS.get(p, {}).get("workflow") == "A" for p in parts)
                 and e.get("is_drivable", True))
        # workflow A must never talk about filing a claim or asking a policy
        if minor and ("claim file" in low or "claim apply" in low
                      or "policy number" in low or "policy batayein" in low):
            return False
        # never state an unknown policy number
        if not e.get("policy_number"):
            found = extract_policy_number(text)
            if found:
                return False
        # never fake an emergency
        if not triage.get("injured") and ("112" in low and "ambulance" in low):
            return False
        return True

    def _staged_turn(self, mem: CallMemory, low: str,
                     tools: List[Dict[str, Any]], triage: Dict[str, Any]):
        stage = mem.stage or ST_SAFETY_TRIAGE
        entities = mem.entities
        parts = entities.get("parts", []) or []
        # Workflow A = minor, drivable, on-spot fix: RSA covers it, and filing
        # an insurance claim would cost the 20% NCB. This is derived from the
        # catalog metadata, so it works for ANY part, not a hardcoded list.
        minor_only = (bool(parts)
                      and all(PARTS.get(p, {}).get("workflow") == "A" for p in parts)
                      and entities.get("is_drivable", True))
        part_word = spoken_part(parts)

        has_incident = _looks_like_vehicle_problem(low, parts, triage) \
            or bool(entities.get("policy_number"))

        if stage in (ST_SAFETY_TRIAGE,) and not has_incident:
            mem.set_stage(ST_INCIDENT)
            reply = ("Bahut shukriya ji — aap sab safe hain, yahi sabse badi baat hai. "
                     "Ab bataiye, gaadi ko kya hua?")
            mem.ticket_status = "STAGE 2 — INCIDENT DETAILS IN PROGRESS"
            return reply, None, None, mem

        if has_incident:
            if minor_only:
                if stage == ST_DONE and entities.get("last_dispatch"):
                    d = entities["last_dispatch"]
                    reply = (f"Dispatch already ho gaya ji — mechanic {d.get('eta_mins', 22)} "
                             "minute mein pahunch raha hai. Claim file karne ki zaroorat nahi, "
                             "aapka No-Claim Bonus safe hai.")
                    return reply, d, None, mem
                if not entities.get("road_explicit"):
                    mem.set_stage(ST_INCIDENT)
                    mem.ticket_status = "STAGE 2 — LOCATION NEEDED (minor repair)"
                    reply = (f"{part_word.capitalize() if part_word else 'Achha'}? "
                             "Achha, koi baat nahi ji. "
                             "Aap abhi city ke andar hain, ya highway par?")
                    return reply, None, None, mem
                dispatch, _draft, reply = self._dispatch_and_readback(
                    mem, low, tools, ask_confirm=False)
                mem.set_stage(ST_DONE)
                mem.ticket_status = "✅ RSA DISPATCHED — NO CLAIM FILED (NCB PROTECTED)"
                return reply, dispatch, None, mem

            if not entities.get("road_type") or not entities.get("road_explicit"):
                mem.set_stage(ST_INCIDENT)
                mem.ticket_status = "STAGE 2 — ROAD TYPE NEEDED"
                road_variants = [
                    "Aap abhi National Highway par hain, sheher ke road par, ya gaon wali link road par?",
                    "Aap kahan hain — highway par, sheher ke andar, ya gaon ki link road par?",
                    "Kaha par hua ye sab — NH par, city road par, ya rural link road par?",
                ]
                road_q = road_variants[(driver_turns_road := sum(1 for u in mem.utterances if u.get("role") == "driver")) % len(road_variants)]
                reply = (f"{part_word.capitalize() + ' ka issue? ' if part_word else ''}"
                         "Achha, samajh gayi. " + road_q)
                return reply, None, None, mem
            # Only ask for confirmation when we actually have a policy to file
            # against; otherwise the single next question is the policy number.
            dispatch, draft, reply = self._dispatch_and_readback(
                mem, low, tools, ask_confirm=bool(entities.get("policy_number")))
            if entities.get("policy_number") and draft:
                mem.set_stage(ST_CONFIRM)
                mem.ticket_status = "CONFIRM_PENDING"
                if not (entities.get("confirm_intent") == "confirm") \
                        and not reply.rstrip().endswith("?"):
                    reply += " Kya main aapka insurance claim apply kar doon?"
            else:
                mem.set_stage(ST_POLICY)
                mem.ticket_status = "RSA DISPATCH-PENDING — FNOL AWAITING POLICY NUMBER"
                policy_variants = [
                    " Insurance claim lagane aur dispatch lock karne ke liye apna char anko wala policy number batayein.",
                    " Claim aur dispatch lock karane ke liye apna 4 anka policy number de dijiye.",
                    " Policy number (char ank ka) batayein taaki claim aur dispatch dono confirm ho sakein.",
                ]
                p_idx = sum(1 for u in mem.utterances if u.get("role") == "driver") % len(policy_variants)
                reply += policy_variants[p_idx]
            return reply, dispatch, draft, mem

        # no incident info yet -> ask (Stage 2 opener)
        mem.set_stage(ST_INCIDENT)
        mem.ticket_status = "STAGE 2 — INCIDENT DETAILS IN PROGRESS"
        reply = "Ji bataiye, gaadi ke saath kya hua hai — kya takleef aa rahi hai?"
        return reply, None, None, mem

    def _emergency_reply(self, mem: CallMemory) -> str:
        nh = bool(mem.entities.get("nhai_patrol"))
        c1 = ("Aapki jaan sabse pehle hai — main turant 112 ambulance aur 112 police "
              "bhej rahi hoon, kripya phone na kaatein.")
        c2 = ("Ek emergency officer aapko directly connect kar rahi hoon"
              + (", aur 1033 highway patrol bhi aapke location par aa raha hai." if nh else ".")
              + " Gaadi mein baithe rahein, helmet/halki awaz mein rahiye.")
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
            c2 += f" Help lagbhag {dispatch['eta_mins']} minute mein aayega. Aap chinta mat kijiye, hum hain na? Safe rahiye ji"
        elif draft and draft.get("estimated_range_display"):
            c2 += f" Estimated repair {draft['estimated_range_display']} — final surveyor check karke aayega. Safe trip ji!"
        return f"{c1} {c2}"


_CALLS: Dict[str, CallMemory] = {}
