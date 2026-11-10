"""
BimaVani — Multilingual Catalog & Native Response Templates (All 10 Gnani Languages)
====================================================================================
Supports every language offered by Gnani Prisma v2.5/v3.0 STT and Timbre v2.5/v3.0 TTS:
  - hi-IN : Hindi / Roman Hinglish
  - ta-IN : Tamil (தமிழ்)
  - te-IN : Telugu (తెలుగు)
  - kn-IN : Kannada (ಕನ್ನಡ)
  - ml-IN : Malayalam (മലയാളം)
  - bn-IN : Bengali (বাংলা)
  - mr-IN : Marathi (मराठी)
  - gu-IN : Gujarati (ગુજરાતી)
  - pa-IN : Punjabi (ਪੰਜਾਬੀ)
  - en-IN : English (India)

Every regional language template uses strictly native script & native vocabulary
(no stray Hindi or English words mixed in) so Timbre TTS speaks naturally and
callers always receive replies in the language they spoke.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

LANG_META: Dict[str, Dict[str, str]] = {
    "hi-IN": {"code": "hi-IN", "name": "Hindi / Hinglish", "native": "Hinglish (hi-IN)", "script": "latin_hi", "tts_voice": "Nalini"},
    "ta-IN": {"code": "ta-IN", "name": "Tamil", "native": "தமிழ் (ta-IN)", "script": "tamil", "tts_voice": "Asmita"},
    "te-IN": {"code": "te-IN", "name": "Telugu", "native": "తెలుగు (te-IN)", "script": "telugu", "tts_voice": "Suhana"},
    "kn-IN": {"code": "kn-IN", "name": "Kannada", "native": "ಕನ್ನಡ (kn-IN)", "script": "kannada", "tts_voice": "Saanvi"},
    "ml-IN": {"code": "ml-IN", "name": "Malayalam", "native": "മലയാളം (ml-IN)", "script": "malayalam", "tts_voice": "Reshma"},
    "bn-IN": {"code": "bn-IN", "name": "Bengali", "native": "বাংলা (bn-IN)", "script": "bengali", "tts_voice": "Kirra"},
    "mr-IN": {"code": "mr-IN", "name": "Marathi", "native": "मराठी (mr-IN)", "script": "devanagari", "tts_voice": "Zahira"},
    "gu-IN": {"code": "gu-IN", "name": "Gujarati", "native": "ગુજરાતી (gu-IN)", "script": "gujarati", "tts_voice": "Falak"},
    "pa-IN": {"code": "pa-IN", "name": "Punjabi", "native": "ਪੰਜਾਬੀ (pa-IN)", "script": "gurmukhi", "tts_voice": "Mehuli"},
    "en-IN": {"code": "en-IN", "name": "English (India)", "native": "English (en-IN)", "script": "latin_en", "tts_voice": "Kaveri"},
}

ALL_GNANI_LANGUAGES = tuple(LANG_META.keys())


def tts_voice_for_lang(lang: Optional[str], model: str = "timbre-v2.5") -> str:
    code = normalize_lang_code(lang) or "hi-IN"
    if (model or "").strip().lower() == "timbre-v3.0":
        return "Lakshmi"
    return LANG_META.get(code, LANG_META["hi-IN"])["tts_voice"]

SCRIPT_REGEX_BY_LANG: Dict[str, re.Pattern[str]] = {
    "ta-IN": re.compile(r"[\u0B80-\u0BFF]"),
    "te-IN": re.compile(r"[\u0C00-\u0C7F]"),
    "kn-IN": re.compile(r"[\u0C80-\u0CFF]"),
    "ml-IN": re.compile(r"[\u0D00-\u0D7F]"),
    "bn-IN": re.compile(r"[\u0980-\u09FF]"),
    "mr-IN": re.compile(r"[\u0900-\u097F]"),
    "gu-IN": re.compile(r"[\u0A80-\u0AFF]"),
    "pa-IN": re.compile(r"[\u0A00-\u0A7F]"),
}


def normalize_lang_code(lang: Optional[str]) -> Optional[str]:
    v = (lang or "").strip().lower()
    if not v or v in ("auto", "indic-auto"):
        return None
    mapping = {
        "hi": "hi-IN", "hi-in": "hi-IN", "hindi": "hi-IN", "hinglish": "hi-IN",
        "ta": "ta-IN", "ta-in": "ta-IN", "tamil": "ta-IN",
        "te": "te-IN", "te-in": "te-IN", "telugu": "te-IN",
        "kn": "kn-IN", "kn-in": "kn-IN", "kannada": "kn-IN",
        "ml": "ml-IN", "ml-in": "ml-IN", "malayalam": "ml-IN",
        "bn": "bn-IN", "bn-in": "bn-IN", "bengali": "bn-IN", "bangla": "bn-IN",
        "mr": "mr-IN", "mr-in": "mr-IN", "marathi": "mr-IN",
        "gu": "gu-IN", "gu-in": "gu-IN", "gujarati": "gu-IN",
        "pa": "pa-IN", "pa-in": "pa-IN", "punjabi": "pa-IN",
        "en": "en-IN", "en-in": "en-IN", "english": "en-IN",
    }
    if v in mapping:
        return mapping[v]
    prefix = v.split("-", 1)[0]
    return mapping.get(prefix)


# ---------------------------------------------------------------------------
# Localized vehicle parts & garage names across all 10 languages
# ---------------------------------------------------------------------------

PARTS_I18N: Dict[str, Dict[str, str]] = {
    "hi-IN": {
        "radiator": "radiator", "bumper": "bumper", "headlight": "headlight",
        "side_mirror": "side mirror", "axle": "axle", "puncture": "puncture",
        "battery": "battery", "front_guard": "front guard", "clutch_wire": "clutch wire",
        "fuel_out": "petrol khatam", "ev_system_stuck": "EV system",
        "_default": "breakdown",
    },
    "ta-IN": {
        "radiator": "ரேடியேட்டர்", "bumper": "பம்பர்", "headlight": "ஹெட்லைட்",
        "side_mirror": "சைடு மிரர்", "axle": "ஆக்ஸில்", "puncture": "டயர் பஞ்சர்",
        "battery": "பேட்டரி", "front_guard": "முன் கார்டு", "clutch_wire": "கிளட்ச் ஒயர்",
        "fuel_out": "பெட்ரோல் காலி", "ev_system_stuck": "மின்கல அமைப்பு",
        "_default": "வாகன பழுது",
    },
    "te-IN": {
        "radiator": "రేడియేటర్", "bumper": "బంపర్", "headlight": "హెడ్‌లైట్",
        "side_mirror": "సైడ్ మిర్రర్", "axle": "యాక్సిల్", "puncture": "టైర్ పంక్చర్",
        "battery": "బ్యాటరీ", "front_guard": "ఫ్రంట్ గార్డ్", "clutch_wire": "క్లచ్ వైర్",
        "fuel_out": "ఇంధనం ఖాళీ", "ev_system_stuck": "ఈవీ సిస్టమ్",
        "_default": "వాహన సమస్య",
    },
    "kn-IN": {
        "radiator": "ರೇಡಿಯೇಟರ್", "bumper": "ಬಂಪರ್", "headlight": "ಹೆಡ್‌ಲೈಟ್",
        "side_mirror": "ಸೈಡ್ ಮಿರರ್", "axle": "ಆಕ್ಸಲ್", "puncture": "ಟೈರ್ ಪಂಕ್ಚರ್",
        "battery": "ಬ್ಯಾಟರಿ", "front_guard": "ಫ್ರಂಟ್ ಗಾರ್ಡ್", "clutch_wire": "ಕ್ಲಚ್ ವೈರ್",
        "fuel_out": "ಇಂಧನ ಖಾಲಿ", "ev_system_stuck": "ಇವಿ ಸಿಸ್ಟಮ್",
        "_default": "ವಾಹನ ದೋಷ",
    },
    "ml-IN": {
        "radiator": "റേഡിയേറ്റർ", "bumper": "ബമ്പർ", "headlight": "ഹെഡ്‌ലൈറ്റ്",
        "side_mirror": "സൈഡ് മിറർ", "axle": "ആക്സിൽ", "puncture": "ടയർ പഞ്ചർ",
        "battery": "ബാറ്ററി", "front_guard": "ഫ്രണ്ട് ഗാർഡ്", "clutch_wire": "ക്ലച്ച് വയർ",
        "fuel_out": "ഇന്ധനം തീർന്നു", "ev_system_stuck": "ഇവി സിസ്റ്റം",
        "_default": "വാഹന തകരാറ്",
    },
    "bn-IN": {
        "radiator": "রেডিয়েটর", "bumper": "বাম্পার", "headlight": "হেডলাইট",
        "side_mirror": "সাইড মিরর", "axle": "অ্যাক্সেল", "puncture": "টায়ার পাংচার",
        "battery": "ব্যাটারি", "front_guard": "ফ্রন্ট গার্ড", "clutch_wire": "ক্লাচ ওয়্যার",
        "fuel_out": "জ্বালানি শেষ", "ev_system_stuck": "ইভি সিস্টেম",
        "_default": "গাড়ির সমস্যা",
    },
    "mr-IN": {
        "radiator": "रेडिएटर", "bumper": "बंपर", "headlight": "हेडलाईट",
        "side_mirror": "साईड मिरर", "axle": "ॲक्सल", "puncture": "टायर पंक्चर",
        "battery": "बॅटरी", "front_guard": "फ्रंट गार्ड", "clutch_wire": "क्लच वायर",
        "fuel_out": "इंधन संपले", "ev_system_stuck": "ईव्ही सिस्टीम",
        "_default": "वाहनाचा बिघाड",
    },
    "gu-IN": {
        "radiator": "રેડિએટર", "bumper": "બમ્પર", "headlight": "હેડલાઇટ",
        "side_mirror": "સાઇડ મિરર", "axle": "એક્સલ", "puncture": "ટાયર પંચર",
        "battery": "બેટરી", "front_guard": "ફ્રન્ટ ગાર્ડ", "clutch_wire": "ક્લચ વાયર",
        "fuel_out": "પેટ્રોલ પૂરું", "ev_system_stuck": "ઇવી સિસ્ટમ",
        "_default": "વાહનની ખામી",
    },
    "pa-IN": {
        "radiator": "ਰੇਡੀਏਟਰ", "bumper": "ਬੰਪਰ", "headlight": "ਹੈੱਡਲਾਈਟ",
        "side_mirror": "ਸਾਈਡ ਮਿਰਰ", "axle": "ਐਕਸਲ", "puncture": "ਟਾਇਰ ਪੰਕਚਰ",
        "battery": "ਬੈਟਰੀ", "front_guard": "ਫਰੰਟ ਗਾਰਡ", "clutch_wire": "ਕਲੱਚ ਵਾਇਰ",
        "fuel_out": "ਤੇਲ ਮੁੱਕ ਗਿਆ", "ev_system_stuck": "ਈਵੀ ਸਿਸਟਮ",
        "_default": "ਗੱਡੀ ਦੀ ਖਰਾਬੀ",
    },
    "en-IN": {
        "radiator": "radiator", "bumper": "bumper", "headlight": "headlight",
        "side_mirror": "side mirror", "axle": "axle", "puncture": "tyre puncture",
        "battery": "battery", "front_guard": "front guard", "clutch_wire": "clutch wire",
        "fuel_out": "fuel empty", "ev_system_stuck": "EV system",
        "_default": "breakdown",
    },
}

GARAGES_I18N: Dict[str, Dict[str, str]] = {
    "ta-IN": {
        "g_karnal_motors": "கர்னால் மோட்டார்ஸ் மையம்",
        "g_karol_bagh_2w": "கரோல் பாக் இருசக்கர வாகன மையம்",
        "g_meerut_2w": "மீரட் இருசக்கர வாகன மையம்",
        "g_panipat_motors": "பானிபட் மோட்டார் வொர்க்ஸ் மையம்",
        "g_chennai_ev": "சென்னை மின்சார வாகன பராமரிப்பு மையம்",
        "g_delhi_motors": "டெல்லி என்.சி.ஆர். கேஷ்லெஸ் வாகன மையம்",
        "g_rural_karnal_link": "கர்னால் லிங்க் ரோடு பழுதுபார்க்கும் மையம்",
        "g_rural_panipat_4w": "பானிபட் கிராமப்புற பழுதுபார்க்கும் மையம்",
        "_default": "அங்கீகரிக்கப்பட்ட பழுதுபார்க்கும் மையம்",
    },
    "te-IN": {
        "g_karnal_motors": "కర్నాల్ మోటార్స్ కేంద్రం",
        "g_karol_bagh_2w": "కరోల్ బాగ్ ద్విచక్ర వాహన కేంద్రం",
        "g_meerut_2w": "మీరట్ ద్విచక్ర వాహన కేంద్రం",
        "g_panipat_motors": "పానిపట్ మోటార్ వర్క్స్ కేంద్రం",
        "g_chennai_ev": "చెన్నై ఈవీ వాహన సేవా కేంద్రం",
        "g_delhi_motors": "ఢిల్లీ ఎన్.సి.ఆర్. క్యాష్‌లెస్ ఆటో కేంద్రం",
        "g_rural_karnal_link": "కర్నాల్ లింక్ రోడ్ వర్క్‌షాప్",
        "g_rural_panipat_4w": "పానిపట్ గ్రామీణ మోటార్ వర్క్‌షాప్",
        "_default": "అధీకృత క్యాష్‌లెస్ గ్యారేజ్",
    },
    "kn-IN": {
        "g_karnal_motors": "ಕರ್ನಾಲ್ ಮೋಟಾರ್ಸ್ ಕೇಂದ್ರ",
        "g_karol_bagh_2w": "ಕರೋಲ್ ಬಾಗ್ ದ್ವಿಚಕ್ರ ವಾಹನ ಕೇಂದ್ರ",
        "g_meerut_2w": "ಮೀರತ್ ದ್ವಿಚಕ್ರ ವಾಹನ ಕೇಂದ್ರ",
        "g_panipat_motors": "ಪಾಣಿಪತ್ ಮೋಟಾರ್ ವರ್ಕ್ಸ್ ಕೇಂದ್ರ",
        "g_chennai_ev": "ಚೆನ್ನೈ ಇವಿ ವಾಹನ ಸೇವಾ ಕೇಂದ್ರ",
        "g_delhi_motors": "ದೆಹಲಿ ಎನ್.ಸಿ.ಆರ್. ಕ್ಯಾಶ್‌ಲೆಸ್ ಆಟೋ ಕೇಂದ್ರ",
        "g_rural_karnal_link": "ಕರ್ನಾಲ್ ಲಿಂಕ್ ರೋಡ್ ವರ್ಕ್‌ಶಾಪ್",
        "g_rural_panipat_4w": "ಪಾಣಿಪತ್ ಗ್ರಾಮೀಣ ಮೋಟಾರ್ ವರ್ಕ್‌ಶಾಪ್",
        "_default": "ಅಧಿಕೃತ ಕ್ಯಾಶ್‌ಲೆಸ್ ಗ್ಯಾರೇಜ್",
    },
    "ml-IN": {
        "g_karnal_motors": "കർണാൽ മോട്ടോഴ്സ് കേന്ദ്രം",
        "g_karol_bagh_2w": "കരോൾ ബാഗ് ഇരുചക്ര വാഹന കേന്ദ്രം",
        "g_meerut_2w": "മീററ്റ് ഇരുചക്ര വാഹന കേന്ദ്രം",
        "g_panipat_motors": "പാനിപ്പത്ത് മോട്ടോർ വർക്ക്സ് കേന്ദ്രം",
        "g_chennai_ev": "ചെന്നൈ ഇവി വാഹന സേവന കേന്ദ്രം",
        "g_delhi_motors": "ഡൽഹി എൻ.സി.ആർ. കാഷ്‌ലെസ് ഓട്ടോ കേന്ദ്രം",
        "g_rural_karnal_link": "കർണാൽ ലിങ്ക് റോഡ് വർക്ക്‌ഷോപ്പ്",
        "g_rural_panipat_4w": "പാനിപ്പത്ത് ഗ്രാമീണ മോട്ടോർ വർക്ക്‌ഷോപ്പ്",
        "_default": "അംഗീകൃത കാഷ്‌ലെസ് ഗാരേജ്",
    },
    "bn-IN": {
        "g_karnal_motors": "কার্নাল মোটরস কেন্দ্র",
        "g_karol_bagh_2w": "করোল বাগ টু-হুইলার কেন্দ্র",
        "g_meerut_2w": "মিরাট টু-হুইলার কেন্দ্র",
        "g_panipat_motors": "পানিপথ মোটর ওয়ার্কস কেন্দ্র",
        "g_chennai_ev": "চেন্নাই ইভি সেবা কেন্দ্র",
        "g_delhi_motors": "দিল্লি এনসিআর ক্যাশলেস অটো হাব",
        "g_rural_karnal_link": "কার্নাল লিংক রোড ওয়ার্কশপ",
        "g_rural_panipat_4w": "পানিপথ গ্রামীণ মোটর ওয়ার্কশপ",
        "_default": "অনুমোদিত ক্যাশলেস গ্যারেজ",
    },
    "mr-IN": {
        "g_karnal_motors": "कर्नाल मोटर्स केंद्र",
        "g_karol_bagh_2w": "करोल बाग दुचाकी सेवा केंद्र",
        "g_meerut_2w": "मेरठ दुचाकी सेवा केंद्र",
        "g_panipat_motors": "पानिपत मोटार वर्क्स केंद्र",
        "g_chennai_ev": "चेन्नई ईव्ही सेवा केंद्र",
        "g_delhi_motors": "दिल्ली एनसीआर कॅशलेस ऑटो हब",
        "g_rural_karnal_link": "कर्नाल लिंक रोड कार्यशाळा",
        "g_rural_panipat_4w": "पानिपत ग्रामीण मोटार कार्यशाळा",
        "_default": "अधिकृत कॅशलेस गॅरेज",
    },
    "gu-IN": {
        "g_karnal_motors": "કરનાલ મોટર્સ કેન્દ્ર",
        "g_karol_bagh_2w": "કરોલ બાગ ટુ-વ્હીલર કેન્દ્ર",
        "g_meerut_2w": "મેરઠ ટુ-વ્હીલર કેન્દ્ર",
        "g_panipat_motors": "પાનીપત મોટર વર્ક્સ કેન્દ્ર",
        "g_chennai_ev": "ચેન્નઈ ઇવી સેવા કેન્દ્ર",
        "g_delhi_motors": "દિલ્હી એનસીઆર કેશલેસ ઓટો હબ",
        "g_rural_karnal_link": "કરનાલ લિંક રોડ વર્કશોપ",
        "g_rural_panipat_4w": "પાનીપત ગ્રામીણ મોટર વર્કશોપ",
        "_default": "અધિકૃત કેશલેસ ગેરેજ",
    },
    "pa-IN": {
        "g_karnal_motors": "ਕਰਨਾਲ ਮੋਟਰਜ਼ ਕੇਂਦਰ",
        "g_karol_bagh_2w": "ਕਰੋਲ ਬਾਗ਼ ਟੂ-ਵ੍ਹੀਲਰ ਕੇਂਦਰ",
        "g_meerut_2w": "ਮੇਰਠ ਟੂ-ਵ੍ਹੀਲਰ ਕੇਂਦਰ",
        "g_panipat_motors": "ਪਾਣੀਪਤ ਮੋਟਰ ਵਰਕਸ ਕੇਂਦਰ",
        "g_chennai_ev": "ਚੇਨਈ ਈਵੀ ਸੇਵਾ ਕੇਂਦਰ",
        "g_delhi_motors": "ਦਿੱਲੀ ਐਨਸੀਆਰ ਕੈਸ਼ਲੈੱਸ ਆਟੋ ਹੱਬ",
        "g_rural_karnal_link": "ਕਰਨਾਲ ਲਿੰਕ ਰੋਡ ਵਰਕਸ਼ਾਪ",
        "g_rural_panipat_4w": "ਪਾਣੀਪਤ ਪੇਂਡੂ ਮੋਟਰ ਵਰਕਸ਼ਾਪ",
        "_default": "ਅਧਿਕਾਰਤ ਕੈਸ਼ਲੈੱਸ ਗੈਰੇਜ",
    },
}


def localize_garage(garage: Optional[Dict[str, Any]], lang: str) -> str:
    if not garage:
        tbl = GARAGES_I18N.get(lang)
        return tbl["_default"] if tbl else "network garage"
    tbl = GARAGES_I18N.get(lang)
    if tbl:
        return tbl.get(garage.get("id", ""), tbl["_default"])
    return garage.get("name", "network garage")


def format_range(low: int, high: int, lang: str) -> str:
    if not high:
        zero_labels = {
            "hi-IN": "₹0 (RSA-covered minor job)",
            "en-IN": "₹0 (RSA-covered minor job)",
            "ta-IN": "₹0 (சாலை உதவி சிறிய பழுது)",
            "te-IN": "₹0 (రోడ్‌సైడ్ సహాయం చిన్న మరమ్మత్తు)",
            "kn-IN": "₹0 (ರಸ್ತೆಬದಿ ಸಹಾಯ ಸಣ್ಣ ದುರಸ್ತಿ)",
            "ml-IN": "₹0 (റോഡ്സൈഡ് സഹായ ചെറിയ അറ്റകുറ്റപ്പണി)",
            "bn-IN": "₹0 (সড়ক সহায়তা ছোট মেরামত)",
            "mr-IN": "₹0 (रस्ता मदत किरकोळ दुरुस्ती)",
            "gu-IN": "₹0 (રોડસાઇડ સહાય નાની મરામત)",
            "pa-IN": "₹0 (ਸੜਕ ਸਹਾਇਤਾ ਛੋਟੀ ਮੁਰੰਮਤ)",
        }
        return zero_labels.get(lang, "₹0")
    connectors = {
        "hi-IN": f"₹{low:,} se ₹{high:,}",
        "en-IN": f"₹{low:,} to ₹{high:,}",
        "ta-IN": f"₹{low:,} முதல் ₹{high:,} வரை",
        "te-IN": f"₹{low:,} నుండి ₹{high:,} వరకు",
        "kn-IN": f"₹{low:,} ರಿಂದ ₹{high:,}",
        "ml-IN": f"₹{low:,} മുതൽ ₹{high:,} വരെ",
        "bn-IN": f"₹{low:,} থেকে ₹{high:,}",
        "mr-IN": f"₹{low:,} ते ₹{high:,}",
        "gu-IN": f"₹{low:,} થી ₹{high:,}",
        "pa-IN": f"₹{low:,} ਤੋਂ ₹{high:,}",
    }
    return connectors.get(lang, f"₹{low:,} to ₹{high:,}")


# ---------------------------------------------------------------------------
# Full conversation templates for all 10 Gnani languages
# ---------------------------------------------------------------------------

STRINGS: Dict[str, Dict[str, Any]] = {
    "hi-IN": {
        "greet_1": "Namaste! Main BimaVani Roadside Helpline se bol rahi hoon.",
        "greet_2": "Sabse pehle batayein—kya aap aur gaadi mein baithe sabhi log safe hain, ya kisi ko chot lagi hai?",
        "empty_turn": "Aapki awaaz mujhe saaf nahi aayi ji. Mic check kijiye aur apni baat dobara boliye — main sun rahi hoon.",
        "empty_clause": "Ji, boliye main aapki madad kar rahi hoon.",
        "safe_empathy": "Shukar hai ki aap safe hain.",
        "inj_empathy": "Aapki jaan hamare liye sabse important hai. Main turant 112 ambulance aur 1033 highway patrol bhej rahi hoon. Kripya phone band mat kijiye — ek officer aapko directly connect kar rahi hoon.",
        "emg_c1": "Aapki jaan sabse pehle hai — main turant 112 ambulance aur 112 police bhej rahi hoon, kripya phone na kaatein.",
        "emg_c2": "Ek emergency officer aapko directly connect kar rahi hoon",
        "emg_c2_nh": ", aur 1033 highway patrol bhi aapke location par aa raha hai.",
        "emg_c2_tail": " Surakshit jagah par rahein, line par bane rahiye.",
        "pause": "Haan ji, main ruki hoon. Jab aap ready hon ya kuch badalna ho toh batayein.",
        "correct_prompt": "Ji bilkul, main sun rahi hoon — sahi details batayein, kya badalna hai?",
        "ack_first": "Shukar hai aap safe hain!",
        "acks": [
            "Theek hai ji, note kar liya.", "Achha ji, samajh gayi.", "Haan ji, bilkul.",
            "Samajh gayi ji, aage badhte hain.", "Theek hai, main samajh gayi.",
            "Boliye, main sun rahi hoon.", "Achha, aage badhte hain.", "Haan ji, note kar liya hai.",
        ],
        "safety_pol": "Bahut shukriya ji — policy {pol} note kar li hai. Ab bataiye, gaadi ko kya hua?",
        "safety_ok": "Bahut shukriya ji — aap sab safe hain, yahi sabse badi baat hai. Ab bataiye, gaadi ko kya hua?",
        "pol_note": "Policy {pol} note hai. ",
        "ask_part": "{pol_note}Samajh gayi ji — gaadi mein exact kya problem aa rahi hai? Kya radiator ya engine ka issue hai, tyre puncture hai, battery dead hai, ya bahar ka dent?",
        "ask_loc_minor": "{part_prefix}Koi baat nahi ji. Aap abhi city ke andar hain, ya highway par?",
        "road_qs": [
            "Aap abhi National Highway par hain, sheher ke road par, ya gaon wali link road par?",
            "Aap kahan hain — highway par, sheher ke andar, ya gaon ki link road par?",
            "Kahan par hua ye sab — NH par, city road par, ya rural link road par?",
        ],
        "ask_loc_lead": "{part_prefix}Achha, samajh gayi. ",
        "ask_confirm_q": " Kya main aapka insurance claim apply kar doon?",
        "ask_pols": [
            " Insurance claim lagane aur dispatch lock karne ke liye apna 4-digit policy number batayein.",
            " Claim aur dispatch lock karane ke liye apna char anka policy number de dijiye.",
            " Policy number batayein taaki claim aur dispatch dono confirm ho sakein.",
        ],
        "ask_incident": "Ji bataiye, gaadi ke saath kya hua hai — kya takleef aa rahi hai?",
        "done_a": "Dispatch already active hai ji — mechanic lagbhag {eta} minute mein pahunch raha hai. Claim file karne ki zaroorat nahi, aapka 20 percent No-Claim Bonus bilkul safe hai.",
        "done_bc": "{claim_ref}already lock ho chuka hai, {eta_ref}. Kya aapko koi detail change karni hai ya aur madad chahiye?",
        "done_claim_ref": "Claim {cid} ",
        "done_claim_default": "Aapka claim ",
        "done_eta_ref": "aur help lagbhag {eta} minute mein pahunch rahi hai",
        "done_garage_ref": "aur cashless garage details shared hain",
        "close_c1": "Confirm ho gaya ji — aapka insurance claim APPLY ho gaya aur roadside help DISPATCH ho gaya hai.",
        "close_c2": "Aapka RSA ticket aur FNOL claim card dono lock ho chuke hain.",
        "close_eta": " Help lagbhag {eta} minute mein aayegi. Aap chinta mat kijiye, safe rahiye ji.",
        "close_est": " Estimated repair {est} — final approval surveyor inspection ke baad hoga.",
        "tickets_locked": " Dono tickets lock kar diye gaye hain.",
        "mech_2w": "bike mechanic", "mech_4w": "RSA mechanic",
        "hooks": [
            r"Shukar hai[^.!?।]*[.!?।]", r"Theek hai[^.!?।]*[.!?।]", r"Samajh gay[ai][^.!?।]*[.!?।]",
            r"Aapki jaan[^.!?।]*[.!?।]", r"Bilkul[^.!?।]*[.!?।]", r"Achha ji[^.!?।]*[.!?।]",
            r"Haan ji[^.!?।]*[.!?।]", r"Namaste[^.!?।]*[.!?।]", r"Confirm ho gaya[^.!?।]*[.!?।]",
        ],
    },
    "en-IN": {
        "greet_1": "Hello! I am calling from BimaVani Roadside Assistance Helpline.",
        "greet_2": "First, please confirm—are you and everyone in the vehicle safe, or is anyone injured?",
        "empty_turn": "I could not hear you clearly. Please check your microphone and repeat—I am listening.",
        "empty_clause": "Yes, please go ahead—I am here to help you.",
        "safe_empathy": "Glad to hear everyone is safe.",
        "inj_empathy": "Your life is our top priority. I am immediately dispatching a 112 ambulance and 1033 highway patrol. Please stay on the line—I am connecting you directly to an emergency officer.",
        "emg_c1": "Your safety comes first—I am immediately dispatching a 112 ambulance and 112 police, please do not disconnect.",
        "emg_c2": "I am connecting you directly to an emergency officer",
        "emg_c2_nh": ", and 1033 highway patrol is also heading to your location.",
        "emg_c2_tail": " Please stay in a safe spot and remain on the call.",
        "pause": "Sure, I am holding on. Let me know whenever you are ready or if you want to change any detail.",
        "correct_prompt": "Of course, I am listening—please share the correct details and what you would like to update.",
        "ack_first": "Glad to hear you are safe!",
        "acks": ["Got it, noted.", "Understood.", "Sure, I have noted that.", "Alright, let us proceed."],
        "safety_pol": "Thank you—policy {pol} is noted. Now please tell me, what happened to the vehicle?",
        "safety_ok": "Thank you—knowing everyone is safe matters most. Now please tell me, what happened to the vehicle?",
        "pol_note": "Policy {pol} is noted. ",
        "ask_part": "{pol_note}Understood—what exact issue is the vehicle facing? Is it radiator or engine trouble, tyre puncture, dead battery, or exterior damage?",
        "ask_loc_minor": "{part_prefix}No worries. Are you currently inside the city or on a highway?",
        "road_qs": [
            "Are you currently on a National Highway, a city road, or a rural village link road?",
            "Where are you located—highway, city street, or village link road?",
        ],
        "ask_loc_lead": "{part_prefix}Got it. ",
        "ask_confirm_q": " Shall I go ahead and confirm your insurance claim and dispatch?",
        "ask_pols": [
            " Please share your 4-digit policy number so I can lock the claim draft and roadside dispatch.",
            " Could you give me your 4-digit policy number to confirm both claim and assistance?",
        ],
        "ask_incident": "Please tell me what happened to your vehicle and what assistance you need.",
        "done_a": "Assistance is already active—a technician will reach you in about {eta} minutes. No claim needs to be filed, keeping your 20 percent No-Claim Bonus completely safe.",
        "done_bc": "{claim_ref}is already confirmed, {eta_ref}. Would you like to update anything or need more help?",
        "done_claim_ref": "Claim {cid} ", "done_claim_default": "Your claim ",
        "done_eta_ref": "and help is arriving in about {eta} minutes",
        "done_garage_ref": "and cashless garage details are shared",
        "close_c1": "Confirmed—your insurance claim is submitted and roadside assistance has been dispatched.",
        "close_c2": "Both your RSA ticket and IRDAI claim draft are now locked.",
        "close_eta": " Help will arrive in approximately {eta} minutes. Please stay safe.",
        "close_est": " Estimated repair range is {est}, subject to final surveyor inspection.",
        "tickets_locked": " Both tickets are now confirmed.",
        "mech_2w": "two-wheeler mechanic", "mech_4w": "RSA technician",
        "hooks": [r"Hello[^.!?]*[.!?]", r"Glad to hear[^.!?]*[.!?]", r"Confirmed[^.!?]*[.!?]", r"Your safety[^.!?]*[.!?]"],
    },
    "ta-IN": {
        "greet_1": "வணக்கம்! நான் பீமாவாணி சாலை உதவி மையத்திலிருந்து பேசுகிறேன்.",
        "greet_2": "முதலில் சொல்லுங்கள்—நீங்களும் காரில் உள்ள அனைவரும் பாதுகாப்பாக இருக்கிறீர்களா, அல்லது யாருக்காவது காயம் ஏற்பட்டதா?",
        "empty_turn": "உங்கள் குரல் தெளிவாகக் கேட்கவில்லை. மைக் சரிபார்த்து மீண்டும் சொல்லுங்கள் — நான் கேட்டுக்கொண்டிருக்கிறேன்.",
        "empty_clause": "வணக்கம், சொல்லுங்கள்—நான் உங்களுக்கு உதவத் தயாராக இருக்கிறேன்.",
        "safe_empathy": "நல்லவேளை, நீங்கள் பாதுகாப்பாக இருக்கிறீர்கள்.",
        "inj_empathy": "உங்கள் உயிர் தான் எங்களுக்கு மிக முக்கியம். உடனடியாக 112 ஆம்புலன்ஸ் மற்றும் 1033 நெடுஞ்சாலை ரோந்துப் படையை அனுப்புகிறேன். தயவுசெய்து அழைப்பைத் துண்டிக்காதீர்கள் — அவசர உதவி அதிகாரியுடன் நேரடியாக இணைக்கிறேன்.",
        "emg_c1": "உங்கள் உயிர் தான் மிக முக்கியம் — உடனடியாக 112 ஆம்புலன்ஸ் மற்றும் 112 காவல்துறையை அனுப்புகிறேன், தயவுசெய்து அழைப்பைத் துண்டிக்காதீர்கள்.",
        "emg_c2": "அவசர உதவி அதிகாரியுடன் உங்களை நேரடியாக இணைக்கிறேன்",
        "emg_c2_nh": ", மேலும் 1033 நெடுஞ்சாலை ரோந்துப் படையும் உங்கள் இடத்திற்கு வருகிறது.",
        "emg_c2_tail": " பாதுகாப்பான இடத்தில் இருங்கள், இணைப்பில் காத்திருங்கள்.",
        "pause": "சரிங்க, நான் காத்திருக்கிறேன். நீங்கள் தயாரானதும் அல்லது ஏதேனும் மாற்றம் செய்ய வேண்டுமானால் சொல்லுங்கள்.",
        "correct_prompt": "கண்டிப்பாக, நான் கேட்டுக்கொண்டிருக்கிறேன் — சரியான விவரங்களைச் சொல்லுங்கள், என்ன மாற்ற வேண்டும்?",
        "ack_first": "நல்லவேளை, நீங்கள் பாதுகாப்பாக இருக்கிறீர்கள்!",
        "acks": ["சரிங்க, குறித்துக்கொண்டேன்.", "புரிந்ததுங்க.", "ஆமாம், கண்டிப்பாக.", "புரிந்ததுங்க, அடுத்து பார்ப்போம்."],
        "safety_pol": "மிக்க நன்றி — பாலிசி எண் {pol} குறித்துக்கொள்ளப்பட்டது. இப்போது வண்டிக்கு என்ன ஆயிற்று என்று சொல்லுங்கள்?",
        "safety_ok": "மிக்க நன்றி — அனைவரும் பாதுகாப்பாக இருப்பதே மிக முக்கியம். இப்போது வண்டிக்கு என்ன ஆயிற்று என்று சொல்லுங்கள்?",
        "pol_note": "பாலிசி எண் {pol} குறித்துக்கொள்ளப்பட்டது. ",
        "ask_part": "{pol_note}புரிந்ததுங்க — வண்டியில் சரியாக என்ன பிரச்சினை? ரேடியேட்டர் அல்லது என்ஜின் பிரச்சினையா, டயர் பஞ்சரா, பேட்டரி பழுதா, அல்லது வெளிப்புற சேதமா?",
        "ask_loc_minor": "{part_prefix}கவலைப்படாதீர்கள். நீங்கள் இப்போது நகரத்திற்குள் இருக்கிறீர்களா அல்லது நெடுஞ்சாலையில் இருக்கிறீர்களா?",
        "road_qs": [
            "நீங்கள் இப்போது தேசிய நெடுஞ்சாலையில் இருக்கிறீர்களா, நகர சாலையில் இருக்கிறீர்களா, அல்லது கிராமத்து சாலையில் இருக்கிறீர்களா?",
            "நீங்கள் எங்கே இருக்கிறீர்கள் — நெடுஞ்சாலையா, நகரத்திற்குள்ளா, அல்லது கிராமப்புற சாலையா?",
        ],
        "ask_loc_lead": "{part_prefix}புரிந்ததுங்க. ",
        "ask_confirm_q": " உங்கள் காப்பீட்டு கோரிக்கையை பதிவு செய்யவா?",
        "ask_pols": [
            " காப்பீட்டு கோரிக்கை மற்றும் உதவியை உறுதி செய்ய உங்கள் 4 இலக்க பாலிசி எண்ணைச் சொல்லுங்கள்.",
            " உதவி மற்றும் கோரிக்கையைப் பதிவு செய்ய உங்கள் நான்கு இலக்க பாலிசி எண்ணைக் கூறுங்கள்.",
        ],
        "ask_incident": "சொல்லுங்கள், வண்டிக்கு என்ன ஆயிற்று — என்ன பிரச்சினை ஏற்பட்டுள்ளது?",
        "done_a": "உதவி ஏற்கனவே அனுப்பப்பட்டுவிட்டது — சுமார் {eta} நிமிடங்களில் பணியாளர் வந்துவிடுவார். காப்பீட்டு கோரிக்கை தேவையில்லை, உங்கள் 20 சதவீத போனஸ் சலுகை பாதுகாப்பாக உள்ளது.",
        "done_bc": "{claim_ref}ஏற்கனவே உறுதி செய்யப்பட்டுவிட்டது, {eta_ref}. வேறு ஏதேனும் விவரம் மாற்ற வேண்டுமா அல்லது கூடுதல் உதவி தேவையா?",
        "done_claim_ref": "கோரிக்கை எண் {cid} ", "done_claim_default": "உங்கள் கோரிக்கை ",
        "done_eta_ref": "உதவி சுமார் {eta} நிமிடங்களில் வந்துவிடும்",
        "done_garage_ref": "பழுதுபார்க்கும் மைய விவரங்கள் பகிரப்பட்டுள்ளன",
        "close_c1": "உறுதி செய்யப்பட்டது — உங்கள் காப்பீட்டு கோரிக்கை பதிவு செய்யப்பட்டு சாலை உதவி அனுப்பப்பட்டுவிட்டது.",
        "close_c2": "உங்கள் சாலை உதவி மற்றும் கோரிக்கை விவரங்கள் இரண்டும் உறுதி செய்யப்பட்டுவிட்டன.",
        "close_eta": " உதவி சுமார் {eta} நிமிடங்களில் வரும். கவலைப்படாதீர்கள், பாதுகாப்பாக இருங்கள்.",
        "close_est": " மதிப்பீட்டு பழுது செலவு {est} — ஆய்வாளர் பரிசோதனைக்குப் பிறகு இறுதி ஒப்புதல் வழங்கப்படும்.",
        "tickets_locked": " இரண்டு கோரிக்கைகளும் உறுதி செய்யப்பட்டுவிட்டன.",
        "mech_2w": "இருசக்கர வாகன பணியாளர்", "mech_4w": "சாலை உதவி பணியாளர்",
        "wf_a_body": "{ack} {part} என்பது சிறிய பிரச்சினைதான் — கவலைப்படாதீர்கள், வண்டி பாதுகாப்பாக உள்ளது. நான் {eta} நிமிடங்களில் நேரடியாக {mech} அனுப்புகிறேன், இந்த சாலை உதவி சேவை முற்றிலும் இலவசம், மேலும் காப்பீட்டு கோரிக்கை போடாததால் உங்கள் 20 சதவீத போனஸ் சலுகை பாதுகாப்பாக இருக்கும்",
        "wf_a_nh": ", மேலும் பாதுகாப்பிற்காக 1033 நெடுஞ்சாலை ரோந்துப் படைக்கும் தகவல் தெரிவிக்கப்பட்டுள்ளது",
        "wf_a_q": ". உதவியை உறுதி செய்யவா?", "wf_a_done": " — உதவி அனுப்பப்பட்டுவிட்டது.",
        "wf_b_rural": "{ack} {part} சேதமடைந்துள்ளதால் தயவுசெய்து வண்டியை ஸ்டார்ட் செய்யாதீர்கள். {pol_ref}அருகிலுள்ள பழுதுபார்க்கும் மையம் 2 மணி நேரத்திற்கு மேல் தொலைவில் உள்ளது — எனவே காப்பீட்டு ஒழுங்குமுறை விதிப்படி உள்ளூர் இழுவை வாகனத்திற்காக ₹{rural_amt} திருப்பித் தர அனுமதி அளிக்கிறேன் — அதாவது உள்ளூர் கிரேன் ஏற்பாடும் செய்துகொள்ளலாம்.",
        "wf_b_tow": "{ack} {part} சேதமடைந்துள்ளதால் தயவுசெய்து வண்டியை ஸ்டார்ட் செய்யாதீர்கள். {pol_ref}{garage} வழியாக இலவச இழுவை வாகனம் அனுப்புகிறோம்",
        "wf_b_nh": ", மேலும் 1033 நெடுஞ்சாலை ரோந்துப் படையும் பாதுகாப்பு கூம்புகள் அமைக்கிறது",
        "wf_b_est": ", மற்றும் {est} மதிப்பீட்டு கோரிக்கை நகல் தயாராக உள்ளது",
        "wf_c_body": "{ack} வண்டி ஓட்டக்கூடிய நிலையில் உள்ளது, வெளிப்புற சேதம் மட்டுமே தெரிகிறது. தயவுசெய்து எண் பலகை தெரியும்படி 4 புகைப்படங்கள் எடுத்து நேரடியாக {garage} செல்லுங்கள்",
        "wf_c_est": " — {est} மதிப்பீட்டு கோரிக்கை நகல் தயாராக உள்ளது",
        "wf_pol_known": "பாலிசி எண் {pol} கீழ் ", "wf_pol_unknown": "உங்கள் சாலை உதவி பாலிசி கீழ் ",
        "wf_confirm_q": " உறுதி செய்யவா?",
        "wf_fallback": "{ack} எனக்கு இன்னும் சிறிது விவரம் தேவை — வண்டியில் என்ன ஆனது, நீங்கள் இப்போது எங்கே இருக்கிறீர்கள்?",
        "hooks": [r"வணக்கம்[^.!?]*[.!?]", r"நல்லவேளை[^.!?]*[.!?]", r"மிக்க நன்றி[^.!?]*[.!?]", r"சரிங்க[^.!?]*[.!?]", r"புரிந்ததுங்க[^.!?]*[.!?]", r"உறுதி செய்யப்பட்டது[^.!?]*[.!?]", r"உங்கள் உயிர்[^.!?]*[.!?]"],
    },
    "te-IN": {
        "greet_1": "నమస్కారం! నేను బీమావాణి రోడ్‌సైడ్ సహాయ కేంద్రం నుండి మాట్లాడుతున్నాను.",
        "greet_2": "ముందుగా చెప్పండి—మీరు మరియు వాహనంలో ఉన్న అందరూ సురక్షితంగా ఉన్నారా, లేదా ఎవరికైనా గాయం అయిందా?",
        "empty_turn": "మీ గొంతు స్పష్టంగా వినిపించలేదు. దయచేసి మైక్ తనిఖీ చేసి మళ్లీ చెప్పండి — నేను వింటున్నాను.",
        "empty_clause": "నమస్కారం, చెప్పండి—నేను మీకు సహాయం చేయడానికి సిద్ధంగా ఉన్నాను.",
        "safe_empathy": "మంచిది, మీరు సురక్షితంగా ఉన్నారు.",
        "inj_empathy": "మీ ప్రాణం మాకు అత్యంత ముఖ్యం. వెంటనే 112 అంబులెన్స్ మరియు 1033 హైవే పెట్రోల్ పంపుతున్నాను. దయచేసి కాల్ కట్ చేయవద్దు — అత్యవసర అధికారికి నేరుగా కనెక్ట్ చేస్తున్నాను.",
        "emg_c1": "మీ ప్రాణం అన్నిటికంటే ముఖ్యం — వెంటనే 112 అంబులెన్స్ మరియు 112 పోలీసులను పంపుతున్నాను, దయచేసి ఫోన్ కట్ చేయకండి.",
        "emg_c2": "అత్యవసర సహాయ అధికారికి మిమ్మల్ని నేరుగా కనెక్ట్ చేస్తున్నాను",
        "emg_c2_nh": ", మరియు 1033 హైవే పెట్రోల్ కూడా మీ ప్రదేశానికి వస్తోంది.",
        "emg_c2_tail": " సురక్షితమైన ప్రదేశంలో ఉండండి, లైన్‌లో వేచి ఉండండి.",
        "pause": "సరేనండి, నేను వేచి ఉన్నాను. మీరు సిద్ధంగా ఉన్నప్పుడు లేదా ఏదైనా మార్పు చేయాలంటే చెప్పండి.",
        "correct_prompt": "తప్పకుండా, నేను వింటున్నాను — సరైన వివరాలు చెప్పండి, ఏమి మార్చాలి?",
        "ack_first": "మంచిది, మీరు సురక్షితంగా ఉన్నారు!",
        "acks": ["సరేనండి, నోట్ చేసుకున్నాను.", "అర్థమైందండి.", "అవును, తప్పకుండా.", "అర్థమైందండి, ముందుకు వెళ్దాం."],
        "safety_pol": "ధన్యవాదాలు — పాలసీ సంఖ్య {pol} నమోదు చేశాము. ఇప్పుడు వాహనానికి ఏమి జరిగిందో చెప్పండి?",
        "safety_ok": "ధన్యవాదాలు — అందరూ సురక్షితంగా ఉండటం చాలా ముఖ్యం. ఇప్పుడు వాహనానికి ఏమి జరిగిందో చెప్పండి?",
        "pol_note": "పాలసీ సంఖ్య {pol} నమోదు చేశాము. ",
        "ask_part": "{pol_note}అర్థమైందండి — వాహనంలో సరిగ్గా ఏ సమస్య వచ్చింది? రేడియేటర్ లేదా ఇంజిన్ సమస్యా, టైర్ పంక్చరా, బ్యాటరీ లోపా, లేక బయటి నష్టమా?",
        "ask_loc_minor": "{part_prefix}పరవాలేదు. మీరు ఇప్పుడు నగరంలో ఉన్నారా లేక హైవే మీద ఉన్నారా?",
        "road_qs": [
            "మీరు ఇప్పుడు జాతీయ రహదారి మీద ఉన్నారా, నగర రోడ్డు మీద ఉన్నారా, లేక గ్రామీణ రోడ్డు మీద ఉన్నారా?",
            "మీరు ఎక్కడ ఉన్నారు — హైవేపైన, నగరంలో, లేక గ్రామ రహదారిపైన?",
        ],
        "ask_loc_lead": "{part_prefix}అర్థమైందండి. ",
        "ask_confirm_q": " మీ బీమా క్లెయిమ్ నమోదు చేయమంటారా?",
        "ask_pols": [
            " బీమా క్లెయిమ్ మరియు సహాయం నిర్ధారించడానికి మీ 4 అంకెల పాలసీ నంబర్ చెప్పండి.",
            " సహాయం మరియు క్లెయిమ్ నమోదు చేయడానికి మీ నాలుగు అంకెల పాలసీ నంబర్ తెలియజేయండి.",
        ],
        "ask_incident": "చెప్పండి, వాహనానికి ఏమి జరిగింది — ఏ సమస్య వచ్చింది?",
        "done_a": "సహాయం ఇప్పటికే పంపబడింది — సుమారు {eta} నిమిషాల్లో టెక్నీషియన్ చేరుకుంటారు. క్లెయిమ్ వేయాల్సిన అవసరం లేదు, మీ 20 శాతం నో-క్లెయిమ్ బోనస్ సురక్షితంగా ఉంటుంది.",
        "done_bc": "{claim_ref}ఇప్పటికే ఖరారైంది, {eta_ref}. ఏదైనా వివరం మార్చాలా లేదా మరిన్ని వివరాలు కావాలా?",
        "done_claim_ref": "క్లెయిమ్ {cid} ", "done_claim_default": "మీ క్లెయిమ్ ",
        "done_eta_ref": "సహాయం సుమారు {eta} నిమిషాల్లో చేరుకుంటుంది",
        "done_garage_ref": "క్యాష్‌లెస్ గ్యారేజ్ వివరాలు పంపబడ్డాయి",
        "close_c1": "ఖరారైంది — మీ బీమా క్లెయిమ్ నమోదు చేయబడింది మరియు రోడ్‌సైడ్ సహాయం పంపబడింది.",
        "close_c2": "మీ సహాయం మరియు క్లెయిమ్ వివరాలు రెండూ నిర్ధారించబడ్డాయి.",
        "close_eta": " సహాయం సుమారు {eta} నిమిషాల్లో వస్తుంది. ఆందోళన పడకండి, సురక్షితంగా ఉండండి.",
        "close_est": " అంచనా మరమ్మత్తు వ్యయం {est} — సర్వేయర్ పరిశీలన తర్వాత తుది ఆమోదం ఉంటుంది.",
        "tickets_locked": " రెండు అభ్యర్థనలు ఖరారయ్యాయి.",
        "mech_2w": "ద్విచక్ర వాహన మెకానిక్", "mech_4w": "రోడ్‌సైడ్ సహాయ టెక్నీషియన్",
        "wf_a_body": "{ack} {part} చిన్న సమస్య మాత్రమే — కంగారు పడకండి, వాహనం సురక్షితంగా ఉంది. నేను {eta} నిమిషాల్లో మీ వద్దకు {mech} పంపుతున్నాను, ఈ సేవ పూర్తిగా ఉచితం, మరియు క్లెయిమ్ వేయనందున మీ 20 శాతం బోనస్ సురక్షితంగా ఉంటుంది",
        "wf_a_nh": ", మరియు భద్రత కోసం 1033 హైవే పెట్రోల్‌కు కూడా సమాచారం ఇచ్చాము",
        "wf_a_q": ". సహాయం పంపి నిర్ధారించమంటారా?", "wf_a_done": " — సహాయం పంపబడింది.",
        "wf_b_rural": "{ack} {part} దెబ్బతిన్నందున దయచేసి వాహనం స్టార్ట్ చేయకండి. {pol_ref}దగ్గరలోని వర్క్‌షాప్ 2 గంటల కంటే ఎక్కువ దూరంలో ఉంది — కాబట్టి నిబంధనల ప్రకారం స్థానిక క్రేన్/టోయింగ్ కోసం ₹{rural_amt} రీయింబర్స్‌మెంట్ ఆమోదిస్తున్నాను.",
        "wf_b_tow": "{ack} {part} దెబ్బతిన్నందున దయచేసి వాహనం స్టార్ట్ చేయకండి. {pol_ref}{garage} నుండి ఉచిత టోయింగ్ వాహనం పంపుతున్నాము",
        "wf_b_nh": ", అలాగే 1033 హైవే పెట్రోల్ కూడా భద్రతా ఏర్పాట్లు చేస్తోంది",
        "wf_b_est": ", మరియు {est} అంచనా క్లెయిమ్ పత్రం సిద్ధంగా ఉంది",
        "wf_c_body": "{ack} వాహనం నడపగల స్థితిలో ఉంది, బయటి నష్టం మాత్రమే కనిపిస్తోంది. దయచేసి నంబర్ ప్లేట్ కనిపించేలా 4 ఫోటోలు తీసి నేరుగా {garage} కు వెళ్ళండి",
        "wf_c_est": " — {est} అంచనా క్లెయిమ్ పత్రం సిద్ధంగా ఉంది",
        "wf_pol_known": "పాలసీ సంఖ్య {pol} ప్రకారం ", "wf_pol_unknown": "మీ రోడ్‌సైడ్ పాలసీ ప్రకారం ",
        "wf_confirm_q": " నిర్ధారించమంటారా?",
        "wf_fallback": "{ack} నాకు మరికొంత సమాచారం కావాలి — వాహనానికి ఏమి జరిగింది మరియు మీరు ఎక్కడ ఉన్నారు?",
        "hooks": [r"నమస్కారం[^.!?]*[.!?]", r"మంచిది[^.!?]*[.!?]", r"ధన్యవాదాలు[^.!?]*[.!?]", r"సరేనండి[^.!?]*[.!?]", r"అర్థమైందండి[^.!?]*[.!?]", r"ఖరారైంది[^.!?]*[.!?]"],
    },
    "kn-IN": {
        "greet_1": "ನಮಸ್ಕಾರ! ನಾನು ಬೀಮಾವಾಣಿ ರಸ್ತೆಬದಿ ಸಹಾಯ ಕೇಂದ್ರದಿಂದ ಮಾತನಾಡುತ್ತಿದ್ದೇನೆ.",
        "greet_2": "ಮೊದಲು ತಿಳಿಸಿ—ನೀವು ಮತ್ತು ವಾಹನದಲ್ಲಿರುವ ಎಲ್ಲರೂ ಸುರಕ್ಷಿತವಾಗಿದ್ದೀರಾ, ಅಥವಾ ಯಾರಿಗಾದರೂ ಗಾಯವಾಗಿದೆಯೇ?",
        "empty_turn": "ನಿಮ್ಮ ಧ್ವನಿ ಸ್ಪಷ್ಟವಾಗಿ ಕೇಳಿಸಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಮೈಕ್ ಪರಿಶೀಲಿಸಿ ಮತ್ತೊಮ್ಮೆ ಹೇಳಿ — ನಾನು ಕೇಳಿಸಿಕೊಳ್ಳುತ್ತಿದ್ದೇನೆ.",
        "empty_clause": "ನಮಸ್ಕಾರ, ಹೇಳಿ—ನಾನು ನಿಮಗೆ ಸಹಾಯ ಮಾಡಲು ಸಿದ್ಧನಿದ್ದೇನೆ.",
        "safe_empathy": "ಒಳ್ಳೆಯದು, ನೀವು ಸುರಕ್ಷಿತವಾಗಿದ್ದೀರಿ.",
        "inj_empathy": "ನಿಮ್ಮ ಜೀವ ನಮಗೆ ಅತ್ಯಂತ ಮುಖ್ಯ. ತಕ್ಷಣವೇ 112 ಆಂಬ್ಯುಲೆನ್ಸ್ ಮತ್ತು 1033 ಹೆದ್ದಾರಿ ಗಸ್ತು ತಂಡವನ್ನು ಕಳುಹಿಸುತ್ತಿದ್ದೇನೆ. ದಯವಿಟ್ಟು ಕರೆ ಕಟ್ ಮಾಡಬೇಡಿ — ತುರ್ತು ಅಧಿಕಾರಿಗೆ ನೇರವಾಗಿ ಸಂಪರ್ಕಿಸುತ್ತಿದ್ದೇನೆ.",
        "emg_c1": "ನಿಮ್ಮ ಸುರಕ್ಷತೆಯೇ ಮೊದಲ ಆದ್ಯತೆ — ತಕ್ಷಣವೇ 112 ಆಂಬ್ಯುಲೆನ್ಸ್ ಮತ್ತು 112 ಪೊಲೀಸರನ್ನು ಕಳುಹಿಸುತ್ತಿದ್ದೇನೆ, ದಯವಿಟ್ಟು ಕರೆ ಕಟ್ ಮಾಡಬೇಡಿ.",
        "emg_c2": "ತುರ್ತು ಸಹಾಯ ಅಧಿಕಾರಿಗೆ ನಿಮ್ಮನ್ನು ನೇರವಾಗಿ ಸಂಪರ್ಕಿಸುತ್ತಿದ್ದೇನೆ",
        "emg_c2_nh": ", ಮತ್ತು 1033 ಹೆದ್ದಾರಿ ಗಸ್ತು ತಂಡವೂ ನಿಮ್ಮ ಸ್ಥಳಕ್ಕೆ ಬರುತ್ತಿದೆ.",
        "emg_c2_tail": " ಸುರಕ್ಷಿತ ಸ್ಥಳದಲ್ಲಿರಿ, ಕರೆಯಲ್ಲಿಯೇ ಇರಿ.",
        "pause": "ಸರಿ, ನಾನು ಕಾಯುತ್ತಿದ್ದೇನೆ. ನೀವು ಸಿದ್ಧರಾದಾಗ ಅಥವಾ ಏನಾದರೂ ಬದಲಾಯಿಸಬೇಕಾದರೆ ತಿಳಿಸಿ.",
        "correct_prompt": "ಖಂಡಿತ, ನಾನು ಕೇಳಿಸಿಕೊಳ್ಳುತ್ತಿದ್ದೇನೆ — ಸರಿಯಾದ ವಿವರಗಳನ್ನು ತಿಳಿಸಿ, ಏನು ಬದಲಾಯಿಸಬೇಕು?",
        "ack_first": "ಒಳ್ಳೆಯದು, ನೀವು ಸುರಕ್ಷಿತವಾಗಿದ್ದೀರಿ!",
        "acks": ["ಸರಿ, ಗುರುತು ಮಾಡಿಕೊಂಡಿದ್ದೇನೆ.", "ಅರ್ಥವಾಯಿತು.", "ಹೌದು, ಖಂಡಿತ.", "ಅರ್ಥವಾಯಿತು, ಮುಂದೆ ನೋಡೋಣ."],
        "safety_pol": "ಧನ್ಯವಾದಗಳು — ಪಾಲಿಸಿ ಸಂಖ್ಯೆ {pol} ದಾಖಲಿಸಲಾಗಿದೆ. ಈಗ ವಾಹನಕ್ಕೆ ಏನಾಯಿತು ಎಂದು ತಿಳಿಸಿ?",
        "safety_ok": "ಧನ್ಯವಾದಗಳು — ಎಲ್ಲರೂ ಸುರಕ್ಷಿತವಾಗಿರುವುದೇ ಅತ್ಯಂತ ಮುಖ್ಯ. ಈಗ ವಾಹನಕ್ಕೆ ಏನಾಯಿತು ಎಂದು ತಿಳಿಸಿ?",
        "pol_note": "ಪಾಲಿಸಿ ಸಂಖ್ಯೆ {pol} ದಾಖಲಿಸಲಾಗಿದೆ. ",
        "ask_part": "{pol_note}ಅರ್ಥವಾಯಿತು — ವಾಹನದಲ್ಲಿ ನಿಖರವಾಗಿ ಏನು ತೊಂದರೆ ಇದೆ? ರೇಡಿಯೇಟರ್ ಅಥವಾ ಎಂಜಿನ್ ಸಮಸ್ಯೆಯೇ, ಟೈರ್ ಪಂಕ್ಚರಾ, ಬ್ಯಾಟರಿ ಸಮಸ್ಯೆಯೇ, ಅಥವಾ ಹೊರಭಾಗದ ಹಾನಿಯೇ?",
        "ask_loc_minor": "{part_prefix}ಚಿಂತಿಸಬೇಡಿ. ನೀವು ಈಗ ನಗರದೊಳಗಿದ್ದೀರಾ ಅಥವಾ ಹೆದ್ದಾರಿಯಲ್ಲಿದ್ದೀರಾ?",
        "road_qs": [
            "ನೀವು ಈಗ ರಾಷ್ಟ್ರೀಯ ಹೆದ್ದಾರಿಯಲ್ಲಿದ್ದೀರಾ, ನಗರದ ರಸ್ತೆಯಲ್ಲಿದ್ದೀರಾ, ಅಥವಾ ಗ್ರಾಮೀಣ ರಸ್ತೆಯಲ್ಲಿದ್ದೀರಾ?",
            "ನೀವು ಎಲ್ಲಿದ್ದೀರಿ — ಹೆದ್ದಾರಿಯಲ್ಲಿ, ನಗರದಲ್ಲಿ, ಅಥವಾ ಹಳ್ಳಿಯ ರಸ್ತೆಯಲ್ಲಿ?",
        ],
        "ask_loc_lead": "{part_prefix}ಅರ್ಥವಾಯಿತು. ",
        "ask_confirm_q": " ನಿಮ್ಮ ವಿಮಾ ಕ್ಲೈಮ್ ಅನ್ನು ದೃಢೀಕರಿಸಲೇ?",
        "ask_pols": [
            " ವಿಮಾ ಕ್ಲೈಮ್ ಮತ್ತು ಸಹಾಯವನ್ನು ದೃಢೀಕರಿಸಲು ನಿಮ್ಮ 4 ಅಂಕಿಯ ಪಾಲಿಸಿ ಸಂಖ್ಯೆಯನ್ನು ತಿಳಿಸಿ.",
            " ಸಹಾಯ ಮತ್ತು ಕ್ಲೈಮ್ ದಾಖಲಿಸಲು ನಿಮ್ಮ ನಾಲ್ಕು ಅಂಕಿಯ ಪಾಲಿಸಿ ಸಂಖ್ಯೆಯನ್ನು ಹೇಳಿ.",
        ],
        "ask_incident": "ಹೇಳಿ, ವಾಹನಕ್ಕೆ ಏನಾಗಿದೆ — ಯಾವ ತೊಂದರೆ ಎದುರಾಗಿದೆ?",
        "done_a": "ಸಹಾಯವನ್ನು ಈಗಾಗಲೇ ಕಳುಹಿಸಲಾಗಿದೆ — ಸುಮಾರು {eta} ನಿಮಿಷಗಳಲ್ಲಿ ತಂತ್ರಜ್ಞರು ಬರುತ್ತಾರೆ. ಕ್ಲೈಮ್ ಸಲ್ಲಿಸುವ ಅಗತ್ಯವಿಲ್ಲ, ನಿಮ್ಮ 20 ಶೇಕಡಾ ಬೋನಸ್ ಸುರಕ್ಷಿತವಾಗಿದೆ.",
        "done_bc": "{claim_ref}ಈಗಾಗಲೇ ದೃಢೀಕರಿಸಲ್ಪಟ್ಟಿದೆ, {eta_ref}. ಬೇರೆ ಏನಾದರೂ ಬದಲಾವಣೆ ಬೇಕೇ ಅಥವಾ ಹೆಚ್ಚುವರಿ ಸಹಾಯ ಬೇಕೇ?",
        "done_claim_ref": "ಕ್ಲೈಮ್ {cid} ", "done_claim_default": "ನಿಮ್ಮ ಕ್ಲೈಮ್ ",
        "done_eta_ref": "ಸಹಾಯ ಸುಮಾರು {eta} ನಿಮಿಷಗಳಲ್ಲಿ ತಲುಪುತ್ತದೆ",
        "done_garage_ref": "ಕ್ಯಾಶ್‌ಲೆಸ್ ಗ್ಯಾರೇಜ್ ವಿವರಗಳನ್ನು ಹಂಚಿಕೊಳ್ಳಲಾಗಿದೆ",
        "close_c1": "ದೃಢೀಕರಿಸಲಾಗಿದೆ — ನಿಮ್ಮ ವಿಮಾ ಕ್ಲೈಮ್ ದಾಖಲಾಗಿದೆ ಮತ್ತು ರಸ್ತೆಬದಿ ಸಹಾಯವನ್ನು ಕಳುಹಿಸಲಾಗಿದೆ.",
        "close_c2": "ನಿಮ್ಮ ಸಹಾಯ ಮತ್ತು ಕ್ಲೈಮ್ ವಿವರಗಳು ಎರಡೂ ಖಚಿತವಾಗಿವೆ.",
        "close_eta": " ಸಹಾಯ ಸುಮಾರು {eta} ನಿಮಿಷಗಳಲ್ಲಿ ಬರುತ್ತದೆ. ಚಿಂತಿಸಬೇಡಿ, ಸುರಕ್ಷಿತವಾಗಿರಿ.",
        "close_est": " ಅಂದಾಜು ದುರಸ್ತಿ ವೆಚ್ಚ {est} — ಪರಿಶೀಲಕರು ಪರಿಶೀಲಿಸಿದ ನಂತರ ಅಂತಿಮ ಅನುಮೋದನೆ ಸಿಗುತ್ತದೆ.",
        "tickets_locked": " ಎರಡೂ ವಿನಂತಿಗಳು ದೃಢೀಕರಿಸಲ್ಪಟ್ಟಿವೆ.",
        "mech_2w": "ದ್ವಿಚಕ್ರ ವಾಹನ ಮೆಕ್ಯಾನಿಕ್", "mech_4w": "ರಸ್ತೆಬದಿ ಸಹಾಯ ತಂತ್ರಜ್ಞ",
        "wf_a_body": "{ack} {part} ಸಣ್ಣ ತೊಂದರೆ ಮಾತ್ರ — ಚಿಂತಿಸಬೇಡಿ, ವಾಹನ ಸುರಕ್ಷಿತವಾಗಿದೆ. ನಾನು {eta} ನಿಮಿಷಗಳಲ್ಲಿ {mech} ಕಳುಹಿಸುತ್ತಿದ್ದೇನೆ, ಈ ಸೇವೆ ಸಂಪೂರ್ಣ ಉಚಿತ, ಮತ್ತು ಕ್ಲೈಮ್ ಸಲ್ಲಿಸದಿರುವುದರಿಂದ ನಿಮ್ಮ 20 ಶೇಕಡಾ ಬೋನಸ್ ಸುರಕ್ಷಿತವಾಗಿರುತ್ತದೆ",
        "wf_a_nh": ", ಹಾಗೂ ಸುರಕ್ಷತೆಗಾಗಿ 1033 ಹೆದ್ದಾರಿ ಗಸ್ತು ತಂಡಕ್ಕೂ ತಿಳಿಸಲಾಗಿದೆ",
        "wf_a_q": ". ಸಹಾಯ ಕಳುಹಿಸಿ ದೃಢೀಕರಿಸಲೇ?", "wf_a_done": " — ಸಹಾಯ ಕಳುಹಿಸಲಾಗಿದೆ.",
        "wf_b_rural": "{ack} {part} ಹಾನಿಗೊಳಗಾಗಿರುವುದರಿಂದ ದಯವಿಟ್ಟು ವಾಹನವನ್ನು ಸ್ಟಾರ್ಟ್ ಮಾಡಬೇಡಿ. {pol_ref}ಹತ್ತಿರದ ವರ್ಕ್‌ಶಾಪ್ 2 ಗಂಟೆಗಿಂತ ಹೆಚ್ಚು ದೂರದಲ್ಲಿದೆ — ಆದ್ದರಿಂದ ನಿಯಮದಂತೆ ಸ್ಥಳೀಯ ಕ್ರೇನ್/ಟೋಯಿಂಗ್‌ಗಾಗಿ ₹{rural_amt} ಮರುಪಾವತಿಗೆ ಅನುಮತಿ ನೀಡುತ್ತಿದ್ದೇನೆ.",
        "wf_b_tow": "{ack} {part} ಹಾನಿಗೊಳಗಾಗಿರುವುದರಿಂದ ದಯವಿಟ್ಟು ವಾಹನವನ್ನು ಸ್ಟಾರ್ಟ್ ಮಾಡಬೇಡಿ. {pol_ref}{garage} ಮೂಲಕ ಉಚಿತ ಟೋಯಿಂಗ್ ವಾಹನ ಕಳುಹಿಸುತ್ತಿದ್ದೇವೆ",
        "wf_b_nh": ", ಮತ್ತು 1033 ಹೆದ್ದಾರಿ ಗಸ್ತು ತಂಡವೂ ಸುರಕ್ಷತಾ ವ್ಯವಸ್ಥೆ ಮಾಡುತ್ತಿದೆ",
        "wf_b_est": ", ಹಾಗೂ {est} ಅಂದಾಜು ಕ್ಲೈಮ್ ಕರಡು ಸಿದ್ಧವಾಗಿದೆ",
        "wf_c_body": "{ack} ವಾಹನ ಚಲಾಯಿಸಬಹುದಾದ ಸ್ಥಿತಿಯಲ್ಲಿದೆ, ಹೊರಭಾಗದ ಹಾನಿ ಮಾತ್ರ ಕಾಣುತ್ತಿದೆ. ದಯವಿಟ್ಟು ನಂಬರ್ ಪ್ಲೇಟ್ ಕಾಣಿಸುವಂತೆ 4 ಫೋಟೋಗಳನ್ನು ತೆಗೆದು ನೇರವಾಗಿ {garage} ಗೆ ಹೋಗಿ",
        "wf_c_est": " — {est} ಅಂದಾಜು ಕ್ಲೈಮ್ ಕರಡು ಸಿದ್ಧವಾಗಿದೆ",
        "wf_pol_known": "ಪಾಲಿಸಿ ಸಂಖ್ಯೆ {pol} ಅಡಿಯಲ್ಲಿ ", "wf_pol_unknown": "ನಿಮ್ಮ ರಸ್ತೆಬದಿ ಪಾಲಿಸಿ ಅಡಿಯಲ್ಲಿ ",
        "wf_confirm_q": " ದೃಢೀಕರಿಸಲೇ?",
        "wf_fallback": "{ack} ನನಗೆ ಇನ್ನೂ ಸ್ವಲ್ಪ ಮಾಹಿತಿ ಬೇಕು — ವಾಹನಕ್ಕೆ ಏನಾಗಿದೆ ಮತ್ತು ನೀವು ಎಲ್ಲಿದ್ದೀರಿ?",
        "hooks": [r"ನಮಸ್ಕಾರ[^.!?]*[.!?]", r"ಒಳ್ಳೆಯದು[^.!?]*[.!?]", r"ಧನ್ಯವಾದಗಳು[^.!?]*[.!?]", r"ಸರಿ[^.!?]*[.!?]", r"ಅರ್ಥವಾಯಿತು[^.!?]*[.!?]", r"ದೃಢೀಕರಿಸಲಾಗಿದೆ[^.!?]*[.!?]"],
    },
    "ml-IN": {
        "greet_1": "നമസ്കാരം! ഞാൻ ബീമാവാണി റോഡ്‌സൈഡ് സഹായ കേന്ദ്രത്തിൽ നിന്നാണ് സംസാരിക്കുന്നത്.",
        "greet_2": "ആദ്യം പറയൂ—നിങ്ങളും വാഹനത്തിലുള്ള എല്ലാവരും സുരക്ഷിതരാണോ, അതോ ആർക്കെങ്കിലും പരിക്കേറ്റോ?",
        "empty_turn": "നിങ്ങളുടെ ശബ്ദം വ്യക്തമായി കേട്ടില്ല. ദയവായി മൈക്ക് പരിശോധിച്ച് വീണ്ടും പറയൂ — ഞാൻ കേൾക്കുന്നുണ്ട്.",
        "empty_clause": "നമസ്കാരം, പറയൂ—നിങ്ങളെ സഹായിക്കാൻ ഞാൻ തയ്യാറാണ്.",
        "safe_empathy": "നല്ലത്, നിങ്ങൾ സുരക്ഷിതരാണ്.",
        "inj_empathy": "നിങ്ങളുടെ ജീവനാണ് ഞങ്ങൾക്ക് ഏറ്റവും പ്രധാനം. ഉടൻ തന്നെ 112 ആംബുലൻസും 1033 ഹൈവേ പട്രോളും അയക്കുന്നുണ്ട്. ദയവായി കോൾ കട്ട് ചെയ്യരുത് — അടിയന്തര ഉദ്യോഗസ്ഥനുമായി നേരിട്ട് ബന്ധിപ്പിക്കാം.",
        "emg_c1": "നിങ്ങളുടെ സുരക്ഷയാണ് ഏറ്റവും പ്രധാനം — ഉടൻ തന്നെ 112 ആംബുലൻസും 112 പോലീസും അയക്കുന്നുണ്ട്, ദയവായി ഫോൺ കട്ട് ചെയ്യരുത്.",
        "emg_c2": "അടിയന്തര സഹായ ഉദ്യോഗസ്ഥനുമായി നിങ്ങളെ നേരിട്ട് ബന്ധിപ്പിക്കുന്നു",
        "emg_c2_nh": ", കൂടാതെ 1033 ഹൈവേ പട്രോളും നിങ്ങളുടെ സ്ഥലത്തേക്ക് എത്തുന്നുണ്ട്.",
        "emg_c2_tail": " സുരക്ഷിതമായ സ്ഥലത്ത് നിൽക്കൂ, ലൈനിൽ തുടരൂ.",
        "pause": "ശരി, ഞാൻ കാത്തിരിക്കാം. തയ്യാറാകുമ്പോൾ അല്ലെങ്കിൽ എന്തെങ്കിലും മാറ്റം വേണമെങ്കിൽ പറയൂ.",
        "correct_prompt": "തീർച്ചയായും, ഞാൻ കേൾക്കുന്നുണ്ട് — ശരിയായ വിവരങ്ങൾ പറയൂ, എന്താണ് മാറ്റേണ്ടത്?",
        "ack_first": "നല്ലത്, നിങ്ങൾ സുരക്ഷിതരാണ്!",
        "acks": ["ശരി, രേഖപ്പെടുത്തി.", "മനസ്സിലായി.", "തീർച്ചയായും.", "മനസ്സിലായി, അടുത്തത് നോക്കാം."],
        "safety_pol": "നന്ദി — പോളിസി നമ്പർ {pol} രേഖപ്പെടുത്തി. ഇപ്പോൾ വാഹനത്തിന് എന്തുപറ്റി എന്ന് പറയൂ?",
        "safety_ok": "നന്ദി — എല്ലാവരും സുരക്ഷിതരായിരിക്കുകയാണ് ഏറ്റവും പ്രധാനം. ഇപ്പോൾ വാഹനത്തിന് എന്തുപറ്റി എന്ന് പറയൂ?",
        "pol_note": "പോളിസി നമ്പർ {pol} രേഖപ്പെടുത്തി. ",
        "ask_part": "{pol_note}മനസ്സിലായി — വാഹനത്തിൽ കൃത്യമായി എന്താണ് പ്രശ്നം? റേഡിയേറ്റർ അല്ലെങ്കിൽ എൻജിൻ തകരാറാണോ, ടയർ പഞ്ചറാണോ, ബാറ്ററി തകരാറാണോ, അതോ പുറംഭാഗത്തെ കേടുപാടാണോ?",
        "ask_loc_minor": "{part_prefix}വിഷമിക്കേണ്ട. നിങ്ങൾ ഇപ്പോൾ നഗരത്തിനുള്ളിലാണോ അതോ ഹൈവേയിലാണോ?",
        "road_qs": [
            "നിങ്ങൾ ഇപ്പോൾ ദേശീയപാതയിലാണോ, നഗരത്തിലെ റോഡിലാണോ, അതോ ഗ്രാമീണ റോഡിലാണോ?",
            "നിങ്ങൾ എവിടെയാണ് — ഹൈവേയിൽ, നഗരത്തിൽ, അതോ ഗ്രാമത്തിലെ റോഡിൽ?",
        ],
        "ask_loc_lead": "{part_prefix}മനസ്സിലായി. ",
        "ask_confirm_q": " നിങ്ങളുടെ ഇൻഷുറൻസ് ക്ലെയിം സ്ഥിരീകരിക്കട്ടെ?",
        "ask_pols": [
            " ഇൻഷുറൻസ് ക്ലെയിമും സഹായവും ഉറപ്പാക്കാൻ നിങ്ങളുടെ 4 അക്ക പോളിസി നമ്പർ പറയൂ.",
            " സഹായവും ക്ലെയിമും രേഖപ്പെടുത്താൻ നിങ്ങളുടെ നാലക്ക പോളിസി നമ്പർ നൽകൂ.",
        ],
        "ask_incident": "പറയൂ, വാഹനത്തിന് എന്താണ് സംഭവിച്ചത് — എന്താണ് തകരാറ്?",
        "done_a": "സഹായം ഇതിനകം അയച്ചിട്ടുണ്ട് — ഏകദേശം {eta} മിനിറ്റിനുള്ളിൽ ടെക്നീഷ്യൻ എത്തും. ക്ലെയിം ഫയൽ ചെയ്യേണ്ടതില്ല, നിങ്ങളുടെ 20 ശതമാനം ബോണസ് സുരക്ഷിതമാണ്.",
        "done_bc": "{claim_ref}ഇതിനകം സ്ഥിരീകരിച്ചിട്ടുണ്ട്, {eta_ref}. എന്തെങ്കിലും വിവരം മാറ്റണോ അതോ കൂടുതൽ സഹായം വേണോ?",
        "done_claim_ref": "ക്ലെയിം {cid} ", "done_claim_default": "നിങ്ങളുടെ ക്ലെയിം ",
        "done_eta_ref": "സഹായം ഏകദേശം {eta} മിനിറ്റിനുള്ളിൽ എത്തും",
        "done_garage_ref": "കാഷ്‌ലെസ് ഗാരേജ് വിവരങ്ങൾ പങ്കിട്ടിട്ടുണ്ട്",
        "close_c1": "സ്ഥിരീകരിച്ചു — നിങ്ങളുടെ ഇൻഷുറൻസ് ക്ലെയിം രേഖപ്പെടുത്തി, റോഡ്‌സൈഡ് സഹായവും അയച്ചു.",
        "close_c2": "നിങ്ങളുടെ സഹായവും ക്ലെയിം വിവരങ്ങളും രണ്ടും ഉറപ്പാക്കിയിട്ടുണ്ട്.",
        "close_eta": " സഹായം ഏകദേശം {eta} മിനിറ്റിനുള്ളിൽ എത്തും. വിഷമിക്കേണ്ട, സുരക്ഷിതമായിരിക്കൂ.",
        "close_est": " ഏകദേശ അറ്റകുറ്റപ്പണി ചെലവ് {est} — സർവേയർ പരിശോധനയ്ക്ക് ശേഷമാകും അന്തിമ അനുമതി.",
        "tickets_locked": " രണ്ടു അഭ്യർത്ഥനകളും സ്ഥിരീകരിച്ചു.",
        "mech_2w": "ഇരുചക്ര വാഹന മെക്കാനിക്", "mech_4w": "റോഡ്‌സൈഡ് സഹായ ടെക്നീഷ്യൻ",
        "wf_a_body": "{ack} {part} ചെറിയ പ്രശ്നമേയുള്ളൂ — വിഷമിക്കേണ്ട, വാഹനം സുരക്ഷിതമാണ്. {eta} മിനിറ്റിനുള്ളിൽ {mech} എത്തും, ഈ സേവനം പൂർണ്ണമായും സൗജന്യമാണ്, ക്ലെയിം ഇടാത്തതിനാൽ നിങ്ങളുടെ 20 ശതമാനം ബോണസ് സുരക്ഷിതമായിരിക്കും",
        "wf_a_nh": ", കൂടാതെ സുരക്ഷയ്ക്കായി 1033 ഹൈവേ പട്രോളിനെയും അറിയിച്ചിട്ടുണ്ട്",
        "wf_a_q": ". സഹായം അയച്ച് സ്ഥിരീകരിക്കട്ടെ?", "wf_a_done": " — സഹായം അയച്ചു.",
        "wf_b_rural": "{ack} {part} കേടായതിനാൽ ദയവായി വാഹനം സ്റ്റാർട്ട് ചെയ്യരുത്. {pol_ref}അടുത്തുള്ള വർക്ക്‌ഷോപ്പ് 2 മണിക്കൂറിലധികം ദൂരെയാണ് — അതിനാൽ ചട്ടപ്രകാരം പ്രാദേശിക ടോയിങ്ങിനായി ₹{rural_amt} റീഇംബേഴ്‌സ്‌മെന്റ് അനുവദിക്കുന്നു.",
        "wf_b_tow": "{ack} {part} കേടായതിനാൽ ദയവായി വാഹനം സ്റ്റാർട്ട് ചെയ്യരുത്. {pol_ref}{garage} വഴി സൗജന്യ ടോയിംഗ് വാഹനം അയക്കുന്നുണ്ട്",
        "wf_b_nh": ", ഒപ്പം 1033 ഹൈവേ പട്രോളും സുരക്ഷാ ക്രമീകരണങ്ങൾ ഒരുക്കുന്നു",
        "wf_b_est": ", കൂടാതെ {est} ഏകദേശ ക്ലെയിം കരട് തയ്യാറാണ്",
        "wf_c_body": "{ack} വാഹനം ഓടിക്കാൻ കഴിയുന്ന നിലയിലാണ്, പുറംഭാഗത്തെ കേടുപാട് മാത്രമേ കാണുന്നുള്ളൂ. ദയവായി നമ്പർ പ്ലേറ്റ് കാണുന്ന വിധത്തിൽ 4 ഫോട്ടോകൾ എടുത്ത് നേരെ {garage} ലേക്ക് പോകൂ",
        "wf_c_est": " — {est} ഏകദേശ ക്ലെയിം കരട് തയ്യാറാണ്",
        "wf_pol_known": "പോളിസി നമ്പർ {pol} പ്രകാരം ", "wf_pol_unknown": "നിങ്ങളുടെ റോഡ്‌സൈഡ് പോളിസി പ്രകാരം ",
        "wf_confirm_q": " സ്ഥിരീകരിക്കട്ടെ?",
        "wf_fallback": "{ack} എനിക്ക് കുറച്ചുകൂടി വിവരം വേണം — വാഹനത്തിന് എന്താണ് പറ്റിയത്, നിങ്ങൾ ഇപ്പോൾ എവിടെയാണ്?",
        "hooks": [r"നമസ്കാരം[^.!?]*[.!?]", r"നല്ലത്[^.!?]*[.!?]", r"നന്ദി[^.!?]*[.!?]", r"ശരി[^.!?]*[.!?]", r"മനസ്സിലായി[^.!?]*[.!?]", r"സ്ഥിരീകരിച്ചു[^.!?]*[.!?]"],
    },
    "bn-IN": {
        "greet_1": "নমস্কার! আমি বীমাবাণী সড়ক সহায়তা কেন্দ্র থেকে বলছি।",
        "greet_2": "প্রথমে বলুন—আপনি এবং গাড়িতে থাকা সবাই নিরাপদ আছেন তো, নাকি কারও চোট লেগেছে?",
        "empty_turn": "আপনার কথা স্পষ্ট শুনতে পাইনি। দয়া করে মাইক পরীক্ষা করে আবার বলুন — আমি শুনছি।",
        "empty_clause": "নমস্কার, বলুন—আমি আপনাকে সাহায্য করতে প্রস্তুত।",
        "safe_empathy": "ভালো কথা, আপনারা নিরাপদ আছেন।",
        "inj_empathy": "আপনার প্রাণ আমাদের কাছে সবচেয়ে গুরুত্বপূর্ণ। আমি এখনই 112 অ্যাম্বুলেন্স এবং 1033 হাইওয়ে পেট্রোল পাঠাচ্ছি। দয়া করে কল কাটবেন না — জরুরি কর্মকর্তার সাথে সরাসরি সংযোগ করছি।",
        "emg_c1": "আপনার জীবন সবার আগে — আমি এখনই 112 অ্যাম্বুলেন্স এবং 112 পুলিশ পাঠাচ্ছি, দয়া করে ফোন কাটবেন না।",
        "emg_c2": "আমি আপনাকে সরাসরি একজন জরুরি কর্মকর্তার সাথে সংযোগ করছি",
        "emg_c2_nh": ", এবং 1033 হাইওয়ে পেট্রোলও আপনার অবস্থানে আসছে।",
        "emg_c2_tail": " নিরাপদ জায়গায় থাকুন, লাইনে অপেক্ষা করুন।",
        "pause": "ঠিক আছে, আমি অপেক্ষা করছি। আপনি প্রস্তুত হলে বা কিছু পরিবর্তন করতে চাইলে বলুন।",
        "correct_prompt": "অবশ্যই, আমি শুনছি — সঠিক তথ্য বলুন, কী পরিবর্তন করতে হবে?",
        "ack_first": "ভালো কথা, আপনারা নিরাপদ আছেন!",
        "acks": ["ঠিক আছে, লিখে নিয়েছি।", "বুঝতে পেরেছি।", "হ্যাঁ, অবশ্যই।", "বুঝতে পেরেছি, এগিয়ে যাই।"],
        "safety_pol": "ধন্যবাদ — পলিসি নম্বর {pol} নোট করা হয়েছে। এবার বলুন, গাড়ির কী হয়েছে?",
        "safety_ok": "ধন্যবাদ — আপনারা সবাই নিরাপদ আছেন সেটাই সবচেয়ে বড় কথা। এবার বলুন, গাড়ির কী হয়েছে?",
        "pol_note": "পলিসি নম্বর {pol} নোট করা হয়েছে। ",
        "ask_part": "{pol_note}বুঝতে পেরেছি — গাড়িতে ঠিক কী সমস্যা হয়েছে? রেডিয়েটর বা ইঞ্জিনের সমস্যা, টায়ার পাংচার, ব্যাটারি নষ্ট, নাকি বাইরের ক্ষতি?",
        "ask_loc_minor": "{part_prefix}চিন্তা করবেন না। আপনি এখন শহরের ভেতরে আছেন নাকি হাইওয়েতে আছেন?",
        "road_qs": [
            "আপনি কি এখন জাতীয় মহাসড়কে, শহরের রাস্তায়, নাকি গ্রামের রাস্তায় আছেন?",
            "আপনি এখন কোথায় আছেন — হাইওয়েতে, শহরের ভেতরে, নাকি গ্রামীণ রাস্তায়?",
        ],
        "ask_loc_lead": "{part_prefix}বুঝতে পেরেছি। ",
        "ask_confirm_q": " আপনার বীমা ক্লেম কি নিশ্চিত করব?",
        "ask_pols": [
            " বীমা ক্লেম এবং সহায়তা নিশ্চিত করতে আপনার ৪ সংখ্যার পলিসি নম্বরটি বলুন।",
            " সহায়তা ও ক্লেম নথিভুক্ত করতে অনুগ্রহ করে আপনার চার সংখ্যার পলিসি নম্বর দিন।",
        ],
        "ask_incident": "বলুন, গাড়ির সাথে কী ঘটেছে — কী ধরনের সমস্যা হচ্ছে?",
        "done_a": "সহায়তা ইতিমধ্যেই পাঠানো হয়েছে — প্রায় {eta} মিনিটের মধ্যে মেকানিক পৌঁছে যাবে। ক্লেম করার দরকার নেই, আপনার ২০ শতাংশ বোনাস সম্পূর্ণ নিরাপদ আছে।",
        "done_bc": "{claim_ref}ইতিমধ্যেই নিশ্চিত হয়ে গেছে, {eta_ref}। আপনি কি কোনো তথ্য বদলাতে চান নাকি আরও সাহায্য লাগবে?",
        "done_claim_ref": "ক্লেম নম্বর {cid} ", "done_claim_default": "আপনার ক্লেম ",
        "done_eta_ref": "সহায়তা প্রায় {eta} মিনিটে পৌঁছে যাবে",
        "done_garage_ref": "ক্যাশলেস গ্যারেজের তথ্য পাঠানো হয়েছে",
        "close_c1": "নিশ্চিত হয়েছে — আপনার বীমা ক্লেম নথিভুক্ত হয়েছে এবং সড়ক সহায়তা পাঠানো হয়েছে।",
        "close_c2": "আপনার সহায়তা ও ক্লেমের উভয় বিবরণই নিশ্চিত করা হয়েছে।",
        "close_eta": " সহায়তা প্রায় {eta} মিনিটে পৌঁছাবে। চিন্তা করবেন না, নিরাপদে থাকুন।",
        "close_est": " আনুমানিক মেরামত খরচ {est} — সার্ভেয়ারের পরিদর্শনের পর চূড়ান্ত অনুমোদন হবে।",
        "tickets_locked": " উভয় অনুরোধই নিশ্চিত হয়েছে।",
        "mech_2w": "বাইক মেকানিক", "mech_4w": "সড়ক সহায়তা টেকনিশিয়ান",
        "wf_a_body": "{ack} {part} তো সামান্য সমস্যা — চিন্তা করবেন না, গাড়ি নিরাপদ আছে। আমি {eta} মিনিটের মধ্যে আপনার কাছে {mech} পাঠাচ্ছি, এই পরিষেবা সম্পূর্ণ বিনামূল্যে, আর ক্লেম না করায় আপনার ২০ শতাংশ বোনাস সুরক্ষিত থাকবে",
        "wf_a_nh": ", এবং নিরাপত্তার জন্য 1033 হাইওয়ে পেট্রোলকেও জানানো হয়েছে",
        "wf_a_q": "। সহায়তা পাঠিয়ে নিশ্চিত করব?", "wf_a_done": " — সহায়তা পাঠানো হয়েছে।",
        "wf_b_rural": "{ack} {part} ক্ষতিগ্রস্ত হওয়ায় দয়া করে গাড়ি স্টার্ট করবেন না। {pol_ref}কাছের ওয়ার্কশপ ২ ঘণ্টার বেশি দূরে — তাই নিয়ম অনুযায়ী স্থানীয় টোয়িংয়ের জন্য ₹{rural_amt} ফেরতের অনুমোদন দিচ্ছি।",
        "wf_b_tow": "{ack} {part} ক্ষতিগ্রস্ত হওয়ায় দয়া করে গাড়ি স্টার্ট করবেন না। {pol_ref}{garage} থেকে বিনামূল্যে টো ট্রাক পাঠাচ্ছি",
        "wf_b_nh": ", এবং 1033 হাইওয়ে পেট্রোলও নিরাপত্তার ব্যবস্থা করছে",
        "wf_b_est": ", এবং {est} আনুমানিক ক্লেমের খসড়া তৈরি হয়েছে",
        "wf_c_body": "{ack} গাড়ি চালানোর মতো অবস্থায় আছে, শুধু বাইরের ক্ষতি দেখা যাচ্ছে। দয়া করে নম্বর প্লেট স্পষ্ট রেখে ৪টি ছবি তুলুন এবং সরাসরি {garage}-এ চলে যান",
        "wf_c_est": " — {est} আনুমানিক ক্লেমের খসড়া তৈরি হয়েছে",
        "wf_pol_known": "পলিসি নম্বর {pol} অনুযায়ী ", "wf_pol_unknown": "আপনার সড়ক সহায়তা পলিসি অনুযায়ী ",
        "wf_confirm_q": " নিশ্চিত করব?",
        "wf_fallback": "{ack} আমার আরও একটু তথ্য দরকার — গাড়িতে কী হয়েছে এবং আপনি কোথায় আছেন?",
        "hooks": [r"নমস্কার[^.!?।]*[.!?।]", r"ভালো কথা[^.!?।]*[.!?।]", r"ধন্যবাদ[^.!?।]*[.!?।]", r"ঠিক আছে[^.!?।]*[.!?।]", r"বুঝতে পেরেছি[^.!?।]*[.!?।]", r"নিশ্চিত হয়েছে[^.!?।]*[.!?।]"],
    },
    "mr-IN": {
        "greet_1": "नमस्कार! मी बीमावाणी रस्ता मदत केंद्रातून बोलत आहे.",
        "greet_2": "सर्वात आधी सांगा—तुम्ही आणि गाडीतील सर्वजण सुरक्षित आहात का, की कोणाला दुखापत झाली आहे?",
        "empty_turn": "तुमचा आवाज स्पष्ट ऐकू आला नाही. कृपया माइक तपासून पुन्हा सांगा — मी ऐकत आहे.",
        "empty_clause": "नमस्कार, बोला—मी तुम्हाला मदत करण्यासाठी तयार आहे.",
        "safe_empathy": "चांगली गोष्ट आहे, तुम्ही सुरक्षित आहात.",
        "inj_empathy": "तुमचा जीव आमच्यासाठी सर्वात महत्त्वाचा आहे. मी लगेच 112 रुग्णवाहिका आणि 1033 महामार्ग गस्त पाठवत आहे. कृपया फोन बंद करू नका — आपत्कालीन अधिकाऱ्याशी थेट जोडत आहे.",
        "emg_c1": "तुमचा जीव सर्वात आधी आहे — मी लगेच 112 रुग्णवाहिका आणि 112 पोलीस पाठवत आहे, कृपया फोन कापू नका.",
        "emg_c2": "मी तुम्हाला थेट आपत्कालीन अधिकाऱ्याशी जोडत आहे",
        "emg_c2_nh": ", आणि 1033 महामार्ग गस्त देखील तुमच्या ठिकाणी येत आहे.",
        "emg_c2_tail": " सुरक्षित ठिकाणी राहा, कॉलवरच थांबा.",
        "pause": "हो, मी थांबले आहे. तुम्ही तयार असाल किंवा काही बदलायचे असेल तर सांगा.",
        "correct_prompt": "नक्कीच, मी ऐकत आहे — बरोबर माहिती सांगा, काय बदलायचे आहे?",
        "ack_first": "चांगली गोष्ट आहे, तुम्ही सुरक्षित आहात!",
        "acks": ["ठीक आहे, नोंदवले आहे.", "समजले.", "हो, नक्कीच.", "समजले, पुढे पाहूया."],
        "safety_pol": "धन्यवाद — पॉलिसी क्रमांक {pol} नोंदवला आहे. आता सांगा, गाडीला काय झाले?",
        "safety_ok": "धन्यवाद — तुम्ही सर्व सुरक्षित आहात हीच सर्वात मोठी गोष्ट आहे. आता सांगा, गाडीला काय झाले?",
        "pol_note": "पॉलिसी क्रमांक {pol} नोंदवला आहे. ",
        "ask_part": "{pol_note}समजले — गाडीत नेमकी काय अडचण आली आहे? रेडिएटर किंवा इंजिनचा त्रास आहे, टायर पंक्चर आहे, बॅटरी खराब आहे, की बाहेरचे नुकसान आहे?",
        "ask_loc_minor": "{part_prefix}काळजी करू नका. तुम्ही सध्या शहराच्या आत आहात की महामार्गावर आहात?",
        "road_qs": [
            "तुम्ही सध्या राष्ट्रीय महामार्गावर आहात, शहरातील रस्त्यावर आहात, की गावाकडच्या रस्त्यावर आहात?",
            "तुम्ही कुठे आहात — महामार्गावर, शहरात, की ग्रामीण रस्त्यावर?",
        ],
        "ask_loc_lead": "{part_prefix}समजले. ",
        "ask_confirm_q": " मी तुमचा विमा दावा निश्चित करू का?",
        "ask_pols": [
            " विमा दावा आणि मदत निश्चित करण्यासाठी तुमचा ४ अंकी पॉलिसी क्रमांक सांगा.",
            " मदत आणि दावा नोंदवण्यासाठी कृपया तुमचा चार अंकी पॉलिसी क्रमांक द्या.",
        ],
        "ask_incident": "बोला, गाडीला काय झाले आहे — काय अडचण येत आहे?",
        "done_a": "मदत आधीच पाठवली आहे — सुमारे {eta} मिनिटांत तंत्रज्ञ पोहोचेल. दावा दाखल करण्याची गरज नाही, तुमचा २० टक्के बोनस सुरक्षित राहील.",
        "done_bc": "{claim_ref}आधीच निश्चित झाले आहे, {eta_ref}. तुम्हाला काही माहिती बदलायची आहे का की आणखी मदत हवी आहे?",
        "done_claim_ref": "दावा क्रमांक {cid} ", "done_claim_default": "तुमचा दावा ",
        "done_eta_ref": "मदत सुमारे {eta} मिनिटांत पोहोचेल",
        "done_garage_ref": "कॅशलेस गॅरेज तपशील शेअर केले आहेत",
        "close_c1": "निश्चित झाले — तुमचा विमा दावा नोंदवला गेला आहे आणि रस्ता मदत पाठवली गेली आहे.",
        "close_c2": "तुमच्या मदतीचा आणि दाव्याचा दोन्ही तपशील पक्का झाला आहे.",
        "close_eta": " मदत सुमारे {eta} मिनिटांत येईल. काळजी करू नका, सुरक्षित राहा.",
        "close_est": " अंदाजे दुरुस्ती खर्च {est} — तपासणी अधिकाऱ्याच्या पाहणीनंतर अंतिम मंजुरी मिळेल.",
        "tickets_locked": " दोन्ही विनंत्या निश्चित झाल्या आहेत.",
        "mech_2w": "दुचाकी मेकॅनिक", "mech_4w": "रस्ता मदत तंत्रज्ञ",
        "wf_a_body": "{ack} {part} ही छोटीशी बाब आहे — काळजी करू नका, गाडी सुरक्षित आहे. मी {eta} मिनिटांत तुमच्याकडे {mech} पाठवत आहे, ही सेवा पूर्णपणे मोफत आहे, आणि दावा न केल्यामुळे तुमचा २० टक्के बोनस सुरक्षित राहील",
        "wf_a_nh": ", आणि सुरक्षेसाठी 1033 महामार्ग गस्तीलाही कळवले आहे",
        "wf_a_q": ". मदत पाठवून निश्चित करू का?", "wf_a_done": " — मदत पाठवली आहे.",
        "wf_b_rural": "{ack} {part} खराब झाल्यामुळे कृपया गाडी स्टार्ट करू नका. {pol_ref}जवळचे वर्कशॉप २ तासांपेक्षा जास्त दूर आहे — म्हणून नियमानुसार स्थानिक टोइंगसाठी ₹{rural_amt} परतावा मंजूर करत आहे.",
        "wf_b_tow": "{ack} {part} खराब झाल्यामुळे कृपया गाडी स्टार्ट करू नका. {pol_ref}{garage} मधून मोफत टो ट्रक पाठवत आहोत",
        "wf_b_nh": ", आणि 1033 महामार्ग गस्त देखील सुरक्षेची व्यवस्था करत आहे",
        "wf_b_est": ", तसेच {est} अंदाजे दाव्याचा मसुदा तयार आहे",
        "wf_c_body": "{ack} गाडी चालवण्यायोग्य आहे, फक्त बाहेरचे नुकसान दिसते आहे. कृपया नंबर प्लेट स्पष्ट दिसेल असे ४ फोटो काढा आणि थेट {garage} येथे जा",
        "wf_c_est": " — {est} अंदाजे दाव्याचा मसुदा तयार आहे",
        "wf_pol_known": "पॉलिसी क्रमांक {pol} नुसार ", "wf_pol_unknown": "तुमच्या रस्ता मदत पॉलिसी नुसार ",
        "wf_confirm_q": " निश्चित करू का?",
        "wf_fallback": "{ack} मला थोडी अधिक माहिती हवी आहे — गाडीत काय झाले आणि तुम्ही कुठे आहात?",
        "hooks": [r"नमस्कार[^.!?।]*[.!?।]", r"चांगली गोष्ट[^.!?।]*[.!?।]", r"धन्यवाद[^.!?।]*[.!?।]", r"ठीक आहे[^.!?।]*[.!?।]", r"समजले[^.!?।]*[.!?।]", r"निश्चित झाले[^.!?।]*[.!?।]"],
    },
    "gu-IN": {
        "greet_1": "નમસ્તે! હું બીમાવાણી રોડસાઇડ સહાય કેન્દ્રમાંથી બોલું છું.",
        "greet_2": "સૌથી પહેલાં કહો—તમે અને ગાડીમાં બેઠેલા બધા સુરક્ષિત છો ને, કે કોઈને ઈજા થઈ છે?",
        "empty_turn": "તમારો અવાજ સ્પષ્ટ સંભળાયો નહીં. કૃપા કરીને માઇક તપાસીને ફરી બોલો — હું સાંભળી રહી છું.",
        "empty_clause": "નમસ્તે, બોલો—હું તમને મદદ કરવા તૈયાર છું.",
        "safe_empathy": "સારું થયું કે તમે સુરક્ષિત છો.",
        "inj_empathy": "તમારો જીવ અમારા માટે સૌથી મહત્ત્વનો છે. હું તરત જ 112 એમ્બ્યુલન્સ અને 1033 હાઇવે પેટ્રોલ મોકલું છું. કૃપા કરીને ફોન કાપશો નહીં — કટોકટી અધિકારી સાથે સીધું જોડાણ કરું છું.",
        "emg_c1": "તમારી સલામતી સૌથી પહેલી છે — હું તરત જ 112 એમ્બ્યુલન્સ અને 112 પોલીસ મોકલું છું, કૃપા કરીને ફોન બંધ કરશો નહીં.",
        "emg_c2": "હું તમને સીધા કટોકટી સહાય અધિકારી સાથે જોડું છું",
        "emg_c2_nh": ", અને 1033 હાઇવે પેટ્રોલ પણ તમારા સ્થળે આવી રહ્યું છે.",
        "emg_c2_tail": " સુરક્ષિત જગ્યાએ રહો, કોલ પર જ રહેજો.",
        "pause": "ભલે, હું રાહ જોઉં છું. તમે તૈયાર હો અથવા કંઈ બદલવું હોય તો જણાવજો.",
        "correct_prompt": "ચોક્કસ, હું સાંભળી રહી છું — સાચી વિગતો જણાવો, શું બદલવું છે?",
        "ack_first": "સારું થયું કે તમે સુરક્ષિત છો!",
        "acks": ["ઠીક છે, નોંધી લીધું.", "સમજી ગઈ.", "હા, ચોક્કસ.", "સમજી ગઈ, આગળ વધીએ."],
        "safety_pol": "આભાર — પોલિસી નંબર {pol} નોંધી લીધો છે. હવે કહો, ગાડીને શું થયું છે?",
        "safety_ok": "આભાર — તમે બધા સુરક્ષિત છો એ જ સૌથી મોટી વાત છે. હવે કહો, ગાડીને શું થયું છે?",
        "pol_note": "પોલિસી નંબર {pol} નોંધેલો છે. ",
        "ask_part": "{pol_note}સમજી ગઈ — ગાડીમાં બરાબર શું તકલીફ છે? રેડિએટર કે એન્જિનની સમસ્યા છે, ટાયર પંચર છે, બેટરી ખરાબ છે, કે બહારનું નુકસાન છે?",
        "ask_loc_minor": "{part_prefix}ચિંતા કરશો નહીં. તમે અત્યારે શહેરની અંદર છો કે હાઇવે પર છો?",
        "road_qs": [
            "તમે અત્યારે નેશનલ હાઇવે પર છો, શહેરના રસ્તા પર છો, કે ગામડાના રસ્તા પર છો?",
            "તમે ક્યાં છો — હાઇવે પર, શહેરમાં, કે ગ્રામીણ રસ્તા પર?",
        ],
        "ask_loc_lead": "{part_prefix}સમજી ગઈ. ",
        "ask_confirm_q": " શું હું તમારો વીમા ક્લેમ પાકો કરી દઉં?",
        "ask_pols": [
            " વીમા ક્લેમ અને મદદ પાકી કરવા માટે તમારો ૪ અંકનો પોલિસી નંબર જણાવો.",
            " મદદ અને ક્લેમ નોંધવા માટે કૃપા કરીને તમારો ચાર અંકનો પોલિસી નંબર આપો.",
        ],
        "ask_incident": "કહો, ગાડી સાથે શું થયું છે — શું તકલીફ આવી છે?",
        "done_a": "મદદ પહેલેથી જ રવાના થઈ ગઈ છે — લગભગ {eta} મિનિટમાં ટેકનિશિયન પહોંચી જશે. ક્લેમ કરવાની જરૂર નથી, તમારો ૨૦ ટકા બોનસ સુરક્ષિત રહેશે.",
        "done_bc": "{claim_ref}પહેલેથી જ પાકો થઈ ગયો છે, {eta_ref}. શું તમારે કોઈ વિગત બદલવી છે કે વધુ મદદ જોઈએ છે?",
        "done_claim_ref": "ક્લેમ નંબર {cid} ", "done_claim_default": "તમારો ક્લેમ ",
        "done_eta_ref": "મદદ લગભગ {eta} મિનિટમાં પહોંચી રહી છે",
        "done_garage_ref": "કેશલેસ ગેરેજની વિગતો આપેલી છે",
        "close_c1": "પાકું થઈ ગયું — તમારો વીમા ક્લેમ નોંધાઈ ગયો છે અને રોડસાઇડ મદદ રવાના થઈ ગઈ છે.",
        "close_c2": "તમારી મદદ અને ક્લેમ બંનેની વિગતો પાકી થઈ ગઈ છે.",
        "close_eta": " મદદ લગભગ {eta} મિનિટમાં આવશે. ચિંતા ન કરશો, સુરક્ષિત રહેજો.",
        "close_est": " અંદાજિત મરામત ખર્ચ {est} — સર્વેયરની તપાસ પછી અંતિમ મંજૂરી મળશે.",
        "tickets_locked": " બંને વિનંતીઓ પાકી થઈ ગઈ છે.",
        "mech_2w": "બાઇક મિકેનિક", "mech_4w": "રોડસાઇડ સહાય ટેકનિશિયન",
        "wf_a_body": "{ack} {part} તો નાની વાત છે — ચિંતા કરશો નહીં, ગાડી સુરક્ષિત છે. હું {eta} મિનિટમાં તમારી પાસે {mech} મોકલું છું, આ સેવા બિલકુલ મફત છે, અને ક્લેમ ન કરવાથી તમારો ૨૦ ટકા બોનસ સુરક્ષિત રહેશે",
        "wf_a_nh": ", અને સલામતી માટે 1033 હાઇવે પેટ્રોલને પણ જાણ કરી દીધી છે",
        "wf_a_q": ". મદદ મોકલીને પાકું કરું?", "wf_a_done": " — મદદ મોકલી દેવાઈ છે.",
        "wf_b_rural": "{ack} {part}ને નુકસાન થયું હોવાથી કૃપા કરીને ગાડી સ્ટાર્ટ કરશો નહીં. {pol_ref}નજીકનું વર્કશોપ ૨ કલાકથી વધુ દૂર છે — તેથી નિયમ મુજબ સ્થાનિક ટોઇંગ માટે ₹{rural_amt} વળતરની મંજૂરી આપું છું.",
        "wf_b_tow": "{ack} {part}ને નુકસાન થયું હોવાથી કૃપા કરીને ગાડી સ્ટાર્ટ કરશો નહીં. {pol_ref}{garage}થી મફત ટો ટ્રક મોકલી રહ્યા છીએ",
        "wf_b_nh": ", અને 1033 હાઇવે પેટ્રોલ પણ સલામતીની વ્યવસ્થા કરી રહ્યું છે",
        "wf_b_est": ", તેમજ {est} અંદાજિત ક્લેમનો ડ્રાફ્ટ તૈયાર છે",
        "wf_c_body": "{ack} ગાડી ચલાવી શકાય તેવી સ્થિતિમાં છે, ફક્ત બહારનું નુકસાન લાગે છે. કૃપા કરીને નંબર પ્લેટ દેખાય તેવા ૪ ફોટા પાડો અને સીધા {garage} જતા રહો",
        "wf_c_est": " — {est} અંદાજિત ક્લેમનો ડ્રાફ્ટ તૈયાર છે",
        "wf_pol_known": "પોલિસી નંબર {pol} હેઠળ ", "wf_pol_unknown": "તમારી રોડસાઇડ પોલિસી હેઠળ ",
        "wf_confirm_q": " પાકું કરું?",
        "wf_fallback": "{ack} મને થોડી વધુ માહિતી જોઈએ છે — ગાડીમાં શું થયું અને તમે ક્યાં છો?",
        "hooks": [r"નમસ્તે[^.!?।]*[.!?।]", r"સારું થયું[^.!?।]*[.!?।]", r"આભાર[^.!?।]*[.!?।]", r"ઠીક છે[^.!?।]*[.!?।]", r"સમજી ગઈ[^.!?।]*[.!?।]", r"પાકું થઈ ગયું[^.!?।]*[.!?।]"],
    },
    "pa-IN": {
        "greet_1": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ! ਮੈਂ ਬੀਮਾਵਾਣੀ ਸੜਕ ਸਹਾਇਤਾ ਕੇਂਦਰ ਤੋਂ ਬੋਲ ਰਹੀ ਹਾਂ।",
        "greet_2": "ਸਭ ਤੋਂ ਪਹਿਲਾਂ ਦੱਸੋ—ਤੁਸੀਂ ਅਤੇ ਗੱਡੀ ਵਿੱਚ ਬੈਠੇ ਸਾਰੇ ਲੋਕ ਸੁਰੱਖਿਅਤ ਹੋ, ਜਾਂ ਕਿਸੇ ਨੂੰ ਸੱਟ ਲੱਗੀ ਹੈ?",
        "empty_turn": "ਤੁਹਾਡੀ ਆਵਾਜ਼ ਸਾਫ਼ ਨਹੀਂ ਆਈ। ਕਿਰਪਾ ਕਰਕੇ ਮਾਈਕ ਜਾਂਚ ਕੇ ਦੁਬਾਰਾ ਬੋਲੋ — ਮੈਂ ਸੁਣ ਰਹੀ ਹਾਂ।",
        "empty_clause": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ, ਦੱਸੋ—ਮੈਂ ਤੁਹਾਡੀ ਮਦਦ ਕਰਨ ਲਈ ਤਿਆਰ ਹਾਂ।",
        "safe_empathy": "ਸ਼ੁਕਰ ਹੈ ਕਿ ਤੁਸੀਂ ਸੁਰੱਖਿਅਤ ਹੋ।",
        "inj_empathy": "ਤੁਹਾਡੀ ਜਾਨ ਸਾਡੇ ਲਈ ਸਭ ਤੋਂ ਜ਼ਰੂਰੀ ਹੈ। ਮੈਂ ਤੁਰੰਤ 112 ਐਂਬੂਲੈਂਸ ਅਤੇ 1033 ਹਾਈਵੇ ਪੈਟਰੋਲ ਭੇਜ ਰਹੀ ਹਾਂ। ਕਿਰਪਾ ਕਰਕੇ ਫ਼ੋਨ ਨਾ ਕੱਟੋ — ਮੈਂ ਤੁਹਾਨੂੰ ਸਿੱਧੇ ਐਮਰਜੈਂਸੀ ਅਧਿਕਾਰੀ ਨਾਲ ਜੋੜ ਰਹੀ ਹਾਂ।",
        "emg_c1": "ਤੁਹਾਡੀ ਸੁਰੱਖਿਆ ਸਭ ਤੋਂ ਪਹਿਲਾਂ ਹੈ — ਮੈਂ ਤੁਰੰਤ 112 ਐਂਬੂਲੈਂਸ ਅਤੇ 112 ਪੁਲਿਸ ਭੇਜ ਰਹੀ ਹਾਂ, ਕਿਰਪਾ ਕਰਕੇ ਫ਼ੋਨ ਨਾ ਕੱਟੋ।",
        "emg_c2": "ਮੈਂ ਤੁਹਾਨੂੰ ਸਿੱਧੇ ਐਮਰਜੈਂਸੀ ਅਧਿਕਾਰੀ ਨਾਲ ਜੋੜ ਰਹੀ ਹਾਂ",
        "emg_c2_nh": ", ਅਤੇ 1033 ਹਾਈਵੇ ਪੈਟਰੋਲ ਵੀ ਤੁਹਾਡੇ ਕੋਲ ਆ ਰਿਹਾ ਹੈ।",
        "emg_c2_tail": " ਸੁਰੱਖਿਅਤ ਥਾਂ 'ਤੇ ਰਹੋ, ਲਾਈਨ 'ਤੇ ਬਣੇ ਰਹੋ।",
        "pause": "ਹਾਂਜੀ, ਮੈਂ ਰੁਕੀ ਹੋਈ ਹਾਂ। ਜਦੋਂ ਤੁਸੀਂ ਤਿਆਰ ਹੋਵੋ ਜਾਂ ਕੁਝ ਬਦਲਣਾ ਹੋਵੇ ਤਾਂ ਦੱਸ ਦਿਓ।",
        "correct_prompt": "ਬਿਲਕੁਲ, ਮੈਂ ਸੁਣ ਰਹੀ ਹਾਂ — ਸਹੀ ਵੇਰਵੇ ਦੱਸੋ, ਕੀ ਬਦਲਣਾ ਹੈ?",
        "ack_first": "ਸ਼ੁਕਰ ਹੈ ਕਿ ਤੁਸੀਂ ਸੁਰੱਖਿਅਤ ਹੋ!",
        "acks": ["ਠੀਕ ਹੈ ਜੀ, ਨੋਟ ਕਰ ਲਿਆ।", "ਸਮਝ ਗਈ ਜੀ।", "ਹਾਂਜੀ, ਬਿਲਕੁਲ।", "ਸਮਝ ਗਈ, ਅੱਗੇ ਵਧਦੇ ਹਾਂ।"],
        "safety_pol": "ਬਹੁਤ ਧੰਨਵਾਦ — ਪਾਲਿਸੀ ਨੰਬਰ {pol} ਨੋਟ ਕਰ ਲਿਆ ਹੈ। ਹੁਣ ਦੱਸੋ, ਗੱਡੀ ਨੂੰ ਕੀ ਹੋਇਆ?",
        "safety_ok": "ਬਹੁਤ ਧੰਨਵਾਦ — ਤੁਸੀਂ ਸਾਰੇ ਸੁਰੱਖਿਅਤ ਹੋ ਇਹੀ ਸਭ ਤੋਂ ਵੱਡੀ ਗੱਲ ਹੈ। ਹੁਣ ਦੱਸੋ, ਗੱਡੀ ਨੂੰ ਕੀ ਹੋਇਆ?",
        "pol_note": "ਪਾਲਿਸੀ ਨੰਬਰ {pol} ਨੋਟ ਹੈ। ",
        "ask_part": "{pol_note}ਸਮਝ ਗਈ ਜੀ — ਗੱਡੀ ਵਿੱਚ ਬਿਲਕੁਲ ਕੀ ਖਰਾਬੀ ਆਈ ਹੈ? ਰੇਡੀਏਟਰ ਜਾਂ ਇੰਜਣ ਦੀ ਦਿੱਕਤ ਹੈ, ਟਾਇਰ ਪੰਕਚਰ ਹੈ, ਬੈਟਰੀ ਡਾਊਨ ਹੈ, ਜਾਂ ਬਾਹਰੋਂ ਨੁਕਸਾਨ ਹੋਇਆ ਹੈ?",
        "ask_loc_minor": "{part_prefix}ਕੋਈ ਗੱਲ ਨਹੀਂ ਜੀ। ਤੁਸੀਂ ਇਸ ਵੇਲੇ ਸ਼ਹਿਰ ਦੇ ਅੰਦਰ ਹੋ ਜਾਂ ਹਾਈਵੇ 'ਤੇ ਹੋ?",
        "road_qs": [
            "ਤੁਸੀਂ ਇਸ ਵੇਲੇ ਨੈਸ਼ਨਲ ਹਾਈਵੇ 'ਤੇ ਹੋ, ਸ਼ਹਿਰ ਦੀ ਸੜਕ 'ਤੇ ਹੋ, ਜਾਂ ਪਿੰਡ ਦੀ ਲਿੰਕ ਰੋਡ 'ਤੇ ਹੋ?",
            "ਤੁਸੀਂ ਕਿੱਥੇ ਹੋ — ਹਾਈਵੇ 'ਤੇ, ਸ਼ਹਿਰ ਵਿੱਚ, ਜਾਂ ਪਿੰਡ ਦੀ ਸੜਕ 'ਤੇ?",
        ],
        "ask_loc_lead": "{part_prefix}ਸਮਝ ਗਈ ਜੀ। ",
        "ask_confirm_q": " ਕੀ ਮੈਂ ਤੁਹਾਡਾ ਬੀਮਾ ਕਲੇਮ ਪੱਕਾ ਕਰ ਦੇਵਾਂ?",
        "ask_pols": [
            " ਬੀਮਾ ਕਲੇਮ ਅਤੇ ਮਦਦ ਪੱਕੀ ਕਰਨ ਲਈ ਆਪਣਾ 4 ਅੰਕਾਂ ਦਾ ਪਾਲਿਸੀ ਨੰਬਰ ਦੱਸੋ।",
            " ਮਦਦ ਅਤੇ ਕਲੇਮ ਦਰਜ ਕਰਨ ਲਈ ਕਿਰਪਾ ਕਰਕੇ ਆਪਣਾ ਚਾਰ ਅੰਕਾਂ ਦਾ ਪਾਲਿਸੀ ਨੰਬਰ ਦਿਓ।",
        ],
        "ask_incident": "ਦੱਸੋ ਜੀ, ਗੱਡੀ ਨਾਲ ਕੀ ਹੋਇਆ ਹੈ — ਕੀ ਪਰੇਸ਼ਾਨੀ ਆ ਰਹੀ ਹੈ?",
        "done_a": "ਮਦਦ ਪਹਿਲਾਂ ਹੀ ਰਵਾਨਾ ਹੋ ਚੁੱਕੀ ਹੈ — ਤਕਰੀਬਨ {eta} ਮਿੰਟਾਂ ਵਿੱਚ ਮਕੈਨਿਕ ਪਹੁੰਚ ਜਾਵੇਗਾ। ਕਲੇਮ ਦਰਜ ਕਰਨ ਦੀ ਲੋੜ ਨਹੀਂ, ਤੁਹਾਡਾ 20 ਫੀਸਦੀ ਬੋਨਸ ਬਿਲਕੁਲ ਸੁਰੱਖਿਅਤ ਹੈ।",
        "done_bc": "{claim_ref}ਪਹਿਲਾਂ ਹੀ ਪੱਕਾ ਹੋ ਚੁੱਕਾ ਹੈ, {eta_ref}। ਕੀ ਤੁਸੀਂ ਕੋਈ ਵੇਰਵਾ ਬਦਲਣਾ ਚਾਹੁੰਦੇ ਹੋ ਜਾਂ ਹੋਰ ਮਦਦ ਚਾਹੀਦੀ ਹੈ?",
        "done_claim_ref": "ਕਲੇਮ ਨੰਬਰ {cid} ", "done_claim_default": "ਤੁਹਾਡਾ ਕਲੇਮ ",
        "done_eta_ref": "ਮਦਦ ਤਕਰੀਬਨ {eta} ਮਿੰਟਾਂ ਵਿੱਚ ਪਹੁੰਚ ਰਹੀ ਹੈ",
        "done_garage_ref": "ਕੈਸ਼ਲੈੱਸ ਗੈਰੇਜ ਦੇ ਵੇਰਵੇ ਸਾਂਝੇ ਕੀਤੇ ਗਏ ਹਨ",
        "close_c1": "ਪੱਕਾ ਹੋ ਗਿਆ ਜੀ — ਤੁਹਾਡਾ ਬੀਮਾ ਕਲੇਮ ਦਰਜ ਹੋ ਗਿਆ ਹੈ ਅਤੇ ਸੜਕ ਸਹਾਇਤਾ ਭੇਜ ਦਿੱਤੀ ਗਈ ਹੈ।",
        "close_c2": "ਤੁਹਾਡੀ ਮਦਦ ਅਤੇ ਕਲੇਮ ਦੋਵੇਂ ਵੇਰਵੇ ਪੱਕੇ ਹੋ ਚੁੱਕੇ ਹਨ।",
        "close_eta": " ਮਦਦ ਤਕਰੀਬਨ {eta} ਮਿੰਟਾਂ ਵਿੱਚ ਆਵੇਗੀ। ਚਿੰਤਾ ਨਾ ਕਰੋ, ਸੁਰੱਖਿਅਤ ਰਹੋ ਜੀ।",
        "close_est": " ਅੰਦਾਜ਼ਨ ਮੁਰੰਮਤ ਖਰਚ {est} — ਸਰਵੇਅਰ ਦੀ ਜਾਂਚ ਤੋਂ ਬਾਅਦ ਅੰਤਿਮ ਮਨਜ਼ੂਰੀ ਮਿਲੇਗੀ।",
        "tickets_locked": " ਦੋਵੇਂ ਬੇਨਤੀਆਂ ਪੱਕੀਆਂ ਹੋ ਗਈਆਂ ਹਨ।",
        "mech_2w": "ਬਾਈਕ ਮਕੈਨਿਕ", "mech_4w": "ਸੜਕ ਸਹਾਇਤਾ ਟੈਕਨੀਸ਼ੀਅਨ",
        "wf_a_body": "{ack} {part} ਤਾਂ ਛੋਟੀ ਜਿਹੀ ਗੱਲ ਹੈ — ਚਿੰਤਾ ਨਾ ਕਰੋ, ਗੱਡੀ ਸੁਰੱਖਿਅਤ ਹੈ। ਮੈਂ {eta} ਮਿੰਟਾਂ ਵਿੱਚ ਤੁਹਾਡੇ ਕੋਲ {mech} ਭੇਜ ਰਹੀ ਹਾਂ, ਇਹ ਸੇਵਾ ਬਿਲਕੁਲ ਮੁਫ਼ਤ ਹੈ, ਅਤੇ ਕਲੇਮ ਨਾ ਕਰਨ ਨਾਲ ਤੁਹਾਡਾ 20 ਫੀਸਦੀ ਬੋਨਸ ਸੁਰੱਖਿਅਤ ਰਹੇਗਾ",
        "wf_a_nh": ", ਅਤੇ ਸੁਰੱਖਿਆ ਲਈ 1033 ਹਾਈਵੇ ਪੈਟਰੋਲ ਨੂੰ ਵੀ ਸੂਚਿਤ ਕਰ ਦਿੱਤਾ ਗਿਆ ਹੈ",
        "wf_a_q": "। ਮਦਦ ਭੇਜ ਕੇ ਪੱਕਾ ਕਰਾਂ?", "wf_a_done": " — ਮਦਦ ਭੇਜ ਦਿੱਤੀ ਗਈ ਹੈ।",
        "wf_b_rural": "{ack} {part} ਨੂੰ ਨੁਕਸਾਨ ਹੋਣ ਕਰਕੇ ਕਿਰਪਾ ਕਰਕੇ ਗੱਡੀ ਸਟਾਰਟ ਨਾ ਕਰੋ। {pol_ref}ਨੇੜਲਾ ਵਰਕਸ਼ਾਪ 2 ਘੰਟਿਆਂ ਤੋਂ ਵੱਧ ਦੂਰ ਹੈ — ਇਸ ਲਈ ਨਿਯਮ ਮੁਤਾਬਕ ਸਥਾਨਕ ਟੋਇੰਗ ਲਈ ₹{rural_amt} ਵਾਪਸੀ ਦੀ ਮਨਜ਼ੂਰੀ ਦੇ ਰਹੀ ਹਾਂ।",
        "wf_b_tow": "{ack} {part} ਨੂੰ ਨੁਕਸਾਨ ਹੋਣ ਕਰਕੇ ਕਿਰਪਾ ਕਰਕੇ ਗੱਡੀ ਸਟਾਰਟ ਨਾ ਕਰੋ। {pol_ref}{garage} ਤੋਂ ਮੁਫ਼ਤ ਟੋ ਟਰੱਕ ਭੇਜ ਰਹੇ ਹਾਂ",
        "wf_b_nh": ", ਅਤੇ 1033 ਹਾਈਵੇ ਪੈਟਰੋਲ ਵੀ ਸੁਰੱਖਿਆ ਦੇ ਪ੍ਰਬੰਧ ਕਰ ਰਿਹਾ ਹੈ",
        "wf_b_est": ", ਅਤੇ {est} ਅੰਦਾਜ਼ਨ ਕਲੇਮ ਦਾ ਖਰੜਾ ਤਿਆਰ ਹੈ",
        "wf_c_body": "{ack} ਗੱਡੀ ਚੱਲਣ ਯੋਗ ਹੈ, ਸਿਰਫ਼ ਬਾਹਰੀ ਨੁਕਸਾਨ ਲੱਗਦਾ ਹੈ। ਕਿਰਪਾ ਕਰਕੇ ਨੰਬਰ ਪਲੇਟ ਦਿਖਦੀ ਹੋਈ 4 ਤਸਵੀਰਾਂ ਖਿੱਚ ਲਓ ਅਤੇ ਸਿੱਧਾ {garage} ਚਲੇ ਜਾਓ",
        "wf_c_est": " — {est} ਅੰਦਾਜ਼ਨ ਕਲੇਮ ਦਾ ਖਰੜਾ ਤਿਆਰ ਹੈ",
        "wf_pol_known": "ਪਾਲਿਸੀ ਨੰਬਰ {pol} ਤਹਿਤ ", "wf_pol_unknown": "ਤੁਹਾਡੀ ਸੜਕ ਸਹਾਇਤਾ ਪਾਲਿਸੀ ਤਹਿਤ ",
        "wf_confirm_q": " ਪੱਕਾ ਕਰਾਂ?",
        "wf_fallback": "{ack} ਮੈਨੂੰ ਥੋੜ੍ਹੀ ਹੋਰ ਜਾਣਕਾਰੀ ਚਾਹੀਦੀ ਹੈ — ਗੱਡੀ ਵਿੱਚ ਕੀ ਹੋਇਆ ਅਤੇ ਤੁਸੀਂ ਕਿੱਥੇ ਹੋ?",
        "hooks": [r"ਸਤ ਸ੍ਰੀ ਅਕਾਲ[^.!?।]*[.!?।]", r"ਸ਼ੁਕਰ ਹੈ[^.!?।]*[.!?।]", r"ਬਹੁਤ ਧੰਨਵਾਦ[^.!?।]*[.!?।]", r"ਠੀਕ ਹੈ[^.!?।]*[.!?।]", r"ਸਮਝ ਗਈ[^.!?।]*[.!?।]", r"ਪੱਕਾ ਹੋ ਗਿਆ[^.!?।]*[.!?।]"],
    },
}


def get_lang_bundle(lang: Optional[str]) -> Dict[str, Any]:
    code = normalize_lang_code(lang) or "hi-IN"
    return STRINGS.get(code, STRINGS["hi-IN"])


def spoken_part_localized(parts: List[str], parts_catalog: Dict[str, Dict[str, Any]], lang: str = "hi-IN") -> str:
    if not parts:
        return ""
    priority = {"non_drivable": 0, "drivable_minor": 1, "minor_breakdown": 2}
    ordered = sorted(
        parts,
        key=lambda p: priority.get(parts_catalog.get(p, {}).get("safety", "minor_breakdown"), 3),
    )
    first = ordered[0]
    tbl = PARTS_I18N.get(lang, PARTS_I18N["hi-IN"])
    if first in tbl:
        return tbl[first]
    return tbl.get("_default", "breakdown")


def compose_localized_reply(
    lang: str,
    incident: Dict[str, Any],
    parts_catalog: Dict[str, Dict[str, Any]],
    rural_inr: int,
    ack: str = "",
    ask_confirm: bool = True,
) -> Optional[str]:
    """Compose pure native-script reply for ta/te/kn/ml/bn/mr/gu/pa/en.
    Returns None for 'hi-IN' so legacy Hinglish formatter keeps exact phrasing."""
    if lang not in STRINGS or lang == "hi-IN":
        return None
    b = STRINGS[lang]
    tri = incident.get("triage", {})
    disp = incident.get("dispatch", {})
    draft = incident.get("claim_draft", {})

    if tri.get("injured"):
        return b["inj_empathy"]

    policy_no = disp.get("policy_number") or (draft.get("policy_number") if draft else "")
    part_word = spoken_part_localized(disp.get("parts", []) or [], parts_catalog, lang) or PARTS_I18N[lang]["_default"]
    veh_cls = disp.get("vehicle_class", "four_wheeler")
    mech_word = b["mech_2w"] if veh_cls == "two_wheeler" else b["mech_4w"]
    ack_str = (ack or "").strip() or b["ack_first"]
    wf = disp.get("workflow")

    if lang == "en-IN":
        pol_ref = f"Under policy {policy_no}, " if policy_no else "Under your RSA policy, "
        if wf == "A":
            s = (
                f"{ack_str} A {part_word} issue is minor—no worries, the vehicle is safe. "
                f"I am sending an on-spot {mech_word} within {disp.get('eta_mins', 22)} minutes; "
                "this RSA service is completely FREE and keeps your 20 percent No-Claim Bonus intact"
            )
            if disp.get("nhai_patrol_alerted"):
                s += ", and 1033 highway patrol has also been alerted for safety"
            s += ". Shall I confirm this dispatch?" if ask_confirm else "—dispatch confirmed."
            return s
        if wf == "B" and (disp.get("rural_reimbursement") or {}).get("eligible"):
            return (
                f"{ack_str} Because of {part_word} damage, please do not start the engine. "
                f"{pol_ref}the nearest workshop is over 2 hours away, so under IRDAI rules I am authorizing ₹{rural_inr} local crane/towing reimbursement."
                + (" Shall I confirm?" if ask_confirm else "")
            )
        if wf == "B":
            gname = localize_garage(disp.get("destination_garage"), lang)
            est = format_range(draft["estimated_repair_low_inr"], draft["estimated_repair_high_inr"], lang) if draft else ""
            s = (
                f"{ack_str} Because of {part_word} damage, please do not start the engine. "
                f"{pol_ref}we are sending a FREE flatbed tow truck from {gname}"
            )
            if disp.get("nhai_patrol_alerted"):
                s += ", and 1033 highway patrol is setting up safety cones"
            if est:
                s += f", with an estimated claim draft of {est}"
            s += ". Shall I confirm?" if ask_confirm else "."
            return s
        if wf == "C":
            gname = localize_garage(disp.get("destination_garage"), lang)
            est = format_range(draft["estimated_repair_low_inr"], draft["estimated_repair_high_inr"], lang) if draft else ""
            s = (
                f"{ack_str} The vehicle appears drivable with minor exterior damage. "
                f"Please take 4 photos showing the number plate and drive directly to {gname}"
            )
            if est:
                s += f"—estimated claim draft is {est}"
            s += ". Shall I confirm this claim?" if ask_confirm else "."
            return s
        return f"{ack_str} Could you share a little more detail—what happened to the vehicle and where are you?"

    # Native regional Indian languages (ta/te/kn/ml/bn/mr/gu/pa)
    pol_ref = b["wf_pol_known"].format(pol=policy_no) if policy_no else b["wf_pol_unknown"]
    if wf == "A":
        s = b["wf_a_body"].format(ack=ack_str, part=part_word, eta=disp.get("eta_mins", 22), mech=mech_word)
        if disp.get("nhai_patrol_alerted"):
            s += b["wf_a_nh"]
        s += b["wf_a_q"] if ask_confirm else b["wf_a_done"]
        return s
    if wf == "B" and (disp.get("rural_reimbursement") or {}).get("eligible"):
        s = b["wf_b_rural"].format(ack=ack_str, part=part_word, pol_ref=pol_ref, rural_amt=rural_inr)
        if ask_confirm:
            s += b["wf_confirm_q"]
        return s
    if wf == "B":
        gname = localize_garage(disp.get("destination_garage"), lang)
        est = format_range(draft["estimated_repair_low_inr"], draft["estimated_repair_high_inr"], lang) if draft else ""
        s = b["wf_b_tow"].format(ack=ack_str, part=part_word, pol_ref=pol_ref, garage=gname)
        if disp.get("nhai_patrol_alerted"):
            s += b["wf_b_nh"]
        if est:
            s += b["wf_b_est"].format(est=est)
        s += b["wf_confirm_q"] if ask_confirm else "."
        return s
    if wf == "C":
        gname = localize_garage(disp.get("destination_garage"), lang)
        est = format_range(draft["estimated_repair_low_inr"], draft["estimated_repair_high_inr"], lang) if draft else ""
        s = b["wf_c_body"].format(ack=ack_str, garage=gname)
        if est:
            s += b["wf_c_est"].format(est=est)
        s += b["wf_confirm_q"] if ask_confirm else "."
        return s
    return b["wf_fallback"].format(ack=ack_str)


def all_hook_patterns() -> List[str]:
    out: List[str] = []
    seen = set()
    for b in STRINGS.values():
        for pat in b.get("hooks", []):
            if pat not in seen:
                seen.add(pat)
                out.append(pat)
    return out
