# How to Use, Judge-Demo & SUBMIT — BimaVani
*Add your key, verify live, record the 60-second video, submit. Deadline: November 10.*

---

## 1. Get & Add Your Gnani API Key (2 min)

1. Go to **https://app.gnani.ai/voice** (docs: https://docs.gnani.ai) → copy your programme key (5,000 credits included).
2. From the project folder:
   ```bash
   cp .env.example .env
   # edit .env and put your key into:
   #   GNANI_API_KEY=<your-key>
   ```

   **OR (no restart ever needed):** start the app first, click **🔑 Set Gnani API Key** in the navbar, paste, Save → the backend hot-reloads it into the running Gnani client (see proof on the pill: 🟢 LIVE GNANI).

```env
GNANI_API_KEY=...                 # required for live Prisma/Timbre
GNANI_STT_MODEL=prisma-v2.5       # or prisma-v3.0
GNANI_TTS_MODEL=timbre-v2.5       # or timbre-v3.0 — NEVER timbre-v2.0 (retired)
```

No .env? The app still runs in **Credit-Shield Fallback Mode** — every screen works, TTS returns local spoken-tone audio, 0 credits burned.

## 2. Verify the Live API (1 command)

```bash
./run.sh --verify          # or: python verify_gnani_api.py
```
Expected: synthesized test phrase → fed into Prisma → **live transcript, request_id, round-trip ms**.
In-app: click **🧪 Test Live Gnani API** in the navbar.

## 3. Run the Full Demo

```bash
./run.sh                   # server on http://127.0.0.1:8000
```

1. Tab 1 → press **📞 Call 1-800-BIMA-VANI** (timer starts, waveform animates).
2. Pick a **🔊 Noise preset** — set **NH-44 Highway Horns**, keep *mix into mic audio* + *play aloud to judges* ON.
3. Click **Scenario (1)** — NH-44 crash. Watch the 8-box pipeline light up; then Scenario (5) for the 112 emergency escalation.
4. During the spoken read-back, press **⚡ Interrupt** — this demonstrates a barge-in policy correction ("ruko…"). Cards 1 & 2 update instantly with the corrected/locked tickets.
5. Hold **🎙️** and actually speak Hinglish to see live Prisma (needs the key).
6. Tab 2 → **▶ Run 20-Case Benchmark** for the honest evaluation + waterfall.
7. Tab 3 → the 60-second script you're about to read into the video.

## 4. Screen-Record the 60-Second Video

Script lives in Tab 3 and `EVALUATION.md §60s`. The beats:

```text
0:00 hook: "NH-44 par crash ho gaya… engine se bhaap!" (noisy highway audio)
0:05 noise is mixed INTO mic — Prisma still reads "radiator… policy 7820"
0:14 agent: "Shukar hai aap safe hain! … gaadi start mat kijiye." + 1033 + free tow
0:24 BARGE-IN: "Ruko! policy 4491 hai!" → cards update, flush X ms shown
0:34 claim math on screen: ₹1,000 deductible, est ₹11,500–14,000, surveyor label
0:44 stack: deterministic brain, semaphore, MD5 cache, bounded queues
0:54 CTA "BimaVani — voice jo bima hai." + github link
```

**Recording tips:** dark console already looks pro; zoom to the RIGHT panel when the claim cards update; let the flush-latency number be visible for 1s; total ≤ 60s; export 1080p.

## 5. Submit (before November 10)

Checklist:
- [ ] `run.sh` + `run.bat` fresh-boot clean (delete `.venv` once and re-run to be sure)
- [ ] `python verify_gnani_api.py` green in LIVE mode
- [ ] 35/35 tests: `./run.sh --test`
- [ ] One video take with barge-in beat included
- [ ] GitHub repo pushed: code + README.md + EVALUATION.md + this file (no `.env`! `.gitignore` already blocks it)

**LinkedIn post copy (paste + edit):**

> 🚨 1.2 lakh road accidents, and calls into roadside-helplines happen at 8kHz quality from the emergency site. Voice AI that works there must survive: truck horns, Hinglish, a student API key, and angry drivers interrupting mid-sentence.
>
> I built **BimaVani (बीमा-वाणी)** — a 24×7 voice-first RSA + IRDAI claim desk. 1-800-BIMA-VANI in one call: injury triage to 112/1033 with human handoff, NCB-saving minor repairs (don't file a claim for a puncture!), free flatbed tow up to 50 km for non-drivable crashes, rural-road ₹1,500 towing reimbursement protocol, and an auto-filled FNOL draft — all confirmed by the driver's OWN voice.
>
> Engineering highlights:
> — an 8-stage hybrid streaming-turn pipeline, lit up live with per-stage latency
> — noise-proof push-to-talk by default (truck horns can't fake speech into the VAD)
> — asyncio.Semaphore(1) + 429-retry so my student-tier Gnani key never trips
> — max 2 TTS clauses per turn + MD5 disk cache = credit shield
> — barge-in: browser audio stops <20ms, backend flushes queues & updates cards <50ms
> — 100% deterministic Hinglish tool-brain: zero LLM, zero hallucination, works with only a Gnani key
>
> Stack: Gnani Prisma STT + Timbre TTS (voice Nalini) · FastAPI · Web Audio API. Repo + latency waterfall in the comments. 🛣️

*(Optional: attach the video — LinkedIn prefers uploaded native video.)*

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| Pill shows "🛡 FALLBACK" | No key loaded → 🔑 modal or `.env` + restart |
| `HTTP 429` in server logs | Semaphore(1) + auto-retry is engaged; reduce simultaneous tabs |
| Browser mic blocked | Allow mic on `127.0.0.1`; use Spacebar PTT alternative |
| 8-box bar stuck | hit `/api/health` — stage telemetry resets each turn |
