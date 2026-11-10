"""
BimaVani — Devanagari → Roman Hinglish normalizer
=================================================
Gnani Prisma v3.0 returns Hindi words in Devanagari (e.g. "गाड़ी खराब",
"चोट", "सात हज़ार आठ सौ बीस") while the deterministic brain matches Roman
Hinglish keywords. This module transliterates Devanagari to the common
Hinglish spelling used across `synthetic_db.json`, so real Hindi speech
drives the same tools as typed/scripted Hinglish.

Rules are deliberately simple (consonant + inherent 'a', matras, nukta,
virama conjuncts, anusvara) — enough for keyword/number matching, never
used for display (the original transcript is preserved in memory/UI).
"""
from __future__ import annotations

import re

_CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ng",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h", "ळ": "l",
}
# nukta forms — Hinglish convention (गाड़ी -> gaadi, पढ़ -> padh)
_NUKTA = {"क": "q", "ख": "kh", "ग": "g", "ज": "z", "ड": "d", "ढ": "dh", "फ": "f"}

_VOWELS = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u",
    "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au",
    "ऍ": "e", "ऑ": "o",
}
_MATRAS = {
    "ा": "aa", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "ृ": "ri",
    "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o", "ॅ": "e", "ॆ": "e",
}
_SIGNS = {"ं": "n", "ँ": "n", "ः": "h", "्": "", "़": ""}
_DIGITS = {chr(0x0966 + i): str(i) for i in range(10)}
_DEV_RE = re.compile(r"[\u0900-\u097F]+")


def _word_to_hinglish(word: str) -> str:
    out: list[str] = []
    i, n = 0, len(word)
    while i < n:
        ch = word[i]
        if ch in _DIGITS:
            out.append(_DIGITS[ch]); i += 1; continue
        if ch in _CONS:
            base = _CONS[ch]
            j = i + 1
            if j < n and word[j] == "़":          # nukta modifies consonant
                base = _NUKTA.get(ch, base)
                j += 1
            if j < n and word[j] == "्":          # virama: conjunct, no vowel
                out.append(base); i = j + 1; continue
            if j < n and word[j] in _MATRAS:      # explicit matra
                out.append(base + _MATRAS[word[j]]); i = j + 1; continue
            # inherent 'a' — dropped at word end (खराब -> kharaab, ब -> b)
            out.append(base + ("" if j >= n else "a"))
            i = j; continue
        if ch in _VOWELS:
            out.append(_VOWELS[ch]); i += 1; continue
        if ch in _MATRAS:
            out.append(_MATRAS[ch]); i += 1; continue
        if ch in _SIGNS:
            out.append(_SIGNS[ch]); i += 1; continue
        out.append(ch); i += 1
    return "".join(out)


def to_hinglish(text: str) -> str:
    """Transliterate every Devanagari run in `text` to Roman Hinglish.
    Non-Devanagari text (English/numbers) passes through untouched."""
    if not text:
        return ""
    return _DEV_RE.sub(lambda m: " " + _word_to_hinglish(m.group(0)) + " ", text)


def normalize_for_matching(text: str) -> str:
    """Original text + transliterated Hinglish, ready for `.lower()` matching.
    Keeps Latin words (radiator, highway, policy) exactly as spoken."""
    t = text or ""
    h = to_hinglish(t)
    return f"{t} {h}".strip()
