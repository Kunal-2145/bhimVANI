"""
BimaVani — FastAPI Application Server (backend/main.py)
=======================================================
Endpoints
  GET  /api/health           — heartbeat + mode + pipeline telemetry
  POST /api/config/key       — HOT-RELOAD GNANI_API_KEY (UI 🔑 modal;
                               never restarts the server)
  GET  /api/config           — current safe config (masked key, models)
  POST /api/verify-gnani     — live Timbre→Prisma round-trip for the 🧪 button
  POST /api/call/turn        — one driver voice turn: 16kHz WAV → STT →
                               Brain tools → 2-clause reply (+ clause audio)
  POST /api/call/barge-in    — <50ms flush: cancel tasks + clear queues,
                               optional corrected transcript → updated cards
  POST /api/tts              — Timbre synth with cache (jitter-buffer feeder)
  POST /api/eval             — run the 20-case benchmark suite
  GET  /api/pipeline-state   — queue depths, barge-in latency, stage timings
Static: serves frontend/ at "/"
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

from backend.gnani_client import (
    CONFIG,
    fallback_tts_wav,
    has_api_key,
    is_valid_wav,
    live_mode,
    set_api_key,
    synthesize_speech,
    transcribe_wav,
    verify_round_trip,
)
from backend.pipeline.barge_in import get_state_machine
from backend.pipeline.brain import Brain, llm_status
from backend.pipeline.clause_buffer import split_into_clauses
from backend.pipeline.queues import BUS
from backend import eval_runner

APP_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = APP_ROOT / "frontend"

app = FastAPI(title="BimaVani (बीमा-वाणी) — Voice-First RSA + IRDAI Claim Desk",
              version="1.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

BRAIN = Brain()


@app.on_event("startup")
async def _warm_llm() -> None:
    """Probe the .env LLM once at boot (background, no tokens) so a dead
    provider never adds seconds to the first live turn of a demo."""
    import asyncio as _asyncio
    from backend.pipeline.brain import llm_warmup
    _asyncio.create_task(llm_warmup())

# live stage timings for the 8-box visualizer
STAGE_TELEMETRY: Dict[str, float] = {f"stage_{i}": 0.0 for i in range(1, 9)}
STAGE_TELEMETRY.update({"stage_names": [
    "Mic", "Prisma v3.0 STT", "Auto-VAD Endpoint", "Brain (DeepSeek + Tools)",
    "Clause Buffer", "Timbre v2.5 TTS", "Jitter Buffer", "Speaker / Barge-In"]})

class KeyIn(BaseModel):
    api_key: str = Field(min_length=0, max_length=256)


class StartIn(BaseModel):
    call_id: str = "live"


class TurnIn(BaseModel):
    call_id: str = "live"
    wav_b64: str = ""                       # genuine 16kHz mono PCM WAV (stage 1)
    transcript_override: str = ""           # scripted demo push (no STT credits)
    endpoint_mode: str = "auto_vad"         # auto_vad (default now) | push_to_talk
    vad_silence_ms: int = 600


class BargeInIn(BaseModel):
    call_id: str = "live"
    transcript: str = ""                    # e.g. "Ruko! policy 4491 hai!"
    update_cards: bool = True


class TtsIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    use_cache: bool = True


class EvalIn(BaseModel):
    run: bool = True


class EndIn(BaseModel):
    call_id: str = "live"


# ---------------------------------------------------------------------------
# Health / config
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "BimaVani",
        "mode": "LIVE_GNANI" if live_mode() else "CREDIT_SHIELD_FALLBACK",
        "stt_model": CONFIG.stt_model,
        "tts_model": CONFIG.tts_model,
        "gnani_base_url": CONFIG.base_url,
        "queues": BUS.snapshot(),
        "stages": {k: round(v, 1) for k, v in STAGE_TELEMETRY.items() if k != "stage_names"},
        "stage_names": STAGE_TELEMETRY["stage_names"],
        "brain": llm_status(),
    }


@app.get("/api/config")
async def config() -> Dict[str, Any]:
    masked = (CONFIG.api_key[:4] + "…" + CONFIG.api_key[-4:]) if len(CONFIG.api_key) > 10 else ("set" if CONFIG.api_key else "")
    return {
        "live_mode": live_mode(),
        "api_key_masked": masked,
        "stt_model": CONFIG.stt_model,
        "stt_language": CONFIG.stt_language,
        "stt_format": CONFIG.stt_format,
        "tts_model": CONFIG.tts_model,
        "tts_voice": CONFIG.tts_voice,
        "llm_model": os.environ.get("BIMAVANI_LLM_MODEL", "gnani/gnani-evon-v3.3-30B-A3B"),
        "llm_enabled": bool(os.environ.get("LLM_API_KEY")),
        "semaphore_limit": 1,
    }


@app.post("/api/config/key")
async def hot_set_key(payload: KeyIn) -> Dict[str, Any]:
    """🔑 UI modal: hot-reload GNANI_API_KEY without a server restart."""
    ok = set_api_key(payload.api_key)
    masked = (CONFIG.api_key[:4] + "…" + CONFIG.api_key[-4:]) if len(CONFIG.api_key) > 10 else ("set" if CONFIG.api_key else "")
    return {
        "saved_from_live_config": ok,
        "live_mode": live_mode(),
        "api_key_masked": masked,
        "note": "Key hot-loaded into the running Gnani client (no restart needed).",
    }


# ---------------------------------------------------------------------------
# 🧪 Live Gnani verification (UI button + CLI share this path)
# ---------------------------------------------------------------------------

@app.post("/api/verify-gnani")
async def verify_gnani() -> Dict[str, Any]:
    result = await verify_round_trip()
    result["ui_hint"] = (
        "Live OK — Prisma transcript matched synthesized speech."
        if not result["fallback"] else
        "Credit-Shield fallback used (no GNANI_API_KEY). Set the key for live calls."
    )
    return result


# ---------------------------------------------------------------------------
# [0] Call start — greeting through Clause Buffer -> Timbre -> Jitter Buffer
# ---------------------------------------------------------------------------

@app.post("/api/call/start")
async def call_start(payload: StartIn) -> Dict[str, Any]:
    """📞 Dial: builds the call session and speaks the opening operator
    greeting IMMEDIATELY (2 clauses streamed back for the Jitter Buffer)."""
    t0 = time.perf_counter()
    call_id = payload.call_id or "live"
    # Wipe any memory left over from a previous call so this one starts fresh:
    # no old policy number, parts, road type, stage or ticket status bleeds
    # into the new greeting / turn.
    BRAIN.reset_call(call_id)
    sm = get_state_machine(call_id)
    sm.transition("LISTENING")

    started = await BRAIN.start_call(call_id)
    clause_payloads = []
    for c in started["clauses"]:
        t6 = time.perf_counter()
        synth = await synthesize_speech(c["text"], use_cache=True)
        dt = (time.perf_counter() - t6) * 1000
        STAGE_TELEMETRY["stage_6"] = STAGE_TELEMETRY.get("stage_6", 0) * 0.3 + dt * 0.7
        await BUS.hops["tts"].put(len(synth["audio"]))
        wav = synth["audio"] if is_valid_wav(synth["audio"]) else fallback_tts_wav(c["text"])
        clause_payloads.append({
            "index": c["index"], "priority": c["priority"],
            "text": c["text"],
            "audio_base64": "data:audio/wav;base64,"
                            + base64.b64encode(wav).decode("ascii"),
            "latency_ms": round(dt, 1),
            "cached": synth.get("cached", False),
            "fallback": synth.get("fallback", False),
        })
    await BUS.hops["clause_buffer"].put(len(clause_payloads))

    return {
        "ok": True,
        "call_id": call_id,
        "greeting": started["reply_text"],
        "stage": started["memory"]["stage"],
        "ticket_status": started.get("ticket_status"),
        "clauses": clause_payloads,
        "speech_fallback_hint": "app.js falls back to window.speechSynthesis if decodeAudioData fails.",
        "pipeline": {"stages_ms": dict(STAGE_TELEMETRY), "queues": BUS.snapshot()},
        "total_start_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


# ---------------------------------------------------------------------------
# [1] Mic → [2] Prisma STT → [3] Endpointing → [4] Brain → [5] clauses
# ---------------------------------------------------------------------------

@app.post("/api/call/turn")
async def call_turn(payload: TurnIn) -> Dict[str, Any]:
    t_stage1 = time.perf_counter()
    call_id = payload.call_id or "live"
    sm = get_state_machine(call_id)
    sm.transition("LISTENING")

    wav_bytes = b""
    if payload.wav_b64:
        try:
            wav_bytes = base64.b64decode(payload.wav_b64)
        except Exception:
            wav_bytes = b""
        if wav_bytes and wav_bytes[:1] not in (b"R",):
            wav_bytes = wav_bytes  # browsers always send RIFF; tolerate quirks
    await BUS.hops["mic_ingest"].put(len(wav_bytes))

    # [3] endpointing telemetry (push-to-talk is the default; VAD optional)
    from backend.pipeline.endpointing import endpoint_turn, EndpointingState
    ep_state = EndpointingState(mode=payload.endpoint_mode or "push_to_talk",
                                vad_silence_ms=payload.vad_silence_ms or 600)
    ep = endpoint_turn(ep_state)
    STAGE_TELEMETRY["stage_3"] = ep.get("endpoint_reason") == "push_to_talk_release" and 0.0 or 0.0
    await BUS.hops["endpointing"].put(1)

    # [2] Prisma STT (or scripted override -> 0 ms / 0 credits)
    t2 = time.perf_counter()
    if payload.transcript_override:
        transcript = payload.transcript_override.strip()
        stt_meta: Dict[str, Any] = {
            "transcript": transcript, "request_id": "scripted-" + call_id,
            "stt_latency_ms": 0.0, "fallback": False, "skip_reason":
            "scripted_demo_transcript (0 credits)",
        }
    elif wav_bytes and len(wav_bytes) > 44:
        stt_meta = await transcribe_wav(wav_bytes)
    else:
        stt_meta = {"transcript": "", "request_id": "empty",
                    "stt_latency_ms": 0.0, "fallback": True}
    STAGE_TELEMETRY["stage_2"] = float(stt_meta.get("stt_latency_ms") or 0.0)
    await BUS.hops["stt"].put(stt_meta.get("transcript", ""))
    transcript = stt_meta.get("transcript", "")

    # [4] Brain (deterministic tools + memory; optional LLM assist)
    if not transcript:
        return await _empty_turn(call_id, stt_meta, ep)
    t4 = time.perf_counter()
    result = await BRAIN.process_turn(call_id, transcript, stt_meta)
    STAGE_TELEMETRY["stage_4"] = (time.perf_counter() - t4) * 1000
    await BUS.hops["brain"].put(len(result["clauses"]))

    # [5] 2-Clause buffer hop (measure only the hop itself, not the Brain)
    t5 = time.perf_counter()
    await BUS.hops["clause_buffer"].put([c["index"] for c in result["clauses"]])
    STAGE_TELEMETRY["stage_5"] = (time.perf_counter() - t5) * 1000

    # [6] Timbre TTS for each clause (<=2). clause1 first — <400ms TTFA goal
    clause_audio = []
    for c in result["clauses"]:
        t6 = time.perf_counter()
        synth = await synthesize_speech(c["text"], use_cache=True)
        dt = (time.perf_counter() - t6) * 1000
        STAGE_TELEMETRY["stage_6"] = STAGE_TELEMETRY.get("stage_6", 0) * 0.3 + dt * 0.7
        await BUS.hops["tts"].put(len(synth["audio"]))
        if synth["audio"] and is_valid_wav(synth["audio"]):
            clause_audio.append({
                "index": c["index"], "priority": c["priority"],
                "text": c["text"], "tts_latency_ms": round(dt, 1),
                "audio_b64": base64.b64encode(synth["audio"]).decode("ascii"),
                "cached": synth.get("cached", False),
                "fallback": synth.get("fallback", False),
            })

    # unified clauses payload for the Jitter Buffer (Issue 4 contract):
    # [{text, audio_base64: "data:audio/wav;base64,...", latency_ms}, ...]
    clauses_payload = [
        {
            "index": ca["index"], "priority": ca["priority"],
            "text": ca["text"],
            "audio_base64": "data:audio/wav;base64," + ca["audio_b64"],
            "latency_ms": ca["tts_latency_ms"],
            "cached": ca["cached"], "fallback": ca["fallback"],
        }
        for ca in clause_audio
    ]

    sm.transition("SPEAKING")
    result["pipeline"] = {
        "stages_ms": {k: STAGE_TELEMETRY[k] for k in sorted(STAGE_TELEMETRY) if k.startswith("stage_")},
        "endpointing": ep,
        "stt": {k: stt_meta.get(k) for k in ("transcript", "request_id", "stt_latency_ms", "fallback")},
        "queues": BUS.snapshot(),
    }
    result["clause_audio"] = clause_audio
    result["clauses"] = clauses_payload          # jitter-buffer contract (data URIs)
    result["total_turn_ms"] = round((time.perf_counter() - t_stage1) * 1000, 1)
    result["barge_in_hint"] = "Press ⚡ Interrupt during read-back to correct policy fields."
    return result


async def _empty_turn(call_id: str, stt_meta: Dict[str, Any], ep: Dict[str, Any]) -> Dict[str, Any]:
    """STT returned no words — NEVER stay silent: speak a 2-clause retry
    prompt through the same Clause Buffer -> Timbre -> Jitter path, and
    surface a clear hint when the cause is a missing GNANI_API_KEY."""
    reply = ("Aapki awaaz mujhe saaf nahi aayi ji. "
             "Mic check kijiye aur apni baat dobara boliye — main sun rahi hoon.")
    batch = split_into_clauses(reply, call_id + "-empty")
    clauses_payload = []
    for c in batch.as_list():
        synth = await synthesize_speech(c["text"], use_cache=True)
        wav = synth["audio"] if is_valid_wav(synth["audio"]) else fallback_tts_wav(c["text"])
        clauses_payload.append({
            "index": c["index"], "priority": c["priority"],
            "text": c["text"],
            "audio_base64": "data:audio/wav;base64," + base64.b64encode(wav).decode("ascii"),
            "latency_ms": round(float(synth.get("tts_latency_ms") or 0), 1),
            "cached": synth.get("cached", False),
            "fallback": synth.get("fallback", False),
        })
    hint = None
    if stt_meta.get("fallback"):
        hint = ("Prisma STT is in Credit-Shield fallback (GNANI_API_KEY missing) — "
                "set the key via 🔑, or use the typed-line box to drive the conversation.")
    return {
        "call_id": call_id,
        "transcript": "",
        "reply_text": reply,
        "stt_empty": True,
        "hint": hint,
        "clauses": clauses_payload,
        "clause_audio": [],
        "ticket_status": "DRAFT",
        "entities": {},
        "tools_invoked": [],
        "memory": BRAIN.get_or_create_call(call_id).snapshot(),
        "pipeline": {"stages_ms": STAGE_TELEMETRY, "endpointing": ep,
                     "stt": stt_meta, "queues": BUS.snapshot()},
    }


# ---------------------------------------------------------------------------
# ⚡ Barge-in — Stop & Flush ALL (<50ms contract)
# ---------------------------------------------------------------------------

@app.post("/api/call/barge-in")
async def call_barge_in(payload: BargeInIn) -> Dict[str, Any]:
    t0 = time.perf_counter()
    call_id = payload.call_id or "live"
    sm = get_state_machine(call_id)

    flushed = BUS.clear_all()
    ev = await sm.trigger_barge_in(payload.transcript, flush_fn=lambda: {"flushed_clauses": sum(flushed.values())})
    sm.resume_listening()

    cards: Dict[str, Any] = {}
    if payload.update_cards and payload.transcript:
        updated = await BRAIN.process_turn(call_id, payload.transcript,
                                           {"barge_in": True})
        cards = {
            "entities": updated["entities"],
            "tools_invoked": updated["tools_invoked"],
            "reply_text": updated["reply_text"],
            "ticket_status": updated["ticket_status"],
            "clauses": updated["clauses"],
        }

    return {
        "call_id": call_id,
        "flush_latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        "state_before": "SPEAKING",
        "state_after": sm.state,
        "queues_flushed": flushed,
        "cancelled_tasks": ev.cancelled_tasks,
        "turn_counter": sm.turn_counter,
        "cards": cards,
        "contract_note": "Stop & Flush ALL <50ms (browser audio stops <20ms separately).",
    }


# ---------------------------------------------------------------------------
# 📴 Call end — reset this call's agent memory so the next call starts fresh
# ---------------------------------------------------------------------------

@app.post("/api/call/end")
async def call_end(payload: EndIn) -> Dict[str, Any]:
    """Hang up: wipe this call's short-term conversation memory so the next
    call starts with a clean slate — zero cross-call context bleed (the
    previous turn's entities, parts, policy, stage and ticket status are all
    dropped)."""
    call_id = payload.call_id or "live"
    BRAIN.reset_call(call_id)
    sm = get_state_machine(call_id)
    sm.transition("IDLE")
    return {
        "ok": True,
        "call_id": call_id,
        "memory_reset": True,
        "note": "Call memory cleared — next call starts fresh with no prior context.",
    }


# ---------------------------------------------------------------------------
# Direct TTS (used by jitter-buffer top-up + tests)
# ---------------------------------------------------------------------------

@app.post("/api/tts")
async def tts(payload: TtsIn) -> Dict[str, Any]:
    synth = await synthesize_speech(payload.text, use_cache=payload.use_cache)
    if synth["audio"] and not is_valid_wav(synth["audio"]):
        synth["audio"] = fallback_tts_wav(payload.text)
    return {
        "audio_b64": base64.b64encode(synth["audio"]).decode("ascii"),
        "cached": synth.get("cached"),
        "tts_latency_ms": synth.get("tts_latency_ms"),
        "model": synth.get("model"),
        "voice": synth.get("voice"),
        "fallback": synth.get("fallback"),
    }


# ---------------------------------------------------------------------------
# Evaluation suite
# ---------------------------------------------------------------------------

@app.post("/api/eval")
async def eval_api(payload: Optional[EvalIn] = None) -> Dict[str, Any]:
    if payload is not None and not payload.run:
        return {"ok": False, "reason": "run=false"}
    results = await eval_runner.run_eval_suite()
    return {"ok": True, **results}


@app.get("/api/pipeline-state")
async def pipeline_state() -> Dict[str, Any]:
    return {
        "queues": BUS.snapshot(),
        "stages_ms": {k: round(v, 1) for k, v in STAGE_TELEMETRY.items() if k != "stage_names"},
        "stage_names": STAGE_TELEMETRY["stage_names"],
        "barge_in": {sid: sm.snapshot() for sid, sm in
                     __import__("backend.pipeline.barge_in", fromlist=["x"]).SESSION_STATE_MACHINES.items()},
    }


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


@app.exception_handler(Exception)
async def turn_errors(request: Request, exc: Exception) -> JSONResponse:
    """Zero-crash rule: API never 500s into the demo UI."""
    return JSONResponse(
        status_code=200,
        content={"ok": False, "error": str(exc)[:200],
                 "degraded": "BimaVani Credit-Shield engaged — demo continues."},
    )
