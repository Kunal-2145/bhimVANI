"""
BimaVani — Domain Rule Engine: Injury Triage • Smart RSA Dispatch • IRDAI Claim Math
==================================================================================
Three domain tools called by backend/pipeline/brain.py:

    Tool 1: triage_injury_and_safety(transcript)
    Tool 2: dispatch_smart_rsa(road_type, parts, is_drivable, policy)
    Tool 3: calculate_irdai_claim_draft(policy_number, parts)

Plus deterministic Hinglish entity extraction helpers (spoken Hindi numeral
parser, road-type detection, part detection, policy number / confirmation
detection). Zero hallucination: every number spoken by the agent is computed
from backend/data/synthetic_db.json.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.i18n import (
    STRINGS,
    compose_localized_reply,
    format_range,
    localize_garage,
    normalize_lang_code,
    spoken_part_localized,
)
from backend.translit import normalize_for_matching

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "synthetic_db.json")

with open(_DB_PATH, "r", encoding="utf-8") as _fh:
    DB: Dict[str, Any] = json.load(_fh)

POLICIES: Dict[str, Dict[str, Any]] = DB["policies"]
PARTS: Dict[str, Dict[str, Any]] = DB["parts_catalog"]
GARAGES: List[Dict[str, Any]] = DB["cashless_garages"]
EMERGENCY: Dict[str, Any] = DB["emergency"]
IRDAI: Dict[str, Any] = DB["irdai_rules"]
WORKFLOWS: Dict[str, Dict[str, Any]] = DB["rsa_workflows"]

# ===========================================================================
# Spoken Hindi / Hinglish numeral parser ("saat hazaar aath sau bees" -> 7820)
# ===========================================================================

# Single digits ONLY (for digit-by-digit runs like "seven eight two zero" and
# multiplier before "hazaar" / "sau"). Note: "o" and "to" are intentionally
# excluded because common Hinglish words ("o bhai", "ek to") would corrupt runs.
_DIGIT_WORDS: Dict[str, str] = {
    "zero": "0", "shunya": "0", "sunya": "0", "sifar": "0",
    "one": "1", "ek": "1", "onay": "1",
    "two": "2", "do": "2", "dou": "2", "doo": "2",
    "three": "3", "teen": "3", "tin": "3", "tina": "3",
    "four": "4", "char": "4", "chaar": "4", "fourr": "4", "chara": "4",
    "five": "5", "paanch": "5", "panch": "5", "pach": "5",
    "six": "6", "che": "6", "chhe": "6", "chah": "6", "chay": "6",
    "seven": "7", "saat": "7", "sat": "7", "saath": "7", "satt": "7",
    "eight": "8", "aath": "8", "aat": "8", "ath": "8",
    "nine": "9", "nau": "9", "nov": "9", "nauy": "9",
}

_MAG_WORDS: Dict[str, int] = {
    "hazaar": 1000, "hajar": 1000, "hazār": 1000, "hajaar": 1000, "thousand": 1000,
    "sau": 100, "hundred": 100,
}

# Full 10..99 Hindi/Hinglish compound number words (including common spelling variants)
_TENS_WORDS: Dict[str, int] = {
    # 10-19
    "das": 10, "ten": 10,
    "gyaarah": 11, "gyarah": 11, "eleven": 11,
    "baarah": 12, "barah": 12, "twelve": 12,
    "terah": 13, "thirteen": 13,
    "chaudah": 14, "chaudahh": 14, "fourteen": 14,
    "pandrah": 15, "pandra": 15, "fifteen": 15,
    "solah": 16, "sixteen": 16,
    "satrah": 17, "seventeen": 17,
    "atharah": 18, "athaara": 18, "eighteen": 18,
    "unnees": 19, "unnis": 19, "nineteen": 19,
    # 20-29
    "bees": 20, "bis": 20, "twenty": 20,
    "ikkees": 21, "ikkis": 21, "baees": 22, "bais": 22, "baais": 22,
    "teees": 23, "teis": 23, "chaubees": 24, "chaubis": 24,
    "pachchees": 25, "pachchis": 25, "pachis": 25,
    "chhabbees": 26, "chhabbis": 26, "sattaees": 27, "sattais": 27,
    "atthaees": 28, "atthais": 28, "untees": 29, "untis": 29,
    # 30-39
    "tees": 30, "tis": 30, "thirty": 30,
    "ikattees": 31, "ikattis": 31, "battees": 32, "battis": 32,
    "taintees": 33, "taintis": 33, "chauntees": 34, "chauntis": 34,
    "paintees": 35, "paintis": 35, "chhattees": 36, "chhattis": 36,
    "saintees": 37, "saintis": 37, "adtees": 38, "adtis": 38,
    "untaalees": 39, "untalis": 39,
    # 40-49
    "chalis": 40, "chaalis": 40, "chealis": 40, "forty": 40,
    "iktaalis": 41, "iktalis": 41, "ikyaalis": 41, "ikyatlis": 41,
    "bayaalis": 42, "bayalis": 42, "taintaalis": 43, "taintalis": 43,
    "chawaalis": 44, "chawalis": 44, "paintaalis": 45, "paintalis": 45,
    "chhiyaalis": 46, "chhiyalis": 46, "saintaalis": 47, "saintalis": 47,
    "adtaalis": 48, "adtalis": 48,
    "unachas": 49, "unanchas": 49, "unachaas": 49, "unachasa": 49, "unchaas": 49,
    # 50-59
    "pachas": 50, "pachaas": 50, "panchaas": 50, "fifty": 50,
    "ikyaavan": 51, "ikyavan": 51, "baavan": 52, "bavan": 52,
    "tirpan": 53, "chawan": 54, "chawvan": 54, "chatween": 54,
    "pachpan": 55, "chavan": 55, "chhappan": 56,
    "sattaavan": 57, "sattavan": 57, "atthaavan": 58, "atthavan": 58,
    "unsath": 59,
    # 60-69
    "saathh": 60, "saatth": 60, "saasat": 60, "sasat": 60, "asath": 60, "sixty": 60,
    "iksath": 61, "baasath": 62, "basath": 62, "tirsath": 63,
    "chausath": 64, "painsath": 65, "chhiyaasath": 66, "chhiyasath": 66,
    "sadsath": 67, "adsath": 68, "unhattar": 69, "unhatar": 69,
    # 70-79
    "sattar": 70, "asarh": 70, "seventy": 70,
    "ikhattar": 71, "bahattar": 72, "tihattar": 73, "chauhattar": 74,
    "pachhattar": 75, "chhihattar": 76, "satthattar": 77, "sathattar": 77,
    "athahattar": 78, "athhattar": 78, "unaasi": 79,
    # 80-89
    "assi": 80, "eighty": 80,
    "ikyaasi": 81, "ikyasi": 81, "bayaasi": 82, "bayasi": 82,
    "tiraasi": 83, "tirasi": 83, "chauraasi": 84, "chaurasi": 84,
    "pachaasi": 85, "pachasi": 85, "chhiyaasi": 86, "chhiyasi": 86,
    "sataasi": 87, "satasi": 87, "atthaasi": 88, "athasi": 88,
    "nawaasi": 89, "nawasi": 89, "unayanave": 89,
    # 90-99
    "nabbe": 90, "navagate": 90, "ninety": 90,
    "ikyaanave": 91, "ikyaanve": 91, "ekyaanave": 91, "ekyaanve": 91, "ekyaanavay": 91,
    "baanave": 92, "baanve": 92, "barevanv": 92,
    "tiraanave": 93, "tiraanve": 93, "tiravanve": 93,
    "chauraanave": 94, "chauraanve": 94, "pachaanave": 95, "pachaanve": 95,
    "chhiyaanave": 96, "chhiyaanve": 96, "sattaanave": 97, "sattaanve": 97,
    "atthaanave": 98, "atthaanve": 98,
    "ninyanave": 99, "ninyaanve": 99, "ninyanavay": 99, "ninyanaveen": 99, "ninyanavis": 99,
}

_POLICY_HINT = re.compile(r"\b(p[- ]?o[- ]?l[- ]?i[- ]?c[- ]?y|paulisi|policy no|number)\b", re.IGNORECASE)
_RAW_NUM = re.compile(r"\b(\d{4,5})\b")
_NON_POLICY_PREFIX = re.compile(r"(?:nh[-\s]?|highway\s*|rs\.?\s*|₹\s*)$", re.IGNORECASE)
_NON_POLICY_SUFFIX = re.compile(r"^\s*(?:rupaye|rupees|rs\.?|inr|km|kilomete?r|mins?|minutes?|hours?|ghante)\b", re.IGNORECASE)


def parse_spoken_numbers(text: str) -> List[int]:
    """Public helper: ordered unique integer candidates from the transcript."""
    return [int(v) for v, _neg in _parse_number_strings_detailed(text)]


def _parse_numbers_detailed(text: str) -> List[Tuple[int, bool]]:
    """Backwards-compatible integer view of candidate numbers + negation flags."""
    return [(int(v), neg) for v, neg in _parse_number_strings_detailed(text)]


def _parse_number_strings_detailed(text: str) -> List[Tuple[str, bool]]:
    """Extract (number_str, is_negated) candidates preserving leading zeros.

    Handles:
      - raw "7820" / "0782"
      - digit-by-digit "saat aath do zero" / "zero saat aath do"
      - magnitude forms "ek hazaar pachpan" (1055), "saat hazaar aath sau bees" (7820)
      - compound Hindi tens pairs like "chawaalis ikyaanave" (4491)
    Tracks true character offsets for accurate ordering and negation detection.
    """
    t = normalize_for_matching(text or "").lower().strip()
    # (str_val, char_start, char_end)
    found: List[Tuple[str, int, int]] = []

    for m in _RAW_NUM.finditer(t):
        s, e = m.start(), m.end()
        if _NON_POLICY_PREFIX.search(t[:s]) or _NON_POLICY_SUFFIX.search(t[e:]):
            continue
        found.append((m.group(1), s, e))

    # Word tokens with true character spans
    word_matches = list(re.finditer(r"[a-zāīū]+", t))
    words = [m.group(0) for m in word_matches]

    # 1. Digit-by-digit runs of spoken digits (keeping leading zeros e.g. "0782")
    i = 0
    while i < len(words):
        if words[i] in _DIGIT_WORDS:
            digits = ""
            j = i
            while j < len(words) and words[j] in _DIGIT_WORDS:
                digits += _DIGIT_WORDS[words[j]]
                j += 1
            if 3 <= len(digits) <= 5:
                s_char = word_matches[i].start()
                e_char = word_matches[j - 1].end()
                found.append((digits, s_char, e_char))
            i = j
            continue
        i += 1

    # 2. Adjacent two-digit Hindi compound pairs (e.g. "chawaalis ikyaanave" -> "4491")
    for i in range(len(words) - 1):
        w1, w2 = words[i], words[i + 1]
        if w1 in _TENS_WORDS and w2 in _TENS_WORDS and 10 <= _TENS_WORDS[w1] <= 99 and 10 <= _TENS_WORDS[w2] <= 99:
            s_char = word_matches[i].start()
            e_char = word_matches[i + 1].end()
            found.append((f"{_TENS_WORDS[w1]:02d}{_TENS_WORDS[w2]:02d}", s_char, e_char))

    # 3. Magnitude form: "<digit> hazaar [<d> sau] [<tens|d>]"
    for m in re.finditer(r"([a-zāīū]+)\s+(?:hazaar|hajar|hazār|hajaar|thousand)\b", t):
        hi = _DIGIT_WORDS.get(m.group(1))
        if not hi:
            continue
        val = int(hi) * 1000
        end_pos = m.end()
        trail_matches = list(re.finditer(r"[a-zāīū]+", t[m.end():]))
        k = 0
        while k < len(trail_matches):
            w = trail_matches[k].group(0)
            if w in _DIGIT_WORDS:
                nxt = trail_matches[k + 1].group(0) if k + 1 < len(trail_matches) else ""
                if nxt in _MAG_WORDS and _MAG_WORDS[nxt] == 100:
                    val += int(_DIGIT_WORDS[w]) * 100
                    end_pos = m.end() + trail_matches[k + 1].end()
                    k += 2
                    continue
                val += int(_DIGIT_WORDS[w])
                end_pos = m.end() + trail_matches[k].end()
                k += 1
            elif w in _TENS_WORDS:
                val += _TENS_WORDS[w]
                end_pos = m.end() + trail_matches[k].end()
                k += 1
            else:
                break
        if _NON_POLICY_SUFFIX.search(t[end_pos:]):
            continue
        found.append((str(val), m.start(), end_pos))

    out: List[Tuple[str, bool]] = []
    seen = set()
    # Sort by start offset; for identical starts prefer longest match (magnitude beats partial digit run)
    for val_str, s, e in sorted(found, key=lambda x: (x[1], -(x[2] - x[1]))):
        try:
            int_val = int(val_str)
        except ValueError:
            continue
        if val_str in seen or not (100 <= int_val <= 99999):
            continue
        after = t[e:e + 20]
        neg = bool(re.search(r"^\W*(?:nahi|nahin|na|wrong|galat|null|koni|இல்ல|இல்லை|வேண்டாம்|தப்பு|தவறு)\b", after))
        out.append((val_str, neg))
        seen.add(val_str)
    return out


# ===========================================================================
# Deterministic Hinglish entity extraction helpers (used by brain.py)
# ===========================================================================

def extract_policy_number(
    transcript: str,
    expecting_policy: bool = False,
) -> Optional[str]:
    """Return the best 4-digit policy code mentioned, else None.

    Prefers values adjacent to "policy/number"; falls back to known policy IDs,
    or any non-negated 4-digit candidate when `expecting_policy=True` (ST_POLICY).
    """
    t = normalize_for_matching(transcript or "")
    nums = _parse_number_strings_detailed(t)
    if not nums:
        return None
    low = t.lower()
    known = set(POLICIES.keys())
    live = [v for v, neg in nums if not neg]
    if not live:
        return None
    if _POLICY_HINT.search(low):
        return live[-1]
    for n in live:
        if n in known:
            return n
    if expecting_policy:
        for n in live:
            if len(n) == 4 or 100 <= int(n) <= 9999:
                return n
    return None


def _keyword_negated_in_text(t: str, start: int, end: int) -> bool:
    """True if a road/part span is directly negated before a clause break or breakdown verb."""
    after = t[end: min(len(t), end + 34)]
    before = t[max(0, start - 24): start]
    # Stop at punctuation or active breakdown verbs so "radiator phat gaya start nahi ho rahi"
    # never treats "start nahi" as negating "radiator".
    clause_after = re.split(r"[,.;!?।—]", after, maxsplit=1)[0]
    if re.search(
        r"^\s*(?:(?:(?!(?:phat|toot|tut|band|start|chal|gay[ai]|hua|hui|lag|aa|raha|rahi)\b)[a-z]+)\s+){0,3}"
        r"(?:nahi|nahin|na|naa|koni|mat|not)\b",
        clause_after,
    ):
        return True
    if re.search(r"\b(?:no|not|without|bina|binaa|nahi|nahin)\s+(?:any\s+)?$", before):
        return True
    return False


def extract_road_type(transcript: str, policy: Optional[Dict[str, Any]] = None) -> str:
    """One of NH | CITY | RURAL (NH wins on unnegated ties — safer default for RSA)."""
    _ = policy
    t = normalize_for_matching(transcript or "").lower()

    nh_patterns = [
        r"\bnh[-\s]?\d+\b",
        r"\bnh\b",
        r"\bnational highway\b",
        r"\bhighway\b",
        r"\bhaaiway\b",
        r"\bhaaive\b",
        r"\bhaiwe\b",
        r"\bexpressway\b",
        r"\bexpresway\b",
        r"\bgt road\b",
        r"\bfast lane\b",
        r"\bkmp\b",
        r"\bring road\b",
        r"\bd[ií]vai? road\b",
    ]
    rural_patterns = [
        r"\blink road\b",
        r"\brural\b",
        r"\bga[ao]nv?\b",
        r"\bgaav\b",
        r"\bvillage road\b",
        r"\bvillage\b",
        r"\bkaccha\b",
        r"\bkachcha\b",
        r"\binterior\b",
        r"\bcountryside\b",
        r"\bcorner ka rasta\b",
        r"\blocal road\b",
        r"\btappal\b",
    ]

    for pat in nh_patterns:
        for m in re.finditer(pat, t):
            if not _keyword_negated_in_text(t, m.start(), m.end()):
                return "NH"
    for pat in rural_patterns:
        for m in re.finditer(pat, t):
            if not _keyword_negated_in_text(t, m.start(), m.end()):
                return "RURAL"
    return "CITY"


def _build_part_pattern(kw: str) -> re.Pattern[str]:
    """Match catalog keywords safely:
      - multi-word phrases and short tokens (<=4 chars) require BOTH boundaries
        so 'ev' never hits 'even' and 'air' never hits 'airbag'
      - single words >=5 chars allow word-start prefix (e.g. 'punctur' -> 'puncture')
      - common English 4-char stems (leak/dent/fuel) allow inflectional suffixes
    """
    k = kw.lower().strip()
    if k in {"leak", "dent"}:
        return re.compile(r"(?<![a-z])" + re.escape(k) + r"(?:s|ed|ing|y)?(?![a-z])")
    if " " in k or len(k) <= 4:
        return re.compile(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])")
    return re.compile(r"(?<![a-z])" + re.escape(k))


_COMPILED_PART_KWS: Dict[str, List[re.Pattern[str]]] = {
    key: [_build_part_pattern(k) for k in (meta.get("keywords_en", []) + meta.get("keywords_hi", []))]
    for key, meta in PARTS.items()
}


def extract_parts(transcript: str) -> List[str]:
    """Map transcript to part keys from the catalog (dedup, negation-aware)."""
    t = normalize_for_matching(transcript or "").lower()
    hits: List[str] = []
    for key, patterns in _COMPILED_PART_KWS.items():
        pos_hit = False
        for pat in patterns:
            for m in pat.finditer(t):
                if not _keyword_negated_in_text(t, m.start(), m.end()):
                    pos_hit = True
                    break
            if pos_hit:
                break
        if pos_hit:
            hits.append(key)
    return hits


def extract_negated_parts(transcript: str) -> List[str]:
    """Return catalog parts explicitly negated in `transcript` (for corrections)."""
    t = normalize_for_matching(transcript or "").lower()
    neg_hits: List[str] = []
    for key, patterns in _COMPILED_PART_KWS.items():
        neg = False
        pos = False
        for pat in patterns:
            for m in pat.finditer(t):
                if _keyword_negated_in_text(t, m.start(), m.end()):
                    neg = True
                else:
                    pos = True
        if neg and not pos:
            neg_hits.append(key)
    return neg_hits


def detect_drivability(parts: List[str], transcript: str = "") -> Tuple[bool, str]:
    """False if ANY non-drivable part is present, OR puncture+crash parts combine,
    OR transcript explicitly says the car is not drivable."""
    worst = ""
    for p in parts:
        meta = PARTS.get(p, {})
        if meta.get("safety") == "non_drivable":
            return False, meta.get("display_en", p)

    # Flat tyre combined with collision/body damage cannot be safely self-driven
    if "puncture" in parts and any(PARTS.get(p, {}).get("workflow") == "C" for p in parts):
        return False, "Tyre puncture with crash damage — unsafe to self-drive"

    t = normalize_for_matching(transcript or "").lower()
    drivable_negation_phrases = (
        "chalne lik nahi",
        "chalne layak nahi",
        "chalne layak nahin",
        "chalne laayak nahi",
        "chalne laayak nahin",
        "chalane layak nahi",
        "chalane layak nahin",
        "chalane laayak nahi",
        "chalane laayak nahin",
        "chal nahi rahi",
        "chal nahi paa rahi",
        "move nahi ho rahi",
        "start mat kijiye",
        "gaadi start mat",
        "engine start mat",
        "drivable nahi",
        "not drivable",
        "cannot drive",
        "can't drive",
        "cant drive",
        "unable to drive",
        "won't move",
        "wont move",
        "not movable",
        "won't start",
        "wont start",
        "tyre toot",
        "tyre phat",
        "wheel toot",
        "pahiya toot",
    )
    for phrase in drivable_negation_phrases:
        if phrase in t:
            return False, "Driver stated vehicle is not drivable"

    # Only flag generic "not working" / "gaadi start nahi ho rahi" as non-drivable
    # when the detected parts are NOT pure Workflow-A battery/fuel on-spot jobs.
    all_minor_a = bool(parts) and all(PARTS.get(p, {}).get("workflow") == "A" for p in parts)
    if not all_minor_a:
        for phrase in ("not working", "not functional", "gaadi chalne nahi aa rahi"):
            if phrase in t:
                return False, "Driver stated vehicle is not functional"

    for p in parts:
        if PARTS.get(p, {}).get("safety") == "drivable_minor":
            worst = PARTS[p].get("display_en", p)
    return True, (worst or "no visible damage / drivable")


_CONFIRM_RE = re.compile(
    r"\b(?:confirm kar do|confirm karde|haan confirm|haan ji|haan|yes please|yes confirm|yes go ahead|"
    r"go ahead|please confirm|sounds good|yes|theek hai|thik hai|"
    r"ok|okay|kardo|kar do|confirm)\b",
    re.IGNORECASE,
)
_CORRECT_RE = re.compile(
    r"\b(?:ruko|ruk ja|rukja|galat|wrong|incorrect|mistake|actually no|not that|"
    r"arre nahi|nahi nahi|hold on|pause|change|badal)\b",
    re.IGNORECASE,
)


def confirm_detection(transcript: str, prior_policy: Optional[str] = None) -> str:
    """none | confirm | correct — the Turn-2/Barge-In confirmation loop."""
    t = normalize_for_matching(transcript or "").lower()
    mentioned_policy = extract_policy_number(t)
    has_policy_change = bool(
        mentioned_policy
        and _POLICY_HINT.search(t)
        and prior_policy
        and str(mentioned_policy) != str(prior_policy)
    )
    if _CORRECT_RE.search(t) or has_policy_change:
        return "correct"
    if _CONFIRM_RE.search(t) and not _POLICY_HINT.search(t):
        return "confirm"
    return "none"


# ===========================================================================
# TOOL 1 — Injury & Safety Triage
# ===========================================================================

_SAFE = re.compile(
    r"\b(?:safe|everyone is safe|we are safe|nobody is injured|no one is injured|nobody got hurt|"
    r"no injuries|no injury|all safe|uninjured|unhurt|"
    r"सुरक्षित|सब ठीक|सब सुरक्षित|theek hai|thik hai|सब ठीक है|"
    r"koi chot nahi|koi takleef nahi|chot nahi lagi|chot nahi hui|chot na lagi|"
    r"sab thik|sab safe|sab surakshit|khatre mein nahi|"
    r"பாதுகாப்பு|பாதுகாப்பாக|பத்திரம்|நல்லா இருக்கோம்|காயமில்லை|அடிபடல)\b",
    re.IGNORECASE,
)

_NEGATION_WORDS = (
    "नहीं", "नही", "नहि", "नाहि", "बिना", "बगैर", "ना", "मत",
    "कोई चोट नहीं", "कोई तकलीफ नहीं",
    "no", "not", "none", "nobody", "no one", "neither", "don't", "do not", "without", "uninjured", "unhurt",
    "जरूरत नहीं", "zarurat nahi", "zarurat nahin",
    "necessity nahi", "nahi hai", "nahi ho", "nahi hui", "nahi lagi",
    "nahi", "nahin", "nahiin", "naa", "na", "naahi", "binaa", "bina", "bagair", "mat", "koni",
)

# Word-bounded negation regex so "no" never hits "minor"/"unknown" and "na" never hits "kitna"
_NEGATION_RE = re.compile(
    r"(?:(?<![a-z\u0900-\u0d7f])(?:"
    + "|".join(re.escape(w) for w in sorted(_NEGATION_WORDS, key=len, reverse=True))
    + r")(?![a-z\u0900-\u0d7f]))",
    re.IGNORECASE,
)


_CLAUSE_BREAK_LEFT_RE = re.compile(r"(?:[,.;!?।—]|\b(?:par|lekin|magar|but|jabki|ab)\b)", re.IGNORECASE)
_CLAUSE_BREAK_RIGHT_RE = re.compile(r"(?:[,.;!?।—]|\b(?:par|lekin|magar|but|jabki)\b)", re.IGNORECASE)


def _injury_keyword_negated_at(t: str, start: int, end: int) -> bool:
    """True if this injury match sits within the same clause as a negation word."""
    left = t[max(0, start - 36): start].lower()
    right = t[end: min(len(t), end + 36)].lower()
    left_parts = _CLAUSE_BREAK_LEFT_RE.split(left)
    left_clause = left_parts[-1] if left_parts else left
    right_parts = _CLAUSE_BREAK_RIGHT_RE.split(right)
    right_clause = right_parts[0] if right_parts else right
    local = f"{left_clause} {right_clause}"
    return bool(_NEGATION_RE.search(local))


def _injury_keyword_negated(t: str, keyword: str) -> bool:
    """Backwards-compatible helper: True if every occurrence of keyword is negated."""
    hits = list(re.finditer(r"(?<![a-z])" + re.escape(keyword) + r"(?![a-z])", t))
    if not hits:
        return False
    return all(_injury_keyword_negated_at(t, m.start(), m.end()) for m in hits)


def triage_injury_and_safety(transcript: str, language: str = "hi-IN") -> Dict[str, Any]:
    """Detect injury signal. If injured: HALT claim form-filling, raise
    112 ambulance + 1033 NHAI patrol + live human officer handoff.
    """
    t = normalize_for_matching(transcript or "").lower()
    inj_kw = list(dict.fromkeys(EMERGENCY["injury_keywords_en"] + EMERGENCY["injury_keywords_hi"]))
    lang = normalize_lang_code(language) or "hi-IN"

    hits: List[Tuple[str, int, int]] = []
    for k in inj_kw:
        for m in re.finditer(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", t):
            hits.append((k, m.start(), m.end()))

    safe_assertion = bool(_SAFE.search(t))
    real_hits = [k for k, s, e in hits if not _injury_keyword_negated_at(t, s, e)]
    real_injured = bool(real_hits)

    injured = real_injured
    if safe_assertion and not real_injured:
        injured = False

    matched = list(dict.fromkeys(real_hits)) if injured else []

    out = {
        "tool": "triage_injury_and_safety",
        "injured": injured,
        "matched_keywords": matched,
        "safety_status": "EMERGENCY — 112 AMBULANCE & POLICE DISPATCHED" if injured else "SAFE — No injury reported",
    }
    if injured:
        empathy_local = STRINGS.get(lang, STRINGS["hi-IN"])["inj_empathy"]
        out.update(
            {
                "emergency_calls": [
                    {"line": EMERGENCY["ambulance"], "label": "🚨 Ambulance (112)"},
                    {"line": "112-POLICE", "label": "👮 Police Control Room (112)"},
                    {"line": EMERGENCY["nhai_highway_patrol"], "label": "🛣️ NHAI Highway Patrol (1033)"},
                    {"line": "LIVE-AGENT", "label": "☎️ Human Emergency Officer Handoff (priority queue)"},
                ],
                "halt_claim_intake": True,
                "spoken_empathy": empathy_local,
                "spoken_empathy_hi": STRINGS["hi-IN"]["inj_empathy"],
                "spoken_empathy_ta": STRINGS["ta-IN"]["inj_empathy"],
                "claim_intake_paused_reason": "Human safety > claim forms. FNOL resumed only after medical handoff.",
            }
        )
    else:
        safe_local = STRINGS.get(lang, STRINGS["hi-IN"])["safe_empathy"]
        out.update({
            "emergency_calls": [],
            "halt_claim_intake": False,
            "spoken_empathy": safe_local,
            "spoken_empathy_hi": STRINGS["hi-IN"]["safe_empathy"],
            "spoken_empathy_ta": STRINGS["ta-IN"]["safe_empathy"],
        })
    return out


# ===========================================================================
# TOOL 2 — Smart RSA Dispatch (Workflows A / B / C)
# ===========================================================================

def _pick_garage(
    road_type: str,
    vehicle_class: str,
    is_ev: bool = False,
    preferred_garage_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    cands = [g for g in GARAGES if g["road_type"] == road_type]
    if is_ev:
        ev = [g for g in cands if g.get("services_ev")]
        if ev:
            cands = ev
    else:
        non_ev = [g for g in cands if not g.get("services_ev")]
        if non_ev:
            cands = non_ev

    if vehicle_class == "two_wheeler":
        two = [g for g in cands if g.get("services_2w")]
        if two:
            cands = two
    else:
        four = [g for g in cands if not g.get("services_2w")]
        if four:
            cands = four

    if preferred_garage_id:
        for g in cands:
            if g.get("id") == preferred_garage_id:
                return g
    return cands[0] if cands else (GARAGES[0] if GARAGES else None)


def dispatch_smart_rsa(road_type: str, parts: List[str], is_drivable: bool,
                       policy: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Route to exactly one of the 3 real Indian RSA workflows."""
    policy = policy or {}
    veh_cls = policy.get("vehicle_class", "four_wheeler")
    is_ev = policy.get("fuel") == "electric"
    pref_garage = policy.get("preferred_garage")
    out: Dict[str, Any] = {
        "tool": "dispatch_smart_rsa",
        "road_type": road_type,
        "vehicle_class": veh_cls,
        "policy_number": policy.get("policy_number"),
        "parts": parts,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }

    is_minor = bool(parts) and all(PARTS.get(p, {}).get("workflow") == "A" for p in parts)
    if is_minor and is_drivable:
        wf = WORKFLOWS["A_minor_breakdown"]
        vendor = (
            wf["vendor"]
            if veh_cls == "two_wheeler"
            else "On-Spot RSA Technician (4-Wheeler)"
        )
        mechanic_label = "Bike mechanic" if veh_cls == "two_wheeler" else "RSA technician"
        out.update(
            {
                "workflow": "A",
                "workflow_label": wf["label"],
                "dispatch_vendor": vendor,
                "dispatch_vendor_type": "on_spot_bike_mechanic" if veh_cls == "two_wheeler" else "on_spot_rsa_technician",
                "eta_mins": wf["vendor_eta_mins"],
                "ncb_protected": True,
                "nhai_patrol_alerted": road_type == "NH",
                "do_not_claim_warning": (
                    "Ye chhoti si service RSA se FREE ho jayegi aur aapka 20% No-Claim Bonus "
                    "bhi salamat rahega — isliye insurance claim FILE MAT kijiye!"
                ),
                "do_not_claim_spoken_hi": (
                    f"Sahi news! Ye chota breakdown hai. {mechanic_label} sirf 22 minute mein aayega "
                    "aur service bilkul FREE hogi. Sabse badi baat — aapke 20 percent No-Claim "
                    "Bonus salamat rahenge, isliye claim file bilkul mat kijiye."
                ),
                "do_not_claim_spoken_ta": (
                    "நல்ல செய்தி! இது சிறிய பழுதுதான். 22 நிமிடங்களில் சாலை உதவி பணியாளர் "
                    "உங்கள் இடத்திற்கு வருவார், இந்த சேவை முற்றிலும் இலவசம். காப்பீட்டு கோரிக்கை "
                    "போட வேண்டாம் — உங்கள் இருபது சதவீத தள்ளுபடி சலுகை பாதுகாப்பாக இருக்கும்."
                ),
            }
        )
        return out

    if not is_drivable:
        wf = WORKFLOWS["B_non_drivable"]
        garage = _pick_garage(road_type, veh_cls, is_ev, pref_garage)
        tow_km = IRDAI["free_tow_km_limit"]
        out.update(
            {
                "workflow": "B",
                "workflow_label": wf["label"],
                "do_not_start_engine": True,
                "engine_warning": "🔴 Do NOT Start Engine! (radiator/axle/coolant/tyre damage risk)",
                "engine_warning_spoken_hi": (
                    "Kripya gaadi start bilkul MAT kijiye — engine seize ho sakta hai."
                ),
                "engine_warning_spoken_ta": (
                    "தயவுசெய்து வண்டியை ஸ்டார்ட் செய்யாதீர்கள் — என்ஜின் பழுதடைய வாய்ப்புள்ளது."
                ),
                "dispatch_vendor": wf["vendor"],
                "dispatch_vendor_type": "flatbed_tow_truck",
                "eta_mins": wf["vendor_eta_mins"],
                "free_tow_km_limit": tow_km,
                "destination_garage": garage,
                "nhai_patrol_alerted":
                    road_type == "NH" and bool(wf.get("nhai_patrol_1033")),
                "nhai_patrol_detail":
                    "1033 NHAI Highway Patrol — fast-lane safety cones requested"
                    if road_type == "NH" else None,
                "rural_reimbursement":
                    {
                        "eligible": road_type == "RURAL",
                        "reason": "Rural link road: nearest workshop ETA > 2 hours",
                        "amount_inr": IRDAI["rural_towing_reimbursement_inr"],
                        "authority": "IRDAI local crane/towing reimbursement authorization ₹1,500",
                    }
                    if road_type == "RURAL" else None,
                "ncb_protected": False,
                "photos_note": "For surveyor record: 4 photos with number plate visible.",
            }
        )
        return out

    wf = WORKFLOWS["C_drivable_minor"]
    garage = _pick_garage(road_type, veh_cls, is_ev, pref_garage) if parts else None
    out.update(
        {
            "workflow": "C",
            "workflow_label": wf["label"],
            "dispatch_vendor": wf["vendor"],
            "dispatch_vendor_type": "self_drive",
            "eta_mins": 0,
            "photos_required": wf.get("photos_required", 4),
            "photo_instruction_spoken_hi": (
                "Garage jaane se pehle number plate ke saath 4 photo zaroor kheench lein."
            ),
            "photo_instruction_spoken_ta": (
                "பழுதுபார்க்கும் மையத்திற்குச் செல்லும் முன் எண் பலகை தெரியும்படி 4 புகைப்படங்கள் எடுத்துக்கொள்ளுங்கள்."
            ),
            "destination_garage": garage,
            "ncb_protected": False,
        }
    )
    return out


# ===========================================================================
# TOOL 3 — IRDAI Claim Draft Math
# ===========================================================================

def calculate_irdai_claim_draft(policy_number: Optional[str], parts: List[str],
                                age_of_vehicle_years: float = 2.0,
                                policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Compute deductible + depreciation + estimated repair range for FNOL."""
    pol = policy or (POLICIES.get(str(policy_number)) if policy_number else None)
    if not pol:
        return {
            "tool": "calculate_irdai_claim_draft",
            "status": "policy_not_found",
            "message": "Policy number not found in synthetic DB; claim draft not created.",
        }

    veh_cls = pol["vehicle_class"]
    deductible = IRDAI["compulsory_deductible_inr"][veh_cls]
    zero_dep = bool(pol.get("zero_depreciation"))

    line_items = []
    total_low = 0
    total_high = 0
    for p in parts:
        meta = PARTS.get(p)
        if not meta:
            continue
        low, high = meta["base_cost_inr"]
        dep_pct = meta["depreciation_pct"]
        if zero_dep:
            dep_after = 0.0
        else:
            scaled = dep_pct * max(0.0, min(3.0, age_of_vehicle_years) / 3.0) + dep_pct * 0.25
            dep_after = round(min(scaled, 60.0), 1)
        dep_factor = 1.0 - dep_after / 100.0
        allow_low = round(low * dep_factor)
        allow_high = round(high * dep_factor)
        line_items.append(
            {
                "part": p,
                "display_en": meta["display_en"],
                "genuine_cost_low": low,
                "genuine_cost_high": high,
                "depreciation_pct_applied": dep_after,
                "allowable_low": allow_low,
                "allowable_high": allow_high,
            }
        )
        total_low += allow_low
        total_high += allow_high

    est_low = max(0, total_low - deductible)
    est_high = max(0, total_high - deductible)
    claim_id = "FNOL-" + uuid.uuid4().hex[:8].upper()

    return {
        "tool": "calculate_irdai_claim_draft",
        "claim_id": claim_id,
        "policy_number": pol["policy_number"],
        "owner": pol["owner"],
        "vehicle": f'{pol["vehicle_make"]} {pol["vehicle_model"]}',
        "vehicle_class": veh_cls,
        "plan": pol["plan"],
        "zero_depreciation": zero_dep,
        "idv_inr": pol["idv"],
        "compulsory_deductible_inr": deductible,
        "line_items": line_items,
        "estimated_repair_low_inr": est_low,
        "estimated_repair_high_inr": est_high,
        "estimated_range_display":
            f"₹{est_low:,} se ₹{est_high:,}" if est_high else "₹0 (RSA-covered minor job)",
        "estimated_range_display_ta":
            f"₹{est_low:,} முதல் ₹{est_high:,} வரை" if est_high else "₹0 (சாலை உதவி சிறிய பழுது)",
        "status_label": IRDAI["range_label"],
        "status_label_spoken_hi": (
            "Yeh anumaanit range hai — antim rashi surveyor nireekshan ke baad tay hogi."
        ),
        "status_label_spoken_ta": (
            "இது மதிப்பீட்டுத் தொகை மட்டுமே — ஆய்வாளர் பரிசோதனைக்குப் பிறகு இறுதித் தொகை உறுதி செய்யப்படும்."
        ),
        "photos_required": 4,
        "age_of_vehicle_years": age_of_vehicle_years,
    }


# ===========================================================================
# Spoken read-back composer (feeds clause_buffer)
# ===========================================================================

SPOKEN_PART: Dict[str, str] = {
    "radiator": "radiator",
    "bumper": "bumper",
    "headlight": "headlight",
    "side_mirror": "side mirror",
    "axle": "axle",
    "puncture": "puncture",
    "battery": "battery",
    "front_guard": "front guard",
    "clutch_wire": "clutch wire",
    "fuel_out": "petrol khatam",
    "ev_system_stuck": "EV system",
}

SPOKEN_PART_TA: Dict[str, str] = {
    "radiator": "ரேடியேட்டர்",
    "bumper": "பம்பர்",
    "headlight": "ஹெட்லைட்",
    "side_mirror": "சைடு மிரர்",
    "axle": "ஆக்ஸில்",
    "puncture": "டயர் பஞ்சர்",
    "battery": "பேட்டரி",
    "front_guard": "முன் கார்டு",
    "clutch_wire": "கிளட்ச் ஒயர்",
    "fuel_out": "பெட்ரோல் காலி",
    "ev_system_stuck": "மின்கல அமைப்பு",
}

_GARAGE_NAMES_TA: Dict[str, str] = {
    "g_karnal_motors": "கர்னால் மோட்டார்ஸ் மையம்",
    "g_karol_bagh_2w": "கரோல் பாக் இருசக்கர வாகன மையம்",
    "g_meerut_2w": "மீரட் இருசக்கர வாகன மையம்",
    "g_panipat_motors": "பானிபட் மோட்டார் வொர்க்ஸ் மையம்",
    "g_chennai_ev": "சென்னை மின்சார வாகன பராமரிப்பு மையம்",
    "g_delhi_motors": "டெல்லி என்.சி.ஆர். கேஷ்லெஸ் வாகன மையம்",
    "g_rural_karnal_link": "கர்னால் லிங்க் ரோடு பழுதுபார்க்கும் மையம்",
    "g_rural_panipat_4w": "பானிபட் கிராமப்புற பழுதுபார்க்கும் மையம்",
}


def _garage_name_for_lang(garage: Optional[Dict[str, Any]], language: str = "hi-IN") -> str:
    lang = normalize_lang_code(language) or "hi-IN"
    return localize_garage(garage, lang)


def spoken_part(parts: List[str], language: str = "hi-IN") -> str:
    """Human-friendly name of the highest-priority part (non-drivable first)."""
    if not parts:
        return ""
    lang = normalize_lang_code(language) or "hi-IN"
    if lang == "hi-IN":
        priority = {"non_drivable": 0, "drivable_minor": 1, "minor_breakdown": 2}
        ordered = sorted(
            parts,
            key=lambda p: priority.get(PARTS.get(p, {}).get("safety", "minor_breakdown"), 3),
        )
        first = ordered[0]
        return SPOKEN_PART.get(first, PARTS.get(first, {}).get("display_en", first).split(" / ")[0].lower())
    return spoken_part_localized(parts, PARTS, lang)


def compose_agent_reply(incident: Dict[str, Any], ack: str = "",
                        ask_confirm: bool = True, language: str = "hi-IN") -> str:
    """Turn structured tool outputs into ONE 2-clause-ready spoken reply in any Gnani language."""
    lang = normalize_lang_code(language) or "hi-IN"
    if lang != "hi-IN":
        localized = compose_localized_reply(
            lang, incident, PARTS, IRDAI["rural_towing_reimbursement_inr"],
            ack=ack, ask_confirm=ask_confirm,
        )
        if localized:
            return localized

    tri = incident.get("triage", {})
    disp = incident.get("dispatch", {})
    draft = incident.get("claim_draft", {})

    if tri.get("injured"):
        return tri["spoken_empathy_hi"]

    policy_no = disp.get("policy_number") or (draft.get("policy_number") if draft else "")
    policy_ref = f"Policy {policy_no} ke tahat " if policy_no else "Aapki RSA policy ke tahat "
    part_word = spoken_part(disp.get("parts", []) or []) or "breakdown"
    veh_cls = disp.get("vehicle_class", "four_wheeler")
    mech_word = "bike mechanic" if veh_cls == "two_wheeler" else "RSA mechanic"

    if disp.get("workflow") == "A":
        opener = ack.strip() or "Achha ji."
        clause1 = (
            f"{opener} {part_word.capitalize()} to chhoti si baat hai — "
            "tension mat lijiye, gaadi safe hai."
        )
        clause2 = (
            f"Main {disp.get('eta_mins', 22)} minute mein on-spot {mech_word} bhej rahi hoon, "
            "service RSA se bilkul FREE hai, aur claim file nahi karne se aapka "
            "20 percent No-Claim Bonus salamat rahega"
        )
        if disp.get("nhai_patrol_alerted"):
            clause2 += ", aur 1033 highway patrol ko bhi safety ke liye bata diya hai"
        clause2 += (". Dispatch confirm kar doon?" if ask_confirm
                    else " — dispatch ho gaya ji.")
    elif disp.get("workflow") == "B" and (disp.get("rural_reimbursement") or {}).get("eligible"):
        clause1 = (f"{ack.strip() or 'Shukar hai aap safe hain!'} "
                   f"Kyunki {part_word} damage hai, kripya gaadi start mat kijiye.")
        clause2 = (
            f"{policy_ref}aapke sabse nazdik workshop 2 ghante se zyada dur hai, "
            f"isliye I.R.D.A.I. niyam se ₹{IRDAI['rural_towing_reimbursement_inr']} ka local towing "
            "reimbursement authorize kar rahi hoon — matlab local crane bhi ho jayega."
            + (" Kya main confirm kar doon?" if ask_confirm else "")
        )
    elif disp.get("workflow") == "B":
        gname = (disp.get("destination_garage") or {}).get("name", "network garage")
        clause1 = (
            f"{ack.strip() or 'Shukar hai aap safe hain!'} "
            f"Kyunki {part_word} damage hai, kripya gaadi start mat kijiye."
        )
        clause2 = (
            f"{policy_ref}{gname} se FREE flatbed tow truck bhej rahe hain"
            + (", aur 1033 highway patrol safety cones bhi laga raha hai" if disp.get("nhai_patrol_alerted") else "")
            + (f", aur {draft.get('estimated_range_display', '')} ka claim draft taiyaar hai" if draft else "")
            + (". Kya main confirm kar doon?" if ask_confirm else ".")
        )
    elif disp.get("workflow") == "C":
        gname = (disp.get("destination_garage") or {}).get("name", "network garage")
        clause1 = (f"{ack.strip() or 'Shukar hai aap safe hain!'} "
                   "Gaadi chalne layak hai, sirf bahar ka damage lagta hai.")
        clause2 = (
            f"Kripya number plate dikhta hua 4 photo kheench lein aur seedha "
            f"{gname} chale jaiye"
            + (f" — {draft.get('estimated_range_display', '')} ka claim draft taiyaar hai" if draft else "")
            + (". Kya main intimation confirm kar doon?" if ask_confirm else ".")
        )
    else:
        clause1 = ack.strip() or "Samajh gayi ji."
        clause2 = "Mujhe thoda aur detail chahiye — gaadi mein kya hua aur aap kahan hain?"

    return f"{clause1} {clause2}".strip()
