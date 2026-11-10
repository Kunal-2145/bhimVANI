"""
BimaVani — Gnani.ai Vachana/Artha Ecosystem Client (backend/gnani_client.py)
===========================================================================
Wraps the LIVE Gnani.ai APIs with production guardrails:

  * STT  (Prisma):   POST https://api.vachana.ai/stt/v3
  * TTS  (Timbre):   POST https://api.vachana.ai/api/v1/tts/inference
  * Header X-API-Key-ID: <GNANI_API_KEY> on every call.
  * G2 GUARDRAIL: a single asyncio.Semaphore(1) around ALL live calls —
    student keys allow strictly 1 request at a time. Auto-retries on
    HTTP 429 / 5xx with exponential backoff + jitter.
  * Credit-Shield Fallback Mode: with no GNANI_API_KEY the app NEVER
    crashes — STT returns a deterministic pass-through and TTS synthesizes
    a locally generated 24kHz spoken-tone WAV (0 credits consumed).
  * 44-byte RIFF/WAVE header wrapper: if Timbre returns raw PCM (no "RIFF"
    magic), it is framed as 16-bit mono 24kHz WAV so browser
    AudioContext.decodeAudioData never fails.
  * MD5 disk cache (backend/cache/tts/): every synthesized clause is cached
    as md5(text|voice|model|lang).wav so demo re-runs cost 0 credits.
  * Optionally uses the `gnani-vachana` SDK if installed (REST path is the
    default; SDK import failures are non-fatal).
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import random
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx

try:  # optional SDK (non-fatal if absent)
    from gnani import stt as _gnani_stt_sdk  # type: ignore
    from gnani import tts as _gnani_tts_sdk  # type: ignore
    _SDK_AVAILABLE = True
except Exception:  # pragma: no cover
    _gnani_stt_sdk = None
    _gnani_tts_sdk = None
    _SDK_AVAILABLE = False

# ---------------------------------------------------------------------------
# Configuration (loaded from env at import; hot-reloadable via set_api_key)
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "https://api.vachana.ai"
DEFAULT_STT_MODEL = "prisma-v2.5"
ALLOWED_STT_MODELS = {"prisma-v2.5", "prisma-v3.0"}
DEFAULT_TTS_MODEL = "timbre-v2.5"          # NEVER timbre-v2.0 (retired!)
ALLOWED_TTS_MODELS = {"timbre-v2.5", "timbre-v3.0"}
DEFAULT_VOICE = "Nalini"
DEFAULT_LANGUAGE = "hi-IN"
DEFAULT_KEYTERMS = (
    "Splendor,Swift,Activa,Bolero,NH-44,Karnal,"
    "radiator,bumper,headlight,axle,puncture,cashless,policy"
)
STT_FORMATS = {"codemix", "transcribe"}
STT_LANGS = {"hi-IN", "indic-auto", "en-IN", "mr-IN", "ta-IN", "te-IN"}

_SEMAPHORE: Optional[asyncio.Semaphore] = None      # live-call gate
_LOCK = asyncio.Lock()                              # config hot-reload lock

_CACHE_DIR = Path(os.path.dirname(os.path.abspath(__file__))) / "cache" / "tts"


@dataclass
class GnaniConfig:
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    stt_model: str = DEFAULT_STT_MODEL
    stt_language: str = DEFAULT_LANGUAGE
    stt_format: str = "codemix"
    tts_model: str = DEFAULT_TTS_MODEL
    tts_voice: str = DEFAULT_VOICE
    tts_language: str = DEFAULT_LANGUAGE
    tts_sample_rate: int = 24000
    timeout_s: float = 60.0
    max_retries: int = 3
    telemetry: Dict[str, Any] = field(default_factory=dict)


def _load_config() -> GnaniConfig:
    """Build config from environment (supports python-dotenv preloading)."""
    key = (os.environ.get("GNANI_API_KEY") or "").strip()
    stt_model = os.environ.get("GNANI_STT_MODEL", DEFAULT_STT_MODEL)
    stt_lang = os.environ.get("GNANI_STT_LANGUAGE", DEFAULT_LANGUAGE)
    stt_format = os.environ.get("GNANI_STT_FORMAT", "codemix")
    tts_model = os.environ.get("GNANI_TTS_MODEL", DEFAULT_TTS_MODEL)
    return GnaniConfig(
        api_key=key,
        base_url=os.environ.get("GNANI_BASE_URL", DEFAULT_BASE_URL),
        stt_model=stt_model if stt_model in ALLOWED_STT_MODELS else DEFAULT_STT_MODEL,
        stt_language=stt_lang if stt_lang in STT_LANGS else DEFAULT_LANGUAGE,
        stt_format=stt_format if stt_format in STT_FORMATS else "codemix",
        tts_model=tts_model if tts_model in ALLOWED_TTS_MODELS else DEFAULT_TTS_MODEL,
        tts_voice=os.environ.get("GNANI_TTS_VOICE", DEFAULT_VOICE),
        tts_language=os.environ.get("GNANI_TTS_LANGUAGE", DEFAULT_LANGUAGE),
    )


CONFIG: GnaniConfig = _load_config()


def _sem() -> asyncio.Semaphore:
    """G2 guardrail: ONE global semaphore -> student API = 1 live request."""
    global _SEMAPHORE
    if _SEMAPHORE is None:
        _SEMAPHORE = asyncio.Semaphore(1)
    return _SEMAPHORE


def set_api_key(key: str) -> bool:
    """Runtime hot-reload of GNANI_API_KEY (from the UI '🔑 Set Key' modal)."""
    CONFIG.api_key = (key or "").strip()
    return has_api_key()


def has_api_key() -> bool:
    return bool(CONFIG.api_key)


def live_mode() -> bool:
    return has_api_key()


# ---------------------------------------------------------------------------
# 44-byte RIFF/WAVE header wrapper (browser decodeAudioData never fails)
# ---------------------------------------------------------------------------

def make_wav_header(num_samples: int, sample_rate: int = 24000,
                    channels: int = 1, bits_per_sample: int = 16) -> bytes:
    """Standard 44-byte PCM WAV header."""
    data_bytes = num_samples * channels * (bits_per_sample // 8)
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_bytes,          # ChunkSize
        b"WAVE",
        b"fmt ",                  # Subchunk1ID
        16,                       # Subchunk1Size (PCM)
        1,                        # AudioFormat = PCM
        channels,                 # NumChannels
        sample_rate,              # SampleRate
        sample_rate * channels * (bits_per_sample // 8),  # ByteRate
        channels * (bits_per_sample // 8),                # BlockAlign
        bits_per_sample,          # BitsPerSample
        b"data",                  # Subchunk2ID
        data_bytes,               # Subchunk2Size
    )


def wrap_pcm_as_wav(pcm: bytes, sample_rate: int = 24000,
                    channels: int = 1, bits_per_sample: int = 16) -> bytes:
    """If bytes are NOT already RIFF/WAVE, frame raw PCM into a WAV blob."""
    if pcm[:3] == b"RIF" or pcm.startswith(b"RIFF"):
        return pcm
    # Estimate safe intake: trust config sample rate for raw PCM chunk sizes.
    header = make_wav_header(len(pcm) // (channels * (bits_per_sample // 8)),
                             sample_rate, channels, bits_per_sample)
    return header + pcm


def is_valid_wav(audio: bytes) -> bool:
    return bool(audio) and audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"


# ---------------------------------------------------------------------------
# MD5 disk cache — re-running demos consumes 0 extra Gnani credits
# ---------------------------------------------------------------------------

def cache_key(text: str, voice: str, model: str, language: str) -> str:
    """MD5 disk-cache key. Includes a schema tag + the LIVE/FALLBACK mode so a
    fallback tone can NEVER be served in place of real Timbre speech (that bug
    made the agent 'beep' and Prisma transcribe silence)."""
    mode = "live" if live_mode() else "fallback"
    return hashlib.md5(
        f"tts-v2|{mode}|{text}|{voice}|{model}|{language}".encode("utf-8")
    ).hexdigest()


def _cache_path(key: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{key}.wav"


def _read_cache(key: str) -> Optional[bytes]:
    p = _cache_path(key)
    if p.exists() and p.stat().st_size > 44:
        try:
            return p.read_bytes()
        except Exception:
            return None
    return None


def _write_cache(key: str, wav: bytes) -> None:
    try:
        _cache_path(key).write_bytes(wav)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# STT — Gnani Prisma v2.5 / v3.0
# ---------------------------------------------------------------------------

async def transcribe_wav(
    wav_bytes: bytes,
    *,
    model: Optional[str] = None,
    language_code: Optional[str] = None,
    audio_format: Optional[str] = None,
    preferred_language: str = "hi-IN",
    keyterms: str = DEFAULT_KEYTERMS,
) -> Dict[str, Any]:
    """POST /stt/v3 — genuine 16kHz mono 16-bit PCM WAV -> Hinglish transcript.

    Returns {"transcript", "request_id", "stt_latency_ms", "model",
             "language_code", "fallback", "score"}. Retries 429/5xx.
    """
    m = model or CONFIG.stt_model
    lang = language_code or CONFIG.stt_language
    fmt = audio_format or CONFIG.stt_format
    started = time.perf_counter()

    if not live_mode():
        # ----- Credit-Shield Fallback (0 credits, zero crash) -----
        return {
            "transcript": _fallback_transcript_hint(wav_bytes),
            "request_id": "fallback-" + hashlib.md5(wav_bytes or b"empty").hexdigest()[:8],
            "stt_latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "model": m + " (fallback)",
            "language_code": lang,
            "fallback": True,
            "score": None,
        }

    url = f"{CONFIG.base_url}/stt/v3"
    files = {"audio_file": ("call_turn.wav", wav_bytes, "audio/wav")}
    data = {
        "model": m,
        "language_code": lang,
        "format": fmt,
        "preferred_language": preferred_language,
        "keyterms": keyterms,
    }
    headers = {"X-API-Key-ID": CONFIG.api_key}

    last_err: str = ""
    for attempt in range(1, CONFIG.max_retries + 1):
        try:
            async with _sem():               # G2: one request at a time
                async with httpx.AsyncClient(timeout=CONFIG.timeout_s) as client:
                    resp = await client.post(url, headers=headers, files=files, data=data)
            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = f"HTTP {resp.status_code}"
                await asyncio.sleep(min(2.0, 0.35 * attempt) * (1 + random.random() * 0.4))
                continue
            resp.raise_for_status()
            payload = _json_or_bow(resp)
            latency = round((time.perf_counter() - started) * 1000, 1)
            CONFIG.telemetry["last_stt_latency_ms"] = latency
            transcript = str(payload.get("transcript") or payload.get("display") or
                             payload.get("text") or payload.get("Transcript") or "")
            return {
                "transcript": transcript.strip(),
                "request_id": str(payload.get("request_id") or payload.get("id") or ""),
                "stt_latency_ms": latency,
                "model": m,
                "language_code": lang,
                "format": fmt,
                "fallback": False,
                "score": payload.get("Confidence") or payload.get("confidence"),
            }
        except Exception as exc:  # network/parse error counts as a retryable failure
            last_err = str(exc)
            await asyncio.sleep(min(2.0, 0.35 * attempt))
    return {
        "transcript": "",
        "request_id": "",
        "stt_latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "model": m,
        "language_code": lang,
        "fallback": True,
        "score": None,
        "error": last_err or "unreachable",
    }


def _fallback_transcript_hint(wav_bytes: bytes) -> str:
    """Deterministic fallback: the frontend sends the driver's typed/scripted
    line; when STT is unavailable the brain proceeds from provided text."""
    return ""


def _json_or_bow(resp: httpx.Response) -> Dict[str, Any]:
    try:
        return resp.json()
    except Exception:
        try:
            import json as _json
            return _json.loads(resp.text)
        except Exception:
            return {}


# ---------------------------------------------------------------------------
# TTS — Gnani Timbre v2.5 / v3.0 (voice "Nalini") + cache
# ---------------------------------------------------------------------------

async def synthesize_speech(
    text: str,
    *,
    voice: Optional[str] = None,
    model: Optional[str] = None,
    language: Optional[str] = None,
    speed: float = 1.0,
    sample_rate: int = 24000,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """POST /api/v1/tts/inference -> {"audio_b64" not used; we return bytes}.

    Returns {"audio": wav_bytes, "cached": bool, "tts_latency_ms", "model",
             "voice", "fallback", "chars", "credit_shield"}.
    """
    started = time.perf_counter()
    v = voice or CONFIG.tts_voice
    mdl = model or CONFIG.tts_model
    lang = language or CONFIG.tts_language
    ck = cache_key(text, v, mdl, lang)

    if use_cache and live_mode():
        hit = _read_cache(ck)
        if hit and is_valid_wav(hit):
            return {"audio": hit, "cached": True,
                    "tts_latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    "model": mdl, "voice": v, "fallback": False,
                    "chars": len(text), "credit_shield": live_mode()}

    if not live_mode():
        wav = fallback_tts_wav(text, sample_rate=sample_rate)
        # NEVER cache fallback tones: a tone must not be served as Timbre speech
        return {"audio": wav, "cached": False,
                "tts_latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "model": mdl + " (fallback voice-tone)",
                "voice": v, "fallback": True, "chars": len(text),
                "credit_shield": False}

    url = f"{CONFIG.base_url}/api/v1/tts/inference"
    body = {
        "text": text,
        "voice": v,
        "model": mdl,
        "language": lang,
        "speed": speed,
        "audio_config": {
            "sample_rate": sample_rate,
            "encoding": "linear_pcm",
            "container": "wav",
        },
    }
    headers = {
        "X-API-Key-ID": CONFIG.api_key,
        "Content-Type": "application/json",
    }
    last_err = ""
    for attempt in range(1, CONFIG.max_retries + 1):
        try:
            async with _sem():
                async with httpx.AsyncClient(timeout=CONFIG.timeout_s) as client:
                    resp = await client.post(url, headers=headers, json=body)
            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = f"HTTP {resp.status_code}"
                await asyncio.sleep(min(2.0, 0.4 * attempt) * (1 + random.random() * 0.5))
                continue
            resp.raise_for_status()
            audio = _extract_audio_bytes(resp)
            latency = round((time.perf_counter() - started) * 1000, 1)
            CONFIG.telemetry["last_tts_latency_ms"] = latency
            wav = wrap_pcm_as_wav(audio, sample_rate=sample_rate)  # RIFF guard
            if is_valid_wav(wav):
                _write_cache(ck, wav)
            return {"audio": wav, "cached": False, "tts_latency_ms": latency,
                    "model": mdl, "voice": v, "fallback": False,
                    "chars": len(text), "credit_shield": True}
        except Exception as exc:
            last_err = str(exc)
            await asyncio.sleep(min(2.0, 0.4 * attempt))
    # graceful degradation (never crash the demo)
    wav = fallback_tts_wav(text, sample_rate=sample_rate)
    return {"audio": wav, "cached": False,
            "tts_latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "model": mdl + " (fallback voice-tone)", "voice": v,
            "fallback": True, "chars": len(text),
            "error": last_err, "credit_shield": False}


def _extract_audio_bytes(resp: httpx.Response) -> bytes:
    """Accept either a binary WAV/PCM body or JSON with base64 audio."""
    ctype = resp.headers.get("content-type", "")
    if "audio" in ctype or "octet-stream" in ctype:
        return resp.content
    payload = _json_or_bow(resp)
    import base64 as _b64
    for k in ("audio", "audio_b64", "audioBase64", "data", "audio_content"):
        raw = payload.get(k)
        if isinstance(raw, str) and raw:
            try:
                return _b64.b64decode(raw, validate=False)
            except Exception:
                continue
    return b""


# ---------------------------------------------------------------------------
# Credit-Shield TTS fallback tone (24kHz 16-bit mono WAV, 0 credits)
# ---------------------------------------------------------------------------

def _fallback_tts_pcm(text: str, sample_rate: int = 24000,
                      base_freq_hz: float = 200.0) -> bytes:
    """Deterministic spoken-tone contour: duration ~ chars*52ms so the demo
    still hears cadence even with no credits. Pitched vowels feel like speech."""
    import math
    dur = max(0.45, min(6.0, len(text) * 0.052))
    n = int(dur * sample_rate)
    out = bytearray()
    for i in range(n):
        f = base_freq_hz + 40.0 * math.sin(2 * math.pi * (i / sample_rate) / 0.28)
        wob = 0.55 + 0.45 * math.sin(2 * math.pi * (i % (sample_rate // 4)) / (sample_rate // 4))
        s = math.sin(2 * math.pi * f * i / sample_rate) * 0.32 * wob
        out += struct.pack("<h", int(s * 32767))
    return bytes(out)


def fallback_tts_wav(text: str, sample_rate: int = 24000) -> bytes:
    pcm = _fallback_tts_pcm(text, sample_rate)
    return wrap_pcm_as_wav(pcm, sample_rate=sample_rate)


# ---------------------------------------------------------------------------
# 1-command live verification (TTS -> STT round-trip), used by UI + CLI
# ---------------------------------------------------------------------------

TEST_PHRASE = "Shukriya BimaVani, radiator damage hai, kripya gaadi start mat kijiye."


async def verify_round_trip(phrase: str = TEST_PHRASE) -> Dict[str, Any]:
    """Synthesize with Timbre v2.5, feed into Prisma v2.5, report metrics."""
    t0 = time.perf_counter()
    tts = await synthesize_speech(phrase, use_cache=True)
    t1 = time.perf_counter()
    stt = await transcribe_wav(tts["audio"])
    t2 = time.perf_counter()
    return {
        "phrase": phrase,
        "tts_model": tts["model"],
        "tts_latency_ms": tts["tts_latency_ms"],
        "tts_cached": tts["cached"],
        "stt_model": stt["model"],
        "stt_latency_ms": stt["stt_latency_ms"],
        "round_trip_ms": round((t2 - t0) * 1000, 1),
        "tts_bytes": len(tts["audio"]) if tts.get("audio") else 0,
        "request_id": stt.get("request_id", ""),
        "live_transcript": stt.get("transcript", ""),
        "fallback": bool(tts.get("fallback") or stt.get("fallback")),
        "sdk_available": _SDK_AVAILABLE,
        "live_mode": live_mode(),
    }


# ---------------------------------------------------------------------------
# (Optional) SDK adapters — exposed for completeness per spec
# ---------------------------------------------------------------------------

def sdk_stt_client() -> Any:
    """gnani.stt.GnaniSTTClient if the gnani-vachana SDK is installed."""
    if _SDK_AVAILABLE and _gnani_stt_sdk is not None:
        try:
            return _gnani_stt_sdk.GnaniSTTClient(api_key=CONFIG.api_key)
        except Exception:
            return None
    return None


def sdk_tts_client() -> Any:
    """gnani.tts.GnaniTTSClient if the gnani-vachana SDK is installed."""
    if _SDK_AVAILABLE and _gnani_tts_sdk is not None:
        try:
            return _gnani_tts_sdk.GnaniTTSClient(api_key=CONFIG.api_key)
        except Exception:
            return None
    return None
