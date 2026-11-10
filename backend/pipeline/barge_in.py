"""
BimaVani — G6 Guardrail: Turn-Taking State Machine & Barge-In Controller
========================================================================
Contract: "Stop & Flush ALL < 50ms" from the 8-stage architecture diagram.

  IDLE --(driver dial / PTT press)--> LISTENING
  LISTENING --(endpoint)--> THINKING
  THINKING --(reply ready)--> SPEAKING
  SPEAKING --(turn ends)--> IDLE
  ANY     --(BARGE-IN press)-->> INTERRUPTED  (flush all, <50ms)
  INTERRUPTED --(flush done)--> LISTENING (next turn)

Browser must ALSO stop playback <20ms and clear the jitter buffer;
backend cancels in-flight asyncio tasks + flushes the clause queue.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# Allowed transitions (ANY state is allowed to SKIP to INTERRUPTED)
TRANSITIONS: Dict[str, set[str]] = {
    "IDLE": {"LISTENING"},
    "LISTENING": {"THINKING", "INTERRUPTED", "IDLE"},
    "THINKING": {"SPEAKING", "INTERRUPTED", "IDLE"},
    "SPEAKING": {"IDLE", "INTERRUPTED", "LISTENING"},
    "INTERRUPTED": {"LISTENING", "IDLE"},
}


@dataclass
class BargeInEvent:
    at_ms: float = field(default_factory=lambda: time.time() * 1000)
    source: str = "driver_button"
    transcript: str = ""
    cleared_audio_bytes: int = 0
    cancelled_tasks: int = 0
    flushed_clauses: int = 0
    flush_latency_ms: float = 0.0


class TurnStateMachine:
    """Single instance per live call session."""

    def __init__(self, session_id: str = "default"):
        self.session_id = session_id
        self.state: str = "IDLE"
        self.turn_counter = 0
        self.last_transition_ms = time.time() * 1000
        self.events: List[BargeInEvent] = []
        self._tasks: set[asyncio.Task] = set()
        self._flush_hooks: List[Callable[[], Any]] = []

    # -- registry for cooperative cancel -----------------------------------
    def register_task(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def register_flush_hook(self, hook: Callable[[], Any]) -> None:
        self._flush_hooks.append(hook)

    # -- transitions --------------------------------------------------------
    def transition(self, new_state: str) -> bool:
        if new_state == self.state:
            return True
        if new_state not in TRANSITIONS.get(self.state, set()):
            return False
        self.state = new_state
        self.last_transition_ms = time.time() * 1000
        return True

    # -- G6: barge-in -------------------------------------------------------
    async def trigger_barge_in(self, transcript: str = "",
                               flush_fn: Optional[Callable[[], Any]] = None) -> BargeInEvent:
        """Cancel in-flight tasks + flush queues/clauses, all <50ms target."""
        ev = BargeInEvent(transcript=transcript)
        self.state = "INTERRUPTED"                       # unconditional skip
        self.last_transition_ms = ev.at_ms
        self.turn_counter += 1

        t0 = time.perf_counter()
        cancelled = 0
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
                cancelled += 1
        flushed = 0
        if flush_fn is not None:
            try:
                out = flush_fn()
                flushed = out.get("flushed_clauses", 0) if isinstance(out, dict) else 0
            except Exception:
                flushed = 0
        for hook in self._flush_hooks:
            try:
                hook()
            except Exception:
                pass
        ev.cancelled_tasks = cancelled
        ev.flushed_clauses = flushed
        ev.flush_latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        self.events.append(ev)
        if len(self.events) > 100:
            self.events = self.events[-100:]
        return ev

    def resume_listening(self) -> bool:
        """After flush completes -> back to LISTENING for the corrected turn."""
        return self.transition("LISTENING")

    def snapshot(self) -> Dict[str, Any]:
        last = self.events[-1] if self.events else None
        return {
            "session_id": self.session_id,
            "state": self.state,
            "turn_counter": self.turn_counter,
            "last_transition_ms": round(self.last_transition_ms, 1),
            "last_barge_in": {
                "flush_latency_ms": last.flush_latency_ms,
                "cancelled_tasks": last.cancelled_tasks,
                "flushed_clauses": last.flushed_clauses,
                "source": last.source,
                "transcript": last.transcript,
            } if last else None,
            "barge_in_count": len(self.events),
        }


SESSION_STATE_MACHINES: Dict[str, TurnStateMachine] = {}


def get_state_machine(session_id: str = "default") -> TurnStateMachine:
    if session_id not in SESSION_STATE_MACHINES:
        SESSION_STATE_MACHINES[session_id] = TurnStateMachine(session_id)
    return SESSION_STATE_MACHINES[session_id]


def reset_state_machine(session_id: str) -> None:
    sm = SESSION_STATE_MACHINES.pop(session_id, None)
    if sm is not None:
        for task in list(sm._tasks):
            if not task.done():
                task.cancel()
