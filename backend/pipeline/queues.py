"""
BimaVani — G3 Guardrail: Bounded Queues, Session Isolation & Rolling SLOs
===========================================================================
Every hop of the 8-stage pipeline passes through a bounded asyncio.Queue
(maxsize=32). Queue depth, drop/backpressure stats, and rolling p50/p95
latency SLOs (TTFA, STT, Brain, TTS, Barge-In Flush, TTS cache efficiency)
stream live to `/api/health`, `/api/pipeline-state`, and the call UI.
"""
from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

MAX_QUEUE_SIZE = 32                      # bounded backpressure window


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(math.ceil(pct * len(ordered))) - 1))
    return round(ordered[idx], 1)


class LatencyTracker:
    """Bounded rolling ring (maxlen=200) tracking p50/p95 SLOs & counters."""

    METRICS = (
        "ttfa_ms",
        "stt_ms",
        "brain_ms",
        "tts_ms",
        "barge_in_flush_ms",
        "total_turn_ms",
    )

    def __init__(self, maxlen: int = 200) -> None:
        self.maxlen = maxlen
        self._samples: Dict[str, Deque[float]] = {
            k: deque(maxlen=maxlen) for k in self.METRICS
        }
        self.turns_total = 0
        self.tts_cache_hits = 0
        self.tts_cache_misses = 0
        self.fallback_turns = 0
        self.barge_ins_total = 0
        self.pii_redactions = 0

    def record(self, metric: str, ms: float) -> None:
        if metric in self._samples and ms is not None:
            self._samples[metric].append(max(0.0, float(ms)))

    def record_turn(
        self,
        *,
        ttfa_ms: Optional[float] = None,
        stt_ms: Optional[float] = None,
        brain_ms: Optional[float] = None,
        tts_ms: Optional[float] = None,
        total_turn_ms: Optional[float] = None,
        cache_hits: int = 0,
        cache_misses: int = 0,
        fallback: bool = False,
        pii_redactions: int = 0,
    ) -> None:
        self.turns_total += 1
        if ttfa_ms is not None:
            self.record("ttfa_ms", ttfa_ms)
        if stt_ms is not None:
            self.record("stt_ms", stt_ms)
        if brain_ms is not None:
            self.record("brain_ms", brain_ms)
        if tts_ms is not None:
            self.record("tts_ms", tts_ms)
        if total_turn_ms is not None:
            self.record("total_turn_ms", total_turn_ms)
        self.tts_cache_hits += max(0, int(cache_hits))
        self.tts_cache_misses += max(0, int(cache_misses))
        if fallback:
            self.fallback_turns += 1
        self.pii_redactions += max(0, int(pii_redactions))

    def record_barge_in(self, flush_ms: float) -> None:
        self.barge_ins_total += 1
        self.record("barge_in_flush_ms", flush_ms)

    def snapshot(self) -> Dict[str, Any]:
        percentiles: Dict[str, Dict[str, Any]] = {}
        for k, dq in self._samples.items():
            vals = list(dq)
            percentiles[k] = {
                "count": len(vals),
                "p50": _percentile(vals, 0.50),
                "p95": _percentile(vals, 0.95),
                "last": round(vals[-1], 1) if vals else 0.0,
            }
        total_tts = self.tts_cache_hits + self.tts_cache_misses
        return {
            "turns_total": self.turns_total,
            "barge_ins_total": self.barge_ins_total,
            "fallback_turns": self.fallback_turns,
            "tts_cache_hits": self.tts_cache_hits,
            "tts_cache_misses": self.tts_cache_misses,
            "tts_cache_hit_rate": round(self.tts_cache_hits / total_tts, 3) if total_tts else 0.0,
            "pii_redactions": self.pii_redactions,
            "percentiles": percentiles,
        }


@dataclass
class QueueTelemetry:
    depth: int = 0
    max_depth: int = MAX_QUEUE_SIZE
    pushed: int = 0
    popped: int = 0
    dropped_backpressure: int = 0
    backpressure_active: bool = False
    last_latency_ms: float = 0.0
    updated_at: float = field(default_factory=time.time)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "depth": self.depth,
            "max_depth": self.max_depth,
            "pushed": self.pushed,
            "popped": self.popped,
            "dropped_backpressure": self.dropped_backpressure,
            "backpressure_active": self.backpressure_active,
            "last_latency_ms": round(self.last_latency_ms, 1),
        }


class BoundedPipelineQueue:
    """asyncio.Queue(maxsize=32) wrapper with honest backpressure behavior:
    when full, `drop_oldest_on_full=True` evicts the stalest queued item to
    keep latency low; `drop_oldest_on_full=False` drops the newest item.
    Either way `dropped_backpressure` rises so the UI flags backpressure."""

    def __init__(self, name: str, maxsize: int = MAX_QUEUE_SIZE):
        self.name = name
        self.maxsize = maxsize
        self._q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.telemetry = QueueTelemetry(max_depth=maxsize)

    async def put(self, item: Any, drop_oldest_on_full: bool = True) -> bool:
        t0 = time.perf_counter()
        ok = True
        if self._q.full():
            if drop_oldest_on_full:
                try:
                    self._q.get_nowait()          # evict stalest item
                    self.telemetry.popped += 1
                    self.telemetry.dropped_backpressure += 1
                except asyncio.QueueEmpty:
                    pass
                try:
                    self._q.put_nowait(item)
                except asyncio.QueueFull:
                    ok = False
                    self.telemetry.dropped_backpressure += 1
            else:
                ok = False
                self.telemetry.dropped_backpressure += 1
        else:
            self._q.put_nowait(item)
        self.telemetry.pushed += 1 if ok else 0
        self.telemetry.last_latency_ms = (time.perf_counter() - t0) * 1000
        self._refresh()
        return ok

    def put_nowait(self, item: Any) -> bool:
        try:
            self._q.put_nowait(item)
            self.telemetry.pushed += 1
            self._refresh()
            return True
        except asyncio.QueueFull:
            try:
                self._q.get_nowait()
                self.telemetry.dropped_backpressure += 1
            except asyncio.QueueEmpty:
                pass
            try:
                self._q.put_nowait(item)
                self.telemetry.pushed += 1
            except asyncio.QueueFull:
                pass
            self._refresh()
            return False

    async def get(self) -> Any:
        item = await self._q.get()
        self.telemetry.popped += 1
        self._refresh()
        return item

    def get_nowait(self) -> Any:
        try:
            item = self._q.get_nowait()
            self.telemetry.popped += 1
            self._refresh()
            return item
        except asyncio.QueueEmpty:
            return None

    def clear(self) -> int:
        """Barge-in flush: empty the entire queue instantly, return count."""
        n = 0
        while True:
            try:
                self._q.get_nowait()
                n += 1
            except asyncio.QueueEmpty:
                break
        self.telemetry.popped += n
        self._refresh()
        return n

    @property
    def depth(self) -> int:
        return self._q.qsize()

    def _refresh(self) -> None:
        d = self._q.qsize()
        self.telemetry.depth = d
        self.telemetry.backpressure_active = d >= self.maxsize
        self.telemetry.updated_at = time.time()


class PipelineBus:
    """Holds every hop queue of the 8-stage pipeline + aggregated telemetry
    exposed to the UI's 8-box live visualizer."""

    HOP_NAMES = [
        "mic_ingest",        # [1] -> [2]
        "stt",               # [2] -> [3]
        "endpointing",       # [3] -> [4]
        "brain",             # [4] -> [5]
        "clause_buffer",     # [5] -> [6]
        "tts",               # [6] -> [7]
        "jitter_buffer",     # [7] -> [8]
    ]

    def __init__(self) -> None:
        self.hops: Dict[str, BoundedPipelineQueue] = {
            n: BoundedPipelineQueue(n) for n in self.HOP_NAMES
        }

    def clear_all(self) -> Dict[str, int]:
        """Barge-in: flush every queue; returns per-hop eviction counts."""
        return {n: q.clear() for n, q in self.hops.items()}

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        return {n: q.telemetry.snapshot() for n, q in self.hops.items()}


class SessionPipeline:
    """Isolated per-call streaming state: bounded queues, jitter buffers, stage ms."""

    def __init__(self, call_id: str, max_queue_size: int = MAX_QUEUE_SIZE) -> None:
        from backend.pipeline.endpointing import EndpointingState
        from backend.pipeline.jitter_buffer import InputJitterBuffer, OutputJitterBuffer

        self.call_id = call_id
        self.bus = PipelineBus()
        if max_queue_size != MAX_QUEUE_SIZE:
            self.bus.hops = {
                n: BoundedPipelineQueue(n, maxsize=max_queue_size)
                for n in PipelineBus.HOP_NAMES
            }
        self.stages_ms: Dict[str, float] = {f"stage_{i}": 0.0 for i in range(1, 9)}
        self.endpoint_state = EndpointingState(mode="auto_vad")
        self.input_jitter = InputJitterBuffer()
        self.output_jitter = OutputJitterBuffer()
        self.metrics = LatencyTracker(maxlen=100)
        self.active_agent_reply: str = ""
        self.language: str = ""
        self.stt_language: str = ""
        self.created_at = time.time()
        self.last_active_at = self.created_at

    def touch(self) -> None:
        self.last_active_at = time.time()

    def stages_snapshot(self) -> Dict[str, float]:
        return {f"stage_{i}": round(float(self.stages_ms.get(f"stage_{i}", 0.0)), 2) for i in range(1, 9)}

    def flush_all(self) -> Dict[str, Any]:
        """<50ms stop & flush across queues + output jitter buffer."""
        q_flushed = self.bus.clear_all()
        out_flushed = self.output_jitter.flush()
        return {
            "queues": q_flushed,
            "output_jitter_flushed": out_flushed,
            "total_flushed": sum(q_flushed.values()) + out_flushed,
        }

    def snapshot(self) -> Dict[str, Any]:
        return {
            "call_id": self.call_id,
            "queues": self.bus.snapshot(),
            "stages_ms": self.stages_snapshot(),
            "input_jitter": self.input_jitter.snapshot(),
            "output_jitter": self.output_jitter.snapshot(),
            "slo": self.metrics.snapshot(),
            "last_active_at": round(self.last_active_at, 2),
        }


class SessionRegistry:
    """Scalable bounded multi-session coordinator with idle TTL eviction."""

    def __init__(self, max_sessions: int = 500, idle_ttl_s: float = 1800.0) -> None:
        self.max_sessions = max_sessions
        self.idle_ttl_s = idle_ttl_s
        self.sessions: Dict[str, SessionPipeline] = {}

    def evict_expired(self) -> int:
        now = time.time()
        expired = [
            cid for cid, sess in self.sessions.items()
            if (now - sess.last_active_at) > self.idle_ttl_s
        ]
        for cid in expired:
            self.sessions.pop(cid, None)
        if len(self.sessions) > self.max_sessions:
            by_age = sorted(self.sessions.items(), key=lambda kv: kv[1].last_active_at)
            to_drop = len(self.sessions) - self.max_sessions
            for cid, _ in by_age[:to_drop]:
                self.sessions.pop(cid, None)
            return len(expired) + to_drop
        return len(expired)

    def get_or_create(self, call_id: str) -> SessionPipeline:
        self.evict_expired()
        if call_id not in self.sessions:
            if len(self.sessions) >= self.max_sessions:
                oldest = min(self.sessions.items(), key=lambda kv: kv[1].last_active_at)[0]
                self.sessions.pop(oldest, None)
            self.sessions[call_id] = SessionPipeline(call_id)
        sess = self.sessions[call_id]
        sess.touch()
        return sess

    def get(self, call_id: str) -> Optional[SessionPipeline]:
        return self.sessions.get(call_id)

    def remove(self, call_id: str) -> Optional[SessionPipeline]:
        return self.sessions.pop(call_id, None)

    def snapshot(self) -> Dict[str, Any]:
        self.evict_expired()
        return {
            "active_sessions": len(self.sessions),
            "max_sessions": self.max_sessions,
            "idle_ttl_s": self.idle_ttl_s,
            "session_ids": list(self.sessions.keys())[-20:],
        }


BUS = PipelineBus()
REGISTRY = SessionRegistry()
METRICS = LatencyTracker(maxlen=200)
