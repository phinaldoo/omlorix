const ICONS = {
  memory: '<path d="M12 5.5a3.5 3.5 0 0 0-6.7-1.4A3.5 3.5 0 0 0 3 10.7V14a4 4 0 0 0 4 4h1v2h4V5.5Z"/><path d="M12 5.5a3.5 3.5 0 0 1 6.7-1.4A3.5 3.5 0 0 1 21 10.7V14a4 4 0 0 1-4 4h-1"/><path d="M7 9h2m-2 4h3m7-4h-2m2 4h-3"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a15 15 0 0 1 0 18M12 3a15 15 0 0 0 0 18"/>',
  sparkles: '<path d="m12 3-1.1 3.1A4.7 4.7 0 0 1 8 9l-3 1 3 1.1a4.7 4.7 0 0 1 2.9 2.8L12 17l1.1-3.1A4.7 4.7 0 0 1 16 11l3-1-3-1a4.7 4.7 0 0 1-2.9-2.9L12 3Z"/><path d="m19 16-.5 1.4a2.3 2.3 0 0 1-1.1 1.1L16 19l1.4.5a2.3 2.3 0 0 1 1.1 1.1L19 22l.5-1.4a2.3 2.3 0 0 1 1.1-1.1L22 19l-1.4-.5a2.3 2.3 0 0 1-1.1-1.1L19 16Z"/>',
  inbox: '<path d="M4 4h16v14H4z"/><path d="M4 13h4l2 3h4l2-3h4"/>',
  layers: '<path d="m12 3-9 5 9 5 9-5-9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  cpu: '<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M9 1v3m6-3v3M9 20v3m6-3v3M20 9h3m-3 6h3M1 9h3m-3 6h3M10 10h4v4h-4z"/>',
  send: '<path d="m3 3 18 9-18 9 4-9-4-9Z"/><path d="M7 12h14"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  bot: '<rect x="4" y="7" width="16" height="13" rx="3"/><path d="M12 3v4M8 12h.01M16 12h.01M8 16h8"/>',
  'git-branch': '<circle cx="6" cy="5" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="6" cy="19" r="2"/><path d="M6 7v10M8 9c5 0 4-3 8-3"/>',
  anchor: '<circle cx="12" cy="5" r="2"/><path d="M12 7v14M5 12H2a10 10 0 0 0 20 0h-3M8 21h8"/>',
  sliders: '<path d="M4 5h10m4 0h2M4 12h2m4 0h10M4 19h8m4 0h4"/><circle cx="16" cy="5" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="14" cy="19" r="2"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M18.5 9A7 7 0 0 0 6 6.5L4 9m16 6-2 2.5A7 7 0 0 1 5.5 15"/>',
  zap: '<path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z"/>',
  scan: '<path d="M4 7V4h3m10 0h3v3M4 17v3h3m10 0h3v-3M7 12h10"/>',
  'fast-forward': '<path d="m4 5 8 7-8 7V5Zm9 0 8 7-8 7V5Z"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 21h16"/>',
  rotate: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4L16.5 3.5Z"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  alert: '<path d="M10.3 3.7 2.6 17a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 3.7a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4m0 3h.01"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3m3 0-1 14H7L6 7m4 4v6m4-6v6"/>',
};

function createIcon(name) {
  const wrapper = document.createElement('span');
  wrapper.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.memory}</svg>`;
  return wrapper.firstElementChild;
}

export function renderIcons(root = document) {
  root.querySelectorAll('[data-icon]:not([data-icon-ready])').forEach((target) => {
    target.append(createIcon(target.dataset.icon));
    target.dataset.iconReady = 'true';
  });
}
