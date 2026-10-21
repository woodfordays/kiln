/* Settings: Kiln URL, API token, notification switches. Saving tests the connection. */
import { api, getConfig, normalizeBase, hostOf, STATE_WORDS } from './lib/kiln.js';
import { icon } from './lib/icons.js';

const $ = (id) => document.getElementById(id);
const boxes = [...document.querySelectorAll('[data-notify]')];

function show(tone, text) {
  const r = $('result');
  r.hidden = false;
  r.className = `result result--${tone}`;
  r.replaceChildren(icon(tone === 'ok' ? 'check-circle' : tone === 'warn' ? 'alert-triangle' : 'alert-circle', 16), document.createElement('span'));
  r.lastChild.textContent = text;
}

(async () => {
  const cfg = await getConfig();
  $('base').value = cfg.base;
  $('token').value = cfg.token;
  for (const b of boxes) b.checked = !!cfg.notify[b.dataset.notify];
})();

$('reveal').addEventListener('click', () => {
  const t = $('token');
  t.type = t.type === 'password' ? 'text' : 'password';
  $('reveal').setAttribute('aria-label', t.type === 'password' ? 'Show token' : 'Hide token');
});

$('form').addEventListener('submit', (e) => {
  e.preventDefault();
  let base;
  try {
    base = normalizeBase($('base').value);
  } catch {
    show('bad', 'That is not a valid address. Use something like http://ai-box:8090.');
    return;
  }
  if (!base) { show('bad', 'Enter the Kiln URL.'); return; }
  // Host permission lets the extension skip CORS; Kiln allows extension origins anyway, so a
  // refusal is fine. permissions.request must run before any await to count as a user action.
  const u = new URL(base);
  browser.permissions.request({ origins: [`${u.protocol}//${u.hostname}/*`] }).catch(() => {});
  save(base);
});

async function save(base) {
  const token = $('token').value.trim();
  const notify = Object.fromEntries(boxes.map((b) => [b.dataset.notify, b.checked]));
  $('base').value = base;
  await browser.storage.local.set({ base, token, notify });
  $('saved').textContent = 'Saved';
  setTimeout(() => { $('saved').textContent = ''; }, 2000);

  const cfg = { base, token, notify };
  show('warn', `Testing ${hostOf(base)}…`);
  try {
    await api.health(cfg);
  } catch (e) {
    show('bad', `${e.message} Check the address and that the Kiln container is running. If the address is http:// and Zen's HTTPS-Only Mode is on, add an exception for this host.`);
    return;
  }
  try {
    const q = await api.quick(cfg);
    const s = q.server;
    const what = s.state === 'ready' || s.state === 'loading' ? `${STATE_WORDS[s.state]} ${s.model}` : STATE_WORDS[s.state] || s.state;
    show('ok', `Connected. ${what} · ${q.presets.length} preset${q.presets.length === 1 ? '' : 's'}.`);
  } catch (e) {
    if (e.kind === 'auth') show('bad', `Kiln is reachable but needs a token. ${token ? 'This token was rejected.' : 'Enter the KILN_API_TOKEN value.'}`);
    else if (e.status === 404) show('bad', 'Kiln is reachable but too old for the extension (no /api/quick). Rebuild the Kiln container.');
    else show('bad', e.message);
  }
}
