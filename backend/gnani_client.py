"""
BimaVani — Gnani.ai Vachana/Artha Ecosystem Client (backend/gnani_client.py)
===========================================================================
Wraps the LIVE Gnani.ai APIs with production guardrails:

  * STT  (Prisma):   POST https://api.vachana.ai/stt/v3
  * TTS  (Timbre):   POST https://api.vachana.ai/api/v1/tts/inference
  * Header X-API-Key-ID: <GNANI_API_KEY> on every call.
  * G2 GUARDRAIL: a per-event-loop asyncio.Semaphore(1) around ALL live calls —
    student keys allow strictly 1 request at a time. Auto-retries on
    HTTP 429 / 5xx with exponential backoff + jitter; fails fast on 401/403/4xx.
  * Credit-Shield Fallback Mode: with no GNANI_API_KEY the app NEVER
    crashes — STT returns a deterministic pass-through and TTS synthesizes
    a locally generated 24kHz spoken-tone WAV (0 credits consumed).
  * 44-byte RIFF/WAVE header wrapper: if Timbre returns raw PCM (no "RIFF"
    magic), it is framed as 16-bit mono 24kHz WAV so browser
    AudioContext.decodeAudioData never fails.
  * MD5 disk cache (backend/cache/tts/): every synthesized clause is cached
    as md5(tts-v2|live|text|voice|model|lang).wav so demo re-runs cost 0 credits.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import random
import struct
import time
import weakref
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from backend.i18n import ALL_GNANI_LANGUAGES, normalize_lang_code, tts_voice_for_lang
from backend.translit import detect_script_language

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

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
STT_FORMATS = {"verbatim", "transcribe", "codemix", "transliterate"}
STT_LANGS = set(ALL_GNANI_LANGUAGES) | {"indic-auto"}
TTS_LANGS = set(ALL_GNANI_LANGUAGES) | {"auto"}
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

_LOOP_SEMS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()
_LOOP_CLIENTS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[int, httpx.AsyncClient]]" = weakref.WeakKeyDictionary()
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
    tts_lang = os.environ.get("GNANI_TTS_LANGUAGE", DEFAULT_LANGUAGE)
    return GnaniConfig(
        api_key=key,
        base_url=os.environ.get("GNANI_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        stt_model=stt_model if stt_model in ALLOWED_STT_MODELS else DEFAULT_STT_MODEL,
        stt_language=stt_lang if stt_lang in STT_LANGS else DEFAULT_LANGUAGE,
        stt_format=stt_format if stt_format in STT_FORMATS else "codemix",
        tts_model=tts_model if tts_model in ALLOWED_TTS_MODELS else DEFAULT_TTS_MODEL,
        tts_voice=os.environ.get("GNANI_TTS_VOICE", DEFAULT_VOICE),
        tts_language=tts_lang if tts_lang in TTS_LANGS else DEFAULT_LANGUAGE,
    )


CONFIG: GnaniConfig = _load_config()


def reload_config() -> GnaniConfig:
    global CONFIG
    CONFIG = _load_config()
    return CONFIG


def _sem() -> asyncio.Semaphore:
    """G2 guardrail: ONE semaphore(1) per running event loop -> student API = 1 live request."""
    loop = asyncio.get_running_loop()
    sem = _LOOP_SEMS.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(1)
        _LOOP_SEMS[loop] = sem
    return sem


async def _get_http_client() -> tuple[httpx.AsyncClient, bool]:
    """Return a pooled AsyncClient per running loop (or transient if AsyncClient is mocked)."""
    cls_id = id(httpx.AsyncClient)
    # If httpx.AsyncClient was monkeypatched in tests, construct directly as context-managed
    if cls_id != id(getattr(httpx, "_orig_async_client", httpx.AsyncClient)):
        return httpx.AsyncClient(timeout=CONFIG.timeout_s), True
    loop = asyncio.get_running_loop()
    entry = _LOOP_CLIENTS.get(loop)
    if entry is not None and entry[0] == cls_id and not getattr(entry[1], "is_closed", False):
        return entry[1], False
    client = httpx.AsyncClient(timeout=CONFIG.timeout_s)
    _LOOP_CLIENTS[loop] = (cls_id, client)
    return client, False


if not hasattr(httpx, "_orig_async_client"):
    setattr(httpx, "_orig_async_client", httpx.AsyncClient)


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
    if not pcm:
        return b""
    if len(pcm) >= 12 and pcm[:4] == b"RIFF" and pcm[8:12] == b"WAVE":
        return pcm
    bytes_per_sample = channels * (bits_per_sample // 8)
    header = make_wav_header(len(pcm) // max(1, bytes_per_sample),
                             sample_rate, channels, bits_per_sample)
    return header + pcm


def is_valid_wav(audio: bytes) -> bool:
    return bool(audio) and len(audio) > 44 and audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"


def resample_wav_to_16k(wav_bytes: bytes) -> bytes:
    """Downsample mono 16-bit PCM WAV (e.g. 24kHz Timbre) to 16kHz for Prisma STT."""
    if not is_valid_wav(wav_bytes):
        return wav_bytes
    try:
        src_rate = struct.unpack_from("<I", wav_bytes, 24)[0]
        channels = struct.unpack_from("<H", wav_bytes, 22)[0]
        bits = struct.unpack_from("<H", wav_bytes, 34)[0]
        if src_rate == 16000 and channels == 1 and bits == 16:
            return wav_bytes
        if channels != 1 or bits != 16 or src_rate <= 0:
            return wav_bytes
        pcm = wav_bytes[44:]
        n_in = len(pcm) // 2
        if n_in < 2:
            return wav_bytes
        samples = struct.unpack(f"<{n_in}h", pcm[:n_in * 2])
        ratio = src_rate / 16000.0
        n_out = max(1, int(n_in / ratio))
        out_samples = []
        for i in range(n_out):
            pos = i * ratio
            i0 = min(n_in - 1, int(pos))
            i1 = min(n_in - 1, i0 + 1)
            frac = pos - i0
            val = int(samples[i0] * (1.0 - frac) + samples[i1] * frac)
            out_samples.append(max(-32768, min(32767, val)))
        out_pcm = struct.pack(f"<{len(out_samples)}h", *out_samples)
        return make_wav_header(len(out_samples), sample_rate=16000) + out_pcm
    except Exception:
        return wav_bytes


# ---------------------------------------------------------------------------
# MD5 disk cache — re-running demos consumes 0 extra Gnani credits
# ---------------------------------------------------------------------------

def cache_key(text: str, voice: str, model: str, language: str) -> str:
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
    preferred_language: Optional[str] = None,
    language_candidates: Optional[str] = None,
    keyterms: str = DEFAULT_KEYTERMS,
) -> Dict[str, Any]:
    """POST /stt/v3 — 16kHz mono 16-bit PCM WAV -> transcript across all 10 Gnani languages + indic-auto."""
    raw_lang = language_code or CONFIG.stt_language
    if raw_lang in STT_LANGS:
        lang = raw_lang
    else:
        lang = normalize_lang_code(raw_lang) or ("indic-auto" if "auto" in str(raw_lang).lower() else DEFAULT_LANGUAGE)
    m = model or CONFIG.stt_model
    fmt = audio_format or CONFIG.stt_format
    # Gnani prisma-v3.0 is required for indic-auto, codemix/transliterate, and all 8 regional languages
    if lang == "indic-auto" or lang not in ("hi-IN", "en-IN") or fmt in ("codemix", "transliterate"):
        m = "prisma-v3.0"
    if m == "prisma-v3.0" and audio_format is None and lang != "hi-IN":
        fmt = "transcribe"
    started = time.perf_counter()

    if not live_mode():
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
    data: Dict[str, Any] = {
        "model": m,
        "language_code": lang,
        "format": fmt,
    }
    if lang == "indic-auto" and language_candidates:
        cands_raw = [c.strip() for c in str(language_candidates).split(",") if c.strip()]
        cands: list[str] = []
        for c in cands_raw:
            nc = normalize_lang_code(c) or (c if c in ALL_GNANI_LANGUAGES else None)
            if nc and nc not in cands:
                cands.append(nc)
        if 2 <= len(cands) <= 4:
            data["multi_lang_codes"] = ",".join(cands)
    if keyterms:
        if m == "prisma-v3.0":
            kw_list = [w.strip() for w in keyterms.split(",") if w.strip()][:200]
            if kw_list:
                data["keywords"] = json.dumps(kw_list, ensure_ascii=False)
        else:
            data["keyterms"] = keyterms
    _ = preferred_language  # kept in signature for backwards compatibility
    headers = {"X-API-Key-ID": CONFIG.api_key}

    last_err: str = ""
    for attempt in range(1, CONFIG.max_retries + 1):
        files = {"audio_file": ("call_turn.wav", wav_bytes, "audio/wav")}
        try:
            client, transient = await _get_http_client()
            try:
                async with _sem():
                    resp = await client.post(url, headers=headers, files=files, data=data)
            finally:
                if transient:
                    await client.aclose()

            if resp.status_code in RETRYABLE_STATUS:
                last_err = f"HTTP {resp.status_code}"
                await asyncio.sleep(min(2.0, 0.35 * attempt) * (1 + random.random() * 0.4))
                continue
            if resp.status_code >= 400:
                last_err = f"HTTP {resp.status_code}"
                break

            payload = _json_or_bow(resp)
            latency = round((time.perf_counter() - started) * 1000, 1)
            CONFIG.telemetry["last_stt_latency_ms"] = latency
            nested = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            transcript = str(
                payload.get("transcript") or payload.get("display")
                or payload.get("text") or payload.get("Transcript")
                or nested.get("transcript") or nested.get("text") or ""
            ).strip()
            detected_raw = (
                payload.get("detected_language_code")
                or payload.get("language")
                or payload.get("detected_language")
                or nested.get("detected_language_code")
                or nested.get("language")
                or nested.get("detected_language")
            )
            resolved_lang = (
                normalize_lang_code(str(detected_raw or ""))
                or detect_script_language(transcript)
                or (lang if lang != "indic-auto" else None)
                or DEFAULT_LANGUAGE
            )
            return {
                "transcript": transcript,
                "request_id": str(payload.get("request_id") or payload.get("id") or nested.get("request_id") or ""),
                "stt_latency_ms": latency,
                "model": m,
                "language_code": lang,
                "detected_language_code": detected_raw,
                "language": str(resolved_lang),
                "format": fmt,
                "fallback": False,
                "score": payload.get("Confidence") or payload.get("confidence"),
            }
        except Exception as exc:
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
# TTS — Gnani Timbre v2.5 / v3.0 (native voice per language) + cache
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
    """POST /api/v1/tts/inference -> RIFF/WAV bytes + latency telemetry."""
    started = time.perf_counter()
    mdl = model or CONFIG.tts_model
    lang = "auto" if language == "auto" else (normalize_lang_code(language) or CONFIG.tts_language)
    v = voice or (tts_voice_for_lang(lang, mdl) if CONFIG.tts_voice == DEFAULT_VOICE else CONFIG.tts_voice)
    ck = cache_key(text, v, mdl, lang)

    if use_cache and live_mode():
        hit = _read_cache(ck)
        if hit and is_valid_wav(hit):
            return {"audio": hit, "cached": True,
                    "tts_latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    "model": mdl, "voice": v, "language": lang, "fallback": False,
                    "chars": len(text), "credit_shield": True}

    if not live_mode():
        wav = fallback_tts_wav(text, sample_rate=sample_rate)
        return {"audio": wav, "cached": False,
                "tts_latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "model": mdl + " (fallback voice-tone)",
                "voice": v, "language": lang, "fallback": True, "chars": len(text),
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
            client, transient = await _get_http_client()
            try:
                async with _sem():
                    resp = await client.post(url, headers=headers, json=body)
            finally:
                if transient:
                    await client.aclose()

            if resp.status_code in RETRYABLE_STATUS:
                last_err = f"HTTP {resp.status_code}"
                await asyncio.sleep(min(2.0, 0.4 * attempt) * (1 + random.random() * 0.5))
                continue
            if resp.status_code >= 400:
                last_err = f"HTTP {resp.status_code}"
                break

            audio = _extract_audio_bytes(resp)
            latency = round((time.perf_counter() - started) * 1000, 1)
            CONFIG.telemetry["last_tts_latency_ms"] = latency
            wav = wrap_pcm_as_wav(audio, sample_rate=sample_rate)
            if is_valid_wav(wav):
                _write_cache(ck, wav)
                return {"audio": wav, "cached": False, "tts_latency_ms": latency,
                        "model": mdl, "voice": v, "language": lang, "fallback": False,
                        "chars": len(text), "credit_shield": True}
            last_err = "empty_audio_payload"
            break
        except Exception as exc:
            last_err = str(exc)
            await asyncio.sleep(min(2.0, 0.4 * attempt))

    wav = fallback_tts_wav(text, sample_rate=sample_rate)
    return {"audio": wav, "cached": False,
            "tts_latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "model": mdl + " (fallback voice-tone)", "voice": v, "language": lang,
            "fallback": True, "chars": len(text),
            "error": last_err, "credit_shield": False}


def _extract_audio_bytes(resp: httpx.Response) -> bytes:
    """Accept either a binary WAV/PCM body or JSON with base64 audio."""
    ctype = resp.headers.get("content-type", "")
    if "audio" in ctype or "octet-stream" in ctype:
        return resp.content
    payload = _json_or_bow(resp)
    for k in ("audio", "audio_b64", "audioBase64", "data", "audio_content"):
        raw = payload.get(k)
        if isinstance(raw, str) and raw:
            if raw.startswith("data:") and "," in raw:
                raw = raw.split(",", 1)[1]
            try:
                return base64.b64decode(raw, validate=False)
            except Exception:
                continue
    return b""


# ---------------------------------------------------------------------------
# Credit-Shield TTS fallback tone (24kHz 16-bit mono WAV, 0 credits)
# ---------------------------------------------------------------------------

def _fallback_tts_pcm(text: str, sample_rate: int = 24000,
                      base_freq_hz: float = 200.0) -> bytes:
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
# 1-command live verification (TTS -> 16kHz resample -> STT round-trip)
# ---------------------------------------------------------------------------

TEST_PHRASE = "Shukriya BimaVani, radiator damage hai, kripya gaadi start mat kijiye."


async def verify_round_trip(phrase: str = TEST_PHRASE) -> Dict[str, Any]:
    """Synthesize with Timbre, resample to 16kHz, feed into Prisma, report metrics."""
    t0 = time.perf_counter()
    tts = await synthesize_speech(phrase, use_cache=True)
    stt_wav = resample_wav_to_16k(tts["audio"]) if tts.get("audio") else b""
    stt = await transcribe_wav(stt_wav)
    t2 = time.perf_counter()
    live_tx = (stt.get("transcript") or "").strip()
    ratio = round(SequenceMatcher(None, phrase.lower(), live_tx.lower()).ratio(), 3) if live_tx else 0.0
    fallback_used = bool(tts.get("fallback") or stt.get("fallback"))
    verified = (not fallback_used) and bool(live_tx)
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
        "live_transcript": live_tx,
        "match_ratio": ratio,
        "verified": verified,
        "fallback": fallback_used,
        "error": stt.get("error") or tts.get("error"),
        "sdk_available": _SDK_AVAILABLE,
        "live_mode": live_mode(),
    }


# ---------------------------------------------------------------------------
# (Optional) SDK adapters
# ---------------------------------------------------------------------------

def sdk_stt_client() -> Any:
    if _SDK_AVAILABLE and _gnani_stt_sdk is not None:
        try:
            return _gnani_stt_sdk.GnaniSTTClient(api_key=CONFIG.api_key)
        except Exception:
            return None
    return None


def sdk_tts_client() -> Any:
    if _SDK_AVAILABLE and _gnani_tts_sdk is not None:
        try:
            return _gnani_tts_sdk.GnaniTTSClient(api_key=CONFIG.api_key)
        except Exception:
            return None
    return None
