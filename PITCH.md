# BimaVani (बीमा-वाणी) — 1-Page Project Pitch
**10-Language Voice-First Roadside Assistance & IRDAI Motor Claim Desk · `1-800-BIMA-VANI`**

---

## Problem
Over **1.2 lakh fatal road crashes** and millions of highway breakdowns occur across India every year. Stranded drivers call helplines in Hindi, Hinglish, or regional languages over noisy 8 kHz connections beside idling trucks and horns. Conventional voice bots break down on three fronts:
1. Fixed VAD gates cut off drivers or lock open under continuous truck noise.
2. Unbounded TTS responses waste student-tier API credits and ignore mid-sentence corrections (*"Ruko, policy 4491 hai!"*).
3. Naive claim bots blindly file insurance claims even on a minor puncture — destroying the policyholder's **20% No-Claim Bonus (NCB)**.

---

## Solution & Domain Workflows
**BimaVani** runs an end-to-end 3-stage voice triage & IRDAI FNOL desk across **all 10 Gnani languages** (`hi-IN`, `en-IN`, `mr-IN`, `ta-IN`, `te-IN`, `kn-IN`, `ml-IN`, `bn-IN`, `gu-IN`, `pa-IN`) plus `indic-auto`:
1. **Stage 1 — Injury & Safety First:** Detects trauma/bleeding across all 10 scripts with negation-aware phrasing, halts claim intake, and raises **112 Ambulance + Police** (+ **1033 NHAI Patrol** on highways) with human handoff.
2. **Stage 2 — Smart RSA Triage (A / B / C):**
   - **Workflow A (NCB Saver):** Puncture, battery, keys, or fuel → dispatches on-spot bike mechanic (~22 min ETA) and **explicitly protects the 20% NCB** by filing **no claim**.
   - **Workflow B (Non-Drivable):** Coolant leak, radiator, axle, smoke, EV fault → warns *"do not start engine"*, requests **free flatbed tow ≤50 km**, and auto-authorizes **IRDAI ₹1,500 rural local towing reimbursement** when remote ETA exceeds 2 hours.
   - **Workflow C (Drivable Damage):** Guides 4 damage photos with number plate and self-drive to nearest cashless garage.
3. **Stage 3 — IRDAI FNOL Draft & Voice Confirmation:** Computes statutory deductibles (₹100 2W / ₹1,000 4W), age-scaled depreciation (0% on Zero-Dep), and outputs *"Estimated Range Subject to Surveyor Inspection — Not Final Approval"* before voice-confirmed `FNOL-<HEX>` submission.

---

## Production Voice-Telephony Engineering (Gnani Vachana Stack)
- **Gnani Prisma (`prisma-v3.0`, `codemix` / `indic-auto`) + Timbre (`timbre-v3.0`, 10 per-language voices):** Pooled HTTP client with `asyncio.Semaphore(1)` rate-limit protection, `429/5xx` backoff retries, 44-byte RIFF framing, 16 kHz linear resampling, MD5 disk TTS caching (0 credits on repeat clauses), and deterministic **Credit-Shield fallback**.
- **20 ms WebSocket Streaming + DSP + PLC (`backend/pipeline/`):** Browser `AudioWorklet` mixes selectable NH-44 highway/8 kHz GSM noise into 20 ms 16 kHz PCM frames; server applies an **80 Hz RC high-pass diesel-rumble filter**, **minimum-statistics adaptive noise floor** (`AutoVADDetector` with 600 ms hysteresis & SNR telemetry), min-heap packet reordering, **Packet Loss Concealment (PLC)** across 1–3 dropped packets, and a 300 ms pre-roll ring.
- **True Async Producer/Consumer Clause Streaming + `<50 ms` Barge-In:** Replies split at ≤2 clauses (`ClauseBuffer`); async producer & consumer tasks stream Clause 1 through bounded `asyncio.Queue(maxsize=32)` hops and a **≥120 ms `OutputJitterBuffer` prebuffer gate** while Clause 2 synthesizes. Interruptions kill browser audio in `<20 ms`, flush backend queues & tasks in `<50 ms`, and inject `[interrupted]` context into call memory.
- **DPDP Compliance & Rolling SLOs:** Deterministic **Aadhaar / PAN / mobile / email redactor** scrubs persisted transcripts (`call_history.json`) and LLM context while preserving policy/claim IDs; `LatencyTracker` publishes live **p50 / p95** latency SLOs on `/api/health` and the UI.

---

## Verified Results
- **46 / 46** automated `pytest` tests passing (pipeline guardrails, DSP attenuation, PLC recovery, DPDP PII redaction, WebSocket streaming, all 10 Gnani languages).
- **20 / 20 (100%)** core acoustic & barge-in benchmark (`CLEAN`, `HIGHWAY`, `GSM`, `BARGE-IN`, `HONEST-FAIL`) + **30 / 30 (100%)** across the **10-language multilingual workflow matrix** (deterministic engine: **~1.5 ms/turn**).
