"""
BimaVani — Input & Output Jitter Buffers (20ms Ingest + PLC + 120ms Playout)
=============================================================================
Two jitter buffers mirror real-time streaming telephony:

1. InputJitterBuffer (Mic [1] -> Auto-VAD / ASR [2/3]):
   * Reorders out-of-order 20ms 16kHz mono PCM packets via min-heap
   * Adaptive jitter target (40–120ms) driven by EWMA arrival jitter
   * Packet Loss Concealment (PLC): synthesizes attenuated fade frames
     across 1–3 lost packets instead of leaving holes in STT/VAD audio
   * 300ms pre-roll ring so the first syllable / barge-in is never clipped
   * Spec-accurate 44-byte RIFF/WAVE headers (`len(pcm) // 2` samples)

2. OutputJitterBuffer (Timbre TTS [6] -> Playout [7] -> Speaker [8]):
   * Enforces >= 120ms prebuffer gating (`drain_ready(force=...)`) before
     releasing clauses to Speaker [8], and flushes in <1ms on barge-in.
"""
from __future__ import annotations

import heapq
import math
import struct
import time
from array import array
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

from backend.gnani_client import make_wav_header
from backend.pipeline.endpointing import (
    DEFAULT_SILENCE_DBFS,
    DEFAULT_SPEECH_DBFS,
    DEFAULT_VAD_SILENCE_MS,
    FRAME_MS,
    AutoVADDetector,
    frame_rms_dbfs,
)


def _attenuate_pcm16(frame: bytes, gain: float) -> bytes:
    """Apply smooth PLC decay gain to a 16-bit PCM frame."""
    n = len(frame) // 2
    if n == 0:
        return frame
    samples = array("h")
    samples.frombytes(frame[:n * 2])
    for i in range(n):
        samples[i] = int(samples[i] * gain)
    return samples.tobytes()


class InputJitterBuffer:
    """Orders & paces 20ms 16kHz mono PCM frames with adaptive depth & PLC."""

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_ms: int = FRAME_MS,
        target_jitter_ms: int = 60,
        max_buffer_frames: int = 64,
        pre_roll_frames: int = 15,  # 300ms pre-roll
        vad_silence_ms: int = DEFAULT_VAD_SILENCE_MS,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.target_frames = max(1, target_jitter_ms // max(1, frame_ms))
        self.min_target_frames = min(self.target_frames, 2)
        self.max_target_frames = max(self.target_frames, 6)
        self.max_buffer_frames = max_buffer_frames
        self.pre_roll: Deque[bytes] = deque(maxlen=max(1, pre_roll_frames))
        self.speech_frames: List[bytes] = []
        self._heap: List[Tuple[int, float, bytes]] = []
        self._next_seq: Optional[int] = None
        self._in_speech = False
        self._last_good_frame: Optional[bytes] = None
        self.vad = AutoVADDetector(
            sample_rate=sample_rate,
            vad_silence_ms=vad_silence_ms,
            speech_dbfs=DEFAULT_SPEECH_DBFS,
            silence_dbfs=DEFAULT_SILENCE_DBFS,
        )
        # Telemetry
        self.frames_in = 0
        self.frames_emitted = 0
        self.out_of_order = 0
        self.late_dropped = 0
        self.overflow_dropped = 0
        self.packets_lost = 0
        self.plc_frames = 0
        self.last_dbfs = -120.0
        self._last_arrival_ms: Optional[float] = None
        self.jitter_ms_est = 0.0

    @property
    def adaptive_target_frames(self) -> int:
        if self.jitter_ms_est > self.frame_ms:
            est = int(math.ceil(self.jitter_ms_est / max(1, self.frame_ms))) + 1
            return min(self.max_target_frames, max(self.target_frames, est))
        if self.frames_emitted >= 20 and self.jitter_ms_est < 8.0 and self.out_of_order == 0:
            return max(self.min_target_frames, self.target_frames - 1)
        return self.target_frames

    def configure_vad(self, vad_silence_ms: int) -> None:
        self.vad.silence_ms_needed = max(300, min(1500, int(vad_silence_ms)))

    def push(self, pcm: bytes, seq: Optional[int] = None, ts_ms: Optional[float] = None) -> List[Dict[str, Any]]:
        """Push one 20ms (or multi-frame) PCM chunk; returns drained VAD events."""
        if not pcm:
            return []
        now_ms = time.perf_counter() * 1000.0
        if self._last_arrival_ms is not None:
            delta = abs((now_ms - self._last_arrival_ms) - self.frame_ms)
            self.jitter_ms_est = 0.85 * self.jitter_ms_est + 0.15 * delta
        self._last_arrival_ms = now_ms

        chunk_sz = int(self.sample_rate * self.frame_ms / 1000) * 2
        if len(pcm) > chunk_sz:
            events: List[Dict[str, Any]] = []
            base_seq = seq if seq is not None else self.frames_in
            for idx, off in enumerate(range(0, len(pcm) - (len(pcm) % chunk_sz), chunk_sz)):
                events.extend(self.push(pcm[off:off + chunk_sz], seq=base_seq + idx, ts_ms=ts_ms))
            return events

        if seq is None:
            seq = self.frames_in
        if self._next_seq is None:
            self._next_seq = seq
        elif seq < self._next_seq:
            self.late_dropped += 1
            return []
        elif seq > self._next_seq + len(self._heap):
            self.out_of_order += 1

        if len(self._heap) >= self.max_buffer_frames:
            heapq.heappop(self._heap)
            self.overflow_dropped += 1
            if self._heap:
                self._next_seq = max(self._next_seq or 0, self._heap[0][0])

        heapq.heappush(self._heap, (seq, float(ts_ms or now_ms), pcm))
        self.frames_in += 1
        return self._drain_ready()

    def _emit_plc(self, gap: int, frame_len: int) -> List[Dict[str, Any]]:
        """Synthesize attenuated PLC frames when sequence gaps exceed jitter window."""
        self.packets_lost += gap
        conceal_n = min(gap, 3)
        events: List[Dict[str, Any]] = []
        base = self._last_good_frame or (b"\x00" * frame_len)
        for k in range(1, conceal_n + 1):
            plc_pcm = _attenuate_pcm16(base, 0.65 ** k)
            self.plc_frames += 1
            ev = self._process_frame(plc_pcm, is_plc=True)
            events.append(ev)
        return events

    def _drain_ready(self) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        target = self.adaptive_target_frames
        while self._heap:
            head_seq = self._heap[0][0]
            if self._next_seq is not None and head_seq == self._next_seq:
                _, _, frame = heapq.heappop(self._heap)
                self._last_good_frame = frame
                self._next_seq += 1
                events.append(self._process_frame(frame))
            elif len(self._heap) >= target:
                head_seq, _, frame = heapq.heappop(self._heap)
                if self._next_seq is not None and head_seq > self._next_seq:
                    events.extend(self._emit_plc(head_seq - self._next_seq, len(frame)))
                self._last_good_frame = frame
                self._next_seq = head_seq + 1
                events.append(self._process_frame(frame))
            else:
                break
        return events

    def flush_pending_frames(self) -> List[Dict[str, Any]]:
        """Force-drain any jitter-queued frames immediately (e.g. at turn end)."""
        events: List[Dict[str, Any]] = []
        while self._heap:
            head_seq, _, frame = heapq.heappop(self._heap)
            if self._next_seq is not None and head_seq > self._next_seq:
                events.extend(self._emit_plc(head_seq - self._next_seq, len(frame)))
            self._last_good_frame = frame
            self._next_seq = head_seq + 1
            events.append(self._process_frame(frame))
        return events

    def _process_frame(self, frame: bytes, is_plc: bool = False) -> Dict[str, Any]:
        self.frames_emitted += 1
        self.last_dbfs = round(frame_rms_dbfs(frame), 1)
        vad_ev = self.vad.feed(frame)
        if vad_ev == "speech":
            self._in_speech = True
            self.speech_frames.extend(self.pre_roll)
            self.speech_frames.append(frame)
        elif self._in_speech:
            self.speech_frames.append(frame)
            if vad_ev == "endpoint":
                self._in_speech = False
        else:
            self.pre_roll.append(frame)
        return {
            "vad": vad_ev,
            "in_speech": self._in_speech,
            "dbfs": self.last_dbfs,
            "plc": is_plc,
        }

    def take_utterance_wav(self, include_preroll_if_empty: bool = False) -> bytes:
        """Return spec-compliant 16kHz mono RIFF/WAV (`num_samples = len(pcm)//2`)."""
        self.flush_pending_frames()
        frames = list(self.speech_frames)
        self.speech_frames.clear()
        if not frames and include_preroll_if_empty:
            frames = list(self.pre_roll)
        pcm = b"".join(frames)
        if not pcm:
            return b""
        return make_wav_header(len(pcm) // 2, sample_rate=self.sample_rate) + pcm

    def dump_interruption_snapshot(self) -> Dict[str, Any]:
        """Snapshot pre-roll + detected speech frames with exact RIFF sample count."""
        frames = list(self.speech_frames) if self.speech_frames else list(self.pre_roll)
        pcm = b"".join(frames)
        n_samples = len(pcm) // 2
        duration_ms = round(1000.0 * n_samples / max(1, self.sample_rate), 1)
        wav = (make_wav_header(n_samples, sample_rate=self.sample_rate) + pcm) if pcm else b""
        return {
            "wav": wav,
            "duration_ms": duration_ms,
            "frames": len(frames),
            "peak_dbfs": self.last_dbfs,
            "jitter_ms_est": round(self.jitter_ms_est, 1),
        }

    def clear_speech(self) -> int:
        n = len(self.speech_frames) + len(self._heap)
        self.speech_frames.clear()
        self._heap.clear()
        self._in_speech = False
        self.vad._in_speech = False
        self.vad._speech_ms = 0.0
        self.vad._silence_ms = 0.0
        return n

    def snapshot(self) -> Dict[str, Any]:
        return {
            "frame_ms": self.frame_ms,
            "target_jitter_ms": self.target_frames * self.frame_ms,
            "adaptive_target_ms": self.adaptive_target_frames * self.frame_ms,
            "buffered_frames": len(self._heap),
            "buffered_ms": len(self._heap) * self.frame_ms,
            "pre_roll_frames": len(self.pre_roll),
            "speech_frames_held": len(self.speech_frames),
            "in_speech": self._in_speech,
            "frames_in": self.frames_in,
            "frames_emitted": self.frames_emitted,
            "out_of_order": self.out_of_order,
            "late_dropped": self.late_dropped,
            "overflow_dropped": self.overflow_dropped,
            "packets_lost": self.packets_lost,
            "plc_frames": self.plc_frames,
            "jitter_ms_est": round(self.jitter_ms_est, 1),
            "last_dbfs": self.last_dbfs,
            "vad": self.vad.snapshot(),
        }


@dataclass
class OutputChunk:
    clause_index: int
    audio_bytes: bytes
    duration_ms: float
    pushed_at: float = field(default_factory=time.perf_counter)


class OutputJitterBuffer:
    """Stage 7 output prebuffer: holds >= prebuffer_ms before draining to Speaker."""

    def __init__(self, prebuffer_ms: float = 120.0, max_chunks: int = 16) -> None:
        self.prebuffer_ms = prebuffer_ms
        self.max_chunks = max_chunks
        self._chunks: Deque[OutputChunk] = deque()
        self.buffered_ms = 0.0
        self.chunks_pushed = 0
        self.chunks_played = 0
        self.flushed_chunks = 0
        self.underruns = 0
        self.prebuffer_gate_waits = 0

    @staticmethod
    def estimate_wav_duration_ms(wav_bytes: bytes) -> float:
        if len(wav_bytes) <= 44 or wav_bytes[:4] != b"RIFF":
            return 0.0
        try:
            rate = struct.unpack_from("<I", wav_bytes, 24)[0] or 24000
            channels = struct.unpack_from("<H", wav_bytes, 22)[0] or 1
            bits = struct.unpack_from("<H", wav_bytes, 34)[0] or 16
            data_bytes = max(0, len(wav_bytes) - 44)
            bytes_per_sec = rate * channels * max(1, bits // 8)
            return round(1000.0 * data_bytes / max(1, bytes_per_sec), 1)
        except Exception:
            return 0.0

    def push(self, clause_index: int, wav_bytes: bytes) -> Dict[str, Any]:
        dur_ms = self.estimate_wav_duration_ms(wav_bytes)
        if len(self._chunks) >= self.max_chunks:
            old = self._chunks.popleft()
            self.buffered_ms = max(0.0, self.buffered_ms - old.duration_ms)
        self._chunks.append(OutputChunk(clause_index=clause_index, audio_bytes=wav_bytes, duration_ms=dur_ms))
        self.buffered_ms += dur_ms
        self.chunks_pushed += 1
        gate_open = self.buffered_ms >= self.prebuffer_ms
        if not gate_open:
            self.prebuffer_gate_waits += 1
        return {
            "ready": gate_open or len(self._chunks) >= 1,
            "prebuffer_met": gate_open,
            "buffered_ms": round(self.buffered_ms, 1),
            "prebuffer_ms": self.prebuffer_ms,
        }

    def drain_ready(self, force: bool = False) -> List[OutputChunk]:
        """Release buffered chunks once prebuffer_ms is met (or force-drain at EOF)."""
        if not self._chunks:
            return []
        if not force and self.buffered_ms < self.prebuffer_ms:
            return []
        out: List[OutputChunk] = []
        while self._chunks:
            ch = self._chunks.popleft()
            self.buffered_ms = max(0.0, self.buffered_ms - ch.duration_ms)
            self.chunks_played += 1
            out.append(ch)
        return out

    def flush(self) -> int:
        n = len(self._chunks)
        self._chunks.clear()
        self.buffered_ms = 0.0
        self.flushed_chunks += n
        return n

    def snapshot(self) -> Dict[str, Any]:
        return {
            "prebuffer_ms": self.prebuffer_ms,
            "queued_chunks": len(self._chunks),
            "buffered_ms": round(self.buffered_ms, 1),
            "chunks_pushed": self.chunks_pushed,
            "chunks_played": self.chunks_played,
            "flushed_chunks": self.flushed_chunks,
            "underruns": self.underruns,
            "prebuffer_gate_waits": self.prebuffer_gate_waits,
        }
