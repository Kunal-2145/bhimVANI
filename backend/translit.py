"""
BimaVani — All-India Multilingual Script Detector & NLU Normalizer
====================================================================
Handles all 10 languages supported by Gnani Prisma v2.5/v3.0 STT and
Timbre v2.5/v3.0 TTS:
  hi-IN (Hindi/Hinglish), ta-IN (Tamil), te-IN (Telugu), kn-IN (Kannada),
  ml-IN (Malayalam), bn-IN (Bengali), mr-IN (Marathi), gu-IN (Gujarati),
  pa-IN (Punjabi), en-IN (English India).

1. `detect_script_language(text)` -> BCP-47 tag or None
2. `to_hinglish(text)`            -> Devanagari-to-Hinglish transliteration
3. `normalize_for_matching(text)` -> Canonical matching text preserving clause breaks
"""
from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Devanagari -> Roman Hinglish (kept exact for existing Hinglish & nukta tests)
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
# All Indic Script Unicode Regexes
# ---------------------------------------------------------------------------

_TAMIL_RE = re.compile(r"[\u0B80-\u0BFF]+")
_TELUGU_RE = re.compile(r"[\u0C00-\u0C7F]+")
_KANNADA_RE = re.compile(r"[\u0C80-\u0CFF]+")
_MALAYALAM_RE = re.compile(r"[\u0D00-\u0D7F]+")
_BENGALI_RE = re.compile(r"[\u0980-\u09FF]+")
_GUJARATI_RE = re.compile(r"[\u0A80-\u0AFF]+")
_GURMUKHI_RE = re.compile(r"[\u0A00-\u0A7F]+")
_NON_DEV_INDIC_RE = re.compile(r"[\u0980-\u0D7F]+")

# Marathi cues inside Devanagari (keeps Devanagari Hindi & Hinglish mix cleanly as hi-IN)
_MARATHI_CHARS_RE = re.compile(r"[ळऱ]")
_MARATHI_CUES_RE = re.compile(
    r"(?:मला|माझी|माझा|माझे|तुम्ही|आहेत|आहे|नाही|नाहीये|जखम|अपघात|रस्त्यावर|महामार्गावर|महामार्ग|"
    r"गावात|शहरात|थांबा|चुकलं|चुकले|बदला|दुखापत|सांगा|मदत|नोंदवा|निश्चित)"
)

# Clear pure Indian English sentence markers (never triggers on terse Hinglish phrases)
_ENGLISH_SENTENCE_RE = re.compile(
    r"\b(?:my car|my vehicle|my policy|we are safe|everyone is safe|no one is|nobody is|"
    r"nobody got hurt|please send|please confirm|thank you|yes please|yes confirm|"
    r"is broken|is damaged|is dead|is leaking|broke down|broken down|head injury|"
    r"severe bleeding|bleeding and|car accident|in crash|flat tyre|flat tire|tow truck|"
    r"on the highway|in the city|village road|help me|do not start|what happened)\b",
    re.I,
)
_HINGLISH_EXCL_RE = re.compile(
    r"\b(?:hai|hain|ho|gaya|gayi|gaye|nahi|nahin|chot|gaadi|kya|aap|haan|ruko|"
    r"bhejo|bhejiye|mein|par|ka|ki|ke|ji|saat|aath|chaar|hazaar|khatam|phat|toot)\b",
    re.I,
)

# Tamil Latin cues (backwards-compatible Tanglish support)
_TANGLISH_CUES_RE = re.compile(
    r"\b(?:vanakkam|enakku|enaku|illai|aamam|vandi|kaayam|raththam|iratham|"
    r"pannunga|niruthunga|sollunga|udanjiduchu|udanchiduchu|odanjiduchu|"
    r"aayiduchu|aagiduchu|thevai|paathukonga|nedunjalai|kaayame|saringa)\b",
    re.I,
)

# ---------------------------------------------------------------------------
# Generic Brahmic Unicode Offset Phonetic Transliterator (bn/pa/gu/te/kn/ml)
# ---------------------------------------------------------------------------

_BRAHMIC_BASES = (0x0980, 0x0A00, 0x0A80, 0x0C00, 0x0C80, 0x0D00)
_BRAHMIC_OFFSETS_VOWEL = {
    0x05: "a", 0x06: "aa", 0x07: "i", 0x08: "i", 0x09: "u", 0x0A: "u",
    0x0B: "ri", 0x0E: "e", 0x0F: "e", 0x10: "ai", 0x12: "o", 0x13: "o", 0x14: "au",
}
_BRAHMIC_OFFSETS_MATRA = {
    0x3E: "aa", 0x3F: "i", 0x40: "i", 0x41: "u", 0x42: "u", 0x43: "ri",
    0x46: "e", 0x47: "e", 0x48: "ai", 0x4A: "o", 0x4B: "o", 0x4C: "au",
}
_BRAHMIC_OFFSETS_CONS = {
    0x15: "k", 0x16: "kh", 0x17: "g", 0x18: "gh", 0x19: "ng",
    0x1A: "ch", 0x1B: "chh", 0x1C: "j", 0x1D: "jh", 0x1E: "ny",
    0x1F: "t", 0x20: "th", 0x21: "d", 0x22: "dh", 0x23: "n",
    0x24: "t", 0x25: "th", 0x26: "d", 0x27: "dh", 0x28: "n", 0x29: "n",
    0x2A: "p", 0x2B: "ph", 0x2C: "b", 0x2D: "bh", 0x2E: "m",
    0x2F: "y", 0x30: "r", 0x31: "r", 0x32: "l", 0x33: "l", 0x34: "zh",
    0x35: "v", 0x36: "sh", 0x37: "sh", 0x38: "s", 0x39: "h",
    0x5C: "d", 0x5D: "dh", 0x5F: "y",
}
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

# Native digits across all 8 Indic digit blocks -> ASCII 0-9
_ALL_INDIC_DIGITS: dict[str, str] = {}
for _base in (0x0966, 0x09E6, 0x0A66, 0x0AE6, 0x0BE6, 0x0C66, 0x0CE6, 0x0D66):
    for _d in range(10):
        _ALL_INDIC_DIGITS[chr(_base + _d)] = str(_d)

# ---------------------------------------------------------------------------
# All-India Semantic + Numeral Phrase Rules (Longest-first per concept)
# ---------------------------------------------------------------------------

_INDIC_PHRASE_MAP: list[tuple[str, str]] = [
    # ---- TAMIL (ta-IN) ----
    (r"யாருக்கும்\s*காயமில்லை|யாருக்கும்\s*காயம்\s*இல்லை|யாருக்கும்\s*அடி\s*படல|யாருக்கும்\s*அடிபடல|யாருக்கும்\s*அடி\s*படவில்லை|யாருக்கும்\s*எதுவும்\s*ஆகல", "kisi ko chot nahi lagi safe"),
    (r"காயமில்லை|காயமில்லா|காயம்\s*இல்லை|காயம்\s*இல்ல|காயம்\s*எதுவும்\s*இல்லை|காயம்\s*ஏதும்\s*இல்லை|அடி\s*படல|அடிபடல|அடிபடவில்லை|அடி\s*படவில்லை|அடி\s*எதுவும்\s*படவில்லை", "chot nahi lagi safe"),
    (r"பாதுகாப்பாக\s*இருக்கோம்|பாதுகாப்பா\s*இருக்கோம்|பத்திரமா\s*இருக்கோம்|நல்லா\s*இருக்கோம்|எல்லாரும்\s*நல்லா|சேஃபா\s*இருக்கோம்|பாதுகாப்பு", "sab safe theek hai"),
    (r"ஆம்புலன்ஸ்\s*வேண்டாம்", "ambulance nahi"),
    (r"இரத்தம்\s*வருது|ரத்தம்\s*வருது|இரத்தம்|ரத்தம்|இரத்தக்\s*காயம்", "khoon bleeding"),
    (r"காயம்பட்ட|காயம்\s*ஏற்பட்டது|காயம்", "chot injury"),
    (r"அடிபட்டு|அடி\s*பட்டு|மயக்கம்|எலும்பு\s*முறிவு|வலி\s*அதிகமா", "injured"),
    (r"ஆம்புலன்ஸ்|மருத்துவமனை|ஆஸ்பத்திரி", "ambulance hospital"),
    (r"ரேடியேட்டர்|ரேடியட்டர்|கூலன்ட்|நீர்\s*கசிவு|தண்ணீர்\s*கசிவு|என்ஜின்\s*சூடாகி|வண்டி\s*சூடாகி|ஆவி\s*வருது|புகை\s*வருது", "radiator"),
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
    (r"தேசிய\s*நெடுஞ்சாலை|நெடுஞ்சாலை|ஹைவேயில்|ஹைவேல|ஹைவே|எக்ஸ்பிரஸ்வே|என்\.?\s*எச்\.?", "highway"),
    (r"கிராமத்து\s*சாலை|கிராம\s*சாலை|கிராமம்|கிராமத்தில்|ஊர்\s*சாலை|ஊர்ல|லிங்க்\s*ரோடு|மண்\s*சாலை", "gaon village rural link road"),
    (r"நகர\s*சாலை|நகரத்தில்|நகரத்துல|நகரம்|பட்டணம்|சிட்டி|சென்னை|மதுரை|கோவை|கோயம்புத்தூர்|திருச்சி|சேலம்|வேலூர்", "city"),
    (r"ஓட்ட\s*முடியாது|ஓட்ட\s*இயலாது|நகர\s*முடியாது|நகர்த்த\s*முடியாது|வண்டி\s*நகரல|ஓட\s*மாட்டேங்குது", "chalne layak nahi"),
    (r"ஸ்டார்ட்\s*ஆகல|ஸ்டார்ட்\s*ஆகவில்லை|ஸ்டார்ட்\s*ஆகமாட்டேங்குது|வண்டி\s*ஓடல|வண்டி\s*ஓடவில்லை", "start nahi"),
    (r"உடைஞ்சிடுச்சு|உடைஞ்சு|உடைந்து|கெட்டுப்\s*போச்சு|பழுதாகி|பிரச்சினை|பிரச்சனை|கோளாறு|வண்டி\s*நின்னுடுச்சு|வண்டி\s*நின்றுவிட்டது|விபத்து|மோதல்", "breakdown crash"),
    (r"பாலிசி\S*\s*எண்\S*|பாலிசி\S*\s*நம்பர்\S*|பாலிசி\S*", "policy number"),
    (r"நில்லுங்க\S*|நிறுத்துங்க\S*|இருங்க\S*|ஒரு\s*நிமிஷம்|தப்பு|தவறு|இல்லை\s*இல்லை|இல்ல\s*இல்ல|மாத்துங்க\S*|மாற்றுங்க\S*", "ruko galat change"),
    (r"ஆமாம்|ஆமா|சரிங்க|சரி|பண்ணுங்க\S*|செய்யுங்க\S*|உறுதி\s*செய்யுங்க\S*|கன்ஃபர்ம்|ஓகே", "haan confirm"),
    (r"இல்லை|இல்ல|வேண்டாம்|கூடாது|படல|கிடையாது", "nahi"),
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

    # ---- TELUGU (te-IN) ----
    (r"ఎవరికీ\s*గాయం\s*లేదు|ఎవరికీ\s*దెబ్బ\s*తగలలేదు|గాయం\s*లేదు|గాయపడలేదు|గాయం\s*కాలేదు|దెబ్బ\s*తగలలేదు", "chot nahi lagi safe"),
    (r"సురక్షితంగా\s*ఉన్నాం|బాగున్నాం|అందరం\s*సురక్షితం|భయం\s*లేదు", "sab safe theek hai"),
    (r"రక్తం\s*వస్తోంది|రక్తం|గాయమైంది|గాయం|దెబ్బ\s*తగిలింది|అపస్మారక|ఆసుపత్రి|అంబులెన్స్", "khoon chot injured"),
    (r"రేడియేటర్|కూలెంట్|నీరు\s*కారుతోంది|పొగ\s*వస్తోంది", "radiator"),
    (r"పంక్చర్|టైర్\s*పగిలింది|గాలి\s*పోయింది", "puncture"),
    (r"బ్యాటరీ", "battery"),
    (r"పెట్రోల్\s*అయిపోయింది|డీజిల్\s*అయిపోయింది|ఇంధనం\s*ఖాళీ", "petrol khatam"),
    (r"బంపర్", "bumper"),
    (r"హెడ్‌లైట్|హెడ్లైట్", "headlight"),
    (r"సైడ్\s*మిర్రర్|అద్దం\s*పగిలింది", "side mirror"),
    (r"యాక్సిల్|చక్రం\s*విరిగింది", "axle"),
    (r"క్లచ్\s*వైర్", "clutch wire"),
    (r"ఫ్రంట్\s*గార్డ్", "front guard"),
    (r"ఈవీ\s*సిస్టమ్", "ev system stuck"),
    (r"జాతీయ\s*రహదారి|రహదారి|హైవేపై|హైవే", "highway"),
    (r"గ్రామీణ\s*రోడ్డు|గ్రామంలో|గ్రామం|పల్లె|లింక్\s*రోడ్డు", "gaon village rural link road"),
    (r"నగరంలో|నగరం|సిటీ|హైదరాబాద్|విజయవాడ|విశాఖపట్నం|తిరుపతి", "city"),
    (r"నడపలేము|నడపడం\s*కుదరదు|కదలడం\s*లేదు", "chalne layak nahi"),
    (r"స్టార్ట్\s*కావడం\s*లేదు|స్టార్ట్\s*అవ్వడం\s*లేదు", "start nahi"),
    (r"విరిగిపోయింది|పాడైపోయింది|సమస్య|ప్రమాదం|యాక్సిడెంట్", "breakdown crash"),
    (r"పాలసీ\s*నంబర్|పాలసీ\s*సంఖ్య|పాలసీ", "policy number"),
    (r"ఆగండి|ఆపండి|తప్పు|మార్చండి|వద్దు\s*వద్దు", "ruko galat change"),
    (r"అవును|సరే|నిర్ధారించండి|చేయండి|ఓకే", "haan confirm"),
    (r"లేదు|వద్దు|కాదు", "nahi"),
    (r"సున్నా", "zero"), (r"వేల|వేయి", "hazaar"), (r"వందల|వంద", "sau"),
    (r"ఒకటి", "ek"), (r"రెండు", "do"), (r"మూడు", "teen"), (r"నాలుగు", "chaar"),
    (r"ఐదు", "paanch"), (r"ఆరు", "che"), (r"ఏడు", "saat"), (r"ఎనిమిది", "aath"),
    (r"తొమ్మిది", "nau"), (r"పది", "das"), (r"ఇరవై", "bees"), (r"ముప్పై", "tees"),
    (r"తొంభై\s*ఒకటి", "ikyaanave"), (r"యాభై\s*ఐదు", "pachpan"), (r"ముప్పై\s*రెండు", "battees"),

    # ---- KANNADA (kn-IN) ----
    (r"ಯಾರಿಗೂ\s*ಗಾಯ\s*ಇಲ್ಲ|ಯಾರಿಗೂ\s*ಗಾಯವಾಗಿಲ್ಲ|ಗಾಯ\s*ಇಲ್ಲ|ಗಾಯವಾಗಿಲ್ಲ|ಪೆಟ್ಟಾಗಿಲ್ಲ", "chot nahi lagi safe"),
    (r"ಸುರಕ್ಷಿತವಾಗಿದ್ದೇವೆ|ಚೆನ್ನಾಗಿದ್ದೇವೆ|ಎಲ್ಲರೂ\s*ಸುರಕ್ಷಿತ", "sab safe theek hai"),
    (r"ರಕ್ತ\s*ಬರುತ್ತಿದೆ|ರಕ್ತ|ಗಾಯವಾಗಿದೆ|ಗಾಯ|ಪೆಟ್ಟು|ಆಸ್ಪತ್ರೆ|ಆಂಬ್ಯುಲೆನ್ಸ್", "khoon chot injured"),
    (r"ರೇಡಿಯೇಟರ್|ಕೂಲಂಟ್|ಹೊಗೆ\s*ಬರುತ್ತಿದೆ", "radiator"),
    (r"ಪಂಕ್ಚರ್|ಟೈರ್\s*ಒಡೆದಿದೆ", "puncture"),
    (r"ಬ್ಯಾಟರಿ", "battery"),
    (r"ಪೆಟ್ರೋಲ್\s*ಖಾಲಿ|ಡೀಸೆಲ್\s*ಖಾಲಿ|ಇಂಧನ\s*ಖಾಲಿ", "petrol khatam"),
    (r"ಬಂಪರ್", "bumper"),
    (r"ಹೆಡ್‌ಲೈಟ್|ಹೆಡ್ಲೈಟ್", "headlight"),
    (r"ಸೈಡ್\s*ಮಿರರ್|ಕನ್ನಡಿ\s*ಒಡೆದಿದೆ", "side mirror"),
    (r"ಆಕ್ಸಲ್|ಚಕ್ರ\s*ಮುರಿತ", "axle"),
    (r"ಕ್ಲಚ್\s*ವೈರ್", "clutch wire"),
    (r"ಫ್ರಂಟ್\s*ಗಾರ್ಡ್", "front guard"),
    (r"ಇವಿ\s*ಸಿಸ್ಟಮ್", "ev system stuck"),
    (r"ರಾಷ್ಟ್ರೀಯ\s*ಹೆದ್ದಾರಿ|ಹೆದ್ದಾರಿ|ಹೈವೇ", "highway"),
    (r"ಗ್ರಾಮೀಣ\s*ರಸ್ತೆ|ಹಳ್ಳಿ|ಗ್ರಾಮ|ಲಿಂಕ್\s*ರೋಡ್", "gaon village rural link road"),
    (r"ನಗರ|ಸಿಟಿ|ಬೆಂಗಳೂರು|ಮೈಸೂರು|ಮಂಗಳೂರು|ಹುಬ್ಬಳ್ಳಿ", "city"),
    (r"ಓಡಿಸಲು\s*ಆಗಲ್ಲ|ಓಡಿಸಲು\s*ಸಾಧ್ಯವಿಲ್ಲ|ಚಲಿಸುತ್ತಿಲ್ಲ", "chalne layak nahi"),
    (r"ಸ್ಟಾರ್ಟ್\s*ಆಗುತ್ತಿಲ್ಲ|ಸ್ಟಾರ್ಟ್\s*ಆಗಲಿಲ್ಲ", "start nahi"),
    (r"ಮುರಿತ|ಕೆಟ್ಟುಹೋಗಿದೆ|ತೊಂದರೆ|ಅಪಘಾತ", "breakdown crash"),
    (r"ಪಾಲಿಸಿ\s*ಸಂಖ್ಯೆ|ಪಾಲಿಸಿ\s*ನಂಬರ್|ಪಾಲಿಸಿ", "policy number"),
    (r"ನಿಲ್ಲಿಸಿ|ತಡೆಯಿರಿ|ತಪ್ಪು|ಬದಲಾಯಿಸಿ", "ruko galat change"),
    (r"ಹೌದು|ಸರಿ|ದೃಢೀಕರಿಸಿ|ಮಾಡಿ|ಓಕೆ", "haan confirm"),
    (r"ಇಲ್ಲ|ಬೇಡ|ಅಲ್ಲ", "nahi"),
    (r"ಸೊನ್ನೆ", "zero"), (r"ಸಾವಿರದ|ಸಾವಿರ", "hazaar"), (r"ನೂರ|ನೂರು", "sau"),
    (r"ಒಂದು", "ek"), (r"ಎರಡು", "do"), (r"ಮೂರು", "teen"), (r"ನಾಲ್ಕು", "chaar"),
    (r"ಐದು", "paanch"), (r"ಆರು", "che"), (r"ಏಳು", "saat"), (r"ಎಂಟು", "aath"),
    (r"ಒಂಬತ್ತು", "nau"), (r"ಹತ್ತು", "das"), (r"ಇಪ್ಪತ್ತು", "bees"), (r"ತೊಂಬತ್ತೊಂದು", "ikyaanave"),

    # ---- MALAYALAM (ml-IN) ----
    (r"ആർക്കും\s*പരിക്കില്ല|പരിക്കില്ല|പരിക്കേറ്റില്ല|മുറിവില്ല|അപകടമില്ല", "chot nahi lagi safe"),
    (r"സുരക്ഷിതരാണ്|സുരക്ഷിതമാണ്|എല്ലാവരും\s*സുഖമായി", "sab safe theek hai"),
    (r"രക്തം\s*വരുന്നു|രക്തം|പരിക്കേറ്റു|പരിക്ക്|ആശുപത്രി|ആംബുലൻസ്", "khoon chot injured"),
    (r"റേഡിയേറ്റർ|കൂളന്റ്|പുക\s*വരുന്നു", "radiator"),
    (r"പഞ്ചർ|ടയർ\s*പൊട്ടി", "puncture"),
    (r"ബാറ്ററി", "battery"),
    (r"പെട്രോൾ\s*തീർന്നു|ഡീസൽ\s*തീർന്നു|ഇന്ധനം\s*തീർന്നു", "petrol khatam"),
    (r"ബമ്പർ", "bumper"),
    (r"ഹെഡ്‌ലൈറ്റ്|ഹെഡ്ലൈറ്റ്", "headlight"),
    (r"സൈഡ്\s*മിറർ", "side mirror"),
    (r"ആക്സിൽ|ചക്രം\s*ഒടിഞ്ഞു", "axle"),
    (r"ക്ലച്ച്\s*വയർ", "clutch wire"),
    (r"ഫ്രണ്ട്\s*ഗാർഡ്", "front guard"),
    (r"ഇവി\s*സിസ്റ്റം", "ev system stuck"),
    (r"ദേശീയപാത|ഹൈവേയിൽ|ഹൈവേ", "highway"),
    (r"ഗ്രാമീണ\s*റോഡ്|ഗ്രാമത്തിൽ|ഗ്രാമം|ലിങ്ക്\s*റോഡ്", "gaon village rural link road"),
    (r"നഗരത്തിൽ|നഗരം|സിറ്റി|കൊച്ചി|തിരുവനന്തപുരം|കോഴിക്കോട്", "city"),
    (r"ഓടിക്കാൻ\s*കഴിയില്ല|ഓടിക്കാൻ\s*പറ്റില്ല|നീങ്ങുന്നില്ല", "chalne layak nahi"),
    (r"സ്റ്റാർട്ട്\s*ആകുന്നില്ല|സ്റ്റാർട്ട്\s*ആയില്ല", "start nahi"),
    (r"തകരാറ്|കേടായി|പ്രശ്നം|അപകടം", "breakdown crash"),
    (r"പോളിസി\s*നമ്പർ|പോളിസി", "policy number"),
    (r"നിൽക്കൂ|നിർത്തൂ|തെറ്റ്|മാറ്റൂ", "ruko galat change"),
    (r"അതെ|ശരി|സ്ഥിരീകരിക്കൂ|ചെയ്യൂ|ഓക്കെ", "haan confirm"),
    (r"ഇല്ല|വേണ്ട|അല്ല", "nahi"),
    (r"പൂജ്യം", "zero"),
    (r"ഏഴായിരത്തി|ഏഴ്\s*ആയിരം", "saat hazaar"), (r"നാലായിരത്തി", "chaar hazaar"),
    (r"മൂവായിരത്തി", "teen hazaar"), (r"ആയിരത്തി|ആയിരം", "hazaar"),
    (r"എണ്ണൂറ്റി|എണ്ണൂറ്", "aath sau"), (r"മുന്നൂറ്റി|മുന്നൂറ്", "teen sau"), (r"നൂറ്റി|നൂറ്", "sau"),
    (r"ഒന്ന്", "ek"), (r"രണ്ട്", "do"), (r"മൂന്ന്", "teen"), (r"നാല്", "chaar"),
    (r"അഞ്ച്", "paanch"), (r"ആറ്", "che"), (r"ഏഴ്", "saat"), (r"എട്ട്", "aath"),
    (r"ഒമ്പത്", "nau"), (r"പത്ത്", "das"), (r"ഇരുപത്", "bees"),

    # ---- BENGALI (bn-IN) ----
    (r"কারও\s*চোট\s*লাগেনি|চোট\s*লাগেনি|ক্ষত\s*নেই|আঘাত\s*লাগেনি|আহত\s*হয়নি", "chot nahi lagi safe"),
    (r"নিরাপদ\s*আছি|ভালো\s*আছি|সবাই\s*নিরাপদ|সবাই\s*সুস্থ", "sab safe theek hai"),
    (r"রক্ত\s*পড়ছে|রক্ত\s*বেরোচ্ছে|রক্ত|চোট\s*লেগেছে|আঘাত|হাসপাতাল|অ্যাম্বুলেন্স", "khoon chot injured"),
    (r"রেডিয়েটর|কুল্যান্ট|ধোঁয়া\s*বেরোচ্ছে", "radiator"),
    (r"পাংচার|টায়ার\s*ফেটে\s*গেছে", "puncture"),
    (r"ব্যাটারি", "battery"),
    (r"পেট্রোল\s*শেষ|ডিজেল\s*শেষ|জ্বালানি\s*শেষ", "petrol khatam"),
    (r"বাম্পার", "bumper"),
    (r"হেডলাইট", "headlight"),
    (r"সাইড\s*মিরর", "side mirror"),
    (r"অ্যাক্সেল", "axle"),
    (r"ক্লাচ\s*ওয়্যার|ক্লাচ\s*তার", "clutch wire"),
    (r"ফ্রন্ট\s*গার্ড", "front guard"),
    (r"ইভি\s*সিস্টেম", "ev system stuck"),
    (r"জাতীয়\s*সড়ক|মহাসড়ক|হাইওয়েতে|হাইওয়ে", "highway"),
    (r"গ্রামের\s*রাস্তা|গ্রামে|গ্রাম|লিংক\s*রোড", "gaon village rural link road"),
    (r"শহরে|শহর|কলকাতা|হাওড়া|শিলিগুড়ি|দুর্গাপুর", "city"),
    (r"চালানো\s*যাবে\s*না|গাড়ি\s*চলছে\s*না", "chalne layak nahi"),
    (r"স্টার্ট\s*হচ্ছে\s*না|স্টার্ট\s*নিচ্ছে\s*না", "start nahi"),
    (r"ভেঙে\s*গেছে|খারাপ\s*হয়ে\s*গেছে|সমস্যা|দুর্ঘটনা", "breakdown crash"),
    (r"পলিসি\s*নম্বর|পলিসি", "policy number"),
    (r"থামুন|দাঁড়ান|ভুল|বদলান", "ruko galat change"),
    (r"হ্যাঁ|ঠিক\s*আছে|নিশ্চিত\s*করুন|করুন|ওকে", "haan confirm"),
    (r"নেই|না|নয়|দরকার\s*নেই", "nahi"),
    (r"শূন্য", "zero"), (r"আটশো", "aath sau"), (r"চারশো", "chaar sau"), (r"তিনশো", "teen sau"), (r"নয়শো", "nau sau"),
    (r"হাজার", "hazaar"), (r"শো|শত", "sau"),
    (r"এক", "ek"), (r"দুই", "do"), (r"তিন", "teen"), (r"চার", "chaar"),
    (r"পাঁচ", "paanch"), (r"ছয়", "che"), (r"সাত", "saat"), (r"আট", "aath"),
    (r"নয়", "nau"), (r"দশ", "das"), (r"বিশ|কুড়ি", "bees"),

    # ---- MARATHI (mr-IN) ----
    (r"कोणालाही\s*जखम\s*नाही|जखम\s*नाही|दुखापत\s*नाही|इजा\s*नाही|लागलं\s*नाही|लागले\s*नाही", "chot nahi lagi safe"),
    (r"सुरक्षित\s*आहोत|सर्व\s*ठीक\s*आहे|आम्ही\s*सुरक्षित", "sab safe theek hai"),
    (r"रक्त\s*येतंय|रक्तस्त्राव|रक्त|जखम\s*झाली|दुखापत\s*झाली|इस्पितळ|रुग्णालय|रुग्णवाहिका", "khoon chot injured"),
    (r"रेडिएटर|कुलंट|धूर\s*येतोय", "radiator"),
    (r"पंक्चर|टायर\s*फुटले", "puncture"),
    (r"बॅटरी", "battery"),
    (r"पेट्रोल\s*संपलं|पेट्रोल\s*संपले|डिझेल\s*संपले|इंधन\s*संपले", "petrol khatam"),
    (r"बंपर", "bumper"),
    (r"हेडलाईट", "headlight"),
    (r"साईड\s*मिरर", "side mirror"),
    (r"ॲक्सल|चाक\s*तुटले", "axle"),
    (r"क्लच\s*वायर", "clutch wire"),
    (r"फ्रंट\s*गार्ड", "front guard"),
    (r"ईव्ही\s*सिस्टीम", "ev system stuck"),
    (r"राष्ट्रीय\s*महामार्ग|महामार्गावर|महामार्ग|हायवेवर|हायवे", "highway"),
    (r"गावाकडच्या\s*रस्त्यावर|गावाकडचा\s*रस्ता|गावात|गाव|लिंक\s*रोड", "gaon village rural link road"),
    (r"शहरात|शहर|पुणे|मुंबई|नागपूर|नाशिक", "city"),
    (r"चालवता\s*येत\s*नाही|चालू\s*शकत\s*नाही", "chalne layak nahi"),
    (r"स्टार्ट\s*होत\s*नाही|सुरू\s*होत\s*नाही", "start nahi"),
    (r"तुटलं|तुटले|बिघाड|खराब\s*झाली|अपघात", "breakdown crash"),
    (r"पॉलिसी\s*क्रमांक|पॉलिसी\s*नंबर|पॉलिसी", "policy number"),
    (r"थांबा|चुकलं|चुकले|बदला", "ruko galat change"),
    (r"होय|हो|ठीक\s*आहे|निश्चित\s*करा|करा|ओके", "haan confirm"),
    (r"नाही|नको", "nahi"),
    (r"शून्य", "zero"), (r"आठशे", "aath sau"), (r"चारशे", "chaar sau"), (r"तीनशे", "teen sau"), (r"नऊशे", "nau sau"),
    (r"हजार", "hazaar"), (r"शे|शंभर", "sau"),
    (r"दोन", "do"), (r"तीन", "teen"), (r"चार", "chaar"), (r"पाच", "paanch"),
    (r"सहा", "che"), (r"सात", "saat"), (r"आठ", "aath"), (r"नऊ", "nau"), (r"दहा", "das"), (r"वीस", "bees"),

    # ---- GUJARATI (gu-IN) ----
    (r"કોઈને\s*ઈજા\s*નથી|ઈજા\s*નથી|વાગ્યું\s*નથી|નુકસાન\s*નથી", "chot nahi lagi safe"),
    (r"સલામત\s*છીએ|સુરક્ષિત\s*છીએ|બધા\s*ઠીક\s*છે", "sab safe theek hai"),
    (r"લોહી\s*નીકળે\s*છે|લોહી|ઈજા\s*થઈ|હોસ્પિટલ|એમ્બ્યુલન્સ", "khoon chot injured"),
    (r"રેડિએટર|કૂલન્ટ|ધુમાડો\s*નીકળે", "radiator"),
    (r"પંચર|ટાયર\s*ફાટ્યું", "puncture"),
    (r"બેટરી", "battery"),
    (r"પેટ્રોલ\s*પૂરું|ડીઝલ\s*પૂરું|પેટ્રોલ\s*ખૂટ્યું", "petrol khatam"),
    (r"બમ્પર", "bumper"),
    (r"હેડલાઇટ", "headlight"),
    (r"સાઇડ\s*મિરર", "side mirror"),
    (r"એક્સલ", "axle"),
    (r"ક્લચ\s*વાયર", "clutch wire"),
    (r"ફ્રન્ટ\s*ગાર્ડ", "front guard"),
    (r"ઇવી\s*સિસ્ટમ", "ev system stuck"),
    (r"નેશનલ\s*હાઇવે|હાઇવે\s*પર|હાઇવે", "highway"),
    (r"ગામડાનો\s*રસ્તો|ગામમાં|ગામ|લિંક\s*રોડ", "gaon village rural link road"),
    (r"શહેરમાં|શહેર|અમદાવાદ|સુરત|વડોદરા|રાજકોટ", "city"),
    (r"ચાલે\s*તેમ\s*નથી|ચલાવી\s*શકાય\s*નહીં", "chalne layak nahi"),
    (r"સ્ટાર્ટ\s*થતી\s*નથી|ચાલુ\s*થતી\s*નથી", "start nahi"),
    (r"તૂટી\s*ગયું|ખરાબ\s*થઈ|તકલીફ|અકસ્માત", "breakdown crash"),
    (r"પોલિસી\s*નંબર|પોલિસી", "policy number"),
    (r"રોકો|થોભો|ખોટું|બદલો", "ruko galat change"),
    (r"હા|બરાબર|પાકું\s*કરો|કરો|ઓકે", "haan confirm"),
    (r"નથી|ના", "nahi"),
    (r"શૂન્ય", "zero"), (r"હજાર", "hazaar"), (r"સો", "sau"),
    (r"એક", "ek"), (r"બે", "do"), (r"ત્રણ", "teen"), (r"ચાર", "chaar"),
    (r"પાંચ", "paanch"), (r"છ", "che"), (r"સાત", "saat"), (r"આઠ", "aath"),
    (r"નવ", "nau"), (r"દસ", "das"), (r"વીસ", "bees"),

    # ---- PUNJABI (pa-IN) ----
    (r"ਕਿਸੇ\s*ਨੂੰ\s*ਸੱਟ\s*ਨਹੀਂ|ਸੱਟ\s*ਨਹੀਂ|ਜ਼ਖ਼ਮ\s*ਨਹੀਂ|ਚੋਟ\s*ਨਹੀਂ", "chot nahi lagi safe"),
    (r"ਸੁਰੱਖਿਅਤ\s*ਹਾਂ|ਸਭ\s*ਠੀਕ\s*ਹੈ|ਅਸੀਂ\s*ਠੀਕ\s*ਹਾਂ", "sab safe theek hai"),
    (r"ਖ਼ੂਨ\s*ਵਗ\s*ਰਿਹਾ|ਖ਼ੂਨ|ਖੂਨ|ਸੱਟ\s*ਲੱਗੀ|ਜ਼ਖ਼ਮ|ਹਸਪਤਾਲ|ਐਂਬੂਲੈਂਸ", "khoon chot injured"),
    (r"ਰੇਡੀਏਟਰ|ਕੂਲੈਂਟ|ਧੂੰਆਂ\s*ਨਿਕਲ", "radiator"),
    (r"ਪੰਕਚਰ|ਟਾਇਰ\s*ਫਟ", "puncture"),
    (r"ਬੈਟਰੀ", "battery"),
    (r"ਤੇਲ\s*ਮੁੱਕ\s*ਗਿਆ|ਪੈਟਰੋਲ\s*ਮੁੱਕ\s*ਗਿਆ|ਡੀਜ਼ਲ\s*ਮੁੱਕ", "petrol khatam"),
    (r"ਬੰਪਰ", "bumper"),
    (r"ਹੈੱਡਲਾਈਟ|ਹੈਡਲਾਈਟ", "headlight"),
    (r"ਸਾਈਡ\s*ਮਿਰਰ", "side mirror"),
    (r"ਐਕਸਲ", "axle"),
    (r"ਕਲੱਚ\s*ਵਾਇਰ|ਕਲੱਚ\s*ਤਾਰ", "clutch wire"),
    (r"ਫਰੰਟ\s*ਗਾਰਡ", "front guard"),
    (r"ਈਵੀ\s*ਸਿਸਟਮ", "ev system stuck"),
    (r"ਨੈਸ਼ਨਲ\s*ਹਾਈਵੇ|ਹਾਈਵੇ\s*'ਤੇ|ਹਾਈਵੇ", "highway"),
    (r"ਪਿੰਡ\s*ਦੀ\s*ਸੜਕ|ਪਿੰਡ\s*ਵਿੱਚ|ਪਿੰਡ|ਲਿੰਕ\s*ਰੋਡ", "gaon village rural link road"),
    (r"ਸ਼ਹਿਰ\s*ਵਿੱਚ|ਸ਼ਹਿਰ|ਲੁਧਿਆਣਾ|ਅੰਮ੍ਰਿਤਸਰ|ਜਲੰਧਰ|ਚੰਡੀਗੜ੍ਹ|ਪਟਿਆਲਾ", "city"),
    (r"ਚੱਲ\s*ਨਹੀਂ\s*ਸਕਦੀ|ਚਲਾ\s*ਨਹੀਂ\s*ਸਕਦੇ", "chalne layak nahi"),
    (r"ਸਟਾਰਟ\s*ਨਹੀਂ\s*ਹੋ\s*ਰਹੀ", "start nahi"),
    (r"ਟੁੱਟ\s*ਗਿਆ|ਖਰਾਬ\s*ਹੋ\s*ਗਈ|ਦਿੱਕਤ|ਹਾਦਸਾ|ਐਕਸੀਡੈਂਟ", "breakdown crash"),
    (r"ਪਾਲਿसी\s*ਨੰਬਰ|ਪਾਲਿਸੀ\s*ਨੰਬਰ|ਪਾਲਿਸੀ", "policy number"),
    (r"ਰੁਕੋ|ਗਲਤ|ਬਦਲੋ", "ruko galat change"),
    (r"ਹਾਂਜੀ|ਹਾਂ|ਠੀਕ\s*ਹੈ|ਪੱਕਾ\s*ਕਰੋ|ਕਰੋ|ਓਕੇ", "haan confirm"),
    (r"ਨਹੀਂ|ਨਾ", "nahi"),
    (r"ਜ਼ੀਰੋ|ਸਿਫ਼ਰ", "zero"), (r"ਹਜ਼ਾਰ|ਹਜਾਰ", "hazaar"), (r"ਸੌ", "sau"),
    (r"ਇੱਕ|ਇਕ", "ek"), (r"ਦੋ", "do"), (r"ਤਿੰਨ", "teen"), (r"ਚਾਰ", "chaar"),
    (r"ਪੰਜ", "paanch"), (r"ਛੇ", "che"), (r"ਸੱਤ", "saat"), (r"ਅੱਠ", "aath"),
    (r"ਨੌਂ|ਨੌ", "nau"), (r"ਦਸ", "das"), (r"ਵੀਹ", "bees"),
]

_COMPILED_INDIC_MAP = [(re.compile(pat, re.I), repl) for pat, repl in _INDIC_PHRASE_MAP]

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


def detect_script_language(text: str) -> Optional[str]:
    """Detect caller language from transcript text across all 10 Gnani languages."""
    t = text or ""
    if _TAMIL_RE.search(t):
        return "ta-IN"
    if _TELUGU_RE.search(t):
        return "te-IN"
    if _KANNADA_RE.search(t):
        return "kn-IN"
    if _MALAYALAM_RE.search(t):
        return "ml-IN"
    if _BENGALI_RE.search(t):
        return "bn-IN"
    if _GUJARATI_RE.search(t):
        return "gu-IN"
    if _GURMUKHI_RE.search(t):
        return "pa-IN"
    if _DEV_RE.search(t):
        if _MARATHI_CHARS_RE.search(t) or _MARATHI_CUES_RE.search(t):
            return "mr-IN"
        return "hi-IN"
    if _TANGLISH_CUES_RE.search(t):
        return "ta-IN"
    if _ENGLISH_SENTENCE_RE.search(t) and not _HINGLISH_EXCL_RE.search(t):
        return "en-IN"
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
                if j < n and word[j] == "़":
                    base = _NUKTA.get(ch, base)
                    j += 1
            if j < n and word[j] == "्":
                out.append(base)
                i = j + 1
                continue
            if j < n and word[j] in _MATRAS:
                out.append(base + _MATRAS[word[j]])
                i = j + 1
                continue
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
        if ch in _ALL_INDIC_DIGITS:
            out.append(_ALL_INDIC_DIGITS[ch])
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


def _brahmic_word_to_latin(word: str) -> str:
    """Phonetic transliteration for Bengali, Gurmukhi, Gujarati, Telugu, Kannada, Malayalam."""
    out: list[str] = []
    i, n = 0, len(word)
    while i < n:
        ch = word[i]
        if ch in _ALL_INDIC_DIGITS:
            out.append(_ALL_INDIC_DIGITS[ch])
            i += 1
            continue
        cp = ord(ch)
        base_blk = next((b for b in _BRAHMIC_BASES if b <= cp < b + 0x80), None)
        if base_blk is None:
            out.append(ch)
            i += 1
            continue
        off = cp - base_blk
        if off in _BRAHMIC_OFFSETS_CONS:
            cons = _BRAHMIC_OFFSETS_CONS[off]
            j = i + 1
            if j < n and (ord(word[j]) - base_blk) in (0x3C,):  # nukta
                j += 1
            if j < n and (ord(word[j]) - base_blk) == 0x4D:   # virama
                out.append(cons)
                i = j + 1
                continue
            if j < n and (ord(word[j]) - base_blk) in _BRAHMIC_OFFSETS_MATRA:
                out.append(cons + _BRAHMIC_OFFSETS_MATRA[ord(word[j]) - base_blk])
                i = j + 1
                continue
            out.append(cons + ("" if j >= n else "a"))
            i = j
            continue
        if off in _BRAHMIC_OFFSETS_VOWEL:
            out.append(_BRAHMIC_OFFSETS_VOWEL[off])
            i += 1
            continue
        if off in _BRAHMIC_OFFSETS_MATRA:
            out.append(_BRAHMIC_OFFSETS_MATRA[off])
            i += 1
            continue
        if off in (0x01, 0x02, 0x03, 0x70, 0x71):  # anusvara/chandrabindu/visarga/tippi/addak
            out.append("n" if off in (0x01, 0x02, 0x70) else "")
            i += 1
            continue
        i += 1
    return "".join(out)


def _indic_to_canonical(text: str) -> str:
    """Translate native regional phrases/numbers + transliterate remaining Indic runs."""
    if not text:
        return ""
    work = text
    for td, d in _ALL_INDIC_DIGITS.items():
        if td in work:
            work = work.replace(td, d)
    # Normalize regional NH abbreviations ("என்.எச்-44" / "एन.एच-44" etc.)
    work = re.sub(r"என்\.?\s*எச்\.?\s*[-\s]?(\d+)", r" nh-\1 ", work)
    work = re.sub(r"एन\.?\s*एच\.?\s*[-\s]?(\d+)", r" nh-\1 ", work)
    for rx, repl in _COMPILED_INDIC_MAP:
        work = rx.sub(lambda m: f" {repl} ", work)
    if _TAMIL_RE.search(work):
        work = _TAMIL_RE.sub(lambda m: " " + _tamil_word_to_latin(m.group(0)) + " ", work)
    if _NON_DEV_INDIC_RE.search(work):
        work = _NON_DEV_INDIC_RE.sub(lambda m: " " + _brahmic_word_to_latin(m.group(0)) + " ", work)
    if _DEV_RE.search(work):
        work = _DEV_RE.sub(lambda m: " " + _word_to_hinglish(m.group(0)) + " ", work)
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
    Keeps Latin words (radiator, highway, policy) & clause breaks intact."""
    t = text or ""
    if not t:
        return ""
    if _NON_DEV_INDIC_RE.search(t):
        return _apply_tanglish(_indic_to_canonical(t))
    if _DEV_RE.search(t):
        # Apply Marathi/Indic phrase rules on Devanagari too, while keeping raw+to_hinglish compatibility
        if _MARATHI_CHARS_RE.search(t) or _MARATHI_CUES_RE.search(t):
            return _indic_to_canonical(t)
        h = to_hinglish(t)
        return f"{t} {h}".strip() if h and h != t else t
    if _TANGLISH_CUES_RE.search(t):
        return _apply_tanglish(t)
    return t
