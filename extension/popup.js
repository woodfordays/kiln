/* Popup: server state, stop/restart, and the preset list with one-click start or switch. */
import { api, getConfig, openKiln, hostOf, fmtDuration, fmtWhen, fmtGiB, fmtCtx, STATE_WORDS, KilnError } from './lib/kiln.js';
import { icon } from './lib/icons.js';

const $ = (id) => document.getElementById(id);
const main = $('main');

/* Tiny element builder. Children are nodes or strings (set as text, never parsed as HTML). */
function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k in el && k !== 'list') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) el.append(c);
  return el;
}

let cfg = null;
let snap = null;
let busy = null;      // what's in flight: 'stop' | 'restart' | preset id
let filter = '';
let timer = null;

/* ---------------------------------------------------------------- data */

async function refresh() {
  clearTimeout(timer);
  try {
    snap = await api.quick(cfg);
    browser.runtime.sendMessage({ type: 'snapshot', snapshot: snap }).catch(() => {});
    render();
  } catch (e) {
    snap = null;
    renderProblem(e);
    browser.runtime.sendMessage({ type: 'poll' }).catch(() => {});
  }
  const s = snap && snap.server.state;
  timer = setTimeout(refresh, s === 'loading' || s === 'stopping' || busy != null ? 1000 : 4000);
}

async function act(key, fn, startedName) {
  busy = key;
  showNotice(null);
  render();
  try {
    await fn();
    if (startedName !== undefined) browser.runtime.sendMessage({ type: 'started', name: startedName }).catch(() => {});
  } catch (e) {
    showNotice(e.message);
  } finally {
    busy = null;
    await refresh();
  }
}

/* ---------------------------------------------------------------- rendering */

function showNotice(msg) {
  const n = $('notice');
  n.replaceChildren();
  n.hidden = !msg;
  if (!msg) return;
  n.append(icon('alert-circle', 16), h('span', { class: 'notice__text' }, msg),
    h('button', { class: 'icon-btn icon-btn--sm', type: 'button', 'aria-label': 'Dismiss', onclick: () => showNotice(null) }, icon('x', 14)));
}

function badge(state) {
  const tone = { ready: 'success', loading: 'warning', stopping: 'neutral', stopped: 'neutral', crashed: 'danger' }[state] || 'neutral';
  return h('span', { class: `badge badge--${tone}` },
    state === 'ready' ? h('span', { class: 'badge__dot badge__dot--live' })
      : state === 'loading' || state === 'stopping' ? icon('clock', 12)
      : state === 'crashed' ? icon('alert-circle', 12)
      : h('span', { class: 'badge__dot' }),
    STATE_WORDS[state] || state);
}

function button(label, { iconName, variant = 'secondary', onclick, disabled, loading, title } = {}) {
  return h('button', { class: `btn btn--${variant}${loading ? ' is-loading' : ''}`, type: 'button', onclick, disabled: disabled || loading, title },
    loading ? h('span', { class: 'spinner', 'aria-hidden': 'true' }) : iconName && icon(iconName, 14), label);
}

function serverCard() {
  const s = snap.server;
  const running = s.state === 'ready' || s.state === 'loading';
  const blocked = !!s.blocked;
  const meta = [];
  if (s.preset && running) meta.push(s.preset.name);
  if (s.state === 'ready') meta.push(`up ${fmtDuration(s.uptime_s)}`, s.n_ctx ? `${fmtCtx(s.n_ctx)} ctx` : null);
  if (s.state === 'loading') meta.push(`loading ${fmtDuration(s.loading_s)}`);

  return h('section', { class: 'server' },
    h('div', { class: 'server__row' },
      badge(s.state),
      h('span', { class: 'server__model', title: s.model_path || '' }, running || s.state === 'crashed' ? s.model : 'Nothing loaded')),
    meta.filter(Boolean).length ? h('p', { class: 'server__meta text-caption' }, meta.filter(Boolean).join(' · ')) : null,
    s.state === 'crashed' && s.error ? h('p', { class: 'server__error text-caption' }, s.error) : null,
    blocked ? h('div', { class: 'callout callout--warning' }, icon('workflow', 14),
      h('span', {}, snap.tune.running ? `Tuning · ${snap.tune.stage || 'starting'}${snap.tune.trial ? ' · ' + snap.tune.trial : ''}` : s.blocked)) : null,
    h('div', { class: 'server__actions' },
      running || s.state === 'stopping'
        ? button('Stop', { iconName: 'stop', onclick: () => act('stop', () => api.stop(cfg)), loading: busy === 'stop' || s.state === 'stopping', disabled: busy != null })
        : null,
      s.state === 'ready' || s.state === 'crashed'
        ? button('Restart', { iconName: 'refresh', onclick: () => act('restart', () => api.restart(cfg), s.preset ? s.preset.name : s.model), loading: busy === 'restart', disabled: busy != null || blocked })
        : null),
  );
}

function presetRow(p, lead) {
  const s = snap.server;
  const running = s.state === 'ready' || s.state === 'loading';
  const blocked = !!s.blocked;
  const sub = p.model_missing ? 'Model file missing' : [p.model_name, fmtWhen(p.last_used, snap.server_time)].filter(Boolean).join(' · ');
  let action;
  if (p.active) {
    action = h('span', { class: `chip ${s.state === 'ready' ? 'chip--success' : 'chip--warning'}` },
      s.state === 'ready' ? icon('check', 12) : icon('clock', 12), s.state === 'ready' ? 'Running' : 'Loading');
  } else {
    action = button(running ? 'Switch' : 'Start', {
      iconName: 'play', variant: lead ? 'primary' : 'secondary', // one ember action per view
      loading: busy === p.id, disabled: busy != null || blocked || p.model_missing,
      title: p.model_missing ? 'The model file is not on disk' : running ? `Stop ${s.model} and start ${p.name}` : `Start ${p.name}`,
      onclick: () => act(p.id, () => api.run(p.id, cfg), p.name),
    });
  }
  return h('li', { class: 'preset' + (p.active ? ' is-active' : '') + (p.model_missing ? ' is-missing' : '') },
    h('div', { class: 'preset__text' },
      h('span', { class: 'preset__name', title: p.description || p.name }, p.name),
      h('span', { class: 'preset__sub text-caption' + (p.model_missing ? ' danger' : '') }, sub)),
    action);
}

function presetList() {
  const all = snap.presets;
  if (!all.length) {
    return h('section', { class: 'empty' },
      h('p', { class: 'text-body' }, 'No presets yet.'),
      h('p', { class: 'text-caption muted' }, 'Create one in Kiln, or run the tuner to make one.'),
      button('Open Presets', { iconName: 'external-link', onclick: () => { openKiln('presets'); window.close(); } }));
  }
  const q = filter.trim().toLowerCase();
  const shown = q ? all.filter((p) => (p.name + ' ' + p.model_name).toLowerCase().includes(q)) : all;
  const lead = snap.server.state === 'ready' || snap.server.state === 'loading' ? null : shown.find((p) => !p.model_missing);
  const list = h('ul', { class: 'presets', 'aria-label': 'Presets' }, shown.map((p) => presetRow(p, p === lead)));
  return h('section', { class: 'presets-wrap' },
    h('div', { class: 'section-head' },
      h('span', { class: 'text-label' }, 'Presets'),
      h('span', { class: 'text-caption subtle' }, q ? `${shown.length} of ${all.length}` : String(all.length))),
    all.length > 6 ? filterBox(shown) : null,
    shown.length ? list : h('p', { class: 'text-caption muted pad' }, 'No preset matches.'));
}

function filterBox(shown) {
  const input = h('input', {
    class: 'filter__input', type: 'search', placeholder: 'Filter presets · Enter starts the first', value: filter,
    'aria-label': 'Filter presets',
    oninput: (e) => { filter = e.target.value; render(true); },
    onkeydown: (e) => {
      const first = shown.find((p) => !p.active && !p.model_missing);
      if (e.key === 'Enter' && first && busy == null && !snap.server.blocked) act(first.id, () => api.run(first.id, cfg), first.name);
    },
  });
  return h('label', { class: 'filter' }, icon('search', 14), input);
}

function footer() {
  const bits = [];
  const g = snap.gpu;
  if (g && g.vram_total) bits.push(`VRAM ${fmtGiB(g.vram_used)} / ${fmtGiB(g.vram_total)} GiB`);
  if (g && g.busy != null) bits.push(`${Math.round(g.busy * 100)}% busy`);
  if (g && g.power_w != null) bits.push(`${Math.round(g.power_w)} W`);
  const dl = snap.downloads.active;
  if (!bits.length && !dl) return null;
  return h('div', { class: 'foot text-caption' },
    bits.length ? h('span', {}, bits.join(' · ')) : null,
    dl ? h('button', { class: 'link', type: 'button', onclick: () => { openKiln('models'); window.close(); } },
      icon('download', 12), `${dl} downloading`) : null);
}

function render(keepFocus) {
  if (!snap) return;
  const focused = keepFocus && document.activeElement && document.activeElement.classList.contains('filter__input');
  const pos = focused ? document.activeElement.selectionStart : 0;
  main.replaceChildren(serverCard(), presetList());
  $('foot').replaceChildren(...[footer()].filter(Boolean));
  if (focused) {
    const el = main.querySelector('.filter__input');
    el.focus();
    el.setSelectionRange(pos, pos);
  }
}

function renderProblem(e) {
  const kind = e instanceof KilnError ? e.kind : 'unreachable';
  const copy = {
    config: ['Connect to Kiln', 'Enter the address of your Kiln control panel, for example http://ai-box:8090.', 'Open settings'],
    auth: ['Kiln needs a token', `${e.message} Add the KILN_API_TOKEN value in the extension settings.`, 'Open settings'],
    unreachable: ["Can't reach Kiln", `${e.message} Check that the container is running and the address is right.`, 'Retry'],
    http: ['Kiln returned an error', e.message, 'Retry'],
  }[kind] || ["Can't reach Kiln", e.message, 'Retry'];
  const toSettings = kind === 'config' || kind === 'auth';
  $('foot').replaceChildren();
  main.replaceChildren(h('section', { class: 'empty' },
    h('span', { class: 'empty__icon' }, icon(kind === 'config' ? 'flame' : 'alert-triangle', 24)),
    h('p', { class: 'empty__title' }, copy[0]),
    h('p', { class: 'text-caption muted' }, copy[1]),
    h('div', { class: 'empty__actions' },
      button(copy[2], { variant: 'primary', iconName: toSettings ? 'sliders' : 'refresh', onclick: () => (toSettings ? openSettings() : refresh()) }),
      toSettings ? null : button('Settings', { iconName: 'sliders', onclick: openSettings }))));
}

function openSettings() {
  browser.runtime.openOptionsPage();
  window.close();
}

/* ---------------------------------------------------------------- start */

$('open').append(icon('external-link', 16));
$('settings').append(icon('sliders', 16));
$('open').addEventListener('click', () => { openKiln(); window.close(); });
$('settings').addEventListener('click', openSettings);

(async () => {
  cfg = await getConfig();
  $('host').textContent = cfg.base ? hostOf(cfg.base) : 'not connected';
  main.replaceChildren(h('div', { class: 'loading text-caption muted' }, h('span', { class: 'spinner' }), 'Connecting…'));
  refresh();
})();
