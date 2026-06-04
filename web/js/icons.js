// Иконографика Submind. Единый стиль: 24x24, stroke 1.75, скруглённые концы.
// Без эмодзи и сторонних библиотек.
const S = (body) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;

export const ICONS = {
  open: S('<path d="M3 7h6l2 2h10v9a2 2 0 0 1-2 2H3z"/><path d="M3 7V5a1 1 0 0 1 1-1h4l2 2"/>'),
  play: S('<path d="M7 5l12 7-12 7z"/>'),
  pause: S('<rect x="7" y="5" width="3.5" height="14" rx="1"/><rect x="14" y="5" width="3.5" height="14" rx="1"/>'),
  captions: S('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M8 11h3M8 14h5M14 11h2"/>'),
  notes: S('<path d="M4 4h12l4 4v12H4z"/><path d="M16 4v4h4"/><path d="M8 12h8M8 16h6"/>'),
  faces: S('<circle cx="9" cy="9" r="4"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 6a4 4 0 0 1 0 6"/><path d="M18 19a6 6 0 0 0-3-4.5"/>'),
  // FaceID — рамка-скобки + контур лица (в стиле Apple Face ID)
  faceid: S('<path d="M4 8V6a2 2 0 0 1 2-2h2"/><path d="M16 4h2a2 2 0 0 1 2 2v2"/><path d="M20 16v2a2 2 0 0 1-2 2h-2"/><path d="M8 20H6a2 2 0 0 1-2-2v-2"/><path d="M9 10v1M15 10v1"/><path d="M12 10v3l-1 1"/><path d="M9 15s1 1.2 3 1.2S15 15 15 15"/>'),
  mesh: S('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3v18M6 6l12 12M18 6L6 18"/>'),
  glossary: S('<path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 0-3 3z"/><path d="M5 4v16"/><path d="M9 9h7M9 12h5"/>'),
  chapters: S('<path d="M4 6h10M4 12h16M4 18h12"/><circle cx="19" cy="6" r="1.5"/>'),
  search: S('<circle cx="11" cy="11" r="6"/><path d="M20 20l-4-4"/>'),
  export: S('<path d="M12 3v12"/><path d="M8 7l4-4 4 4"/><path d="M5 14v5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5"/>'),
  settings: S('<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2"/>'),
  spark: S('<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M18 16l.8 2.2L21 19l-2.2.8L18 22l-.8-2.2L15 19l2.2-.8z"/>'),
  bookmark: S('<path d="M6 4h12v16l-6-4-6 4z"/>'),
  note: S('<path d="M4 5h16v10l-4 4H4z"/><path d="M16 19v-4h4"/>'),
  translate: S('<path d="M4 5h8M8 3v2M6 5c0 4-2 6-4 7M5 7c1 3 3 4 5 5"/><path d="M12 20l4-9 4 9M13.5 17h5"/>'),
  mic: S('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>'),
  layers: S('<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>'),
  clock: S('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
  chevron: S('<path d="M9 6l6 6-6 6"/>'),
  close: S('<path d="M6 6l12 12M18 6L6 18"/>'),
  check: S('<path d="M5 12l5 5 9-11"/>'),
  trash: S('<path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/>'),
  wave: S('<path d="M3 12h2l2-6 3 13 3-16 3 13 2-4h3"/>'),
  brain: S('<path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-1 5 3 3 0 0 0 2 5 3 3 0 0 0 5 1V4a2 2 0 0 0-3 0z"/><path d="M15 4a3 3 0 0 1 3 3 3 3 0 0 1 1 5 3 3 0 0 1-2 5 3 3 0 0 1-3 1"/>'),
  chat: S('<path d="M4 5h16v11H9l-4 4V5z"/><path d="M8 10h8M8 13h5"/>'),
  send: S('<path d="M4 12l16-8-6 16-3-6-7-2z"/>'),
  stop: S('<rect x="6" y="6" width="12" height="12" rx="2"/>'),
  attach: S('<path d="M20 11l-8.5 8.5a4 4 0 0 1-6-6L13 5a2.5 2.5 0 0 1 4 4l-8 8a1 1 0 0 1-1.5-1.5L15 8"/>'),
  fullscreen: S('<path d="M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 1-1-1v-4"/>'),
  download: S('<path d="M12 3v12"/><path d="M8 11l4 4 4-4"/><path d="M5 19h14"/>'),
  plus: S('<path d="M12 5v14M5 12h14"/>'),
  bot: S('<rect x="5" y="8" width="14" height="11" rx="3"/><path d="M12 4v4M9 13h.01M15 13h.01M9 16h6"/><path d="M5 12H3M21 12h-2"/>'),
  scan: S('<path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2"/><path d="M4 12h16"/>'),
  gauge: S('<path d="M5 18a8 8 0 1 1 14 0"/><path d="M12 14l4-4"/><path d="M5 18h14"/>'),
  cards: S('<rect x="3" y="6" width="13" height="13" rx="2"/><path d="M8 3h10a2 2 0 0 1 2 2v10"/><path d="M6 11h7M6 14h5"/>'),
  compare: S('<path d="M12 3v18"/><path d="M5 8l-3 4 3 4M19 8l3 4-3 4"/><circle cx="8" cy="6" r="0"/>'),
  chip: S('<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3v2M14 3v2M10 19v2M14 19v2M3 10h2M3 14h2M19 10h2M19 14h2"/>'),
  cam: S('<path d="M4 7h3l1.5-2h7L17 7h3a0 0 0 0 1 0 0v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/><circle cx="12" cy="13" r="4"/>'),
  user: S('<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>'),
  phone: S('<rect x="7" y="2.5" width="10" height="19" rx="2"/><path d="M10 18h4"/><path d="M10 5h4"/>'),
  qr: S('<rect x="4" y="4" width="6" height="6"/><rect x="14" y="4" width="6" height="6"/><rect x="4" y="14" width="6" height="6"/><path d="M14 14h2v2h-2zM18 14h2M14 18h6M18 16v4"/>'),
  link: S('<path d="M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1"/>'),
  volume: S('<path d="M4 10v4h4l5 4V6l-5 4z"/><path d="M16 9a4 4 0 0 1 0 6"/>'),
};

export function mountIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((el) => {
    const name = el.getAttribute("data-icon");
    if (ICONS[name]) el.innerHTML = ICONS[name];
  });
}
