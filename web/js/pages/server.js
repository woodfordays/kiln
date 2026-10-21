/* Server: the models that are running, GPU memory, start and swap, command, live log, run history. */
import {
  html, E, useState, useEffect, useRef, useMemo, api, useApp, useFetch, useEvent, useNow,
  fmtInt, fmtDuration, fmtMs, fmtWhen, isNum, StateBadge, RoleBadge, liveOf, crashedOf, fmtMiB, copyText, shellQuote, modelLabel,
} from '../lib.js';

export function ServerPage() {
  const { status, toast, fail, refreshStatus } = useApp();
  const presets = useFetch('/api/presets');
  const runs = useFetch('/api/runs?limit=25');
  useEvent('status', () => runs.reload());
  useEvent('presets', () => presets.reload());
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState(null);

  const act = async (fn, okMsg) => {
    setBusy(true);
    try { await fn(); if (okMsg) toast({ tone: 'success', title: okMsg }); refreshStatus(); } catch (e) { fail(e); } finally { setBusy(false); }
  };
  if (!status) return html`<div className="page"><${E.Spinner} label="Loading" /></div>`;
  const live = liveOf(status);
  const crashed = crashedOf(status);
  const ready = live.filter((i) => i.state === 'ready');
  const all = status.instances || [];
  const sel = all.find((i) => i.run_id === picked) || all.find((i) => i.run_id === status.primary) || all[0] || null;

  return html`<div className="page">
    <header className="page-head">
      <div className="page-head__titles">
        <h1 className="text-headline">Server</h1>
        <p className="text-body">${all.length > 1 ? 'Each model runs in its own llama-server. ' : ''}Requests to <span className="mono">${location.hostname}:${status.api_port}</span> go to the model named in the request.</p>
      </div>
      <div className="page-head__actions">
        ${ready.length > 0 && html`<${E.Button} variant="secondary" icon="external-link" onClick=${() => window.open(`${location.protocol}//${location.hostname}:${status.api_port}/`, '_blank', 'noopener')}>Open llama.cpp chat<//>`}
        ${live.length > 0 && html`<${E.Button} variant="secondary" icon="pause" disabled=${busy} onClick=${() => act(() => api.post('/api/server/stop'), live.length > 1 ? 'All models stopped' : 'llama-server stopped')}>${live.length > 1 ? 'Stop all' : 'Stop'}<//>`}
      </div>
    </header>

    ${crashed.map((i) => html`<${E.Alert} key=${i.run_id} tone="danger" title=${`${i.name} exited unexpectedly.`}
      action=${html`<${E.Button} size="sm" variant="ghost" onClick=${() => act(() => api.post('/api/server/dismiss', { run_id: i.run_id }))}>Dismiss<//>`}>
      <span className="mono">${i.error || 'No error message was printed.'}</span> Check its log below, adjust the preset, and start again.
    <//>`)}
    ${status.llama && status.llama.error && html`<${E.Alert} tone="danger" title="llama-server can't run.">
      ${status.llama.binary}: ${status.llama.error}
    <//>`}
    ${status.blocked && html`<${E.Alert} tone="warning" title="The tuner has the GPU.">${status.blocked}<//>`}

    ${all.length > 0 && html`<${InstancesCard} status=${status} selected=${sel && sel.run_id} onSelect=${setPicked} busy=${busy} act=${act} />`}
    <div className="grid grid--side">
      <${StartCard} status=${status} presets=${presets.data || []} busy=${busy} act=${act} />
      <${MemoryCard} status=${status} />
    </div>

    ${sel && sel.argv && sel.argv.length > 0 && html`<${CommandCard} inst=${sel} />`}
    ${sel && html`<${LogCard} inst=${sel} instances=${all} onSelect=${setPicked} />`}
    <${RunsCard} runs=${runs.data || []} />
  </div>`;
}

/* ------------------------------------------------------------------ running models */

function InstancesCard({ status, selected, onSelect, busy, act }) {
  const now = useNow(1000);
  const columns = [
    { key: 'name', header: 'Model', render: (i) => html`<div className="cell-main">
      <span className="row">${i.name}
        ${i.pinned && html`<${E.Badge} size="sm" tone="neutral">Pinned<//>`}
        ${i.adapter && html`<${E.Badge} size="sm" tone="info">Scored by Kiln<//>`}
        ${i.run_id === selected && html`<${E.Badge} size="sm" tone="neutral">Log shown<//>`}</span>
      <span className="cell-sub text-caption truncate" style=${{ maxWidth: 360 }} title=${i.model_path}>${i.preset ? `Preset ${i.preset.name} · ` : ''}${i.model}</span>
    </div>` },
    { key: 'role', header: 'Role', render: (i) => html`<${RoleBadge} role=${i.role} />` },
    { key: 'state', header: 'State', render: (i) => html`<span className="row" style=${{ flexWrap: 'nowrap' }}><${StateBadge} state=${i.state} size="sm" />
      ${(i.state === 'loading' || i.state === 'starting') && i.started_at && html`<span className="text-caption subtle">${fmtDuration(now - i.started_at)}</span>`}</span>` },
    { key: 'n_ctx', header: 'Context', numeric: true, render: (i) => fmtInt(i.n_ctx) },
    { key: 'mem', header: 'GPU memory', numeric: true, render: (i) => (isNum(i.gpu_mib) ? html`<span title="Measured when it loaded">${fmtMiB(i.gpu_mib)}</span>`
      : i.need_mib ? html`<span className="subtle" title="Estimated from the model and flags">~${fmtMiB(i.need_mib)}</span>` : '–') },
    { key: 'up', header: 'Up', numeric: true, render: (i) => (i.ready_at && i.state === 'ready' ? fmtDuration(now - i.ready_at) : '–') },
    { key: 'busy', header: 'In flight', numeric: true, render: (i) => (i.inflight ? fmtInt(i.inflight) : '–') },
    { key: 'actions', header: '', align: 'end', render: (i) => html`<div className="row" style=${{ justifyContent: 'flex-end', flexWrap: 'nowrap' }}>
      <${E.IconButton} icon="file" size="sm" label=${'Show the log of ' + i.name} onClick=${() => onSelect(i.run_id)} />
      ${i.state !== 'crashed' && html`<${E.IconButton} icon="refresh" size="sm" label=${'Restart ' + i.name} disabled=${busy || i.state === 'stopping'}
        onClick=${() => act(() => api.post('/api/server/restart', { run_id: i.run_id }), `Restarting ${i.name}`)} />
      <${E.IconButton} icon="pause" size="sm" label=${'Stop ' + i.name} disabled=${busy || i.state === 'stopping'}
        onClick=${() => act(() => api.post('/api/server/stop', { run_id: i.run_id }), `${i.name} stopped`)} />`}
    </div>` },
  ];
  return html`<${E.Card} title=${status.instances.length > 1 ? 'Running models' : 'Running model'} padding="none">
    <${E.Table} caption="Running models" density="compact" columns=${columns} rows=${status.instances} rowKey=${(i) => String(i.run_id)} />
  <//>`;
}

function MemoryCard({ status }) {
  const v = status.vram;
  const live = liveOf(status);
  const ours = live.reduce((a, i) => a + (isNum(i.gpu_mib) ? i.gpu_mib : (i.need_mib || 0)), 0);
  if (!v) {
    return html`<${E.Card} title="GPU memory"><p className="text-body muted">This machine reports no GPU memory, so Kiln can't check whether another model fits. Models start anyway.</p><//>`;
  }
  const pct = v.used_mib / v.total_mib;
  return html`<${E.Card} title="GPU memory" description=${ours > 0 ? `Kiln's models account for about ${fmtMiB(ours)}.` : undefined}>
    <div className="stack">
      <${E.Progress} value=${v.used_mib} max=${v.total_mib} label="In use" tone=${pct > 0.93 ? 'warning' : 'ember'}
        valueText=${`${fmtMiB(v.used_mib)} of ${fmtMiB(v.total_mib)}`} />
      <p className="text-caption muted">${fmtMiB(v.free_mib)} free. Before another model starts, Kiln compares what it needs (measured the last time it ran, otherwise estimated from the model and flags) with this, and keeps 512 MiB spare.</p>
    </div>
  <//>`;
}

/* ------------------------------------------------------------------ start */

function StartCard({ status, presets, busy, act }) {
  const { go } = useApp();
  const [mode, setMode] = useState('preset');
  const [presetId, setPresetId] = useState('');
  const [stopOthers, setStopOthers] = useState(true);
  const [plan, setPlan] = useState(null);
  const models = useFetch('/api/models');
  const [model, setModel] = useState('');
  const [args, setArgs] = useState('-ngl 99 -fa on -c 16384 -np 1');
  const live = liveOf(status);
  useEffect(() => {
    if (!presetId && presets.length) setPresetId(String((status.preset && status.preset.id) || presets[0].id));
  }, [presets]);
  const modelOpts = (models.data ? models.data.models : []).filter((m) => !m.is_projector && !m.is_draft)
    .map((m) => ({ value: m.path, label: `${m.name}${m.repo ? ' · ' + m.repo : ''}` }));
  useEffect(() => { if (!model && modelOpts.length) setModel(modelOpts[0].value); }, [models.data]);

  const chosen = presets.find((p) => String(p.id) === presetId);
  const already = chosen && live.some((i) => i.preset && i.preset.id === chosen.id);
  useEffect(() => {
    if (mode !== 'preset' || !presetId || already) { setPlan(null); return undefined; }
    let alive = true;
    api.post('/api/server/plan', { preset_id: Number(presetId), replace: stopOthers ? 'auto' : undefined })
      .then((p) => alive && setPlan(p)).catch(() => alive && setPlan(null));
    return () => { alive = false; };
  }, [mode, presetId, stopOthers, live.length, already]);

  const start = (body) => act(() => api.post('/api/server/start', body), 'Starting llama-server');
  const startPreset = (force) => start({ preset_id: Number(presetId), replace: stopOthers ? 'auto' : undefined, force: force || undefined });
  const startCustom = () => act(async () => {
    const parsed = await api.post('/api/presets/parse', { command: 'llama-server ' + args });
    await api.post('/api/server/start', { model, flags: parsed.flags, env: parsed.env, replace: stopOthers ? 'auto' : undefined });
  }, 'Starting llama-server');

  const tooBig = plan && plan.fits === false;
  const verb = live.length ? (plan && plan.evict && plan.evict.length ? 'Swap in' : 'Start alongside') : 'Start';
  return html`<${E.Card} title=${live.length ? 'Start another model' : 'Start'}
    description=${live.length ? 'Runs next to the others when it fits in GPU memory.' : 'Load a model to serve requests.'}>
    <div className="stack">
      <${E.SegmentedControl} label="Start from" value=${mode} onChange=${setMode} size="sm" fullWidth
        options=${[{ value: 'preset', label: 'Preset' }, { value: 'custom', label: 'Custom flags' }]} />
      ${mode === 'preset' ? (presets.length ? html`
        <${E.Select} label="Preset" value=${presetId} onChange=${(e) => setPresetId(e.target.value)}
          options=${presets.map((p) => ({ value: String(p.id), label: p.name + (live.some((i) => i.preset && i.preset.id === p.id) ? ' (running)' : '') }))}
          hint=${chosen ? modelLabel(chosen.model) || 'No model set' : undefined} />
        ${already ? html`<p className="text-caption muted">This preset is running.</p>` : html`
          <${PlanNote} plan=${plan} />
          ${live.length > 0 && html`<${E.Switch} label="Stop other models if this one doesn't fit" checked=${stopOthers} onChange=${(e) => setStopOthers(e.target.checked)}
            description="Idle models that aren't pinned go first, least recently used first." />`}
          <div className="row">
            <${E.Button} variant=${live.length ? 'secondary' : 'primary'} icon="play" loading=${busy} disabled=${!presetId || status.blocked} onClick=${() => startPreset(false)}>${verb}<//>
            ${tooBig && html`<${E.Button} variant="ghost" size="sm" disabled=${busy} onClick=${() => startPreset(true)}>Start anyway<//>`}
          </div>`}
      ` : html`<${E.EmptyState} icon="file" title="No presets yet" description="Save a model and its flags as a preset to start it in one click."
          actions=${html`<${E.Button} variant="secondary" icon="plus" onClick=${() => go('presets')}>Create a preset<//>`} />`)
      : html`
        <${E.Select} label="Model" value=${model} onChange=${(e) => setModel(e.target.value)} options=${modelOpts}
          placeholder=${modelOpts.length ? undefined : 'No models found'} />
        <div>
          <label className="field-label text-label" htmlFor="custom-args">Flags</label>
          <textarea id="custom-args" className="textarea text-code" rows="3" value=${args} onChange=${(e) => setArgs(e.target.value)} spellCheck=${false}></textarea>
          <p className="field-hint text-caption">Anything llama-server accepts. Kiln sets the model, host and port.</p>
        </div>
        <${E.Button} variant=${live.length ? 'secondary' : 'primary'} icon="play" loading=${busy} disabled=${!model || status.blocked} onClick=${startCustom}>${live.length ? 'Start alongside' : 'Start model'}<//>
      `}
    </div>
  <//>`;
}

function PlanNote({ plan }) {
  if (!plan) return null;
  const gib = (m) => fmtMiB(m);
  if (plan.fits === false) {
    return html`<${E.Alert} tone="warning" title="Doesn't fit right now.">${plan.reason}${plan.evictable && plan.evictable.length ? ' Turn on “Stop other models” to make room.' : ''}<//>`;
  }
  if (plan.evict && plan.evict.length) {
    return html`<p className="text-caption muted">Needs about ${gib(plan.need_mib)}. Will stop ${plan.evict.map((e) => e.name).join(', ')} to make room.</p>`;
  }
  if (plan.fits === true) {
    return html`<p className="text-caption muted">Fits next to what's running: needs about ${gib(plan.need_mib)}${plan.basis === 'measured' ? ' (measured last time)' : ' (estimated)'}, ${gib(plan.free_mib)} free.</p>`;
  }
  return html`<p className="text-caption muted">GPU memory can't be read here, so the fit isn't checked.</p>`;
}

/* ------------------------------------------------------------------ command and log */

function CommandCard({ inst }) {
  const { toast } = useApp();
  const envPart = Object.entries(inst.env || {}).map(([k, v]) => `${k}=${shellQuote(v)}`);
  const cmd = [...envPart, ...inst.argv.map(shellQuote)].join(' ');
  return html`<${E.Card} title=${'Command · ' + inst.name} description="Exactly what Kiln ran, including the flags it added for the role and the host, port and metrics flags.">
    <div className="stack">
      <div className="code-wrap">
        <pre className="code text-code">${cmd}</pre>
        <${E.IconButton} icon="copy" label="Copy command" size="sm" variant="secondary" onClick=${() => copyText(cmd).then(() => toast({ title: 'Command copied' }))} />
      </div>
      ${inst.notes && inst.notes.length > 0 && html`<ul className="notes text-caption muted">${inst.notes.map((n, i) => html`<li key=${i}>${n}</li>`)}</ul>`}
    </div>
  <//>`;
}

function LogCard({ inst, instances, onSelect }) {
  const rid = inst.run_id;
  const [lines, setLines] = useState([]);
  const [follow, setFollow] = useState(true);
  const [filter, setFilter] = useState('');
  const box = useRef(null);
  const ridRef = useRef(rid);
  useEffect(() => {
    ridRef.current = rid;
    api.get(`/api/server/logs?tail=2000&run_id=${rid}`).then((d) => { if (ridRef.current === rid) setLines(d.lines); }).catch(() => setLines([]));
  }, [rid]);
  useEvent('log', (d) => {
    if (d.run_id !== ridRef.current) return;
    setLines((ls) => [...ls, ...d.lines].slice(-5000));
  });
  useEffect(() => { if (follow && box.current) box.current.scrollTop = box.current.scrollHeight; }, [lines, follow]);
  const shown = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return f ? lines.filter((l) => l[1].toLowerCase().includes(f)) : lines;
  }, [lines, filter]);
  const cls = (l) => {
    if (l[2] === 'kiln') return 'is-kiln';
    if (/ E |error|failed/i.test(l[1])) return 'is-error';
    if (/ W |warn/i.test(l[1])) return 'is-warn';
    return '';
  };
  const time = (t) => new Date(t * 1000).toLocaleTimeString('en-US', { hour12: false });
  return html`<${E.Card} title=${'Log · ' + inst.name} description=${'Run ' + rid + ' · stdout and stderr of llama-server.'} padding="none"
    actions=${html`<div className="card-actions">
      ${instances.length > 1 && html`<${E.Select} label="Show log of" hideLabel size="sm" value=${String(rid)} onChange=${(e) => onSelect(Number(e.target.value))}
        options=${instances.map((i) => ({ value: String(i.run_id), label: i.name }))} />`}
      <${E.TextField} label="Filter log" hideLabel icon="search" size="sm" placeholder="Filter" value=${filter} onChange=${(e) => setFilter(e.target.value)} />
      <${E.Switch} label="Follow" checked=${follow} onChange=${(e) => setFollow(e.target.checked)} />
      <${E.IconButton} icon="download" label="Download log" size="sm" onClick=${() => { location.href = `/api/runs/${rid}/log`; }} />
    </div>`}>
    <div className="log text-code" ref=${box} role="log" aria-live="off" tabIndex="0">
      ${shown.length === 0 ? html`<p className="log-empty text-body">${lines.length ? 'No lines match the filter.' : 'No output yet.'}</p>`
        : shown.map((l, i) => html`<div key=${i} className=${'log__line ' + cls(l)}><span className="log__time">${time(l[0])}</span><span className="log__text">${l[1]}</span></div>`)}
    </div>
  <//>`;
}

/* ------------------------------------------------------------------ history */

const RUN_BADGE = {
  ready: ['success', 'Running', 'check-circle'], loading: ['warning', 'Loading', 'clock'], stopped: ['neutral', 'Stopped', null],
  crashed: ['danger', 'Crashed', 'alert-circle'], lost: ['warning', 'Interrupted', 'alert-triangle'], stopping: ['neutral', 'Stopping', 'clock'],
};

function RunsCard({ runs }) {
  const now = useNow(60000);
  const columns = [
    { key: 'started_at', header: 'Started', render: (r) => html`<span className="nowrap">${fmtWhen(r.started_at, now)}</span>` },
    { key: 'preset_name', header: 'Preset', render: (r) => r.preset_name || html`<span className="subtle">Custom</span>` },
    { key: 'role', header: 'Role', render: (r) => html`<${RoleBadge} role=${r.role || 'chat'} />` },
    { key: 'model', header: 'Model', render: (r) => html`<span className="truncate" style=${{ maxWidth: 280, display: 'inline-block' }} title=${r.model}>${modelLabel(r.model)}</span>` },
    { key: 'load_ms', header: 'Load time', numeric: true, render: (r) => fmtMs(r.load_ms) },
    { key: 'n_ctx', header: 'Context', numeric: true, render: (r) => fmtInt(r.n_ctx) },
    { key: 'gpu_mib', header: 'GPU memory', numeric: true, render: (r) => fmtMiB(r.gpu_mib) },
    { key: 'dur', header: 'Ran for', numeric: true, render: (r) => (r.ready_at ? fmtDuration((r.stopped_at || now) - r.ready_at) : '–') },
    { key: 'status', header: 'Result', render: (r) => { const [tone, label, icon] = RUN_BADGE[r.status] || ['neutral', r.status, null]; return html`<${E.Badge} size="sm" tone=${tone} icon=${icon || undefined} dot=${!icon}>${label}<//>`; } },
    { key: 'log', header: '', align: 'end', render: (r) => html`<${E.IconButton} icon="file" size="sm" label=${'Download log of run ' + r.id} onClick=${() => { location.href = `/api/runs/${r.id}/log`; }} />` },
  ];
  return html`<${E.Card} title="Run history" padding="none">
    <${E.Table} caption="Run history" density="compact" columns=${columns} rows=${runs} rowKey=${(r) => String(r.id)} empty="No runs yet. Runs appear here after the first start." />
  <//>`;
}
