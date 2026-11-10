"""
BimaVani — Devanagari & Tamil → Canonical Matching Normalizer
==============================================================
Gnani Prisma v2.5/v3.0 returns Hindi in Devanagari (e.g. "गाड़ी खराब") and
Tamil in native Tamil script (e.g. "ரேடியேட்டர் உடைஞ்சிடுச்சு", "காயமில்லை").
This module:
  1. Detects script/language cues (`detect_script_language` -> "ta-IN" | "hi-IN" | None)
  2. Transliterates Devanagari (decomposed & precomposed nukta) to Hinglish
  3. Maps Tamil script + Tamil spoken numbers + key Tanglish cues into
     canonical matching tokens alongside phonetic transliteration
Never used for display — the caller's original transcript is preserved in memory/UI.
"""
from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Devanagari -> Roman Hinglish
# ---------------------------------------------------------------------------

_CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ng",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "ऩ": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ऱ": "r", "ल": "l", "व": "v", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h", "ळ": "l", "ऴ": "l",
}
_NUKTA = {"क": "q", "ख": "kh", "ग": "g", "ज": "z", "ड": "d", "ढ": "dh", "फ": "f", "य": "y"}

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

# ---------------------------------------------------------------------------
# Tamil script (U+0B80..U+0BFF) -> Canonical matching tokens + phonetic Latin
# ---------------------------------------------------------------------------

_TAMIL_RE = re.compile(r"[\u0B80-\u0BFF]+")
_TAMIL_DIGITS = {chr(0x0BE6 + i): str(i) for i in range(10)}

_TAMIL_VOWELS = {
    "அ": "a", "ஆ": "aa", "இ": "i", "ஈ": "i", "உ": "u", "ஊ": "u",
    "எ": "e", "ஏ": "e", "ஐ": "ai", "ஒ": "o", "ஓ": "o", "ஔ": "au",
}
_TAMIL_MATRAS = {
    "ா": "aa", "ி": "i", "ீ": "i", "ு": "u", "ூ": "u",
    "ெ": "e", "ே": "e", "ை": "ai", "ொ": "o", "ோ": "o", "ௌ": "au",
}
_TAMIL_CONS = {
    "க": "k", "ங": "ng", "ச": "ch", "ஜ": "j", "ஞ": "ny",
    "ட": "t", "ண": "n", "த": "th", "ந": "n", "ன": "n",
    "ப": "p", "ம": "m", "ய": "y", "ர": "r", "ற": "r",
    "ல": "l", "ள": "l", "ழ": "zh", "வ": "v",
    "ஶ": "sh", "ஷ": "sh", "ஸ": "s", "ஹ": "h",
}

# Ordered longest-first regex rules translating native Tamil & Tanglish phrases
# into canonical Hinglish/English keywords for the deterministic extractors.
_TAMIL_PHRASE_MAP: list[tuple[str, str]] = [
    # Safety & injury negation (check BEFORE raw injury terms)
    (r"யாருக்கும்\s*காயமில்லை|யாருக்கும்\s*காயம்\s*இல்லை|யாருக்கும்\s*அடி\s*படல|யாருக்கும்\s*அடிபடல|யாருக்கும்\s*அடி\s*படவில்லை|யாருக்கும்\s*எதுவும்\s*ஆகல", "kisi ko chot nahi lagi safe"),
    (r"காயமில்லை|காயமில்லா|காயம்\s*இல்லை|காயம்\s*இல்ல|காயம்\s*எதுவும்\s*இல்லை|காயம்\s*ஏதும்\s*இல்லை|அடி\s*படல|அடிபடல|அடிபடவில்லை|அடி\s*படவில்லை|அடி\s*எதுவும்\s*படவில்லை", "chot nahi lagi safe"),
    (r"பாதுகாப்பாக\s*இருக்கோம்|பாதுகாப்பா\s*இருக்கோம்|பத்திரமா\s*இருக்கோம்|நல்லா\s*இருக்கோம்|எல்லாரும்\s*நல்லா|சேஃபா\s*இருக்கோம்|பாதுகாப்பு", "sab safe theek hai"),
    (r"ஆம்புலன்ஸ்\s*வேண்டாம்", "ambulance nahi"),
    # Positive injury
    (r"இரத்தம்\s*வருது|ரத்தம்\s*வருது|இரத்தம்|ரத்தம்|இரத்தக்\s*காயம்", "khoon bleeding"),
    (r"காயம்பட்ட|காயம்\s*ஏற்பட்டது|காயம்", "chot injury"),
    (r"அடிபட்டு|அடி\s*பட்டு|மயக்கம்|எலும்பு\s*முறிவு|வலி\s*அதிகமா", "injured"),
    (r"ஆம்புலன்ஸ்|மருத்துவமனை|ஆஸ்பத்திரி", "ambulance hospital"),
    # Vehicle parts (native Tamil & transliterated loanwords)
    (r"ரேடியேட்டர்|ரேடியட்டர்", "radiator"),
    (r"கூலன்ட்|நீர்\s*கசிவு|தண்ணீர்\s*கசிவு|என்ஜின்\s*சூடாகி|வண்டி\s*சூடாகி|ஆவி\s*வருது|புகை\s*வருது", "radiator coolant smoke"),
    (r"பஞ்ச்சர்|பஞ்சர்|டயர்\s*வெடிச்சிருச்சு|டயர்\s*வெடிச்சு|டயர்\s*காற்று\s*போச்சு|டயர்\s*பிளாட்", "puncture"),
    (r"பேட்டரி|மின்கலம்", "battery"),
    (r"பெட்ரோல்\s*தீர்ந்து|பெட்ரோல்\s*காலி|டீசல்\s*தீர்ந்து|டீசல்\s*காலி|எரிபொருள்\s*காலி", "petrol khatam"),
    (r"பம்பர்", "bumper"),
    (r"ஹெட்லைட்|முகப்பு\s*விளக்கு|முன்\s*விளக்கு", "headlight"),
    (r"சைடு\s*மிரர்|பக்கவாட்டு\s*கண்ணாடி|சைடு\s*கண்ணாடி", "side mirror"),
    (r"ஆக்ஸில்|அச்சு\s*முறிவு|சக்கரம்\s*உடைஞ்சு|வீல்\s*உடைஞ்சு", "axle"),
    (r"கிளட்ச்\s*ஒயர்|கிளட்ச்\s*வயர்|கிளட்ச்\s*கம்பி", "clutch wire"),
    (r"பிரண்ட்\s*கார்டு|முன்\s*கார்டு", "front guard"),
    (r"இவி\s*சிஸ்டம்|இ\.வி\.\s*சிஸ்டம்", "ev system stuck"),
    # Road types
    (r"தேசிய\s*நெடுஞ்சாலை|நெடுஞ்சாலை|ஹைவேயில்|ஹைவேல|ஹைவே|எக்ஸ்பிரஸ்வே|என்\.?\s*எச்\.?", "highway"),
    (r"கிராமத்து\s*சாலை|கிராம\s*சாலை|கிராமம்|கிராமத்தில்|ஊர்\s*சாலை|ஊர்ல|லிங்க்\s*ரோடு|மண்\s*சாலை", "gaon village rural link road"),
    (r"நகர\s*சாலை|நகரத்தில்|நகரத்துல|நகரம்|பட்டணம்|சிட்டி|சென்னை|மதுரை|கோவை|கோயம்புத்தூர்|திருச்சி|சேலம்|வேலூர்", "city"),
    # Drivability & breakdown signals
    (r"ஓட்ட\s*முடியாது|ஓட்ட\s*இயலாது|நகர\s*முடியாது|நகர்த்த\s*முடியாது|வண்டி\s*நகரல|ஓட\s*மாட்டேங்குது", "chalne layak nahi"),
    (r"ஸ்டார்ட்\s*ஆகல|ஸ்டார்ட்\s*ஆகவில்லை|ஸ்டார்ட்\s*ஆகமாட்டேங்குது|வண்டி\s*ஓடல|வண்டி\s*ஓடவில்லை", "start nahi"),
    (r"உடைஞ்சிடுச்சு|உடைஞ்சு|உடைந்து|கெட்டுப்\s*போச்சு|பழுதாகி|பிரச்சினை|பிரச்சனை|கோளாறு|வண்டி\s*நின்னுடுச்சு|வண்டி\s*நின்றுவிட்டது|விபத்து|மோதல்", "breakdown crash"),
    # Policy & confirmation & barge-in
    (r"பாலிசி\S*\s*எண்\S*|பாலிசி\S*\s*நம்பர்\S*|பாலிசி\S*", "policy number"),
    (r"நில்லுங்க\S*|நிறுத்துங்க\S*|இருங்க\S*|ஒரு\s*நிமிஷம்|தப்பு|தவறு|இல்லை\s*இல்லை|இல்ல\s*இல்ல|மாத்துங்க\S*|மாற்றுங்க\S*", "ruko galat change"),
    (r"ஆமாம்|ஆமா|சரிங்க|சரி|பண்ணுங்க\S*|செய்யுங்க\S*|உறுதி\s*செய்யுங்க\S*|கன்ஃபர்ம்|ஓகே", "haan confirm"),
    (r"இல்லை|இல்ல|வேண்டாம்|கூடாது|படல|கிடையாது", "nahi"),
    # Tamil spoken numerals -> Hindi canonical tokens for unified number parser
    (r"பூஜ்ஜியம்|பூஜ்யம்|சைபர்", "zero"),
    (r"ஏழாயிரத்து|ஏழு\s*ஆயிரத்து|ஏழாயிரம்", "saat hazaar"),
    (r"நாலாயிரத்து|நான்காயிரத்து|நாலாயிரம்|நான்காயிரம்", "chaar hazaar"),
    (r"ஓராயிரத்து|ஆயிரத்து|ஆயிரம்", "ek hazaar"),
    (r"ஒன்பதாயிரத்து|ஒன்பது\s*ஆயிரத்து|ஒன்பதாயிரம்", "nau hazaar"),
    (r"மூவாயிரத்து|மூன்று\s*ஆயிரத்து|மூவாயிரம்", "teen hazaar"),
    (r"எண்ணூற்று|எண்ணூறு", "aath sau"),
    (r"நானூற்று|நானூறு", "chaar sau"),
    (r"தொள்ளாயிரத்து|தொள்ளாயிரம்", "nau sau"),
    (r"முன்னூற்று|முன்னூறு", "teen sau"),
    (r"தொண்ணூற்று\s*ஒன்று|தொண்ணூற்று\s*ஒண்ணு", "ikyaanave"),
    (r"ஐம்பத்தைந்து|அம்பத்தஞ்சு", "pachpan"),
    (r"முப்பத்திரண்டு|முப்பத்தி\s*ரெண்டு", "battees"),
    (r"நாற்பத்து\s*நான்கு|நாற்பத்தி\s*நாலு", "chawaalis"),
    (r"இருபது", "bees"),
    (r"பத்து", "das"),
    (r"ஒன்று|ஒண்ணு", "ek"),
    (r"இரண்டு|ரெண்டு", "do"),
    (r"மூன்று|மூணு", "teen"),
    (r"நான்கு|நாலு", "chaar"),
    (r"ஐந்து|அஞ்சு", "paanch"),
    (r"ஆறு", "che"),
    (r"ஏழு", "saat"),
    (r"எட்டு", "aath"),
    (r"ஒன்பது", "nau"),
]

_COMPILED_TAMIL_MAP = [(re.compile(pat, re.I), repl) for pat, repl in _TAMIL_PHRASE_MAP]

# Unambiguous Roman Tanglish -> canonical tokens (zero collision with Hinglish/English)
_TANGLISH_WORD_MAP: list[tuple[str, str]] = [
    (r"\b(?:kaayam|kayam)\s+(?:illa|illai)\b", "chot nahi lagi safe"),
    (r"\b(?:adi)\s+(?:padala|padavillai)\b", "chot nahi lagi safe"),
    (r"\b(?:kaayam|kayam)\b", "chot injury"),
    (r"\b(?:raththam|iratham)\b", "khoon bleeding"),
    (r"\b(?:panjar|panchar)\b", "puncture"),
    (r"\b(?:nedunjalai|nedunchalai)\b", "national highway"),
    (r"\b(?:kiramam|gramam)\b", "gaon village rural"),
    (r"\b(?:otta\s+mudiyathu|otta\s+mudiyala|nagara\s+mudiyala|start\s+aagala|start\s+aagavillai)\b", "chalne layak nahi"),
    (r"\b(?:udanjiduchu|udanchiduchu|odanjiduchu)\b", "breakdown"),
    (r"\b(?:niruthunga|nillunga|irunga|thappu|maatthunga)\b", "ruko galat change"),
    (r"\b(?:aamam|aamaa|aama|saringa|pannunga|seyunga)\b", "haan confirm"),
    (r"\b(?:illai|illa)\b", "nahi"),
]
_COMPILED_TANGLISH_MAP = [(re.compile(pat, re.I), repl) for pat, repl in _TANGLISH_WORD_MAP]

# Unambiguous Roman Tanglish vocabulary cues (used ONLY when no Tamil/Devanagari script present)
_TANGLISH_CUES_RE = re.compile(
    r"\b(?:vanakkam|enakku|enaku|illai|aamam|vandi|kaayam|raththam|iratham|"
    r"pannunga|niruthunga|sollunga|udanjiduchu|udanchiduchu|odanjiduchu|"
    r"aayiduchu|aagiduchu|thevai|paathukonga|nedunjalai|kaayame|saringa)\b",
    re.I,
)


def detect_script_language(text: str) -> Optional[str]:
    """Detect caller language from transcript text ('ta-IN' | 'hi-IN' | None)."""
    t = text or ""
    if _TAMIL_RE.search(t):
        return "ta-IN"
    if _DEV_RE.search(t):
        return "hi-IN"
    if _TANGLISH_CUES_RE.search(t):
        return "ta-IN"
    return None


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


def _tamil_word_to_latin(word: str) -> str:
    out: list[str] = []
    i, n = 0, len(word)
    while i < n:
        ch = word[i]
        if ch in _TAMIL_DIGITS:
            out.append(_TAMIL_DIGITS[ch])
            i += 1
            continue
        if ch in _TAMIL_CONS:
            base = _TAMIL_CONS[ch]
            j = i + 1
            if j < n and word[j] == "்":
                out.append(base)
                i = j + 1
                continue
            if j < n and word[j] in _TAMIL_MATRAS:
                out.append(base + _TAMIL_MATRAS[word[j]])
                i = j + 1
                continue
            out.append(base + ("" if j >= n else "a"))
            i = j
            continue
        if ch in _TAMIL_VOWELS:
            out.append(_TAMIL_VOWELS[ch])
            i += 1
            continue
        if ch in _TAMIL_MATRAS:
            out.append(_TAMIL_MATRAS[ch])
            i += 1
            continue
        if ch in ("்", "ஃ"):
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def tamil_to_canonical(text: str) -> str:
    """Translate native Tamil phrases/numbers + transliterate remaining Tamil runs."""
    if not text or not _TAMIL_RE.search(text):
        return text or ""
    work = text
    # Convert Tamil digits first
    for td, d in _TAMIL_DIGITS.items():
        if td in work:
            work = work.replace(td, d)
    # Handle NH-44 in Tamil ("என்.எச்-44" -> "NH-44")
    work = re.sub(r"என்\.?\s*எச்\.?\s*[-\s]?(\d+)", r" nh-\1 ", work)
    # Apply canonical phrase map
    for rx, repl in _COMPILED_TAMIL_MAP:
        work = rx.sub(lambda m: f" {repl} ", work)
    # Transliterate any remaining Tamil runs phonetically
    work = _TAMIL_RE.sub(lambda m: " " + _tamil_word_to_latin(m.group(0)) + " ", work)
    return re.sub(r"\s+", " ", work).strip()


def to_hinglish(text: str) -> str:
    """Transliterate every Devanagari run in `text` to Roman Hinglish.
    Non-Devanagari text (English/numbers) passes through untouched."""
    if not text:
        return ""
    raw = _DEV_RE.sub(lambda m: " " + _word_to_hinglish(m.group(0)) + " ", text)
    return re.sub(r"\s+", " ", raw).strip()


def _apply_tanglish(text: str) -> str:
    work = text
    for rx, repl in _COMPILED_TANGLISH_MAP:
        work = rx.sub(repl, work)
    return re.sub(r"\s+", " ", work).strip()


def normalize_for_matching(text: str) -> str:
    """Original text + transliterated/canonical tokens for `.lower()` matching.
    Keeps Latin words (radiator, highway, policy) exactly as spoken."""
    t = text or ""
    if not t:
        return ""
    if _TAMIL_RE.search(t):
        return _apply_tanglish(tamil_to_canonical(t))
    if _DEV_RE.search(t):
        h = to_hinglish(t)
        return f"{t} {h}".strip() if h and h != t else t
    if _TANGLISH_CUES_RE.search(t):
        return _apply_tanglish(t)
    return t
