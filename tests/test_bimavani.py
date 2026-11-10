"""
BimaVani — Comprehensive Pytest Suite (tests/test_bimavani.py)
Covers all 6 engineering guardrails (G1-G6), Devanagari transliteration,
domain triage/RSA/IRDAI math, multi-turn Brain orchestration, 20-case eval,
Gnani client contracts, FastAPI endpoints, and frontend assets.
"""
from __future__ import annotations

import asyncio
import base64
import json
import struct
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import eval_runner, gnani_client
from backend.main import APP_ROOT, BRAIN, app
from backend.pipeline.barge_in import TurnStateMachine
from backend.pipeline.brain import Brain, _MEMORY_PATH
from backend.pipeline.clause_buffer import ClauseBuffer, split_into_clauses
from backend.pipeline.endpointing import (
    AutoVADDetector,
    EndpointingState,
    analyze_wav_endpoint,
)
from backend.pipeline.queues import BoundedPipelineQueue
from backend.rsa_and_claim_rules import (
    POLICIES,
    calculate_irdai_claim_draft,
    confirm_detection,
    detect_drivability,
    dispatch_smart_rsa,
    extract_parts,
    extract_policy_number,
    extract_road_type,
    parse_spoken_numbers,
    triage_injury_and_safety,
)
from backend.translit import to_hinglish


def _pcm_frame(amp: int, n_samples: int = 320) -> bytes:
    return struct.pack(f"<{n_samples}h", *([amp] * n_samples))


@pytest.fixture(autouse=True)
def _restore_gnani_key():
    orig = gnani_client.CONFIG.api_key
    yield
    gnani_client.set_api_key(orig)


# ===========================================================================
# G1 — Endpointing & Auto-VAD
# ===========================================================================

def test_vad_ignores_short_horn_burst_and_endpoints_after_silence():
    vad = AutoVADDetector(sample_rate=16000, vad_silence_ms=600)
    speech = _pcm_frame(4000)      # ~ -18 dBFS
    mid_noise = _pcm_frame(320)    # ~ -40 dBFS (between -44 and -34 dBFS)
    quiet = _pcm_frame(60)         # ~ -54 dBFS

    # 80ms horn spike (< MIN_SPEECH_MS=220ms) must NOT trigger speech
    events = [vad.feed(speech) for _ in range(4)]
    assert "speech" not in events

    # Drain decay, then feed 240ms real speech (12 x 20ms)
    for _ in range(4):
        vad.feed(quiet)
    events = [vad.feed(speech) for _ in range(12)]
    assert "speech" in events

    # 600ms of mid-level noise must NOT accumulate silence
    mid_events = [vad.feed(mid_noise) for _ in range(30)]
    assert "endpoint" not in mid_events

    # 600ms of genuine quiet (< -44 dBFS) triggers endpoint
    quiet_events = [vad.feed(quiet) for _ in range(30)]
    assert quiet_events[-1] == "endpoint"


def test_analyze_wav_endpoint_measures_frames_and_dbfs():
    pcm = _pcm_frame(4000) * 15 + _pcm_frame(50) * 35
    wav = gnani_client.wrap_pcm_as_wav(pcm, sample_rate=16000)
    tel = analyze_wav_endpoint(wav, EndpointingState(mode="auto_vad", vad_silence_ms=600))
    assert tel["frames"] == 50
    assert tel["speech_frames"] == 15
    assert tel["vad_endpoint_detected"] is True


# ===========================================================================
# G2 — Gnani Client, Semaphore(1), Retries, WAV Framing & Cache
# ===========================================================================

def test_semaphore_is_one_and_reentrant_across_loops():
    async def _check():
        sem = gnani_client._sem()
        assert sem._value == 1  # noqa: SLF001
        return True

    assert asyncio.run(_check()) is True
    assert asyncio.run(_check()) is True


def test_wav_header_44_bytes_and_empty_rejection():
    hdr = gnani_client.make_wav_header(1600, sample_rate=24000)
    assert len(hdr) == 44
    assert hdr[:4] == b"RIFF" and hdr[8:12] == b"WAVE"

    assert gnani_client.wrap_pcm_as_wav(b"") == b""
    assert gnani_client.is_valid_wav(b"") is False
    assert gnani_client.is_valid_wav(hdr) is False  # header-only 44 bytes has 0 audio samples

    wrapped = gnani_client.wrap_pcm_as_wav(b"\x01\x00" * 100, sample_rate=24000)
    assert gnani_client.is_valid_wav(wrapped) is True

    # 3-byte "RIF" prefix is NOT valid RIFF/WAVE and gets wrapped cleanly
    bad = b"RIF\x00\x01\x02"
    fixed = gnani_client.wrap_pcm_as_wav(bad, sample_rate=24000)
    assert gnani_client.is_valid_wav(fixed) is True


def test_resample_wav_to_16k():
    wav24 = gnani_client.fallback_tts_wav("Namaste BimaVani", sample_rate=24000)
    wav16 = gnani_client.resample_wav_to_16k(wav24)
    assert gnani_client.is_valid_wav(wav16)
    rate = struct.unpack_from("<I", wav16, 24)[0]
    assert rate == 16000


def test_never_timbre_v2_and_cache_mode_isolation():
    assert gnani_client.CONFIG.tts_model != "timbre-v2.0"
    assert gnani_client.CONFIG.tts_model in gnani_client.ALLOWED_TTS_MODELS

    gnani_client.set_api_key("")
    k_fallback = gnani_client.cache_key("hello", "Nalini", "timbre-v2.5", "hi-IN")
    gnani_client.set_api_key("test-live-key")
    k_live = gnani_client.cache_key("hello", "Nalini", "timbre-v2.5", "hi-IN")
    assert k_fallback != k_live


def test_rate_limit_retry_guard_and_fail_fast_401(monkeypatch):
    gnani_client.set_api_key("dummy-key-123456")

    # 1. Retry on 429 then succeed
    calls = {"n": 0}

    class _FakeResp:
        def __init__(self, code: int, payload: Dict[str, Any]):
            self.status_code = code
            self._payload = payload
            self.headers = {"content-type": "application/json"}
            self.content = b""
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class _FakeClient429:
        def __init__(self, *a, **kw):
            pass

        async def post(self, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return _FakeResp(429, {"error": "rate limit"})
            return _FakeResp(200, {"transcript": "radiator phat gaya", "request_id": "req-1"})

        async def aclose(self):
            pass

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient429)
    res = asyncio.run(gnani_client.transcribe_wav(gnani_client.fallback_tts_wav("hi")))
    assert calls["n"] == 2
    assert res["fallback"] is False
    assert res["transcript"] == "radiator phat gaya"

    # 2. Fail fast on 401 (no retry)
    calls401 = {"n": 0}

    class _FakeClient401(_FakeClient429):
        async def post(self, *a, **kw):
            calls401["n"] += 1
            return _FakeResp(401, {"error": "unauthorized"})

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient401)
    res401 = asyncio.run(gnani_client.transcribe_wav(gnani_client.fallback_tts_wav("hi")))
    assert calls401["n"] == 1
    assert res401["fallback"] is True
    assert res401["error"] == "HTTP 401"


# ===========================================================================
# G3 — Bounded Queues
# ===========================================================================

def test_bounded_queue_backpressure_drop_oldest_vs_newest():
    async def _run():
        q = BoundedPipelineQueue("test", maxsize=32)
        for i in range(44):
            await q.put(i, drop_oldest_on_full=True)
        assert q.depth == 32
        assert q.telemetry.dropped_backpressure == 12
        assert q.telemetry.backpressure_active is True
        assert q.get_nowait() == 12

        q2 = BoundedPipelineQueue("test2", maxsize=2)
        assert await q2.put("a", drop_oldest_on_full=False) is True
        assert await q2.put("b", drop_oldest_on_full=False) is True
        assert await q2.put("c", drop_oldest_on_full=False) is False
        assert q2.telemetry.dropped_backpressure == 1
        assert [q2.get_nowait(), q2.get_nowait()] == ["a", "b"]

    asyncio.run(_run())


# ===========================================================================
# G4 — 2-Clause Buffer
# ===========================================================================

def test_clause_buffer_max_two_and_hook_first():
    batch = split_into_clauses(
        "Shukar hai aap safe hain! Main flatbed tow bhej rahi hoon. Claim draft bhi ready hai.",
        "turn-1",
    )
    cl = batch.as_list()
    assert len(cl) == 2
    assert cl[0]["priority"] == "clause1_fast_hook"
    assert cl[1]["priority"] == "clause2_details"


def test_clause_buffer_preserves_irdai_acronym_and_no_empty_clause():
    b1 = split_into_clauses(
        "I.R.D.A.I. niyam se aapko ₹1,500 milenge. Hum local crane authorize kar rahe hain.",
        "t-abbr",
    )
    assert b1.as_list()[0]["text"].startswith("I.R.D.A.I. niyam")

    b2 = split_into_clauses("A" * 130, "t-nospace")
    assert all(c["text"] for c in b2.as_list())
    assert len(b2.as_list()) == 1

    cb = ClauseBuffer()
    out = cb.ingest("t2", "")
    assert len(out.as_list()) == 1 and out.as_list()[0]["text"]


# ===========================================================================
# G6 — Barge-In State Machine
# ===========================================================================

def test_state_machine_transitions_and_barge_in_flush_under_50ms():
    async def _run():
        sm = TurnStateMachine("s-test")
        assert sm.transition("LISTENING") is True
        # LISTENING cannot skip THINKING straight to SPEAKING
        assert sm.transition("SPEAKING") is False
        assert sm.transition("THINKING") is True
        assert sm.transition("SPEAKING") is True

        async def _slow():
            await asyncio.sleep(5)

        t = asyncio.create_task(_slow())
        sm.register_task(t)
        ev = await sm.trigger_barge_in("Ruko policy 4491", flush_fn=lambda: {"flushed_clauses": 3})
        await asyncio.sleep(0)
        assert ev.flush_latency_ms < 50.0
        assert ev.cancelled_tasks == 1
        assert ev.flushed_clauses == 3
        assert sm.state == "INTERRUPTED"
        assert sm.resume_listening() is True
        assert sm.state == "LISTENING"

    asyncio.run(_run())


# ===========================================================================
# Transliteration & Domain Engine Regressions
# ===========================================================================

def test_precomposed_and_decomposed_nukta_translit():
    assert "d" in to_hinglish("गा\u095cी")
    assert "z" in to_hinglish("ह\u095bार")
    assert "gaadi" in to_hinglish("गाड़ी")


def test_injury_negation_and_mixed_clause_triage():
    assert triage_injury_and_safety("ना भाई, किसी का चोट ना लग की हम सब safe रखती।")["injured"] is False
    assert triage_injury_and_safety("ओ भाई, चोट ना लगी किसी के भी?")["injured"] is False
    assert triage_injury_and_safety("ambulance ki koi zarurat nahi hai")["injured"] is False

    assert triage_injury_and_safety("minor injury in accident")["injured"] is True
    assert triage_injury_and_safety("unknown injury")["injured"] is True
    assert triage_injury_and_safety("pehle chot nahi lagi thi ab khoon beh raha hai")["injured"] is True


def test_drivability_hindi_and_puncture_crash_combo():
    assert detect_drivability([], "गाड़ी चलने लायक नहीं है")[0] is False
    assert detect_drivability([], "dono tyre toot gaye")[0] is False
    assert detect_drivability(["headlight", "puncture"], "")[0] is False
    assert detect_drivability(["battery"], "gaadi start nahi ho rahi")[0] is True


def test_part_extraction_no_false_positives_and_negation():
    assert extract_parts("even system ko nahi hai airbag") == []
    assert extract_parts("battery khatam ho gayi") == ["battery"]
    assert extract_parts("radiator phat gaya start nahi ho rahi") == ["radiator"]
    assert extract_parts("clutch wire damage nahi hai") == []


def test_spoken_hindi_numerals_and_policy_extraction():
    assert 7820 in parse_spoken_numbers("saat hazaar aath sau bees")
    assert 4491 in parse_spoken_numbers("chaar hazaar chaar sau ikyaanave")
    assert 1055 in parse_spoken_numbers("ek hazaar pachpan")
    assert 9932 in parse_spoken_numbers("nau hazaar nau sau battees")
    assert 3310 in parse_spoken_numbers("teen hazaar teen sau das")
    assert extract_policy_number("mera policy number 0782 hai") == "0782"
    assert extract_policy_number("4321 hai", expecting_policy=True) == "4321"
    assert extract_policy_number("ek to chaar") is None
    assert extract_policy_number("ruko policy 7820 nahi 4491 hai") == "4491"


def test_road_type_and_confirm_detection():
    assert extract_road_type("city ke highway pe kahin bhi nahi, gaon mein hoon") == "RURAL"
    assert extract_road_type("unhone kaha yahan hain") == "CITY"
    assert extract_road_type("NH-44 Karnal ke paas") == "NH"

    assert confirm_detection("toko mat broken smoke book") == "none"
    assert confirm_detection("mera policy number 7820 hai") == "none"
    assert confirm_detection("mera policy number 4491 hai", prior_policy="7820") == "correct"
    assert confirm_detection("haan kar do") == "confirm"


def test_irdai_depreciation_age_scaling_and_city_garage():
    d0 = calculate_irdai_claim_draft("9932", ["axle"], age_of_vehicle_years=0.5)
    d3 = calculate_irdai_claim_draft("9932", ["axle"], age_of_vehicle_years=3.0)
    db = calculate_irdai_claim_draft("9932", ["bumper"], age_of_vehicle_years=2.0)
    assert d0["line_items"][0]["depreciation_pct_applied"] < d3["line_items"][0]["depreciation_pct_applied"]
    assert db["line_items"][0]["depreciation_pct_applied"] > 0

    d7820 = calculate_irdai_claim_draft("7820", ["radiator", "bumper"])
    assert all(li["depreciation_pct_applied"] == 0.0 for li in d7820["line_items"])

    disp_city_4w = dispatch_smart_rsa("CITY", ["headlight"], True, POLICIES["7820"])
    assert "2W" not in disp_city_4w["destination_garage"]["name"]

    disp_a_4w = dispatch_smart_rsa("CITY", ["battery"], True, POLICIES["7820"])
    assert "2-Wheeler" not in disp_a_4w["dispatch_vendor"]


# ===========================================================================
# Brain Multi-Turn Orchestration
# ===========================================================================

def test_brain_staged_flow_guards_and_done_immutability():
    async def _run():
        b = Brain(persist_history=False)
        await b.start_call("t-flow")

        # Early safety + policy must NOT jump to DONE with empty parts
        r1 = await b.process_turn("t-flow", "haan hum safe hain, 7820 hai")
        assert r1["stage"] == "incident"
        assert r1["entities"].get("last_draft") is None

        # Provide problem + NH-44 -> CONFIRM_PENDING
        r2 = await b.process_turn("t-flow", "radiator phat gaya NH-44 par")
        assert r2["stage"] == "confirm"
        assert r2["ticket_status"] == "CONFIRM_PENDING"
        claim_id = r2["entities"]["last_draft"]["claim_id"]

        # Confirm -> DONE
        r3 = await b.process_turn("t-flow", "haan kar do")
        assert r3["stage"] == "done"
        assert "APPLIED" in r3["ticket_status"]

        # Follow-up question after DONE must NOT mint a new claim ID or revert
        r4 = await b.process_turn("t-flow", "tow truck kitni der mein aayega?")
        assert r4["stage"] == "done"
        assert r4["entities"]["last_draft"]["claim_id"] == claim_id

    asyncio.run(_run())


def test_brain_emergency_112_and_no_false_1033():
    async def _run():
        b = Brain(persist_history=False)
        await b.start_call("t-emg")
        r = await b.process_turn("t-emg", "sar mein chot lagi hai, yahan hain hum")
        assert r["stage"] == "emergency_handoff"
        assert r["entities"]["injured"] is True
        assert not r["entities"].get("nhai_patrol")

        # Distressed follow-up stays in emergency handoff
        r2 = await b.process_turn("t-emg", "jaldi bhejiye")
        assert r2["stage"] == "emergency_handoff"

    asyncio.run(_run())


# ===========================================================================
# 20-Case Eval Benchmark + Non-Pollution Check
# ===========================================================================

def test_eval_suite_20_cases_and_no_history_pollution():
    before_bytes = _MEMORY_PATH.read_bytes() if _MEMORY_PATH.exists() else None
    res = asyncio.run(eval_runner.run_eval_suite(persist_history=False))
    after_bytes = _MEMORY_PATH.read_bytes() if _MEMORY_PATH.exists() else None

    assert res["summary"]["total_cases"] == 20
    assert res["summary"]["domain_accuracy"] == 1.0
    assert res["summary"]["overall_accuracy"] == 1.0
    assert before_bytes == after_bytes


# ===========================================================================
# FastAPI HTTP E2E + Frontend Assets
# ===========================================================================

def test_api_e2e_lifecycle_and_frontend_contracts():
    # Use non-persisting Brain during HTTP tests so call_history.json stays untouched
    orig_persist = BRAIN.persist_history
    BRAIN.persist_history = False
    try:
        with TestClient(app) as client:
            # 1. Static frontend
            r_idx = client.get("/")
            assert r_idx.status_code == 200
            assert "BimaVani" in r_idx.text
            assert client.get("/style.css").status_code == 200
            r_js = client.get("/app.js")
            assert r_js.status_code == 200
            assert 'call_id: "live"' not in r_js.text

            # 2. Health, config, hot key reload, verify
            h = client.get("/api/health").json()
            assert h["ok"] is True
            assert "stage_names" not in h["stages"]
            assert len(h["stages"]) == 8

            cfg = client.get("/api/config").json()
            assert cfg["semaphore_limit"] == 1

            k_res = client.post("/api/config/key", json={"api_key": ""}).json()
            assert k_res["live_mode"] is False

            v_res = client.post("/api/verify-gnani").json()
            assert v_res["fallback"] is True

            # 3. Call start -> greeting WAVs
            cid = "pytest-e2e-01"
            st = client.post("/api/call/start", json={"call_id": cid}).json()
            assert st["ok"] is True
            assert len(st["clauses"]) == 2
            for cl in st["clauses"]:
                b64 = cl["audio_base64"].split(",", 1)[1]
                assert gnani_client.is_valid_wav(base64.b64decode(b64))

            # 4. Turn 1 (incident + NH-44 + 7820)
            t1 = client.post(
                "/api/call/turn",
                json={
                    "call_id": cid,
                    "transcript_override": "NH-44 highway crash, radiator phat gaya hai, policy 7820",
                },
            ).json()
            assert t1["stage"] == "confirm"
            assert t1["entities"]["policy_number"] == "7820"
            assert len(t1["clauses"]) <= 2
            b64_1 = t1["clauses"][0]["audio_base64"].split(",", 1)[1]
            assert gnani_client.is_valid_wav(base64.b64decode(b64_1))

            # 5. Barge-in correction to 4491 (verifies state_before + audio in cards + [interrupted] dump!)
            bi = client.post(
                "/api/call/barge-in",
                json={"call_id": cid, "transcript": "Ruko policy number 4491 hai", "update_cards": True},
            ).json()
            assert bi["flush_latency_ms"] < 50.0
            assert bi["state_before"] == "SPEAKING"
            assert bi["interrupted_context"]["tag"] == "[interrupted]"
            assert bi["interrupted_context"]["mic_dump"].startswith("[interrupted]")
            assert bi["cards"]["entities"]["policy_number"] == "4491"
            assert bi["cards"]["clauses"][0]["audio_base64"].startswith("data:audio/wav;base64,")

            # 6. Direct TTS & Pipeline state & End call
            tts_r = client.post("/api/tts", json={"text": "Shukar hai aap safe hain."}).json()
            assert gnani_client.is_valid_wav(base64.b64decode(tts_r["audio_b64"]))

            ps = client.get("/api/pipeline-state").json()
            assert "stage_1" in ps["stages_ms"]

            end_r = client.post("/api/call/end", json={"call_id": cid}).json()
            assert end_r["ok"] is True and end_r["memory_reset"] is True
    finally:
        BRAIN.persist_history = orig_persist


# ===========================================================================
# Streaming Architecture: Input/Output Jitter Buffers, Multi-Session, [interrupted] & WS
# ===========================================================================

def test_input_and_output_jitter_buffers_and_vad_reordering():
    from backend.pipeline.jitter_buffer import InputJitterBuffer, OutputJitterBuffer

    ij = InputJitterBuffer(sample_rate=16000, target_jitter_ms=60, vad_silence_ms=600)
    quiet = _pcm_frame(50)
    speech = _pcm_frame(4500)

    # Push pre-roll quiet frames in order, then out-of-order speech frames (seq 6 before 5)
    for seq in range(4):
        ij.push(quiet, seq=seq)
    evs_ooo = []
    evs_ooo.extend(ij.push(speech, seq=4))
    evs_ooo.extend(ij.push(speech, seq=6))   # arrives early -> buffered
    evs_ooo.extend(ij.push(speech, seq=5))   # delayed -> triggers ordered drain 5 then 6
    assert ij.out_of_order >= 1

    # Complete >=220ms speech then 600ms quiet -> endpoint
    all_vad = []
    for s in range(7, 20):
        all_vad.extend(e["vad"] for e in ij.push(speech, seq=s))
    assert "speech" in all_vad

    end_vad = []
    for s in range(20, 55):
        end_vad.extend(e["vad"] for e in ij.push(quiet, seq=s))
    assert "endpoint" in end_vad

    wav = ij.take_utterance_wav()
    assert gnani_client.is_valid_wav(wav)

    # OutputJitterBuffer 120ms prebuffer + flush
    oj = OutputJitterBuffer(prebuffer_ms=120.0)
    sample_wav = gnani_client.fallback_tts_wav("Namaste ji")
    push_res = oj.push(1, sample_wav)
    assert push_res["ready"] is True
    assert push_res["buffered_ms"] > 120.0
    assert oj.flush() == 1
    assert oj.buffered_ms == 0.0


def test_session_registry_isolation_and_eviction():
    from backend.pipeline.queues import SessionRegistry

    reg = SessionRegistry(max_sessions=3, idle_ttl_s=1800.0)
    s1 = reg.get_or_create("s1")
    s2 = reg.get_or_create("s2")
    s1.stages_ms["stage_4"] = 42.5
    assert s2.stages_ms["stage_4"] == 0.0

    # Exceed max_sessions=3 -> evicts oldest
    reg.get_or_create("s3")
    reg.get_or_create("s4")
    assert len(reg.sessions) == 3
    assert "s1" not in reg.sessions


def test_interrupted_context_dump_and_llm_prompt_visibility():
    from backend.pipeline.brain import _agent_context

    async def _run():
        b = Brain(persist_history=False)
        await b.start_call("int-01")
        await b.process_turn("int-01", "NH-44 crash, radiator phat gaya, policy 7820")

        # Simulate mic speech interruption while agent is speaking (with empty STT first, raw audio stats)
        entry = b.record_interruption(
            "int-01",
            mic_text="",
            spoken_prefix="Shukar hai aap safe hain!",
            audio_stats={"duration_ms": 480.0, "peak_dbfs": -19.2},
            source="auto_vad_mic",
        )
        assert entry["tag"] == "[interrupted]"
        assert "[interrupted]" in entry["mic_dump"]

        mem = b.get_or_create_call("int-01")
        agent_utts = [u for u in mem.utterances if str(u.get("role", "")).startswith("agent")]
        assert agent_utts[-1].get("interrupted") is True
        assert "[interrupted]" in agent_utts[-1]["text"]

        # Context fed to LLM includes [interrupted] history & last_interruption
        ctx = _agent_context(mem, "galat hai policy 4491 hai", {"injured": False}, [], "reply", "ask_confirm")
        assert ctx["interrupted_context"] is True
        assert any("[interrupted]" in h["text"] for h in ctx["conversation_history"])

        # Caller then corrects policy -> Brain updates from 7820 to 4491 and clears transient flag
        r2 = await b.process_turn("int-01", "nahi galat hai, policy number 4491 hai")
        assert r2["entities"]["policy_number"] == "4491"
        assert r2["entities"]["was_interrupted"] is False
        assert len(r2["entities"]["interruption_log"]) == 1

    asyncio.run(_run())


def test_websocket_streaming_20ms_frames_barge_in_and_clauses():
    orig_persist = BRAIN.persist_history
    BRAIN.persist_history = False
    try:
        with TestClient(app) as client:
            with client.websocket_connect("/ws/call/ws-stream-01") as ws:
                ready = ws.receive_json()
                assert ready["type"] == "ready"
                assert ready["jitter_config"]["frame_ms"] == 20

                # 1. Start call via WS -> streams brain_result + 2 tts_clause messages + turn_complete
                ws.send_json({"type": "start"})
                msgs = []
                while True:
                    m = ws.receive_json()
                    msgs.append(m)
                    if m["type"] == "turn_complete":
                        break
                assert any(m["type"] == "brain_result" for m in msgs)
                clauses = [m["clause"] for m in msgs if m["type"] == "tts_clause"]
                assert len(clauses) == 2

                # 2. Stream 20ms speech frames while agent is SPEAKING -> server InputJitterBuffer + VAD fires barge_in_ack!
                speech_b64 = base64.b64encode(_pcm_frame(4500)).decode("ascii")
                got_barge = None
                for seq in range(15):
                    ws.send_json({"type": "audio_frame", "seq": seq, "ts_ms": seq * 20.0, "pcm_b64": speech_b64})
                for _ in range(10):
                    m = ws.receive_json()
                    if m["type"] == "barge_in_ack":
                        got_barge = m
                        break
                assert got_barge is not None
                assert got_barge["source"] == "server_vad_mic"
                assert got_barge["flush_latency_ms"] < 50.0
                assert got_barge["interrupted_context"]["mic_dump"].startswith("[interrupted]")

                # 3. Stream turn override via WS -> stt_result -> brain_result -> tts_clause(s) -> turn_complete
                ws.send_json({
                    "type": "end_turn",
                    "transcript_override": "NH-44 highway crash, radiator phat gaya hai, policy 7820",
                })
                turn_msgs = []
                while True:
                    m = ws.receive_json()
                    if m["type"] == "vad_state":
                        continue
                    turn_msgs.append(m)
                    if m["type"] == "turn_complete":
                        break
                types = [m["type"] for m in turn_msgs]
                assert "stt_result" in types
                assert "brain_result" in types
                assert "tts_clause" in types
                br = next(m for m in turn_msgs if m["type"] == "brain_result")
                assert br["entities"]["policy_number"] == "7820"
                assert br["stage"] == "confirm"

                ws.send_json({"type": "end_call"})
                end_msg = ws.receive_json()
                assert end_msg["type"] == "call_ended"
    finally:
        BRAIN.persist_history = orig_persist


# ===========================================================================
# Native Tamil (ta-IN) End-to-End Language Detection, Extraction & Responses
# ===========================================================================

def _assert_pure_native_tamil(text: str) -> None:
    import re
    assert text.strip(), "Reply text must not be empty"
    assert re.search(r"[\u0B80-\u0BFF]", text), f"Expected Tamil script in: {text}"
    assert not re.search(r"[\u0900-\u097F]", text), f"Unexpected Devanagari in Tamil reply: {text}"
    stray_latin = re.findall(r"\b(?!(?:NH|EV|OK|FNOL)\b)[A-Za-z]{3,}\b", text)
    assert not stray_latin, f"Unexpected English/Hinglish words {stray_latin} in Tamil reply: {text}"


def test_native_tamil_rules_numbers_and_injury_triage():
    # Safety & injury negation in native Tamil
    assert triage_injury_and_safety("எங்களுக்கு காயமில்லை, எல்லாரும் நல்லா இருக்கோம்")["injured"] is False
    assert triage_injury_and_safety("யாருக்கும் அடி படல, பாதுகாப்பாக இருக்கோம்")["injured"] is False
    assert triage_injury_and_safety("விபத்து ஆகிடுச்சு, இரத்தம் வருது காயம் ஏற்பட்டது")["injured"] is True

    # Spoken Tamil numbers -> 7820 & 3310
    assert 7820 in parse_spoken_numbers("பாலிசி எண் ஏழாயிரத்து எண்ணூற்று இருபது")
    assert 3310 in parse_spoken_numbers("மூவாயிரத்து முன்னூற்று பத்து")

    # Parts, road types & drivability in native Tamil
    assert extract_parts("ஹைவேயில் ரேடியேட்டர் உடைஞ்சிடுச்சு") == ["radiator"]
    assert extract_parts("பம்பர் இல்ல, ஹெட்லைட் உடைஞ்சிடுச்சு") == ["headlight"]
    assert extract_road_type("தேசிய நெடுஞ்சாலையில் இருக்கோம்") == "NH"
    assert extract_road_type("கிராமத்து சாலையில் இருக்கோம்") == "RURAL"
    assert extract_road_type("சென்னை நகரத்தில் இருக்கோம்") == "CITY"
    assert detect_drivability(["puncture"], "வண்டியை ஓட்ட முடியாது")[0] is False
    assert confirm_detection("ஆமாம் உறுதி செய்யுங்க") == "confirm"
    assert confirm_detection("நில்லுங்க தப்பு, மாத்துங்க") == "correct"


def test_brain_native_tamil_multiturn_workflows_and_llm_safety_guard():
    async def _run():
        b = Brain(persist_history=False)

        # 1. Start default session, then caller speaks native Tamil -> replies MUST be pure Tamil
        await b.start_call("ta-flow-b")
        r1 = await b.process_turn(
            "ta-flow-b",
            "வணக்கம், எங்களுக்கு காயமில்லை, ஆனா நெடுஞ்சாலையில் கார் ரேடியேட்டர் உடைஞ்சிடுச்சு, பாலிசி எண் ஏழாயிரத்து எண்ணூற்று இருபது",
        )
        assert r1["language"] == "ta-IN"
        assert r1["entities"]["injured"] is False
        assert r1["entities"]["policy_number"] == "7820"
        assert r1["entities"]["road_type"] == "NH"
        assert r1["entities"]["parts"] == ["radiator"]
        assert r1["stage"] == "confirm"
        _assert_pure_native_tamil(r1["reply_text"])
        assert all(c["language"] == "ta-IN" for c in r1["clauses"])

        # Follow-up ASCII/digit-neutral confirmation stays in Tamil
        r2 = await b.process_turn("ta-flow-b", "ஆமாம் உறுதி செய்யுங்க")
        assert r2["language"] == "ta-IN"
        assert r2["stage"] == "done"
        _assert_pure_native_tamil(r2["reply_text"])

        # 2. Minor breakdown (Workflow A - NCB Saver) in Tamil
        await b.start_call("ta-flow-a")
        ra = await b.process_turn("ta-flow-a", "சென்னை நகரத்தில் டயர் பஞ்சர் ஆகிடுச்சு, காயம் இல்லை")
        assert ra["language"] == "ta-IN"
        assert ra["stage"] == "done"
        assert ra["entities"]["last_dispatch"]["workflow"] == "A"
        _assert_pure_native_tamil(ra["reply_text"])

        # 3. Emergency 112 in Tamil
        await b.start_call("ta-emg")
        re_ = await b.process_turn("ta-emg", "நெடுஞ்சாலையில் விபத்து, இரத்தம் வருது காயம் ஏற்பட்டது")
        assert re_["language"] == "ta-IN"
        assert re_["stage"] == "emergency_handoff"
        _assert_pure_native_tamil(re_["reply_text"])

        # 4. LLM safety validator MUST reject English or Hindi when session is Tamil
        mem = b.get_or_create_call("ta-flow-b")
        assert b._llm_reply_is_safe("Sorry, please tell me your policy number.", mem, {"injured": False}) is False
        assert b._llm_reply_is_safe("Shukar hai aap safe hain, main tow bhej rahi hoon.", mem, {"injured": False}) is False
        assert b._llm_reply_is_safe(
            "நல்லவேளை, நீங்கள் பாதுகாப்பாக இருக்கிறீர்கள்! உடனடியாக சாலை உதவி பணியாளர் வருகிறார்.",
            mem,
            {"injured": False},
        ) is True

    asyncio.run(_run())


def test_gnani_client_and_api_native_tamil_stt_tts_routing(monkeypatch):
    gnani_client.set_api_key("dummy-tamil-test-key")
    captured: Dict[str, Any] = {}

    class _MockResp:
        def __init__(self, payload: Dict[str, Any]):
            self.status_code = 200
            self.headers = {"content-type": "application/json"}
            self.content = b""
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class _MockClient:
        def __init__(self, *a, **kw):
            pass

        async def post(self, url, headers=None, files=None, data=None, json=None):
            if "/stt/v3" in url:
                captured["stt_data"] = dict(data or {})
                return _MockResp({"transcript": "ரேடியேட்டர் உடைஞ்சிடுச்சு", "language": "ta-IN", "request_id": "ta-req"})
            captured["tts_json"] = dict(json or {})
            wav = gnani_client.fallback_tts_wav("ok")
            return _MockResp({"audio": base64.b64encode(wav).decode("ascii")})

        async def aclose(self):
            pass

    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)

    async def _check():
        # indic-auto without restricting candidates lets prisma-v3.0 detect all 10 Gnani languages
        stt_auto = await gnani_client.transcribe_wav(gnani_client.fallback_tts_wav("ta"), language_code="indic-auto")
        assert captured["stt_data"]["model"] == "prisma-v3.0"
        assert captured["stt_data"]["language_code"] == "indic-auto"
        assert "multi_lang_codes" not in captured["stt_data"]
        assert stt_auto["transcript"] == "ரேடியேட்டர் உடைஞ்சிடுச்சு"
        assert stt_auto["language"] == "ta-IN"

        # When 2-4 candidates are passed, sends Gnani SDK field `multi_lang_codes`
        await gnani_client.transcribe_wav(
            gnani_client.fallback_tts_wav("te"),
            language_code="indic-auto",
            language_candidates="hi-IN,ta-IN,te-IN,en-IN",
        )
        assert captured["stt_data"]["multi_lang_codes"] == "hi-IN,ta-IN,te-IN,en-IN"

        stt_ta = await gnani_client.transcribe_wav(gnani_client.fallback_tts_wav("ta"), language_code="ta-IN")
        assert captured["stt_data"]["model"] == "prisma-v3.0"
        assert captured["stt_data"]["language_code"] == "ta-IN"
        assert stt_ta["language"] == "ta-IN"

        tts_ta = await gnani_client.synthesize_speech("நல்லவேளை, நீங்கள் பாதுகாப்பாக இருக்கிறீர்கள்.", language="ta-IN", use_cache=False)
        assert captured["tts_json"]["language"] == "ta-IN"
        assert captured["tts_json"]["voice"] == "Asmita"
        assert tts_ta["language"] == "ta-IN"

    asyncio.run(_check())

    # End-to-end REST + WebSocket in native Tamil (fallback mode)
    gnani_client.set_api_key("")
    orig_persist = BRAIN.persist_history
    BRAIN.persist_history = False
    try:
        with TestClient(app) as client:
            st = client.post("/api/call/start", json={"call_id": "ta-http-01", "language": "ta-IN"}).json()
            assert st["language"] == "ta-IN"
            _assert_pure_native_tamil(st["greeting"])
            assert all(c["language"] == "ta-IN" for c in st["clauses"])

            t1 = client.post(
                "/api/call/turn",
                json={"call_id": "ta-http-01", "transcript_override": "ஹைவேல ரேடியேட்டர் உடைஞ்சிடுச்சு, காயமில்லை, பாலிசி 3310"},
            ).json()
            assert t1["language"] == "ta-IN"
            assert t1["entities"]["policy_number"] == "3310"
            _assert_pure_native_tamil(t1["reply_text"])
            assert all(c["language"] == "ta-IN" for c in t1["clauses"])

            with client.websocket_connect("/ws/call/ta-ws-01") as ws:
                assert ws.receive_json()["type"] == "ready"
                ws.send_json({"type": "end_turn", "transcript_override": "காயமில்லை, சென்னைல பேட்டரி டெட் ஆகிடுச்சு"})
                msgs = []
                while True:
                    m = ws.receive_json()
                    if m["type"] == "vad_state":
                        continue
                    msgs.append(m)
                    if m["type"] == "turn_complete":
                        break
                br = next(m for m in msgs if m["type"] == "brain_result")
                assert br["language"] == "ta-IN"
                _assert_pure_native_tamil(br["reply_text"])
                tc = [m["clause"] for m in msgs if m["type"] == "tts_clause"]
                assert tc and all(c["language"] == "ta-IN" for c in tc)
                ws.send_json({"type": "end_call"})
                ws.receive_json()
    finally:
        BRAIN.persist_history = orig_persist


# ===========================================================================
# All 10 Gnani Languages (hi/ta/te/kn/ml/bn/mr/gu/pa/en) + indic-auto E2E
# ===========================================================================

_ALL_10_SAMPLES = {
    "hi-IN": {
        "safe_nh": "Haan safe hain, NH-44 highway par radiator phat gaya hai, policy 7820",
        "minor_city": "Delhi mein battery dead ho gayi hai, koi chot nahi",
        "confirm": "Haan confirm kar do",
        "injury": "Accident ho gaya, sar mein chot aur khoon beh raha hai",
    },
    "ta-IN": {
        "safe_nh": "வணக்கம், எங்களுக்கு காயமில்லை, தேசிய நெடுஞ்சாலையில் ரேடியேட்டர் உடைஞ்சிடுச்சு, பாலிசி 7820",
        "minor_city": "சென்னை நகரத்தில் பேட்டரி பழுதாகிடுச்சு, காயமில்லை",
        "confirm": "ஆமாம் உறுதி செய்யுங்க",
        "injury": "விபத்து, இரத்தம் வருது காயம் ஏற்பட்டது",
    },
    "te-IN": {
        "safe_nh": "నమస్కారం, మాకు గాయం లేదు, జాతీయ రహదారిపై రేడియేటర్ పాడైపోయింది, పాలసీ 7820",
        "minor_city": "హైదరాబాద్ నగరంలో బ్యాటరీ పాడైంది, గాయం లేదు",
        "confirm": "అవును నిర్ధారించండి",
        "injury": "యాక్సిడెంట్ అయింది, రక్తం వస్తోంది గాయమైంది",
    },
    "kn-IN": {
        "safe_nh": "ನಮಸ್ಕಾರ, ಯಾರಿಗೂ ಗಾಯ ಇಲ್ಲ, ರಾಷ್ಟ್ರೀಯ ಹೆದ್ದಾರಿಯಲ್ಲಿ ರೇಡಿಯೇಟರ್ ಹಾಳಾಗಿದೆ, ಪಾಲಿಸಿ 7820",
        "minor_city": "ಬೆಂಗಳೂರು ನಗರದಲ್ಲಿ ಬ್ಯಾಟರಿ ಹಾಳಾಗಿದೆ, ಗಾಯ ಇಲ್ಲ",
        "confirm": "ಹೌದು ದೃಢೀಕರಿಸಿ",
        "injury": "ಅಪಘಾತ ಆಗಿದೆ, ರಕ್ತ ಬರುತ್ತಿದೆ ಗಾಯವಾಗಿದೆ",
    },
    "ml-IN": {
        "safe_nh": "നമസ്കാരം, ആർക്കും പരിക്കില്ല, ദേശീയപാതയിൽ റേഡിയേറ്റർ തകരാറായി, പോളിസി 7820",
        "minor_city": "കൊച്ചി നഗരത്തിൽ ബാറ്ററി തീർന്നു, പരിക്കില്ല",
        "confirm": "അതെ സ്ഥിരീകരിക്കൂ",
        "injury": "അപകടം പറ്റി, രക്തം വരുന്നു പരിക്കേറ്റു",
    },
    "bn-IN": {
        "safe_nh": "নমস্কার, কারও চোট লাগেনি, জাতীয় সড়কে রেডিয়েটর খারাপ হয়ে গেছে, পলিসি 7820",
        "minor_city": "কলকাতা শহরে ব্যাটারি শেষ হয়েছে, চোট লাগেনি",
        "confirm": "হ্যাঁ নিশ্চিত করুন",
        "injury": "দুর্ঘটনা হয়েছে, রক্ত পড়ছে আঘাত লেগেছে",
    },
    "mr-IN": {
        "safe_nh": "नमस्कार, कोणालाही जखम नाही, राष्ट्रीय महामार्गावर रेडिएटर फुटला आहे, पॉलिसी 7820",
        "minor_city": "पुणे शहरात बॅटरी खराब झाली आहे, जखम नाही",
        "confirm": "होय निश्चित करा",
        "injury": "अपघात झाला, रक्त येतंय दुखापत झाली",
    },
    "gu-IN": {
        "safe_nh": "નમસ્તે, કોઈને ઈજા નથી, નેશનલ હાઇવે પર રેડિએટર તૂટી ગયું છે, પોલિસી 7820",
        "minor_city": "અમદાવાદ શહેરમાં બેટરી ખરાબ થઈ છે, ઈજા નથી",
        "confirm": "હા પાકું કરો",
        "injury": "અકસ્માત થયો છે, લોહી નીકળે છે ઈજા થઈ",
    },
    "pa-IN": {
        "safe_nh": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ, ਕਿਸੇ ਨੂੰ ਸੱਟ ਨਹੀਂ, ਨੈਸ਼ਨਲ ਹਾਈਵੇ 'ਤੇ ਰੇਡੀਏਟਰ ਟੁੱਟ ਗਿਆ, ਪਾਲਿਸੀ 7820",
        "minor_city": "ਲੁਧਿਆਣਾ ਸ਼ਹਿਰ ਵਿੱਚ ਬੈਟਰੀ ਖਰਾਬ ਹੋ ਗਈ, ਸੱਟ ਨਹੀਂ",
        "confirm": "ਹਾਂਜੀ ਪੱਕਾ ਕਰੋ",
        "injury": "ਹਾਦਸਾ ਹੋ ਗਿਆ, ਖ਼ੂਨ ਵਗ ਰਿਹਾ ਸੱਟ ਲੱਗੀ",
    },
    "en-IN": {
        "safe_nh": "We are safe, nobody is injured, radiator is damaged on the highway, policy 7820",
        "minor_city": "My car battery is dead in the city, no one is injured",
        "confirm": "Yes please confirm",
        "injury": "Severe bleeding and head injury in crash",
    },
}


def _assert_reply_matches_language(lang: str, text: str) -> None:
    import re
    from backend.i18n import SCRIPT_REGEX_BY_LANG

    assert text.strip()
    if lang == "hi-IN":
        assert not re.search(r"[\u0901-\u0963\u0966-\u0D7F]", text)
    elif lang == "en-IN":
        assert not re.search(r"[\u0901-\u0963\u0966-\u0D7F]", text)
        assert not re.search(r"\b(?:aap|hai|hain|nahi|kya|bhej|gaadi|chot)\b", text, re.I)
    elif lang == "mr-IN":
        assert re.search(r"[\u0901-\u0963\u0966-\u097F]", text)
        assert not re.search(r"[\u0980-\u0D7F]", text)
        assert not re.findall(r"\b(?!(?:NH|EV|OK|FNOL|RSA)\b)[A-Za-z]{3,}\b", text)
    else:
        assert SCRIPT_REGEX_BY_LANG[lang].search(text), f"Missing {lang} script in: {text}"
        assert not re.search(r"[\u0901-\u0963\u0966-\u097F]", text)
        assert not re.findall(r"\b(?!(?:NH|EV|OK|FNOL)\b)[A-Za-z]{3,}\b", text)


@pytest.mark.parametrize("lang", sorted(_ALL_10_SAMPLES.keys()))
def test_all_10_gnani_languages_rules_detection_and_brain_workflows(lang: str):
    from backend.translit import detect_script_language

    samples = _ALL_10_SAMPLES[lang]
    if lang != "hi-IN":
        assert detect_script_language(samples["safe_nh"]) == lang
    else:
        assert detect_script_language(samples["safe_nh"]) is None

    assert triage_injury_and_safety(samples["safe_nh"], language=lang)["injured"] is False
    assert triage_injury_and_safety(samples["injury"], language=lang)["injured"] is True
    assert extract_parts(samples["safe_nh"]) == ["radiator"]
    assert extract_road_type(samples["safe_nh"]) == "NH"
    assert extract_road_type(samples["minor_city"]) == "CITY"
    assert confirm_detection(samples["confirm"]) == "confirm"

    async def _run():
        b = Brain(persist_history=False)

        # Workflow B -> Confirm -> Done
        await b.start_call(f"b-{lang}", language=lang)
        r1 = await b.process_turn(f"b-{lang}", samples["safe_nh"])
        assert r1["language"] == lang
        assert r1["entities"]["policy_number"] == "7820"
        assert r1["entities"]["road_type"] == "NH"
        assert r1["entities"]["parts"] == ["radiator"]
        assert r1["stage"] == "confirm"
        _assert_reply_matches_language(lang, r1["reply_text"])
        assert all(c["language"] == lang for c in r1["clauses"])

        r2 = await b.process_turn(f"b-{lang}", samples["confirm"])
        assert r2["language"] == lang
        assert r2["stage"] == "done"
        _assert_reply_matches_language(lang, r2["reply_text"])

        # Workflow A (minor breakdown in city -> NCB protected, skip policy)
        await b.start_call(f"a-{lang}")
        ra = await b.process_turn(f"a-{lang}", samples["minor_city"])
        assert ra["language"] == lang
        assert ra["stage"] == "done"
        assert ra["entities"]["last_dispatch"]["workflow"] == "A"
        _assert_reply_matches_language(lang, ra["reply_text"])

        # Emergency 112
        await b.start_call(f"e-{lang}")
        re_ = await b.process_turn(f"e-{lang}", samples["injury"])
        assert re_["language"] == lang
        assert re_["stage"] == "emergency_handoff"
        _assert_reply_matches_language(lang, re_["reply_text"])

        # LLM safety validator permits matching reply and blocks wrong-script drift
        mem = b.get_or_create_call(f"b-{lang}")
        assert b._llm_reply_is_safe(r1["reply_text"], mem, {"injured": False}) is True
        if lang != "en-IN":
            assert b._llm_reply_is_safe("Sorry, please provide your policy number.", mem, {"injured": False}) is False
        else:
            assert b._llm_reply_is_safe("Shukar hai aap safe hain, radiator toot gaya.", mem, {"injured": False}) is False

    asyncio.run(_run())


def test_all_10_gnani_languages_config_stt_tts_voices_and_frontend_options(monkeypatch):
    from backend.i18n import ALL_GNANI_LANGUAGES, LANG_META

    assert len(ALL_GNANI_LANGUAGES) == 10
    gnani_client.set_api_key("dummy-10lang-key")
    captured: Dict[str, Any] = {}

    class _MockResp:
        def __init__(self, payload: Dict[str, Any]):
            self.status_code = 200
            self.headers = {"content-type": "application/json"}
            self.content = b""
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class _MockClient:
        def __init__(self, *a, **kw):
            pass

        async def post(self, url, headers=None, files=None, data=None, json=None):
            if "/stt/v3" in url:
                captured["stt_data"] = dict(data or {})
                return _MockResp({
                    "transcript": "రేడియేటర్ పాడైపోయింది",
                    "detected_language_code": "te-IN",
                    "request_id": "te-auto-req",
                })
            captured["tts_json"] = dict(json or {})
            wav = gnani_client.fallback_tts_wav("ok")
            return _MockResp({"audio": base64.b64encode(wav).decode("ascii")})

        async def aclose(self):
            pass

    monkeypatch.setattr(httpx, "AsyncClient", _MockClient)

    async def _check():
        # indic-auto honors server detected_language_code across all 10 Gnani languages
        stt = await gnani_client.transcribe_wav(gnani_client.fallback_tts_wav("te"), language_code="indic-auto")
        assert stt["detected_language_code"] == "te-IN"
        assert stt["language"] == "te-IN"

        # Every Gnani language sends its native Timbre v2.5 voice + language tag
        for code, meta in LANG_META.items():
            tts = await gnani_client.synthesize_speech("Test", language=code, use_cache=False)
            assert captured["tts_json"]["language"] == code
            assert captured["tts_json"]["voice"] == meta["tts_voice"]
            assert tts["language"] == code

    asyncio.run(_check())

    # HTTP /api/config + frontend HTML/JS advertise all 10 Gnani languages
    gnani_client.set_api_key("")
    orig_persist = BRAIN.persist_history
    BRAIN.persist_history = False
    try:
        with TestClient(app) as client:
            cfg = client.get("/api/config").json()
            assert set(cfg["supported_languages"]) == set(ALL_GNANI_LANGUAGES)
            idx_html = client.get("/").text
            app_js = client.get("/app.js").text
            for code in ALL_GNANI_LANGUAGES:
                assert f'value="{code}"' in idx_html
                assert f'"{code}"' in app_js
            assert 'value="indic-auto"' in idx_html

            # Explicit English call via HTTP
            st_en = client.post("/api/call/start", json={"call_id": "en-http-01", "language": "en-IN"}).json()
            assert st_en["language"] == "en-IN"
            _assert_reply_matches_language("en-IN", st_en["greeting"])

            # Malayalam auto-detect on default call
            st_ml = client.post("/api/call/start", json={"call_id": "ml-http-01"}).json()
            assert st_ml["language"] == "hi-IN"
            t_ml = client.post(
                "/api/call/turn",
                json={"call_id": "ml-http-01", "transcript_override": _ALL_10_SAMPLES["ml-IN"]["safe_nh"]},
            ).json()
            assert t_ml["language"] == "ml-IN"
            assert t_ml["entities"]["policy_number"] == "7820"
            _assert_reply_matches_language("ml-IN", t_ml["reply_text"])
    finally:
        BRAIN.persist_history = orig_persist


def test_ask_part_pol_note_no_placeholder_leak_all_10_languages():
    from backend.i18n import ALL_GNANI_LANGUAGES

    async def _run():
        b = Brain(persist_history=False)
        for lang in ALL_GNANI_LANGUAGES:
            cid = f"polnote-{lang}"
            await b.start_call(cid, language=lang)
            await b.process_turn(cid, "7820", language=lang)
            r = await b.process_turn(cid, "gaadi kharab ho gayi", language=lang)
            assert "{pol_note}" not in r["reply_text"]
            assert "{pol}" not in r["reply_text"]
            assert "7820" in r["reply_text"]

            cid2 = f"nopol-{lang}"
            await b.start_call(cid2, language=lang)
            r2 = await b.process_turn(cid2, "gaadi kharab ho gayi", language=lang)
            assert "{pol_note}" not in r2["reply_text"]

    asyncio.run(_run())


def test_negation_drivability_marathi_lock_and_ws_error_resilience(monkeypatch):
    from backend.rsa_and_claim_rules import extract_road_type

    # 1. Mixed Devanagari/Latin 4-word road negation ("city के highway पे कहीं भी नहीं हूँ")
    assert extract_road_type("city के highway पे कहीं भी नहीं हूँ") == "CITY"

    async def _run():
        b = Brain(persist_history=False)

        # 2. Negated problem word ("हम कोई दिक्कत की बात को नहीं") stays in safe/incident flow
        await b.start_call("neg-prob-1")
        r_safe = await b.process_turn("neg-prob-1", "अरे भाई, कति safe! हम कोई दिक्कत की बात को नहीं।")
        assert "{pol_note}" not in r_safe["reply_text"]
        assert r_safe["ticket_status"] == "STAGE 2 — INCIDENT DETAILS IN PROGRESS"

        # 3. Cross-turn non-drivable flag survives follow-up road turn with parts=[]
        await b.start_call("nd-cross-1")
        r1 = await b.process_turn("nd-cross-1", "गाड़ी चलने लायक नहीं है")
        assert r1["entities"]["is_drivable"] is False
        r2 = await b.process_turn("nd-cross-1", "NH-44 par hoon, policy 7820 hai")
        assert r2["entities"]["is_drivable"] is False

        # 4. Marathi session stays mr-IN on short Devanagari turns like policy number
        await b.start_call("mr-lock-1", language="mr-IN")
        rm = await b.process_turn("mr-lock-1", "पॉलिसी नंबर 7820")
        assert rm["language"] == "mr-IN"

    asyncio.run(_run())

    # 5. WebSocket _stream_turn exception emits error + turn_complete instead of hanging client
    orig_persist = BRAIN.persist_history
    BRAIN.persist_history = False

    async def _boom(*a, **kw):
        raise RuntimeError("simulated brain failure")

    try:
        with TestClient(app) as client:
            with client.websocket_connect("/ws/call/ws-err-resilience") as ws:
                assert ws.receive_json()["type"] == "ready"
                monkeypatch.setattr(BRAIN, "process_turn", _boom)
                ws.send_json({"type": "end_turn", "transcript_override": "hello"})
                seen = []
                for _ in range(10):
                    ev = ws.receive_json()
                    seen.append(ev["type"])
                    if ev["type"] == "turn_complete":
                        break
                assert "error" in seen
                assert "turn_complete" in seen
                ws.send_json({"type": "ping"})
                assert ws.receive_json()["type"] == "pong"
    finally:
        BRAIN.persist_history = orig_persist




