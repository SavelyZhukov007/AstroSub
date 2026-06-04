// Мост между Python (pywebview) и UI.

// --- шина событий: Python зовёт window.SubmindBus.emit(event, payload) ----
const handlers = {};
window.SubmindBus = {
  emit(event, payload) {
    (handlers[event] || []).forEach((fn) => {
      try { fn(payload); } catch (e) { console.error(e); }
    });
  },
};
export function on(event, fn) {
  (handlers[event] = handlers[event] || []).push(fn);
}

// --- доступ к Python API (ждём готовности pywebview) ----------------------
export function api() {
  return (window.pywebview && window.pywebview.api) || null;
}
export function ready() {
  return new Promise((resolve) => {
    if (api()) return resolve(api());
    window.addEventListener("pywebviewready", () => resolve(api()), { once: true });
    // подстраховка
    const iv = setInterval(() => { if (api()) { clearInterval(iv); resolve(api()); } }, 80);
  });
}

// --- крошечный безопасный markdown (без сторонних зависимостей) -----------
export function renderMarkdown(src) {
  if (!src) return "";
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const lines = esc(src).split("\n");
  let html = "", inList = false, listTag = "ul";
  const inline = (t) =>
    t
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>");
  const closeList = () => { if (inList) { html += `</${listTag}>`; inList = false; } };

  for (let raw of lines) {
    const line = raw.trimEnd();
    let m;
    if ((m = line.match(/^(#{1,3})\s+(.*)$/))) {
      closeList(); html += `<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`;
    } else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
      if (!inList || listTag !== "ul") { closeList(); listTag = "ul"; html += "<ul>"; inList = true; }
      html += `<li>${inline(m[1])}</li>`;
    } else if ((m = line.match(/^\s*\d+\.\s+(.*)$/))) {
      if (!inList || listTag !== "ol") { closeList(); listTag = "ol"; html += "<ol>"; inList = true; }
      html += `<li>${inline(m[1])}</li>`;
    } else if (line === "") {
      closeList();
    } else {
      closeList(); html += `<p>${inline(line)}</p>`;
    }
  }
  closeList();
  return html;
}

// --- формат таймкода ------------------------------------------------------
export function fmt(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = String(Math.floor(sec / 3600)).padStart(2, "0");
  const m = String(Math.floor((sec % 3600) / 60)).padStart(2, "0");
  const s = String(sec % 60).padStart(2, "0");
  return `${h}:${m}:${s}`;
}
