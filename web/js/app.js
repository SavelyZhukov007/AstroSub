import { ICONS, mountIcons } from "./icons.js";
import { on, ready, renderMarkdown, fmt } from "./bridge.js";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

let API = null;
let SERVER_BASE = "";
const state = {
  segments: [], duration: 0, videoPath: null, projectId: null,
  drawerBuf: "", summaryBuf: "", askBuf: "", meshOn: false,
  chat: [], chatBotBuf: "", recBuf: "",
  fidAnalysisBuf: "", lastFaceUid: null,
};

ready().then(async (api) => {
  API = api;
  mountIcons();
  $("#railLogo").innerHTML = await fetchLogo();
  renderFeatures();
  bindUI();
  bindBus();
  await refreshEnv();
  await refreshRecent();
  await maybeFirstRun();
});

async function fetchLogo() {
  try { return await (await fetch("assets/logo.svg")).text(); }
  catch { return ICONS.captions; }
}

/* ----------------------------------------------------- env / device */
async function refreshEnv() {
  try {
    const e = await API.environment();
    SERVER_BASE = e.server_base || SERVER_BASE;
    flag("#envFf", e.ffmpeg);
    flag("#envOl", e.ollama);
    flag("#envQ", !!e.qwen);
    $("#modelInfo").textContent = e.qwen ? "qwen: " + e.qwen : "";
    const d = e.device || {};
    flag("#envDev", true);
    $("#envDevTxt").textContent = d.has_cuda ? (d.gpu ? "GPU" : "CUDA") : (d.gpu_available ? "GPU setup" : "CPU");
    $("#envDev").title = d.has_cuda
      ? `${d.gpu || "GPU"} · тяжёлые задачи на видеокарте, ${d.cpu_workers} CPU-потоков`
      : d.gpu_setup_needed
        ? `${d.gpu || "GPU"} найдена, нужно установить CUDA-пакеты`
      : `CPU: ${d.cpu_count} ядер`;
  } catch (_) {}
}
function flag(sel, ok) { const el = $(sel); el.classList.toggle("ok", ok); el.classList.toggle("bad", !ok); }

/* ----------------------------------------------------- features grid */
const FEATURES = [
  ["mic", "Локальное ASR", "faster-whisper распознаёт речь офлайн, с таймкодами по словам."],
  ["spark", "Кнопка «Подробнее»", "Выделите слова — Qwen объяснит фрагмент."],
  ["brain", "Умный конспект", "Структурированный конспект по всей расшифровке."],
  ["faceid", "FaceID", "Запись лица, разбор геометрии, пол/возраст, вывод Qwen."],
  ["chat", "Чат с Qwen", "Диалог с моделью и обсуждение обработанного видео, голосовые."],
  ["faces", "Спикеры", "ArcFace + кластеризация: один человек = один ID, имена сохраняются."],
  ["chip", "GPU/CPU", "Тяжёлые задачи — на видеокарту, остальное параллельно на CPU."],
  ["cards", "Сервисы", "Карточки для запоминания, сравнение лиц, галерея известных лиц."],
];
function renderFeatures() {
  $("#featureGrid").innerHTML = FEATURES.map(
    ([ic, t, d]) => `<div class="feature"><div class="fi">${ICONS[ic] || ""}</div><h4>${t}</h4><p>${d}</p></div>`
  ).join("");
}
async function refreshRecent() {
  const list = await API.list_projects();
  const box = $("#recentList");
  if (!list.length) { box.innerHTML = `<p class="muted">Пока пусто. Откройте первое видео.</p>`; return; }
  box.innerHTML = list.map((p) =>
    `<div class="item" data-pid="${p.id}"><span class="ti">${escapeHtml(p.title)}</span><span class="grow"></span><span class="meta">${fmt(p.duration)}</span></div>`).join("");
  $$(".recent .item").forEach((el) => el.addEventListener("click", () => openProject(el.dataset.pid)));
}

/* ----------------------------------------------------- UI bindings */
function bindUI() {
  $("#btnOpen").addEventListener("click", openVideo);
  $("#btnOpen2").addEventListener("click", openVideo);
  $("#btnReprocess").addEventListener("click", () => openProcModal());
  $("#btnSummary").addEventListener("click", () => { state.summaryBuf = ""; API.make_summary("тезисы"); });
  $("#btnGlossary").addEventListener("click", () => API.make_glossary());
  $("#btnChapters").addEventListener("click", () => API.make_chapters());
  $("#btnExport").addEventListener("click", exportMenu);

  $$(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.pane)));

  $$(".rail-btn[data-view]").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));

  // плеер
  const v = $("#video");
  $("#btnPlay").addEventListener("click", () => (v.paused ? v.play() : v.pause()));
  v.addEventListener("play", () => ($("#btnPlay").innerHTML = ICONS.pause));
  v.addEventListener("pause", () => ($("#btnPlay").innerHTML = ICONS.play));
  v.addEventListener("timeupdate", onTime);
  v.addEventListener("loadedmetadata", () => { state.duration = v.duration; $("#tcDur").textContent = fmt(v.duration); });
  $("#seek").addEventListener("click", (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    v.currentTime = ((e.clientX - r.left) / r.width) * (state.duration || v.duration || 0);
  });
  $("#btnMeshToggle").addEventListener("click", toggleMesh);
  $("#btnFullscreen").addEventListener("click", () => {
    const st = $("#stage");
    if (document.fullscreenElement) document.exitFullscreen();
    else st.requestFullscreen && st.requestFullscreen();
  });

  bindSplitter();

  // выделение -> «Подробнее»
  document.addEventListener("mouseup", onSelection);
  $("#explainPop").addEventListener("mousedown", (e) => { e.preventDefault(); doExplain(); });
  $("#drawerClose").addEventListener("click", () => $("#drawer").classList.remove("open"));

  $("#searchInput").addEventListener("input", debounce(doSearch, 180));
  $("#askInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.value.trim()) { state.askBuf = ""; API.ask(e.target.value.trim()); }
  });

  // настройки + модалы
  $("#btnSettings").addEventListener("click", openSettings);
  $("#settingsClose").addEventListener("click", closeSettings);
  $("#settingsCancel").addEventListener("click", closeSettings);
  $("#settingsSave").addEventListener("click", saveSettings);
  $("#envDev").addEventListener("click", openModels);

  // модал задач
  $("#procModalClose").addEventListener("click", () => $("#procOverlay").classList.remove("open"));
  $("#procJustOpen").addEventListener("click", () => $("#procOverlay").classList.remove("open"));
  $("#procStart").addEventListener("click", startProcessing);

  // установка
  $("#installSkip").addEventListener("click", finishFirstRun);
  $("#installRun").addEventListener("click", runInstall);

  // модели
  $("#modelsClose").addEventListener("click", () => $("#modelsOverlay").classList.remove("open"));
  $("#modelsDone").addEventListener("click", () => $("#modelsOverlay").classList.remove("open"));
  $("#pullBtn").addEventListener("click", () => { const n = $("#pullName").value.trim(); if (n) API.pull_model(n); });

  bindChat();
  bindFaceId();
  bindLab();
}

function showView(view) {
  $$(".rail-btn").forEach((x) => x.classList.toggle("active", x.dataset.view === view));
  const map = { editor: "#viewEditor", welcome: "#viewWelcome", chat: "#viewChat", faceid: "#viewFaceid", lab: "#viewLab" };
  Object.entries(map).forEach(([k, sel]) => $(sel).classList.toggle("hidden", k !== view));
  if (view === "welcome") refreshRecent();
  if (view === "chat") populateChatAttach();
  if (view === "faceid") refreshFidList();
  if (view === "lab") { refreshKnown(); populateCompare(); }
}

/* ----------------------------------------------------- splitter */
function bindSplitter() {
  const sp = $("#splitter"), ed = $("#viewEditor");
  let drag = false;
  sp.addEventListener("mousedown", () => { drag = true; sp.classList.add("drag"); document.body.style.cursor = "col-resize"; });
  document.addEventListener("mouseup", () => {
    if (!drag) return; drag = false; sp.classList.remove("drag"); document.body.style.cursor = "";
    const pct = parseFloat(getComputedStyle(ed).getPropertyValue("--vw"));
    API.update_settings({ split_ratio: (pct || 56) / 100 });
  });
  document.addEventListener("mousemove", (e) => {
    if (!drag) return;
    const r = ed.getBoundingClientRect();
    let pct = ((e.clientX - r.left) / r.width) * 100;
    pct = Math.max(30, Math.min(75, pct));
    ed.style.setProperty("--vw", pct + "%");
    resizeMesh();
  });
}

/* ----------------------------------------------------- open / process */
async function openVideo() {
  const r = await API.pick_video();
  if (!r) return;
  await loadMedia(r.path, r.title, r.duration, r.id);
  state.segments = [];
  renderSegments();
  openProcModal();
}
async function openProject(pid) {
  const p = await API.open_project(pid);
  await loadMedia(p.video_path, p.title, p.duration, p.id);
  state.segments = p.segments || [];
  renderSegments();
  if (p.summary) $("#summaryOut").innerHTML = renderMarkdown(p.summary);
  if (p.glossary && p.glossary.length) renderGlossary(p.glossary);
  if (p.chapters && p.chapters.length) renderChapters(p.chapters);
  if (p.persons && p.persons.length) renderPersons(p.persons);
  if (state.segments.length) enableTools();
}
async function loadMedia(path, title, duration, id) {
  state.videoPath = path; state.duration = duration || 0; state.projectId = id || null;
  const uri = await API.media_uri(path);
  const v = $("#video");
  v.src = uri; v.classList.remove("hidden");
  $("#stageEmpty").classList.add("hidden");
  $("#btnReprocess").disabled = false;
  $("#tcDur").textContent = fmt(state.duration);
  showView("editor");
  status("Загружено: " + title);
}

const PROC_OPTS = [
  ["subtitles", "captions", "Субтитры", "Распознавание речи с таймкодами", true],
  ["speakers", "faces", "Спикеры", "Идентификация лиц и привязка к репликам", true],
  ["summary", "brain", "Конспект", "Структурированный конспект (Qwen)", false],
  ["glossary", "glossary", "Глоссарий", "Ключевые термины с определениями", false],
  ["chapters", "chapters", "Главы", "Разбивка на смысловые главы", false],
  ["translate", "translate", "Перевод", "Перевести субтитры на ваш язык", false],
];
function openProcModal() {
  $("#optGrid").innerHTML = PROC_OPTS.map(([k, ic, t, d, on]) =>
    `<div class="opt ${on ? "on" : ""}" data-k="${k}">
       <div class="chk">${ICONS.check}</div>
       <div><h4>${ICONS[ic] ? "" : ""}${t}</h4><p>${d}</p></div>
     </div>`).join("");
  $$("#optGrid .opt").forEach((o) => o.addEventListener("click", () => o.classList.toggle("on")));
  $("#procOverlay").classList.add("open");
}
function startProcessing() {
  const opt = {};
  $$("#optGrid .opt").forEach((o) => { opt[o.dataset.k] = o.classList.contains("on"); });
  $("#procOverlay").classList.remove("open");
  API.process(opt);
}

/* ----------------------------------------------------- processing overlay */
function showProc(on) { $("#proc").classList.toggle("hidden", !on); }
function setSteps(steps) {
  $("#procSteps").innerHTML = steps.map((s) => `<span class="st" data-k="${s.key}">${s.label}</span>`).join("");
}
function markStep(label) {
  $$("#procSteps .st").forEach((el) => {
    el.classList.remove("active");
    if (el.textContent === label) el.classList.add("active");
  });
}

/* ----------------------------------------------------- event bus */
function bindBus() {
  on("stage", (d) => status(d.text));
  on("error", (d) => { status("Ошибка: " + d.message); showProc(false); });

  // единый конвейер
  on("process:start", (d) => { showProc(true); setSteps(d.steps || []); setProc(0, "Подготовка…", 0); progress(0); status("Обработка…"); });
  on("process:progress", (d) => { setProc(d.progress, d.label, d.eta); markStep(d.label); progress(d.progress); });
  on("process:done", (d) => {
    showProc(false); progressDone(); status("Готово");
    state.segments = d.segments || []; state.duration = d.duration || state.duration;
    renderSegments();
    if (d.persons && d.persons.length) renderPersons(d.persons);
    if (d.summary) $("#summaryOut").innerHTML = renderMarkdown(d.summary);
    if (d.glossary && d.glossary.length) renderGlossary(d.glossary);
    if (d.chapters && d.chapters.length) renderChapters(d.chapters);
    enableTools();
  });
  on("subtitles:ready", (d) => { state.segments = d.segments || []; renderSegments(); });
  on("speakers:ready", (d) => { renderPersons(d.persons); });

  on("summary:start", () => { status("Qwen строит конспект..."); $("#summaryOut").innerHTML = `<span class="spinner"></span>`; state.summaryBuf = ""; });
  on("summary:token", (d) => { state.summaryBuf += d.token; $("#summaryOut").innerHTML = renderMarkdown(state.summaryBuf); });
  on("summary:done", (d) => {
    if (d && d.summary) $("#summaryOut").innerHTML = renderMarkdown(d.summary);
    else if (!state.summaryBuf) $("#summaryOut").innerHTML = `<p class="muted">Ollama не запущен.</p>`;
    status("Конспект готов");
  });

  on("glossary:start", () => { $("#glossaryOut").innerHTML = `<span class="spinner"></span>`; });
  on("glossary:done", (d) => { renderGlossary(d.glossary); });
  on("chapters:start", () => { $("#chaptersOut").innerHTML = `<span class="spinner"></span>`; });
  on("chapters:done", (d) => { renderChapters(d.chapters); });

  on("explain:start", (d) => { state.drawerBuf = ""; openDrawer("Подробнее", d.selection); });
  on("explain:token", (d) => { state.drawerBuf += d.token; $("#drawerOut").innerHTML = renderMarkdown(state.drawerBuf); });
  on("explain:done", () => status("Готов"));
  on("explain:error", (d) => { openDrawer("Подробнее", ""); $("#drawerOut").innerHTML = `<p class="muted">${d.message}. Запустите Ollama и модель qwen.</p>`; });

  on("ask:start", () => { status("Qwen отвечает..."); $("#askOut").innerHTML = `<span class="spinner"></span>`; state.askBuf = ""; });
  on("ask:token", (d) => { state.askBuf += d.token; $("#askOut").innerHTML = renderMarkdown(state.askBuf); });
  on("ask:done", () => status("Готов"));

  // чат
  on("chat:start", () => { state.chatBotBuf = ""; pushBot(""); });
  on("chat:token", (d) => { state.chatBotBuf += d.token; updateLastBot(renderMarkdown(state.chatBotBuf)); });
  on("chat:done", () => { state.chat.push({ role: "assistant", content: state.chatBotBuf }); status("Готов"); });
  on("chat:error", (d) => updateLastBot(`<p class="muted">${d.message}.</p>`));

  on("voice:start", () => status("Расшифровка голосового..."));
  on("voice:done", (d) => onVoiceText(d.text));

  // faceid
  on("faceid:start", () => { fidProg(0); $("#fidResult").innerHTML = ""; });
  on("faceid:progress", (d) => { fidProg(d.progress); $("#fidPrompt").textContent = d.text; });
  on("faceid:done", (d) => onFaceEnrolled(d));
  on("faceid:analysis_start", () => { state.fidAnalysisBuf = ""; $("#fidAnalysis") && ($("#fidAnalysis").innerHTML = `<span class="spinner"></span>`); });
  on("faceid:analysis_token", (d) => { state.fidAnalysisBuf += d.token; const el = $("#fidAnalysis"); if (el) el.innerHTML = renderMarkdown(state.fidAnalysisBuf); });
  on("faceid:analysis_done", () => status("Разбор готов"));
  on("faceid:analysis_error", (d) => { const el = $("#fidAnalysis"); if (el) el.innerHTML = `<p class="muted">${d.message}.</p>`; status(d.message); });

  // модели / установка
  on("pull:start", () => { $("#pullProgress").classList.remove("hidden"); $("#pullStatus").textContent = "Загрузка…"; });
  on("pull:progress", (d) => { $("#pullFill").style.width = Math.round(d.progress * 100) + "%"; $("#pullStatus").textContent = d.text; });
  on("pull:done", async (d) => { $("#pullStatus").textContent = d.ok ? "Готово" : ("Ошибка: " + d.error); await openModels(); refreshEnv(); });

  on("install:start", () => { $("#installProgress").classList.remove("hidden"); });
  on("install:progress", (d) => { $("#installFill").style.width = Math.round(d.progress * 100) + "%"; $("#installStatus").textContent = d.text; });
  on("install:done", (d) => {
    if (d && d.ok === false) {
      const failed = (d.failed || []).map((x) => x.package).join(", ");
      $("#installStatus").textContent = failed ? `Не удалось установить: ${failed}` : "Установка завершилась с ошибкой.";
      refreshEnv();
      return;
    }
    $("#installStatus").textContent = "Готово. Перезапустите при необходимости.";
    setTimeout(finishFirstRun, 1200);
    refreshEnv();
  });

  // lab
  on("flashcards:start", () => { $("#cardsOut").innerHTML = `<span class="spinner"></span>`; });
  on("flashcards:done", (d) => renderFlashcards(d.cards));
}

function setProc(frac, label, eta) {
  const pct = Math.round((frac || 0) * 100);
  $("#procPct").textContent = pct + "%";
  $("#procFill").style.width = pct + "%";
  if (label) $("#procLabel").textContent = label;
  $("#procEta").textContent = eta ? "осталось ~" + fmt(eta) : "";
}

/* ----------------------------------------------------- segments / time */
function renderSegments() {
  const box = $("#transcript");
  if (!state.segments.length) { box.innerHTML = `<p class="muted pad">Нет субтитров.</p>`; return; }
  box.innerHTML = state.segments.map((s, i) => {
    const words = (s.words && s.words.length)
      ? s.words.map((w) => `<span class="w" data-s="${w.start}" data-e="${w.end}">${escapeHtml(w.word)}</span>`).join("")
      : escapeHtml(s.text);
    const spk = s.speaker != null ? `<span class="spk">${escapeHtml(speakerName(s.speaker))}</span>` : "";
    return `<div class="seg" data-i="${i}" data-s="${s.start}"><span class="t">${tc(s.start)}</span><div class="body">${spk}<span class="txt">${words}</span></div></div>`;
  }).join("");
  $$(".seg").forEach((el) => el.querySelector(".t").addEventListener("click", () => { $("#video").currentTime = +el.dataset.s; }));
}
let _persons = [];
function speakerName(id) { const p = _persons.find((x) => x.id === id); return p ? p.label : "Спикер " + (id + 1); }

function onTime() {
  const t = $("#video").currentTime;
  $("#tcCur").textContent = fmt(t);
  $("#seekFill").style.width = (state.duration ? (t / state.duration) * 100 : 0) + "%";
  let active = null;
  const segs = $$(".seg");
  for (const s of segs) { const seg = state.segments[+s.dataset.i]; if (seg && t >= seg.start && t <= seg.end) { active = s; break; } }
  segs.forEach((s) => s.classList.toggle("active", s === active));
  if (active && !isInView(active)) active.scrollIntoView({ block: "center", behavior: "smooth" });
  if (active) {
    const seg = state.segments[+active.dataset.i];
    $("#liveCap").innerHTML = (seg.words || []).map((w) =>
      `<span class="w ${t >= w.start && t <= w.end ? "on" : ""}">${escapeHtml(w.word)}</span>`).join("") || escapeHtml(seg.text);
    active.querySelectorAll(".w").forEach((w) => w.classList.toggle("on", t >= +w.dataset.s && t <= +w.dataset.e));
  }
  if (state.meshOn) drawMesh(t);
}

/* ----------------------------------------------------- explain */
let lastSel = null;
function onSelection() {
  const sel = window.getSelection();
  const text = sel.toString().trim();
  const pop = $("#explainPop");
  if (!text || text.length < 2) { pop.style.display = "none"; lastSel = null; return; }
  const node = sel.anchorNode && (sel.anchorNode.nodeType === 3 ? sel.anchorNode.parentElement : sel.anchorNode);
  if (!node || !node.closest(".txt, .md")) { pop.style.display = "none"; return; }
  const rect = sel.getRangeAt(0).getBoundingClientRect();
  lastSel = { text, context: contextFor(node) };
  pop.style.left = rect.left + rect.width / 2 + "px";
  pop.style.top = rect.top + "px";
  pop.style.display = "flex";
}
function contextFor(node) {
  const seg = node.closest(".seg");
  if (!seg) return node.textContent.slice(0, 400);
  const i = +seg.dataset.i;
  return [state.segments[i - 1], state.segments[i], state.segments[i + 1]].filter(Boolean).map((s) => s.text).join(" ");
}
function doExplain() { if (!lastSel) return; $("#explainPop").style.display = "none"; API.explain(lastSel.text, lastSel.context); }
function openDrawer(title, sel) {
  $("#drawerTitle").textContent = title;
  $("#drawerSel").textContent = sel || "";
  $("#drawerSel").style.display = sel ? "block" : "none";
  $("#drawerOut").innerHTML = `<span class="spinner"></span>`;
  $("#drawer").classList.add("open");
}

/* ----------------------------------------------------- persons / glossary / chapters */
function renderPersons(persons) {
  _persons = persons || [];
  const box = $("#personsOut");
  if (!persons || !persons.length) { box.innerHTML = `<p class="muted">Лица не найдены.</p>`; return; }
  box.innerHTML = persons.map((p) => {
    const ag = [p.gender, p.age ? Math.round(p.age) + " лет" : null].filter(Boolean).join(", ");
    const badge = p.known ? `<span class="badge">узнан</span>` : "";
    return `<div class="person">
       <img class="av" src="${toFileUri(p.thumb)}" onerror="this.style.opacity=.2"/>
       <div><input class="nm" value="${escapeAttr(p.label)}" data-pid="${p.id}"/>${badge}
         <div class="meta">появлений: ${p.count} · ${tc(p.first_t)}–${tc(p.last_t)}${ag ? " · " + ag : ""}</div></div>
     </div>`;
  }).join("");
  $$(".person .nm").forEach((inp) => inp.addEventListener("change", () => API.rename_person(+inp.dataset.pid, inp.value)));
}
function renderGlossary(items) {
  const box = $("#glossaryOut");
  if (!items || !items.length) { box.innerHTML = `<p class="muted">Термины не найдены.</p>`; return; }
  box.innerHTML = items.map((g) => `<div class="term"><b>${escapeHtml(g.term)}</b> — ${escapeHtml(g.definition)}</div>`).join("");
}
function renderChapters(items) {
  const box = $("#chaptersOut");
  if (!items || !items.length) { box.innerHTML = `<p class="muted">Главы не определены.</p>`; return; }
  box.innerHTML = items.map((c) => `<div class="chapter" data-t="${c.time}"><span class="cn">${tc(c.time)}</span><span class="ct">${escapeHtml(c.title)}</span></div>`).join("");
  $$(".chapter").forEach((el) => el.addEventListener("click", () => { $("#video").currentTime = +el.dataset.t; }));
}

/* ----------------------------------------------------- search */
async function doSearch(e) {
  const hits = await API.search(e.target.value);
  $("#searchOut").innerHTML = hits.length
    ? hits.map((h) => `<div class="hit" data-t="${h.t}"><span class="ht">${tc(h.t)}</span> <span class="hx">${escapeHtml(h.text)}</span></div>`).join("")
    : `<p class="muted">Ничего не найдено.</p>`;
  $$(".hit").forEach((el) => el.addEventListener("click", () => { $("#video").currentTime = +el.dataset.t; }));
}

/* ----------------------------------------------------- mesh */
async function toggleMesh() {
  state.meshOn = !state.meshOn;
  $("#mesh").classList.toggle("hidden", !state.meshOn);
  $("#btnMeshToggle").style.color = state.meshOn ? "var(--accent)" : "";
  if (state.meshOn) drawMesh($("#video").currentTime);
}
function resizeMesh() { const v = $("#video"), cv = $("#mesh"); if (cv && v) { cv.width = v.clientWidth; cv.height = v.clientHeight; } }
let meshBusy = false;
async function drawMesh(t) {
  if (meshBusy) return; meshBusy = true;
  try {
    const faces = await API.face_landmarks(t);
    const v = $("#video"), cv = $("#mesh"), ctx = cv.getContext("2d");
    cv.width = v.clientWidth; cv.height = v.clientHeight;
    ctx.clearRect(0, 0, cv.width, cv.height);
    if (!faces || !faces.length || !v.videoWidth) return;
    const sc = Math.min(cv.width / v.videoWidth, cv.height / v.videoHeight);
    const ox = (cv.width - v.videoWidth * sc) / 2, oy = (cv.height - v.videoHeight * sc) / 2;
    ctx.fillStyle = "rgba(232,162,74,0.85)";
    for (const pts of faces) for (const [x, y] of pts) { ctx.beginPath(); ctx.arc(ox + x * sc, oy + y * sc, 0.9, 0, 7); ctx.fill(); }
  } finally { meshBusy = false; }
}

/* ----------------------------------------------------- chat */
function bindChat() {
  const ta = $("#chatText");
  ta.addEventListener("input", () => { ta.style.height = "auto"; ta.style.height = Math.min(140, ta.scrollHeight) + "px"; });
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); } });
  $("#chatSend").addEventListener("click", sendChat);
  $("#chatClear").addEventListener("click", () => { state.chat = []; $("#chatBody").innerHTML = ""; });
  $("#chatMic").addEventListener("click", toggleRec);
}
async function populateChatAttach() {
  const list = await API.list_projects();
  $("#chatAttach").innerHTML = `<option value="">Без привязки</option>` +
    list.filter((p) => p.has_segments).map((p) => `<option value="${p.id}">${escapeHtml(p.title)}</option>`).join("");
}
function sendChat() {
  const ta = $("#chatText"); const text = ta.value.trim();
  if (!text) return;
  pushUser(text);
  state.chat.push({ role: "user", content: text });
  ta.value = ""; ta.style.height = "auto";
  API.chat_send(state.chat, $("#chatAttach").value || null);
}
function pushUser(text) { addMsg("user", `<div class="bubble">${escapeHtml(text).replace(/\n/g, "<br>")}</div>`); }
function pushBot(html) { addMsg("bot", `<div class="bubble">${html || '<span class="spinner"></span>'}</div>`); }
function updateLastBot(html) { const b = $$("#chatBody .msg.bot"); if (b.length) b[b.length - 1].querySelector(".bubble").innerHTML = html; }
function addMsg(role, inner) {
  const hello = $(".chat-hello"); if (hello) hello.remove();
  const d = document.createElement("div"); d.className = "msg " + role; d.innerHTML = inner;
  $("#chatBody").appendChild(d); $("#chatBody").scrollTop = $("#chatBody").scrollHeight;
}

/* голосовое сообщение */
let mediaRec = null, recChunks = [], audioCtx = null, analyser = null, rafId = 0, recStream = null;
async function toggleRec() {
  if (mediaRec && mediaRec.state === "recording") { stopRec(); return; }
  try {
    recStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) { status("Нет доступа к микрофону"); return; }
  recChunks = [];
  mediaRec = new MediaRecorder(recStream);
  mediaRec.ondataavailable = (e) => { if (e.data.size) recChunks.push(e.data); };
  mediaRec.onstop = onRecStop;
  mediaRec.start();
  $("#chatMic").classList.add("rec");
  $("#micViz").classList.remove("hidden");
  startViz(recStream);
}
function stopRec() {
  if (mediaRec) mediaRec.stop();
  $("#chatMic").classList.remove("rec");
  $("#micViz").classList.add("hidden");
  cancelAnimationFrame(rafId);
  if (audioCtx) { audioCtx.close(); audioCtx = null; }
  if (recStream) recStream.getTracks().forEach((t) => t.stop());
}
function startViz(stream) {
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const src = audioCtx.createMediaStreamSource(stream);
  analyser = audioCtx.createAnalyser(); analyser.fftSize = 64;
  src.connect(analyser);
  const bars = $$("#micViz span");
  const data = new Uint8Array(analyser.frequencyBinCount);
  const tick = () => {
    analyser.getByteFrequencyData(data);
    bars.forEach((b, i) => { const v = data[i * 2] || 0; b.style.height = 6 + (v / 255) * 26 + "px"; });
    rafId = requestAnimationFrame(tick);
  };
  tick();
}
function onRecStop() {
  const blob = new Blob(recChunks, { type: "audio/webm" });
  const fr = new FileReader();
  fr.onload = () => { state.recBuf = fr.result; API.transcribe_voice(fr.result); };
  fr.readAsDataURL(blob);
}
function onVoiceText(text) {
  if (!text) { status("Пустое голосовое"); return; }
  // телеграм-стиль: пузырь голосового + расшифровка по кнопке
  const wave = Array.from({ length: 22 }, () => `<i style="height:${4 + Math.random() * 16 | 0}px"></i>`).join("");
  const id = "vt" + Date.now();
  addMsg("user", `<div class="bubble voice">${ICONS.mic}<div class="voice-wave">${wave}</div>
     <span class="voice-tr" data-t="${id}">текст</span></div>`);
  const last = $$("#chatBody .msg.user").pop();
  const trText = document.createElement("div"); trText.className = "voice-tr-text"; trText.style.display = "none";
  trText.textContent = text; last.querySelector(".bubble").appendChild(trText);
  last.querySelector(".voice-tr").addEventListener("click", () => {
    trText.style.display = trText.style.display === "none" ? "block" : "none";
  });
  state.chat.push({ role: "user", content: text });
  API.chat_send(state.chat, $("#chatAttach").value || null);
}

/* ----------------------------------------------------- FaceID */
let fidStream = null, fidFrames = [];
const fidActions = [
  ["front", "Смотрите прямо"],
  ["turn_left", "Поверните голову влево"],
  ["turn_right", "Поверните голову вправо"],
  ["chin_up", "Поднимите подбородок"],
  ["chin_down", "Опустите подбородок"],
  ["smile", "Улыбнитесь"],
];
function bindFaceId() {
  $("#fidStartCam").addEventListener("click", startFidCam);
  $("#fidRecord").addEventListener("click", recordFace);
}
async function startFidCam() {
  try {
    fidStream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 }, audio: false });
    $("#fidCam").srcObject = fidStream;
    $("#fidRecord").disabled = false;
    $("#fidPrompt").textContent = "Камера включена. Нажмите «Записать».";
  } catch (e) { $("#fidPrompt").textContent = "Нет доступа к камере."; }
}
async function recordFace() {
  if (!fidStream) return;
  $("#fidRecord").disabled = true;
  $("#fidRing").classList.remove("hidden");
  fidFrames = [];
  const v = $("#fidCam");
  const cv = document.createElement("canvas"); cv.width = 640; cv.height = 480;
  const ctx = cv.getContext("2d");
  const shots = 18;
  for (let i = 0; i < shots; i++) {
    const [action, prompt] = fidActions[Math.floor(i / shots * fidActions.length)] || fidActions[0];
    $("#fidPrompt").textContent = prompt || "Держите лицо в кадре";
    ctx.drawImage(v, 0, 0, cv.width, cv.height);
    fidFrames.push({ image: cv.toDataURL("image/jpeg", 0.85), action, prompt });
    await sleep(180);
  }
  $("#fidRing").classList.add("hidden");
  $("#fidPrompt").textContent = "Обработка…";
  API.faceid_enroll(fidFrames, $("#fidLabel").value.trim());
}
function fidProg(frac) { $("#fidProgress").classList.remove("hidden"); $("#fidProgFill").style.width = Math.round(frac * 100) + "%"; }
function onFaceEnrolled(d) {
  $("#fidRecord").disabled = false;
  $("#fidPrompt").textContent = d.ok ? "Готово" : (d.error || "Не удалось");
  if (!d.ok) { $("#fidResult").innerHTML = `<p class="muted">${d.error || "Лицо не распознано."}</p>`; return; }
  state.lastFaceUid = d.uid;
  $("#fidResult").innerHTML = `
    <div class="fid-card">
      <img src="${toFileUri(d.thumb)}" onerror="this.style.opacity=.2"/>
      <div>
        <div class="kv">профиль: <b>${escapeHtml(d.label)}</b></div>
        <div class="kv">пол: <b>${d.gender || "—"}</b> · возраст: <b>${d.age != null ? Math.round(d.age) : "—"}</b></div>
        <div class="kv">кадров: <b>${d.frames_total}</b> · качество: <b>${d.quality}</b></div>
        <button class="btn" id="fidAnalyzeBtn" style="margin-top:8px;"><span data-icon="spark"></span>Разбор Qwen</button>
      </div>
    </div>
    <div class="md" id="fidAnalysis" style="margin-top:14px;"></div>`;
  mountIcons($("#fidResult"));
  $("#fidAnalyzeBtn").addEventListener("click", () => API.faceid_analyze(d.uid));
  refreshFidList();
}
async function refreshFidList() {
  const list = await API.faceid_list();
  const box = $("#fidList");
  if (!list.length) { box.innerHTML = `<p class="muted">Пока нет профилей.</p>`; return; }
  box.innerHTML = list.map((p) =>
    `<div class="fid-tile">
       <img src="${toFileUri(p.thumb)}" onerror="this.style.opacity=.2"/>
       <div class="nm">${escapeHtml(p.label)}</div>
       <div class="meta">${p.gender || "—"} · ${p.age != null ? Math.round(p.age) : "—"} · ${p.frames_total} к.</div>
       <div class="acts">
         <div class="iconbtn" data-an="${p.uid}" title="Разбор">${ICONS.spark}</div>
         <div class="iconbtn" data-del="${p.uid}" title="Удалить">${ICONS.trash}</div>
       </div>
     </div>`).join("");
  $$("#fidList [data-an]").forEach((b) => b.addEventListener("click", () => API.faceid_analyze(b.dataset.an)));
  $$("#fidList [data-del]").forEach((b) => b.addEventListener("click", async () => { await API.faceid_delete(b.dataset.del); refreshFidList(); populateCompare(); }));
}

/* ----------------------------------------------------- LAB */
function bindLab() {
  $("#labCards").addEventListener("click", () => API.make_flashcards());
  $("#labCompare").addEventListener("click", doCompare);
}
function renderFlashcards(cards) {
  const box = $("#cardsOut");
  if (!cards || !cards.length) { box.innerHTML = `<p class="muted">Нет карточек. Откройте и обработайте видео.</p>`; return; }
  box.innerHTML = cards.map((c) => `<div class="flash"><div class="q">${escapeHtml(c.q || "")}</div><div class="a">${escapeHtml(c.a || "")}</div></div>`).join("");
  $$("#cardsOut .flash").forEach((el) => el.addEventListener("click", () => el.classList.toggle("open")));
}
async function populateCompare() {
  const list = await API.faceid_list();
  const opts = list.map((p) => `<option value="${p.uid}">${escapeHtml(p.label)}</option>`).join("");
  $("#cmpA").innerHTML = opts; $("#cmpB").innerHTML = opts;
}
async function doCompare() {
  const a = $("#cmpA").value, b = $("#cmpB").value;
  if (!a || !b) { $("#cmpOut").innerHTML = `<p class="muted">Нужно два профиля.</p>`; return; }
  const r = await API.face_compare(a, b);
  if (!r.ok) { $("#cmpOut").innerHTML = `<p class="muted">${r.error}</p>`; return; }
  $("#cmpOut").innerHTML = `<div class="cmp-res ${r.same ? "same" : "diff"}">сходство: ${r.similarity} — ${r.same ? "вероятно один человек" : "разные люди"}</div>`;
}
async function refreshKnown() {
  const list = await API.known_faces();
  const box = $("#knownOut");
  if (!list.length) { box.innerHTML = `<p class="muted">Пусто.</p>`; return; }
  box.innerHTML = list.map((p) =>
    `<div class="known"><img src="${toFileUri(p.thumb)}" onerror="this.style.opacity=.2"/>
       <input value="${escapeAttr(p.label)}" data-uid="${p.uid}"/>
       <div class="iconbtn" data-forget="${p.uid}" title="Забыть">${ICONS.trash}</div></div>`).join("");
  $$("#knownOut input").forEach((i) => i.addEventListener("change", () => API.rename_known(i.dataset.uid, i.value)));
  $$("#knownOut [data-forget]").forEach((b) => b.addEventListener("click", async () => { await API.forget_known(b.dataset.forget); refreshKnown(); }));
}

/* ----------------------------------------------------- models modal */
async function openModels() {
  const r = await API.list_models();
  $("#modelsList").innerHTML = (r.models && r.models.length)
    ? r.models.map((m) => `<div class="model-item ${m === r.current ? "cur" : ""}"><span class="nm">${escapeHtml(m)}</span><span class="grow"></span>${m === r.current ? '<span class="tag">текущая</span>' : `<button class="btn ghost" data-use="${escapeAttr(m)}">Выбрать</button>`}</div>`).join("")
    : `<p class="muted">Модели не найдены. Скачайте ниже (нужен запущенный Ollama).</p>`;
  $$("#modelsList [data-use]").forEach((b) => b.addEventListener("click", async () => { await API.set_model(b.dataset.use); openModels(); refreshEnv(); }));
  $("#modelsOverlay").classList.add("open");
}

/* ----------------------------------------------------- first-run install */
async function maybeFirstRun() {
  try {
    const e = await API.environment();
    const pkgs = e.packages || [];
    const shouldOpen = !e.first_run_done || pkgs.some((p) => !p.installed && p.recommended);
    if (!shouldOpen) return;
    $("#installList").innerHTML = pkgs.map((p) =>
      `<div class="inst-item ${p.installed ? "installed" : (p.recommended ? "on" : "")}" data-k="${p.key}">
         <div class="chk">${ICONS.check}</div>
         <div><h4>${escapeHtml(p.title)}</h4><p>${escapeHtml(p.desc)}</p></div>
         <span class="tag">${p.installed ? "установлено" : (!p.available ? "нет GPU" : (p.recommended ? "рекомендуется" : ""))}</span>
       </div>`).join("");
    $$("#installList .inst-item").forEach((it) => {
      if (it.classList.contains("installed")) return;
      it.querySelector(".chk").addEventListener("click", () => it.classList.toggle("on"));
    });
    $("#installOverlay").classList.add("open");
  } catch (_) {}
}
function runInstall() {
  const keys = $$("#installList .inst-item.on:not(.installed)").map((it) => it.dataset.k);
  if (!keys.length) { finishFirstRun(); return; }
  API.install_packages(keys);
}
async function finishFirstRun() { await API.finish_first_run(); $("#installOverlay").classList.remove("open"); }

/* ----------------------------------------------------- export */
function exportMenu() {
  const old = $("#expMenu"); if (old) { old.remove(); return; }
  const m = document.createElement("div"); m.id = "expMenu";
  Object.assign(m.style, { position: "fixed", zIndex: 500, background: "var(--panel)", border: "1px solid var(--line)", borderRadius: "8px", padding: "6px", boxShadow: "0 12px 30px var(--shadow)" });
  const r = $("#btnExport").getBoundingClientRect();
  m.style.left = r.left + "px"; m.style.top = r.bottom + 6 + "px";
  ["srt", "vtt", "ass", "txt", "md"].forEach((f) => {
    const b = document.createElement("div"); b.textContent = f.toUpperCase();
    Object.assign(b.style, { padding: "7px 16px", cursor: "pointer", fontFamily: "var(--mono)", fontSize: "12px", borderRadius: "5px" });
    b.onmouseenter = () => (b.style.background = "var(--bg-2)"); b.onmouseleave = () => (b.style.background = "");
    b.onclick = async () => { m.remove(); const res = await API.export_as(f); status(res.ok ? "Сохранено: " + res.path : "Экспорт отменён"); };
    m.appendChild(b);
  });
  document.body.appendChild(m);
  setTimeout(() => document.addEventListener("mousedown", function h(ev) { if (!m.contains(ev.target)) { m.remove(); document.removeEventListener("mousedown", h); } }), 0);
}
function enableTools() { $("#btnExport").disabled = false; $("#btnReprocess").disabled = false; }

/* ----------------------------------------------------- settings */
const SETTINGS_SCHEMA = [
  ["whisper_model", "Модель Whisper", "select", ["tiny", "base", "small", "medium", "large-v3"]],
  ["gpu_policy", "Тяжёлые задачи", "select", ["auto", "gpu", "cpu"]],
  ["language", "Язык речи", "select", ["auto", "ru", "en", "de", "fr", "es", "zh"]],
  ["translate_to", "Перевод субтитров", "select", ["", "ru", "en"]],
  ["ollama_host", "Адрес Ollama", "text"],
  ["default_model", "Модель по умолчанию", "text"],
  ["remember_faces", "Запоминать лица", "select", ["true", "false"]],
  ["face_match_threshold", "Порог узнавания лица", "text"],
  ["face_sample_fps", "Кадров/сек для лиц", "text"],
  ["face_cluster_threshold", "Порог слияния лиц", "text"],
];
async function openSettings() {
  const cfg = await API.get_settings();
  $("#settingsBody").innerHTML = SETTINGS_SCHEMA.map(([k, label, type, opts]) => {
    if (type === "select") {
      const o = opts.map((v) => `<option value="${v}" ${String(cfg[k]) === String(v) ? "selected" : ""}>${v === "" ? "— нет —" : v}</option>`).join("");
      return `<div class="row"><label>${label}</label><select data-k="${k}">${o}</select></div>`;
    }
    return `<div class="row"><label>${label}</label><input data-k="${k}" value="${escapeAttr(cfg[k] ?? "")}"/></div>`;
  }).join("");
  $("#settingsOverlay").classList.add("open");
}
function closeSettings() { $("#settingsOverlay").classList.remove("open"); }
async function saveSettings() {
  const patch = {};
  $$("#settingsBody [data-k]").forEach((el) => {
    let v = el.value;
    if (["face_sample_fps", "face_cluster_threshold", "face_match_threshold"].includes(el.dataset.k)) v = parseFloat(v) || 0;
    if (el.dataset.k === "remember_faces") v = v === "true";
    patch[el.dataset.k] = v;
  });
  await API.update_settings(patch);
  closeSettings(); await refreshEnv(); status("Настройки сохранены");
}

/* ----------------------------------------------------- utils */
function switchTab(name) {
  $$(".tab").forEach((x) => x.classList.toggle("active", x.dataset.pane === name));
  $$(".pane").forEach((x) => x.classList.remove("active"));
  $("#pane" + cap(name)).classList.add("active");
}
function status(t) { $("#statusText").textContent = t; }
function progress(f) { const b = $("#progressBar"); b.classList.remove("hidden"); b.firstElementChild.style.width = Math.round(f * 100) + "%"; }
function progressDone() { setTimeout(() => $("#progressBar").classList.add("hidden"), 600); }
function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }
function tc(s) { return fmt(s); }
function isInView(el) { const r = el.getBoundingClientRect(), p = el.parentElement.getBoundingClientRect(); return r.top >= p.top && r.bottom <= p.bottom; }
function escapeHtml(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function escapeAttr(s) { return escapeHtml(s).replace(/"/g, "&quot;"); }
function toFileUri(p) { if (!p) return ""; if (SERVER_BASE) return SERVER_BASE + "/local?p=" + encodeURIComponent(p); let s = String(p).replace(/\\/g, "/"); if (!s.startsWith("/")) s = "/" + s; return "file://" + encodeURI(s); }
function debounce(fn, ms) { let id; return (...a) => { clearTimeout(id); id = setTimeout(() => fn(...a), ms); }; }
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

/* применяем сохранённую ширину сплита */
(async () => { try { const c = await ready(); const s = await c.get_settings(); if (s.split_ratio) $("#viewEditor").style.setProperty("--vw", Math.round(s.split_ratio * 100) + "%"); } catch (_) {} })();
