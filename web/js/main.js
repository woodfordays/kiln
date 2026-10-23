/* App shell: side nav, hash routing, live status, toasts. */
import { html, E, useState, useEffect, useCallback, useRef, api, useEvent, AppContext, StateBadge, liveOf } from './lib.js';
import { OverviewPage } from './pages/overview.js';
import { ServerPage } from './pages/server.js';
import { PresetsPage } from './pages/presets.js';
import { ModelsPage } from './pages/models.js';
import { RequestsPage } from './pages/requests.js';
import { DecidePage } from './pages/decide.js';
import { SettingsPage } from './pages/settings.js';
import { TunerPage } from './pages/tuner.js';
import { useBackgroundNotifications } from './notify.js';

const PAGES = {
  overview: { label: 'Overview', icon: 'grid', component: OverviewPage },
  server: { label: 'Server', icon: 'server', component: ServerPage },
  presets: { label: 'Presets', icon: 'file', component: PresetsPage },
  models: { label: 'Models', icon: 'database', component: ModelsPage },
  tuner: { label: 'Tuner', icon: 'workflow', component: TunerPage },
  decide: { label: 'Decide', icon: 'filter', component: DecidePage },
  requests: { label: 'Requests', icon: 'activity', component: RequestsPage },
  settings: { label: 'Settings', icon: 'sliders', component: SettingsPage },
};

function parseHash() {
  const h = location.hash.replace(/^#\/?/, '');
  const [page, ...rest] = h.split('/');
  return { page: PAGES[page] ? page : 'overview', arg: rest.join('/') };
}

function useTheme() {
  const [theme, setTheme] = useState(document.documentElement.getAttribute('data-theme') || 'dark');
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    try { localStorage.setItem('kiln-theme', theme); } catch {}
  }, [theme]);
  return [theme, setTheme];
}

/* A stable onClose per toast: Ember restarts the auto-dismiss timer whenever onClose changes, and
   the shell re-renders on every status and download-progress event. */
function ToastItem({ toast: t, onDismiss }) {
  const close = useCallback(() => onDismiss(t.id), [t.id, onDismiss]);
  return html`<${E.Toast} tone=${t.tone} title=${t.title} description=${t.description} action=${t.action}
    duration=${t.duration} onClose=${close} />`;
}

function App() {
  const [route, setRoute] = useState(parseHash());
  const [status, setStatus] = useState(null);
  const [toasts, setToasts] = useState([]);
  const [downloads, setDownloads] = useState({});
  const [navOpen, setNavOpen] = useState(false);
  const [theme, setTheme] = useTheme();
  const toastId = useRef(0);
  const seedRef = useRef(() => {});

  useEffect(() => {
    const on = () => { setRoute(parseHash()); setNavOpen(false); window.scrollTo(0, 0); };
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);

  const refreshStatus = useCallback(() => api.get('/api/status').then((s) => { seedRef.current(s); setStatus(s); }).catch(() => {}), []);
  useEffect(() => { refreshStatus(); const id = setInterval(refreshStatus, 30000); return () => clearInterval(id); }, []);
  useEvent('status', (s) => setStatus((prev) => ({ ...(prev || {}), ...s })));
  useEvent('connection', (ok) => { if (ok) refreshStatus(); });
  useEvent('download', (d) => setDownloads((m) => {
    const next = { ...m };
    if (d.removed || !['queued', 'downloading'].includes(d.status)) delete next[d.id]; else next[d.id] = d;
    return next;
  }));
  useEffect(() => {
    api.get('/api/downloads').then((rows) => {
      const m = {};
      rows.filter((r) => ['queued', 'downloading'].includes(r.status)).forEach((r) => { m[r.id] = r; });
      setDownloads(m);
    }).catch(() => {});
  }, []);

  // duration 0 keeps a toast until it is dismissed. Returns the id, for dismiss().
  const toast = useCallback((t) => {
    const id = ++toastId.current;
    setToasts((ts) => [...ts.slice(-3), { id, duration: t.tone === 'danger' ? 8000 : 4500, ...t }]);
    return id;
  }, []);
  const dismiss = useCallback((id) => setToasts((ts) => ts.filter((x) => x.id !== id)), []);
  const fail = useCallback((err, title = 'Something went wrong') => toast({ tone: 'danger', title, description: err.message || String(err) }), [toast]);
  const go = useCallback((page, arg) => { location.hash = '#/' + page + (arg ? '/' + arg : ''); }, []);
  seedRef.current = useBackgroundNotifications({ toast, dismiss, go }).seedStatus;

  const ctx = { status, refreshStatus, toast, dismiss, fail, go, route };
  const Page = PAGES[route.page].component;
  const activeDownloads = Object.keys(downloads).length;

  const sections = [
    { items: ['overview', 'server', 'presets', 'tuner', 'models', 'decide', 'requests'].map((id) => ({
      id, label: PAGES[id].label, icon: PAGES[id].icon, href: '#/' + id,
      count: id === 'models' && activeDownloads ? activeDownloads : id === 'tuner' && status && status.blocked ? 'Running' : undefined,
    })) },
    { label: 'System', items: [{ id: 'settings', label: PAGES.settings.label, icon: PAGES.settings.icon, href: '#/settings' }] },
  ];

  const header = html`<div className="brand">
    <span className="brand__name text-subtitle">Kiln</span>
    <span className="brand__sub text-caption">llama.cpp control</span>
  </div>`;

  const footer = html`<div className="nav-footer">
    <div className="nav-footer__status">
      <div className="nav-footer__row">
        ${status ? html`<${StateBadge} state=${status.state} size="sm" />` : html`<${E.Spinner} size=${14} label="Loading status" />`}
        <${E.IconButton} icon=${theme === 'dark' ? 'sun' : 'moon'} label=${theme === 'dark' ? 'Use light theme' : 'Use dark theme'}
          size="sm" onClick=${() => setTheme(theme === 'dark' ? 'light' : 'dark')} />
      </div>
      ${liveOf(status).length > 1
        ? liveOf(status).map((i) => html`<span key=${i.run_id} className="text-caption muted truncate nav-footer__model" title=${i.model}>
            <span className=${'nav-footer__dot' + (i.state === 'ready' ? ' is-ready' : '')} aria-hidden="true"></span>${i.name}</span>`)
        : html`${status && status.model && html`<span className="text-caption muted truncate" title=${status.model}>${status.model}</span>`}
          ${status && status.preset && html`<span className="text-caption subtle truncate">${status.preset.name}</span>`}`}
    </div>
  </div>`;

  return html`<${AppContext.Provider} value=${ctx}>
    <div className="app">
      ${navOpen && html`<div className="nav-scrim" onClick=${() => setNavOpen(false)}></div>`}
      <div className=${'app__nav' + (navOpen ? ' is-open' : '')}>
        <${E.SideNav} sections=${sections} active=${route.page} header=${header} footer=${footer} label="Kiln" />
      </div>
      <div>
        <div className="mobile-bar">
          <${E.IconButton} icon="menu" label="Open navigation" onClick=${() => setNavOpen(true)} />
          <span className="text-subtitle">Kiln</span>
          <span style=${{ flex: 1 }}></span>
          ${status && html`<${StateBadge} state=${status.state} size="sm" />`}
        </div>
        <main className="app__main" id="main">
          <${Page} key=${route.page} arg=${route.arg} />
        </main>
      </div>
    </div>
    <${E.ToastRegion}>
      ${toasts.map((t) => html`<${ToastItem} key=${t.id} toast=${t} onDismiss=${dismiss} />`)}
    <//>
  <//>`;
}

ReactDOM.createRoot(document.getElementById('root')).render(html`<${App} />`);
