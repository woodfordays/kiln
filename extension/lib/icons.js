/* A few Ember icons (same 24×24 stroke paths as web/vendor/ember.js), built as DOM nodes. */
const ICONS = {
  play: [['path', { d: 'M8 5.5v13l10.5-6.5z' }]],
  stop: [['rect', { x: 6.5, y: 6.5, width: 11, height: 11, rx: 1.5 }]],
  refresh: [['path', { d: 'M19.5 12a7.5 7.5 0 1 1-2.2-5.3L19.5 9' }], ['path', { d: 'M19.5 4.5V9H15' }]],
  'external-link': [['path', { d: 'M14 4.5h5.5V10' }], ['path', { d: 'M19.5 4.5L11 13' }], ['path', { d: 'M17.5 14v3.5a2 2 0 0 1-2 2h-9a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2H10' }]],
  sliders: [['path', { d: 'M4 7.5h8M16 7.5h4M4 16.5h3M11 16.5h9' }], ['circle', { cx: 14, cy: 7.5, r: 2 }], ['circle', { cx: 9, cy: 16.5, r: 2 }]],
  search: [['circle', { cx: 11, cy: 11, r: 6.5 }], ['path', { d: 'M16 16l4.5 4.5' }]],
  check: [['path', { d: 'M5 12.5l4.5 4.5L19 7.5' }]],
  x: [['path', { d: 'M6.5 6.5l11 11M17.5 6.5l-11 11' }]],
  'alert-circle': [['circle', { cx: 12, cy: 12, r: 9 }], ['path', { d: 'M12 7.5v5.5' }], ['circle', { cx: 12, cy: 16.3, r: 0.55 }]],
  'alert-triangle': [['path', { d: 'M10.3 4.6a2 2 0 0 1 3.4 0l7 12a2 2 0 0 1-1.7 3H5a2 2 0 0 1-1.7-3z' }], ['path', { d: 'M12 9.5v4' }], ['circle', { cx: 12, cy: 16.6, r: 0.55 }]],
  'check-circle': [['circle', { cx: 12, cy: 12, r: 9 }], ['path', { d: 'M8.5 12.5l2.5 2.5 4.5-5' }]],
  clock: [['circle', { cx: 12, cy: 12, r: 9 }], ['path', { d: 'M12 7.5V12l3 2' }]],
  workflow: [['rect', { x: 3.5, y: 3.5, width: 7, height: 7, rx: 1.75 }], ['rect', { x: 13.5, y: 13.5, width: 7, height: 7, rx: 1.75 }], ['path', { d: 'M10.5 7h3a3.5 3.5 0 0 1 3.5 3.5v3' }]],
  download: [['path', { d: 'M12 4v11' }], ['path', { d: 'M7.5 10.5L12 15l4.5-4.5' }], ['path', { d: 'M5 19.5h14' }]],
  flame: [['path', { d: 'M12 3.5c.7 4.6 4.6 6.6 4.6 10.6a4.6 4.6 0 0 1-9.2 0C7.4 10.9 10.6 9.2 12 3.5z' }]],
};

const NS = 'http://www.w3.org/2000/svg';

export function icon(name, size = 16) {
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', size);
  svg.setAttribute('height', size);
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', size <= 16 ? 2 : 1.75);
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('icon');
  for (const [tag, attrs] of ICONS[name] || []) {
    const el = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    svg.appendChild(el);
  }
  return svg;
}
