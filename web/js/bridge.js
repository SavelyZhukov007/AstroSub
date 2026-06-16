// Мост между Python (pywebview) и UI.

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

export function api() {
  return (window.pywebview && window.pywebview.api) || null;
}

export function ready() {
  return new Promise((resolve, reject) => {
    if (api()) return resolve(api());
    window.addEventListener("pywebviewready", () => resolve(api()), { once: true });
    setTimeout(() => {
      if (!api()) reject(new Error("Submind должен быть запущен через desktop-приложение."));
    }, 5000);
  });
}

export function fmt(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = String(Math.floor(sec / 3600)).padStart(2, "0");
  const m = String(Math.floor((sec % 3600) / 60)).padStart(2, "0");
  const s = String(sec % 60).padStart(2, "0");
  return `${h}:${m}:${s}`;
}
