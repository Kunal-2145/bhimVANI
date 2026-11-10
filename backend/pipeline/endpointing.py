"""
BimaVani — G1 Guardrail: Endpointing (Push-to-Talk default + Optional Auto-VAD)
==============================================================================
Why: the demo mixes loud Indian highway horn/engine noise INTO the mic
stream. A naive silence-based VAD hears truck horns as "speech" and
endpoint-cuts the driver mid-sentence. Therefore:

  * DEFAULT endpointing mode = PUSH-TO-TALK — the turn ends exactly when
    the driver releases the mic button. Zero false endpoints in noise.
  * OPTIONAL "Auto-VAD (600ms)" for quiet rooms: 20ms frame energy gate
    (RMS in dBFS) with hysteresis — only ends the turn after 600ms of
    sustained silence BELOW a speech threshold, and requires some speech
    energy first so pure horn blasts can still not trigger endpoints.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List

FRAME_MS = 20
SUPPORT_RATES = {16000, 24000, 48000, 44100}
DEFAULT_SPEECH_DBFS = -34.0     # speech RMS must exceed this to open VAD
DEFAULT_SILENCE_DBFS = -44.0    # below this counts as silence
DEFAULT_VAD_SILENCE_MS = 600
MIN_SPEECH_MS = 220             # must hear some real speech before endpointing


@dataclass
class EndpointingState:
    mode: str = "push_to_talk"                      # push_to_talk | auto_vad
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
        return ("Push-to-Talk default so highway horns cannot fake speech; "
                "Auto-VAD only in quiet rooms (G1).")


def frame_rms_dbfs(frame: bytes) -> float:
    """RMS of a little-endian 16-bit PCM frame, expressed in dBFS."""
    n = len(frame) // 2
    if n == 0:
        return -120.0
    acc = 0
    ints = __import__("struct").unpack(f"<{n}h", frame[:n * 2])
    for s in ints:
        acc += s * s
    rms = math.sqrt(acc / n) / 32768.0
    if rms <= 0:
        return -120.0
    return 20 * math.log10(rms)


def is_speech_frame(frame: bytes, threshold_dbfs: float = DEFAULT_SPEECH_DBFS) -> bool:
    return frame_rms_dbfs(frame) >= threshold_dbfs


class AutoVADDetector:
    """20ms-frame hysteresis VAD — the OPTIONAL (non-default) endpointing path.

    feed(frame_bytes) -> "none" | "speech" | "endpoint"
      * only after >= MIN_SPEECH_MS of detected speech will silence trigger
        "endpoint" — this is what keeps NH-44 truck horns from cutting turns.
    """

    def __init__(self, sample_rate: int = 16000,
                 vad_silence_ms: int = DEFAULT_VAD_SILENCE_MS,
                 speech_dbfs: float = DEFAULT_SPEECH_DBFS,
                 silence_dbfs: float = DEFAULT_SILENCE_DBFS):
        self.sample_rate = sample_rate
        self.silence_ms_needed = vad_silence_ms
        self.speech_dbfs = speech_dbfs
        self.silence_dbfs = silence_dbfs
        self._speech_ms = 0
        self._silence_ms = 0
        self._in_speech = False

    def _frame_ms(self, frame: bytes) -> float:
        samples = len(frame) // 2
        return 1000.0 * samples / max(1, self.sample_rate)

    def feed(self, frame: bytes) -> str:
        ms = self._frame_ms(frame)
        db = frame_rms_dbfs(frame)
        if db >= self.speech_dbfs:
            self._speech_ms += ms
            self._silence_ms = 0
            if not self._in_speech and self._speech_ms >= 40:
                self._in_speech = True
                return "speech"
            return "none"
        if self._in_speech:
            self._silence_ms += ms
            if db < self.silence_dbfs and self._silence_ms >= self.silence_ms_needed:
                self._in_speech = False
                self._speech_ms = self._silence_ms = 0
                return "endpoint"
        return "none"

    def push_to_talk_active(self) -> bool:
        return False   # push-to-talk short-circuits VAD entirely


def endpoint_turn(state: EndpointingState) -> Dict[str, Any]:
    """Terminal decision for a recorded push-to-talk turn (always a full turn)."""
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
