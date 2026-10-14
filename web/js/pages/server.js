/* Server: status, start from a preset or a custom command, live log, run history. */
import {
  html, E, useState, useEffect, useRef, useMemo, api, useApp, useFetch, useEvent, useNow,
  fmtInt, fmtDuration, fmtDateTime, fmtMs, fmtWhen, isNum, StateBadge, copyText, commandLine, shellQuote, modelLabel,
} from '../lib.js';

export function ServerPage() {
  const { status, toast, fail, refreshStatus } = useApp();
  const presets = useFetch('/api/presets');
  const runs = useFetch('/api/runs?limit=25');
  useEvent('status', () => runs.reload());
  useEvent('presets', () => presets.reload());
  const [busy, setBusy] = useState(false);

  const act = async (fn, okMsg) => {
    setBusy(true);
    try { await fn(); if (okMsg) toast({ tone: 'success', title: okMsg }); refreshStatus(); } catch (e) { fail(e); } finally { setBusy(false); }
  };
  if (!status) return html`<div className="page"><${E.Spinner} label="Loading" /></div>`;
  const running = status.state === 'ready' || status.state === 'loading';

  return html`<div className="page">
    <header className="page-head">
      <div className="page-head__titles">
        <h1 className="text-headline">Server</h1>
        <p className="text-body">One llama-server process, reachable through the API port at <span className="mono">${location.hostname}:${status.api_port}</span>.</p>
      </div>
      <div className="page-head__actions">
        ${running && html`
          <${E.Button} variant="secondary" icon="external-link" onClick=${() => window.open(`${location.protocol}//${location.hostname}:${status.api_port}/`, '_blank', 'noopener')}>Open llama.cpp chat<//>
          <${E.Button} variant="secondary" icon="refresh" disabled=${busy} onClick=${() => act(() => api.post('/api/server/restart'), 'Restarting llama-server')}>Restart<//>
          <${E.Button} variant="secondary" icon="pause" disabled=${busy} onClick=${() => act(() => api.post('/api/server/stop'), 'llama-server stopped')}>Stop<//>`}
      </div>
    </header>

    ${status.state === 'crashed' && html`<${E.Alert} tone="danger" title="llama-server exited unexpectedly.">
      <span className="mono">${status.error || 'No error message was printed.'}</span> Check the log below, adjust the preset, and start again.
    <//>`}
    ${status.llama && status.llama.error && html`<${E.Alert} tone="danger" title="llama-server can't run.">
      ${status.llama.binary}: ${status.llama.error}
    <//>`}

    <div className="grid grid--side">
      <${StatusCard} status=${status} />
      <${StartCard} status=${status} presets=${presets.data || []} busy=${busy} act=${act} />
    </div>

    ${status.argv && status.argv.length > 0 && html`<${CommandCard} status=${status} />`}
    <${LogCard} status=${status} />
    <${RunsCard} runs=${runs.data || []} />
  </div>`;
}

function StatusCard({ status }) {
  const now = useNow(1000);
  const st = status.state;
  const item = (label, value, mono) => html`<div><dt className="text-caption">${label}</dt><dd className=${mono ? 'text-code' : 'text-body'}>${value}</dd></div>`;
  const buffers = Object.entries(status.buffers || {});
  return html`<${E.Card} title="Status" actions=${html`<${StateBadge} state=${st} />`}>
    <dl className="facts">
      ${item('Model', status.model || '–')}
      ${item('Preset', status.preset ? status.preset.name : (status.model ? 'Custom' : '–'))}
      ${item(st === 'loading' ? 'Loading for' : 'Uptime', st === 'loading' && status.started_at ? fmtDuration(now - status.started_at) : (status.ready_at && st === 'ready' ? fmtDuration(now - status.ready_at) : '–'))}
      ${item('Context', isNum(status.n_ctx) ? fmtInt(status.n_ctx) + ' tokens' : '–')}
      ${item('Slots', isNum(status.slots) ? fmtInt(status.slots) : '–')}
      ${item('Process', status.pid ? 'PID ' + status.pid : '–', true)}
      ${item('Build', status.build || (status.llama && status.llama.version) || '–', true)}
      ${item('Device', status.llama && status.llama.devices && status.llama.devices.length ? status.llama.devices.join(', ') : 'None detected')}
      ${buffers.map(([k, v]) => item(k + ' buffer', fmtInt(v) + ' MiB'))}
    </dl>
  <//>`;
}

function StartCard({ status, presets, busy, act }) {
  const { go } = useApp();
  const [mode, setMode] = useState('preset');
  const [presetId, setPresetId] = useState('');
  const models = useFetch('/api/models');
  const [model, setModel] = useState('');
  const [args, setArgs] = useState('-ngl 99 -fa on -c 16384 -np 1');
  useEffect(() => {
    if (!presetId && presets.length) setPresetId(String((status.preset && status.preset.id) || presets[0].id));
  }, [presets]);
  const modelOpts = (models.data ? models.data.models : []).filter((m) => !m.is_projector)
    .map((m) => ({ value: m.path, label: `${m.name}${m.repo ? ' · ' + m.repo : ''}` }));
  useEffect(() => { if (!model && modelOpts.length) setModel(modelOpts[0].value); }, [models.data]);

  const running = status.state === 'ready' || status.state === 'loading';
  const verb = running ? 'Switch to' : 'Start';
  const start = async (body) => {
    if (running) await api.post('/api/server/stop');
    await api.post('/api/server/start', body);
  };
  const startPreset = () => act(() => start({ preset_id: Number(presetId) }), running ? 'Switching model' : 'Starting llama-server');
  const startCustom = () => act(async () => {
    const parsed = await api.post('/api/presets/parse', { command: 'llama-server ' + args });
    await start({ model, flags: parsed.flags, env: parsed.env });
  }, 'Starting llama-server');

  const chosen = presets.find((p) => String(p.id) === presetId);
  return html`<${E.Card} title=${running ? 'Switch model' : 'Start'} description=${running ? 'Stops the current process, then starts the new one.' : 'Load a model to serve requests.'}>
    <div className="stack">
      <${E.SegmentedControl} label="Start from" value=${mode} onChange=${setMode} size="sm" fullWidth
        options=${[{ value: 'preset', label: 'Preset' }, { value: 'custom', label: 'Custom flags' }]} />
      ${mode === 'preset' ? (presets.length ? html`
        <${E.Select} label="Preset" value=${presetId} onChange=${(e) => setPresetId(e.target.value)}
          options=${presets.map((p) => ({ value: String(p.id), label: p.name }))}
          hint=${chosen ? modelLabel(chosen.model) || 'No model set' : undefined} />
        <${E.Button} variant=${running ? 'secondary' : 'primary'} icon="play" loading=${busy} disabled=${!presetId} onClick=${startPreset}>${verb} preset<//>
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
        <${E.Button} variant=${running ? 'secondary' : 'primary'} icon="play" loading=${busy} disabled=${!model} onClick=${startCustom}>${verb} model<//>
      `}
    </div>
  <//>`;
}

function CommandCard({ status }) {
  const { toast } = useApp();
  const argv = status.argv;
  const envPart = Object.entries(status.env || {}).map(([k, v]) => `${k}=${shellQuote(v)}`);
  const cmd = [...envPart, ...argv.map(shellQuote)].join(' ');
  return html`<${E.Card} title="Command" description="Exactly what Kiln ran, including the host, port and metrics flags it adds.">
    <div className="code-wrap">
      <pre className="code text-code">${cmd}</pre>
      <${E.IconButton} icon="copy" label="Copy command" size="sm" variant="secondary" onClick=${() => copyText(cmd).then(() => toast({ title: 'Command copied' }))} />
    </div>
  <//>`;
}

function LogCard({ status }) {
  const [lines, setLines] = useState([]);
  const [follow, setFollow] = useState(true);
  const [filter, setFilter] = useState('');
  const box = useRef(null);
  const runRef = useRef(status.run_id);
  useEffect(() => { api.get('/api/server/logs?tail=2000').then((d) => { setLines(d.lines); runRef.current = d.run_id; }).catch(() => {}); }, [status.run_id]);
  useEvent('log', (d) => {
    if (d.run_id !== runRef.current) { runRef.current = d.run_id; setLines(d.lines); return; }
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
  return html`<${E.Card} title="Log" description=${status.run_id ? 'Run ' + status.run_id + ' · stdout and stderr of llama-server.' : 'Output appears here when the server starts.'} padding="none"
    actions=${html`<div className="card-actions">
      <${E.TextField} label="Filter log" hideLabel icon="search" size="sm" placeholder="Filter" value=${filter} onChange=${(e) => setFilter(e.target.value)} />
      <${E.Switch} label="Follow" checked=${follow} onChange=${(e) => setFollow(e.target.checked)} />
      ${status.run_id && html`<${E.IconButton} icon="download" label="Download log" size="sm" onClick=${() => { location.href = `/api/runs/${status.run_id}/log`; }} />`}
    </div>`}>
    <div className="log text-code" ref=${box} role="log" aria-live="off" tabIndex="0">
      ${shown.length === 0 ? html`<p className="log-empty text-body">${lines.length ? 'No lines match the filter.' : 'No output yet.'}</p>`
        : shown.map((l, i) => html`<div key=${i} className=${'log__line ' + cls(l)}><span className="log__time">${time(l[0])}</span><span className="log__text">${l[1]}</span></div>`)}
    </div>
  <//>`;
}

const RUN_BADGE = {
  ready: ['success', 'Running', 'check-circle'], loading: ['warning', 'Loading', 'clock'], stopped: ['neutral', 'Stopped', null],
  crashed: ['danger', 'Crashed', 'alert-circle'], lost: ['warning', 'Interrupted', 'alert-triangle'], stopping: ['neutral', 'Stopping', 'clock'],
};

function RunsCard({ runs }) {
  const now = useNow(60000);
  const columns = [
    { key: 'started_at', header: 'Started', render: (r) => html`<span className="nowrap">${fmtWhen(r.started_at, now)}</span>` },
    { key: 'preset_name', header: 'Preset', render: (r) => r.preset_name || html`<span className="subtle">Custom</span>` },
    { key: 'model', header: 'Model', render: (r) => html`<span className="truncate" style=${{ maxWidth: 280, display: 'inline-block' }} title=${r.model}>${modelLabel(r.model)}</span>` },
    { key: 'load_ms', header: 'Load time', numeric: true, render: (r) => fmtMs(r.load_ms) },
    { key: 'n_ctx', header: 'Context', numeric: true, render: (r) => fmtInt(r.n_ctx) },
    { key: 'dur', header: 'Ran for', numeric: true, render: (r) => (r.ready_at ? fmtDuration((r.stopped_at || now) - r.ready_at) : '–') },
    { key: 'status', header: 'Result', render: (r) => { const [tone, label, icon] = RUN_BADGE[r.status] || ['neutral', r.status, null]; return html`<${E.Badge} size="sm" tone=${tone} icon=${icon || undefined} dot=${!icon}>${label}<//>`; } },
    { key: 'log', header: '', align: 'end', render: (r) => html`<${E.IconButton} icon="file" size="sm" label=${'Download log of run ' + r.id} onClick=${() => { location.href = `/api/runs/${r.id}/log`; }} />` },
  ];
  return html`<${E.Card} title="Run history" padding="none">
    <${E.Table} caption="Run history" density="compact" columns=${columns} rows=${runs} rowKey=${(r) => String(r.id)} empty="No runs yet. Runs appear here after the first start." />
  <//>`;
}
