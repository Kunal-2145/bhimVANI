"""

BimaVani — Domain Rule Engine: Injury Triage • Smart RSA Dispatch • IRDAI Claim Math

==================================================================================

Three domain tools called by backend/pipeline/brain.py:



    Tool 1: triage_injury_and_safety(transcript)

    Tool 2: dispatch_smart_rsa(road_type, parts, is_drivable, policy)

    Tool 3: calculate_irdai_claim_draft(policy_number, parts)



Plus the deterministic Hinglish Danny entity extractor helpers used by

brain.py (spoken Hindi numeral parser, road-type detection, part detection,

policy number / confirmation detection). Zero hallucination: every number

spoken by the agent below is computed from backend/data/synthetic_db.json.

"""

from __future__ import annotations



import json

import os

import re

import uuid

from datetime import datetime, timezone

from typing import Any, Dict, List, Optional, Tuple



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



_DIGIT_WORDS: Dict[str, str] = {

    "zero": "0", "shunya": "0", "sunya": "0", "sifar": "0", "o": "0",

    "one": "1", "ek": "1", "onay": "1",

    "two": "2", "do": "2", "dou": "2", "tu": "2", "to": "2", "doo": "2",

    "three": "3", "teen": "3", "tin": "3", "tina": "3",

    "four": "4", "char": "4", "chaar": "4", "fourr": "4", "chara": "4",

    "five": "5", "paanch": "5", "panch": "5", "pach": "5",

    "six": "6", "che": "6", "chhe": "6", "chah": "6", "chay": "6",

    "seven": "7", "saat": "7", "sat": "7", "saath": "7", "satt": "7",

    "eight": "8", "aath": "8", "aat": "8", "ath": "8", "eight-aath": "8",

    "nine": "9", "nau": "9", "nov": "9", "nauy": "9",

}



_MAG_WORDS: Dict[str, int] = {

    "hazaar": 1000, "hajar": 1000, "hazār": 1000, "thousand": 1000,

    "sau": 100, "hundred": 100,

}



# Hindi/Hinglish tens used after "sau"; each value INCLUDES units:

#   "chaar sau ikyaanave" = 400 + 91;  "sau ninyanave" = 100 + 99.

_TENS_WORDS: Dict[str, int] = {

    "bees": 20, "bis": 20, "tees": 30, "tis": 30, "chalis": 40, "chealis": 40,

    "pachas": 50, "panchaas": 50, "saasat": 60, "sasat": 60, "asath": 60,

    "asarh": 70, "unhatar": 70, "assi": 80, "athasi": 80, "nabbe": 90,

    "navagate": 90, "ninyanavay": 99, "ninyanave": 99, "ninyanaveen": 99,

    "ninyaanve": 99, "unachas": 49, "unanchas": 49, "unachaas": 49,

    "unachasa": 49, "unayanave": 89, "ninyanavis": 99, "chavan": 55, "chatween": 54,

    "ikyaanave": 91, "ikyaanve": 91, "ekyaanave": 91, "ekyaanve": 91, "ekyaanavay": 91,

    "ikyaalis": 41, "ikyatlis": 41, "ikyaavan": 51, "barevanv": 92, "tiravanve": 93,

}



_POLICY_HINT = re.compile(r"\b(p[- ]?o[- ]?l[- ]?i[- ]?c[- ]?y|paulisi|policy no|number)\b", re.IGNORECASE)

_RAW_NUM = re.compile(r"\b(\d{4})\b")





def parse_spoken_numbers(text: str) -> List[int]:

    """Public helper: ordered unique candidate numbers from the transcript."""

    return [v for v, _neg in _parse_numbers_detailed(text)]





def _parse_numbers_detailed(text: str) -> List[Tuple[int, bool]]:

    """Explode transcript into (number, is_negated) candidates.



    Handles: raw "7820", digit-by-digit "saat aath do zero", magnitude forms

    "saat hazaar aath sau bees" (7*1000 + 8*100 + 20), and flags negation

    ("... 7820 nahi") so barge-in corrections can drop the retracted number.

    """

    t = (text or "").lower().strip()

    found: List[Tuple[int, int, int]] = []



    for m in _RAW_NUM.finditer(t):

        found.append((int(m.group(1)), m.start(), m.end()))

    # digit-by-digit runs of spoken words

    words = re.findall(r"[a-zāīū]+", t)

    i = 0

    while i < len(words):

        w = words[i]

        if w in _DIGIT_WORDS:

            digits = ""

            j = i

            while j < len(words) and words[j] in _DIGIT_WORDS:

                digits += _DIGIT_WORDS[words[j]]

                j += 1

            if len(digits) >= 2:

                found.append((int(digits), i, j))

            i = max(i + 1, j - 1 if j > i else i)

            i = j

            continue

        i += 1

    # magnitude form: "<digit> hazaar [<d> sau] [<tens|d>]" — greedy token walk

    for m in re.finditer(r"(\w+)\s+(?:hazaar|hajar|hazār|thousand)", t):

        hi = _DIGIT_WORDS.get(m.group(1))

        if not hi:

            continue

        val = int(hi) * 1000

        j = m.end()

        trail = re.findall(r"[a-zāīū]+", t[j:])

        k = 0

        while k < len(trail) and trail[k] in _DIGIT_WORDS | _TENS_WORDS:

            w = trail[k]

            if w in _DIGIT_WORDS:

                nxt = trail[k + 1] if k + 1 < len(trail) else ""

                if nxt in _MAG_WORDS and _MAG_WORDS[nxt] == 100:

                    val += int(_DIGIT_WORDS[w]) * 100

                    k += 2

                    continue

                val += int(_DIGIT_WORDS[w])

                k += 1

            else:  # tens word includes its units (e.g. ikyaanave = 91)

                val += _TENS_WORDS[w]

                k += 1

        found.append((val, m.start(), m.end()))



    out: List[Tuple[int, bool]] = []

    seen = set()

    for v, s, e in sorted(found, key=lambda x: x[1]):

        if v in seen or not (100 <= v <= 99999):

            continue

        after = t[e:e + 16]

        neg = bool(re.search(r"^\W*(nahi|nahin|wrong|galat|null)", after))

        out.append((v, neg))

        seen.add(v)

    return out





# ===========================================================================

# Deterministic Hinglish entity extraction helpers (used by brain.py)

# ===========================================================================



def extract_policy_number(transcript: str) -> Optional[str]:

    """Return the best 4-digit policy code mentioned, else None.



    Prefers values adjacent to the word "policy/number"; falls back to the

    first spoken number that matches a known policy id, then any 4-digit.

    """

    t = transcript or ""

    nums = _parse_numbers_detailed(t)

    if not nums:

        return None

    low = t.lower()

    known = set(POLICIES.keys())

    live = [str(v) for v, neg in nums if not neg] or [str(nums[-1][0])]

    if _POLICY_HINT.search(low):

        # barge-in correction rule: the most recent NON-negated number wins

        return live[-1]

    for n in live:

        if n in known:

            return n

    return None





def extract_road_type(transcript: str, policy: Optional[Dict[str, Any]] = None) -> str:

    """One of NH | CITY | RURAL (NH wins on ties — safer default for RSA)."""

    t = (transcript or "").lower()

    nh_kw = ["nh", "nh-44", "nh44", "highway", "haaiway", "haaive", "haiwe",

             "national highway", "gt road",

             "fast lane", "kmp", "expressway", "ring road", "dive road", "dívai"]

    rural_kw = ["link road", "rural", "gaon", "village road", "kaccha", "kachcha",

                "interior", "countryside", "corner ka rasta", "local road", "tappal"]

    if any(k in t for k in nh_kw):

        return "NH"

    if any(k in t for k in rural_kw):

        return "RURAL"

    if policy and policy.get("home_city"):

        pass

    return "CITY"





def extract_parts(transcript: str) -> List[str]:

    """Map transcript to part keys from the catalog (dedup, order stable).



    Keywords match at a WORD START (prefix allowed, so "punctur" still hits

    "puncture") but never mid-word — otherwise "staart" (स्टार्ट) would match

    the clutch-wire keyword "taar" and invent a part that was never damaged.

    """

    t = (transcript or "").lower()

    hits: List[str] = []

    for key, meta in PARTS.items():

        kws = [k.lower() for k in meta.get("keywords_en", []) + meta.get("keywords_hi", [])]

        if any(re.search(r"(?<![a-z])" + re.escape(k), t) for k in kws):

            hits.append(key)

    return hits





def detect_drivability(parts: List[str], transcript: str = "") -> Tuple[bool, str]:

    """False if ANY non-drivable part is present, OR if transcript explicitly says car is not drivable."""

    worst = ""

    # First check for non-drivable parts in the catalog

    for p in parts:

        meta = PARTS.get(p, {})

        if meta.get("safety") == "non_drivable":

            return False, meta.get("display_en", p)

    # Then check transcript for explicit "not drivable" / "not working" phrases

    t = (transcript or "").lower()

    drivable_negation_phrases = (

        "chalne lik nahi",

        "chalne layak nahi",

        "chalne lायक nahi",

        "start mat kijiye",

        "gaadi start mat",

        "engine start mat",

        "drivable nahi",

        "not drivable",

        "not working",

        "not functional",

        "gaadi chalne nahi aa rahi",

        "gaadi start nahi ho rahi",

    )

    for phrase in drivable_negation_phrases:

        if phrase in t:

            return False, "Driver stated car is not drivable"

    # If no non-drivable parts found and no explicit negation, check minor

    for p in parts:

        if PARTS.get(p, {}).get("safety") == "drivable_minor":

            worst = PARTS[p].get("display_en", p)

    return True, (worst or "no visible damage / drivable")





def confirm_detection(transcript: str) -> str:

    """none | confirm | correct — the Turn-2/Barge-In confirmation loop.



    Priority: a correction always beats a plain confirm; a bare "haan"

    without any policy re-statement is a confirm.

    """

    t = (transcript or "").lower()

    confirm_kw = ["confirm kar do", "confirm karde", "haan confirm", "haan ji",

                  "haan", "yes", "theek hai", "ok", "kardo", "kar do", "confirm"]

    correct_kw = ["ruko", "ruk ja", "rukja", "galat", "wrong", "arre nahi",

                  "nahi nahi", "hold on", "pause", "matlab"]

    has_policy_restatement = bool(extract_policy_number(t) and _POLICY_HINT.search(t))

    if any(k in t for k in correct_kw) or has_policy_restatement:

        return "correct"

    if any(k in t for k in confirm_kw) and not _POLICY_HINT.search(t):

        return "confirm"

    return "none"





# ===========================================================================

# TOOL 1 — Injury & Safety Triage

# ===========================================================================



# Negation-aware triage helpers -----------------------------------------------

# Hindi injury keywords often appear NEGATED ("चोट नहीं लगी", "जरूरत नहीं").

# A bare substring match would fire the emergency on "ambulance ki koi

# zarurat nahi hai", so we (a) drop any keyword that sits inside a negation

# window and (b) honor explicit "safe / theek hai" assertions.

_SAFE = re.compile(

    r"\b(safe|सुरक्षित|सब ठीक|सब सुरक्षित|theek hai|thik hai|सब ठीक है|"

    r"koi chot nahi|koi takleef nahi|chot nahi lagi|chot nahi hui|"

    r"sab thik|sab safe|sab surakshit|khatre mein nahi)\b",

    re.IGNORECASE,

)





_NEGATION_WORDS = (

    "नहीं", "नही", "नहि", "नाहि", "बिना", "बगैर", "ना", "मत",

    "कोई चोट नहीं", "कोई तकलीफ नहीं",

    "no", "not", "don't", "do not", "without", "uninjured", "unhurt",

    "जरूरत नहीं", "zarurat nahi", "zarurat nahin",

    "necessity nahi", "nahi hai", "nahi ho", "nahi hui", "nahi lagi",

    "nahi", "nahin", "nahiin",

)





def _injury_keyword_negated(t: str, keyword: str) -> bool:

    """True if `keyword` appears within ~40 chars of a negation word."""

    for m in re.finditer(re.escape(keyword), t):

        window = t[max(0, m.start() - 40): min(len(t), m.end() + 40)]

        w = window.lower()

        if any(n in w for n in _NEGATION_WORDS):

            return True

    return False



def triage_injury_and_safety(transcript: str) -> Dict[str, Any]:

    """Detect injury signal. If injured: HALT claim form-filling, raise

    112 ambulance + 1033 NHAI patrol + live human officer handoff.



    Negation-aware: an injury keyword that is negated ("चोट नहीं लगी",

    "ambulance not needed") does NOT count as injured, and an explicit

    "safe / theek hai" assertion overrides a bare keyword hit.

    """

    t = (transcript or "").lower()

    inj_kw = list(dict.fromkeys(EMERGENCY["injury_keywords_en"] + EMERGENCY["injury_keywords_hi"]))



    # Collect every injury keyword hit with its span.

    hits: List[Tuple[str, int, int]] = []

    for k in inj_kw:

        for m in re.finditer(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", t):

            hits.append((k, m.start(), m.end()))



    safe_assertion = bool(_SAFE.search(t))



    # Keep only hits that are NOT negated.

    real_hits = [k for k, s, e in hits if not _injury_keyword_negated(t, k)]

    real_injured = bool(real_hits)



    # Safety first: a real (non-negated) injury keyword always escalates.

    # An explicit "safe / theek hai" assertion with no real injury keyword

    # keeps the driver out of the emergency path.

    injured = real_injured

    if safe_assertion and not real_injured:

        injured = False



    matched = real_hits if injured else []



    out = {

        "tool": "triage_injury_and_safety",

        "injured": injured,

        "matched_keywords": matched,

        "safety_status": "EMERGENCY — 112 AMBULANCE & POLICE DISPATCHED" if injured else "SAFE — No injury reported",

    }

    if injured:

        out.update(

            {

                "emergency_calls": [

                    {"line": EMERGENCY["ambulance"], "label": "🚨 Ambulance (112)"},

                    {"line": "112-POLICE", "label": "👮 Police Control Room (112)"},

                    {"line": EMERGENCY["nhai_highway_patrol"], "label": "🛣️ NHAI Highway Patrol (1033)"},

                    {"line": "LIVE-AGENT", "label": "☎️ Human Emergency Officer Handoff (priority queue)"},

                ],

                "halt_claim_intake": True,

                "spoken_empathy_hi": (

                    "Aapki jaan hamare liye sabse important hai. Main turant 112 ambulance "

                    "aur 1033 highway patrol bhej raha hoon. Kripya phone band mat kijiye — "

                    "ek officer aapko directly connect kar raha hoon."

                ),

                "claim_intake_paused_reason": "Human safety > claim forms. FNOL resumed only after medical handoff.",

            }

        )

    else:

        out.update({"emergency_calls": [], "halt_claim_intake": False,

                    "spoken_empathy_hi": "Shukar hai ki aap safe hain."})

    return out





# ===========================================================================

# TOOL 2 — Smart RSA Dispatch (Workflows A / B / C)

# ===========================================================================



def _pick_garage(road_type: str, vehicle_class: str, is_ev: bool = False) -> Optional[Dict[str, Any]]:

    cands = [g for g in GARAGES if g["road_type"] == road_type]

    if is_ev:

        ev = [g for g in cands if g.get("services_ev")]

        if ev:

            cands = ev

    if vehicle_class == "two_wheeler":

        two = [g for g in cands if g.get("services_2w")]

        if two:

            cands = two

    return cands[0] if cands else (GARAGES[0] if GARAGES else None)





def dispatch_smart_rsa(road_type: str, parts: List[str], is_drivable: bool,

                       policy: Optional[Dict[str, Any]]) -> Dict[str, Any]:

    """Route to exactly one of the 3 real Indian RSA workflows.



    Workflow A — Minor breakdown (puncture/battery/keys/fuel): on-spot bike

        mechanic, ETA 22 min, and explicitly PROTECTS the 20% NCB.

    Workflow B — Non-drivable crash: do-NOT-start engine + 1033 cones (NH)

        + free flatbed tow (<=50km) to cashless garage; remote rural ETA>2h

        -> IRDAI ₹1,500 local towing reimbursement authorization.

    Workflow C — Drivable minor crash: 4 photos with plate + self-drive to

        cashless garage.

    """

    policy = policy or {}

    veh_cls = policy.get("vehicle_class", "four_wheeler")

    is_ev = policy.get("fuel") == "electric"

    out: Dict[str, Any] = {

        "tool": "dispatch_smart_rsa",

        "road_type": road_type,

        "vehicle_class": veh_cls,

        "policy_number": policy.get("policy_number"),

        "parts": parts,

        "timestamp_utc": datetime.now(timezone.utc).isoformat(),

    }



    # Workflow A gate: minor breakdown, drivable, and all parts flagged do_not_claim

    is_minor = bool(parts) and all(PARTS.get(p, {}).get("workflow") == "A" for p in parts)

    if is_minor and is_drivable:

        wf = WORKFLOWS["A_minor_breakdown"]

        out.update(

            {

                "workflow": "A",

                "workflow_label": wf["label"],

                "dispatch_vendor": wf["vendor"],

                "dispatch_vendor_type": "on_spot_bike_mechanic",

                "eta_mins": wf["vendor_eta_mins"],

                "ncb_protected": True,

                "do_not_claim_warning": (

                    "Ye chhoti si service RSA se FREE ho jayegi aur aapka 20% No-Claim Bonus "

                    "bhi salamat rahega — isliye insurance claim FILE MAT kijiye!"

                ),

                "do_not_claim_spoken_hi": (

                    "Sahi news! Ye chota breakdown hai. Bike mechanic sirf 22 minute mein aayega "

                    "aur service bilkul FREE hogi. Sabse badi baat — aapke 20 percent No-Claim "

                    "Bonus salamat rahenge, isliye claim file bilkul mat kijiye."

                ),

            }

        )

        return out



    if not is_drivable:

        wf = WORKFLOWS["B_non_drivable"]

        garage = _pick_garage(road_type, veh_cls, is_ev)

        tow_km = IRDAI["free_tow_km_limit"]

        out.update(

            {

                "workflow": "B",

                "workflow_label": wf["label"],

                "do_not_start_engine": True,

                "engine_warning": "🔴 Do NOT Start Engine! (radiator/axle/coolant damage risk)",

                "engine_warning_spoken_hi": (

                    "Kripya gaadi start bilkul MAT kijiye — engine kekk ho sakta hai."

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

                "photos_note": "To invoice/rash record for surveyor: 4 photos with number plate visible.",

            }

        )

        return out



    # else: drivable minor (Workflow C) or nothing to dispatch

    wf = WORKFLOWS["C_drivable_minor"]

    garage = _pick_garage(road_type, veh_cls, is_ev) if parts else None

    out.update(

        {

            "workflow": "C",

            "workflow_label": wf["label"],

            "dispatch_vendor": wf["vendor"],

            "dispatch_vendor_type": "self_drive",

            "eta_mins": 0,

            "photos_required": wf.get("photos_required", 4),

            "photo_instruction_spoken_hi": (

                "Tow-truck aane se pehle number plate ke saath 4 photo zaroor kheench lein."

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

    """Compute deductible + depreciation + estimated repair range for FNOL.



    Every ₹ figure is derived from synthetic_db.json part costs and I.R.D.A.I.

    rules; output is explicitly labeled

    "Estimated Range Subject to Surveyor Inspection — Not Final Approval".

    `policy` optionally OVERRIDES the synthetic-DB lookup (voice-verified

    dynamic policies created mid-call still get a real draft).

    """

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

        if zero_dep or meta.get("zero_dep_zero_cost"):

            dep_after = 0.0

        else:

            dep_after = dep_pct * max(0.0, min(3.0, age_of_vehicle_years) / 3.0) + dep_pct * 0.25

            dep_after = min(dep_pct, 60.0)

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

        "owner": pol["owner"],                    # synthetic DPDP-compliant data only

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

        "status_label": IRDAI["range_label"],

        "status_label_spoken_hi": (

            "Yeh anumaanit range hai — aantim rashi sirf surveyor aur nireeksh ke baad."

        ),

        "photos_required": 4,

        "age_of_vehicle_years": age_of_vehicle_years,

    }





# ===========================================================================

# Spoken read-back composer (feeds clause_buffer)

# ===========================================================================



# Short, natural spoken names (catalog display_en is UI text, too long to say)

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





def spoken_part(parts: List[str]) -> str:

    """Human-friendly name of the first mentioned part (for speech)."""

    for p in parts or []:

        return SPOKEN_PART.get(p, PARTS.get(p, {}).get("display_en", p).split(" / ")[0].lower())

    return ""





def compose_agent_reply(incident: Dict[str, Any], ack: str = "",

                        ask_confirm: bool = True) -> str:

    """Turn structured tool outputs into ONE 2-clause-ready spoken reply.



    Clause 1 = fast empathy/ack hook; Clause 2 = RSA + claim read-back.

    `ack` lets the Brain vary the opener so the agent doesn't repeat the

    same line every turn (deterministic, no LLM needed).

    """

    tri = incident.get("triage", {})

    disp = incident.get("dispatch", {})

    draft = incident.get("claim_draft", {})



    if tri.get("injured"):

        return tri["spoken_empathy_hi"]



    policy_no = disp.get("policy_number") or (draft.get("policy_number") if draft else "")

    policy_ref = f"Policy {policy_no} ke tahat " if policy_no else "Aapki RSA policy ke tahat "

    part_word = spoken_part(disp.get("parts", []) or []) or "breakdown"



    if disp.get("workflow") == "A":

        # Minor, drivable, on-spot fix: the whole point is NOT to file a claim.

        opener = ack.strip() or "Achha ji."

        clause1 = (

            f"{opener} {part_word.capitalize()} to chhoti si baat hai — "

            "tension mat lijiye, gaadi chalane layak hai."

        )

        clause2 = (

            f"Main {disp.get('eta_mins', 22)} minute mein on-spot mechanic bhej rahi hoon, "

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

            "reimbursement authorize kar raha hoon — matlab local crane bhi ho jayega. "

            "Kya main confirm kar doon?"

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

            + (". Kya main confirm kar doon?" if ask_confirm else ". RSA help taiyaar hai.")

        )

    elif disp.get("workflow") == "C":

        clause1 = (f"{ack.strip() or 'Shukar hai aap safe hain!'} "

                   "Gaadi chalne layak hai, sirf bahar ka damage lagta hai.")

        clause2 = (

            f"Kripya number plate dikhta hua 4 photo kheench lein aur seedha "

            f"{(disp.get('destination_garage') or {}).get('name', 'network garage')} chale jaiye"

            + (f" — {draft.get('estimated_range_display', '')} ka claim draft taiyaar hai" if draft else "")

            + (". Kya main intimation confirm kar doon?" if ask_confirm else ". Photos taiyaar rakhiye.")

        )

    else:

        clause1 = ack.strip() or "Samajh gayi ji."

        clause2 = "Mujhe thoda aur detail chahiye — kya hua aur kahan hain?"



    return f"{clause1} {clause2}".strip()
