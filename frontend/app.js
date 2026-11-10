/* ============================================================
   BimaVani — frontend/app.js
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

function escHtml(v) {
  return String(v ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function makeCallId() {
  try {
    if (window.crypto && crypto.randomUUID) return "call-" + crypto.randomUUID().slice(0, 8);
  } catch {}
  return "call-" + Math.random().toString(36).slice(2, 10);
}

/* ---------------- live pipeline strip (bottom band) ---------------- */
const stageEls = [...document.querySelectorAll(".node")]
  .sort((a, b) => Number(a.dataset.stage) - Number(b.dataset.stage));

function litStage(i, ms, opts = {}) {
  const el = stageEls[i];
  if (!el) return;
  el.classList.add("hot");
  el.querySelector(".node-ms").textContent = ms != null ? `${Math.round(ms)} ms` : "live";
  setTimeout(() => el.classList.remove("hot"), opts.hold ? 1400 : 520);
}

function decodeStages(pipeline, bargeFlushMs) {
  const s = (pipeline && pipeline.stages_ms) || {};
  litStage(0, s.stage_1 ?? 1.0);
  if (s.stage_2 !== undefined) litStage(1, s.stage_2);
  litStage(2, s.stage_3 ?? 0.2);
  if (s.stage_4 !== undefined) litStage(3, s.stage_4);
  litStage(4, s.stage_5 ?? 0.2);
  if (s.stage_6 !== undefined) litStage(5, s.stage_6);
  litStage(6, s.stage_7 ?? 120);
  if (bargeFlushMs != null) litStage(7, bargeFlushMs, { hold: true });
  else if (s.stage_8) litStage(7, s.stage_8);
}

/* ---------------- state ---------------- */
const S = {
  callId: makeCallId(),
  callActive: false, callT0: 0, callTimer: null,
  callEnded: false, phase: "idle", callSeq: 0,
  recording: false, talkStart: 0,
  mode: "auto_vad",
  vadSilenceMs: 650,
  vadSens: 2.2,
  playing: false, lastTurnResult: null, awaitingAgentReply: false,
  micDenied: false,
  pttLatched: false, pttTimer: null,
  latencyHistory: [],
  turnSentAt: 0,
  lastLatencyData: null,
  lastDispatch: null, lastDraft: null,
  transcriptItems: [],
  lastBargeNotifiedAt: 0,
  languagePref: "indic-auto",
  detectedLang: "hi-IN",
};

const LANG_LABELS = {
  "hi-IN": "Hinglish (hi-IN)",
  "ta-IN": "தமிழ் (ta-IN)",
  "te-IN": "తెలుగు (te-IN)",
  "kn-IN": "ಕನ್ನಡ (kn-IN)",
  "ml-IN": "മലയാളം (ml-IN)",
  "bn-IN": "বাংলা (bn-IN)",
  "mr-IN": "मराठी (mr-IN)",
  "gu-IN": "ગુજરાતી (gu-IN)",
  "pa-IN": "ਪੰਜਾਬੀ (pa-IN)",
  "en-IN": "English (en-IN)",
};

function explicitSelectedLang() {
  return (S.languagePref && S.languagePref !== "indic-auto") ? S.languagePref : "";
}

function normalizeFrontendLang(lang) {
  const v = (lang || "").trim().toLowerCase();
  if (!v || v === "auto" || v === "indic-auto") return "";
  for (const code of Object.keys(LANG_LABELS)) {
    if (v === code.toLowerCase() || v.startsWith(code.slice(0, 2).toLowerCase() + "-") || v === code.slice(0, 2).toLowerCase()) {
      return code;
    }
  }
  return "";
}

function detectTextLanguage(text, hint) {
  const t = text || "";
  if (/[\u0B80-\u0BFF]/.test(t)) return "ta-IN";
  if (/[\u0C00-\u0C7F]/.test(t)) return "te-IN";
  if (/[\u0C80-\u0CFF]/.test(t)) return "kn-IN";
  if (/[\u0D00-\u0D7F]/.test(t)) return "ml-IN";
  if (/[\u0980-\u09FF]/.test(t)) return "bn-IN";
  if (/[\u0A80-\u0AFF]/.test(t)) return "gu-IN";
  if (/[\u0A00-\u0A7F]/.test(t)) return "pa-IN";
  if (/[\u0901-\u0963\u0966-\u097F]/.test(t)) {
    if (/[ळऱ]|(?:मला|माझी|आहे|नाही|जखम|महामार्ग|थांबा|बदला)/.test(t)) return "mr-IN";
    return "hi-IN";
  }
  const h = normalizeFrontendLang(hint);
  if (h) return h;
  if (/\b(?:my car|we are safe|nobody is injured|no one is injured|please send|thank you|shall i confirm|radiator damage)\b/i.test(t)
      && !/\b(?:hai|hain|nahi|chot|gaadi|aap|kya|ruko|haan)\b/i.test(t)) {
    return "en-IN";
  }
  return "hi-IN";
}

const WS = {
  sock: null,
  open: false,
  callId: "",
  seq: 0,
  pcmCarry: [],
  pendingResolve: null,
  pendingTurnResolve: null,
  streamComplete: false,
  fallbackQueue: [],
  fallbackLang: "hi-IN",
};

function setWsPill(txt, live = false) {
  const el = $("#ws-pill");
  if (!el) return;
  el.textContent = txt;
  el.className = "chip-status" + (live ? " live" : "");
}

function updateJitterPill(ij, oj) {
  const el = $("#jitter-pill");
  if (!el) return;
  const inEst = ij && ij.adaptive_target_ms != null ? `${ij.adaptive_target_ms}ms` : (ij && ij.jitter_ms_est != null ? `${ij.jitter_ms_est}ms` : "60ms");
  const reord = ij && ij.out_of_order ? ` · ${ij.out_of_order} reord` : "";
  const plc = ij && ij.plc_frames ? ` · ${ij.plc_frames} PLC` : "";
  const snr = ij && ij.vad && ij.vad.estimated_snr_db > 0 ? ` · SNR ${ij.vad.estimated_snr_db}dB` : "";
  const outPre = oj && oj.prebuffer_ms != null ? `${oj.prebuffer_ms}ms` : "120ms";
  el.textContent = `In-Jitter: ${inEst}${reord}${plc}${snr} · Out: ${outPre}`;
}

function grabRecentMicPcmB64(maxMs = 450) {
  if (!REC.frames.length || !REC.srcRate) return "";
  const cutoff = performance.now() - maxMs;
  const slice = REC.frames.filter(f => f.t >= cutoff);
  const src = slice.length ? slice : REC.frames.slice(-6);
  const merged = mergeChunks(src.map(f => f.f));
  if (!merged.length) return "";
  const downs = downsampleTo16k(merged, REC.srcRate);
  const i16 = floatTo16PCM(downs);
  return btoa64(new Uint8Array(i16.buffer, i16.byteOffset, i16.byteLength));
}

function pushWsAudioChunk(f32) {
  if (!WS.open || !WS.sock || WS.sock.readyState !== 1) return;
  if (WS.sock.bufferedAmount > 65536) return; // client-side backpressure guard
  const downs = downsampleTo16k(f32, REC.srcRate || 48000);
  const i16 = floatTo16PCM(downs);
  for (let i = 0; i < i16.length; i++) WS.pcmCarry.push(i16[i]);
  while (WS.pcmCarry.length >= 320) {
    const frame = new Int16Array(WS.pcmCarry.splice(0, 320));
    const pcmB64 = btoa64(new Uint8Array(frame.buffer));
    try {
      WS.sock.send(JSON.stringify({
        type: "audio_frame",
        seq: WS.seq++,
        ts_ms: performance.now(),
        pcm_b64: pcmB64,
      }));
    } catch {
      break;
    }
  }
}

/* ============================================================
   Stage 1: 16kHz Mono 16-bit Linear PCM WAV Recorder + Noise Mixer
   ============================================================ */
const REC = { ctx: null, stream: null, proc: null, sink: null, frames: [], srcRate: 48000, chunks: [] };

function floatTo16PCM(f32) {
  const out = new Int16Array(f32.length);
  for (let i = 0; i < f32.length; i++) {
    const v = Math.max(-1, Math.min(1, f32[i]));
    out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  return out;
}

function interleaveWav(samples, sampleRate) {
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
  if (REC.ctx && (REC.stream || S.micDenied)) return;
  if (!REC.ctx) {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    try {
      REC.ctx = new AudioCtx({ sampleRate: 48000 });
    } catch {
      REC.ctx = new AudioCtx();
    }
  }
  if (REC.ctx.state === "suspended") await REC.ctx.resume();
  REC.srcRate = REC.ctx.sampleRate;

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    S.micDenied = true;
    setCallState("⌨️ No browser mic API — typed & quick-demo mode active");
    $("#ptt-hint").textContent = "Mic unavailable — type or click a scenario";
    return;
  }

  try {
    REC.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: false,
        autoGainControl: false,
        channelCount: 1,
      },
    });
    S.micDenied = false;
  } catch (e) {
    S.micDenied = true;
    REC.stream = null;
    VAD.analyser = null;
    setCallState(`⌨️ Mic blocked (${e.name || e.message}) — typed mode`);
    $("#ptt-hint").textContent = "Mic unavailable — type or click a scenario";
    return;
  }

  const src = REC.ctx.createMediaStreamSource(REC.stream);

  // RAW clean mic analyser BEFORE noise mixer
  VAD.analyser = REC.ctx.createAnalyser();
  VAD.analyser.fftSize = 1024;
  VAD.buf = new Float32Array(VAD.analyser.fftSize);
  src.connect(VAD.analyser);

  REC.sink = REC.ctx.createScriptProcessor(4096, 1, 1);
  REC.sink.onaudioprocess = (e) => {
    const f32 = e.inputBuffer.getChannelData(0);
    const m = noiseAmount($("#noise-preset").value) * ($("#noise-into-mic").checked ? 1 : 0);
    if (S.callActive) {
      REC.frames.push({ t: performance.now(), f: new Float32Array(f32), mixed: m });
      if (REC.frames.length > 220) REC.frames.splice(0, REC.frames.length - 220);
      if (S.recording) {
        REC.chunks = REC.chunks || [];
        REC.chunks.push(new Float32Array(f32));
      }
      pushWsAudioChunk(f32);
    }
    drawWave(f32);
  };

  try {
    const workletSrc = `
      class NoiseMixer extends AudioWorkletProcessor {
        constructor() {
          super();
          this.mixNoise = 0;
          this.keep = false;
          this.ph = 0; this.burst = 0;
          this.port.onmessage = (e) => {
            if (e.data.cmd === "mix") this.mixNoise = e.data.amount;
            if (e.data.cmd === "mode") this.keep = e.data.keep;
          };
        }
        process(inputs, outputs) {
          const inp = inputs[0][0], out = outputs[0][0];
          if (!inp) return true;
          const amt = this.mixNoise, gsm = this.keep;
          for (let i = 0; i < inp.length; i++) {
            let v = inp[i];
            const t = i / 128 + this.ph;
            let noise = 0.22 * Math.sin(2 * Math.PI * 3 * t) + 0.08 * (Math.random() * 2 - 1);
            if (this.burst <= 0 && Math.random() < 0.0007) this.burst = 222;
            if (this.burst > 0) { this.burst--; noise += 0.30 * Math.sin(2 * Math.PI * 12 * t) + 0.18 * Math.sin(2 * Math.PI * 19 * t); }
            v = v * (1 - amt * (gsm ? 0.75 : 0.35)) + noise * amt;
            if (gsm) {
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
    const blobUrl = URL.createObjectURL(new Blob([workletSrc], { type: "application/javascript" }));
    await REC.ctx.audioWorklet.addModule(blobUrl);
    URL.revokeObjectURL(blobUrl);
    REC.proc = new AudioWorkletNode(REC.ctx, "noise-mixer");
    REC.proc.port.postMessage({ cmd: "mix", amount: 0 });
    src.connect(REC.proc);
    REC.proc.connect(REC.sink);
  } catch (err) {
    console.warn("Noise mixer unavailable — recording raw mic instead.", err);
    REC.proc = null;
    src.connect(REC.sink);
  }
  // Route sink through silent zero-gain node so ScriptProcessor runs without speaker echo
  const zeroGain = REC.ctx.createGain();
  zeroGain.gain.value = 0;
  REC.sink.connect(zeroGain).connect(REC.ctx.destination);
}

function noiseAmount(preset) {
  return { clean: 0.0, mandi: 0.178, highway: 0.316, gsm: 0.5 }[preset] ?? 0;
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
  if (S.callActive && aloud && preset !== "clean") startNoiseSpeaker(preset);
}

let noiseGainNode = null;
function startNoiseSpeaker(preset) {
  const ctx = REC.ctx || new (window.AudioContext || window.webkitAudioContext)();
  if (!REC.ctx) REC.ctx = ctx;
  noiseGainNode = ctx.createGain();
  noiseGainNode.gain.value = 0.35;
  const b = ctx.createBufferSource();
  const len = ctx.sampleRate * 2;
  const buf = ctx.createBuffer(1, len, ctx.sampleRate);
  const d = buf.getChannelData(0);
  let burst = 0;
  for (let i = 0; i < len; i++) {
    let v = 0.10 * (Math.random() * 2 - 1);
    const t = i / ctx.sampleRate;
    if (preset === "mandi") v += 0.05 * Math.sin(2 * Math.PI * 55 * t) + 0.03 * (Math.random() - 0.5);
    if (preset === "highway") {
      if (burst <= 0 && Math.random() < 0.0009) burst = 14000;
      if (burst > 0) { burst--; v += 0.18 * Math.sin(2 * Math.PI * (640 + 30 * Math.sin(2 * Math.PI * 3 * t)) * t); }
      v += 0.09 * Math.sin(2 * Math.PI * 92 * t);
    }
    if (preset === "gsm") v = v * 0.6 + (i % 48 < 1 ? 0.2 : 0);
    d[i] = Math.max(-1, Math.min(1, v * (preset === "gsm" ? 2.5 : 1)));
  }
  b.buffer = buf; b.loop = true;
  b.connect(noiseGainNode).connect(ctx.destination);
  b.start();
  noiseGainNode._src = b;
}

function stopNoiseSpeaker() {
  if (noiseGainNode && noiseGainNode._src) {
    try { noiseGainNode._src.stop(); } catch {}
    try { noiseGainNode.disconnect(); } catch {}
    noiseGainNode = null;
  }
}

/* ============================================================
   Auto-VAD — raw mic analyser, calibration, barge-in & end-of-turn
   ============================================================ */
const VAD = {
  analyser: null, buf: null,
  active: false, awaiting: false,
  calibrating: false, calibrated: false,
  ambientRms: 0, threshold: 0.018,
  lastFrameAt: 0, lastUiAt: 0,
  speechMs: 0, silMs: 0,
  isUserSpeaking: false,
  speechStartT: 0, preRollMs: 250,
};

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
    await sleep(20);
  }
  VAD.ambientRms = sum / Math.max(1, n);
  VAD.threshold = Math.max(VAD.ambientRms * (S.vadSens || 2.2), 0.018);
  VAD.calibrating = false;
  VAD.calibrated = true;
  const el = $("#vad-live");
  if (el) el.textContent = `Ambient ${VAD.ambientRms.toFixed(4)} → thr ${VAD.threshold.toFixed(3)} (raw mic)`;
}

function startVad() {
  if (!VAD.analyser) return;
  VAD.active = true;
  VAD.lastFrameAt = 0;
  VAD.speechMs = 0; VAD.silMs = 0; VAD.isUserSpeaking = false;
  setCallState(VAD.calibrated
    ? `🎧 LISTENING (Auto-VAD Active) — boliye, ${S.vadSilenceMs}ms rukne par send hoga`
    : "🎧 LISTENING (Auto-VAD Active) — calibrating mic…");
  updateMicBtn();
}

function stopVad() {
  VAD.active = false;
  VAD.isUserSpeaking = false;
  updateMicBtn();
}

function setCallState(txt) {
  const el = $("#call-state");
  if (el) el.textContent = txt;
  updateHero(txt);
}

function updateMicBtn() {
  const b = $("#btn-ptt");
  if (!b) return;
  const activeSpeak = !!VAD.isUserSpeaking || !!S.recording;
  b.classList.toggle("talking", activeSpeak);
  b.classList.toggle("listening", (!!VAD.active || (S.callActive && S.mode === "push_to_talk")) && !activeSpeak);
  b.classList.toggle("thinking", !!S.awaitingAgentReply);
  const hint = $("#ptt-hint");
  if (!hint) return;
  if (!S.callActive) hint.textContent = "Press Call to start";
  else if (S.micDenied) hint.textContent = "Mic unavailable — type or click a scenario";
  else if (S.recording) hint.textContent = S.pttLatched ? "Recording… click mic again to send" : "Recording… release to send";
  else if (VAD.isUserSpeaking) hint.textContent = "I'm hearing you…";
  else if (S.mode === "auto_vad") hint.textContent = "Listening — speak naturally, pause to send";
  else hint.textContent = "Hold to talk · or tap once to latch";
}

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

  if (now - VAD.lastUiAt > 120) {
    VAD.lastUiAt = now;
    const el = $("#vad-live");
    if (el) {
      el.textContent = `VAD ${rms.toFixed(4)} / thr ${VAD.threshold.toFixed(3)} · `
        + (VAD.isUserSpeaking ? "SPEECH" : "silence");
    }
    const METER_MAX = 0.30;
    const bar = $("#vad-bar");
    if (bar) {
      bar.style.width = Math.min(100, (rms / METER_MAX) * 100).toFixed(1) + "%";
      bar.classList.toggle("hot", VAD.isUserSpeaking);
    }
    const mark = $("#vad-thr-mark");
    if (mark) mark.style.left = Math.min(100, (VAD.threshold / METER_MAX) * 100).toFixed(1) + "%";
  }

  if (!VAD.calibrated) {
    if (!VAD.calibrating) calibrateAmbient();
    return;
  }

  const playing = S.playing;
  const gate = playing ? VAD.threshold * 2.5 : VAD.threshold;

  if (rms >= gate) {
    VAD.speechMs += dt;
    VAD.silMs = 0;
    if (!VAD.isUserSpeaking && VAD.speechMs >= 150) {
      VAD.isUserSpeaking = true;
      VAD.speechStartT = now - VAD.speechMs - VAD.preRollMs;
      updateMicBtn();
      if (S.playing || S.awaitingAgentReply) {
        const t0 = performance.now();
        const spokenSnapshot = $("#agent-reply")?.textContent || "";
        const pcmSnap = grabRecentMicPcmB64(450);
        browserStopAudio();
        beginUserTurnAfterBargeIn(true);
        litStage(7, performance.now() - t0, { hold: true });
        setCallState("⚡ BARGE-IN — main sun rahi hoon, boliye.");
        notifyAutoVadBargeIn(spokenSnapshot, pcmSnap, "auto_vad_mic");
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

function buildUserWavNow() {
  const cut = VAD.speechStartT - 200;
  const idx = REC.frames.findIndex(f => f.t >= cut);
  const slice = idx >= 0 ? REC.frames.slice(idx) : REC.frames.slice(-20);
  if (!slice.length) return null;
  const merged = mergeChunks(slice.map(f => f.f));
  if (merged.length < REC.srcRate * 0.25) return null;
  const downs = downsampleTo16k(merged, REC.srcRate);
  return interleaveWav(floatTo16PCM(downs), 16000);
}

async function endTurnAuto() {
  if (!S.callActive || VAD.awaiting) return;
  const wav = buildUserWavNow();
  if (!wav) {
    setCallState("🎧 LISTENING (Auto-VAD Active) — awaaz nahi mili, dobara boliye.");
    return;
  }
  setCallState("⏳ Processing turn… (Prisma STT → Brain → Timbre)");
  litStage(0, null, { hold: true });
  await sendTurn(btoa64(wav));
}

function promptDialFirst() {
  setCallState("Press 📞 Call first to start a helpline session");
  const btn = $("#btn-dial");
  if (btn) {
    btn.classList.remove("pulse");
    void btn.offsetWidth;
    btn.classList.add("pulse");
  }
}

async function micToggle() {
  if (!S.callActive) { promptDialFirst(); return; }
  if (S.micDenied) {
    setCallState("⌨️ Mic blocked — type your turn or click a scenario chip");
    $("#type-say")?.focus();
    return;
  }
  if (!REC.ctx) await initAudioGraph();
  if (!VAD.isUserSpeaking) {
    VAD.isUserSpeaking = true;
    VAD.speechStartT = performance.now() - VAD.preRollMs;
    VAD.speechMs = 0; VAD.silMs = 0;
    if (S.playing || S.awaitingAgentReply) {
      const t0 = performance.now();
      const spokenSnapshot = $("#agent-reply")?.textContent || "";
      const pcmSnap = grabRecentMicPcmB64(450);
      browserStopAudio();
      beginUserTurnAfterBargeIn(true);
      litStage(7, performance.now() - t0, { hold: true });
      notifyAutoVadBargeIn(spokenSnapshot, pcmSnap, "mic_toggle");
    }
    updateMicBtn();
    setCallState("🔴 Mic locked ON — boliye, dubara click kijiye to send hoga.");
  } else {
    VAD.isUserSpeaking = false;
    VAD.silMs = 0; VAD.speechMs = 0;
    endTurnAuto();
  }
}

/* ---------------- Push-to-talk recorder ---------------- */
function pttSetStatus(txt) {
  const el = $("#ptt-live");
  if (el) el.textContent = txt;
}

function pttTick() {
  if (!S.recording) return;
  const ms = performance.now() - S.talkStart;
  const last = (REC.chunks || [])[REC.chunks.length - 1];
  let peak = 0;
  if (last) for (let i = 0; i < last.length; i++) {
    const a = Math.abs(last[i]);
    if (a > peak) peak = a;
  }
  const bars = "█".repeat(Math.min(16, Math.round(peak * 36)));
  pttSetStatus(`🔴 ${(ms / 1000).toFixed(1)}s · ${bars || "·"}`);
  const timer = $("#ptt-timer");
  if (timer) timer.textContent = (ms / 1000).toFixed(1) + "s";
  S.pttTimer = setTimeout(pttTick, 120);
}

async function startTalk(latched = false) {
  if (!S.callActive) { promptDialFirst(); return; }
  if (S.micDenied) { setCallState("⌨️ Mic blocked — type below"); $("#type-say")?.focus(); return; }
  if (S.recording || S.awaitingAgentReply) return;
  PTT._latchPending = false;
  try {
    if (!REC.ctx) await initAudioGraph();
    if (REC.ctx && REC.ctx.state === "suspended") await REC.ctx.resume();
    await updateNoise();
  } catch (e) {
    setCallState(`⚠️ ${e.message || e.name} — allow the microphone and try again.`);
    return;
  }
  if (S.playing) {
    const t0 = performance.now();
    browserStopAudio();
    litStage(7, performance.now() - t0, { hold: true });
  }
  stopVad();

  S.recording = true; S.talkStart = performance.now(); S.pttLatched = latched;
  REC.chunks = [];
  if (PTT._latchPending) {
    PTT._latchPending = false;
    S.pttLatched = true;
  }
  setCallState("🔴 PTT RECORDING — boliye, phir release/click kijiye");
  litStage(0, null, { hold: true });
  syncPttControls();
  updateMicBtn();
  pttTick();
}

function cancelTalk() {
  if (!S.recording) return;
  S.recording = false; S.pttLatched = false;
  clearTimeout(S.pttTimer);
  REC.chunks = [];
  pttSetStatus("PTT: cancelled");
  setCallState("🎙️ PUSH-TO-TALK — hold the orb to reply");
  syncPttControls();
  updateMicBtn();
}

async function endTalk() {
  if (!S.recording) return;
  S.recording = false; S.pttLatched = false;
  clearTimeout(S.pttTimer);
  const merged = mergeChunks((REC.chunks || []).map(f => f.f || f));
  const recMs = performance.now() - S.talkStart;
  REC.chunks = [];
  updateMicBtn();

  if (recMs < 350 || merged.length < (REC.srcRate || 48000) * 0.3) {
    pttSetStatus(`PTT: too short (${(recMs / 1000).toFixed(1)}s)`);
    setCallState("🎙️ PUSH-TO-TALK — hold ~1s and speak");
    syncPttControls();
    return;
  }
  const downs = downsampleTo16k(merged, REC.srcRate || 48000);
  const wav = interleaveWav(floatTo16PCM(downs), 16000);
  pttSetStatus(`PTT: ${(wav.length / 1024).toFixed(1)} KB · ${(recMs / 1000).toFixed(1)}s`);
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
  for (let i = 0; i < u8.length; i += 0x8000)
    bin += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
  return btoa(bin);
}

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
   ============================================================ */
const JITTER = { ctx: null, queue: [], prebufferMs: 120, token: 0 };

function stripDataUri(b64) {
  if (b64.startsWith("data:")) {
    const comma = b64.indexOf(",");
    return b64.slice(comma + 1);
  }
  return b64;
}

function pickSpeechVoice(lang) {
  try {
    const voices = window.speechSynthesis?.getVoices?.() || [];
    const prefix = String(lang || "hi-IN").toLowerCase().slice(0, 2);
    return voices.find(v => (v.lang || "").toLowerCase() === lang.toLowerCase())
      || voices.find(v => (v.lang || "").toLowerCase().startsWith(prefix))
      || null;
  } catch { return null; }
}

function speakFallbackAsync(text, lang) {
  return new Promise((resolve) => {
    try {
      if (!window.speechSynthesis || !text) return resolve(false);
      const resolved = detectTextLanguage(text, lang || S.detectedLang);
      const u = new SpeechSynthesisUtterance(text);
      const v = pickSpeechVoice(resolved);
      if (v) u.voice = v;
      u.lang = resolved; u.rate = 0.98; u.pitch = 1.05;
      u.onend = () => resolve(true);
      u.onerror = () => resolve(false);
      speechSynthesis.speak(u);
      setTimeout(() => resolve(true), Math.max(1800, text.length * 85));
    } catch { resolve(false); }
  });
}

async function playClausesFromPayload(payloadClauses, fallbackText) {
  const my = ++JITTER.token;
  const ctx = S.playCtx || new (window.AudioContext || window.webkitAudioContext)();
  S.playCtx = ctx;
  if (ctx.state === "suspended") { try { await ctx.resume(); } catch {} }

  const allFallback = payloadClauses.length > 0 && payloadClauses.every(c => c.fallback);
  if (allFallback) {
    S.playing = true;
    maybeEnableBarge();
    const firstMs = S.turnSentAt ? Math.max(1, performance.now() - S.turnSentAt) : 120;
    if (S.lastTurnResult) S.lastTurnResult.ttfa_ms = firstMs;
    if (S.lastLatencyData) { S.lastLatencyData.ttfa_ms = firstMs; renderLatency(S.lastLatencyData); }
    litStage(6, 120);
    const spoken = payloadClauses.map(c => c.text).filter(Boolean).join(" ");
    const clLang = (payloadClauses.find(c => c.language) || {}).language || S.detectedLang;
    try {
      await speakFallbackAsync(spoken || fallbackText || "", clLang);
    } finally {
      S.playing = false;
      if (my === JITTER.token) maybeEnableBarge();
    }
    return;
  }

  const now = ctx.currentTime;
  let startAt = now + JITTER.prebufferMs / 1000;
  JITTER.queue = [];
  let decodedAny = false;
  for (const cl of payloadClauses) {
    if (!cl.audio_base64) continue;
    try {
      const bin = Uint8Array.from(atob(stripDataUri(cl.audio_base64)), c => c.charCodeAt(0));
      const buf = await ctx.decodeAudioData(bin.buffer.slice(0));
      JITTER.queue.push(buf);
      decodedAny = true;
    } catch {}
  }
  if (my !== JITTER.token) return;

  if (!decodedAny) {
    S.playing = true;
    maybeEnableBarge();
    const firstMs = S.turnSentAt ? Math.max(1, performance.now() - S.turnSentAt) : 120;
    if (S.lastTurnResult) S.lastTurnResult.ttfa_ms = firstMs;
    if (S.lastLatencyData) { S.lastLatencyData.ttfa_ms = firstMs; renderLatency(S.lastLatencyData); }
    const clLang = (payloadClauses.find(c => c.language) || {}).language || S.detectedLang;
    try {
      await speakFallbackAsync(fallbackText || (payloadClauses.find(c => c.text) || {}).text || "", clLang);
    } finally {
      S.playing = false;
      if (my === JITTER.token) maybeEnableBarge();
    }
    return;
  }

  S.playing = true;
  maybeEnableBarge();
  let firstMs = null;
  for (const buf of JITTER.queue) {
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    src.start(startAt);
    if (firstMs === null) {
      const firstAudioAt = performance.now() + (startAt - now) * 1000;
      firstMs = S.turnSentAt ? firstAudioAt - S.turnSentAt : (startAt - now) * 1000;
    }
    startAt += buf.duration;
  }
  litStage(6, 120);
  const total = JITTER.queue.reduce((a, b) => a + b.duration, 0);
  if (S.lastTurnResult) S.lastTurnResult.ttfa_ms = firstMs;
  if (S.lastLatencyData) {
    S.lastLatencyData.ttfa_ms = firstMs;
    renderLatency(S.lastLatencyData);
  }

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
    S.playing = false;
    if (my === JITTER.token) maybeEnableBarge();
  }
}

/* ============================================================
   Stage 7 & 8: Streaming Output Jitter Buffer + Barge-In + WS
   ============================================================ */
JITTER.streamEndAt = 0;
JITTER.streamToken = 0;
JITTER.streamPromises = [];

async function streamClauseToJitter(clause) {
  if (!clause) return;
  const my = JITTER.streamToken || JITTER.token;
  if (clause.language) {
    S.detectedLang = clause.language;
    WS.fallbackLang = clause.language;
  }
  if (clause.fallback) {
    WS.fallbackQueue.push(clause.text || "");
    WS.fallbackLang = clause.language || S.detectedLang;
    S.playing = true;
    maybeEnableBarge();
    const firstMs = S.turnSentAt ? Math.max(1, performance.now() - S.turnSentAt) : 120;
    if (S.lastLatencyData && S.lastLatencyData.ttfa_ms == null) {
      S.lastLatencyData.ttfa_ms = firstMs;
      renderLatency(S.lastLatencyData);
    }
    litStage(6, 120);
    return;
  }

  const ctx = S.playCtx || new (window.AudioContext || window.webkitAudioContext)();
  S.playCtx = ctx;
  if (ctx.state === "suspended") { try { await ctx.resume(); } catch {} }
  if (!clause.audio_base64) return;
  let buf;
  try {
    const bin = Uint8Array.from(atob(stripDataUri(clause.audio_base64)), c => c.charCodeAt(0));
    buf = await ctx.decodeAudioData(bin.buffer.slice(0));
  } catch { return; }
  if (my !== JITTER.token) return;

  const now = ctx.currentTime;
  const startAt = JITTER.streamEndAt > now ? JITTER.streamEndAt : (now + JITTER.prebufferMs / 1000);
  const src = ctx.createBufferSource();
  src.buffer = buf;
  src.connect(ctx.destination);
  src.start(startAt);
  JITTER.streamEndAt = startAt + buf.duration;

  if (!S.playing) {
    S.playing = true;
    maybeEnableBarge();
    const firstAudioAt = performance.now() + Math.max(0, startAt - now) * 1000;
    const firstMs = S.turnSentAt ? firstAudioAt - S.turnSentAt : 120;
    if (S.lastLatencyData) {
      S.lastLatencyData.ttfa_ms = firstMs;
      renderLatency(S.lastLatencyData);
    }
    litStage(6, 120);
  }

  const doneP = new Promise((resolve) => {
    const endWall = performance.now() + Math.max(0.05, (JITTER.streamEndAt - now) + 0.08) * 1000;
    (function wait() {
      if (my !== JITTER.token || performance.now() >= endWall) return resolve();
      setTimeout(wait, 40);
    })();
  });
  JITTER.streamPromises.push(doneP);
}

async function waitForStreamingPlayback(fallbackText, langHint) {
  const my = JITTER.streamToken;
  if (WS.fallbackQueue.length) {
    const text = WS.fallbackQueue.join(" ").trim() || fallbackText || "";
    const lang = langHint || WS.fallbackLang || S.detectedLang;
    WS.fallbackQueue = [];
    try {
      await speakFallbackAsync(text, lang);
    } finally {
      if (my === JITTER.token) { S.playing = false; maybeEnableBarge(); }
    }
    return;
  }
  if (JITTER.streamPromises.length) {
    try {
      await Promise.all(JITTER.streamPromises);
    } finally {
      JITTER.streamPromises = [];
      if (my === JITTER.token) { S.playing = false; maybeEnableBarge(); }
    }
  }
}

function browserStopAudio() {
  JITTER.token++;
  JITTER.streamEndAt = 0;
  JITTER.streamPromises = [];
  WS.fallbackQueue = [];
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

function resolvePendingWsTurn(val = null) {
  if (WS.pendingTurnResolve) {
    const r = WS.pendingTurnResolve;
    WS.pendingTurnResolve = null;
    r(val);
  }
}

function beginUserTurnAfterBargeIn(userTalking = false) {
  VAD.awaiting = false;
  S.awaitingAgentReply = false;
  resolvePendingWsTurn(null);
  VAD.isUserSpeaking = !!(userTalking && S.mode === "auto_vad" && !S.micDenied);
  VAD.speechStartT = performance.now() - VAD.preRollMs;
  VAD.speechMs = 0; VAD.silMs = 0;
  updateMicBtn();
  maybeEnableBarge();
  if (S.mode === "auto_vad" && !S.micDenied && S.callActive) startVad();
}

function applyInterruptedContext(ctx) {
  if (!ctx) return;
  let updated = false;
  for (let i = S.transcriptItems.length - 1; i >= 0; i--) {
    if (S.transcriptItems[i].role === "agent") {
      if (!S.transcriptItems[i].interrupted) {
        S.transcriptItems[i].interrupted = true;
        S.transcriptItems[i].badge = "🤖 BimaVani";
        updated = true;
      }
      break;
    }
  }
  if (ctx.mic_dump) {
    pushTranscript("interrupted", ctx.mic_dump, "⚡ Mic Dump", true);
  } else if (updated) {
    renderTranscriptLog();
  }
}

async function notifyAutoVadBargeIn(spokenText = "", pcmB64 = "", source = "auto_vad_mic") {
  const now = performance.now();
  if (now - S.lastBargeNotifiedAt < 250) return;
  S.lastBargeNotifiedAt = now;
  const payload = {
    call_id: S.callId,
    transcript: "",
    spoken_text: spokenText,
    pcm_b64: pcmB64,
    source,
    update_cards: false,
  };
  try {
    if (WS.open && WS.sock && WS.sock.readyState === 1) {
      WS.sock.send(JSON.stringify({ type: "barge_in", ...payload }));
    } else {
      const res = await fetch(API.bargeIn, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.flush_latency_ms != null) litStage(7, data.flush_latency_ms, { hold: true });
      applyInterruptedContext(data.interrupted_context);
    }
  } catch {}
}

function wsClose() {
  if (WS.sock) {
    try { WS.sock.close(); } catch {}
  }
  WS.sock = null;
  WS.open = false;
  WS.callId = "";
  WS.pcmCarry = [];
  WS.pendingResolve = null;
  WS.pendingTurnResolve = null;
  setWsPill("HTTP mode", false);
}

function wsConnect(callId) {
  wsClose();
  return new Promise((resolve) => {
    if (!window.WebSocket) { setWsPill("HTTP fallback"); return resolve(false); }
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws/call/${encodeURIComponent(callId)}`;
    let settled = false;
    const done = (ok) => {
      if (settled) return;
      settled = true;
      resolve(ok);
    };
    const timer = setTimeout(() => done(false), 1200);
    try {
      const sock = new WebSocket(url);
      WS.sock = sock;
      WS.callId = callId;
      sock.onopen = () => {
        WS.open = true;
        setWsPill("⚡ WS Streaming", true);
        sock.send(JSON.stringify({
          type: "config",
          endpoint_mode: S.mode,
          vad_silence_ms: S.vadSilenceMs,
          server_auto_endpoint: false,
          stt_language: S.languagePref,
          language: explicitSelectedLang(),
        }));
        clearTimeout(timer);
        done(true);
      };
      sock.onerror = () => {
        WS.open = false;
        setWsPill("HTTP fallback", false);
        clearTimeout(timer);
        done(false);
      };
      sock.onclose = () => {
        WS.open = false;
        if (S.callActive) setWsPill("HTTP fallback", false);
        if (WS.pendingTurnResolve) {
          const r = WS.pendingTurnResolve;
          WS.pendingTurnResolve = null;
          r(null);
        }
      };
      sock.onmessage = (evt) => {
        let msg;
        try { msg = JSON.parse(evt.data); } catch { return; }
        handleWsMessage(msg);
      };
    } catch {
      clearTimeout(timer);
      setWsPill("HTTP fallback", false);
      done(false);
    }
  });
}

async function handleWsMessage(msg) {
  if (!msg || !msg.type) return;
  if (msg.type === "ready") {
    if (msg.sessions) {
      const sp = $("#session-pill");
      if (sp) sp.textContent = `Sessions: ${msg.sessions.active_sessions}/${msg.sessions.max_sessions}`;
    }
    return;
  }
  if (msg.type === "vad_state") {
    if (msg.input_jitter) updateJitterPill(msg.input_jitter, null);
    return;
  }
  if (msg.type === "error") {
    S.awaitingAgentReply = false;
    VAD.awaiting = false;
    maybeEnableBarge();
    setCallState(`⚠️ ${msg.error || "Turn error — ready to continue"}`);
    resolvePendingWsTurn(null);
    return;
  }
  if (msg.type === "barge_in_ack") {
    litStage(7, msg.flush_latency_ms, { hold: true });
    applyInterruptedContext(msg.interrupted_context);
    if (msg.cards && msg.cards.reply_text) {
      renderTurn({
        _skipTranscriptUser: true,
        transcript: msg.interrupted_context?.raw_transcript || "",
        reply_text: msg.cards.reply_text,
        entities: msg.cards.entities,
        tools_invoked: msg.cards.tools_invoked,
        ticket_status: msg.cards.ticket_status,
        stage: msg.cards.stage,
        clauses: msg.cards.clauses,
      });
      if (msg.cards.clauses && msg.cards.clauses.length) {
        await playClausesFromPayload(msg.cards.clauses, msg.cards.reply_text);
      }
      await agentDoneReplying();
    } else {
      resolvePendingWsTurn(null);
    }
    return;
  }
  if (msg.type === "endpoint_detected") {
    if (!VAD.awaiting && !S.awaitingAgentReply) {
      VAD.awaiting = true;
      S.awaitingAgentReply = true;
      S.turnSentAt = performance.now();
      maybeEnableBarge();
      setCallState("⏳ Processing turn… (WS Streaming)");
    }
    return;
  }
  if (msg.type === "stt_result") {
    const tr = $("#stt-transcript");
    if (tr) tr.textContent = (msg.transcript || "").trim() || "(no words recognised)";
    if (msg.stt && msg.stt.stt_latency_ms != null) litStage(1, msg.stt.stt_latency_ms);
    return;
  }
  if (msg.type === "brain_result") {
    JITTER.streamToken = ++JITTER.token;
    JITTER.streamEndAt = 0;
    JITTER.streamPromises = [];
    WS.fallbackQueue = [];
    WS.fallbackLang = msg.language || detectTextLanguage(msg.reply_text || "", "");
    S.detectedLang = WS.fallbackLang;
    WS.streamComplete = false;
    S.lastTurnResult = msg;
    renderTurn({
      _isGreeting: !!msg.is_greeting,
      language: msg.language,
      transcript: msg.transcript || "",
      reply_text: msg.reply_text || "",
      entities: msg.entities,
      tools_invoked: msg.tools_invoked,
      ticket_status: msg.ticket_status,
      stage: msg.stage,
      stt_empty: msg.stt_empty,
    });
    if (msg.stt_empty) {
      setCallState(msg.hint ? `⚠️ STT empty — ${msg.hint}` : "🎧 Kuch sunai nahi diya — dobara boliye.");
    }
    return;
  }
  if (msg.type === "tts_clause") {
    await streamClauseToJitter(msg.clause);
    return;
  }
  if (msg.type === "turn_complete") {
    WS.streamComplete = true;
    if (msg.pipeline) {
      if (msg.is_greeting) {
        S.lastLatencyData = { _isGreeting: true, pipeline: msg.pipeline, total_turn_ms: msg.total_turn_ms };
      } else {
        S.lastLatencyData = { pipeline: msg.pipeline, total_turn_ms: msg.total_turn_ms };
      }
      decodeStages(msg.pipeline, null);
      renderLatency(S.lastLatencyData);
      updateQueues(msg.pipeline.queues || {});
      updateJitterPill(msg.pipeline.input_jitter, msg.pipeline.output_jitter);
    }
    if (WS.pendingTurnResolve) {
      const r = WS.pendingTurnResolve;
      WS.pendingTurnResolve = null;
      r(msg);
    }
  }
}

async function triggerBargeIn(overrideTranscript = "") {
  if (!S.callActive) return;
  const t0 = performance.now();
  const spokenSnapshot = $("#agent-reply")?.textContent || "";
  const pcmSnap = grabRecentMicPcmB64(450);
  browserStopAudio();
  const typed = (overrideTranscript || $("#type-say")?.value || "").trim();
  beginUserTurnAfterBargeIn(!typed);
  const payload = {
    call_id: S.callId,
    transcript: typed,
    spoken_text: spokenSnapshot,
    pcm_b64: pcmSnap,
    source: typed ? "driver_correction" : "driver_button",
    update_cards: !!typed,
    language: explicitSelectedLang(),
  };

  if (typed) pushTranscript("barge", typed, "⚡ Driver interrupted [interrupted]", true);

  const res = await fetch(API.bargeIn, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok || data.ok === false) {
    setCallState(`⚠️ Barge-in warning: ${data.error || res.status}`);
    return;
  }

  litStage(7, data.flush_latency_ms, { hold: true });
  // Tag cut-off agent turn with [interrupted] (without duplicating typed barge text)
  applyInterruptedContext(typed ? { agent_cut: data.interrupted_context?.agent_cut } : data.interrupted_context);
  const stopMs = Math.round(performance.now() - t0);
  setCallState(`🎧 Interrupted in ${stopMs} ms — listening`);

  if (typed) {
    if ($("#type-say")) $("#type-say").value = "";
    if (data.cards && data.cards.reply_text) {
      renderTurn({
        _skipTranscriptUser: true,
        language: data.cards.language,
        transcript: typed,
        reply_text: data.cards.reply_text,
        entities: data.cards.entities,
        tools_invoked: data.cards.tools_invoked,
        ticket_status: data.cards.ticket_status,
        stage: data.cards.stage,
        clauses: data.cards.clauses,
      });
      if (data.cards.clauses && data.cards.clauses.length) {
        S.turnSentAt = t0;
        await playClausesFromPayload(data.cards.clauses, data.cards.reply_text);
      }
    }
    await agentDoneReplying();
  } else {
    const reply = $("#agent-reply");
    if (reply) reply.textContent = `Stopped in ${stopMs} ms — go ahead, I'm listening.`;
  }
}

$("#btn-barge-in").onclick = () => triggerBargeIn("");
$("#btn-barge-in-2").onclick = () => triggerBargeIn("");

function maybeEnableBarge() {
  const on = S.callActive && (S.playing || S.awaitingAgentReply);
  $("#btn-barge-in").disabled = !on;
  const b2 = $("#btn-barge-in-2");
  if (b2) b2.disabled = !on;
}

/* ============================================================
   Turn submission (Stage 1 → 8 full round trip, WS-first)
   ============================================================ */
async function sendTurn(wavB64, transcriptOverride) {
  S.awaitingAgentReply = true;
  VAD.awaiting = true;
  maybeEnableBarge();
  S.turnSentAt = performance.now();
  const explicitLang = explicitSelectedLang();
  try {
    if (WS.open && WS.sock && WS.sock.readyState === 1) {
      resolvePendingWsTurn(null);
      const done = await new Promise((resolve) => {
        WS.pendingTurnResolve = resolve;
        WS.sock.send(JSON.stringify({
          type: "end_turn",
          wav_b64: wavB64 || "",
          transcript_override: transcriptOverride || "",
          endpoint_mode: S.mode,
          vad_silence_ms: S.vadSilenceMs,
          language: explicitLang,
          stt_language: S.languagePref,
        }));
      });
      if (done && !done.error) {
        await waitForStreamingPlayback(S.lastTurnResult?.reply_text || "", S.lastTurnResult?.language || S.detectedLang);
        return;
      }
      if (done && done.error) {
        return;
      }
    }

    const body = {
      call_id: S.callId,
      wav_b64: wavB64 || "",
      transcript_override: transcriptOverride || "",
      endpoint_mode: S.mode,
      vad_silence_ms: S.vadSilenceMs,
      language: explicitLang,
      stt_language: S.languagePref,
    };
    const res = await fetch(API.turn, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || `HTTP ${res.status}`);
    }
    S.lastTurnResult = data;
    decodeStages(data.pipeline, null);
    renderTurn(data);
    updateQueues(data.pipeline?.queues || {});
    updateJitterPill(data.pipeline?.input_jitter, data.pipeline?.output_jitter);

    if (data.stt_empty) {
      $("#stt-transcript").textContent = "(no words recognised)";
      setCallState(data.hint ? `⚠️ STT empty — ${data.hint}` : "🎧 Kuch sunai nahi diya — dobara boliye.");
      $("#agent-reply").textContent = data.reply_text || "";
    }

    if (data.clauses && data.clauses.length) {
      await playClausesFromPayload(data.clauses, data.reply_text);
    }
  } catch (e) {
    const reply = $("#agent-reply");
    if (reply) reply.textContent = `⚠️ Network hiccup (${e.message}) — Credit-Shield kept the session alive.`;
    setCallState("⚠️ Recovery ready — try speaking or sending again");
  } finally {
    await agentDoneReplying();
  }
}

async function agentDoneReplying() {
  S.awaitingAgentReply = false;
  VAD.awaiting = false;
  VAD.speechStartT = 0;
  S.playing = false;
  maybeEnableBarge();
  if (!S.callActive) return;
  if (S.micDenied) { setCallState("Typed mode — no microphone"); return; }
  if (S.mode === "auto_vad") startVad();
  else setCallState("🎙️ PUSH-TO-TALK — hold the orb to reply");
}

/* ============================================================
   UI LAYER + LIVE CASE INTELLIGENCE RENDERING
   ============================================================ */
const HERO_STATES = [
  [/Dialing/i,                        "Calling<br>BimaVani…",       "Dialing 1-800-BIMA-VANI — the line is connecting."],
  [/Ringing/i,                        "Ringing…",                   "The agent's phone is ringing. Hang on a second."],
  [/picked up/i,                      "Connected.",                 "The agent has picked up — say hello."],
  [/LISTENING|Listening/i,            "Listening for<br>your turn.", "Speak naturally — I answer the moment you pause."],
  [/PUSH-TO-TALK/i,                   "Hold to talk.",              "Press the orb, speak, release. Or click a scenario chip."],
  [/greeting|Agent connected|Speaking/i, "One moment —<br>I'm answering.", "The reply streams clause by clause through the jitter buffer."],
  [/Processing|Thinking|turn/i,       "Thinking…",                  "Running injury triage, smart RSA routing, and IRDAI claim math."],
  [/Typed mode/i,                     "Type or click<br>a scenario.", "No microphone detected — use the quick Hinglish scenarios or typed box."],
  [/Call ended/i,                     "Call ended.",                "Tap Call to start a fresh session with clean memory."],
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

function paintOrb() {
  const orb = $("#btn-ptt");
  if (!orb) return;
  const speaking = !!S.playing;
  const thinking = !!S.awaitingAgentReply && !speaking;
  orb.classList.toggle("talking", !!VAD.isUserSpeaking || !!S.recording);
  orb.classList.toggle("thinking", thinking);
  orb.classList.toggle("listening", !!VAD.active && !VAD.isUserSpeaking && !S.recording && !thinking);
  paintPhones();
}

function paintPhonesBase() {
  const scene = $("#call-scene");
  if (!scene) return;

  const live = !!S.callActive && S.phase === "connected";
  const driverSpeaking = !!VAD.isUserSpeaking || !!S.recording;
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
  const status = $("#scene-status");
  if (status) {
    status.innerHTML = !live
      ? (S.callEnded
          ? "Call ended — tap <b>Call</b> to reconnect with a clean session."
          : "Tap <b>Call</b> (or pick a scenario chip) to connect.")
      : (driverSpeaking ? "You're talking — BimaVani is listening."
        : agentSpeaking ? "BimaVani is answering…"
        : agentThinking ? "BimaVani is thinking…"
        : "Both phones are on the line.");
  }
}

/* ---------- Live Case Intelligence & IRDAI Desk rendering ---------- */
function renderTranscriptLog() {
  const log = $("#transcript-log");
  if (!log) return;
  if (!S.transcriptItems.length) {
    log.innerHTML = `<p class="muted">Multi-turn Hinglish conversation &amp; [interrupted] context log will appear here.</p>`;
  } else {
    log.innerHTML = S.transcriptItems.map(item => `
      <div class="log-item ${escHtml(item.role)}${item.interrupted ? " interrupted" : ""}">
        <div class="log-meta">${item.interrupted ? '<span class="int-tag">[interrupted]</span>' : ""}${escHtml(item.badge || item.role.toUpperCase())} · ${escHtml(item.ts)}</div>
        <div>${escHtml(item.text)}</div>
      </div>
    `).join("");
    log.scrollTop = log.scrollHeight;
  }
  const cnt = $("#log-count");
  if (cnt) cnt.textContent = `${S.transcriptItems.length} msg${S.transcriptItems.length === 1 ? "" : "s"}`;
}

function pushTranscript(role, text, badge = "", interrupted = false) {
  if (!text) return;
  const last = S.transcriptItems[S.transcriptItems.length - 1];
  if (last && last.role === role && last.text === text && !!last.interrupted === !!interrupted) return;
  const now = new Date();
  const ts = now.toTimeString().slice(0, 8);
  S.transcriptItems.push({ role, text, badge, ts, interrupted: !!interrupted });
  renderTranscriptLog();
}

function resetDeskUi() {
  S.transcriptItems = [];
  S.lastDispatch = null;
  S.lastDraft = null;
  const badge = $("#ticket-badge");
  if (badge) { badge.textContent = "DRAFT"; badge.className = "chip-status"; }
  const safety = $("#safety-banner");
  if (safety) { safety.textContent = "🛡️ Safety Triage: Standby"; safety.className = "safety-pill is-neutral"; }
  $("#ent-policy").textContent = "—";
  $("#ent-road").textContent = "—";
  $("#ent-parts").textContent = "—";
  $("#ent-drivable").textContent = "—";
  $("#ent-stage").textContent = "idle";
  if ($("#ent-lang")) $("#ent-lang").textContent = "—";
  S.detectedLang = explicitSelectedLang() || "hi-IN";
  $("#rsa-wf-badge").textContent = "—";
  $("#rsa-card").className = "desk-card";
  $("#rsa-body").innerHTML = `<p class="muted">Start a call or click a demo scenario to view live RSA workflow routing (A: NCB Saver · B: Flatbed Tow · C: Cashless Self-Drive).</p>`;
  $("#fnol-id-badge").textContent = "—";
  $("#fnol-card").className = "desk-card";
  $("#fnol-body").innerHTML = `<p class="muted">Synthetic DPDP-safe claim math (deductible + age-scaled depreciation + surveyor range disclaimer) populates automatically when applicable.</p>`;
  $("#log-count").textContent = "0 turns";
  $("#transcript-log").innerHTML = `<p class="muted">Multi-turn Hinglish conversation log will appear here.</p>`;
}

function renderCaseIntelligence(data) {
  const ents = data.entities || {};
  const tools = data.tools_invoked || [];
  const status = data.ticket_status || "DRAFT";

  // Ticket status pill
  const tBadge = $("#ticket-badge");
  if (tBadge) {
    tBadge.textContent = status;
    tBadge.className = "chip-status"
      + (/EMERGENCY/i.test(status) ? " warn"
        : /(APPLIED|DISPATCHED|NCB PROTECTED)/i.test(status) ? " live" : "");
  }

  // Safety triage banner
  const triage = tools.find(t => t.tool === "triage_injury_and_safety") || {};
  const injured = Boolean(ents.injured || triage.injured);
  const safety = $("#safety-banner");
  if (safety) {
    if (injured) {
      const calls = (ents.emergency_calls || triage.emergency_calls || [])
        .map(c => c.label).join(" · ");
      safety.textContent = `🚨 ${calls || "112 AMBULANCE + 112 POLICE DISPATCHED"}`;
      safety.className = "safety-pill is-emergency";
    } else if (data.stage && data.stage !== "idle") {
      safety.textContent = "✅ SAFE — No injury reported";
      safety.className = "safety-pill is-safe";
    }
  }

  // Entities strip
  const activeLang = data.language || ents.language || detectTextLanguage(data.reply_text || "", "");
  S.detectedLang = activeLang;
  if ($("#ent-lang")) {
    $("#ent-lang").textContent = LANG_LABELS[activeLang] || activeLang;
  }
  $("#ent-policy").textContent = ents.policy_number ? `#${ents.policy_number}` : "—";
  $("#ent-road").textContent = ents.road_explicit && ents.road_type ? ents.road_type : (ents.road_type ? `${ents.road_type} (inferred)` : "—");
  $("#ent-parts").textContent = (ents.parts && ents.parts.length) ? ents.parts.join(", ") : "—";
  $("#ent-drivable").textContent = (ents.parts && ents.parts.length)
    ? (ents.is_drivable ? "Yes (Drivable)" : "No (Non-drivable)")
    : "—";
  $("#ent-stage").textContent = data.stage || "active";

  // Dispatch & Claim Draft lookup (from current tools or persisted entities)
  const disp = tools.find(t => t.tool === "dispatch_smart_rsa") || ents.last_dispatch || null;
  const draft = tools.find(t => t.tool === "calculate_irdai_claim_draft") || ents.last_draft || null;

  // RSA Card
  const rsaCard = $("#rsa-card");
  const rsaBody = $("#rsa-body");
  const wfBadge = $("#rsa-wf-badge");
  if (disp) {
    S.lastDispatch = disp;
    const wf = disp.workflow || "—";
    wfBadge.textContent = wf === "EMERGENCY" ? "112 SOS" : `Workflow ${wf}`;
    rsaCard.className = "desk-card"
      + (wf === "EMERGENCY" ? " is-emergency" : wf === "A" ? " is-ncb" : "");
    const g = disp.destination_garage;
    const rural = disp.rural_reimbursement;
    rsaBody.innerHTML = `
      <div class="kv-list">
        <div class="kv"><span>Workflow</span><b>${escHtml(disp.workflow_label || wf)}</b></div>
        <div class="kv"><span>Dispatch</span><b>${escHtml(disp.dispatch_vendor || "—")}</b></div>
        <div class="kv"><span>ETA</span><b>${disp.eta_mins ? `${escHtml(disp.eta_mins)} mins` : "Self-drive / Immediate"}</b></div>
        ${disp.free_tow_km_limit ? `<div class="kv"><span>Free Tow Limit</span><b>${escHtml(disp.free_tow_km_limit)} km</b></div>` : ""}
        ${g ? `<div class="kv"><span>Garage</span><b>${escHtml(g.name)} (${escHtml(g.network)})</b></div>` : ""}
      </div>
      ${disp.ncb_protected ? `<div class="callout">🛡️ 20% No-Claim Bonus Protected — Do NOT file an insurance claim.</div>` : ""}
      ${disp.do_not_start_engine ? `<div class="callout warn">${escHtml(disp.engine_warning || "🔴 Do NOT Start Engine!")}</div>` : ""}
      ${disp.nhai_patrol_alerted ? `<div class="callout">🛣️ 1033 NHAI Highway Patrol safety cones alerted.</div>` : ""}
      ${rural && rural.eligible ? `<div class="callout">🚜 ${escHtml(rural.authority)} (${escHtml(rural.reason)})</div>` : ""}
    `;
  }

  // FNOL Card
  const fnolCard = $("#fnol-card");
  const fnolBody = $("#fnol-body");
  const fnolBadge = $("#fnol-id-badge");
  if (disp && disp.workflow === "A") {
    fnolBadge.textContent = "NO CLAIM (NCB SAFE)";
    fnolCard.className = "desk-card is-ncb";
    fnolBody.innerHTML = `
      <div class="callout">🛡️ Intentionally skipped FNOL draft — minor RSA jobs keep your 20% NCB intact.</div>
      <p class="muted">Zero deductible charged; covered under roadside assistance.</p>
    `;
  } else if (draft && draft.claim_id) {
    S.lastDraft = draft;
    fnolBadge.textContent = draft.claim_id;
    fnolCard.className = "desk-card";
    const items = draft.line_items || [];
    fnolBody.innerHTML = `
      <div class="kv-list">
        <div class="kv"><span>Policy / Holder</span><b>#${escHtml(draft.policy_number)} · ${escHtml(draft.owner)}</b></div>
        <div class="kv"><span>Vehicle</span><b>${escHtml(draft.vehicle)} (${escHtml(draft.plan)})</b></div>
        <div class="kv"><span>Compulsory Deductible</span><b>₹${Number(draft.compulsory_deductible_inr || 0).toLocaleString()}</b></div>
        <div class="kv"><span>Estimated Range</span><b>${escHtml(draft.estimated_range_display)}</b></div>
      </div>
      ${items.length ? `
        <table class="dep-table">
          <thead><tr><th>Part</th><th>Base</th><th>Dep</th><th>Allowable</th></tr></thead>
          <tbody>
            ${items.map(li => `
              <tr>
                <td>${escHtml(li.part)}</td>
                <td>₹${li.genuine_cost_low.toLocaleString()}–${li.genuine_cost_high.toLocaleString()}</td>
                <td>${escHtml(li.depreciation_pct_applied)}%</td>
                <td>₹${li.allowable_low.toLocaleString()}–${li.allowable_high.toLocaleString()}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      ` : ""}
      <p class="disclaimer">${escHtml(draft.status_label || "")}</p>
    `;
  }
}

/* ============================================================
   Phone-call animation layer
   ============================================================ */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const RING = { ctx: null };

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
      g.gain.linearRampToValueAtTime(0.08, t + 0.03);
      g.gain.setValueAtTime(0.08, t + d - 0.03);
      g.gain.linearRampToValueAtTime(0, t + d);
    };
    burst(t0 + 0.4, 0.35);
    burst(t0 + 1.0, 0.35);
    RING.ctx = ctx;
  } catch {}
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

function paintPhones() {
  paintPhonesBase();
  const ph = S.phase;
  const scene = $("#call-scene");
  if (scene) scene.dataset.phase = ph;
  const copy = {
    idle:      ["Ready to call", "Standby",       "Idle"],
    dialing:   ["Calling…",      "Standby",       "Dialing"],
    ringing:   ["Ringing…",      "Incoming call", "Ringing"],
    connected: ["Connected",     "On call",       "Live"],
    ended:     ["Call ended",    "Call ended",    "Idle"],
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

setInterval(() => {
  const t = ($("#call-timer") || {}).textContent || "00:00";
  $$(".sync-timer").forEach((el) => { el.textContent = t; });
}, 250);

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
      updateMicBtn();
    } else endTalk();
  });
  orb.addEventListener("click", () => { if (S.mode === "auto_vad") micToggle(); });
})();

/* ---------- measured latency ---------- */
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
  const netMs = ttfaMs != null ? Math.max(0, ttfaMs - backendMs) : null;

  if (ttfaMs != null && !data._latPushed && !data._isGreeting) {
    data._latPushed = true;
    S.latencyHistory.push({ ttfa: ttfaMs, total: backendMs });
    if (S.latencyHistory.length > 6) S.latencyHistory.shift();
  }

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

  const slo = (data.pipeline && data.pipeline.slo) || {};
  const pct = slo.percentiles || {};
  const sloLine = pct.ttfa_ms && pct.ttfa_ms.count
    ? `<div class="lat-hist">SLO · TTFA p50/p95: ${pct.ttfa_ms.p50}/${pct.ttfa_ms.p95} ms · Cache ${Math.round((slo.tts_cache_hit_rate || 0) * 100)}%${slo.pii_redactions ? ` · 🔒 ${slo.pii_redactions} PII redacted` : ""}</div>`
    : "";

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
        : "") +
      sloLine;
  }
}

function renderTurn(data) {
  if (!data) return;
  if (data.pipeline) S.lastLatencyData = data;
  const said = (data.transcript || "").trim();
  const transcript = $("#stt-transcript");
  if (transcript && !data._isGreeting) {
    transcript.textContent = said || "…";
    transcript.classList.toggle("muted", !said);
  }
  const reply = $("#agent-reply");
  if (reply) reply.textContent = data.reply_text || "…";

  if (!data._isGreeting && !data._skipTranscriptUser && said && !data.stt_empty) {
    pushTranscript("driver", said, "🧑 Driver");
  }
  if (data.reply_text) {
    pushTranscript("agent", data.reply_text, "🤖 BimaVani");
  }

  renderCaseIntelligence(data);
  if (data.pipeline) renderLatency(data);
  if (data.reply_text) setCallState("🤖 Speaking…");
  paintOrb();
}

/* ============================================================
   Controls & Modals
   ============================================================ */
function syncPttControls() {
  const send = $("#btn-ptt-send");
  const cancel = $("#btn-ptt-cancel");
  if (send) send.disabled = !S.recording;
  if (cancel) cancel.disabled = !S.recording;
}

function sendWsConfig() {
  if (WS.open && WS.sock && WS.sock.readyState === 1) {
    try {
      WS.sock.send(JSON.stringify({
        type: "config",
        endpoint_mode: S.mode,
        vad_silence_ms: S.vadSilenceMs,
        server_auto_endpoint: false,
        stt_language: S.languagePref,
        language: explicitSelectedLang(),
      }));
    } catch {}
  }
}

$("#lang-select")?.addEventListener("change", (e) => {
  S.languagePref = e.target.value || "indic-auto";
  const chosen = explicitSelectedLang();
  if (chosen) S.detectedLang = chosen;
  sendWsConfig();
});

function syncModeUi() {
  $$(".seg-btn").forEach(b => b.classList.toggle("active", b.dataset.mode === S.mode));
  const ptt = $("#ptt-controls");
  if (ptt) ptt.classList.toggle("hidden", S.mode !== "push_to_talk");
  if (S.mode === "push_to_talk") stopVad();
  else if (S.callActive && !S.micDenied) startVad();
  updateMicBtn();
  sendWsConfig();
}

$$(".seg-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    S.mode = btn.dataset.mode;
    syncModeUi();
    if (S.callActive) {
      setCallState(S.mode === "auto_vad"
        ? "🎧 LISTENING (Auto-VAD Active)"
        : "🎙️ PUSH-TO-TALK — hold the orb (or tap once) to speak");
    }
  });
});

$("#vad-silence").addEventListener("input", (e) => {
  S.vadSilenceMs = parseInt(e.target.value, 10) || 650;
  $("#vad-window").textContent = S.vadSilenceMs;
  sendWsConfig();
});
$("#vad-sens").addEventListener("input", (e) => {
  S.vadSens = parseFloat(e.target.value) || 2.2;
  $("#vad-sens-val").textContent = S.vadSens.toFixed(1) + "×";
  if (VAD.calibrated && !VAD.calibrating)
    VAD.threshold = Math.max(VAD.ambientRms * S.vadSens, 0.018);
});

$("#btn-ptt-send").addEventListener("click", () => endTalk());
$("#btn-ptt-cancel").addEventListener("click", () => cancelTalk());

async function sendTypedLine(customText) {
  const text = (customText ?? $("#type-say").value).trim();
  if (!text) return;
  if (!S.callActive) {
    await startCallSession({ fastConnect: true });
  }
  if (!customText && $("#type-say")) $("#type-say").value = "";
  if (S.playing) browserStopAudio();
  beginUserTurnAfterBargeIn(false);
  setCallState("⏳ Processing turn…");
  await sendTurn("", text);
}

$("#btn-say").onclick = () => sendTypedLine();
$("#type-say").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); sendTypedLine(); }
});

$("#say-edit").onclick = () => {
  const setup = document.querySelector(".setup");
  if (setup) setup.open = true;
  $("#type-say").focus();
};

// Quick demo scenario chips
$$(".demo-chip").forEach(chip => {
  chip.addEventListener("click", async () => {
    const bargeText = chip.dataset.barge;
    const utt = chip.dataset.utterance;
    if (bargeText) {
      if (!S.callActive) await startCallSession({ fastConnect: true });
      await triggerBargeIn(bargeText);
    } else if (utt) {
      await sendTypedLine(utt);
    }
  });
});

$("#noise-preset").addEventListener("change", updateNoise);
$("#noise-into-mic").addEventListener("change", updateNoise);
$("#noise-aloud").addEventListener("change", updateNoise);

/* ---------- health & queues ---------- */
function updateQueues(queues) {
  document.querySelectorAll(".node-q").forEach(el => {
    const q = queues[el.dataset.q];
    if (!q) return;
    el.textContent = String(q.depth ?? 0);
    el.classList.toggle("active", q.depth > 0);
    el.classList.toggle("full", !!q.backpressure_active);
  });
}

async function refreshHealth() {
  try {
    const res = await fetch(API.health);
    const h = await res.json();
    const pill = $("#mode-pill");
    if (pill) {
      const live = h.mode === "LIVE_GNANI";
      pill.textContent = live ? "🟢 live gnani" : "🛡 fallback";
      pill.className = "chip-status chip-clickable" + (live ? " live" : "");
    }
    const foot = $("#foot-meta");
    if (foot) foot.textContent = `${h.stt_model} · ${h.tts_model} · ${h.gnani_base_url} · bounded queues (max 32)`;

    const b = h.brain || {};
    const on = b.engine === "llm+deterministic";
    const brainPill = $("#brain-pill");
    if (brainPill) {
      brainPill.textContent = on ? "🧠 LLM phrasing" : "🧠 Rules + tools";
      brainPill.className = "chip-status" + (on ? " live" : "");
      brainPill.title = on
        ? `LLM ${b.model} active`
        : `Deterministic Hinglish engine (~2ms)${b.last_error ? " · LLM standby: " + b.last_error : ""}`;
    }
    if (h.sessions) {
      const sp = $("#session-pill");
      if (sp) sp.textContent = `Sessions: ${h.sessions.active_sessions}/${h.sessions.max_sessions}`;
    }
    updateQueues(h.queues || {});
  } catch {
    const pill = $("#mode-pill");
    if (pill) pill.textContent = "⚠ server down";
  }
}

/* ---------- Modals: Gnani API & 20-Case Benchmark ---------- */
function openModal(id) {
  const m = document.getElementById(id);
  if (m) m.classList.remove("hidden");
}
function closeModal(id) {
  const m = document.getElementById(id);
  if (m) m.classList.add("hidden");
}

$$("[data-close]").forEach(el => {
  el.addEventListener("click", () => closeModal(el.dataset.close));
});
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeModal("modal-gnani");
    closeModal("modal-eval");
  }
});

async function loadGnaniConfig() {
  try {
    const cfg = await (await fetch(API.config)).json();
    $("#gnani-config-summary").innerHTML = `
      <div><span>Mode</span><b>${cfg.live_mode ? "🟢 LIVE GNANI" : "🛡️ Credit-Shield Fallback"}</b></div>
      <div><span>STT Model</span><b>${escHtml(cfg.stt_model)} (${escHtml(cfg.stt_language)})</b></div>
      <div><span>TTS Model</span><b>${escHtml(cfg.tts_model)} · ${escHtml(cfg.tts_voice)}</b></div>
      <div><span>API Key</span><b>${escHtml(cfg.api_key_masked || "not set")}</b></div>
    `;
  } catch {}
}

$("#btn-open-key").onclick = async () => { await loadGnaniConfig(); openModal("modal-gnani"); };
$("#mode-pill").onclick = async () => { await loadGnaniConfig(); openModal("modal-gnani"); };
$("#btn-open-eval").onclick = () => openModal("modal-eval");

$("#key-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const key = $("#gnani-key-input").value.trim();
  const statusEl = $("#key-save-status");
  try {
    const res = await fetch(API.setKey, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: key }),
    });
    const d = await res.json();
    statusEl.textContent = d.live_mode
      ? `✅ Key active (${d.api_key_masked}) — live mode enabled`
      : "🛡️ Cleared key — Credit-Shield fallback engaged";
    await loadGnaniConfig();
    await refreshHealth();
  } catch (err) {
    statusEl.textContent = `❌ Failed: ${err.message}`;
  }
});

$("#btn-verify-gnani").onclick = async () => {
  const box = $("#verify-output");
  box.classList.remove("hidden");
  box.innerHTML = `<span class="muted">Running Timbre TTS → 16kHz resample → Prisma STT round-trip…</span>`;
  try {
    const res = await fetch(API.verify, { method: "POST" });
    const d = await res.json();
    box.innerHTML = `
      <div><b>Status:</b> ${escHtml(d.ui_hint)}</div>
      <div><b>TTS:</b> ${escHtml(d.tts_model)} · ${d.tts_latency_ms} ms ${d.tts_cached ? "(cache hit)" : ""} · ${d.tts_bytes} bytes</div>
      <div><b>STT:</b> ${escHtml(d.stt_model)} · ${d.stt_latency_ms} ms · req <code>${escHtml(d.request_id || "—")}</code></div>
      <div><b>Round-trip:</b> ${d.round_trip_ms} ms · <b>Match:</b> ${Math.round((d.match_ratio || 0) * 100)}%</div>
      <div><b>Live transcript:</b> ${escHtml(d.live_transcript || "(empty)")}</div>
    `;
  } catch (err) {
    box.innerHTML = `<div>❌ Error: ${escHtml(err.message)}</div>`;
  }
};

$("#btn-run-eval").onclick = async () => {
  const btn = $("#btn-run-eval");
  const status = $("#eval-status");
  btn.disabled = true;
  status.textContent = "Running 20-case deterministic & acoustic suite…";
  try {
    const res = await fetch(API.eval, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run: true }),
    });
    const data = await res.json();
    const s = data.summary || {};
    const byc = s.by_condition || {};
    const mlLabel = s.multilingual_10lang_cases
      ? ` · 10-Lang Matrix ${s.multilingual_10lang_correct}/${s.multilingual_10lang_cases}`
      : "";
    status.textContent = `Completed ${s.total_cases} cases · Domain accuracy ${(s.domain_accuracy * 100).toFixed(1)}% · Overall ${(s.overall_accuracy * 100).toFixed(1)}%${mlLabel}`;
    $("#eval-summary-cards").innerHTML = `
      <div class="eval-kpi"><b>${s.total_cases || 0}</b><span>Core Cases</span></div>
      <div class="eval-kpi"><b>${((s.domain_accuracy || 0) * 100).toFixed(0)}%</b><span>Scenario Accuracy</span></div>
      <div class="eval-kpi"><b>${((s.overall_accuracy || 0) * 100).toFixed(0)}%</b><span>Overall (20/20)</span></div>
      <div class="eval-kpi"><b>${s.multilingual_10lang_correct ?? "—"}/${s.multilingual_10lang_cases ?? "—"}</b><span>All 10 Gnani Langs</span></div>
      <div class="eval-kpi"><b>${byc.HIGHWAY?.correct ?? 0}/${byc.HIGHWAY?.cases ?? 0}</b><span>NH-44 Highway</span></div>
      <div class="eval-kpi"><b>${byc.GSM?.correct ?? 0}/${byc.GSM?.cases ?? 0}</b><span>8 kHz GSM</span></div>
    `;
    const rows = data.rows || [];
    $("#eval-table-wrap").innerHTML = `
      <table class="eval-table">
        <thead><tr><th>ID</th><th>Case</th><th>Condition</th><th>Workflow</th><th>Status</th><th>ms</th><th>Pass</th></tr></thead>
        <tbody>
          ${rows.map(r => `
            <tr>
              <td><b>${escHtml(r.case_id)}</b></td>
              <td>${escHtml(r.name)}</td>
              <td>${escHtml(r.condition)}</td>
              <td>${escHtml(r.workflow || r.final_road || "—")}</td>
              <td>${escHtml(r.ticket_status || r.final_status || "—")}</td>
              <td>${r.reply_ms != null ? escHtml(r.reply_ms) : "—"}</td>
              <td>${r.domain_correct ? "✅" : "❌"}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    `;
  } catch (err) {
    status.textContent = `❌ Eval failed: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
};

/* ---------- keyboard ---------- */
window.addEventListener("keydown", (e) => {
  if (document.activeElement && ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
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
   Call lifecycle
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

async function startCallSession(opts = {}) {
  const fastConnect = !!opts.fastConnect;
  S.callId = makeCallId();
  S.callActive = true;
  S.callEnded = false;
  S.micDenied = false;
  S.latencyHistory = [];
  resetDeskUi();
  browserStopAudio();

  S.phase = fastConnect ? "connected" : "dialing";
  { const t = $("#call-timer"); if (t) t.textContent = "00:00"; }
  paintPhones();
  $("#btn-dial").disabled = true;
  $("#btn-end").disabled = false;
  setCallState(fastConnect ? "Agent connected…" : "Dialing 1-800-BIMA-VANI…");
  if ($("#stt-transcript")) $("#stt-transcript").textContent = "…";
  if ($("#agent-reply")) $("#agent-reply").textContent = "…";

  try {
    const myCall = ++S.callSeq;
    const stillOn = () => S.callActive && S.callSeq === myCall;

    const wsPromise = wsConnect(S.callId);

    if (!fastConnect) {
      ringStart();
      const micReady = initAudioGraph().catch(() => {});
      await sleep(650);
      if (!stillOn()) { wsClose(); return ringStop(); }
      S.phase = "ringing"; paintPhones(); setCallState("Ringing…");
      await Promise.all([sleep(1400), micReady]);
      if (!stillOn()) { wsClose(); return ringStop(); }
      ringStop(); pickupBlip();
      $("#agent-accept")?.classList.add("pressed");
      await sleep(280);
      if (!stillOn()) { wsClose(); return; }
      $("#agent-accept")?.classList.remove("pressed");
    } else {
      await Promise.all([initAudioGraph().catch(() => {}), wsPromise]);
    }

    S.phase = "connected"; startCallTimer(); paintPhones();
    if (REC.ctx && REC.ctx.state === "suspended") await REC.ctx.resume();
    await updateNoise();

    if (!S.micDenied && !fastConnect) {
      setCallState("Calibrating mic…");
      await calibrateAmbient(260);
    }

    setCallState("Agent connected — greeting…");
    S.turnSentAt = performance.now();
    await wsPromise;
    window.speechSynthesis?.cancel?.();

    const explicitStartLang = explicitSelectedLang();
    if (WS.open && WS.sock && WS.sock.readyState === 1) {
      await new Promise((resolve) => {
        WS.pendingTurnResolve = resolve;
        WS.sock.send(JSON.stringify({
          type: "start",
          language: explicitStartLang,
          stt_language: S.languagePref,
        }));
      });
      if ($("#stt-transcript")) $("#stt-transcript").textContent = "Waiting for you to speak…";
      if (!fastConnect) {
        if (S.mode === "auto_vad" && !S.micDenied) startVad();
        await waitForStreamingPlayback(S.lastTurnResult?.reply_text || "", S.lastTurnResult?.language || S.detectedLang);
      } else {
        browserStopAudio();
      }
    } else {
      const res = await fetch(API.start, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          call_id: S.callId,
          language: explicitStartLang,
          stt_language: S.languagePref,
        }),
      });
      const data = await res.json();
      if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
      S.lastTurnResult = data;

      decodeStages(data.pipeline, null);
      renderTurn({
        _isGreeting: true,
        language: data.language,
        transcript: "",
        reply_text: data.greeting,
        pipeline: data.pipeline,
        total_turn_ms: data.total_start_ms,
        ticket_status: data.ticket_status,
        stage: data.stage,
      });
      if ($("#stt-transcript")) $("#stt-transcript").textContent = "Waiting for you to speak…";

      if (!fastConnect) {
        const greet = playClausesFromPayload(data.clauses || [], data.greeting);
        if (S.mode === "auto_vad" && !S.micDenied) startVad();
        await greet;
      }
    }

    if (S.micDenied) {
      stopVad();
      setCallState("Typed mode — no microphone");
    } else if (S.mode === "push_to_talk") {
      stopVad();
      setCallState("🎙️ PUSH-TO-TALK — hold the orb to reply");
    } else {
      startVad();
    }
  } catch (e) {
    ringStop();
    stopVad();
    wsClose();
    S.callActive = false;
    S.phase = "idle";
    clearInterval(S.callTimer);
    $("#btn-dial").disabled = false;
    $("#btn-end").disabled = true;
    maybeEnableBarge();
    paintPhones();
    setCallState(`⚠️ ${e.message}`);
  }
}

$("#btn-dial").onclick = () => startCallSession({ fastConnect: false });

$("#btn-end").onclick = async () => {
  const endedId = S.callId;
  S.callActive = false;
  S.callEnded = true; S.callSeq++; ringStop(); S.phase = "ended";
  clearInterval(S.callTimer);
  $("#btn-dial").disabled = false;
  $("#btn-end").disabled = true;
  stopVad();
  stopNoiseSpeaker();
  browserStopAudio();
  if (WS.open && WS.sock && WS.sock.readyState === 1) {
    try { WS.sock.send(JSON.stringify({ type: "end_call" })); } catch {}
  }
  wsClose();
  maybeEnableBarge();
  setCallState("Call ended");
  paintPhones();
  try {
    await fetch(API.end, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ call_id: endedId }),
    });
  } catch {}
};

/* ---------- boot ---------- */
updateHero("Not connected");
$("#vad-window").textContent = S.vadSilenceMs;
$("#vad-sens-val").textContent = S.vadSens.toFixed(1) + "×";
syncModeUi();
refreshHealth();
setInterval(refreshHealth, 5000);
setInterval(paintOrb, 400);
