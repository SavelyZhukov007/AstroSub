const S = (body) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;

export const ICONS = {
  open: S('<path d="M3 7h6l2 2h10v9a2 2 0 0 1-2 2H3z"/><path d="M3 7V5a1 1 0 0 1 1-1h4l2 2"/>'),
  play: S('<path d="M7 5l12 7-12 7z"/>'),
  pause: S('<rect x="7" y="5" width="3.5" height="14" rx="1"/><rect x="14" y="5" width="3.5" height="14" rx="1"/>'),
  captions: S('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M8 11h3M8 14h5M14 11h2"/>'),
  fullscreen: S('<path d="M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 1-1-1v-4"/>'),
  close: S('<path d="M6 6l12 12M18 6L6 18"/>'),
  chip: S('<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3v2M14 3v2M10 19v2M14 19v2M3 10h2M3 14h2M19 10h2M19 14h2"/>'),
  volume: S('<path d="M4 10v4h4l5 4V6l-5 4z"/><path d="M16 9a4 4 0 0 1 0 6"/>'),
};

export function mountIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((el) => {
    const name = el.getAttribute("data-icon");
    if (ICONS[name]) el.innerHTML = ICONS[name];
  });
}
