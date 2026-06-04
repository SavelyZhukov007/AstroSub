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
    const started = Date.now();
    const iv = setInterval(() => {
      if (api()) { clearInterval(iv); resolve(api()); return; }
      if (Date.now() - started > 900) { clearInterval(iv); resolve(lanApi()); }
    }, 80);
  });
}

function lanApi() {
  const defaultBase = /^https?:$/.test(location.protocol) ? location.origin : "";
  const baseUrl = () => (localStorage.getItem("submindLanBase") || defaultBase || "").replace(/\/$/, "");
  const json = async (url, init) => {
    const base = baseUrl();
    if (!base && url.startsWith("/api/")) throw new Error("LAN URL не задан");
    const r = await fetch((base || "") + url, init);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  };
  const pairToken = new URLSearchParams(location.search).get("pair") || "";
  let paired = false;
  const ensurePaired = async () => {
    if (paired) return;
    if (!pairToken) { paired = true; return; }
    try {
      await json("/api/pair/request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: pairToken, device: getMobileDevice() }),
      });
    } catch (_) {}
    paired = true;
  };
  const waitForAccepted = async (id) => {
    for (let i = 0; i < 240; i++) {
      const r = await json(`/api/jobs/${id}/events`);
      const job = r.job || {};
      if (job.status === "accepted" || job.status === "uploading") return job;
      if (job.status === "rejected" || job.status === "failed") throw new Error(job.message || "Задача отклонена");
      window.SubmindBus.emit("mobile:upload_progress", { progress: 0, job: id, text: job.message || "Ожидает подтверждения на компьютере" });
      await sleep(1000);
    }
    throw new Error("Компьютер не подтвердил задачу");
  };
  return {
    __lan: true,
    async environment() {
      await ensurePaired();
      const h = await json("/api/lan/hello");
      return {
        ffmpeg: false, ollama: false, qwen: "", packages: [], first_run_done: true,
        device: { cpu: "remote", cpu_count: 0, has_cuda: false },
        lan: h.pairing || {},
        server_base: baseUrl() || location.origin,
      };
    },
    async lan_info() { return (await json("/api/lan/hello")).pairing || {}; },
    async lan_devices() { return (await json("/api/devices")).devices || []; },
    async lan_jobs() { return (await json("/api/jobs")).jobs || []; },
    async set_lan_base(url) { localStorage.setItem("submindLanBase", String(url || "").replace(/\/$/, "")); paired = false; return this.lan_info(); },
    async list_projects() { return []; },
    async get_settings() { return {}; },
    async finish_first_run() { return true; },
    async install_packages() { return { ok: false, error: "Недоступно в мобильном клиенте" }; },
    async list_models() { return { models: [], current: "", default: "" }; },
    async pick_video() { return null; },
    async upload_lan_file(file, options = {}) {
      await ensurePaired();
      const device = getMobileDevice();
      const job = await json("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: file.name, size: file.size, options, device }),
      });
      if (!job.ok) return job;
      const id = job.job.id;
      await waitForAccepted(id);
      const chunkSize = 1024 * 1024;
      for (let offset = 0, idx = 0; offset < file.size; offset += chunkSize, idx++) {
        const chunk = file.slice(offset, offset + chunkSize);
        const digest = await sha256(chunk);
        const res = await fetch(`${baseUrl()}/api/jobs/${id}/chunks`, {
          method: "POST",
          headers: { "X-Chunk-Index": String(idx), "X-Chunk-Sha256": digest, "X-Device-Id": device.id },
          body: chunk,
        });
        if (!res.ok) throw new Error(await res.text());
        window.SubmindBus.emit("mobile:upload_progress", { progress: Math.min(0.98, (offset + chunk.size) / file.size), job: id, text: "Передача видео" });
      }
      return json(`/api/jobs/${id}/complete`, { method: "POST" });
    },
  };
}

function getMobileDevice() {
  let id = localStorage.getItem("submindDeviceId");
  if (!id) { id = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()); localStorage.setItem("submindDeviceId", id); }
  return { id, name: navigator.userAgent.slice(0, 80), kind: /Android/i.test(navigator.userAgent) ? "android" : (/iPhone|iPad/i.test(navigator.userAgent) ? "ios-web" : "web") };
}

async function sha256(blob) {
  const buf = await blob.arrayBuffer();
  const hash = await crypto.subtle.digest("SHA-256", buf);
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

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
