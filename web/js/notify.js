/* Background notifications: toasts (and desktop notifications when the tab is hidden) for things
   that finish or break while you are looking elsewhere: tuning runs, downloads, the server, the
   live connection. Toasts for actions you just took stay with the page that took them. */
import { html, E, useRef, useEvent, fmtDuration, fmtTps, fmtInt, isNum, modelLabel, api } from './lib.js';

/* Ask once, from a click (browsers ignore requests made without a user gesture). */
export function askDesktopPermission() {
  try {
    if ('Notification' in window && window.isSecureContext && Notification.permission === 'default') Notification.requestPermission();
  } catch {}
}

function desktop(title, body, tag, onClick) {
  try {
    if (!document.hidden || !('Notification' in window) || Notification.permission !== 'granted') return;
    const n = new Notification(title + ' · Kiln', { body, tag });
    n.onclick = () => { window.focus(); if (onClick) onClick(); n.close(); };
  } catch {}
}

const OBJECTIVE_WORDS = { decode: 'generation', balanced: 'balanced', prefill: 'prompt processing', context: 'context length' };

function tuneSummary(t) {
  const b = t.best;
  const bits = [];
  if (b && isNum(b.decode0)) bits.push(`${fmtTps(b.decode0)} t/s decode`);
  if (b && isNum(b.prefillD)) bits.push(`${fmtInt(b.prefillD)} t/s prefill`);
  const took = isNum(t.finished_at) && isNum(t.created_at) ? ` in ${fmtDuration(t.finished_at - t.created_at)}` : '';
  return `${modelLabel(t.model)}: ${bits.join(', ')}. Saved as a preset${took}.`;
}

export function useBackgroundNotifications({ toast, dismiss, go }) {
  const tunes = useRef({});
  const downloads = useRef({});
  const server = useRef({}); // run id -> last state we saw
  const conn = useRef({ ok: null, timer: null, toastId: null });

  // Toast + desktop notification, with an optional button that also closes the toast.
  const notify = (t, { tag, page, action } = {}) => {
    let id;
    const act = action || (page && { label: 'View', run: () => go(page) });
    id = toast({
      ...t,
      ...(document.hidden ? { duration: 0 } : {}), // still there when you come back to the tab
      action: act && html`<${E.Button} size="sm" variant="ghost" onClick=${() => { act.run(); dismiss(id); }}>${act.label}<//>`,
    });
    if (tag) desktop(t.title, t.description || '', tag, page ? () => go(page) : null);
    return id;
  };

  useEvent('tune', (t) => {
    const prev = tunes.current[t.id];
    tunes.current[t.id] = t.status;
    if (prev !== 'running' || t.status === 'running') return;
    if (t.status === 'done') {
      const obj = t.params && OBJECTIVE_WORDS[t.params.objective];
      notify({ tone: 'success', title: obj ? `Tuning finished (${obj})` : 'Tuning finished', description: tuneSummary(t), duration: 0 },
        { tag: 'tune-' + t.id, page: 'tuner' });
    } else if (t.status === 'failed') {
      notify({ tone: 'danger', title: 'Tuning failed', description: t.error || 'The run stopped with an error.', duration: 0 },
        { tag: 'tune-' + t.id, page: 'tuner' });
    } else if (t.status === 'cancelled') {
      notify({ title: 'Tuning cancelled', description: t.params && t.params.previous ? 'Restarting the server that was running before.' : undefined });
    }
  });

  useEvent('tune_note', (n) => notify({ tone: 'warning', title: 'Tuning finished with a problem', description: n.message, duration: 0 }, { page: 'server' }));

  useEvent('download', (d) => {
    if (d.removed) { delete downloads.current[d.id]; return; }
    const prev = downloads.current[d.id];
    downloads.current[d.id] = d.status;
    if (!prev || prev === d.status) return;
    const name = d.variant ? `${d.repo.split('/').pop()} ${d.variant}` : d.repo;
    if (d.status === 'done') {
      notify({ tone: 'success', title: `Downloaded ${name}`, description: 'It is in the model library now.' },
        { tag: 'dl-' + d.id, page: 'models' });
    } else if (d.status === 'failed') {
      notify({ tone: 'danger', title: `Download failed: ${name}`, description: d.error || undefined, duration: 0 },
        { tag: 'dl-' + d.id, action: { label: 'Retry', run: () => api.post(`/api/downloads/${d.id}/retry`).catch(() => go('models')) } });
    }
  });

  // Model state changes this tab saw happen, per running llama-server. The tuner drives its own processes
  // while it holds the GPU, so those are left to the tune notifications.
  const onStatus = (s) => {
    if (!s.instances) return;
    const seen = new Set();
    for (const i of s.instances) {
      seen.add(i.run_id);
      const prev = server.current[i.run_id];
      server.current[i.run_id] = i.state;
      if (!prev || prev === i.state || s.blocked) continue;
      const name = i.name || i.model || 'The model';
      if (i.state === 'ready' && (prev === 'loading' || prev === 'starting')) {
        const took = isNum(i.ready_at) && isNum(i.started_at) ? ` in ${fmtDuration(i.ready_at - i.started_at)}` : '';
        notify({ tone: 'success', title: s.instances.length > 1 ? `${name} ready` : 'Server ready', description: `${i.model || name} loaded${took}.` },
          { tag: 'server-' + i.run_id, page: 'overview' });
      } else if (i.state === 'crashed') {
        notify({ tone: 'danger', title: prev === 'loading' || prev === 'starting' ? `${name} failed to load` : `${name} crashed`, description: i.error || undefined, duration: 0 },
          { tag: 'server-' + i.run_id, action: { label: 'Logs', run: () => go('server') } });
      }
    }
    for (const id of Object.keys(server.current)) if (!seen.has(Number(id))) delete server.current[id];
  };
  useEvent('status', onStatus);

  // Lost connection: wait a few seconds so a blip or a page reload does not flash a toast.
  useEvent('connection', (ok) => {
    const c = conn.current;
    if (ok === c.ok) return;
    const wasKnown = c.ok !== null;
    c.ok = ok;
    clearTimeout(c.timer);
    if (!ok) {
      c.timer = setTimeout(() => {
        c.toastId = toast({ tone: 'warning', title: 'Lost connection to Kiln', description: 'Live updates are paused. Reconnecting…', duration: 0 });
      }, 4000);
    } else if (wasKnown && c.toastId != null) {
      dismiss(c.toastId);
      c.toastId = null;
      toast({ tone: 'success', title: 'Reconnected', description: 'Live updates are back.' });
    }
  });

  return { seedStatus: (s) => {
    if (!s || !s.instances || Object.keys(server.current).length) return;
    for (const i of s.instances) server.current[i.run_id] = i.state;
  } };
}
