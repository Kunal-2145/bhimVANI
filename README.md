# BimaVani (बीमा-वाणी) 🛣️
### 24×7 Multilingual Voice-First Roadside Assistance + IRDAI Claim Desk — Helpline `1-800-BIMA-VANI`

[![BimaVani CI](https://github.com/Kunal-2145/bhimVANI/actions/workflows/ci.yml/badge.svg)](https://github.com/Kunal-2145/bhimVANI/actions/workflows/ci.yml) · 📄 **[1-Page Project Pitch (`PITCH.md`)](PITCH.md)** · 📊 **[Benchmarks (`EVALUATION.md`)](EVALUATION.md)**

A production-grade voice-AI system proving **one thesis end-to-end**:

> A voice agent that survives **loud Indian highway noise**, **packet jitter/loss**, **student-tier API rate limits**, and **mid-sentence barge-in corrections** across **all 10 Gnani Indian languages** — while safely dispatching real-world RSA workflows and auto-drafting IRDAI-compliant FNOL claims.

Built on the **Gnani.ai Vachana/Artha ecosystem**: **Prisma v3.0/v2.5** STT +
**Timbre v3.0/v2.5** TTS (per-language voices such as *Nalini*, *Asmita*, *Ananya*, *Asha*, *Kavya*, *Maya*, *Rani*, *Rekha*), with a **deterministic 10-language tool-brain + DPDP PII redaction + optional OpenAI-compatible LLM adapter (`gnani/gnani-evon-v3.3-30B-A3B`)** — runs completely with *only* `GNANI_API_KEY`.

---

## Streaming 8-Stage Voice Telephony Architecture

```
+---------------------------------------------------------------------------------------------------+
|        TURN-TAKING STATE MACHINE & BARGE-IN CONTROLLER (Browser <20ms stop · Backend <50ms flush) |
+---+-----------------------+--------------------+---------------------+--------------------+-------+
    |                       |                    |                     |                    |
    v                       v                    v                     v                    v
[1. Microphone] --------> [2. Gnani Prisma] -> [3. Endpointing] ----> [4. Brain + DPDP PII Redactor]
 20ms PCM frames          POST /stt/v3          80Hz High-Pass DSP +   10-Lang Injury/RSA/IRDAI
 + AudioWorklet Noise     10-Lang + indic-auto  Adaptive Noise Floor   Tools + [interrupted] Context
 + InputJitterBuffer                            Auto-VAD 600ms (def.)  + Rolling p50/p95 SLOs
 (Min-Heap + PLC)                               or Push-to-Talk             |
    ^                                                                       |
    | (Next Turn / Server-VAD Barge-In)                                     v
[8. Speaker]   <-------- [7. Output Jitter] <-- [6. Gnani Timbre] <--- [5. 2-Clause Buffer]
 Web Audio Queue          >=120ms Prebuffer      Async Producer/Cons.   Empathy Hook Clause 1 first
 + Instant Stop           Gate + Gapless Queue   + MD5 Disk Cache       + Action Details Clause 2
```

| Stage | Component | File | Engineering Details |
|---|---|---|---|
| 1 | 20 ms 16 kHz PCM Streamer + Highway Noise Mixer + `InputJitterBuffer` | `frontend/app.js`, `backend/pipeline/jitter_buffer.py` | Web Audio `AudioWorklet`, min-heap packet reordering, **adaptive jitter target (40–120 ms)**, **Packet Loss Concealment (PLC)**, 300 ms pre-roll |
| 2 | **Gnani Prisma** STT (`prisma-v3.0`/`v2.5`, `codemix` & `indic-auto`) | `backend/gnani_client.py` | `POST /stt/v3`, `asyncio.Semaphore(1)`, `429/5xx` backoff retries, **80 Hz RC high-pass diesel-rumble DSP** before STT |
| 3 | **Auto-VAD** (default, 600 ms hysteresis) / Push-to-Talk (optional) | `backend/pipeline/endpointing.py` | Minimum-statistics **adaptive noise-floor estimator**, **SNR telemetry**, <220 ms horn-spike rejection |
| 4 | Deterministic Brain + DPDP PII Redactor + Rolling SLOs | `backend/pipeline/brain.py`, `safety.py`, `queues.py` | Pure-Python 10-language rules + optional LLM (`gnani-evon-v3.3-30B-A3B`), Aadhaar/PAN/mobile masking before storage, `LatencyTracker` **p50/p95** |
| 5 | 2-Clause Smart Buffer (≤2 TTS calls/turn) | `backend/pipeline/clause_buffer.py` | Protects acronyms (`I.R.D.A.I.`, `N.H.44`), puts safety/empathy hook first |
| 6 | **Gnani Timbre** TTS (`timbre-v3.0`/`v2.5` — never retired `v2.0`) | `backend/gnani_client.py`, `backend/main.py` | **True async producer/consumer streaming** across bounded `tts` queue, 24 kHz WAV framing, MD5 disk cache |
| 7 | `OutputJitterBuffer` + Browser Web Audio Queue | `backend/pipeline/jitter_buffer.py`, `frontend/app.js` | **≥120 ms strict prebuffer gate** (`drain_ready`) + back-to-back gapless WebAudio playout |
| 8 | Barge-In Controller (<20 ms client / <50 ms server flush) | `backend/pipeline/barge_in.py`, `main.py` | Cancels active producer/consumer tasks, flushes 7 queues + jitter buffers, injects `[interrupted]` context |

### Verified Engineering Guardrails (`tests/test_bimavani.py` — 46/46 passing)

| # | Guardrail & Engineering Upgrade | Where | Proof Test |
|---|---|---|---|
| G1 | **Auto-VAD default** (600 ms hysteresis + 80 Hz HPF + adaptive noise floor) + Push-to-Talk toggle | `endpointing.py`, `app.js` | `test_vad_ignores_short_horn_burst_and_endpoints_after_silence`, `test_80hz_highpass_dsp_and_adaptive_noise_floor_vad` |
| G2 | `asyncio.Semaphore(1)` per event loop around all live Gnani calls + `429/5xx` retry & `401` fail-fast | `gnani_client.py` | `test_semaphore_is_one_and_reentrant_across_loops`, `test_rate_limit_retry_guard_and_fail_fast_401` |
| G3 | Bounded `asyncio.Queue(maxsize=32)` hops + `SessionRegistry` isolation + rolling `p50/p95` `LatencyTracker` | `queues.py` | `test_bounded_queue_backpressure_drop_oldest_vs_newest`, `test_rolling_slo_telemetry_and_multilingual_eval_matrix` |
| G4 | 2-Clause Smart Buffer (≤2 clauses/turn, Clause-1 empathy hook first) | `clause_buffer.py` | `test_clause_buffer_max_two_and_hook_first`, `test_clause_buffer_preserves_irdai_acronym_and_no_empty_clause` |
| G5 | Spec-compliant RIFF WAV headers (`len(pcm)//2`), **PLC**, adaptive jitter, and **≥120 ms `OutputJitterBuffer` gate** | `jitter_buffer.py`, `app.js` | `test_input_and_output_jitter_buffers_and_vad_reordering`, `test_wav_header_riff_sample_count_fix_plc_and_output_prebuffer` |
| G6 | Sub-50 ms backend barge-in flush (`<20 ms` browser kill) + `[interrupted]` context dump | `barge_in.py`, `brain.py`, `main.py` | `test_state_machine_transitions_and_barge_in_flush_under_50ms`, `test_interrupted_context_dump_and_llm_prompt_visibility` |
| G7 | **DPDP PII Redactor** (masks Aadhaar/PAN/mobile/email before persistence & LLM serialization; keeps 4-digit policies) | `safety.py`, `brain.py` | `test_dpdp_pii_redaction_and_safe_history_persistence` |

---

## Gnani.ai Ecosystem Contracts

| Feature | Contract |
|---|---|
| Keys | `app.gnani.ai/voice` → env `GNANI_API_KEY`, header `X-API-Key-ID` (or hot-loaded in UI **🔑 Gnani API** modal) |
| STT | `POST {GNANI_BASE_URL:-https://api.vachana.ai}/stt/v3` · `prisma-v2.5` (`codemix`) & `prisma-v3.0` (`indic-auto` / regional) |
| TTS | `POST /api/v1/tts/inference` · `timbre-v2.5` (per-language native voices) / `timbre-v3.0` (*Lakshmi*), 24 kHz linear PCM WAV |
| Cache | `hashlib.md5("tts-v2|mode|text|voice|model|lang").wav` under `backend/cache/tts/` → demo re-runs cost **0 credits** |
| Fallback | **Credit-Shield Fallback Mode** when `GNANI_API_KEY` is unset — full pipeline + deterministic spoken-tone WAV |

**Supported Languages (all 10 Gnani languages + auto-detect):** `indic-auto`, `hi-IN`, `en-IN`, `mr-IN`, `ta-IN`, `te-IN`, `kn-IN`, `ml-IN`, `bn-IN`, `gu-IN`, `pa-IN`.

---

## Domain Workflows & Synthetic Data (100% DPDP-Safe)

1. **`triage_injury_and_safety`** — injury keywords across all 10 scripts → halts claim intake, raises 🚨 **112 Ambulance + Police** + 🛣️ **1033 NHAI Patrol** (on highways) + human emergency officer handoff.
2. **`dispatch_smart_rsa`** — 3 deterministic workflows:
   - **A · NCB Saver (minor breakdown):** puncture/battery/keys/fuel → on-spot bike mechanic (~22 min ETA), **explicitly protects 20% No-Claim Bonus (no claim filed)**.
   - **B · Non-Drivable Crash/Fault:** radiator/coolant/axle/smoke/EV → *"do NOT start engine"*, **free flatbed tow ≤50 km**, plus **IRDAI ₹1,500 rural local towing reimbursement** when remote ETA > 2 h.
   - **C · Drivable Body Damage:** bumper/headlight/mirror → 4 number-plate damage photos + self-drive to cashless garage.
3. **`calculate_irdai_claim_draft`** — compulsory deductible ₹100 (2W) / ₹1,000 (4W), age-scaled depreciation (0% on Zero-Dep), labeled *"Estimated Range Subject to Surveyor Inspection — Not Final Approval"*.

**Seeded Synthetic Policies (`backend/data/synthetic_db.json`):** `7820` Maruti Swift Zero-Dep · `4491` Honda Activa Zero-Dep · `1055` Hero Splendor+ Zero-Dep · `9932` Mahindra Bolero Standard · `3310` Tata Nexon EV Zero-Dep (plus dynamic lookup for any spoken 4-digit policy number).

---

## Run & Test

```bash
# Configure optional live Gnani API key (from https://app.gnani.ai/voice)
cp .env.example .env

# Run web UI + FastAPI + WebSocket server (http://127.0.0.1:8000)
./run.sh            # Windows: run.bat

# Run full automated test suite (46 tests)
.venv/bin/pytest -v

# Run 20-case acoustic/barge-in + 30-case 10-language + engineering layer benchmark
.venv/bin/python backend/eval_runner.py

# Verify live Gnani Timbre ⇄ Prisma round-trip (requires GNANI_API_KEY in .env or UI modal)
.venv/bin/python verify_gnani_api.py
```

## Repository Layout
```
PITCH.md                        # 1-page project pitch
EVALUATION.md                   # Benchmark report & acoustic/multilingual results
.github/workflows/ci.yml        # Automated CI running pytest (46/46) & eval_runner
backend/main.py                 # FastAPI REST + WebSocket streaming orchestrator
backend/gnani_client.py         # Gnani Prisma v2.5/v3.0 & Timbre v2.5/v3.0 client (Semaphore(1), cache, resampler)
backend/rsa_and_claim_rules.py  # Triage, RSA workflows, IRDAI FNOL calculator
backend/i18n.py                 # Native-script templates & metadata for all 10 Gnani languages
backend/eval_runner.py          # 20-case acoustic + 30-case 10-language + engineering layer evaluator
backend/pipeline/{queues,endpointing,jitter_buffer,clause_buffer,barge_in,safety,brain}.py
frontend/{index.html,style.css,app.js}
tests/test_bimavani.py          # 46 automated unit, WebSocket, DSP/PLC/PII, and 10-language tests
```

---

*BimaVani — voice jo bima hai. Built for Gnani's Great Indian AI Internship Challenge 2026.*
