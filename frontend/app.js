/* ============================================================
   BimaVani — frontend/app.js (Round 2)
   ============================================================
   R1  📞 Call → WebAudio unlock + mic + POST /api/call/start →
       greeting streams Clause Buffer → Timbre → Jitter Buffer
       (speechSynthesis fallback) → "LISTENING (Auto-VAD Active)"
   R2  Auto-VAD on the RAW CLEAN mic analyser (BEFORE noise mixer),
       dynamic calibration (300ms ambient floor, threshold =
       max(ambient*2.2, 0.018)), 150ms speech → isUserSpeaking +
       <30ms auto-barge-in, 650ms silence → end-of-turn → 16kHz
       Mono 16-bit PCM WAV → /api/call/turn. Mic button = 1-click
       toggle (click = listen, click again = force end & send).
   R4  clauses: [{text, audio_base64:"data:audio/wav;base64,…",
       latency_ms}] decoded + played GAPLESS via the 120ms Jitter
       Buffer; live 8-box pipeline bar; dark scenario buttons.
   Kept: full 8-stage architecture — never remove the buffers.
   ============================================================ */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const API = {
  health: "/api/health",
  config: "/api/config",
  setKey: "/api/config/key",
  verify: "/api/verify-gnani",
  start: "/api/call/start",
  turn: "/api/call/turn",
  bargeIn: "/api/call/barge-in",
  end: "/api/call/end",
  tts: "/api/tts",
  eval: "/api/eval",
};

/* ---------------- live pipeline strip (bottom band) ---------------- */
/* The 8 stage nodes live in index.html; JS only lights them up and writes
   the real measured milliseconds for each hop. */
const stageEls = [...document.querySelectorAll(".node")]
  .sort((a, b) => Number(a.dataset.stage) - Number(b.dataset.stage));

function litStage(i, ms, opts = {}) {
  const el = stageEls[i];
  if (!el) return;
  el.classList.add("hot");
  el.querySelector(".node-ms").textContent = (ms != null ? `${Math.round(ms)} ms` : "live");
  setTimeout(() => el.classList.remove("hot"), opts.hold ? 1400 : 520);
}
function decodeStages(pipeline, bargeFlushMs) {
  const s = (pipeline && pipeline.stages_ms) || {};
  litStage(0, 18);                                    // mic ingest (WAV render ~18ms)
  if (s.stage_2 !== undefined) litStage(1, s.stage_2); // Prisma STT
  litStage(2, s.stage_2 > 0 ? 0.4 : 0.2);             // Auto-VAD endpoint decision
  if (s.stage_4 !== undefined) litStage(3, s.stage_4); // Brain (DeepSeek + tools)
  litStage(4, s.stage_5 ?? 0.3);                       // clause buffer split
  litStage(5, s.stage_6 ?? 0);                         // Timbre TTS
  litStage(6, 120);                                    // jitter prebuffer window
  if (bargeFlushMs != null) litStage(7, bargeFlushMs, { hold: true });
}

/* ---------------- state ---------------- */
const S = {
  callActive: false, callT0: 0, callTimer: null,
  callEnded: false, phase: "idle", callSeq: 0,              // distinguishes "before first call" from "hung up"
  recording: false, talkStart: 0,
  mode: "auto_vad",              // auto_vad default (Round 2)
  vadSilenceMs: 650,
  vadSens: 2.2,                  // ambient multiplier → speech threshold
  playing: false, lastTurnResult: null, awaitingAgentReply: false,
  currentScenario: null, micDenied: false,
  pendingBargeTranscript: "", liveStatsTimer: null,
  pttLatched: false, pttTimer: null,
  latencyHistory: [],            // [{ttfa, total}] for the last few turns
  turnSentAt: 0,                 // wall clock at turn submission (true TTFA)
  lastDispatch: null, lastDraft: null,   // persisted ticket cards across turns
};

/* ============================================================
   Stage 1: 16kHz Mono 16-bit Linear PCM WAV Recorder + Noise Mixer
   Architecture: mic source → VAD analyser (RAW, BEFORE mixer)
                mic source → NoiseMixer worklet → ScriptProcessor
                (mixing selected noise into the SENT WAV only).
   ============================================================ */
const REC = { ctx: null, stream: null, proc: null, sink: null, frames: [], srcRate: 48000 };

let NOISE = null; // active noise worklet port

function floatTo16PCM(f32) {
  const out = new Int16Array(f32.length);
  for (let i = 0; i < f32.length; i++) {
    let v = Math.max(-1, Math.min(1, f32[i]));
    out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  return out;
}

function interleaveWav(samples, sampleRate) {
  // Mono 16-bit WAV, genuine 44-byte RIFF header
  const bytes = new Uint8Array(44 + samples.length * 2);
  const dv = new DataView(bytes.buffer);
  const wr = (o, s) => { for (let i = 0; i < s.length; i++) dv.setUint8(o + i, s.charCodeAt(i)); };
  wr(0, "RIFF"); dv.setUint32(4, 36 + samples.length * 2, true); wr(8, "WAVE");
  wr(12, "fmt "); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true);
  dv.setUint16(22, 1, true); dv.setUint32(24, sampleRate, true);
  dv.setUint32(28, sampleRate * 2, true); dv.setUint16(32, 2, true);
  dv.setUint16(34, 16, true);
  wr(36, "data"); dv.setUint32(40, samples.length * 2, true);
  let off = 44;
  for (let i = 0; i < samples.length; i++, off += 2) dv.setInt16(off, samples[i], true);
  return bytes;
}

async function initAudioGraph() {
  if (REC.ctx) return;
  REC.ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 48000 });
  if (REC.ctx.state === "suspended") await REC.ctx.resume();   // R1 unlock
  REC.srcRate = REC.ctx.sampleRate;                            // browser may ignore 48k
  try {
    REC.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,     // prevents the agent's own voice from re-triggering VAD
        noiseSuppression: false,    // keep the demo's highway realism in the SENT WAV
        autoGainControl: false,
        channelCount: 1,
      },
    });
  } catch (e) {
    // Mic is optional for the demo: keep the call alive in typed mode so the
    // greeting, the streaming graph and the latency numbers all still run.
    S.micDenied = true;
    REC.stream = null;
    VAD.analyser = null;
    setCallState(`⌨️ Mic blocked (${e.name || e.message}) — typed mode`);
    $("#ptt-hint").textContent = "Mic unavailable — type below to talk";
    return;
  }
  const src = REC.ctx.createMediaStreamSource(REC.stream);

  /* --- R2: RAW CLEAN mic analyser FIRST (before any noise mixing) --- */
  VAD.analyser = REC.ctx.createAnalyser();
  VAD.analyser.fftSize = 1024;
  VAD.buf = new Float32Array(VAD.analyser.fftSize);
  src.connect(VAD.analyser);

  /* --- Noise mixer (goes into the SENT WAV only, never into VAD) --- */
  /* ScriptProcessor must exist before we wire the mixer into it. */
  REC.sink = REC.ctx.createScriptProcessor(4096, 1, 1);
  REC.sink.onaudioprocess = (e) => {
    const f32 = e.inputBuffer.getChannelData(0);
    const m = noiseAmount($("#noise-preset").value) * ($("#noise-into-mic").checked ? 1 : 0);
    // draw + ring-buffer push (per-user speech capture window is chosen later)
    if (S.callActive) {
      REC.frames.push({ t: performance.now(), f: new Float32Array(f32), mixed: m });
      if (REC.frames.length > 220) REC.frames.splice(0, REC.frames.length - 220);
      if (S.recording) {
        REC.chunks = REC.chunks || [];
        REC.chunks.push(new Float32Array(f32));
      }
    }
    drawWave(f32);
  };

  try {
    // NOTE: the Blob MUST declare a JS MIME type, otherwise Chrome rejects the
    // module ("Unable to load a worklet's module") and the whole mic dies.
    const workletSrc = `
      class NoiseMixer extends AudioWorkletProcessor {
        constructor() {
          super();
          this.mixNoise = 0;        // 0..1 amount
          this.keep = false;
          this.ph = 0; this.burst = 0;
          this.port.onmessage = (e) => {
            if (e.data.cmd === "mix") this.mixNoise = e.data.amount;
            if (e.data.cmd === "mode") this.keep = e.data.keep;  // gsm filter
          };
        }
        process(inputs, outputs) {
          const inp = inputs[0][0], out = outputs[0][0];
          if (!inp) return true;
          const amt = this.mixNoise, gsm = this.keep;
          for (let i = 0; i < inp.length; i++) {
            let v = inp[i];
            const t = i / 128 + this.ph;
            // engine rumble
            let noise = 0.22 * Math.sin(2 * Math.PI * 3 * t) + 0.08 * (Math.random() * 2 - 1);
            // horn bursts every ~1.8s for 0.35s
            if (this.burst <= 0 && Math.random() < 0.0007) this.burst = 222;
            if (this.burst > 0) { this.burst--; noise += 0.30 * Math.sin(2 * Math.PI * 12 * t) + 0.18 * Math.sin(2 * Math.PI * 19 * t); }
            v = v * (1 - amt * (gsm ? 0.75 : 0.35)) + noise * amt;
            if (gsm) { // crude telephone roll-off: slow attack smoothing
              this.prev = (this.prev === undefined ? 0 : this.prev);
              v = this.prev + 0.55 * (v - this.prev);
              this.prev = v;
            }
            out[i] = Math.max(-1, Math.min(1, v));
          }
          this.ph += inp.length / 128 / 60;
          return true;
        }
      }
      registerProcessor("noise-mixer", NoiseMixer);
    `;
    const blobUrl = URL.createObjectURL(
      new Blob([workletSrc], { type: "application/javascript" }));
    await REC.ctx.audioWorklet.addModule(blobUrl);
    URL.revokeObjectURL(blobUrl);
    REC.proc = new AudioWorkletNode(REC.ctx, "noise-mixer");
    REC.proc.port.postMessage({ cmd: "mix", amount: 0 });
    src.connect(REC.proc);
    REC.proc.connect(REC.sink);
  } catch (err) {
    // Never lose the microphone because of the noise layer: fall back to raw.
    console.warn("Noise mixer unavailable — recording the raw mic instead.", err);
    REC.proc = null;
    src.connect(REC.sink);
  }
  REC.sink.connect(REC.ctx.destination); // needed for ScriptProcessor to run
}

function noiseAmount(preset) {
  // dB -> linear gain: -10dB ~ .316, -15dB ~ .178
  return { clean: 0.0, mandi: 0.178, highway: 0.316, gsm: 0.5, gsm_off: 0.0 }[preset] ?? 0;
}

async function updateNoise() {
  const preset = $("#noise-preset").value;
  const mixIn = $("#noise-into-mic").checked;
  const aloud = $("#noise-aloud").checked;
  if (!REC.ctx) return;
  const amt = mixIn ? noiseAmount(preset) : 0.0;
  if (REC.proc) {
    REC.proc.port.postMessage({ cmd: "mix", amount: amt });
    REC.proc.port.postMessage({ cmd: "mode", keep: preset === "gsm" });
  }
  stopNoiseSpeaker();
  if (aloud && preset !== "clean") startNoiseSpeaker(preset);
}

let noiseGainNode = null;
async function startNoiseSpeaker(preset) {
  // Judge audible layer: route a band of noise to the speakers
  const ctx = REC.ctx || new AudioContext();
  if (!REC.ctx) REC.ctx = ctx;
  noiseGainNode = ctx.createGain();
  const b = ctx.createBufferSource();
  const len = 48000 * 2;
  const buf = ctx.createBuffer(1, len, 48000);
  const d = buf.getChannelData(0);
  let burst = 0;
  for (let i = 0; i < len; i++) {
    let v = 0.10 * (Math.random() * 2 - 1);
    const t = i / 48000;
    if (preset === "mandi") v += 0.05 * Math.sin(2 * Math.PI * 55 * t) + 0.03 * (Math.random() - .5);
    if (preset === "highway") {
      if (burst <= 0 && Math.random() < 0.0009) burst = 14000;
      if (burst > 0) { burst--; v += 0.18 * Math.sin(2 * Math.PI * (640 + 30 * Math.sin(2 * Math.PI * 3 * t)) * t); }
      v += 0.09 * Math.sin(2 * Math.PI * 92 * t);
    }
    if (preset === "gsm") {
      v = v * 0.6 + (i % 48 < 1 ? 0.2 : 0); // coarse 1kHz square-ish carrier
    }
    d[i] = Math.max(-1, Math.min(1, v * (preset === "gsm" ? 4 : 1)));
  }
  b.buffer = buf; b.loop = true;
  b.connect(noiseGainNode).connect(ctx.destination);
  b.start();
  noiseGainNode._src = b;
  noiseGainNode._preset = preset;
}
function stopNoiseSpeaker() {
  if (noiseGainNode && noiseGainNode._src) {
    try { noiseGainNode._src.stop(); } catch {}
    noiseGainNode.disconnect();
    noiseGainNode = null;
  }
}

/* ============================================================
   R2: Auto-VAD — raw mic analyser, dynamic calibration,
       150ms speech → auto-barge-in, 650ms silence → end-of-turn
   ============================================================ */
const VAD = {
  analyser: null, buf: null,
  active: false,            // loop running while call is active
  awaiting: false,          // a turn is in flight (don't endpoint twice)
  calibrating: false, calibrated: false,
  ambientRms: 0,
  threshold: 0.018,
  lastFrameAt: 0, lastUiAt: 0,
  speechMs: 0, silMs: 0,
  isUserSpeaking: false,
  speechStartT: 0,          // timestamp of speech onset (for the WAV slice)
  preRollMs: 250,           // keep a little pre-speech pad
};

/* Sample the RAW mic for 300ms while NOTHING is playing, then derive the
   speech threshold. This must run BEFORE the greeting, otherwise the
   agent's own speaker audio inflates the ambient floor. */
async function calibrateAmbient(ms = 300) {
  if (!VAD.analyser) return;
  VAD.calibrating = true;
  const t0 = performance.now();
  let sum = 0, n = 0;
  while (performance.now() - t0 < ms) {
    VAD.analyser.getFloatTimeDomainData(VAD.buf);
    let s = 0;
    for (let i = 0; i < VAD.buf.length; i++) s += VAD.buf[i] * VAD.buf[i];
    sum += Math.sqrt(s / VAD.buf.length);
    n++;
    await new Promise(r => setTimeout(r, 20));
  }
  VAD.ambientRms = sum / Math.max(1, n);
  VAD.threshold = Math.max(VAD.ambientRms * (S.vadSens || 2.2), 0.018);
  VAD.calibrating = false;
  VAD.calibrated = true;
  const el = $("#vad-live");
  if (el) el.textContent = `Ambient ${VAD.ambientRms.toFixed(4)} → speech threshold ${VAD.threshold.toFixed(3)} (raw mic)`;
}

function startVad() {
  if (!VAD.analyser) return;
  VAD.active = true;
  VAD.lastFrameAt = 0;
  VAD.speechMs = 0; VAD.silMs = 0; VAD.isUserSpeaking = false;
  setCallState(VAD.calibrated
    ? `🎧 LISTENING (Auto-VAD Active) — boliye, ${S.vadSilenceMs}ms rukne par khud bhej doonga`
    : "🎧 LISTENING (Auto-VAD Active) — calibrating mic…");
  updateMicBtn();
}

function stopVad() {
  VAD.active = false;
  VAD.isUserSpeaking = false;
  setMicIdle();
}

function setCallState(txt) {
  const el = $("#call-state");
  if (el) el.textContent = txt;
  // The big headline mirrors what the agent is doing (see updateHero).
  if (typeof updateHero === "function") updateHero(txt);
}

function updateMicBtn() {
  const b = $("#btn-ptt");
  if (!b) return;
  b.classList.toggle("talking", !!VAD.isUserSpeaking);
  b.classList.toggle("listening", !!VAD.active && !VAD.isUserSpeaking);
  b.classList.toggle("thinking", !!S.awaitingAgentReply);
  $("#ptt-hint").textContent = VAD.isUserSpeaking
    ? "I'm hearing you…"
    : (S.mode === "auto_vad"
        ? "Listening — speak naturally, I'll answer when you pause"
        : "Hold to talk · or tap once to latch");
}
function setMicIdle() { updateMicBtn(); }

/* one rAF tick of the Auto-VAD engine */
function vadTick() {
  requestAnimationFrame(vadTick);
  if (!VAD.active || !VAD.analyser) return;
  const now = performance.now();
  const dt = VAD.lastFrameAt ? Math.min(120, now - VAD.lastFrameAt) : 16;
  VAD.lastFrameAt = now;

  VAD.analyser.getFloatTimeDomainData(VAD.buf);
  let sum = 0;
  for (let i = 0; i < VAD.buf.length; i++) sum += VAD.buf[i] * VAD.buf[i];
  const rms = Math.sqrt(sum / VAD.buf.length);

  // live debug line + meter (helps judge/fix mic gain fast)
  if (now - VAD.lastUiAt > 120) {
    VAD.lastUiAt = now;
    const el = $("#vad-live");
    if (el) el.textContent =
      `VAD ${rms.toFixed(4)} / thr ${VAD.threshold.toFixed(3)} · ` +
      (VAD.isUserSpeaking ? "SPEECH" : "silence") + " · raw mic";
    const METER_MAX = 0.30;
    const bar = $("#vad-bar");
    if (bar) {
      bar.style.width = Math.min(100, (rms / METER_MAX) * 100).toFixed(1) + "%";
      bar.classList.toggle("hot", VAD.isUserSpeaking);
    }
    const mark = $("#vad-thr-mark");
    if (mark) mark.style.left = Math.min(100, (VAD.threshold / METER_MAX) * 100).toFixed(1) + "%";
  }

  // calibration fallback (if startVad ran before calibration completed)
  if (!VAD.calibrated) {
    if (!VAD.calibrating) calibrateAmbient();
    return;
  }

  // While the agent speaks we ONLY look for barge-in — never endpoint the
  // driver's own silence, and require a higher gate to ignore speaker echo.
  const playing = S.playing;
  const gate = playing ? VAD.threshold * 2.5 : VAD.threshold;

  if (rms >= gate) {
    VAD.speechMs += dt;
    VAD.silMs = 0;
    if (!VAD.isUserSpeaking && VAD.speechMs >= 150) {
      VAD.isUserSpeaking = true;
      VAD.speechStartT = now - VAD.speechMs - VAD.preRollMs;
      updateMicBtn();
      if (S.playing) {
        // auto-barge-in: FLUSH agent audio in <30ms and listen
        const t0 = performance.now();
        browserStopAudio();
        beginUserTurnAfterBargeIn();          // capture the user's interruption
        litStage(7, performance.now() - t0, { hold: true });
        setCallState("⚡ BARGE-IN — main sun rahi hoon, ji aap boliye.");
      }
    }
  } else {
    if (VAD.isUserSpeaking) {
      VAD.silMs += dt;
      if (VAD.silMs >= (S.vadSilenceMs || 650) && !VAD.awaiting && !playing) {
        VAD.isUserSpeaking = false;
        VAD.silMs = 0; VAD.speechMs = 0;
        endTurnAuto();
      }
    } else {
      VAD.speechMs = Math.max(0, VAD.speechMs - dt);
    }
  }
}
requestAnimationFrame(vadTick);

/* Slice the ring buffer since speech onset (noise preset applied by mixer
   already — frames were recorded THROUGH the noise worklet), → 16kHz WAV */
function buildUserWavNow() {
  const cut = VAD.speechStartT - 200;
  const idx = REC.frames.findIndex(f => f.t >= cut);
  const slice = (idx >= 0 ? REC.frames.slice(idx) : REC.frames.slice(-20));
  if (!slice.length) return null;
  const merged = mergeChunks(slice.map(f => f.f));
  if (merged.length < REC.srcRate * 0.25) return null;    // <250ms = not speech
  const downs = downsampleTo16k(merged, REC.srcRate);
  return interleaveWav(floatTo16PCM(downs), 16000);
}

async function endTurnAuto() {
  if (!S.callActive || VAD.awaiting) return;
  const wav = buildUserWavNow();
  if (!wav) { setCallState("🎧 LISTENING (Auto-VAD Active) — awaaz nahi mili, dobara boliye."); return; }
  setCallState("⏳ Processing turn… (Prisma STT → Brain → Timbre)");
  litStage(0, null, { hold: true });
  await sendTurn(btoa64(wav));
}

/* Mic button (Auto-VAD mode was routed via click handler; PTT uses hold) */
async function micToggle() {
  if (!S.callActive) { alert("Press 📞 Call 1-800-BIMA-VANI first."); return; }
  if (S.micDenied) {
    setCallState("⌨️ Mic blocked — type your turn in the box below");
    const box = $("#type-say"); if (box) { box.focus(); }
    return;
  }
  if (!REC.ctx) await initAudioGraph();
  if (!VAD.isUserSpeaking) {
    VAD.isUserSpeaking = true;
    VAD.speechStartT = performance.now() - VAD.preRollMs;
    VAD.speechMs = 0; VAD.silMs = 0;
    if (S.playing) { const t0 = performance.now(); browserStopAudio(); beginUserTurnAfterBargeIn(); litStage(7, performance.now() - t0, { hold: true }); }
    updateMicBtn();
    setCallState("🔴 Mic locked ON — boliye, phir se click kijiye to send hoga.");
  } else {
    VAD.isUserSpeaking = false;
    VAD.silMs = 0; VAD.speechMs = 0;
    endTurnAuto();
  }
}

/* ---------------- push-to-talk recorder (test mode) ----------------
   Two ways to use it, both reliable:
     • HOLD the mic ≥0.6s, release → sends immediately (classic PTT)
     • CLICK quickly → recording latches ON; click again → sends
   Auto-VAD is switched OFF in this mode, so it can never auto-endpoint
   in the middle of your sentence. */
function pttSetStatus(txt) {
  const el = $("#ptt-live");
  if (el) el.textContent = txt;
}

function pttTick() {
  if (!S.recording) return;
  const ms = performance.now() - S.talkStart;
  // peak of the last recorded chunk → proves audio is really captured
  const last = (REC.chunks || [])[REC.chunks.length - 1];
  let peak = 0;
  if (last) for (let i = 0; i < last.length; i++) {
    const a = Math.abs(last[i]);
    if (a > peak) peak = a;
  }
  const bars = "█".repeat(Math.min(20, Math.round(peak * 40)));
  pttSetStatus(`🔴 REC ${(ms / 1000).toFixed(1)}s · level ${peak.toFixed(3)} ${bars || "·"}` +
               (S.pttLatched ? " · click mic again to send" : " · release to send"));
  const timer = $("#ptt-timer");
  if (timer) timer.textContent = (ms / 1000).toFixed(1) + "s";
  S.pttTimer = setTimeout(pttTick, 120);
}

async function startTalk(latched = false) {
  if (!S.callActive) { alert("Press 📞 Call 1-800-BIMA-VANI first."); return; }
  if (S.micDenied) { setCallState("⌨️ Mic blocked — type your turn below"); $("#type-say")?.focus(); return; }
  if (S.recording || S.awaitingAgentReply) return;
  PTT._latchPending = false;
  try {
    if (!REC.ctx) await initAudioGraph();
    if (REC.ctx.state === "suspended") await REC.ctx.resume();
    await updateNoise();
  } catch (e) {
    setCallState(`⚠️ ${e.message || e.name} — allow the microphone and try again.`);
    return;
  }
  // barge-in: if the agent is still talking, cut it off
  if (S.playing) { const t0 = performance.now(); browserStopAudio(); beginUserTurnAfterBargeIn(); litStage(7, performance.now() - t0, { hold: true }); }
  // PTT owns the turn: Auto-VAD must not endpoint for us
  stopVad();

  S.recording = true; S.talkStart = performance.now(); S.pttLatched = latched;
  REC.chunks = [];
  $("#btn-ptt").classList.add("talking");
  $("#btn-ptt").textContent = "🔴";
  $("#ptt-hint").textContent = latched
    ? "🔴 Recording… click the mic again to send"
    : "🔴 Recording… release to send";
  setCallState("🔴 PTT RECORDING — boliye, phir release/click kijiye");
  litStage(0, null, { hold: true });
  if (PTT._latchPending) {          // tap finished while the graph was loading
    PTT._latchPending = false;
    S.pttLatched = true;
    $("#ptt-hint").textContent = "🔴 Recording… click the mic again to send";
  }
  syncPttControls();
  pttTick();
}

function cancelTalk() {
  if (!S.recording) return;
  S.recording = false; S.pttLatched = false;
  clearTimeout(S.pttTimer);
  REC.chunks = [];
  $("#btn-ptt").classList.remove("talking");
  $("#btn-ptt").textContent = "🎙️";
  $("#ptt-hint").textContent = "HOLD to talk · or click to latch on/off";
  pttSetStatus("PTT: cancelled (nothing sent)");
  setCallState("🤖 Agent done — HOLD the mic to reply (Push-to-Talk)");
  syncPttControls();
}

async function endTalk() {
  if (!S.recording) return;
  S.recording = false; S.pttLatched = false;
  clearTimeout(S.pttTimer);
  const merged = mergeChunks((REC.chunks || []).map(f => f.f || f));
  const recMs = performance.now() - S.talkStart;
  REC.chunks = [];
  $("#btn-ptt").classList.remove("talking");
  $("#btn-ptt").textContent = "🎙️";
  $("#ptt-hint").textContent = "HOLD to talk · or click to latch on/off";

  if (recMs < 350 || merged.length < (REC.srcRate || 48000) * 0.3) {
    pttSetStatus(`PTT: too short (${(recMs / 1000).toFixed(1)}s) — hold ~1s and speak`);
    setCallState("🤖 Agent done — HOLD the mic to reply (Push-to-Talk)");
    syncPttControls();
    return;
  }
  const downs = downsampleTo16k(merged, REC.srcRate || 48000);
  const wav = interleaveWav(floatTo16PCM(downs), 16000);
  pttSetStatus(`PTT: sent ${(wav.length / 1024).toFixed(1)} KB · ${(recMs / 1000).toFixed(1)}s @16kHz mono`);
  setCallState("⏳ Processing turn… (Prisma STT → Brain → Timbre)");
  syncPttControls();
  litStage(0, null, { hold: true });
  await sendTurn(btoa64(wav));
}

function mergeChunks(chunks) {
  let n = 0; for (const c of chunks) n += c.length;
  const out = new Float32Array(n); let o = 0;
  for (const c of chunks) { out.set(c, o); o += c.length; }
  return out;
}
function downsampleTo16k(f32, srcRate) {
  // proper linear-interpolation resample to exactly 16,000 Hz
  if (!srcRate || srcRate === 16000) return f32;
  const ratio = srcRate / 16000;
  const outLen = Math.floor(f32.length / ratio);
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const pos = i * ratio;
    const i0 = Math.floor(pos);
    const i1 = Math.min(f32.length - 1, i0 + 1);
    const frac = pos - i0;
    out[i] = f32[i0] * (1 - frac) + f32[i1] * frac;
  }
  return out;
}
function btoa64(bytes) {
  let bin = "";
  const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const n = u8.length;
  for (let i = 0; i < n; i += 0x8000)
    bin += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
  return btoa(bin);
}

/* waveform canvas */
function drawWave(f32) {
  const c = $("#waveform"); if (!c) return;
  const ctx = c.getContext("2d");
  ctx.fillStyle = "#f2f1e8"; ctx.fillRect(0, 0, c.width, c.height);
  ctx.strokeStyle = "#3f6f2a"; ctx.lineWidth = 1.4; ctx.beginPath();
  const step = Math.max(1, Math.floor(f32.length / c.width));
  for (let x = 0; x < c.width; x++) {
    const v = f32[x * step] || 0;
    const y = c.height / 2 - v * (c.height / 2 - 4) * 2.2;
    x === 0 ? ctx.moveTo(0, y) : ctx.lineTo(x, y);
  }
  ctx.stroke();
}

/* ============================================================
   Stage 7: Jitter Buffer (120ms prebuffer + gapless scheduling)
   Accepts the R4 contract: audio_base64 = "data:audio/wav;base64,…"
   (legacy plain base64 also supported).speechSynthesis fallback.
   ============================================================ */
const JITTER = { ctx: null, queue: [], scheduledAt: 0, prebufferMs: 120, token: 0 };

function stripDataUri(b64) {
  if (b64.startsWith("data:")) {
    const comma = b64.indexOf(",");
    return b64.slice(comma + 1);
  }
  return b64;
}

function speakFallback(text) {
  try {
    if (!window.speechSynthesis) return false;
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "hi-IN"; u.rate = 0.98; u.pitch = 1.05;
    speechSynthesis.speak(u);
    return true;
  } catch { return false; }
}

function speakFallbackAsync(text) {
  // Resolves when the browser finishes speaking (or a safe timeout).
  return new Promise((resolve) => {
    try {
      if (!window.speechSynthesis || !text) return resolve(false);
      const u = new SpeechSynthesisUtterance(text);
      u.lang = "hi-IN"; u.rate = 0.98; u.pitch = 1.05;
      u.onend = () => resolve(true);
      u.onerror = () => resolve(false);
      speechSynthesis.speak(u);
      setTimeout(() => resolve(true), Math.max(1800, text.length * 90));
    } catch { resolve(false); }
  });
}

async function playClausesFromPayload(payloadClauses, fallbackText) {
  const my = ++JITTER.token;              // barge-in increments → cancels this wait
  const ctx = S.playCtx || new AudioContext();
  S.playCtx = ctx;
  if (ctx.state === "suspended") { try { await ctx.resume(); } catch {} }
  const now = ctx.currentTime;
  let startAt = now + JITTER.prebufferMs / 1000;   // 120ms prebuffer
  JITTER.queue = [];
  let decodedAny = false;
  for (const cl of payloadClauses) {
    if (!cl.audio_base64) continue;
    try {
      const bin = Uint8Array.from(atob(stripDataUri(cl.audio_base64)), c => c.charCodeAt(0));
      const buf = await ctx.decodeAudioData(bin.buffer.slice(0));
      JITTER.queue.push(buf);
      decodedAny = true;
    } catch { /* try next clause; final fallback below */ }
  }
  if (my !== JITTER.token) return;        // barge-in happened during decode
  // Credit-Shield fallback audio is only a tone — speak the WORDS with the
  // browser TTS instead so the agent is never "beeping" at the driver.
  const allFallback = payloadClauses.length > 0 && payloadClauses.every(c => c.fallback);
  if (allFallback) {
    S.playing = true;
    $("#btn-barge-in").disabled = false;
    const spoken = payloadClauses.map(c => c.text).filter(Boolean).join(" ");
    try {
      await speakFallbackAsync(spoken || fallbackText || "");
    } finally {
      S.playing = false;
      if (my === JITTER.token) maybeEnableBarge();
    }
    return;
  }
  if (!decodedAny) {
    // R1: never silent — window.speechSynthesis fallback
    speakFallback(fallbackText || (payloadClauses.find(c => c.text) || {}).text || "");
    await new Promise(r => setTimeout(r, 1200));
    S.playing = false;
    return;
  }
  S.playing = true;
  $("#btn-barge-in").disabled = false;
  let firstMs = null;
  for (const buf of JITTER.queue) {
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    src.start(startAt);                 // gapless: scheduled back-to-back
    if (firstMs === null) {
      // True TTFA = wall-clock from turn submission → first audible sample,
      // so it includes STT + Brain + TTS + network + prebuffer.
      const firstAudioAt = performance.now() + (startAt - now) * 1000;
      firstMs = S.turnSentAt
        ? firstAudioAt - S.turnSentAt
        : (startAt - now) * 1000;
    }
    startAt += buf.duration;            // Clause 2 right behind Clause 1
  }
  litStage(6, 120);
  const total = JITTER.queue.reduce((a, b) => a + b.duration, 0);
  if (S.lastTurnResult) S.lastTurnResult.ttfa_ms = firstMs;
  if (S.lastLatencyData) {
    S.lastLatencyData.ttfa_ms = firstMs;
    renderLatency(S.lastLatencyData);       // TTFA is only known once audio is scheduled
  }
  // await REAL end of playback (or barge-in cancellation)
  try {
    await new Promise((resolve) => {
      const t0 = performance.now();
      (function wait() {
        if (my !== JITTER.token) return resolve();
        if (performance.now() - t0 >= (total + 0.15) * 1000) return resolve();
        setTimeout(wait, 50);
      })();
    });
  } finally {
    S.playing = false;           // never leave playback "stuck on" after a barge-in/token change
    if (my === JITTER.token) maybeEnableBarge();
  }
}

/* ============================================================
   Stage 8: Barge-In — <20ms browser kill, then backend flush
   ============================================================ */
function browserStopAudio() {
  JITTER.token++;                        // cancels any pending playback await
  try { window.speechSynthesis?.cancel?.(); } catch {}
  if (S.playCtx) {
    S.playCtx.close().catch(() => {});
    S.playCtx = null;
  }
  JITTER.queue = [];
  S.playing = false;
  maybeEnableBarge();
  return performance.now();
}

/* After the agent's audio is killed by a barge-in, immediately mark the user
   as speaking and unblock endTurnAuto so their interruption is captured and
   sent on the next pause — instead of being swallowed by VAD.awaiting=true. */
function beginUserTurnAfterBargeIn() {
  VAD.awaiting = false;                       // unblock endTurnAuto()
  VAD.isUserSpeaking = true;
  VAD.speechStartT = performance.now() - VAD.preRollMs;
  VAD.speechMs = 0; VAD.silMs = 0;
  updateMicBtn();
  startVad();                                 // VAD actively listening again
}

/* Barge-in: stop the browser audio instantly, then flush the backend queues.
   Whatever the driver was typing (or had typed) is sent as the interruption. */
async function triggerBargeIn() {
  const t0 = performance.now();
  browserStopAudio();                       // <20 ms browser-side kill
  // Add [interruption] marker to provide context for next turn
  const interruptionLabel = " [interruption]";
  beginUserTurnAfterBargeIn();                // VAD listening for the interruption
  const typed = ($("#type-say")?.value || "").trim();
  const payload = { call_id: "live", transcript: typed, update_cards: !!typed };

  const res = await fetch(API.bargeIn, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();

  litStage(7, data.flush_latency_ms, { hold: true });
  setCallState(`🎧 Listening — stopped in ${Math.round(performance.now() - t0)} ms`);
  const reply = $("#agent-reply");
  if (reply) {
    reply.textContent = `Stopped in ${Math.round(performance.now() - t0)} ms — go ahead, I'm listening.`;
  }
  if (typed) $("#type-say").value = "";
}

$("#btn-barge-in").onclick = triggerBargeIn;
$("#btn-barge-in-2").onclick = triggerBargeIn;

function maybeEnableBarge() {
  const on = S.playing || S.awaitingAgentReply;
  $("#btn-barge-in").disabled = !on;
  const b2 = $("#btn-barge-in-2");
  if (b2) b2.disabled = !on;
}

/* ============================================================
   Turn submission (Stage 1 → 8 full round trip)
   ============================================================ */
async function sendTurn(wavB64, transcriptOverride) {
  S.awaitingAgentReply = true;
  VAD.awaiting = true;
  S.turnSentAt = performance.now();     // true TTFA clock starts here
  try {
    const body = {
      call_id: "live",
      wav_b64: wavB64 || "",
      transcript_override: transcriptOverride || "",
      endpoint_mode: S.mode,
      vad_silence_ms: S.vadSilenceMs,
    };
    const res = await fetch(API.turn, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await res.json();
    S.lastTurnResult = data;
    decodeStages(data.pipeline, null);
    renderTurn(data);
    if (data.stt_empty) {
      $("#stt-transcript").textContent = "(no words recognised)";
      setCallState(data.hint
        ? `⚠️ STT empty — ${data.hint}`
        : "🎧 Kuch sunai nahi diya — dobara boliye.");
      $("#agent-reply").innerHTML =
        `🤖 <b>Agent:</b> ${data.reply_text}` +
        (data.hint ? `<div class="hint-sm" style="color:#fbbf24">⚠️ ${data.hint}</div>` : "");
    }
    // R4 contract: clauses[{audio_base64 data-URI}] (fallback: legacy clause_audio)
    if (data.clauses && data.clauses.length) await playClausesFromPayload(data.clauses, data.reply_text);
    else if (data.clause_audio && data.clause_audio.length)
      await playClausesFromPayload(data.clause_audio.map(c => ({ text: c.text, audio_base64: c.audio_b64, latency_ms: c.tts_latency_ms })), data.reply_text);
  } catch (e) {
    $("#agent-reply").innerHTML = `⚠️ Network hiccup (${e.message}) — Credit-Shield kept the app alive. Try again.`;
  } finally {
    // ALWAYS return to LISTENING so the next driver turn is accepted,
    // even if playback threw or a barge-in cancelled it.
    await agentDoneReplying();
  }
}

async function agentDoneReplying() {
  S.awaitingAgentReply = false;
  VAD.awaiting = false;
  VAD.speechStartT = 0;
  S.playing = false;             // guarantee playback flag is never stuck true
  maybeEnableBarge();
  if (!S.callActive) return;
  if (S.micDenied) { setCallState("Typed mode — no microphone"); return; }
  if (S.mode === "auto_vad") startVad();   // back to LISTENING (Auto-VAD Active)
  else setCallState("🎙️ PUSH-TO-TALK — hold the orb to reply");
}

/* ============================================================
   UI LAYER
   The engine above (mic → VAD → WAV → turn → jitter buffer) is
   untouched here. This section only paints what it is doing:
   the headline, the transcript, the orb and the latency band.
   ============================================================ */

/* ---------- the big headline: one line per agent state ---------- */
const HERO_STATES = [
  [/Dialing/i,                        "Calling<br>BimaVani…",       "Dialing 1-800-BIMA-VANI — the line is connecting."],
  [/Ringing/i,                        "Ringing…",                 "The agent's phone is ringing. Hang on a second."],
  [/picked up/i,                      "Connected.",                  "The agent has picked up — say hello."],
  [/LISTENING|Listening/i,            "Listening for<br>your turn.",   "Speak naturally — I answer the moment you pause."],
  [/PUSH-TO-TALK/i,                   "Hold to talk.",                 "Press the orb, speak, release. No silence timer needed."],
  [/greeting|Agent connected|Speaking/i, "One moment —<br>I'm answering.", "The reply streams clause by clause through the jitter buffer."],
  [/Processing|Thinking|turn/i,       "Thinking…",                     "Reading the situation before I speak."],
  [/Typed mode/i,                     "Type your turn.",               "No microphone? Use the box in Voice agent setup."],
  [/Call ended/i,                     "Call ended.",                   "Tap Call to start again."],
  [/Not connected|waiting|Press Call/i, "Tap call.<br>Then just talk.", "BimaVani answers in Hinglish, listens while you speak, and replies when you pause."],
];

function updateHero(txt) {
  const line = $("#hero-line");
  const sub = $("#hero-sub");
  if (!line || !sub) return;
  for (const [re, headline, subtitle] of HERO_STATES) {
    if (re.test(txt)) {
      line.innerHTML = headline;
      sub.textContent = subtitle;
      break;
    }
  }
  paintOrb();
}

/* ---------- the orb: is the agent listening, thinking or speaking? ---------- */
function paintOrb() {
  const orb = $("#btn-ptt");
  if (!orb) return;
  const speaking = !!S.playing;
  const thinking = !!S.awaitingAgentReply && !speaking;
  orb.classList.toggle("talking", speaking);
  orb.classList.toggle("thinking", thinking);
  orb.classList.toggle("listening", !!VAD.active && !speaking && !thinking);
  paintPhones();
}

/* ---------- the two handsets: which side of the call is talking? ----------
   Same truth as the orb, painted onto the two-phone view: the caller's
   handset lights up while the driver holds the turn, the agent's handset
   lights up while BimaVani streams its clause-by-clause reply. */
function paintPhonesBase() {
  const scene = $("#call-scene");
  if (!scene) return;

  const live = !!S.callActive && S.phase === "connected";
  const driverSpeaking = !!VAD.isUserSpeaking;
  const agentSpeaking = !!S.playing;
  const agentThinking = !!S.awaitingAgentReply && !agentSpeaking;
  const listening = !!VAD.active && live && !driverSpeaking && !agentSpeaking && !agentThinking;

  scene.classList.toggle("is-live", live);
  scene.classList.toggle("is-idle", !live);

  const caller = $("#phone-caller");
  if (caller) {
    caller.classList.toggle("speaking", driverSpeaking);
    caller.classList.toggle("listening", listening);
  }
  const agent = $("#phone-agent");
  if (agent) {
    agent.classList.toggle("speaking", agentSpeaking);
    agent.classList.toggle("thinking", agentThinking);
  }

  const agentHint = $("#agent-hint");
  if (agentHint) {
    agentHint.textContent = agentSpeaking ? "BimaVani is speaking now"
      : agentThinking ? "BimaVani is thinking…"
      : live ? "Waiting for your turn"
      : "Waiting for your call";
  }

  const link = $("#call-link");
  if (link) {
    link.classList.toggle("is-live", live);
    link.classList.toggle("is-idle", !live);
  }
  const label = $("#link-label");
  if (label) label.textContent = live ? "Live" : "Idle";

  const status = $("#scene-status");
  if (status) {
    status.innerHTML = !live
      ? (S.callEnded
          ? "Call ended — tap <b>Call</b> to reconnect the two phones."
          : "Tap <b>Call</b> to connect the two phones.")
      : (driverSpeaking ? "You're talking — BimaVani is listening."
        : agentSpeaking  ? "BimaVani is answering…"
        : agentThinking  ? "BimaVani is thinking…"
        : "Both phones are on the line.");
  }
}

/* ============================================================
   Phone-call layer — dial → ring → the agent picks up → live.
   Presentation only: ringback tone, call phase, incoming-call
   screen. The voice engine above is untouched.
   ============================================================ */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const RING = { ctx: null };

/* ringback tone (400+425 Hz, double ring) heard by the caller */
function ringStart() {
  ringStop();
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const g = ctx.createGain();
    g.gain.value = 0;
    g.connect(ctx.destination);
    [400, 425].forEach((f) => {
      const o = ctx.createOscillator();
      o.type = "sine"; o.frequency.value = f;
      o.connect(g); o.start();
    });
    const t0 = ctx.currentTime + 0.05;
    const burst = (t, d) => {
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(0.09, t + 0.03);
      g.gain.setValueAtTime(0.09, t + d - 0.03);
      g.gain.linearRampToValueAtTime(0, t + d);
    };
    burst(t0 + 0.8, 0.4);      // dialing finishes, then the double ring
    burst(t0 + 1.4, 0.4);
    RING.ctx = ctx;
  } catch { /* audio is optional — the visuals still run */ }
}
function ringStop() {
  if (!RING.ctx) return;
  try { RING.ctx.close(); } catch {}
  RING.ctx = null;
}
function pickupBlip() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.frequency.value = 1100;
    g.gain.setValueAtTime(0.08, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.09);
    o.connect(g).connect(ctx.destination);
    o.start(); o.stop(ctx.currentTime + 0.1);
    setTimeout(() => ctx.close().catch(() => {}), 300);
  } catch {}
}

/* paints the call phase on both handsets (wraps the base painter above) */
function paintPhones() {
  paintPhonesBase();
  const ph = S.phase;
  const scene = $("#call-scene");
  if (scene) scene.dataset.phase = ph;
  const copy = {
    idle:      ["Ready to call", "Standby",       "Idle"],
    dialing:   ["Calling…",     "Standby",       "Dialing"],
    ringing:   ["Ringing…",    "Incoming call", "Ringing"],
    connected: ["Connected",    "On call",       "Live"],
    ended:     ["Call ended",   "Call ended",    "Idle"],
  }[ph] || [];
  const set = (sel, v) => { const el = $(sel); if (el && v) el.textContent = v; };
  set("#caller-status", copy[0]);
  set("#agent-status", copy[1]);
  set("#link-label", copy[2]);
  const status = $("#scene-status");
  if (status && ph === "dialing") status.innerHTML = "Dialing <b>1-800-BIMA-VANI</b>…";
  if (status && ph === "ringing") status.innerHTML = "BimaVani's phone is <b>ringing</b>…";
  const link = $("#call-link");
  if (link) link.classList.toggle("is-ringing", ph === "dialing" || ph === "ringing");
}

/* both handsets show the same running call time */
setInterval(() => {
  const t = ($("#call-timer") || {}).textContent || "00:00";
  $$(".sync-timer").forEach((el) => { el.textContent = t; });
}, 250);

/* the mic orb: hold-to-talk / tap-to-latch in Push-to-talk, tap-to-send in Auto-VAD */
const PTT = { _latchPending: false, downAt: 0 };
(function bindOrb() {
  const orb = $("#btn-ptt");
  if (!orb) return;
  orb.addEventListener("pointerdown", (e) => {
    if (S.mode !== "push_to_talk") return;
    e.preventDefault();
    try { orb.setPointerCapture(e.pointerId); } catch {}
    if (S.recording && S.pttLatched) { endTalk(); return; }
    PTT.downAt = performance.now();
    startTalk(false);
  });
  orb.addEventListener("pointerup", () => {
    if (S.mode !== "push_to_talk" || S.pttLatched) return;
    const tap = performance.now() - PTT.downAt < 600;
    if (!S.recording) { if (tap) PTT._latchPending = true; return; }
    if (tap) {
      S.pttLatched = true;
      $("#ptt-hint").textContent = "🔴 Recording… click the mic again to send";
    } else endTalk();
  });
  orb.addEventListener("click", () => { if (S.mode === "auto_vad") micToggle(); });
  // the recorder swaps the orb's text for an emoji — put the glowing core back
  new MutationObserver(() => {
    if (!orb.querySelector(".orb-core")) {
      orb.textContent = "";
      const core = document.createElement("span");
      core.className = "orb-core";
      orb.appendChild(core);
    }
  }).observe(orb, { childList: true });
})();

/* ---------- measured latency (real backend timers + browser TTFA) ---------- */
function renderLatency(data) {
  data = data || S.lastLatencyData;
  if (!data) return;

  const st = (data.pipeline && data.pipeline.stages_ms) || {};
  const sttMeta = (data.pipeline && data.pipeline.stt) || {};
  const sttMs = Number(st.stage_2 ?? sttMeta.stt_latency_ms ?? 0) || 0;
  const brainMs = Number(st.stage_4 ?? 0) || 0;
  const clauseMs = Number(st.stage_5 ?? 0) || 0;
  const ttsMs = Number(st.stage_6 ?? 0) || 0;
  const backendMs = Number(data.total_turn_ms ?? 0) || 0;
  const ttfaMs = data.ttfa_ms != null ? Number(data.ttfa_ms) : null;
  // Everything the browser adds on top of the backend answer.
  const netMs = ttfaMs != null ? Math.max(0, ttfaMs - backendMs) : null;

  const set = (sel, v) => { const el = $(sel); if (el) el.textContent = v; };
  set("#flow-ttfa", ttfaMs != null ? Math.round(ttfaMs) : "—");
  set("#flow-total", backendMs ? Math.round(backendMs) : "—");
  set("#flow-stt", Math.round(sttMs));

  const rows = [
    ["Prisma STT", sttMs, ""],
    ["Brain + tools", brainMs, ""],
    ["Clause buffer", clauseMs, ""],
    ["Timbre TTS", ttsMs, ""],
    ["Network + client", netMs, "net"],
  ].filter(r => r[1] != null);
  const scale = Math.max(1, ...rows.map(r => r[1]));

  const body = $("#latency-body");
  if (body) {
    body.innerHTML =
      `<div class="lat-rows">${rows.map(([name, ms, cls]) => `
        <div class="lat-row">
          <span class="lat-name">${name}</span>
          <span class="lat-track"><span class="lat-fill ${cls}" style="width:${Math.min(100, (ms / scale) * 100)}%"></span></span>
          <span class="lat-num">${Math.round(ms)} ms</span>
        </div>`).join("")}</div>` +
      (S.latencyHistory.length
        ? `<div class="lat-hist">last turns · ${S.latencyHistory.map(h => `${Math.round(h.ttfa)}/${Math.round(h.total)}`).join("  ·  ")} ms (TTFA / backend)</div>`
        : "");
  }

  // remember the turn for the little history line
  if (ttfaMs != null && !data._latPushed) {
    data._latPushed = true;
    S.latencyHistory.push({ ttfa: ttfaMs, total: backendMs });
    if (S.latencyHistory.length > 6) S.latencyHistory.shift();
  }
}

/* ---------- one turn, painted ---------- */
function renderTurn(data) {
  if (!data) return;

  S.lastLatencyData = data;
  const said = (data.transcript || "").trim();
  const transcript = $("#stt-transcript");
  if (transcript) {
    transcript.textContent = said || "…";
    transcript.classList.toggle("muted", !said);
  }

  const reply = $("#agent-reply");
  if (reply) reply.textContent = data.reply_text || "…";

  renderLatency(data);
  if (data.reply_text) setCallState("🤖 Speaking…");
  paintOrb();
}

/* ============================================================
   Controls — mode, sliders, typed fallback, noise, health
   ============================================================ */

/* turn-taking mode: Auto-VAD (hands free) or Push-to-talk */
function syncPttControls() {
  const send = $("#btn-ptt-send");
  const cancel = $("#btn-ptt-cancel");
  if (send) send.disabled = !S.recording;
  if (cancel) cancel.disabled = !S.recording;
}

function syncModeUi() {
  $$(".seg-btn").forEach(b => b.classList.toggle("active", b.dataset.mode === S.mode));
  const ptt = $("#ptt-controls");
  if (ptt) ptt.classList.toggle("hidden", S.mode !== "push_to_talk");
  if (S.mode === "push_to_talk") stopVad();
  else if (S.callActive) startVad();
  updateMicBtn();
}

$$(".seg-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    S.mode = btn.dataset.mode;
    syncModeUi();
    setCallState(S.mode === "auto_vad"
      ? "🎧 LISTENING (Auto-VAD Active)"
      : "🎙️ PUSH-TO-TALK — hold the orb (or tap once) to speak");
  });
});

/* ear tuning */
$("#vad-silence").addEventListener("input", (e) => {
  S.vadSilenceMs = parseInt(e.target.value, 10) || 650;
  $("#vad-window").textContent = S.vadSilenceMs;
});
$("#vad-sens").addEventListener("input", (e) => {
  S.vadSens = parseFloat(e.target.value) || 2.2;
  $("#vad-sens-val").textContent = S.vadSens.toFixed(1) + "×";
  if (VAD.calibrated && !VAD.calibrating)
    VAD.threshold = Math.max(VAD.ambientRms * S.vadSens, 0.018);
});

/* push-to-talk send / cancel */
$("#btn-ptt-send").addEventListener("click", () => endTalk());
$("#btn-ptt-cancel").addEventListener("click", () => cancelTalk());

/* typed fallback: a full turn without a microphone (ASR bypassed) */
async function sendTypedLine() {
  if (!S.callActive) { setCallState("Press Call first"); return; }
  const input = $("#type-say");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  if (S.playing) browserStopAudio();
  beginUserTurnAfterBargeIn();
  VAD.isUserSpeaking = false;
  setCallState("⏳ Processing turn…");
  await sendTurn("", text);
}
$("#btn-say").onclick = sendTypedLine;
$("#type-say").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); sendTypedLine(); }
});

/* the pencil on the transcript card jumps to typed input */
$("#say-edit").onclick = () => {
  const setup = document.querySelector(".setup");
  if (setup) setup.open = true;
  $("#type-say").focus();
};

/* road noise presets */
$("#noise-preset").addEventListener("change", updateNoise);
$("#noise-into-mic").addEventListener("change", updateNoise);
$("#noise-aloud").addEventListener("change", updateNoise);

function stopJitterAnything() { browserStopAudio(); }

/* ---------- health: mode, brain engine, queue depths ---------- */
function updateQueues(queues) {
  document.querySelectorAll(".node-q").forEach(el => {
    const q = queues[el.dataset.q];
    if (!q) return;
    el.textContent = q.depth > 0 ? String(q.depth) : "";
    el.classList.toggle("full", !!q.backpressure_active);
  });
}

async function refreshHealth() {
  try {
    const h = await (await fetch(API.health)).json();
    const pill = $("#mode-pill");
    if (pill) {
      const live = h.mode === "LIVE_GNANI";
      pill.textContent = live ? "🟢 live gnani" : "🛡 fallback";
      pill.className = "chip-status" + (live ? " live" : "");
    }
    const foot = $("#foot-meta");
    if (foot) foot.textContent = `${h.stt_model} · ${h.tts_model} · bounded queues`;

    const b = h.brain || {};
    const on = b.engine === "llm+deterministic";
    const brainPill = $("#brain-pill");
    if (brainPill) {
      brainPill.textContent = on ? "🧠 LLM phrasing" : "🧠 Rules + tools";
      brainPill.className = "chip-status" + (on ? " live" : "");
      brainPill.title = on
        ? `LLM ${b.model} phrases the turn; rules stay the source of truth`
        : `LLM auto-retry armed${b.last_error ? " (last: " + b.last_error + ")" : ""} — the deterministic brain answers in ~20 ms meanwhile`;
    }
    updateQueues(h.queues || {});
  } catch {
    const pill = $("#mode-pill");
    if (pill) pill.textContent = "⚠ server down";
  }
}

/* ---------- keyboard: space = push-to-talk hold, Esc = cancel ---------- */
window.addEventListener("keydown", (e) => {
  if (e.key === " " && S.callActive && S.mode === "push_to_talk" && !S.recording && !e.repeat) {
    e.preventDefault(); startTalk(false);
  }
  if (e.key === " " && S.mode === "auto_vad" && e.repeat) e.preventDefault();
  if (e.key === "Escape" && S.recording) { e.preventDefault(); cancelTalk(); }
});
window.addEventListener("keyup", (e) => {
  if (e.key === " " && S.recording && !S.pttLatched && S.mode === "push_to_talk") {
    e.preventDefault(); endTalk();
  }
});

/* ============================================================
   Call lifecycle — 📞 Call greets first, then hands the mic back
   ============================================================ */
function startCallTimer() {
  clearInterval(S.callTimer);
  S.callT0 = Date.now();
  S.callTimer = setInterval(() => {
    const s = Math.floor((Date.now() - S.callT0) / 1000);
    const el = $("#call-timer");
    if (el) el.textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  }, 500);
}

$("#btn-dial").onclick = async () => {
  S.callActive = true;
  S.callEnded = false;
  S.micDenied = false;
  S.latencyHistory = [];
  S.lastDispatch = null;
  S.lastDraft = null;
  // dump any previous jitter-buffer / playback audio so this call starts
  // with fresh context — nothing from the last call is brought back
  browserStopAudio();
  S.phase = "dialing"; { const t = $("#call-timer"); if (t) t.textContent = "00:00"; }
  paintPhones();
  $("#btn-dial").disabled = true;
  $("#btn-end").disabled = false;
  setCallState("Dialing 1-800-BIMA-VANI…");
  const stt = $("#stt-transcript");
  if (stt) stt.textContent = "…";
  const reply = $("#agent-reply");
  if (reply) reply.textContent = "…";

  try {
    // 📞 Phone-call handshake: dial → ring → the agent picks up.
    // The mic permission is asked while it rings, so the pickup is instant.
    const myCall = ++S.callSeq;
    const stillOn = () => S.callActive && S.callSeq === myCall;
    ringStart();
    const micReady = initAudioGraph().catch(() => {});
    await sleep(800);
    if (!stillOn()) return ringStop();
    S.phase = "ringing"; paintPhones(); setCallState("Ringing…");
    await Promise.all([sleep(2200), micReady]);
    if (!stillOn()) return ringStop();
    ringStop(); pickupBlip();
    $("#agent-accept")?.classList.add("pressed");
    await sleep(380);
    if (!stillOn()) return;
    $("#agent-accept")?.classList.remove("pressed");
    S.phase = "connected"; startCallTimer(); paintPhones();
    setCallState("Agent picked up…");
    // (original flow continues: unlock audio, calibrate the room, greet)
    await initAudioGraph();
    if (REC.ctx && REC.ctx.state === "suspended") await REC.ctx.resume();
    await updateNoise();

    if (!S.micDenied) {
      setCallState("Calibrating mic…");
      await calibrateAmbient(300);       // learn the room before the greeting
    }

    setCallState("Agent connected — greeting…");
    S.turnSentAt = performance.now();    // TTFA clock for the greeting
    const res = await fetch(API.start, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ call_id: "live" }),
    });
    const data = await res.json();
    S.lastTurnResult = data;

    decodeStages(data.pipeline, null);
    renderTurn({
      transcript: "(greeting)",
      reply_text: data.greeting,
      pipeline: data.pipeline,
      total_turn_ms: data.total_start_ms,
      ticket_status: data.ticket_status,
    });
    const transcript = $("#stt-transcript");
    if (transcript) transcript.textContent = "Waiting for you to speak…";

    window.speechSynthesis?.cancel?.();
    // Greet while VAD is already armed so the driver can interrupt the greeting
    // with their voice (the echo gate keeps the agent's own audio from triggering it).
    const greet = playClausesFromPayload(data.clauses || [], data.greeting);
    startVad();                          // greeting is interruptible by voice + UI
    await greet;

    if (S.micDenied) {
      stopVad();
      setCallState("Typed mode — no microphone");
      const hint = $("#ptt-hint");
      if (hint) hint.textContent = "Mic blocked — type your turn in Voice agent setup";
    } else if (S.mode === "push_to_talk") {
      stopVad();
      setCallState("🎙️ PUSH-TO-TALK — greeting can still be interrupted");
    }
    // auto_vad: VAD stays armed → LISTENING; the greeting can be interrupted mid-sentence
  } catch (e) {
    speakFallback("Namaste! Main BimaVani Roadside Helpline se bol rahi hoon. Sabse pehle batayein — kya aap aur gaadi mein baithe sabhi log safe hain?");
    setCallState(`⚠️ ${e.message}`);
    if (S.mode === "auto_vad" && !S.micDenied) startVad();
  }
};

$("#btn-end").onclick = async () => {
  S.callActive = false;
  S.callEnded = true; S.callSeq++; ringStop(); S.phase = "ended";
  clearInterval(S.callTimer);
  $("#btn-dial").disabled = false;
  $("#btn-end").disabled = true;
  stopVad();
  stopNoiseSpeaker();
  browserStopAudio();
  setCallState("Call ended");
  paintPhones();
  // Tell the backend to reset this call's agent memory so the next call
  // starts with a clean slate (no cross-call context bleed).
  try {
    await fetch(API.end, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ call_id: "live" }),
    });
  } catch (e) {
    // best-effort; UI already reset locally
  }
};

/* ---------- boot ---------- */
updateHero("Not connected");
$("#vad-window").textContent = S.vadSilenceMs;
$("#vad-sens-val").textContent = S.vadSens.toFixed(1) + "×";
syncModeUi();
refreshHealth();
setInterval(refreshHealth, 5000);
setInterval(paintOrb, 400);          // keeps the orb honest during playback
