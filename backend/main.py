"""
BimaVani — FastAPI + WebSocket Streaming Application Server (backend/main.py)
=============================================================================
Endpoints
  GET  /api/health              — heartbeat + mode + active sessions + pipeline telemetry
  POST /api/config/key          — hot-reload GNANI_API_KEY without restart
  GET  /api/config              — safe config (masked key, models)
  POST /api/verify-gnani        — live Timbre→Prisma round-trip verification
  POST /api/call/start          — dial greeting (2 clauses + WAV audio)
  POST /api/call/turn           — one driver turn: WAV → STT → Endpointing → Brain → Clauses → TTS
  POST /api/call/barge-in       — <50ms flush + [interrupted] context dump + optional card update
  POST /api/call/end            — hang up & clear per-call memory + session pipeline
  POST /api/tts                 — Timbre synth with MD5 cache
  POST /api/eval                — run the 20-case benchmark suite
  GET  /api/pipeline-state      — active sessions, queue depths, jitter buffers, barge-in latency
  WS   /ws/call/{call_id}       — real-time 20ms PCM audio streaming + Input/Output Jitter Buffers + streaming TTS clauses
Static: serves frontend/ at "/"
"""
from __future__ import annotations

import asyncio
import base64
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

from backend import eval_runner
from backend.i18n import ALL_GNANI_LANGUAGES, LANG_META, get_lang_bundle, normalize_lang_code
from backend.gnani_client import (
    CONFIG,
    fallback_tts_wav,
    is_valid_wav,
    live_mode,
    set_api_key,
    synthesize_speech,
    transcribe_wav,
    verify_round_trip,
    wrap_pcm_as_wav,
)
from backend.pipeline.barge_in import (
    SESSION_STATE_MACHINES,
    get_state_machine,
    reset_state_machine,
)
from backend.pipeline.brain import Brain, llm_status, llm_warmup
from backend.pipeline.clause_buffer import split_into_clauses
from backend.pipeline.endpointing import EndpointingState, analyze_wav_endpoint, endpoint_turn
from backend.pipeline.queues import BUS, REGISTRY, SessionPipeline
from backend.translit import detect_script_language

APP_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = APP_ROOT / "frontend"

BRAIN = Brain()

# Latest per-stage timings (floats only, ms) mirrored for global /api/health view
STAGE_TELEMETRY: Dict[str, float] = {f"stage_{i}": 0.0 for i in range(1, 9)}


def _stage_names() -> List[str]:
    brain_lbl = "Brain (LLM + Tools)" if llm_status().get("active") else "Brain (Rules + Tools)"
    return [
        "Mic (20ms + In-Jitter)",
        f"{CONFIG.stt_model} STT",
        "Auto-VAD Endpoint",
        brain_lbl,
        "Clause Buffer",
        f"{CONFIG.tts_model} TTS",
        "Out Jitter Buffer",
        "Speaker / Barge-In",
    ]


def _stages_snapshot(sess: Optional[SessionPipeline] = None) -> Dict[str, float]:
    src = sess.stages_ms if sess is not None else STAGE_TELEMETRY
    return {f"stage_{i}": round(float(src.get(f"stage_{i}", 0.0)), 2) for i in range(1, 9)}


def _sync_global_stage(stage_key: str, ms: float, sess: Optional[SessionPipeline] = None) -> None:
    STAGE_TELEMETRY[stage_key] = ms
    if sess is not None:
        sess.stages_ms[stage_key] = ms


def _resolve_stt_lang(explicit: Optional[str], sess: Optional[SessionPipeline]) -> Optional[str]:
    val = (explicit or (sess.stt_language if sess else "") or "").strip()
    if val:
        if val.lower() in ("indic-auto", "auto"):
            return "indic-auto"
        return normalize_lang_code(val) or val
    if sess and sess.language in ALL_GNANI_LANGUAGES:
        return sess.language
    return None


async def _synthesize_one_clause(
    c: Dict[str, Any],
    sess: Optional[SessionPipeline] = None,
    language: Optional[str] = None,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    lang = (
        normalize_lang_code(language)
        or normalize_lang_code(c.get("language"))
        or detect_script_language(c.get("text", ""))
    )
    synth = await synthesize_speech(c["text"], language=lang, use_cache=True)
    dt = (time.perf_counter() - t0) * 1000.0
    wav = synth["audio"] if is_valid_wav(synth.get("audio", b"")) else fallback_tts_wav(c["text"])
    if sess is not None:
        sess.output_jitter.push(c["index"], wav)
        sess.output_jitter.pop_ready()
    return {
        "index": c["index"],
        "priority": c["priority"],
        "text": c["text"],
        "language": lang or synth.get("language") or CONFIG.tts_language,
        "audio_base64": "data:audio/wav;base64," + base64.b64encode(wav).decode("ascii"),
        "latency_ms": round(dt, 1),
        "cached": bool(synth.get("cached", False)),
        "fallback": bool(synth.get("fallback", False)),
    }


async def _synthesize_clauses(
    clauses: List[Dict[str, Any]],
    sess: Optional[SessionPipeline] = None,
    language: Optional[str] = None,
) -> tuple[List[Dict[str, Any]], float]:
    """Synthesize up to 2 clauses through Timbre + fallback guard; return payloads and total TTS ms."""
    payloads: List[Dict[str, Any]] = []
    tts_total_ms = 0.0
    for c in clauses:
        cp = await _synthesize_one_clause(c, sess=sess, language=language)
        tts_total_ms += float(cp["latency_ms"])
        payloads.append(cp)
    return payloads, tts_total_ms


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    task = asyncio.create_task(llm_warmup())
    yield
    if not task.done():
        task.cancel()


app = FastAPI(
    title="BimaVani (बीमा-वाणी) — Scalable Streaming Voice RSA + IRDAI Claim Desk",
    version="1.1.0",
    lifespan=_lifespan,
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


class KeyIn(BaseModel):
    api_key: str = Field(min_length=0, max_length=256)


class StartIn(BaseModel):
    call_id: str = "live"
    language: str = ""
    stt_language: str = ""


class TurnIn(BaseModel):
    call_id: str = "live"
    wav_b64: str = ""
    transcript_override: str = ""
    endpoint_mode: str = "auto_vad"         # auto_vad | push_to_talk
    vad_silence_ms: int = 600
    language: str = ""
    stt_language: str = ""


class BargeInIn(BaseModel):
    call_id: str = "live"
    transcript: str = ""
    spoken_text: str = ""
    wav_b64: str = ""
    pcm_b64: str = ""
    source: str = "driver_button"
    update_cards: bool = True
    language: str = ""


class TtsIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    use_cache: bool = True
    language: str = ""


class EvalIn(BaseModel):
    run: bool = True


class EndIn(BaseModel):
    call_id: str = "live"


# ---------------------------------------------------------------------------
# Health / config
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health() -> Dict[str, Any]:
    latest = max(REGISTRY.sessions.values(), key=lambda s: s.last_active_at) if REGISTRY.sessions else None
    return {
        "ok": True,
        "service": "BimaVani",
        "mode": "LIVE_GNANI" if live_mode() else "CREDIT_SHIELD_FALLBACK",
        "stt_model": CONFIG.stt_model,
        "tts_model": CONFIG.tts_model,
        "gnani_base_url": CONFIG.base_url,
        "ws_endpoint": "/ws/call/{call_id}",
        "sessions": REGISTRY.snapshot(),
        "queues": latest.bus.snapshot() if latest else BUS.snapshot(),
        "stages": _stages_snapshot(latest),
        "stage_names": _stage_names(),
        "brain": llm_status(),
    }


@app.get("/api/config")
async def config() -> Dict[str, Any]:
    masked = (
        (CONFIG.api_key[:4] + "…" + CONFIG.api_key[-4:])
        if len(CONFIG.api_key) > 10
        else ("set" if CONFIG.api_key else "")
    )
    return {
        "live_mode": live_mode(),
        "api_key_masked": masked,
        "stt_model": CONFIG.stt_model,
        "stt_language": CONFIG.stt_language,
        "stt_format": CONFIG.stt_format,
        "tts_model": CONFIG.tts_model,
        "tts_voice": CONFIG.tts_voice,
        "tts_language": CONFIG.tts_language,
        "gnani_base_url": CONFIG.base_url,
        "llm_model": os.environ.get("BIMAVANI_LLM_MODEL", "gnani/gnani-evon-v3.3-30B-A3B"),
        "llm_enabled": bool(os.environ.get("LLM_API_KEY")),
        "semaphore_limit": 1,
        "max_concurrent_sessions": REGISTRY.max_sessions,
        "supported_languages": list(ALL_GNANI_LANGUAGES),
        "language_meta": LANG_META,
    }


@app.post("/api/config/key")
async def hot_set_key(payload: KeyIn) -> Dict[str, Any]:
    """Hot-reload GNANI_API_KEY without restarting the server."""
    ok = set_api_key(payload.api_key)
    masked = (
        (CONFIG.api_key[:4] + "…" + CONFIG.api_key[-4:])
        if len(CONFIG.api_key) > 10
        else ("set" if CONFIG.api_key else "")
    )
    return {
        "saved_from_live_config": ok,
        "live_mode": live_mode(),
        "api_key_masked": masked,
        "note": "Key hot-loaded into the running Gnani client (no restart needed).",
    }


# ---------------------------------------------------------------------------
# Live Gnani verification (UI button + CLI share this path)
# ---------------------------------------------------------------------------

@app.post("/api/verify-gnani")
async def verify_gnani() -> Dict[str, Any]:
    result = await verify_round_trip()
    if result.get("verified"):
        hint = f"Live OK — Prisma transcribed speech ({int(result['match_ratio'] * 100)}% match)."
    elif not result.get("fallback"):
        hint = (
            "Live endpoint reached, but Prisma returned an empty transcript"
            + (f" ({result['error']})" if result.get("error") else ".")
        )
    else:
        hint = (
            f"Fallback mode ({result['error']})"
            if result.get("error") and live_mode()
            else "Credit-Shield fallback used (no GNANI_API_KEY). Set the key for live calls."
        )
    result["ui_hint"] = hint
    return result


# ---------------------------------------------------------------------------
# [0] Call start — greeting through Clause Buffer -> Timbre -> Out Jitter Buffer
# ---------------------------------------------------------------------------

@app.post("/api/call/start")
async def call_start(payload: StartIn) -> Dict[str, Any]:
    t0 = time.perf_counter()
    call_id = payload.call_id or "live"
    BRAIN.reset_call(call_id)
    REGISTRY.remove(call_id)
    sess = REGISTRY.get_or_create(call_id)
    sess.stt_language = (payload.stt_language or "").strip()
    sess.language = (payload.language or "").strip()
    sm = get_state_machine(call_id)
    if sm.state != "IDLE":
        sm.transition("IDLE")
    sm.transition("LISTENING")
    sm.transition("THINKING")

    t4 = time.perf_counter()
    started = await BRAIN.start_call(call_id, language=payload.language or None)
    _sync_global_stage("stage_4", (time.perf_counter() - t4) * 1000.0, sess)
    call_lang = started.get("language") or "hi-IN"
    sess.language = call_lang
    sess.active_agent_reply = started["reply_text"]

    await sess.bus.hops["clause_buffer"].put(len(started["clauses"]))
    clause_payloads, tts_ms = await _synthesize_clauses(
        started["clauses"], sess=sess, language=call_lang
    )
    sess.bus.hops["clause_buffer"].get_nowait()
    await sess.bus.hops["tts"].put(len(clause_payloads))
    _sync_global_stage("stage_6", tts_ms, sess)

    t7 = time.perf_counter()
    sess.bus.hops["tts"].get_nowait()
    await sess.bus.hops["jitter_buffer"].put(len(clause_payloads))
    sess.bus.hops["jitter_buffer"].get_nowait()
    _sync_global_stage("stage_7", (time.perf_counter() - t7) * 1000.0, sess)

    sm.transition("SPEAKING")

    return {
        "ok": True,
        "call_id": call_id,
        "language": call_lang,
        "greeting": started["reply_text"],
        "stage": started["memory"]["stage"],
        "ticket_status": started.get("ticket_status"),
        "clauses": clause_payloads,
        "speech_fallback_hint": "app.js falls back to window.speechSynthesis if fallback tone is active.",
        "pipeline": {
            "stages_ms": _stages_snapshot(sess),
            "queues": sess.bus.snapshot(),
            "input_jitter": sess.input_jitter.snapshot(),
            "output_jitter": sess.output_jitter.snapshot(),
        },
        "total_start_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


# ---------------------------------------------------------------------------
# [1] Mic → [2] Prisma STT → [3] Endpointing → [4] Brain → [5] Clauses → [6] TTS
# ---------------------------------------------------------------------------

def _decode_b64(raw: str) -> bytes:
    if not raw:
        return b""
    if raw.startswith("data:") and "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        return base64.b64decode(raw)
    except Exception:
        return b""


async def _run_turn_core(
    call_id: str,
    sess: SessionPipeline,
    sm: Any,
    wav_bytes: bytes = b"",
    transcript_override: str = "",
    endpoint_mode: str = "auto_vad",
    vad_silence_ms: int = 600,
    language: str = "",
    stt_language: str = "",
) -> Dict[str, Any]:
    t_stage1 = time.perf_counter()

    # [1] Mic ingest (+ feed through InputJitterBuffer when raw WAV PCM present)
    _sync_global_stage("stage_1", (time.perf_counter() - t_stage1) * 1000.0, sess)
    await sess.bus.hops["mic_ingest"].put(len(wav_bytes))

    # [3] Endpointing analysis (on raw WAV frames if audio was sent)
    ep_mode = endpoint_mode if endpoint_mode in ("auto_vad", "push_to_talk") else "auto_vad"
    sess.endpoint_state.configure(mode=ep_mode, vad_silence_ms=vad_silence_ms or 600)
    if wav_bytes and len(wav_bytes) > 44:
        ep = analyze_wav_endpoint(wav_bytes, sess.endpoint_state)
    else:
        t3 = time.perf_counter()
        ep = endpoint_turn(sess.endpoint_state)
        ep["latency_ms"] = round((time.perf_counter() - t3) * 1000, 2)
    _sync_global_stage("stage_3", float(ep.get("latency_ms") or 0.0), sess)
    sess.bus.hops["mic_ingest"].get_nowait()
    await sess.bus.hops["endpointing"].put(1)

    # [2] Prisma STT (or scripted override -> 0 ms / 0 credits)
    stt_lang = _resolve_stt_lang(stt_language or language, sess)
    if transcript_override:
        transcript = transcript_override.strip()
        stt_meta: Dict[str, Any] = {
            "transcript": transcript,
            "request_id": "scripted-" + call_id,
            "stt_latency_ms": 0.0,
            "fallback": False,
            "skip_reason": "scripted_demo_transcript (0 credits)",
        }
    elif wav_bytes and len(wav_bytes) > 44:
        stt_meta = await transcribe_wav(wav_bytes, language_code=stt_lang)
    else:
        stt_meta = {"transcript": "", "request_id": "empty", "stt_latency_ms": 0.0, "fallback": True}
    _sync_global_stage("stage_2", float(stt_meta.get("stt_latency_ms") or 0.0), sess)
    sess.bus.hops["endpointing"].get_nowait()
    await sess.bus.hops["stt"].put(stt_meta.get("transcript", ""))
    transcript = stt_meta.get("transcript", "")

    if not transcript:
        sess.bus.hops["stt"].get_nowait()
        return await _empty_turn(call_id, sess, stt_meta, ep, sm, language=language)

    # [4] Brain (deterministic tools + memory; optional LLM assist)
    sm.transition("THINKING")
    t4 = time.perf_counter()
    result = await BRAIN.process_turn(call_id, transcript, stt_meta, language=language or None)
    _sync_global_stage("stage_4", (time.perf_counter() - t4) * 1000.0, sess)
    reply_lang = result.get("language") or "hi-IN"
    sess.language = reply_lang
    sess.active_agent_reply = result.get("reply_text", "")
    sess.bus.hops["stt"].get_nowait()
    await sess.bus.hops["brain"].put(len(result["clauses"]))

    # [5] 2-Clause Buffer (time splitting + bus hop)
    t5 = time.perf_counter()
    clause_batch = split_into_clauses(result["reply_text"], call_id, language=reply_lang)
    clauses_list = clause_batch.as_list()
    sess.bus.hops["brain"].get_nowait()
    await sess.bus.hops["clause_buffer"].put([c["index"] for c in clauses_list])
    _sync_global_stage("stage_5", (time.perf_counter() - t5) * 1000.0, sess)

    # [6] Timbre TTS (<=2 clauses)
    clauses_payload, tts_ms = await _synthesize_clauses(clauses_list, sess=sess, language=reply_lang)
    _sync_global_stage("stage_6", tts_ms, sess)
    sess.bus.hops["clause_buffer"].get_nowait()
    await sess.bus.hops["tts"].put(sum(len(c["audio_base64"]) for c in clauses_payload))

    # [7] Output Jitter Buffer prep
    t7 = time.perf_counter()
    sess.bus.hops["tts"].get_nowait()
    await sess.bus.hops["jitter_buffer"].put(len(clauses_payload))
    sess.bus.hops["jitter_buffer"].get_nowait()
    _sync_global_stage("stage_7", (time.perf_counter() - t7) * 1000.0, sess)

    sm.transition("SPEAKING")

    clause_meta = [
        {
            "index": cp["index"],
            "priority": cp["priority"],
            "text": cp["text"],
            "language": cp.get("language", reply_lang),
            "tts_latency_ms": cp["latency_ms"],
            "cached": cp["cached"],
            "fallback": cp["fallback"],
        }
        for cp in clauses_payload
    ]

    result["pipeline"] = {
        "stages_ms": _stages_snapshot(sess),
        "endpointing": ep,
        "stt": {k: stt_meta.get(k) for k in ("transcript", "request_id", "stt_latency_ms", "fallback")},
        "queues": sess.bus.snapshot(),
        "input_jitter": sess.input_jitter.snapshot(),
        "output_jitter": sess.output_jitter.snapshot(),
    }
    result["clause_audio"] = clause_meta
    result["clauses"] = clauses_payload
    result["total_turn_ms"] = round((time.perf_counter() - t_stage1) * 1000, 1)
    result["barge_in_hint"] = "Press Interrupt during read-back to correct policy fields."
    return result


@app.post("/api/call/turn")
async def call_turn(payload: TurnIn) -> Dict[str, Any]:
    call_id = payload.call_id or "live"
    sess = REGISTRY.get_or_create(call_id)
    sm = get_state_machine(call_id)
    _sm_ensure_listening(sm)

    cur_task = asyncio.current_task()
    if cur_task is not None:
        sm.register_task(cur_task)

    wav_bytes = _decode_b64(payload.wav_b64)
    return await _run_turn_core(
        call_id=call_id,
        sess=sess,
        sm=sm,
        wav_bytes=wav_bytes,
        transcript_override=payload.transcript_override,
        endpoint_mode=payload.endpoint_mode,
        vad_silence_ms=payload.vad_silence_ms,
        language=payload.language,
        stt_language=payload.stt_language,
    )


async def _empty_turn(
    call_id: str,
    sess: SessionPipeline,
    stt_meta: Dict[str, Any],
    ep: Dict[str, Any],
    sm: Any,
    language: str = "",
) -> Dict[str, Any]:
    """STT returned no words — never stay silent: speak a 2-clause retry prompt."""
    sm.transition("THINKING")
    mem = BRAIN.get_or_create_call(call_id)
    lang = (
        normalize_lang_code(language)
        or normalize_lang_code(sess.language)
        or normalize_lang_code(mem.entities.get("language"))
        or "hi-IN"
    )
    reply = get_lang_bundle(lang)["empty_turn"]
    sess.active_agent_reply = reply
    t5 = time.perf_counter()
    batch = split_into_clauses(reply, call_id + "-empty", language=lang)
    _sync_global_stage("stage_5", (time.perf_counter() - t5) * 1000.0, sess)

    clauses_payload, tts_ms = await _synthesize_clauses(batch.as_list(), sess=sess, language=lang)
    _sync_global_stage("stage_6", tts_ms, sess)
    sm.transition("SPEAKING")

    hint = None
    if stt_meta.get("fallback"):
        hint = (
            "Prisma STT is in Credit-Shield fallback (GNANI_API_KEY missing) — "
            "set the key via API Key modal, or use the typed-line box / scenario chips."
        )
    return {
        "ok": True,
        "call_id": call_id,
        "language": lang,
        "transcript": "",
        "reply_text": reply,
        "stt_empty": True,
        "hint": hint,
        "clauses": clauses_payload,
        "clause_audio": [
            {k: c[k] for k in ("index", "priority", "text", "cached", "fallback")}
            for c in clauses_payload
        ],
        "ticket_status": "DRAFT",
        "entities": mem.entities,
        "tools_invoked": [],
        "memory": mem.snapshot(),
        "pipeline": {
            "stages_ms": _stages_snapshot(sess),
            "endpointing": ep,
            "stt": stt_meta,
            "queues": sess.bus.snapshot(),
            "input_jitter": sess.input_jitter.snapshot(),
            "output_jitter": sess.output_jitter.snapshot(),
        },
    }


# ---------------------------------------------------------------------------
# Barge-in — Stop & Flush ALL (<50ms contract) + [interrupted] context dump
# ---------------------------------------------------------------------------

def _sm_ensure_listening(sm: TurnStateMachine) -> None:
    """Safely bring TurnStateMachine into LISTENING from any non-LISTENING state."""
    if sm.state == "LISTENING":
        return
    if sm.state in ("IDLE", "SPEAKING", "INTERRUPTED"):
        sm.transition("LISTENING")
        return
    if sm.state == "THINKING":
        sm.transition("IDLE")
        sm.transition("LISTENING")


async def _handle_barge_in_core(
    call_id: str,
    sess: SessionPipeline,
    sm: Any,
    transcript: str = "",
    spoken_text: str = "",
    wav_bytes: bytes = b"",
    pcm_bytes: bytes = b"",
    source: str = "driver_button",
    update_cards: bool = True,
    language: str = "",
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    state_before = sm.state

    flushed_info = sess.flush_all()
    ev = await sm.trigger_barge_in(
        transcript,
        flush_fn=lambda: {"flushed_clauses": flushed_info["total_flushed"]},
    )
    sm.resume_listening()
    flush_ms = round((time.perf_counter() - t0) * 1000, 1)
    _sync_global_stage("stage_8", flush_ms, sess)

    # Snapshot any mic audio captured during/before interruption
    dump_snap = sess.input_jitter.dump_interruption_snapshot()
    if not wav_bytes and pcm_bytes:
        wav_bytes = wrap_pcm_as_wav(pcm_bytes, sample_rate=16000)
    if not wav_bytes and dump_snap.get("wav"):
        wav_bytes = dump_snap["wav"]

    audio_stats: Dict[str, Any] = {
        "duration_ms": dump_snap.get("duration_ms", 0.0),
        "peak_dbfs": dump_snap.get("peak_dbfs", -120.0),
        "jitter_ms_est": dump_snap.get("jitter_ms_est", 0.0),
    }
    stt_lang = _resolve_stt_lang(language, sess)
    if wav_bytes and len(wav_bytes) > 44:
        ep_info = analyze_wav_endpoint(wav_bytes, sess.endpoint_state)
        audio_stats["peak_dbfs"] = ep_info.get("peak_dbfs", audio_stats["peak_dbfs"])
        audio_stats["duration_ms"] = round(ep_info.get("frames", 0) * 20.0, 1)
        if not transcript:
            stt = await transcribe_wav(wav_bytes, language_code=stt_lang)
            if stt.get("transcript"):
                transcript = stt["transcript"].strip()

    interrupted_ctx = BRAIN.record_interruption(
        call_id=call_id,
        mic_text=transcript,
        spoken_prefix=spoken_text,
        full_reply=sess.active_agent_reply,
        audio_stats=audio_stats,
        source=source,
    )

    cards: Dict[str, Any] = {}
    if update_cards and transcript:
        _sm_ensure_listening(sm)
        sm.transition("THINKING")
        updated = await BRAIN.process_turn(call_id, transcript, {"barge_in": True}, language=language or None)
        reply_lang = updated.get("language") or "hi-IN"
        sess.language = reply_lang
        sess.active_agent_reply = updated.get("reply_text", "")
        clauses_payload, tts_ms = await _synthesize_clauses(updated["clauses"], sess=sess, language=reply_lang)
        _sync_global_stage("stage_6", tts_ms, sess)
        sm.transition("SPEAKING")
        cards = {
            "stage": updated["stage"],
            "language": reply_lang,
            "entities": updated["entities"],
            "tools_invoked": updated["tools_invoked"],
            "reply_text": updated["reply_text"],
            "ticket_status": updated["ticket_status"],
            "clauses": clauses_payload,
        }

    return {
        "call_id": call_id,
        "source": source,
        "flush_latency_ms": flush_ms,
        "state_before": state_before,
        "state_after": sm.state,
        "queues_flushed": flushed_info["queues"],
        "output_jitter_flushed": flushed_info["output_jitter_flushed"],
        "cancelled_tasks": ev.cancelled_tasks,
        "turn_counter": sm.turn_counter,
        "interrupted_context": interrupted_ctx,
        "memory": BRAIN.get_or_create_call(call_id).snapshot(),
        "cards": cards,
        "contract_note": "Stop & Flush ALL <50ms (browser audio stops <20ms separately); mic/agent cut tagged [interrupted].",
    }


@app.post("/api/call/barge-in")
async def call_barge_in(payload: BargeInIn) -> Dict[str, Any]:
    call_id = payload.call_id or "live"
    sess = REGISTRY.get_or_create(call_id)
    sm = get_state_machine(call_id)
    return await _handle_barge_in_core(
        call_id=call_id,
        sess=sess,
        sm=sm,
        transcript=payload.transcript,
        spoken_text=payload.spoken_text,
        wav_bytes=_decode_b64(payload.wav_b64),
        pcm_bytes=_decode_b64(payload.pcm_b64),
        source=payload.source or "driver_button",
        update_cards=payload.update_cards,
        language=payload.language,
    )


# ---------------------------------------------------------------------------
# WebSocket Streaming Pipeline (/ws/call/{call_id})
# ---------------------------------------------------------------------------

@app.websocket("/ws/call/{call_id}")
async def ws_call(websocket: WebSocket, call_id: str) -> None:
    await websocket.accept()
    call_id = call_id or "live"
    sess = REGISTRY.get_or_create(call_id)
    sm = get_state_machine(call_id)
    ws_lock = asyncio.Lock()
    active_turn_task: Optional[asyncio.Task] = None
    frame_counter = 0
    server_auto_endpoint = True

    async def send_evt(payload: Dict[str, Any]) -> None:
        async with ws_lock:
            await websocket.send_json(payload)

    async def _stream_turn(
        wav_bytes: bytes = b"",
        transcript_override: str = "",
        mode: str = "auto_vad",
        vad_ms: int = 600,
        language: str = "",
        stt_language: str = "",
    ) -> None:
        t_start = time.perf_counter()
        cur = asyncio.current_task()
        if cur is not None:
            sm.register_task(cur)
        try:
            _sm_ensure_listening(sm)

            # [1] + [3]
            _sync_global_stage("stage_1", (time.perf_counter() - t_start) * 1000.0, sess)
            await sess.bus.hops["mic_ingest"].put(len(wav_bytes))
            sess.endpoint_state.configure(mode=mode, vad_silence_ms=vad_ms)
            if wav_bytes and len(wav_bytes) > 44:
                ep = analyze_wav_endpoint(wav_bytes, sess.endpoint_state)
            else:
                t3 = time.perf_counter()
                ep = endpoint_turn(sess.endpoint_state)
                ep["latency_ms"] = round((time.perf_counter() - t3) * 1000, 2)
            _sync_global_stage("stage_3", float(ep.get("latency_ms") or 0.0), sess)
            sess.bus.hops["mic_ingest"].get_nowait()
            await sess.bus.hops["endpointing"].put(1)

            # [2] STT
            stt_lang = _resolve_stt_lang(stt_language or language, sess)
            if transcript_override:
                transcript = transcript_override.strip()
                stt_meta: Dict[str, Any] = {
                    "transcript": transcript,
                    "request_id": "scripted-" + call_id,
                    "stt_latency_ms": 0.0,
                    "fallback": False,
                }
            elif wav_bytes and len(wav_bytes) > 44:
                stt_meta = await transcribe_wav(wav_bytes, language_code=stt_lang)
            else:
                stt_meta = {"transcript": "", "request_id": "empty", "stt_latency_ms": 0.0, "fallback": True}

            _sync_global_stage("stage_2", float(stt_meta.get("stt_latency_ms") or 0.0), sess)
            sess.bus.hops["endpointing"].get_nowait()
            await sess.bus.hops["stt"].put(stt_meta.get("transcript", ""))
            transcript = stt_meta.get("transcript", "")

            await send_evt({
                "type": "stt_result",
                "call_id": call_id,
                "transcript": transcript,
                "stt": stt_meta,
                "endpointing": ep,
            })

            if not transcript:
                sess.bus.hops["stt"].get_nowait()
                empty_res = await _empty_turn(call_id, sess, stt_meta, ep, sm, language=language)
                await send_evt({"type": "brain_result", **empty_res})
                for cp in empty_res["clauses"]:
                    await send_evt({"type": "tts_clause", "call_id": call_id, "clause": cp})
                await send_evt({"type": "turn_complete", "call_id": call_id, "pipeline": empty_res["pipeline"]})
                return

            # [4] Brain
            sm.transition("THINKING")
            t4 = time.perf_counter()
            result = await BRAIN.process_turn(call_id, transcript, stt_meta, language=language or None)
            _sync_global_stage("stage_4", (time.perf_counter() - t4) * 1000.0, sess)
            reply_lang = result.get("language") or "hi-IN"
            sess.language = reply_lang
            sess.active_agent_reply = result.get("reply_text", "")
            sess.bus.hops["stt"].get_nowait()
            await sess.bus.hops["brain"].put(len(result["clauses"]))

            # [5] 2-Clause Buffer
            t5 = time.perf_counter()
            clauses_list = split_into_clauses(result["reply_text"], call_id, language=reply_lang).as_list()
            for cl in clauses_list:
                cl["language"] = reply_lang
            sess.bus.hops["brain"].get_nowait()
            await sess.bus.hops["clause_buffer"].put([c["index"] for c in clauses_list])
            _sync_global_stage("stage_5", (time.perf_counter() - t5) * 1000.0, sess)

            await send_evt({
                "type": "brain_result",
                "call_id": call_id,
                "stage": result["stage"],
                "language": reply_lang,
                "transcript": transcript,
                "reply_text": result["reply_text"],
                "entities": result["entities"],
                "tools_invoked": result["tools_invoked"],
                "ticket_status": result["ticket_status"],
                "memory": result["memory"],
                "clauses_meta": clauses_list,
            })

            # [6] -> [7] -> [8] Stream Clause 1 immediately before Clause 2 finishes!
            sm.transition("SPEAKING")
            sess.bus.hops["clause_buffer"].get_nowait()
            tts_total_ms = 0.0
            streamed_clauses: List[Dict[str, Any]] = []
            for c in clauses_list:
                cp = await _synthesize_one_clause(c, sess=sess, language=reply_lang)
                tts_total_ms += float(cp["latency_ms"])
                streamed_clauses.append(cp)
                await sess.bus.hops["tts"].put(len(cp["audio_base64"]))
                sess.bus.hops["tts"].get_nowait()
                await sess.bus.hops["jitter_buffer"].put(cp["index"])
                sess.bus.hops["jitter_buffer"].get_nowait()
                await send_evt({"type": "tts_clause", "call_id": call_id, "clause": cp})

            _sync_global_stage("stage_6", tts_total_ms, sess)
            _sync_global_stage("stage_7", 0.5, sess)
            await send_evt({
                "type": "turn_complete",
                "call_id": call_id,
                "language": reply_lang,
                "total_turn_ms": round((time.perf_counter() - t_start) * 1000, 1),
                "pipeline": {
                    "stages_ms": _stages_snapshot(sess),
                    "endpointing": ep,
                    "stt": {k: stt_meta.get(k) for k in ("transcript", "request_id", "stt_latency_ms", "fallback")},
                    "queues": sess.bus.snapshot(),
                    "input_jitter": sess.input_jitter.snapshot(),
                    "output_jitter": sess.output_jitter.snapshot(),
                },
            })
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            if sm.state != "IDLE":
                sm.transition("IDLE")
            sm.transition("LISTENING")
            await send_evt({
                "type": "error",
                "call_id": call_id,
                "error": f"Turn processing error: {exc}",
            })
            await send_evt({
                "type": "turn_complete",
                "call_id": call_id,
                "error": str(exc),
                "total_turn_ms": round((time.perf_counter() - t_start) * 1000, 1),
                "pipeline": {
                    "stages_ms": _stages_snapshot(sess),
                    "queues": sess.bus.snapshot(),
                    "input_jitter": sess.input_jitter.snapshot(),
                    "output_jitter": sess.output_jitter.snapshot(),
                },
            })

    await send_evt({
        "type": "ready",
        "call_id": call_id,
        "jitter_config": {
            "frame_ms": 20,
            "input_target_ms": sess.input_jitter.target_frames * 20,
            "output_prebuffer_ms": sess.output_jitter.prebuffer_ms,
        },
        "sessions": REGISTRY.snapshot(),
    })

    try:
        while True:
            msg = await websocket.receive_json()
            sess.touch()
            mtype = str(msg.get("type") or "")

            try:
                if mtype == "ping":
                    await send_evt({"type": "pong", "sessions": REGISTRY.snapshot()})

                elif mtype == "config":
                    sess.endpoint_state.configure(
                        mode=msg.get("endpoint_mode"),
                        vad_silence_ms=msg.get("vad_silence_ms"),
                    )
                    if msg.get("vad_silence_ms"):
                        sess.input_jitter.configure_vad(int(msg["vad_silence_ms"]))
                    if "server_auto_endpoint" in msg:
                        server_auto_endpoint = bool(msg["server_auto_endpoint"])
                    if msg.get("stt_language") is not None:
                        sess.stt_language = str(msg["stt_language"] or "").strip()
                    if msg.get("language") is not None:
                        sess.language = str(msg["language"] or "").strip()

                elif mtype == "start":
                    if active_turn_task and not active_turn_task.done():
                        active_turn_task.cancel()
                    BRAIN.reset_call(call_id)
                    sess.input_jitter.clear_speech()
                    sess.output_jitter.flush()
                    if msg.get("stt_language") is not None:
                        sess.stt_language = str(msg["stt_language"] or "").strip()
                    req_lang = str(msg.get("language") or "").strip()
                    if sm.state != "IDLE":
                        sm.transition("IDLE")
                    sm.transition("LISTENING")
                    sm.transition("THINKING")
                    started = await BRAIN.start_call(call_id, language=req_lang or None)
                    call_lang = started.get("language") or "hi-IN"
                    sess.language = call_lang
                    sess.active_agent_reply = started["reply_text"]
                    await send_evt({
                        "type": "brain_result",
                        "call_id": call_id,
                        "is_greeting": True,
                        "language": call_lang,
                        "stage": started["memory"]["stage"],
                        "reply_text": started["reply_text"],
                        "ticket_status": started["ticket_status"],
                        "memory": started["memory"],
                        "entities": started["memory"]["entities"],
                    })
                    sm.transition("SPEAKING")
                    for c in started["clauses"]:
                        cp = await _synthesize_one_clause(c, sess=sess, language=call_lang)
                        await send_evt({"type": "tts_clause", "call_id": call_id, "clause": cp})
                    await send_evt({
                        "type": "turn_complete",
                        "call_id": call_id,
                        "is_greeting": True,
                        "language": call_lang,
                        "pipeline": {
                            "stages_ms": _stages_snapshot(sess),
                            "queues": sess.bus.snapshot(),
                            "input_jitter": sess.input_jitter.snapshot(),
                            "output_jitter": sess.output_jitter.snapshot(),
                        },
                    })

                elif mtype == "audio_frame":
                    pcm = _decode_b64(msg.get("pcm_b64", ""))
                    seq = msg.get("seq")
                    ts_ms = msg.get("ts_ms")
                    events = sess.input_jitter.push(pcm, seq=seq, ts_ms=ts_ms)
                    frame_counter += 1
                    triggered_vad_barge = False
                    triggered_endpoint = False
                    for ev in events:
                        if ev["vad"] == "speech" and sm.state in ("SPEAKING", "THINKING") and not triggered_vad_barge:
                            triggered_vad_barge = True
                            if active_turn_task and not active_turn_task.done():
                                active_turn_task.cancel()
                            ack = await _handle_barge_in_core(
                                call_id=call_id,
                                sess=sess,
                                sm=sm,
                                source="server_vad_mic",
                                update_cards=False,
                            )
                            await send_evt({"type": "barge_in_ack", **ack})
                        elif ev["vad"] == "endpoint" and sess.endpoint_state.mode == "auto_vad" and server_auto_endpoint:
                            triggered_endpoint = True
                            wav = sess.input_jitter.take_utterance_wav()
                            if wav:
                                await send_evt({
                                    "type": "endpoint_detected",
                                    "call_id": call_id,
                                    "input_jitter": sess.input_jitter.snapshot(),
                                })
                                if active_turn_task and not active_turn_task.done():
                                    active_turn_task.cancel()
                                active_turn_task = asyncio.create_task(
                                    _stream_turn(
                                        wav_bytes=wav,
                                        mode=sess.endpoint_state.mode,
                                        vad_ms=sess.endpoint_state.vad_silence_ms,
                                    )
                                )
                    if frame_counter % 5 == 0 or triggered_vad_barge or triggered_endpoint:
                        await send_evt({
                            "type": "vad_state",
                            "in_speech": sess.input_jitter._in_speech,
                            "dbfs": sess.input_jitter.last_dbfs,
                            "input_jitter": sess.input_jitter.snapshot(),
                        })

                elif mtype == "barge_in":
                    if active_turn_task and not active_turn_task.done():
                        active_turn_task.cancel()
                    ack = await _handle_barge_in_core(
                        call_id=call_id,
                        sess=sess,
                        sm=sm,
                        transcript=str(msg.get("transcript") or ""),
                        spoken_text=str(msg.get("spoken_text") or ""),
                        wav_bytes=_decode_b64(msg.get("wav_b64", "")),
                        pcm_bytes=_decode_b64(msg.get("pcm_b64", "")),
                        source=str(msg.get("source") or "client_barge_in"),
                        update_cards=bool(msg.get("update_cards", True)),
                        language=str(msg.get("language") or ""),
                    )
                    await send_evt({"type": "barge_in_ack", **ack})

                elif mtype == "end_turn":
                    if active_turn_task and not active_turn_task.done():
                        active_turn_task.cancel()
                    wav_bytes = _decode_b64(msg.get("wav_b64", ""))
                    if not wav_bytes and not msg.get("transcript_override"):
                        wav_bytes = sess.input_jitter.take_utterance_wav(include_preroll_if_empty=True)
                    active_turn_task = asyncio.create_task(
                        _stream_turn(
                            wav_bytes=wav_bytes,
                            transcript_override=str(msg.get("transcript_override") or ""),
                            mode=str(msg.get("endpoint_mode") or sess.endpoint_state.mode),
                            vad_ms=int(msg.get("vad_silence_ms") or sess.endpoint_state.vad_silence_ms),
                            language=str(msg.get("language") or ""),
                            stt_language=str(msg.get("stt_language") or ""),
                        )
                    )

                elif mtype == "end_call":
                    if active_turn_task and not active_turn_task.done():
                        active_turn_task.cancel()
                    BRAIN.reset_call(call_id)
                    reset_state_machine(call_id)
                    REGISTRY.remove(call_id)
                    await send_evt({"type": "call_ended", "call_id": call_id})
                    break
            except WebSocketDisconnect:
                raise
            except Exception as exc:
                await send_evt({
                    "type": "error",
                    "call_id": call_id,
                    "message_type": mtype,
                    "error": str(exc),
                })

    except WebSocketDisconnect:
        if active_turn_task and not active_turn_task.done():
            active_turn_task.cancel()


# ---------------------------------------------------------------------------
# Call end — reset call memory, state machine, and session pipeline
# ---------------------------------------------------------------------------

@app.post("/api/call/end")
async def call_end(payload: EndIn) -> Dict[str, Any]:
    call_id = payload.call_id or "live"
    BRAIN.reset_call(call_id)
    reset_state_machine(call_id)
    REGISTRY.remove(call_id)
    return {
        "ok": True,
        "call_id": call_id,
        "memory_reset": True,
        "note": "Call memory cleared — next call starts fresh with no prior context.",
    }


# ---------------------------------------------------------------------------
# Direct TTS
# ---------------------------------------------------------------------------

@app.post("/api/tts")
async def tts(payload: TtsIn) -> Dict[str, Any]:
    lang = normalize_lang_code(payload.language) or detect_script_language(payload.text)
    synth = await synthesize_speech(payload.text, language=lang, use_cache=payload.use_cache)
    wav = synth["audio"] if is_valid_wav(synth.get("audio", b"")) else fallback_tts_wav(payload.text)
    return {
        "audio_b64": base64.b64encode(wav).decode("ascii"),
        "cached": synth.get("cached"),
        "tts_latency_ms": synth.get("tts_latency_ms"),
        "model": synth.get("model"),
        "voice": synth.get("voice"),
        "language": synth.get("language") or lang or CONFIG.tts_language,
        "fallback": synth.get("fallback"),
    }


# ---------------------------------------------------------------------------
# Evaluation suite (non-polluting: does not write fake rows to call_history.json)
# ---------------------------------------------------------------------------

@app.post("/api/eval")
async def eval_api(payload: Optional[EvalIn] = None) -> Dict[str, Any]:
    if payload is not None and not payload.run:
        return {"ok": False, "reason": "run=false"}
    results = await eval_runner.run_eval_suite(persist_history=False)
    return {"ok": True, **results}


@app.get("/api/pipeline-state")
async def pipeline_state(call_id: Optional[str] = None) -> Dict[str, Any]:
    sess = REGISTRY.get(call_id) if call_id else None
    return {
        "sessions": REGISTRY.snapshot(),
        "queues": sess.bus.snapshot() if sess else BUS.snapshot(),
        "stages_ms": _stages_snapshot(sess),
        "stage_names": _stage_names(),
        "input_jitter": sess.input_jitter.snapshot() if sess else None,
        "output_jitter": sess.output_jitter.snapshot() if sess else None,
        "barge_in": {sid: sm.snapshot() for sid, sm in SESSION_STATE_MACHINES.items()},
    }


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


@app.exception_handler(Exception)
async def turn_errors(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": str(exc)[:200],
            "degraded": "Internal error encountered; client should recover gracefully.",
        },
    )
