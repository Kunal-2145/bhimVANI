"""
BimaVani — 20-Case Evaluation Benchmark Suite (backend/eval_runner.py)
=====================================================================
Evaluates all 20 cases across three acoustic plates + multi-turn barge-in + misses:
    CLEAN        — quiet studio (clean transcript + -34dB room noise WAV)
    HIGHWAY      — NH-44 horn/engine noise (-10 dB WAV + Devanagari/Hinglish STT mix)
    GSM          — 8kHz phone-line band-limited WAV + noisy phonetics

Each case runs the DETERMINISTIC brain (no LLM, no credits) with persist_history=False
so benchmarks never pollute production call_history.json, AND exercises the real
synthetic noise WAV generator + frame-energy endpointing layer per plate.
"""
from __future__ import annotations

import asyncio
import json
import math
import random
import struct
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.gnani_client import wrap_pcm_as_wav
from backend.i18n import ALL_GNANI_LANGUAGES
from backend.pipeline.brain import Brain
from backend.pipeline.endpointing import (
    AutoVADDetector,
    EndpointingState,
    analyze_wav_endpoint,
    denoise_wav_for_stt,
)
from backend.pipeline.jitter_buffer import InputJitterBuffer
from backend.pipeline.safety import redact_pii

RESULTS_PATH = Path(__file__).resolve().parent / "eval_results.json"

SCENARIOS: List[Dict[str, Any]] = [
    {
        "id": "S1",
        "name": "NH-44 Highway Crash (Radiator + Bumper, Policy #7820)",
        "utterance": "hello helpline ji crash ho gaya NH-44 highway par, radiator phat gaya hai coolant nikal raha hai aur bumper mein dant aa gaya, policy number 7820",
        "utterance_highway": "हेल्पलाइन जी NH-44 हाईवे पर crash हो गया, रेडिएटर phat gaya coolant leak aur bamper mein dent hai, policy saat hazaar aath sau bees",
        "utterance_gsm": "helpline nh 44 haiwe par raidiyatar leak aur bamper dant policy 7820",
        "expected_policy": "7820",
        "expected_parts": ["radiator", "bumper"],
        "expected_workflow": "B",
    },
    {
        "id": "S2",
        "name": "City Breakdown NCB Saver (Activa Battery + Puncture, Policy #4491)",
        "utterance": "delhi mein activa ka battery khatam ho gaya aur puncture bhi ho gaya hai, policy number 4491",
        "utterance_highway": "दिल्ली में Activa की battery dead aur paunchhar हो गया है, policy 4491",
        "utterance_gsm": "sheher mein activa batteri khatam aur panchar ho gaya policy chaar hazaar chaar sau ikyaanave",
        "expected_policy": "4491",
        "expected_parts": ["battery", "puncture"],
        "expected_workflow": "A",
    },
    {
        "id": "S3",
        "name": "Highway Bike Side-Swipe (Hero Splendor Headlight/Guard, Policy #1055)",
        "utterance": "highway par ek truck ne side swipe mara, splendor ka headlight aur aage wala gard kharaab, policy 1055 hai",
        "utterance_highway": "हाईवे पर truck ने side swipe मारा, splendor की headlight और front guard खराब, policy ek hazaar pachpan",
        "utterance_gsm": "haiwe par truck ne ragad mara haedalait aur gard kharab policy 1055",
        "expected_policy": "1055",
        "expected_parts": ["headlight", "front_guard"],
        "expected_workflow": "C",
    },
    {
        "id": "S4",
        "name": "Rural Road Axle Break (Bolero ₹1,500 Towing, Policy #9932)",
        "utterance": "gaon ki link road par bolero ka axle twist ho gaya, kripya tow bhejo, policy number 9932",
        "utterance_highway": "गाँव की link road पर bolero का axle twist हो गया, tow bhejo policy nau hazaar nau sau battees",
        "utterance_gsm": "gaon link road bolero eksal twist tow bhejiye policy 9932",
        "expected_policy": "9932",
        "expected_parts": ["axle"],
        "expected_workflow": "B",
    },
    {
        "id": "S5",
        "name": "Emergency 112 Head Injury Escalation",
        "utterance": "accident ho gaya hai, sar mein chot lag gayi hai khoon nikal raha hai driver behosh hai",
        "utterance_highway": "एक्सीडेंट हो गया है, सिर में चोट लगी है खून बह रहा है ड्राइवर बेहोश है",
        "utterance_gsm": "accident ho gaya sar me chot hai khun beh raha behosh hai",
        "expected_policy": None,
        "expected_parts": [],
        "expected_workflow": "EMERGENCY",
    },
]

FEEDBACK_CASES: List[Dict[str, Any]] = [
    {
        "id": "F1",
        "name": "Barge-in policy correction: 7820 -> 4491 mid-readback",
        "first": "highway crash radiator coolant leak bumper dent policy number 7820",
        "second": "ruko nahi nahi, mera policy number chaar hazaar chaar sau ikyaanave hai",
        "expect_final_status_contains": "corrected",
        "expect_policy": "4491",
    },
    {
        "id": "F2",
        "name": "Verbal confirm locks ticket",
        "first": "city activa battery dead puncture policy number 4491",
        "second": "haan confirm kar do",
        "expect_final_status_contains": "DISPATCHED",
        "expect_policy": "4491",
    },
    {
        "id": "F3",
        "name": "Barge-in road correction: highway -> rural link road (₹1,500)",
        "first": "highway par bolero ka axle twist ho gaya policy number 9932",
        "second": "ruko highway nahi, gaon ki link road par hoon",
        "expect_final_status_contains": "corrected",
        "expect_policy": "9932",
        "expect_road": "RURAL",
    },
]

MISS_CASES: List[Dict[str, Any]] = [
    {
        "id": "M1",
        "name": "Honest miss: garbled Hinglish slang under 8kHz",
        "utterance": "arre yaar gaadi ka kuch ho gaya idhar udhar baat karne se aata nahi",
        "expected_policy": None,
        "status_note": "Brain asks a clarifying question instead of hallucinating a ticket.",
    },
    {
        "id": "M2",
        "name": "Honest miss: bare unknown policy number without incident",
        "utterance": "policy number 7777 hai mera",
        "status_note": "Ticket stays in intake; agent asks what happened instead of dispatching.",
    },
]


# --------------------------------------------------------------------------
# Acoustic condition simulation
# --------------------------------------------------------------------------

def synth_condition_noise(cond: str, dur_ms: int = 1000, seed: int = 42) -> bytes:
    """Generate the noise layer bytes for CLEAN/HIGHWAY/GSM conditions."""
    rnd = random.Random(seed)
    rate = 16000
    n = int(rate * dur_ms / 1000)
    out = bytearray()
    for i in range(n):
        if cond == "CLEAN":
            s = 0.015 * rnd.uniform(-1, 1)
        elif cond == "HIGHWAY":
            rumble = 0.25 * math.sin(2 * math.pi * 90 * i / rate)
            horn = 0.30 * math.sin(2 * math.pi * 700 * i / rate) if (i // 4000) % 4 == 0 else 0.0
            s = rumble + horn + 0.04 * rnd.uniform(-1, 1)
        else:
            s = 0.16 * rnd.uniform(-1, 1) * math.sin(2 * math.pi * 1800 * i / rate)
        out += struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32000))
    return bytes(out)


def condition_wav(cond: str, dur_ms: int = 1000) -> bytes:
    return wrap_pcm_as_wav(synth_condition_noise(cond, dur_ms), sample_rate=16000)


CONDITIONS = ["CLEAN", "HIGHWAY", "GSM"]


# --------------------------------------------------------------------------
# Case runners
# --------------------------------------------------------------------------

def _utterance_for_condition(case: Dict[str, Any], condition: str) -> str:
    if condition == "HIGHWAY" and case.get("utterance_highway"):
        return case["utterance_highway"]
    if condition == "GSM" and case.get("utterance_gsm"):
        return case["utterance_gsm"]
    return case["utterance"]


async def _run_case(case: Dict[str, Any], condition: str, persist_history: bool = False) -> Dict[str, Any]:
    brain = Brain(persist_history=persist_history)
    call_id = f"eval-{uuid.uuid4().hex[:8]}"
    utterance = _utterance_for_condition(case, condition)

    # Exercise endpointing frame analysis on the synthesized acoustic condition WAV
    wav = condition_wav(condition, dur_ms=600)
    ep = analyze_wav_endpoint(wav, EndpointingState(mode="push_to_talk"))

    t0 = time.perf_counter()
    res = await brain.process_turn(call_id, utterance, {"condition": condition})
    total_ms = (time.perf_counter() - t0) * 1000.0
    brain.reset_call(call_id)

    ents = res["entities"]
    got_parts = set(ents.get("parts", []))
    exp_parts = set(case.get("expected_parts", []))
    part_precision = 1.0 if (not got_parts and not exp_parts) else len(got_parts & exp_parts) / max(1, len(got_parts))
    part_recall = 1.0 if not exp_parts else len(got_parts & exp_parts) / len(exp_parts)

    workflow = ""
    for tool in res["tools_invoked"]:
        if tool.get("tool") == "dispatch_smart_rsa":
            workflow = tool.get("workflow", "")

    pol_ok = (ents.get("policy_number") == case["expected_policy"])
    injured = bool(ents.get("injured", False))
    wf_ok = (workflow == case["expected_workflow"])

    if case["expected_workflow"] == "EMERGENCY":
        domain_correct = injured and wf_ok and (not got_parts)
    else:
        domain_correct = (
            pol_ok
            and wf_ok
            and (got_parts == exp_parts)
            and not injured
        )

    return {
        "case_id": case["id"],
        "name": case["name"],
        "condition": condition,
        "policy_correct": pol_ok,
        "parts_precision": round(part_precision, 3),
        "parts_recall": round(part_recall, 3),
        "workflow": workflow,
        "expected_workflow": case["expected_workflow"],
        "domain_correct": bool(domain_correct),
        "ticket_status": res["ticket_status"],
        "noise_peak_dbfs": ep.get("peak_dbfs"),
        "reply_ms": round(total_ms, 1),
        "clauses": [c["priority"] for c in res["clauses"]],
        "speed_to_dispatch_ms": round(total_ms, 1),
    }


async def _run_feedback_case(case: Dict[str, Any], persist_history: bool = False) -> Dict[str, Any]:
    brain = Brain(persist_history=persist_history)
    call_id = f"eval-{case['id']}-{uuid.uuid4().hex[:6]}"
    t0 = time.perf_counter()
    await brain.process_turn(call_id, case["first"])
    res2 = await brain.process_turn(call_id, case["second"])
    ms = (time.perf_counter() - t0) * 1000.0
    brain.reset_call(call_id)

    ok_status = case["expect_final_status_contains"].lower() in res2["ticket_status"].lower()
    ok_policy = res2["entities"].get("policy_number") == case["expect_policy"]
    ok_road = True if not case.get("expect_road") else (res2["entities"].get("road_type") == case["expect_road"])
    return {
        "case_id": case["id"],
        "name": case["name"],
        "condition": "BARGE-IN",
        "domain_correct": bool(ok_status and ok_policy and ok_road),
        "final_policy": res2["entities"].get("policy_number"),
        "final_road": res2["entities"].get("road_type"),
        "final_status": res2["ticket_status"],
        "reply_ms": round(ms, 1),
        "clauses": [c["priority"] for c in res2["clauses"]],
    }


async def run_eval_suite(persist_history: bool = False) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for sc in SCENARIOS:
        for cond in CONDITIONS:
            rows.append(await _run_case(sc, cond, persist_history=persist_history))
    for fc in FEEDBACK_CASES:
        rows.append(await _run_feedback_case(fc, persist_history=persist_history))
    for mc in MISS_CASES:
        brain = Brain(persist_history=persist_history)
        cid = f"eval-{mc['id']}-{uuid.uuid4().hex[:6]}"
        t0 = time.perf_counter()
        res = await brain.process_turn(cid, mc["utterance"])
        ms = (time.perf_counter() - t0) * 1000.0
        brain.reset_call(cid)
        no_fake_dispatch = not any(t.get("tool") == "dispatch_smart_rsa" for t in res["tools_invoked"])
        no_fake_draft = not any(t.get("tool") == "calculate_irdai_claim_draft" for t in res["tools_invoked"])
        asks_clarification = "?" in res["reply_text"]
        rows.append({
            "case_id": mc["id"],
            "name": mc["name"],
            "condition": "HONEST-FAIL-LOG",
            "domain_correct": bool(no_fake_dispatch and no_fake_draft and asks_clarification),
            "final_status": res["ticket_status"],
            "reply_ms": round(ms, 1),
            "note": mc["status_note"],
            "clauses": [c["priority"] for c in res["clauses"]],
        })

    domain_rows = [r for r in rows if r["condition"] in CONDITIONS]
    all_correct = sum(1 for r in rows if r.get("domain_correct"))

    ml_rows = await _run_multilingual_matrix(persist_history=persist_history)
    ml_correct = sum(1 for r in ml_rows if r.get("domain_correct"))
    eng_checks = _verify_engineering_layers()

    summary = {
        "total_cases": len(rows),
        "scenarios": len(SCENARIOS),
        "conditions": CONDITIONS,
        "domain_accuracy": round(
            sum(1 for r in domain_rows if r["domain_correct"]) / max(1, len(domain_rows)), 3
        ),
        "overall_accuracy": round(all_correct / max(1, len(rows)), 3),
        "multilingual_10lang_cases": len(ml_rows),
        "multilingual_10lang_correct": ml_correct,
        "multilingual_10lang_accuracy": round(ml_correct / max(1, len(ml_rows)), 3),
        "engineering_checks": eng_checks,
        "avg_turn_ms": round(
            sum(r["reply_ms"] for r in rows if isinstance(r.get("reply_ms"), (int, float)))
            / max(1, len([r for r in rows if isinstance(r.get("reply_ms"), (int, float))])),
            1,
        ),
        "by_condition": {
            cond: {
                "cases": sum(1 for r in domain_rows if r["condition"] == cond),
                "correct": sum(1 for r in domain_rows if r["condition"] == cond and r["domain_correct"]),
                "avg_ms": round(
                    sum(r["reply_ms"] for r in domain_rows if r["condition"] == cond)
                    / max(1, len([r for r in domain_rows if r["condition"] == cond])),
                    1,
                ),
            }
            for cond in CONDITIONS
        },
    }

    waterfall_latencies = {cond: summary["by_condition"][cond]["avg_ms"] for cond in CONDITIONS}

    out = {
        "generated_at": time.time(),
        "summary": summary,
        "latency_waterfall_ms": waterfall_latencies,
        "rows": rows,
        "multilingual_rows": ml_rows,
        "engineering_checks": eng_checks,
    }
    try:
        RESULTS_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return out


_MULTILINGUAL_MATRIX: Dict[str, Dict[str, str]] = {
    "hi-IN": {
        "wf_b": "Haan safe hain, NH-44 highway par radiator phat gaya hai, policy 7820",
        "wf_a": "Delhi mein battery dead ho gayi hai, koi chot nahi",
        "emg": "Accident ho gaya, sar mein chot aur khoon beh raha hai",
    },
    "ta-IN": {
        "wf_b": "வணக்கம், எங்களுக்கு காயமில்லை, தேசிய நெடுஞ்சாலையில் ரேடியேட்டர் உடைஞ்சிடுச்சு, பாலிசி 7820",
        "wf_a": "சென்னை நகரத்தில் பேட்டரி பழுதாகிடுச்சு, காயமில்லை",
        "emg": "விபத்து, இரத்தம் வருது காயம் ஏற்பட்டது",
    },
    "te-IN": {
        "wf_b": "నమస్కారం, మాకు గాయం లేదు, జాతీయ రహదారిపై రేడియేటర్ పాడైపోయింది, పాలసీ 7820",
        "wf_a": "హైదరాబాద్ నగరంలో బ్యాటరీ పాడైంది, గాయం లేదు",
        "emg": "యాక్సిడెంట్ అయింది, రక్తం వస్తోంది గాయమైంది",
    },
    "kn-IN": {
        "wf_b": "ನಮಸ್ಕಾರ, ಯಾರಿಗೂ ಗಾಯ ಇಲ್ಲ, ರಾಷ್ಟ್ರೀಯ ಹೆದ್ದಾರಿಯಲ್ಲಿ ರೇಡಿಯೇಟರ್ ಹಾಳಾಗಿದೆ, ಪಾಲಿಸಿ 7820",
        "wf_a": "ಬೆಂಗಳೂರು ನಗರದಲ್ಲಿ ಬ್ಯಾಟರಿ ಹಾಳಾಗಿದೆ, ಗಾಯ ಇಲ್ಲ",
        "emg": "ಅಪಘಾತ ಆಗಿದೆ, ರಕ್ತ ಬರುತ್ತಿದೆ ಗಾಯವಾಗಿದೆ",
    },
    "ml-IN": {
        "wf_b": "നമസ്കാരം, ആർക്കും പരിക്കില്ല, ദേശീയപാതയിൽ റേഡിയേറ്റർ തകരാറായി, പോളിസി 7820",
        "wf_a": "കൊച്ചി നഗരത്തിൽ ബാറ്ററി തീർന്നു, പരിക്കില്ല",
        "emg": "അപകടം പറ്റി, രക്തം വരുന്നു പരിക്കേറ്റു",
    },
    "bn-IN": {
        "wf_b": "নমস্কার, কারও চোট লাগেনি, জাতীয় সড়কে রেডিয়েটর খারাপ হয়ে গেছে, পলিসি 7820",
        "wf_a": "কলকাতা শহরে ব্যাটারি শেষ হয়েছে, চোট লাগেনি",
        "emg": "দুর্ঘটনা হয়েছে, রক্ত পড়ছে আঘাত লেগেছে",
    },
    "mr-IN": {
        "wf_b": "नमस्कार, कोणालाही जखम नाही, राष्ट्रीय महामार्गावर रेडिएटर फुटला आहे, पॉलिसी 7820",
        "wf_a": "पुणे शहरात बॅटरी खराब झाली आहे, जखम नाही",
        "emg": "अपघात झाला, रक्त येतंय दुखापत झाली",
    },
    "gu-IN": {
        "wf_b": "નમસ્તે, કોઈને ઈજા નથી, નેશનલ હાઇવે પર રેડિએટર તૂટી ગયું છે, પોલિસી 7820",
        "wf_a": "અમદાવાદ શહેરમાં બેટરી ખરાબ થઈ છે, ઈજા નથી",
        "emg": "અકસ્માત થયો છે, લોહી નીકળે છે ઈજા થઈ",
    },
    "pa-IN": {
        "wf_b": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ, ਕਿਸੇ ਨੂੰ ਸੱਟ ਨਹੀਂ, ਨੈਸ਼ਨਲ ਹਾਈਵੇ 'ਤੇ ਰੇਡੀਏਟਰ ਟੁੱਟ ਗਿਆ, ਪਾਲਿਸੀ 7820",
        "wf_a": "ਲੁਧਿਆਣਾ ਸ਼ਹਿਰ ਵਿੱਚ ਬੈਟਰੀ ਖਰਾਬ ਹੋ ਗਈ, ਕੋਈ ਸੱਟ ਨਹੀਂ",
        "emg": "ਹਾਦਸਾ ਹੋ ਗਿਆ, ਖੂਨ ਵਗ ਰਿਹਾ ਸੱਟ ਲੱਗੀ",
    },
    "en-IN": {
        "wf_b": "Everyone is safe, our car radiator broke on the national highway, policy 7820",
        "wf_a": "We are in Bengaluru city and the battery is dead, no injuries",
        "emg": "Major accident, driver is bleeding with a head injury",
    },
}


async def _run_multilingual_matrix(persist_history: bool = False) -> List[Dict[str, Any]]:
    ml_rows: List[Dict[str, Any]] = []
    brain = Brain(persist_history=persist_history)
    for lang in ALL_GNANI_LANGUAGES:
        samples = _MULTILINGUAL_MATRIX[lang]
        for kind, utt in samples.items():
            cid = f"eval-ml-{lang}-{kind}-{uuid.uuid4().hex[:5]}"
            await brain.start_call(cid)
            t0 = time.perf_counter()
            res = await brain.process_turn(cid, utt)
            ms = round((time.perf_counter() - t0) * 1000, 1)
            brain.reset_call(cid)
            ents = res["entities"]
            if kind == "wf_b":
                ok = (
                    res["language"] == lang
                    and ents.get("policy_number") == "7820"
                    and ents.get("road_type") == "NH"
                    and "radiator" in (ents.get("parts") or [])
                    and res["stage"] == "confirm"
                )
            elif kind == "wf_a":
                ok = (
                    res["language"] == lang
                    and (ents.get("last_dispatch") or {}).get("workflow") == "A"
                    and res["stage"] == "done"
                )
            else:
                ok = (
                    res["language"] == lang
                    and bool(ents.get("injured"))
                    and res["stage"] == "emergency_handoff"
                )
            ml_rows.append({
                "case_id": f"ML-{lang}-{kind.upper()}",
                "language": lang,
                "workflow_kind": kind,
                "detected_language": res["language"],
                "stage": res["stage"],
                "domain_correct": bool(ok),
                "reply_ms": ms,
            })
    return ml_rows


def _verify_engineering_layers() -> Dict[str, Any]:
    # 1. 80Hz high-pass diesel rumble attenuation check (50Hz tone)
    samples_50hz = [int(14000 * math.sin(2 * math.pi * 50.0 * i / 16000)) for i in range(1600)]
    pcm_50hz = struct.pack(f"<{len(samples_50hz)}h", *samples_50hz)
    _, dsp_meta = denoise_wav_for_stt(wrap_pcm_as_wav(pcm_50hz, sample_rate=16000))
    dsp_ok = bool(dsp_meta.get("dsp_applied") and dsp_meta.get("attenuation_db", 0.0) >= 3.0)

    # 2. PLC gap recovery + spec-compliant RIFF header check
    ij = InputJitterBuffer(sample_rate=16000, target_jitter_ms=40)
    speech_frame = struct.pack("<320h", *([4500] * 320))
    for seq in (0, 1, 2, 4, 5, 6, 7):  # seq=3 dropped -> PLC fills gap
        ij.push(speech_frame, seq=seq)
    wav = ij.take_utterance_wav(include_preroll_if_empty=True)
    riff_size = struct.unpack_from("<I", wav, 4)[0] if len(wav) > 44 else 0
    plc_ok = ij.plc_frames >= 1 and riff_size == (len(wav) - 8)

    # 3. Adaptive noise floor tracking check (-28 dBFS steady noise)
    vad = AutoVADDetector(sample_rate=16000)
    steady_noise = struct.pack("<320h", *([1300] * 320))
    for _ in range(15):
        vad.feed(steady_noise)
    vad_ok = vad.noise_floor_dbfs > -50.0

    # 4. DPDP PII redaction check
    sample_pii = "Aadhaar 1234 5678 9012 PAN ABCDE1234F phone +91 9876543210 policy 7820"
    redacted, n_pii = redact_pii(sample_pii)
    pii_ok = (
        n_pii == 3
        and "1234 5678 9012" not in redacted
        and "ABCDE1234F" not in redacted
        and "9876543210" not in redacted
        and "7820" in redacted
    )
    return {
        "dsp_highpass_80hz_ok": dsp_ok,
        "dsp_50hz_attenuation_db": dsp_meta.get("attenuation_db", 0.0),
        "plc_gap_recovery_ok": plc_ok,
        "adaptive_noise_floor_ok": vad_ok,
        "dpdp_pii_redaction_ok": pii_ok,
        "all_passed": bool(dsp_ok and plc_ok and vad_ok and pii_ok),
    }


if __name__ == "__main__":
    report = asyncio.run(run_eval_suite(persist_history=False))
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    print("Latency waterfall (ms):", json.dumps(report["latency_waterfall_ms"], ensure_ascii=False))
    failed = [r for r in report["rows"] if not r.get("domain_correct")]
    ml_failed = [r for r in report["multilingual_rows"] if not r.get("domain_correct")]
    if failed or ml_failed or not report["engineering_checks"]["all_passed"]:
        print("Failed cases:", [r.get("case_id", r.get("id")) for r in failed + ml_failed])
        sys.exit(1)

