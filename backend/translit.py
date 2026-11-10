"""
BimaVani — Devanagari → Roman Hinglish normalizer
=================================================
Gnani Prisma v2.5/v3.0 returns Hindi words in Devanagari (e.g. "गाड़ी खराब",
"चोट", "सात हज़ार आठ सौ बीस") while the deterministic brain matches Roman
Hinglish keywords. This module transliterates Devanagari (both decomposed and
precomposed nukta codepoints) to the common Hinglish spelling used across
`synthetic_db.json`, so real Hindi speech drives the same tools as typed/scripted
Hinglish.

Rules are deliberately simple (consonant + inherent 'a', nukta, matras,
virama conjuncts, anusvara) — enough for keyword/number matching, never used
for display (the original transcript is preserved in memory/UI).
"""
from __future__ import annotations

import re

_CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ng",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "ऩ": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ऱ": "r", "ल": "l", "व": "v", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h", "ळ": "l", "ऴ": "l",
}
# Decomposed nukta base overrides (consonant + U+093C)
_NUKTA = {"क": "q", "ख": "kh", "ग": "g", "ज": "z", "ड": "d", "ढ": "dh", "फ": "f", "य": "y"}

# Precomposed Unicode nukta codepoints (U+0958..U+095F) -> Hinglish
# Written with explicit \u escapes so editors/serializers never NFD-decompose them.
_PRECOMPOSED_NUKTA = {
    "\u0958": "q",   # क़
    "\u0959": "kh",  # ख़
    "\u095a": "g",   # ग़
    "\u095b": "z",   # ज़
    "\u095c": "d",   # ड़
    "\u095d": "dh",  # ढ़
    "\u095e": "f",   # फ़
    "\u095f": "y",   # य़
}

_VOWELS = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u",
    "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au",
    "ऍ": "e", "ऑ": "o", "ऎ": "e", "ऒ": "o",
}
_MATRAS = {
    "ा": "aa", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "ृ": "ri",
    "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o", "ॅ": "e",
    "ॆ": "e", "ॊ": "o",
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
            out.append(_DIGITS[ch])
            i += 1
            continue
        if ch in _PRECOMPOSED_NUKTA or ch in _CONS:
            if ch in _PRECOMPOSED_NUKTA:
                base = _PRECOMPOSED_NUKTA[ch]
                j = i + 1
            else:
                base = _CONS[ch]
                j = i + 1
                if j < n and word[j] == "़":      # decomposed nukta modifies consonant
                    base = _NUKTA.get(ch, base)
                    j += 1
            if j < n and word[j] == "्":          # virama: conjunct, no vowel
                out.append(base)
                i = j + 1
                continue
            if j < n and word[j] in _MATRAS:      # explicit matra
                out.append(base + _MATRAS[word[j]])
                i = j + 1
                continue
            # inherent 'a' — dropped at word end (खराब -> kharaab, गाड़ी -> gaadi)
            out.append(base + ("" if j >= n else "a"))
            i = j
            continue
        if ch in _VOWELS:
            out.append(_VOWELS[ch])
            i += 1
            continue
        if ch in _MATRAS:
            out.append(_MATRAS[ch])
            i += 1
            continue
        if ch in _SIGNS:
            out.append(_SIGNS[ch])
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def to_hinglish(text: str) -> str:
    """Transliterate every Devanagari run in `text` to Roman Hinglish.
    Non-Devanagari text (English/numbers) passes through untouched."""
    if not text:
        return ""
    raw = _DEV_RE.sub(lambda m: " " + _word_to_hinglish(m.group(0)) + " ", text)
    return re.sub(r"\s+", " ", raw).strip()


def normalize_for_matching(text: str) -> str:
    """Original text + transliterated Hinglish, ready for `.lower()` matching.
    Keeps Latin words (radiator, highway, policy) exactly as spoken."""
    t = text or ""
    h = to_hinglish(t)
    if not h or h == t:
        return t
    return f"{t} {h}".strip()
