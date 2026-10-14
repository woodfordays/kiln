/* Overview: live server strip, hero decode speed, stat cards, performance charts, totals. */
import {
  html, E, useState, useEffect, useMemo, useRef, api, useApp, useFetch, useEvent, useDebounced, useNow,
  fmtInt, fmtCompact, fmtTps, fmtMs, fmtPct, fmtDuration, fmtBytes, fmtWhen, fmtDateTime, fmtAxisTime, isNum,
  RANGE_OPTIONS, RANGE_WORDS, PREV_WORDS, delta, StateBadge,
} from '../lib.js';
import { LineChart, StackedColumns, Scatter, Legend, Meter, seriesColor } from '../charts.js';

const loadPref = (k, d) => { try { return localStorage.getItem(k) || d; } catch { return d; } };
const savePref = (k, v) => { try { localStorage.setItem(k, v); } catch {} };

export function OverviewPage() {
  const { status } = useApp();
  const [range, setRange] = useState(loadPref('kiln-range', '24h'));
  const [model, setModel] = useState('');
  const [tick, setTick] = useState(0);
  useEffect(() => savePref('kiln-range', range), [range]);

  const q = `range=${range}${model ? '&model=' + encodeURIComponent(model) : ''}`;
  const summary = useFetch(`/api/stats/summary?${q}`, [tick]);
  const series = useFetch(`/api/stats/timeseries?${q}`, [tick]);
  const scatter = useFetch(`/api/stats/scatter?${q}`, [tick]);
  const models = useFetch(`/api/stats/models?range=${range}`, [tick]);
  const lifetime = useFetch('/api/stats/lifetime', [tick]);
  const recent = useFetch('/api/requests?limit=8', [tick]);

  const refresh = useDebounced(() => setTick((t) => t + 1), 1500);
  useEvent('request', refresh);

  const autoPicked = useRef(false);
  useEffect(() => {
    if (autoPicked.current || !models.data || !status) return;
    autoPicked.current = true;
    if (!model && models.data.length > 1 && models.data.some((m) => m.model === status.model)) setModel(status.model);
  }, [models.data, status]);

  const modelOptions = useMemo(() => {
    const names = (models.data || []).map((m) => m.model).filter(Boolean);
    if (model && !names.includes(model)) names.unshift(model);
    return [{ value: '', label: 'All models' }, ...names.map((n) => ({ value: n, label: n }))];
  }, [models.data, model]);

  const loading = summary.loading || series.loading;
  const s = summary.data;
  const noData = s && s.current.requests === 0 && lifetime.data && lifetime.data.requests === 0;

  return html`<div className="page">
    <header className="page-head">
      <div className="page-head__titles">
        <h1 className="text-headline">Overview</h1>
        <p className="text-body">Every request through the API port, measured by llama-server and kept on disk.</p>
      </div>
      <div className="page-head__actions">
        <${E.Button} variant="secondary" icon="download" onClick=${() => { location.href = `/api/requests.csv?${q}`; }}>Export CSV<//>
      </div>
    </header>

    <${LiveStrip} status=${status} />

    <div className="filters">
      <${E.SegmentedControl} label="Time range" options=${RANGE_OPTIONS} value=${range} onChange=${setRange} />
      <${E.Select} label="Model" hideLabel options=${modelOptions} value=${model} onChange=${(e) => setModel(e.target.value)} size="md" />
    </div>

    ${noData ? html`<${FirstRun} status=${status} />` : html`
      <div className=${loading ? 'is-refreshing stack' : 'stack'} style=${{ gap: 'var(--space-8)' }}>
        ${s && html`<${Hero} s=${s} range=${range} mixed=${!model && models.data ? models.data.length : 0} />`}
        ${s && html`<${StatRow} s=${s} range=${range} />`}
        <div className="grid grid--2">
          <${SpeedChart} data=${series.data} metric="gen_tps" title="Decode speed" description="Generated tokens per second, token-weighted per bucket." />
          <${SpeedChart} data=${series.data} metric="pp_tps" title="Prefill speed" description="Prompt tokens processed per second." />
          <${TokensChart} data=${series.data} className="span-2" />
          <${DepthChart} data=${scatter.data} className="span-2" />
        </div>
        <${ModelsCard} rows=${models.data} range=${range} />
        <div className="grid grid--side">
          <${RecentCard} data=${recent.data} />
          <${GpuCard} />
        </div>
        <${LifetimeCard} data=${lifetime.data} />
      </div>`}
  </div>`;
}

/* ------------------------------------------------------------------ live strip */

function LiveStrip({ status }) {
  const { go, toast, fail, refreshStatus } = useApp();
  const now = useNow(1000);
  const [busy, setBusy] = useState(false);
  if (!status) return null;
  const st = status.state;
  const act = async (path, msg) => {
    setBusy(true);
    try { await api.post(path); toast({ tone: 'success', title: msg }); refreshStatus(); } catch (e) { fail(e); } finally { setBusy(false); }
  };
  const uptime = status.ready_at && st === 'ready' ? now - status.ready_at : null;
  return html`<section className="live" aria-label="Server">
    <div className="live__info">
      <${StateBadge} state=${st} />
      ${status.model ? html`<div className="live__model">
        <span className="text-subtitle truncate">${status.model}</span>
        <div className="live__meta text-caption">
          ${status.preset && html`<span>Preset ${status.preset.name}</span>`}
          ${isNum(status.n_ctx) && html`<span>Context ${fmtInt(status.n_ctx)}</span>`}
          ${uptime != null && html`<span>Up ${fmtDuration(uptime)}</span>`}
          ${st === 'loading' && status.started_at && html`<span>Loading for ${fmtDuration(now - status.started_at)}</span>`}
          ${st === 'ready' && html`<span className="mono">:${status.api_port}/v1</span>`}
        </div>
      </div>` : html`<span className="text-body muted">No model loaded. Start a preset to serve requests on port ${status.api_port}.</span>`}
    </div>
    <div className="live__actions">
      ${(st === 'ready' || st === 'loading') ? html`
        <${E.Button} variant="secondary" icon="refresh" disabled=${busy} onClick=${() => act('/api/server/restart', 'Restarting llama-server')}>Restart<//>
        <${E.Button} variant="secondary" icon="pause" disabled=${busy} onClick=${() => act('/api/server/stop', 'llama-server stopped')}>Stop<//>
      ` : html`<${E.Button} variant="primary" icon="play" onClick=${() => go('server')}>Start server<//>`}
    </div>
  </section>`;
}

function FirstRun({ status }) {
  const { go } = useApp();
  return html`<${E.Card}>
    <${E.EmptyState} size="lg" icon="activity" title="No requests yet"
      description=${`Charts fill in after the first completion. Load a model, then point an OpenAI client at http://${location.hostname}:${status ? status.api_port : 8080}/v1.`}
      actions=${html`<${E.Button} variant="secondary" icon="database" onClick=${() => go('models')}>Download a model<//>
        <${E.Button} variant="secondary" icon="file" onClick=${() => go('presets')}>Create a preset<//>`} />
  <//>`;
}

/* ------------------------------------------------------------------ hero + stats */

function DeltaText({ value, goodWhen = 'up', label }) {
  if (!isNum(value)) return null;
  const up = value > 0;
  const tone = Math.abs(value) < 0.05 ? 'neutral' : (up === (goodWhen === 'up') ? 'success' : 'danger');
  const icon = Math.abs(value) < 0.05 ? 'minus' : up ? 'arrow-up-right' : 'arrow-down-right';
  return html`<span className="text-caption muted">
    <span className=${'em-delta em-delta--' + tone}><${E.Icon} name=${icon} size=${14} />${(up ? '+' : '') + value.toFixed(1)}%</span> ${label}
  </span>`;
}

function Hero({ s, range, mixed }) {
  const c = s.current, p = s.previous;
  return html`<section className="hero" aria-label="Decode speed">
    <div className="hero__main">
      <span className="text-eyebrow eyebrow">Decode speed</span>
      <p className="hero__value">
        <span className="text-metric-lg">${fmtTps(c.gen_tps)}</span>
        <span className="hero__unit text-title">t/s</span>
      </p>
      <p className="hero__note text-caption">
        Token-weighted over ${fmtInt(c.requests)} ${c.requests === 1 ? 'request' : 'requests'} in ${RANGE_WORDS[range]}${mixed > 1 ? `, mixed across ${mixed} models. Pick one model for a like-for-like number` : ''}.
        ${p && html` <${DeltaText} value=${delta(c.gen_tps, p.gen_tps)} label=${PREV_WORDS[range]} />`}
      </p>
    </div>
    <dl className="hero__side">
      <div className="hero__fact"><dt className="text-caption muted">Median per request</dt><dd className="text-body"><strong>${fmtTps(c.gen_p50)}</strong> t/s</dd></div>
      <div className="hero__fact"><dt className="text-caption muted">Slowest 10%</dt><dd className="text-body"><strong>${fmtTps(c.gen_p10)}</strong> t/s</dd></div>
      <div className="hero__fact"><dt className="text-caption muted">Draft acceptance</dt><dd className="text-body"><strong>${c.draft_rate != null ? fmtPct(c.draft_rate) : '–'}</strong></dd></div>
    </dl>
  </section>`;
}

function StatRow({ s, range }) {
  const c = s.current, p = s.previous;
  const labels = s.trend.map((t) => fmtDateTime(t.t));
  const trend = (k) => s.trend.map((t) => (isNum(t[k]) ? t[k] : 0));
  const dl = p ? PREV_WORDS[range] : undefined;
  return html`<div className="grid grid--4">
    <${E.StatCard} label="Requests" value=${fmtCompact(c.requests)} delta=${p ? delta(c.requests, p.requests) : undefined} deltaLabel=${dl}
      trend=${trend('requests')} trendLabels=${labels} formatValue=${fmtInt} />
    <${E.StatCard} label="Tokens generated" value=${fmtCompact(c.predicted_n)} delta=${p ? delta(c.predicted_n, p.predicted_n) : undefined} deltaLabel=${dl}
      trend=${trend('predicted_n')} trendLabels=${labels} formatValue=${fmtInt} />
    <${E.StatCard} label="Prefill speed" value=${fmtTps(c.pp_tps)} unit="t/s" delta=${p ? delta(c.pp_tps, p.pp_tps) : undefined} deltaLabel=${dl}
      trend=${trend('pp_tps')} trendLabels=${labels} formatValue=${(v) => fmtTps(v) + ' t/s'} />
    <${E.StatCard} label="Time to first token" value=${isNum(c.ttft_p50) ? (c.ttft_p50 < 1000 ? fmtInt(c.ttft_p50) : (c.ttft_p50 / 1000).toFixed(2)) : '–'}
      unit=${isNum(c.ttft_p50) ? (c.ttft_p50 < 1000 ? 'ms' : 's') : undefined}
      delta=${p ? delta(c.ttft_p50, p.ttft_p50) : undefined} deltaLabel=${dl} goodWhen="down"
      trend=${trend('ttft_p50')} trendLabels=${labels} formatValue=${fmtMs} />
  </div>`;
}

/* ------------------------------------------------------------------ chart cards */

function TableToggle({ on, set }) {
  return html`<${E.IconButton} icon=${on ? 'activity' : 'menu'} label=${on ? 'Show chart' : 'Show as table'} size="sm" onClick=${() => set(!on)} />`;
}

function SpeedChart({ data, metric, title, description }) {
  const [table, setTable] = useState(false);
  if (!data) return html`<${E.Card} title=${title} description=${description}><div style=${{ height: 220 }}></div><//>`;
  const xs = data.buckets.map((b) => b.t);
  const fx = (t, long) => (long ? fmtDateTime(t) : fmtAxisTime(t, data.bucket_s));
  const series = data.series
    .filter((s) => s[metric].some(isNum))
    .sort((a, b) => (a.slot || 99) - (b.slot || 99))
    .map((s) => ({ key: s.model, label: s.model || 'Unknown', slot: s.slot, values: s[metric] }));
  const rows = xs.map((t, i) => ({ t, ...Object.fromEntries(series.map((s) => [s.key, s.values[i]])) })).filter((r) => series.some((s) => isNum(r[s.key])));
  return html`<${E.Card} title=${title} description=${description} actions=${html`<${TableToggle} on=${table} set=${setTable} />`}>
    ${table ? html`<div className="table-scroll"><${E.Table} caption=${title} density="compact" rows=${rows} rowKey=${(r) => String(r.t)}
        columns=${[{ key: 't', header: 'Time', render: (r) => fmtDateTime(r.t) },
          ...series.map((s) => ({ key: s.key, header: s.label, numeric: true, render: (r) => fmtTps(r[s.key]) }))]} /></div>`
      : html`<${Legend} items=${series} kind="line" />
        <${LineChart} xs=${xs} series=${series} height=${220} xFormat=${fx} yFormat=${fmtTps} unit="t/s" label=${title} />`}
  <//>`;
}

function TokensChart({ data, className }) {
  const [table, setTable] = useState(false);
  const title = 'Tokens';
  const description = 'Generated, prefilled and reused from the prompt cache, per time bucket.';
  if (!data) return html`<${E.Card} className=${className} title=${title} description=${description}><div style=${{ height: 240 }}></div><//>`;
  const xs = data.buckets.map((b) => b.t);
  const fx = (t, long) => (long ? fmtDateTime(t) : fmtAxisTime(t, data.bucket_s));
  const stacks = [
    { key: 'predicted_n', label: 'Generated', slot: 1, values: data.buckets.map((b) => b.predicted_n) },
    { key: 'prompt_n', label: 'Prefilled', slot: 2, values: data.buckets.map((b) => b.prompt_n) },
    { key: 'cache_n', label: 'From cache', slot: 3, values: data.buckets.map((b) => b.cache_n) },
  ];
  const rows = data.buckets.filter((b) => b.requests > 0);
  return html`<${E.Card} className=${className} title=${title} description=${description} actions=${html`<${TableToggle} on=${table} set=${setTable} />`}>
    ${table ? html`<div className="table-scroll"><${E.Table} caption=${title} density="compact" rows=${rows} rowKey=${(r) => String(r.t)}
        columns=${[{ key: 't', header: 'Time', render: (r) => fmtDateTime(r.t) },
          { key: 'requests', header: 'Requests', numeric: true, render: (r) => fmtInt(r.requests) },
          ...stacks.map((s) => ({ key: s.key, header: s.label, numeric: true, render: (r) => fmtInt(r[s.key]) }))]} /></div>`
      : html`<${Legend} items=${stacks} kind="rect" />
        <${StackedColumns} xs=${xs} stacks=${stacks} height=${240} xFormat=${fx} yFormat=${fmtCompact} label=${title} />`}
  <//>`;
}

function DepthChart({ data, className }) {
  const [metric, setMetric] = useState('predicted_tps');
  const [table, setTable] = useState(false);
  const title = metric === 'predicted_tps' ? 'Decode speed by context depth' : 'Prefill speed by context depth';
  const description = 'Each dot is one request. Depth is the KV position it started at: cached plus new prompt tokens.';
  if (!data) return html`<${E.Card} className=${className} title=${title} description=${description}><div style=${{ height: 280 }}></div><//>`;
  const points = data.points.filter((p) => isNum(p[metric])).map((p) => ({ ...p, x: p.depth, y: p[metric], t: p.ts }));
  const legend = Object.entries(data.slots).sort((a, b) => a[1] - b[1])
    .filter(([name]) => points.some((p) => p.model === name)).map(([name, slot]) => ({ label: name, slot }));
  if (points.some((p) => !p.slot)) legend.push({ label: 'Other', slot: null });
  const actions = html`<div className="card-actions">
    <${E.SegmentedControl} size="sm" label="Metric" value=${metric} onChange=${setMetric}
      options=${[{ value: 'predicted_tps', label: 'Decode' }, { value: 'prompt_tps', label: 'Prefill' }]} />
    <${TableToggle} on=${table} set=${setTable} />
  </div>`;
  const tip = (p) => html`<div className="chart-tip__title">${fmtDateTime(p.ts)}</div>
    <div className="chart-tip__row"><span className="chart-key chart-key--dot" style=${{ '--key': seriesColor(p.slot) }}></span><strong>${fmtTps(p.y)} t/s</strong></div>
    <div className="chart-tip__row"><span className="chart-tip__label">${p.model}</span></div>
    <div className="chart-tip__row"><span className="chart-tip__label">Depth ${fmtInt(p.depth)} · ${fmtInt(p.predicted_n)} generated</span></div>`;
  return html`<${E.Card} className=${className} title=${title} description=${description} actions=${actions}>
    ${table ? html`<div className="table-scroll"><${E.Table} caption=${title} density="compact" rows=${points.slice(0, 500)} rowKey=${(r) => String(r.id)}
        columns=${[{ key: 'ts', header: 'Time', render: (r) => fmtDateTime(r.ts) }, { key: 'model', header: 'Model' },
          { key: 'depth', header: 'Depth', numeric: true, render: (r) => fmtInt(r.depth) },
          { key: 'y', header: metric === 'predicted_tps' ? 'Decode t/s' : 'Prefill t/s', numeric: true, render: (r) => fmtTps(r.y) }]} /></div>`
      : html`<${Legend} items=${legend} kind="dot" />
        <${Scatter} points=${points} height=${280} xFormat=${fmtCompact} yFormat=${fmtTps} xLabel="Context depth (tokens)" yLabel="t/s" label=${title} tip=${tip} />`}
  <//>`;
}

/* ------------------------------------------------------------------ models, GPU, recent, lifetime */

function ModelsCard({ rows, range }) {
  const columns = [
    { key: 'model', header: 'Model', render: (r) => html`<span className="cell-key"><span className="chart-key chart-key--dot" style=${{ '--key': seriesColor(r.slot) }}></span><span className="truncate" style=${{ maxWidth: 260 }} title=${r.model}>${r.model || 'Unknown'}</span></span>` },
    { key: 'requests', header: 'Requests', numeric: true, sortable: true, render: (r) => fmtInt(r.requests) },
    { key: 'predicted_n', header: 'Generated', numeric: true, sortable: true, render: (r) => fmtCompact(r.predicted_n) },
    { key: 'gen_tps', header: 'Decode t/s', numeric: true, sortable: true, render: (r) => fmtTps(r.gen_tps) },
    { key: 'pp_tps', header: 'Prefill t/s', numeric: true, sortable: true, render: (r) => fmtTps(r.pp_tps) },
    { key: 'ttft_p50', header: 'TTFT p50', numeric: true, sortable: true, render: (r) => fmtMs(r.ttft_p50) },
    { key: 'draft_rate', header: 'Draft', numeric: true, render: (r) => (r.draft_rate != null ? fmtPct(r.draft_rate, 0) : '–') },
  ];
  return html`<${E.Card} title="Models" description=${'Performance per model in ' + RANGE_WORDS[range] + '.'} padding="none">
    <${E.Table} caption="Performance per model" columns=${columns} rows=${rows || []} rowKey=${(r) => r.model || '?'}
      empty="No requests in this range." />
  <//>`;
}

function GpuCard() {
  const live = useFetch('/api/gpu/live');
  const [samples, setSamples] = useState([]);
  useEffect(() => { if (live.data) setSamples(live.data.samples); }, [live.data]);
  useEvent('gpu', (s) => setSamples((xs) => [...xs.slice(-299), s]));
  if (live.data && !live.data.available) {
    return html`<${E.Card} title="GPU"><${E.EmptyState} icon="server" title="No GPU telemetry"
      description="Kiln reads AMD cards from /sys/class/drm and NVIDIA cards with nvidia-smi. Check that the container has the GPU (compose.amd.yml or compose.nvidia.yml)." /><//>`;
  }
  const last = samples[samples.length - 1];
  const xs = samples.map((s) => s.t);
  const fx = (t) => fmtWhen(t);
  const gpuName = live.data && live.data.name;
  return html`<${E.Card} title="GPU" description=${(gpuName ? gpuName + ' · ' : '') + 'live, last 10 minutes.'}>
    <div className="stack">
      ${last && html`<${Meter} label="VRAM" value=${last.vram_used} max=${last.vram_total}
        valueText=${fmtBytes(last.vram_used) + ' of ' + fmtBytes(last.vram_total)} />`}
      <dl className="facts facts--tight">
        <div><dt className="text-caption">Busy</dt><dd className="text-subtitle">${last && isNum(last.busy) ? fmtInt(last.busy) + '%' : '–'}</dd></div>
        <div><dt className="text-caption">Power</dt><dd className="text-subtitle">${last && isNum(last.power_w) ? fmtInt(last.power_w) + ' W' : '–'}</dd></div>
        <div><dt className="text-caption">Junction</dt><dd className="text-subtitle">${last && isNum(last.temp_c) ? fmtInt(last.temp_c) + ' °C' : '–'}</dd></div>
      </dl>
      <div>
        <p className="text-caption muted">Power draw</p>
        <${LineChart} xs=${xs} series=${[{ key: 'p', label: 'Power', slot: 1, values: samples.map((s) => s.power_w) }]}
          height=${120} xFormat=${fx} yFormat=${(v) => fmtInt(v)} unit="W" label="GPU power draw" empty="Collecting samples…" />
      </div>
      <div>
        <p className="text-caption muted">GPU busy</p>
        <${LineChart} xs=${xs} series=${[{ key: 'b', label: 'Busy', slot: 1, values: samples.map((s) => s.busy) }]}
          height=${120} xFormat=${fx} yFormat=${(v) => fmtInt(v) + '%'} minTop=${100} label="GPU busy percent" empty="Collecting samples…" />
      </div>
    </div>
  <//>`;
}

function RecentCard({ data }) {
  const { go } = useApp();
  const now = useNow(30000);
  const columns = [
    { key: 'ts', header: 'When', render: (r) => html`<span className="nowrap">${fmtWhen(r.ts, now)}</span>` },
    { key: 'endpoint', header: 'Endpoint', mono: true },
    { key: 'prompt_n', header: 'Prompt', numeric: true, render: (r) => fmtInt(r.prompt_n) },
    { key: 'predicted_n', header: 'Generated', numeric: true, render: (r) => fmtInt(r.predicted_n) },
    { key: 'predicted_tps', header: 'Decode t/s', numeric: true, render: (r) => fmtTps(r.predicted_tps) },
    { key: 'status', header: 'Status', render: (r) => html`<${RequestStatus} r=${r} />` },
  ];
  return html`<${E.Card} title="Recent requests" padding="none"
    footer=${html`<${E.Button} variant="ghost" size="sm" iconRight="arrow-right" onClick=${() => go('requests')}>View all requests<//>`}>
    <${E.Table} caption="Recent requests" density="compact" columns=${columns} rows=${data ? data.rows : []} rowKey=${(r) => String(r.id)} empty="No requests yet." />
  <//>`;
}

export function RequestStatus({ r }) {
  if (r.error === 'client disconnected') return html`<${E.Badge} tone="neutral" size="sm" icon="x">Aborted<//>`;
  if (r.status >= 400 || r.error) return html`<${E.Badge} tone="danger" size="sm" icon="alert-circle">${r.status >= 400 ? 'HTTP ' + r.status : 'Error'}<//>`;
  return html`<${E.Badge} tone="success" size="sm" icon="check-circle">OK<//>`;
}

function LifetimeCard({ data }) {
  const d = data;
  const item = (label, value) => html`<div><dt className="text-caption">${label}</dt><dd className="text-subtitle">${value}</dd></div>`;
  return html`<${E.Card} title="All-time totals" description=${d && d.first_ts ? 'Recording since ' + fmtDateTime(d.first_ts) + '.' : 'Kept until you reset them in Settings.'}>
    ${d && html`<dl className="facts facts--tight">
      ${item('Requests', fmtInt(d.requests))}
      ${item('Tokens generated', fmtCompact(d.predicted_n))}
      ${item('Prompt tokens', fmtCompact(d.prompt_n))}
      ${item('From cache', fmtCompact(d.cache_n))}
      ${item('Generating', fmtDuration(d.gen_ms / 1000))}
      ${item('Prefilling', fmtDuration(d.pp_ms / 1000))}
      ${item('Model loads', fmtInt(d.loads))}
      ${item('Server uptime', fmtDuration(d.uptime_s))}
      ${item('Models used', fmtInt(d.models))}
      ${item('Downloaded', fmtBytes(d.downloaded_bytes))}
    </dl>`}
  <//>`;
}
