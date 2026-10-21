/* Shared by the popup, the options page and the background script: settings, the Kiln API
   client, and formatting. API reference: ../../docs/API.md */

export const DEFAULT_NOTIFY = { tune: true, crash: true, ready: true, downloads: true };

export async function getConfig() {
  const s = await browser.storage.local.get(['base', 'token', 'notify']);
  return { base: s.base || '', token: s.token || '', notify: { ...DEFAULT_NOTIFY, ...(s.notify || {}) } };
}

/* "ai-box:8090", "http://ai-box:8090/#/server" → "http://ai-box:8090". Throws on nonsense. */
export function normalizeBase(input) {
  let s = String(input || '').trim();
  if (!s) return '';
  if (!/^https?:\/\//i.test(s)) s = 'http://' + s;
  const u = new URL(s);
  return u.origin;
}

/* kind: 'config' (no URL set), 'unreachable' (network), 'auth' (401), 'http' (Kiln said no). */
export class KilnError extends Error {
  constructor(message, kind, status) {
    super(message);
    this.kind = kind;
    this.status = status;
  }
}

export async function request(path, { method = 'GET', body, cfg, timeout = 10000 } = {}) {
  cfg = cfg || (await getConfig());
  if (!cfg.base) throw new KilnError('Set the Kiln URL in the extension settings.', 'config');
  const headers = {};
  if (cfg.token) headers.Authorization = `Bearer ${cfg.token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeout);
  let r;
  try {
    r = await fetch(cfg.base + path, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctl.signal, cache: 'no-store', credentials: 'omit',
    });
  } catch (e) {
    throw new KilnError(`Can't reach Kiln at ${hostOf(cfg.base)}.`, 'unreachable');
  } finally {
    clearTimeout(timer);
  }
  if (r.status === 401) {
    throw new KilnError(cfg.token ? 'Kiln rejected the API token.' : 'Kiln needs an API token.', 'auth', 401);
  }
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) {
    const detail = data && typeof data.detail === 'string' ? data.detail : `Kiln answered HTTP ${r.status}.`;
    throw new KilnError(detail, 'http', r.status);
  }
  return data;
}

export const api = {
  health: (cfg) => request('/healthz', { cfg, timeout: 5000 }),
  quick: (cfg) => request('/api/quick', { cfg }),
  // Loading can take minutes, but the call returns as soon as the process is spawned. Stopping
  // waits for the old process to exit (up to 15 s), hence the longer timeout.
  run: (presetId, cfg) => request('/api/server/start', { method: 'POST', body: { preset_id: presetId, replace: true }, cfg, timeout: 30000 }),
  stop: (cfg) => request('/api/server/stop', { method: 'POST', body: {}, cfg, timeout: 30000 }),
  restart: (cfg) => request('/api/server/restart', { method: 'POST', body: {}, cfg, timeout: 30000 }),
  tune: (id, cfg) => request(`/api/tune/${id}`, { cfg }),
  downloads: (cfg) => request('/api/downloads', { cfg }),
};

export const hostOf = (base) => { try { return new URL(base).host; } catch { return base; } };

/* Open the Kiln UI, reusing a tab that already shows it when the browser lets us see one. */
export async function openKiln(page = '') {
  const { base } = await getConfig();
  if (!base) return browser.runtime.openOptionsPage();
  const url = base + '/' + (page ? '#/' + page : '');
  try {
    const [tab] = await browser.tabs.query({ url: base + '/*' });
    if (tab) {
      await browser.tabs.update(tab.id, { active: true, ...(page ? { url } : {}) });
      return browser.windows.update(tab.windowId, { focused: true });
    }
  } catch {}
  return browser.tabs.create({ url });
}

/* ---------------------------------------------------------------- formatting (Kiln's voice) */

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

export function fmtDuration(s) {
  if (!isNum(s)) return '';
  s = Math.max(0, s);
  if (s < 60) return Math.round(s) + ' s';
  const m = Math.floor(s / 60);
  if (m < 60) return m + ' min';
  const h = Math.floor(m / 60);
  if (h < 48) return h + ' h' + (m % 60 ? ' ' + (m % 60) + ' min' : '');
  return Math.floor(h / 24) + ' d ' + (h % 24) + ' h';
}

export function fmtWhen(ts, now = Date.now() / 1000) {
  if (!isNum(ts)) return 'never used';
  const d = now - ts;
  if (d < 60) return 'just now';
  if (d < 3600) return Math.round(d / 60) + ' min ago';
  if (d < 86400) return Math.round(d / 3600) + ' h ago';
  if (d < 86400 * 30) return Math.round(d / 86400) + ' d ago';
  return new Date(ts * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function fmtGiB(b) {
  return isNum(b) ? (b / 1024 ** 3).toFixed(1) : '–';
}

export const fmtTps = (v) => (isNum(v) ? (v >= 100 ? Math.round(v).toLocaleString('en-US') : v.toFixed(1)) : '–');
export const fmtCtx = (n) => (isNum(n) ? (n % 1024 === 0 ? `${n / 1024}K` : n.toLocaleString('en-US')) : '');

export const STATE_WORDS = {
  ready: 'Running', loading: 'Loading', stopping: 'Stopping', stopped: 'Stopped', crashed: 'Crashed',
};
