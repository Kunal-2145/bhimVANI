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

