"""
BimaVani — G4 Guardrail: 2-Clause Smart Chunk Buffer
====================================================
Clause explosion math that keeps credits alive:
  * 6 tiny TTS calls -> burns credit pool + risks HTTP 429
  * 1 huge paragraph -> slow time-to-first-audio (bad UX)
  * BimaVani: AT MOST 2 spoken clauses per reply:
      Clause 1 — fast empathy / triage hook        -> TTS FIRST (<400ms TTFA)
      Clause 2 — actionable RSA + claim read-back  -> TTS SECOND, gapless
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

MAX_CLAUSES = 2

# Hinglish hooks that MUST land in Clause 1 (instant empathy/urgency)
_HOOK_SPLIT_PATTERNS = [
    r"Shukar hai[^.!?]*[.!?]",
    r"Theek hai[^.!?]*[.!?]",
    r"Samajh gaya[^.!?]*[.!?]",
    r"Aapki jaan[^.!?]*[.!?]",
    r"Bilkul[^.!?]*[.!?]",
    r"Achha ji[^.!?]*[.!?]",
    r"Haan ji[^.!?]*[.!?]",
    r"Arre[^.!?]*[.!?]",
]
_FALLBACK_SPLIT_RE = re.compile(r"(?<=[.!?।])\s+")


@dataclass
class ClauseBatch:
    turn_id: str
    clauses: List[Dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def add(self, text: str, is_priority: bool) -> None:
        self.clauses.append(
            {
                "index": len(self.clauses) + 1,
                "text": text,
                "priority": "clause1_fast_hook" if is_priority else "clause2_details",
                "tts_order": 1 if is_priority else 2,
            }
        )

    def as_list(self) -> List[Dict[str, Any]]:
        return self.clauses


def split_into_clauses(reply_text: str, turn_id: str = "t0") -> ClauseBatch:
    """Split ONE agent reply into AT MOST 2 clauses (never more).

    Strategy:
      1. If two sentences with '.'/'!"/'?' AND first matches a hook pattern
         -> (hook, remainder).
      2. Else split on the FIRST sentence boundary (hook = sentence 1).
      3. If no boundary exists and text is long, split at the comma nearest
         to 40% of the length (spoken cadence point).
      4. Never emit >2 or 0 clauses; empty input -> a single neutral clause.
    """
    text = (reply_text or "").strip()
    batch = ClauseBatch(turn_id=turn_id)
    if not text:
        batch.add("Ji, boliye main aapki madad kar raha hoon.", True)
        return batch

    sentences = [s.strip() for s in _FALLBACK_SPLIT_RE.split(text) if s.strip()]
    if len(sentences) >= 2 and _matches_hook(sentences[0]):
        batch.add(sentences[0], True)
        batch.add(" ".join(sentences[1:]), False)
        return batch

    if len(sentences) >= 2:
        batch.add(sentences[0], not _matches_hook(sentences[0]))
        batch.add(" ".join(sentences[1:]), False)
        return batch

    # single sentence
    if len(text) <= 120:
        batch.add(text, True)
        return batch

    cut = _best_comma_cut(text)
    batch.add(text[:cut].strip(), True)
    batch.add(text[cut:].strip(), False)
    return batch


def _matches_hook(s: str) -> bool:
    return any(re.match(p, s, re.IGNORECASE) for p in _HOOK_SPLIT_PATTERNS)


def _best_comma_cut(text: str) -> int:
    target = max(1, int(len(text) * 0.4))
    best, best_d = None, 10**9
    for m in re.finditer(r"[,]", text):
        d = abs(m.start() - target)
        if d < best_d:
            best, best_d = m.start() + 1, d
    if best is None:
        mid = len(text) // 2
        space = text.rfind(" ", 0, mid)
        return space if space > 0 else len(text)
    return best


class ClauseBuffer:
    """Streaming side of the 2-Clause buffer: receives FINAL reply text from
    the Brain ([5]) and emits an ordered, TTS-ready clause pipeline to [6]."""

    def __init__(self) -> None:
        self.batches: Dict[str, ClauseBatch] = {}

    def ingest(self, turn_id: str, reply_text: str) -> ClauseBatch:
        batch = split_into_clauses(reply_text, turn_id)
        # hard enforcement: never more than MAX_CLAUSES
        if len(batch.clauses) > MAX_CLAUSES:
            keep = batch.clauses[:MAX_CLAUSES]
            keep[-1]["text"] = " ".join(c["text"] for c in batch.clauses[MAX_CLAUSES - 1:])
            batch.clauses = keep
        for i, c in enumerate(batch.clauses):
            c["index"] = i + 1
        self.batches[turn_id] = batch
        return batch

    def flush(self, turn_id: str) -> None:
        self.batches.pop(turn_id, None)
