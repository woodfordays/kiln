/* Shared helpers: React + htm, the Ember components, API calls, live events, formatting. */
export const React = window.React;
export const { useState, useEffect, useMemo, useRef, useCallback, useContext, createContext, Fragment } = React;
export const html = window.htm.bind(React.createElement);
export const E = window.Ember;

/* ---------------------------------------------------------------- API */

async function request(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['content-type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(url, opts);
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) {
    const msg = (data && data.detail) ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : `Request failed (HTTP ${r.status}).`;
    throw new Error(msg);
  }
  return data;
}
export const api = {
  get: (u) => request('GET', u),
  post: (u, b) => request('POST', u, b === undefined ? {} : b),
  put: (u, b) => request('PUT', u, b),
  del: (u) => request('DELETE', u),
};

/* ---------------------------------------------------------------- live events (one EventSource per tab) */

const listeners = {};
let source = null;
function ensureSource() {
  if (source) return;
  source = new EventSource('/api/events');
  for (const kind of ['status', 'log', 'request', 'download', 'models', 'presets', 'gpu', 'tune', 'tune_note']) {
    source.addEventListener(kind, (ev) => {
      let data;
      try { data = JSON.parse(ev.data); } catch { return; }
      (listeners[kind] || []).forEach((fn) => fn(data));
    });
  }
  source.onerror = () => { (listeners.connection || []).forEach((fn) => fn(false)); };
  source.onopen = () => { (listeners.connection || []).forEach((fn) => fn(true)); };
}
export function useEvent(kind, fn) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    ensureSource();
    const h = (d) => ref.current(d);
    (listeners[kind] = listeners[kind] || []).push(h);
    return () => { listeners[kind] = listeners[kind].filter((x) => x !== h); };
  }, [kind]);
}

/* Fetch a URL, refetch when deps change; keeps the previous data while reloading. */
export function useFetch(url, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    if (!url) return;
    let live = true;
    setState((s) => ({ ...s, loading: true }));
    api.get(url).then(
      (data) => live && setState({ data, error: null, loading: false }),
      (error) => live && setState((s) => ({ data: s.data, error, loading: false })),
    );
    return () => { live = false; };
  }, [url, nonce, ...deps]);
  return { ...state, reload: () => setNonce((n) => n + 1) };
}

export function useDebounced(fn, ms) {
  const t = useRef(null);
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => () => clearTimeout(t.current), []);
  return useCallback((...a) => { clearTimeout(t.current); t.current = setTimeout(() => ref.current(...a), ms); }, [ms]);
}

export function useNow(ms = 1000) {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => { const id = setInterval(() => setNow(Date.now() / 1000), ms); return () => clearInterval(id); }, [ms]);
  return now;
}

/* ---------------------------------------------------------------- app context (status, toasts, navigation) */

export const AppContext = createContext(null);
export const useApp = () => useContext(AppContext);

/* ---------------------------------------------------------------- formatting (Ember voice: 1,284 · 212 ms · 4.2 GiB · 98.6%) */

const nf0 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1, minimumFractionDigits: 1 });
const compactF = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 });

export const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
export const fmtInt = (v) => (isNum(v) ? nf0.format(v) : '–');
export const fmtCompact = (v) => (isNum(v) ? (Math.abs(v) < 10000 ? nf0.format(v) : compactF.format(v)) : '–');
export const fmtTps = (v) => (isNum(v) ? (v >= 100 ? nf0.format(v) : nf1.format(v)) : '–');
export const fmtPct = (v, digits = 1) => (isNum(v) ? (v * 100).toFixed(digits) + '%' : '–');

export function fmtMs(ms) {
  if (!isNum(ms)) return '–';
  if (ms < 1000) return nf0.format(ms) + ' ms';
  if (ms < 60000) return (ms / 1000).toFixed(ms < 10000 ? 2 : 1) + ' s';
  return fmtDuration(ms / 1000);
}

export function fmtDuration(s) {
  if (!isNum(s)) return '–';
  s = Math.max(0, s);
  if (s < 60) return Math.round(s) + ' s';
  const m = Math.floor(s / 60);
  if (m < 60) return m + ' min' + (s % 60 >= 1 && m < 10 ? ' ' + Math.round(s % 60) + ' s' : '');
  const h = Math.floor(m / 60);
  if (h < 48) return h + ' h' + (m % 60 ? ' ' + (m % 60) + ' min' : '');
  const d = Math.floor(h / 24);
  return d + ' d' + (h % 24 ? ' ' + (h % 24) + ' h' : '');
}

export function fmtBytes(b) {
  if (!isNum(b)) return '–';
  const u = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return (i === 0 ? nf0.format(b) : b.toFixed(b < 10 ? 2 : 1)) + ' ' + u[i];
}

const dtShort = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
const dDay = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' });
const tShort = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' });

export function fmtWhen(ts, now = Date.now() / 1000) {
  if (!isNum(ts)) return '–';
  const d = now - ts;
  if (d < 45) return 'just now';
  if (d < 3600) return Math.round(d / 60) + ' min ago';
  if (d < 86400) return Math.round(d / 3600) + ' h ago';
  return dtShort.format(new Date(ts * 1000));
}
export const fmtDateTime = (ts) => (isNum(ts) ? dtShort.format(new Date(ts * 1000)) : '–');
export const fmtDay = (ts) => dDay.format(new Date(ts * 1000));
export const fmtClock = (ts) => tShort.format(new Date(ts * 1000));

export function fmtAxisTime(ts, bucketS) {
  return bucketS >= 86400 ? fmtDay(ts) : (bucketS >= 3 * 3600 ? dDay.format(new Date(ts * 1000)) + ' ' + fmtClock(ts) : fmtClock(ts));
}

export const RANGE_OPTIONS = [
  { value: '1h', label: '1 h' },
  { value: '24h', label: '24 h' },
  { value: '7d', label: '7 d' },
  { value: '30d', label: '30 d' },
  { value: 'all', label: 'All' },
];
export const RANGE_WORDS = { '1h': 'the last hour', '24h': 'the last 24 hours', '7d': 'the last 7 days', '30d': 'the last 30 days', all: 'all time' };
export const PREV_WORDS = { '1h': 'vs previous hour', '24h': 'vs previous 24 h', '7d': 'vs previous 7 d', '30d': 'vs previous 30 d' };

export function delta(cur, prev) {
  if (!isNum(cur) || !isNum(prev) || prev === 0) return undefined;
  return ((cur - prev) / prev) * 100;
}

export const STATE_BADGE = {
  ready: { tone: 'success', label: 'Running', live: true },
  loading: { tone: 'warning', label: 'Loading', icon: 'clock' },
  stopping: { tone: 'neutral', label: 'Stopping', icon: 'clock' },
  stopped: { tone: 'neutral', label: 'Stopped', dot: true },
  crashed: { tone: 'danger', label: 'Crashed', icon: 'alert-circle' },
};

export function StateBadge({ state, size }) {
  const b = STATE_BADGE[state] || STATE_BADGE.stopped;
  return html`<${E.Badge} tone=${b.tone} live=${b.live} dot=${b.dot} icon=${b.icon} size=${size}>${b.label}<//>`;
}

export function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); } finally { ta.remove(); }
  return Promise.resolve();
}

export function shellQuote(s) {
  if (s === '' || s == null) return "''";
  return /^[A-Za-z0-9_\-+=.,/:@%]+$/.test(s) ? s : "'" + String(s).replace(/'/g, "'\\''") + "'";
}

export function commandLine(model, flags, env) {
  const parts = [];
  for (const [k, v] of Object.entries(env || {})) parts.push(`${k}=${shellQuote(v)}`);
  parts.push('llama-server');
  if (model) parts.push('-m', shellQuote(model));
  for (const f of flags || []) {
    if (!f.flag || f.enabled === false) continue;
    parts.push(f.flag);
    if (f.value != null && f.value !== '') parts.push(shellQuote(f.value));
  }
  return parts.join(' ');
}

export function modelLabel(path) {
  if (!path) return '';
  const base = path.split('/').pop();
  return base.replace(/-\d{5}-of-\d{5}\.gguf$/i, '').replace(/\.gguf$/i, '');
}

export function apiBase() {
  return `${location.protocol}//${location.hostname}`;
}
