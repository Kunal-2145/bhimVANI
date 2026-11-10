# BimaVani (बीमा-वाणी) 🛣️
### 24×7 Hinglish Voice-First Roadside Assistance + IRDAI Claim Desk — Helpline `1-800-BIMA-VANI`

A production-grade hackathon project proving **one thesis end-to-end**:

> A voice agent that survives **loud Indian highway noise**, **student-tier API rate
> limits**, and **mid-sentence barge-in corrections** — while safely dispatching
> real-world RSA workflows and auto-drafting IRDAI-compliant claim numbers.

Built on the **Gnani.ai Vachana/Artha ecosystem**: **Prisma v2.5/v3.0** STT +
**Timbre v2.5/v3.0** TTS (voice *Nalini*), with a **100% deterministic Hinglish
tool-brain that needs ZERO LLM and ZERO hallucinations** — it works with *only*
`GNANI_API_KEY`.

---

## Hybrid Streaming-Turn Voice Architecture (8 Stages)

```
+--------------------------------------------------------------------------------------+
|     TURN-TAKING STATE MACHINE & BARGE-IN CONTROLLER (Stop & Flush All <50ms)          |
+---+------------------+------------------+------------------+------------------+------+
    |                  |                  |                  |                  |
    v                  v                  v                  v                  v
[1. Microphone] --> [2. Gnani Prisma] --> [3. Endpointing] --> [4. Brain: Tools + Memory]
 16kHz Mono PCM      POST /stt/v3         Push-to-Talk          (Injury/RSA/Claim
 WAV + Noise Mix     Hinglish ASR         (Auto-VAD 650ms        Tools + Memory)
                                          600ms silence)          |
    ^                                                             |
    | (Next Turn / Barge-In)                                      v
[8. Speaker]   <-- [7. Jitter Buffer] <-- [6. Gnani Timbre] <-- [5. 2-Clause Buffer]
 Agent Voice      120ms prebuffer +        24kHz PCM WAV           Ack Clause 1 first
                  gapless Web Audio        Nalini + MD5 cache      + Details Clause 2
                  Queue + Instant Stop     (+44-byte RIFF wrap)
```

| Stage | Component | File | Tech |
|---|---|---|---|
| 1 | 16kHz Mono 16-bit PCM WAV Recorder + **Indian Highway Noise Mixer** | `frontend/app.js` | Web Audio API, AudioWorklet |
| 2 | **Gnani Prisma** STT (`prisma-v2.5`/`v3.0`, codemix Hinglish) | `backend/gnani_client.py` | `POST https://api.vachana.ai/stt/v3` |
| 3 | Auto-VAD live (raw-mic analyser, 650ms silence endpoint) / Push-to-Talk hold optional | `backend/pipeline/endpointing.py` | 20ms frame energy gate |
| 4 | Deterministic Brain + tools + short/long memory | `backend/pipeline/brain.py` | pure Python, optional LLM adapter |
| 5 | 2-Clause Smart Buffer (≤2 TTS calls/reply) | `backend/pipeline/clause_buffer.py` | hook-first splitter |
| 6 | **Gnani Timbre** TTS (voice Nalini, `timbre-v2.5` — **never v2.0**, retired) | `backend/gnani_client.py` | `POST /api/v1/tts/inference`, 24kHz PCM → 44-byte RIFF WAV + MD5 cache |
| 7 | Browser Jitter Buffer (120ms prebuffer + gapless scheduling) | `frontend/app.js` | Web Audio `BufferSource` queue |
| 8 | Barge-In Controller (<20ms browser stop / <50ms full flush) | `backend/pipeline/barge_in.py` + `app.js` | asyncio task cancel + queue flush |

### The 6 Mandatory Engineering Guardrails (all implemented & tested)

| # | Guardrail | Where | Proof |
|---|---|---|---|
| G1 | Auto-VAD default (VAD on RAW mic, noise-mixer only affects the sent WAV; 650ms silence endpoint) + Push-to-Talk hold fallback | `endpointing.py`, `app.js` | `test_vad_ignores_horn_then_endpoint_silence` |
| G2 | `asyncio.Semaphore(1)` around ALL live Gnani calls + 429/5xx exponential-backoff retry | `gnani_client.py` | `test_semaphore_is_one`, `test_rate_limit_retry_guard` |
| G3 | Bounded `asyncio.Queue(maxsize=32)` with depth/backpressure telemetry | `queues.py` (+ live on the UI 8-box bar) | `test_bounded_queue_backpressure_count` (20 pushes → depth 8, 12 drops) |
| G4 | 2-Clause Smart Chunk Buffer (≤2 TTS calls; Clause-1 empathy hook first) | `clause_buffer.py` | `test_clause_buffer_max_two_and_hook_first` |
| G5 | Browser jitter buffer: 120ms prebuffer + gapless Clause-2 scheduling | `app.js` | `playClausesGapped()` back-to-back `src.start(t)` |
| G6 | Sub-50ms barge-in: browser audio kill <20ms → backend task cancel + queue flush → cards update | `barge_in.py`, `app.js`, `main.py` | `test_barge_in_flush_under_50ms` |

---

## Gnani.ai Ecosystem Contracts

| Feature | Contract |
|---|---|
| Keys | from **app.gnani.ai/voice** (5,000 programme credits) → env `GNANI_API_KEY`, header `X-API-Key-ID` |
| STT | `POST {GNANI_BASE_URL:-https://api.vachana.ai}/stt/v3` · multipart `audio_file=("call_turn.wav", …, "audio/wav")` + `model, language_code, format, preferred_language, keyterms` |
| TTS | `POST /api/v1/tts/inference` · JSON `{text, voice:"Nalini", model:"timbre-v2.5", language:"hi-IN", speed:1.0, audio_config:{sample_rate:24000, encoding:"linear_pcm", container:"wav"}}` |
| SDK | optional `gnani-vachana` package (`gnani.stt.GnaniSTTClient`, `gnani.tts.GnaniTTSClient`) — REST is primary |
| Cache | `hashlib.md5(text|voice|model|lang).wav` under `backend/cache/tts/` → demo re-runs cost **0 credits** |
| No key? | **Credit-Shield Fallback Mode** — deterministic transcript path + locally-synthesized spoken-tone WAV. The demo never crashes. |

**Models:** STT `prisma-v2.5` (default) / `prisma-v3.0` · TTS `timbre-v2.5` (default) / `timbre-v3.0`.
**Never** `timbre-v2.0` (retired). Languages: `hi-IN` (default) `indic-auto en-IN mr-IN ta-IN te-IN`.

---

## Domain Tools (synthetic, DPDP-compliant — 100% fake data)

1. **triage_injury_and_safety** — injury keywords → halts claim intake, raises 🚨 112 + 🛣️ 1033 NHAI Patrol + live human officer handoff.
2. **dispatch_smart_rsa(road_type, parts, is_drivable, policy)** — 3 real workflows:
   - **A · Minor breakdown** (puncture/battery/keys/fuel): on-spot bike mechanic, ETA 22 min, **protects the 20% No-Claim Bonus** (agent literally says: file no claim!).
   - **B · Non-drivable** (radiator/coolant/axle/smoke/EV stuck): 🔴 *do NOT start engine*, 1033 cones on NH, **free flatbed tow ≤50 km** to the nearest cashless garage; remote rural ETA>2h → **IRDAI ₹1,500 local towing reimbursement** authorization.
   - **C · Drivable minor** (bumper/headlight/mirror): 4 photos with plate → self-drive to cashless garage.
3. **calculate_irdai_claim_draft(policy, parts)** — compulsory deductible ₹100 (2W) / ₹1,000 (4W), depreciation, estimated ₹ range labeled *"Estimated Range Subject to Surveyor Inspection — Not Final Approval"*.

Multi-turn loop: **Turn 1** incident → RSA ticket + FNOL card + spoken read-back → **Turn 2**

**Policies (all synthetic):** `7820` Maruti Swift Zero-Dep · `4491` Honda Activa Zero-Dep · `1055` Hero Splendor+ Zero-Dep · `9932` Mahindra Bolero Standard · `3310` Tata Nexon EV Zero-Dep.

---

## Run It

```bash
# macOS / Linux (also applies --test for pytest, --verify for live Gnani smoke)
./run.sh
# Windows
run.bat

# 1-command live Gnani verification (after putting GNANI_API_KEY in .env)
python verify_gnani_api.py
```

Open http://127.0.0.1:8000 — or set the key live from the UI (**🔑 Set Gnani API Key**, hot-reloads without restart) and press **🧪 Test Live Gnani API**.

## Layout
```
backend/main.py · gnani_client.py · rsa_and_claim_rules.py · eval_runner.py
backend/pipeline/{queues,endpointing,clause_buffer,barge_in,brain}.py
backend/data/synthetic_db.json (+call_history.json) · backend/cache/tts/
frontend/{index.html,style.css,app.js} · tests/test_bimavani.py
plan.txt (single source of truth) · EVALUATION.md · HOW_TO_USE_AND_SUBMIT.md
```

---

*BimaVani — voice jo bima hai. Built for the voice-AI hackathon · deadline Nov 10.*
