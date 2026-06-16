import { ICONS, mountIcons } from "./icons.js";
import { on, ready, fmt } from "./bridge.js";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

let API = null;
let SERVER_BASE = "";

const state = {
  segments: [],
  duration: 0,
  videoPath: null,
  projectId: null,
  activeSegmentIndex: -1,
};

const emotion = {
  raf: 0,
  analyzing: false,
  blocked: false,
  lastAnalysis: 0,
  lastFaces: [],
  lastFrameSize: { width: 0, height: 0 },
  offscreen: null,
  fpsCount: 0,
  fpsAt: 0,
  device: "CPU",
  threshold: 0.55,
};

const EMOTION_ANALYSIS_INTERVAL = 120;

ready().then(async (api) => {
  API = api;
  mountIcons();
  $("#railLogo").innerHTML = await fetchLogo();
  bindUI();
  bindBus();
  await refreshEnv();
  status("Откройте видео");
});

async function fetchLogo() {
  try { return await (await fetch("assets/logo.svg")).text(); }
  catch { return ICONS.captions; }
}

async function refreshEnv() {
  try {
    const e = await API.environment();
    SERVER_BASE = e.server_base || SERVER_BASE;
    flag("#envFf", e.ffmpeg);
    const d = e.device || {};
    flag("#envDev", true);
    $("#envDevTxt").textContent = d.has_cuda ? (d.gpu ? "GPU" : "CUDA") : "CPU";
    $("#envDev").title = d.has_cuda
      ? `${d.gpu || "GPU"} · распознавание речи может использовать видеокарту`
      : `CPU: ${d.cpu_count || "auto"} ядер`;

    const st = e.emotion || {};
    flag("#envEmotion", !!(st.installed && st.models_ready));
    $("#envEmotion").title = st.installed && st.models_ready
      ? `EmotionAI готов: ${(st.devices || ["CPU"]).join(", ")}`
      : "EmotionAI не готов: нужны OpenVINO и модели";
    emotion.device = (st.devices && st.devices.includes("GPU")) ? "GPU" : "CPU";
  } catch (_) {
    flag("#envEmotion", false);
  }
}

function flag(sel, ok) {
  const el = $(sel);
  if (!el) return;
  el.classList.toggle("ok", !!ok);
  el.classList.toggle("bad", !ok);
}

function bindUI() {
  $("#btnOpen").addEventListener("click", openVideo);
  $("#toastClose").addEventListener("click", () => $("#toast").classList.add("hidden"));

  const v = $("#video");
  $("#btnPlay").addEventListener("click", () => {
    if (!state.videoPath) return;
    v.paused ? v.play() : v.pause();
  });
  v.addEventListener("play", () => {
    $("#btnPlay").innerHTML = ICONS.pause;
    $("#fsPlay").innerHTML = ICONS.pause;
    startPlaybackTicker();
    startEmotionLoop();
  });
  v.addEventListener("pause", () => {
    $("#btnPlay").innerHTML = ICONS.play;
    $("#fsPlay").innerHTML = ICONS.play;
    stopPlaybackTicker();
    onTime();
  });
  v.addEventListener("ended", () => {
    stopPlaybackTicker();
    onTime();
  });
  v.addEventListener("timeupdate", onTime);
  v.addEventListener("seeking", () => {
    emotion.lastFaces = [];
    clearEmotionOverlay();
    onTime();
  });
  v.addEventListener("seeked", () => {
    emotion.lastAnalysis = 0;
    analyzeEmotionFrame();
    onTime();
  });
  v.addEventListener("loadedmetadata", () => {
    state.duration = v.duration || state.duration;
    $("#tcDur").textContent = fmt(state.duration);
    $("#fsDur").textContent = fmt(state.duration);
    updateVideoAspect();
    startEmotionLoop();
  });
  v.addEventListener("loadeddata", () => analyzeEmotionFrame());

  $("#seek").addEventListener("click", (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    v.currentTime = ((e.clientX - r.left) / r.width) * (state.duration || v.duration || 0);
  });
  $("#btnFullscreen").addEventListener("click", () => {
    const st = $("#stage");
    if (document.fullscreenElement) document.exitFullscreen();
    else st.requestFullscreen && st.requestFullscreen();
  });
  bindFullscreenPlayer();
  window.addEventListener("resize", () => drawEmotionOverlay());
}

function bindFullscreenPlayer() {
  const v = $("#video");
  const clickSeek = (el, e) => {
    const r = el.getBoundingClientRect();
    v.currentTime = ((e.clientX - r.left) / r.width) * (state.duration || v.duration || 0);
  };
  $("#fsPlay").addEventListener("click", () => (v.paused ? v.play() : v.pause()));
  $("#fsSeek").addEventListener("click", (e) => clickSeek(e.currentTarget, e));
  $("#fsMute").addEventListener("click", () => {
    v.muted = !v.muted;
    $("#fsMute").classList.toggle("active", v.muted);
  });
  $("#fsSpeed").addEventListener("change", (e) => { v.playbackRate = parseFloat(e.target.value) || 1; });
  $("#fsCaptions").addEventListener("click", () => {
    $("#liveCap").classList.toggle("hidden");
    $("#fsCaption").classList.toggle("hidden");
  });
  $("#fsExit").addEventListener("click", () => document.fullscreenElement && document.exitFullscreen());
  document.addEventListener("fullscreenchange", () => {
    $("#stage").classList.toggle("is-fullscreen", !!document.fullscreenElement);
    drawEmotionOverlay();
  });
  document.addEventListener("keydown", (e) => {
    if (!document.fullscreenElement) return;
    if (e.code === "Space") { e.preventDefault(); v.paused ? v.play() : v.pause(); }
    if (e.code === "ArrowLeft") v.currentTime = Math.max(0, v.currentTime - 5);
    if (e.code === "ArrowRight") v.currentTime = Math.min(v.duration || 0, v.currentTime + 5);
    if (e.code === "Escape" && document.fullscreenElement) document.exitFullscreen();
  });
}

async function openVideo() {
  const r = await API.pick_video();
  if (!r) return;
  await loadMedia(r.path, r.title, r.duration, r.id);
  state.segments = [];
  renderSegments();
  clearEmotionOverlay();
  emotion.blocked = false;
  emotion.lastFaces = [];
  status("Видео открыто. Запускаю распознавание речи...");
  API.process({ subtitles: true });
}

async function loadMedia(path, title, duration, id) {
  state.videoPath = path;
  state.duration = duration || 0;
  state.projectId = id || null;
  const uri = await API.media_uri(path);
  const v = $("#video");
  v.src = uri;
  v.classList.remove("hidden");
  $("#stageEmpty").classList.add("hidden");
  $("#tcDur").textContent = fmt(state.duration);
  $("#fsDur").textContent = fmt(state.duration);
  status("Загружено: " + title);
}

function bindBus() {
  on("stage", (d) => status(d.text));
  on("error", (d) => showError(d));
  on("process:start", (d) => {
    showProc(true);
    setSteps(d.steps || []);
    setProc(0, "Подготовка...", 0);
    progress(0);
    status("Обработка субтитров...");
  });
  on("process:progress", (d) => {
    $("#procError").classList.add("hidden");
    setProc(d.progress, d.label, d.eta);
    markStep(d.label);
    progress(d.progress);
  });
  on("process:warning", (d) => showWarning(d.message));
  on("subtitles:ready", (d) => {
    state.segments = d.segments || [];
    renderSegments();
  });
  on("process:done", (d) => {
    showProc(false);
    progressDone();
    state.segments = d.segments || state.segments || [];
    state.duration = d.duration || state.duration;
    renderSegments();
    status("Готово. Включите воспроизведение: эмоции будут выделяться поверх лиц.");
  });
}

function showProc(on) { $("#proc").classList.toggle("hidden", !on); }

function setSteps(steps) {
  $("#procSteps").innerHTML = steps.map((s) => `<span class="st" data-k="${s.key}">${escapeHtml(s.label)}</span>`).join("");
}

function markStep(label) {
  $$("#procSteps .st").forEach((el) => {
    el.classList.remove("active");
    if (el.textContent === label) el.classList.add("active");
  });
}

function setProc(frac, label, eta) {
  const pct = Math.round((frac || 0) * 100);
  $("#procPct").textContent = pct + "%";
  $("#procFill").style.width = pct + "%";
  if (label) $("#procLabel").textContent = label;
  $("#procEta").textContent = eta ? "осталось ~" + fmt(eta) : "";
}

function renderSegments() {
  const box = $("#transcript");
  if (!state.segments.length) {
    box.innerHTML = `<p class="muted pad">Субтитры появятся здесь после обработки.</p>`;
    $("#liveCap").innerHTML = "";
    return;
  }
  box.innerHTML = state.segments.map((s, i) => {
    const words = (s.words && s.words.length)
      ? s.words.map((w) => `<span class="w" data-s="${w.start}" data-e="${w.end}">${escapeHtml(w.word)}</span>`).join("")
      : escapeHtml(s.text);
    return `<div class="seg" data-i="${i}" data-s="${s.start}"><span class="t">${tc(s.start)}</span><div class="body"><span class="txt">${words}</span></div></div>`;
  }).join("");
  $$(".seg").forEach((el) => el.querySelector(".t").addEventListener("click", () => { $("#video").currentTime = +el.dataset.s; }));
}

let playbackTimer = 0;
function startPlaybackTicker() {
  stopPlaybackTicker();
  playbackTimer = window.setInterval(onTime, 80);
}

function stopPlaybackTicker() {
  if (playbackTimer) window.clearInterval(playbackTimer);
  playbackTimer = 0;
}

function onTime() {
  const v = $("#video");
  const t = v.currentTime || 0;
  $("#tcCur").textContent = fmt(t);
  $("#seekFill").style.width = (state.duration ? (t / state.duration) * 100 : 0) + "%";
  $("#fsCur").textContent = fmt(t);
  $("#fsDur").textContent = fmt(state.duration || v.duration || 0);
  $("#fsSeekFill").style.width = (state.duration ? (t / state.duration) * 100 : 0) + "%";

  let active = null;
  const segs = $$(".seg");
  for (const s of segs) {
    const seg = state.segments[+s.dataset.i];
    if (seg && t >= seg.start && t <= seg.end) { active = s; break; }
  }
  segs.forEach((s) => s.classList.toggle("active", s === active));
  if (active && !isInView(active)) active.scrollIntoView({ block: "center", behavior: "smooth" });
  if (active) {
    const seg = state.segments[+active.dataset.i];
    $("#liveCap").innerHTML = (seg.words || []).map((w) =>
      `<span class="w ${t >= w.start && t <= w.end ? "on" : ""}">${escapeHtml(w.word)}</span>`).join("") || escapeHtml(seg.text);
    $("#fsCaption").innerHTML = $("#liveCap").innerHTML;
    active.querySelectorAll(".w").forEach((w) => w.classList.toggle("on", t >= +w.dataset.s && t <= +w.dataset.e));
  } else if (state.segments.length) {
    $("#liveCap").innerHTML = "";
    $("#fsCaption").innerHTML = "";
  }
}

function updateVideoAspect() {
  const v = $("#video");
  const st = $("#stage");
  const ar = (v.videoWidth || 16) / Math.max(1, v.videoHeight || 9);
  st.dataset.aspect = ar < 0.8 ? "portrait" : (ar > 2.2 ? "ultrawide" : (ar > 1.25 ? "wide" : "square"));
  resizeEmotionOverlay();
}

function startEmotionLoop() {
  if (emotion.raf) return;
  emotion.fpsAt = performance.now();
  emotion.fpsCount = 0;
  const tick = (now) => {
    drawEmotionOverlay();
    const v = $("#video");
    if (!v.paused && !v.ended && !emotion.blocked && !emotion.analyzing && now - emotion.lastAnalysis >= EMOTION_ANALYSIS_INTERVAL) {
      emotion.lastAnalysis = now;
      analyzeEmotionFrame();
    }
    emotion.raf = requestAnimationFrame(tick);
  };
  emotion.raf = requestAnimationFrame(tick);
}

async function analyzeEmotionFrame() {
  const v = $("#video");
  if (!v.videoWidth || !v.videoHeight || !API.emotion_analyze_frame || emotion.blocked || emotion.analyzing) return;
  emotion.analyzing = true;
  try {
    const frame = captureVideoFrame(v);
    const result = await API.emotion_analyze_frame(frame, emotion.device, emotion.threshold);
    if (!result || !result.ok) {
      emotion.blocked = true;
      emotion.lastFaces = [];
      clearEmotionOverlay();
      showWarning((result && result.error) || "EmotionAI не смог обработать кадр.");
      return;
    }
    emotion.lastFaces = result.faces || [];
    emotion.lastFrameSize = { width: result.width || 0, height: result.height || 0 };
    updateEmotionFps();
    drawEmotionOverlay();
  } catch (err) {
    emotion.blocked = true;
    emotion.lastFaces = [];
    clearEmotionOverlay();
    showWarning(err.message || "EmotionAI не смог обработать кадр.");
  } finally {
    emotion.analyzing = false;
  }
}

function captureVideoFrame(video) {
  emotion.offscreen = emotion.offscreen || document.createElement("canvas");
  const maxW = 640;
  const scale = Math.min(1, maxW / video.videoWidth);
  emotion.offscreen.width = Math.max(1, Math.round(video.videoWidth * scale));
  emotion.offscreen.height = Math.max(1, Math.round(video.videoHeight * scale));
  const ctx = emotion.offscreen.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(video, 0, 0, emotion.offscreen.width, emotion.offscreen.height);
  return emotion.offscreen.toDataURL("image/jpeg", 0.72);
}

function resizeEmotionOverlay() {
  const canvas = $("#emotionOverlay");
  const stage = $("#stage");
  const ratio = window.devicePixelRatio || 1;
  const rect = stage.getBoundingClientRect();
  const width = Math.max(1, Math.round(rect.width * ratio));
  const height = Math.max(1, Math.round(rect.height * ratio));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  return { width, height, ratio };
}

function drawEmotionOverlay() {
  const v = $("#video");
  const canvas = $("#emotionOverlay");
  if (!canvas || !v.videoWidth || !v.videoHeight) return;
  const { width, height, ratio } = resizeEmotionOverlay();
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, width, height);
  const faces = emotion.lastFaces || [];
  if (!faces.length) return;

  const rawW = emotion.lastFrameSize.width || v.videoWidth;
  const rawH = emotion.lastFrameSize.height || v.videoHeight;
  const scale = Math.min(width / rawW, height / rawH);
  const drawW = rawW * scale;
  const drawH = rawH * scale;
  const ox = (width - drawW) / 2;
  const oy = (height - drawH) / 2;

  ctx.lineWidth = Math.max(2, 2 * ratio);
  ctx.font = `${12 * ratio}px system-ui, sans-serif`;

  faces.forEach((face) => {
    const [x1, y1, x2, y2] = face.box || [0, 0, 0, 0];
    const x = ox + x1 * scale;
    const y = oy + y1 * scale;
    const w = Math.max(1, (x2 - x1) * scale);
    const h = Math.max(1, (y2 - y1) * scale);
    const score = Math.round((face.emotion_score || face.confidence || 0) * 100);
    const label = `${face.label || "лицо"} ${score}%`;
    ctx.strokeStyle = "#BC9756";
    ctx.fillStyle = "rgba(188, 151, 86, .18)";
    ctx.strokeRect(x, y, w, h);
    ctx.fillRect(x, y, w, h);
    const textW = ctx.measureText(label).width + 14 * ratio;
    const textH = 22 * ratio;
    const ty = Math.max(0, y - textH);
    ctx.fillStyle = "rgba(12, 10, 7, .86)";
    ctx.fillRect(x, ty, textW, textH);
    ctx.fillStyle = "#F4E8D0";
    ctx.fillText(label, x + 7 * ratio, ty + 15 * ratio);
  });
}

function clearEmotionOverlay() {
  const canvas = $("#emotionOverlay");
  if (!canvas) return;
  canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
}

function updateEmotionFps() {
  emotion.fpsCount += 1;
  const now = performance.now();
  if (!emotion.fpsAt) emotion.fpsAt = now;
  if (now - emotion.fpsAt >= 1000) {
    const fps = Math.round(emotion.fpsCount * 1000 / (now - emotion.fpsAt));
    $("#modelInfo").textContent = `EmotionAI: ${fps} fps`;
    emotion.fpsAt = now;
    emotion.fpsCount = 0;
  }
}

function showError(d = {}) {
  const msg = humanError(d.message || d.raw || "Неизвестная ошибка");
  status("Ошибка: " + msg);
  $("#toastText").textContent = msg;
  $("#toast").classList.remove("hidden");
  if (!$("#proc").classList.contains("hidden")) {
    $("#procLabel").textContent = "Обработка остановлена";
    $("#procError").textContent = msg;
    $("#procError").classList.remove("hidden");
  }
  progressDone();
}

function showWarning(message) {
  const msg = humanError(message || "");
  status(msg);
  $("#toastText").textContent = msg;
  $("#toast").classList.remove("hidden");
}

function status(t) { $("#statusText").textContent = t; }
function progress(f) { const b = $("#progressBar"); b.classList.remove("hidden"); b.firstElementChild.style.width = Math.round(f * 100) + "%"; }
function progressDone() { setTimeout(() => $("#progressBar").classList.add("hidden"), 600); }
function tc(s) { return fmt(s); }
function isInView(el) {
  const parent = el.parentElement;
  if (!parent) return true;
  const r = el.getBoundingClientRect();
  const p = parent.getBoundingClientRect();
  return r.top >= p.top && r.bottom <= p.bottom;
}
function escapeHtml(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function compactError(s) { return String(s || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim().slice(0, 220) || "нет ответа"; }
function humanError(s) {
  const text = compactError(s);
  const low = text.toLowerCase();
  if (low.includes("cublas") || low.includes("cudnn") || low.includes("cuda")) {
    return "CUDA-библиотеки не загрузились. Распознавание будет выполнено на CPU; для GPU нужны совместимые CUDA/cuBLAS/cuDNN.";
  }
  if (low.includes("ffmpeg")) return "Не удалось прочитать аудио/видео через ffmpeg. Проверьте файл и установку ffmpeg.";
  if (low.includes("openvino")) return "EmotionAI недоступен: установите OpenVINO и проверьте модели в папке models.";
  return text;
}
