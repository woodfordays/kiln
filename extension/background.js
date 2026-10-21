/* Background: keeps the toolbar badge current and sends notifications when something finishes or
   breaks. Polls /api/quick once a minute, and every few seconds while the server is loading or a
   start from the popup is pending. The popup also hands over every snapshot it fetches.

   MV3 background scripts in Firefox are event pages that can be unloaded at any time, so the
   previous snapshot lives in storage, not in variables. */
import { api, getConfig, openKiln, fmtTps, fmtDuration } from './lib/kiln.js';

const SLOW = 'poll';
const FAST = 'poll-fast';

const BADGE = {
  ready: { text: 'ON', color: '#3bd5be', ink: '#0f0c0a' },
  loading: { text: '…', color: '#fdc146', ink: '#0f0c0a' },
  stopping: { text: '…', color: '#fdc146', ink: '#0f0c0a' },
  crashed: { text: '!', color: '#d42834', ink: '#ffffff' },
  tuning: { text: 'TUNE', color: '#fc6012', ink: '#170c09' },
  offline: { text: '?', color: '#5f5a55', ink: '#ffffff' },
  stopped: { text: '', color: '#5f5a55', ink: '#ffffff' },
};

function schedule() {
  browser.alarms.create(SLOW, { periodInMinutes: 1 });
}

async function setFast(on) {
  const existing = await browser.alarms.get(FAST);
  if (on && !existing) browser.alarms.create(FAST, { periodInMinutes: 0.1 }); // ~6 s
  if (!on && existing) browser.alarms.clear(FAST);
}

async function setBadge(kind, title) {
  const b = BADGE[kind] || BADGE.stopped;
  await browser.action.setBadgeText({ text: b.text });
  await browser.action.setBadgeBackgroundColor({ color: b.color });
  try { await browser.action.setBadgeTextColor({ color: b.ink }); } catch {}
  await browser.action.setTitle({ title: title ? `Kiln · ${title}` : 'Kiln' });
}

let notifySeq = 0;
function notify(page, title, message) {
  return browser.notifications.create(`kiln:${page}:${++notifySeq}`, {
    type: 'basic', iconUrl: browser.runtime.getURL('icons/kiln.svg'), title, message: message || '',
  });
}

browser.notifications.onClicked.addListener((id) => {
  const page = id.split(':')[1] || '';
  openKiln(page);
  browser.notifications.clear(id);
});

/* ---------------------------------------------------------------- observing snapshots */

// Serialize: alarms and popup messages can arrive together, and each one reads then writes `last`.
let chain = Promise.resolve();
const serial = (fn) => (chain = chain.then(fn, fn).catch((e) => console.warn('kiln:', e)));

async function poll() {
  const cfg = await getConfig();
  if (!cfg.base) return setBadge('stopped', 'not set up');
  try {
    await observe(await api.quick(cfg), cfg);
  } catch (e) {
    await setBadge('offline', e.message);
    await setFast(false);
  }
}

async function observe(q, cfg) {
  cfg = cfg || (await getConfig());
  const { last = {}, pending = null } = await browser.storage.local.get(['last', 'pending']);
  const s = q.server;
  const n = cfg.notify;

  // Tuning finished, failed or was cancelled.
  if (last.tuneRunning && !q.tune.running && last.tuneId != null && n.tune) {
    try {
      const t = await api.tune(last.tuneId, cfg);
      const b = t.best;
      if (t.status === 'done') {
        const took = t.finished_at && t.created_at ? ` in ${fmtDuration(t.finished_at - t.created_at)}` : '';
        notify('tuner', 'Tuning finished',
          (b ? `${fmtTps(b.decode0)} t/s decode, ${fmtTps(b.prefillD)} t/s prefill. ` : '') + `Saved as a preset${took}.`);
      } else if (t.status === 'failed') {
        notify('tuner', 'Tuning failed', t.error || 'The run stopped with an error.');
      }
    } catch {}
  }

  // The server crashed (the tuner's own churn doesn't count).
  if (s.state === 'crashed' && last.state && last.state !== 'crashed' && !s.blocked && n.crash) {
    notify('server', last.state === 'loading' ? 'Model failed to load' : 'llama-server crashed', s.error || '');
  }

  // A start from the popup finished loading (or failed, which the crash rule covers).
  let nextPending = pending;
  if (pending && (s.state === 'ready' || s.state === 'crashed' || s.state === 'stopped')) {
    if (s.state === 'ready' && n.ready) {
      const ctx = s.n_ctx ? ` with ${s.n_ctx.toLocaleString('en-US')} tokens of context` : '';
      notify('', `${s.preset ? s.preset.name : s.model} is ready`, `${s.model} loaded${ctx}.`);
    }
    nextPending = null;
  } else if (pending && Date.now() - pending.at > 30 * 60 * 1000) {
    nextPending = null; // never resolved; don't hold the fast poll forever
  }

  // Downloads: compare statuses only while something is or was in the queue.
  let dl = last.dl || {};
  if (q.downloads.active > 0 || last.downloadsActive > 0) {
    try {
      const rows = await api.downloads(cfg);
      const next = {};
      for (const r of rows) {
        next[r.id] = r.status;
        const was = dl[r.id];
        if (!n.downloads || !(was === 'queued' || was === 'downloading')) continue;
        const name = `${r.repo.split('/').pop()}${r.variant ? ' ' + r.variant : ''}`;
        if (r.status === 'done') notify('models', `Downloaded ${name}`, 'It is in the model library now.');
        if (r.status === 'failed') notify('models', `Download failed: ${name}`, r.error || '');
      }
      dl = next;
    } catch {}
  }

  await browser.storage.local.set({
    last: { state: s.state, tuneRunning: q.tune.running, tuneId: q.tune.tune_id, downloadsActive: q.downloads.active, dl },
    pending: nextPending,
  });

  const busy = s.state === 'loading' || s.state === 'stopping';
  await setFast(busy || !!nextPending);
  if (q.tune.running) await setBadge('tuning', `tuning: ${q.tune.stage || 'starting'}`);
  else {
    const what = busy || s.state === 'ready' ? (s.preset ? s.preset.name : s.model) : '';
    await setBadge(s.state, STATE_TITLE[s.state] + (what ? ' · ' + what : ''));
  }
}

const STATE_TITLE = { ready: 'running', loading: 'loading', stopping: 'stopping', stopped: 'stopped', crashed: 'crashed' };

/* ---------------------------------------------------------------- wiring */

browser.alarms.onAlarm.addListener((a) => { if (a.name === SLOW || a.name === FAST) serial(poll); });

browser.runtime.onMessage.addListener((msg) => {
  if (!msg || typeof msg !== 'object') return;
  if (msg.type === 'snapshot') return serial(() => observe(msg.snapshot));
  if (msg.type === 'started') {
    return serial(async () => {
      await browser.storage.local.set({ pending: { name: msg.name || '', at: Date.now() } });
      await setFast(true);
    });
  }
  if (msg.type === 'poll') return serial(poll);
});

browser.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && (changes.base || changes.token)) {
    serial(async () => { await browser.storage.local.set({ last: {}, pending: null }); await poll(); });
  }
});

browser.runtime.onInstalled.addListener(async ({ reason }) => {
  schedule();
  serial(poll);
  if (reason === 'install' && !(await getConfig()).base) browser.runtime.openOptionsPage();
});
browser.runtime.onStartup.addListener(() => { schedule(); serial(poll); });
