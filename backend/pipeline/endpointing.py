"""
BimaVani — G1 Guardrail: Adaptive Endpointing + 80Hz High-Pass DSP Filter
===========================================================================
Real-time 20ms frame endpointing tuned for noisy Indian highways (NH-44):

  * DEFAULT endpointing mode = AUTO-VAD (600ms hysteresis) backed by a
    real-time 80 Hz high-pass diesel-rumble filter + adaptive noise-floor
    estimator so steady background noise does not wedge the gate open and
    short <220ms horn blasts cannot falsely trigger speech or endpoints.
  * OPTIONAL Push-to-Talk mode: caller explicitly controls turn end on mic
    button release (available via UI toggle and API `endpoint_mode`).
"""
from __future__ import annotations

import math
import time
from array import array
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Tuple

FRAME_MS = 20
DEFAULT_SPEECH_DBFS = -34.0     # base speech RMS gate
DEFAULT_SILENCE_DBFS = -44.0    # base silence gate
DEFAULT_VAD_SILENCE_MS = 600
MIN_SPEECH_MS = 220             # must hear sustained speech before endpointing
DEFAULT_HIGHPASS_HZ = 80.0      # removes sub-80Hz truck engine rumble & DC drift


@dataclass
class EndpointingState:
    mode: str = "auto_vad"                      # auto_vad | push_to_talk
    vad_silence_ms: int = DEFAULT_VAD_SILENCE_MS
    speech_dbfs: float = DEFAULT_SPEECH_DBFS
    silence_dbfs: float = DEFAULT_SILENCE_DBFS
    _in_speech: bool = False
    _speech_ms: int = 0
    _silence_ms: int = 0
    _frames: int = 0
    telemetry: Dict[str, Any] = field(default_factory=dict)

    def configure(self, mode: str | None = None, vad_silence_ms: int | None = None) -> None:
        if mode in ("push_to_talk", "auto_vad"):
            self.mode = mode
            self._reset()
        if vad_silence_ms:
            self.vad_silence_ms = max(300, min(1200, int(vad_silence_ms)))

    def _reset(self) -> None:
        self._in_speech = False
        self._speech_ms = self._silence_ms = self._frames = 0

    @property
    def g1_guardrail_note(self) -> str:
        return (
            "Auto-VAD (600ms hysteresis + adaptive noise floor + 80Hz rumble HPF) default; "
            "Push-to-Talk fallback toggle available (G1)."
        )


def frame_rms_dbfs(frame: bytes) -> float:
    """RMS of a little-endian 16-bit PCM frame, expressed in dBFS."""
    n = len(frame) // 2
    if n == 0:
        return -120.0
    samples = array("h")
    samples.frombytes(frame[:n * 2])
    acc = sum(s * s for s in samples)
    rms = math.sqrt(acc / n) / 32768.0
    if rms <= 0:
        return -120.0
    return 20.0 * math.log10(rms)


def is_speech_frame(frame: bytes, threshold_dbfs: float = DEFAULT_SPEECH_DBFS) -> bool:
    return frame_rms_dbfs(frame) >= threshold_dbfs


def highpass_denoise_pcm16(
    pcm: bytes,
    sample_rate: int = 16000,
    cutoff_hz: float = DEFAULT_HIGHPASS_HZ,
) -> bytes:
    """1st-order RC high-pass IIR filter stripping DC offset & <80Hz engine rumble.

    Preserves 300–3400 Hz telephony voice formants cleanly with zero external deps.
    """
    n = len(pcm) // 2
    if n < 2 or cutoff_hz <= 0 or sample_rate <= 0:
        return pcm
    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    dt = 1.0 / float(sample_rate)
    alpha = rc / (rc + dt)

    inp = array("h")
    inp.frombytes(pcm[:n * 2])
    out = array("h", [0] * n)

    y_prev = 0.0
    x_prev = float(inp[0])
    for i in range(1, n):
        x = float(inp[i])
        y = alpha * (y_prev + x - x_prev)
        x_prev = x
        y_prev = y
        if y > 32767.0:
            out[i] = 32767
        elif y < -32768.0:
            out[i] = -32768
        else:
            out[i] = int(y)
    return out.tobytes() + pcm[n * 2:]


def denoise_wav_for_stt(
    wav_bytes: bytes,
    cutoff_hz: float = DEFAULT_HIGHPASS_HZ,
) -> Tuple[bytes, Dict[str, Any]]:
    """Apply 80Hz high-pass diesel-rumble filter to a 16-bit RIFF PCM WAV."""
    if len(wav_bytes) <= 44 or wav_bytes[:4] != b"RIFF":
        return wav_bytes, {"dsp_applied": False, "dsp_filter": "bypass"}
    import struct
    try:
        audio_fmt = struct.unpack_from("<H", wav_bytes, 20)[0]
        channels = struct.unpack_from("<H", wav_bytes, 22)[0]
        sample_rate = struct.unpack_from("<I", wav_bytes, 24)[0] or 16000
        bits = struct.unpack_from("<H", wav_bytes, 34)[0]
    except Exception:
        return wav_bytes, {"dsp_applied": False, "dsp_filter": "bypass"}
    if audio_fmt != 1 or channels != 1 or bits != 16:
        return wav_bytes, {"dsp_applied": False, "dsp_filter": "bypass_non_pcm16"}

    pcm = wav_bytes[44:]
    pre_db = frame_rms_dbfs(pcm)
    filtered = highpass_denoise_pcm16(pcm, sample_rate=sample_rate, cutoff_hz=cutoff_hz)
    post_db = frame_rms_dbfs(filtered)
    return wav_bytes[:44] + filtered, {
        "dsp_applied": True,
        "dsp_filter": f"highpass_{int(cutoff_hz)}hz",
        "pre_rms_dbfs": round(pre_db, 1),
        "post_rms_dbfs": round(post_db, 1),
        "attenuation_db": round(max(0.0, pre_db - post_db), 1),
    }


class AutoVADDetector:
    """20ms-frame hysteresis VAD with adaptive noise-floor & SNR estimation.

    feed(frame_bytes) -> "none" | "speech" | "endpoint"
      * Adapts `noise_floor_dbfs` downward rapidly during quiet pauses and
        upward gently on sustained stationary rumble, preventing fixed-threshold
        lockups while rejecting transient <220ms horn blasts.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        vad_silence_ms: int = DEFAULT_VAD_SILENCE_MS,
        speech_dbfs: float = DEFAULT_SPEECH_DBFS,
        silence_dbfs: float = DEFAULT_SILENCE_DBFS,
        min_speech_ms: int = MIN_SPEECH_MS,
        initial_noise_floor_dbfs: float = -58.0,
    ):
        self.sample_rate = sample_rate
        self.silence_ms_needed = vad_silence_ms
        self.speech_dbfs = speech_dbfs
        self.silence_dbfs = silence_dbfs
        self.min_speech_ms = min_speech_ms
        self.noise_floor_dbfs = initial_noise_floor_dbfs
        self._recent_rms: Deque[float] = deque(maxlen=25)
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._in_speech = False
        self._peak_dbfs = -120.0
        self._last_dbfs = -120.0

    def _frame_ms(self, frame: bytes) -> float:
        samples = len(frame) // 2
        return 1000.0 * samples / max(1, self.sample_rate)

    @property
    def effective_speech_dbfs(self) -> float:
        return min(-16.0, max(self.speech_dbfs, self.noise_floor_dbfs + 8.0))

    @property
    def effective_silence_dbfs(self) -> float:
        eff_sp = self.effective_speech_dbfs
        return max(self.silence_dbfs, min(eff_sp - 4.0, self.noise_floor_dbfs + 3.5))

    @property
    def estimated_snr_db(self) -> float:
        if self._peak_dbfs <= -110.0:
            return 0.0
        return round(max(0.0, self._peak_dbfs - self.noise_floor_dbfs), 1)

    def _update_noise_floor(self, db: float, ms: float) -> None:
        if db <= -115.0:
            return
        self._recent_rms.append(db)
        if db < self.noise_floor_dbfs:
            self.noise_floor_dbfs = 0.75 * self.noise_floor_dbfs + 0.25 * db
            return
        # Minimum-statistics stationary rumble detector (< -22 dBFS flat signal for >=160ms):
        # Tracks true ambient drone (even if already above base threshold) while ignoring
        # transient horn blasts (wide win_span) and real louder speech (>= -22 dBFS).
        if not self._in_speech and len(self._recent_rms) >= 8 and db < -22.0:
            recent = list(self._recent_rms)[-8:]
            win_min = min(recent)
            win_span = max(recent) - win_min
            if win_span <= 6.0 and win_min > self.noise_floor_dbfs:
                self.noise_floor_dbfs = 0.65 * self.noise_floor_dbfs + 0.35 * win_min
                return
        if not self._in_speech and db < self.effective_speech_dbfs:
            self.noise_floor_dbfs = 0.96 * self.noise_floor_dbfs + 0.04 * db

    def feed(self, frame: bytes) -> str:
        ms = self._frame_ms(frame)
        db = frame_rms_dbfs(frame)
        self._last_dbfs = db
        if db > self._peak_dbfs:
            self._peak_dbfs = db

        self._update_noise_floor(db, ms)
        eff_speech = self.effective_speech_dbfs
        eff_silence = self.effective_silence_dbfs

        if db >= eff_speech:
            self._speech_ms += ms
            self._silence_ms = 0.0
            if not self._in_speech and self._speech_ms >= self.min_speech_ms:
                self._in_speech = True
                return "speech"
            return "none"
        if not self._in_speech:
            # Decay transient burst energy so brief horn spikes don't accumulate
            self._speech_ms = max(0.0, self._speech_ms - ms)
            return "none"
        if db < eff_silence:
            self._silence_ms += ms
            if self._silence_ms >= self.silence_ms_needed:
                self._in_speech = False
                self._speech_ms = self._silence_ms = 0.0
                return "endpoint"
        else:
            # Mid-band (-44..-34 dBFS or near-floor tail): speech tail, not clean silence
            self._silence_ms = 0.0
        return "none"

    def snapshot(self) -> Dict[str, Any]:
        return {
            "noise_floor_dbfs": round(self.noise_floor_dbfs, 1),
            "effective_speech_dbfs": round(self.effective_speech_dbfs, 1),
            "effective_silence_dbfs": round(self.effective_silence_dbfs, 1),
            "estimated_snr_db": self.estimated_snr_db,
            "in_speech": self._in_speech,
        }


def analyze_wav_endpoint(wav_bytes: bytes, state: EndpointingState) -> Dict[str, Any]:
    """Run 20ms frame analysis + adaptive noise-floor telemetry over WAV bytes."""
    t0 = time.perf_counter()
    pcm = wav_bytes[44:] if len(wav_bytes) > 44 and wav_bytes[:4] == b"RIFF" else b""
    frames = chunk_frames(pcm, sample_rate=16000, frame_ms=FRAME_MS) if pcm else []
    vad = AutoVADDetector(
        sample_rate=16000,
        vad_silence_ms=state.vad_silence_ms,
        speech_dbfs=state.speech_dbfs,
        silence_dbfs=state.silence_dbfs,
    )
    speech_frames = 0
    endpoint_detected = False
    peak_db = -120.0
    for f in frames:
        db = frame_rms_dbfs(f)
        if db > peak_db:
            peak_db = db
        if is_speech_frame(f, vad.effective_speech_dbfs):
            speech_frames += 1
        if vad.feed(f) == "endpoint":
            endpoint_detected = True
    tel = endpoint_turn(state)
    tel.update({
        "frames": len(frames),
        "speech_frames": speech_frames,
        "peak_dbfs": round(peak_db, 1),
        "noise_floor_dbfs": round(vad.noise_floor_dbfs, 1),
        "effective_speech_dbfs": round(vad.effective_speech_dbfs, 1),
        "estimated_snr_db": vad.estimated_snr_db,
        "vad_endpoint_detected": endpoint_detected,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    })
    return tel


def endpoint_turn(state: EndpointingState) -> Dict[str, Any]:
    """Terminal decision for a recorded turn."""
    state._reset()
    telemetry = {
        "mode": state.mode,
        "endpoint_reason": "push_to_talk_release" if state.mode == "push_to_talk"
        else f"auto_vad_{state.vad_silence_ms}ms_silence",
        "vad_silence_ms": state.vad_silence_ms if state.mode == "auto_vad" else None,
        "guardrail": "G1",
    }
    state.telemetry = telemetry
    return telemetry


def chunk_frames(pcm: bytes, sample_rate: int = 16000,
                 frame_ms: int = FRAME_MS) -> List[bytes]:
    """Split mono 16-bit PCM into fixed FRAME_MS frames."""
    bytes_per_frame = int(sample_rate * frame_ms / 1000) * 2
    return [pcm[i:i + bytes_per_frame] for i in range(0, len(pcm) - len(pcm) % bytes_per_frame, bytes_per_frame)]
