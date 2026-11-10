# How to Use, Demo & Submit — BimaVani
*Add your Gnani key, verify live, record your 2–3 minute demo video, and submit. Extended Deadline: November 15, 2026.*

---

## 1. Get & Add Your Gnani API Key (2 min)

1. Go to **https://app.gnani.ai/voice** (docs: https://docs.gnani.ai) → copy your programme key (5,000 credits included).
2. From the project folder:
   ```bash
   cp .env.example .env
   # edit .env and set:
   #   GNANI_API_KEY=<your-key>
   ```

   **OR (no restart needed):** start the app first, click **🔑 Gnani API** in the navbar, paste, and Save → the backend hot-reloads it immediately (`🟢 LIVE GNANI`).

```env
GNANI_API_KEY=...                 # required for live Prisma/Timbre
GNANI_STT_MODEL=prisma-v3.0       # or prisma-v2.5
GNANI_TTS_MODEL=timbre-v3.0       # or timbre-v2.5 — NEVER timbre-v2.0 (retired)
```

No key? The app runs cleanly in **Credit-Shield Fallback Mode** — all workflows, 8-stage telemetry, DSP, jitter buffers, and local WAV synthesis work with 0 credits burned.

---

## 2. Verify Live API & Tests

```bash
./run.sh --verify          # 1-command live Timbre ⇄ Prisma round-trip check
.venv/bin/pytest -v        # 46/46 unit, WebSocket, DSP, PLC, PII & 10-language tests
.venv/bin/python backend/eval_runner.py   # 20/20 acoustic + 30/30 10-lang + DSP/PLC/PII checks
```

In-app: click **🔑 Gnani API** → **🧪 Test Live Timbre ⇄ Prisma Round-Trip**.

---

## 3. Run the Live Demo

```bash
./run.sh                   # serves on http://127.0.0.1:8000 (Windows: run.bat)
```

1. Tap **📞 Call** on the driver handset (WebSocket `/ws/call/{call_id}` opens, timer starts).
2. Select a **🔊 Road noise** preset (`NH-44 highway -10 dB` or `8kHz GSM`) with *into mic* ON — shows real-time 20 ms WebSocket streaming, 80 Hz high-pass diesel-rumble filtering, and adaptive noise-floor VAD.
3. Try quick scenarios or speak live in **any of the 10 Gnani languages** (`hi-IN`, `en-IN`, `mr-IN`, `ta-IN`, `te-IN`, `kn-IN`, `ml-IN`, `bn-IN`, `gu-IN`, `pa-IN` or `indic-auto`):
   - **Workflow B + FNOL (`NH-44 crash`):** *"Haan safe hain, NH-44 par radiator phat gaya, policy 7820"* → dispatches flatbed tow + 1033 cones + drafts `FNOL-<HEX>` (`₹12,300–₹16,000`), then voice-confirm *"Haan confirm kar do"*.
   - **Workflow A (NCB Saver):** *"Delhi mein battery dead ho gayi, koi chot nahi"* → dispatches on-spot mechanic and **protects 20% No-Claim Bonus (no claim filed)**.
   - **Workflow B Rural Reimbursement:** Mention a rural/village road + policy `9932` → authorizes **₹1,500 IRDAI local towing reimbursement**.
   - **Barge-In Correction:** While the agent reads back, speak over or press **⚡ Interrupt** → *"Ruko, policy 4491 hai"* → `<20 ms` browser audio cut, `<50 ms` backend flush, `[interrupted]` context logged, cards update immediately.
   - **Emergency 112:** Mention injury/bleeding → immediately halts claim intake and escalates **112 Ambulance + Police (+ 1033 NHAI)**.
4. Open **📊 Benchmark → ▶ Run 20-Case Suite** to show the 20/20 + 30/30 10-language matrix live.

---

## 4. Record the 2–3 Minute Demo Video (Submission Form Field 6)

Recommended structure (**2:15–2:45 total**, 1080p screen recording with audio):

```text
0:00–0:20  Hook & Problem: Show BimaVani dark telephony console; explain stranded driver
           on NH-44 highway with diesel rumble, horns, 8 kHz phone audio & NCB pain point.
0:20–0:55  Live Call + Highway Noise + Barge-In: Turn on NH-44 noise, tap Call, run
           NH-44 crash (#7820), show 8-stage pipeline lighting up + FNOL draft. Mid-readback,
           interrupt with "Ruko policy 4491 hai" -> highlight <50ms flush + card update.
0:55–1:25  NCB Saver + Emergency 112: Show minor battery/puncture protecting 20% NCB
           (no claim filed), and head injury escalating 112 + 1033 NHAI immediately.
1:25–1:55  Multilingual Demo: Switch language / indic-auto and run a regional turn
           (e.g., Tamil or Marathi) showing native Prisma STT + Timbre TTS response.
1:55–2:30  Engineering Deep-Dive & Benchmark: Highlight 20ms InputJitterBuffer + PLC,
           80Hz HPF + adaptive noise floor, async 2-clause streaming, DPDP PII redaction,
           p50/p95 SLO bar, and run Benchmark (20/20 + 30/30 10-lang, 46/46 pytest).
```

---

## 5. Final Submission Checklist (before November 15, 2026)

- [ ] `.venv/bin/pytest -v` → **46/46 passing**
- [ ] `.venv/bin/python backend/eval_runner.py` → **20/20 + 30/30 + engineering checks all `true`**
- [ ] `python verify_gnani_api.py` green with live `GNANI_API_KEY` (no `.env` committed to Git!)
- [ ] `PITCH.md` (1-page pitch) and `EVALUATION.md` pushed to `main` on GitHub
- [ ] Record 2–3 min demo video (YouTube Unlisted / Google Drive public-view link for Form Field 6)
- [ ] Publish LinkedIn & X demo posts with native uploaded video, tagging **@Gnani.ai** / **@GnaniAi** and `#GnaniAI #GreatIndianAIInternshipChallenge`
- [ ] Submit the official Gnani email form with all fields verified

**LinkedIn post copy (attach native 2–3 min demo video):**

> Over 1.2 lakh road crashes happen across India every year — and stranded drivers call helplines in noisy highway or 8 kHz rural conditions, speaking Hinglish or regional languages while stressed.
>
> I built **BimaVani (बीमा-वाणी)** on **Gnani.ai's Prisma v3.0 & Timbre v3.0** stack — a 24×7 voice-first roadside assistance and IRDAI motor-claim desk across **all 10 Gnani Indian languages**:
> • Injury-first triage (112 Ambulance + Police + 1033 NHAI) with human handoff
> • Smart NCB-Saver workflow (puncture/battery → on-spot mechanic, zero claim filed to protect the driver's 20% No-Claim Bonus)
> • Auto-drafted IRDAI FNOL math + ₹1,500 rural towing reimbursement + voice confirmation
> • Production telephony engineering: 20 ms WebSocket streaming, InputJitterBuffer with Packet Loss Concealment (PLC), 80 Hz high-pass diesel-rumble DSP, adaptive noise-floor Auto-VAD, async 2-clause Timbre streaming behind a 120 ms OutputJitterBuffer gate, `<50 ms` barge-in flush, DPDP PII redaction, and rolling p50/p95 SLO telemetry.
>
> Verified: 46/46 automated tests, 20/20 acoustic benchmark, and 30/30 across all 10 Gnani languages.
> Tagging **@Gnani.ai** `#GnaniAI #GreatIndianAIInternshipChallenge` — GitHub repo in the comments! 🛣️

---

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| Pill shows `🛡 FALLBACK` | Set `GNANI_API_KEY` via **🔑 Gnani API** modal or `.env` |
| `HTTP 429` in server logs | `asyncio.Semaphore(1)` + exponential backoff handles it automatically; avoid multiple concurrent tabs |
| Browser mic blocked | Allow mic permissions on `localhost` / `127.0.0.1`, or toggle **Push-to-talk** / scripted chips |
| Need quick inspection | Hit `/api/health` and `/api/pipeline-state` for live p50/p95 SLOs, SNR, PLC, and queue depths |
