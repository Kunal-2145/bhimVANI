"""
BimaVani — DPDP PII Redaction & Call-Persistence Compliance Guard
=================================================================
Ensures real Indian personal identifiers (Aadhaar, PAN, 10-digit mobile
numbers, and email addresses) are masked before long-term persistence
(`call_history.json`) or optional LLM context serialization, while strictly
preserving synthetic 4-digit policy IDs, claim IDs, highway markers (NH-44),
helplines (112 / 1033), and INR rupee amounts.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Tuple

# Order matters: check 12-digit Aadhaar before 10-digit mobile so 12 digits aren't split.
_AADHAAR_RE = re.compile(r"(?<!\d)(\d{4})[\s-]?(\d{4})[\s-]?(\d{4})(?!\d)")
_PAN_RE = re.compile(r"\b[A-Za-z]{5}\d{4}[A-Za-z]\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def redact_pii(text: str) -> Tuple[str, int]:
    """Return `(redacted_text, redactions_count)` safe for logs/storage/LLM."""
    if not text or not isinstance(text, str):
        return text, 0
    total = 0

    def _mask_aadhaar(m: re.Match) -> str:
        nonlocal total
        total += 1
        return f"XXXX-XXXX-{m.group(3)}"

    out = _AADHAAR_RE.sub(_mask_aadhaar, text)
    out, n_pan = _PAN_RE.subn("[PAN_REDACTED]", out)
    total += n_pan
    out, n_phone = _PHONE_RE.subn("[PHONE_REDACTED]", out)
    total += n_phone
    out, n_email = _EMAIL_RE.subn("[EMAIL_REDACTED]", out)
    total += n_email
    return out, total


def redact_structure(obj: Any) -> Tuple[Any, int]:
    """Recursively redact PII strings across nested dicts/lists (returns copy)."""
    if isinstance(obj, str):
        return redact_pii(obj)
    if isinstance(obj, list):
        total = 0
        res = []
        for item in obj:
            cleaned, n = redact_structure(item)
            total += n
            res.append(cleaned)
        return res, total
    if isinstance(obj, dict):
        total = 0
        res: Dict[str, Any] = {}
        for k, v in obj.items():
            cleaned, n = redact_structure(v)
            total += n
            res[k] = cleaned
        return res, total
    return obj, 0
