/* Requests: the full, paged request log with filters and CSV export. */
import { html, E, useState, useMemo, useApp, useFetch, useEvent, useDebounced, useNow, fmtInt, fmtTps, fmtMs, fmtPct, fmtWhen, fmtDateTime, isNum } from '../lib.js';
import { RequestStatus } from './overview.js';

const PAGE = 50;

export function RequestsPage() {
  const [model, setModel] = useState('');
  const [statusF, setStatusF] = useState('all');
  const [offset, setOffset] = useState(0);
  const [tick, setTick] = useState(0);
  const now = useNow(30000);
  const q = `limit=${PAGE}&offset=${offset}${model ? '&model=' + encodeURIComponent(model) : ''}${statusF !== 'all' ? '&status=' + statusF : ''}`;
  const data = useFetch(`/api/requests?${q}`, [tick]);
  const models = useFetch('/api/stats/models?range=all');
  const refresh = useDebounced(() => { if (offset === 0) setTick((t) => t + 1); }, 1000);
  useEvent('request', refresh);

  const modelOptions = useMemo(() => [{ value: '', label: 'All models' }, ...((models.data || []).map((m) => ({ value: m.model, label: m.model })))], [models.data]);
  const total = data.data ? data.data.total : 0;

  const columns = [
    { key: 'ts', header: 'When', render: (r) => html`<span className="nowrap" title=${fmtDateTime(r.ts)}>${fmtWhen(r.ts, now)}</span>` },
    { key: 'model', header: 'Model', render: (r) => html`<div className="cell-main"><span className="truncate" style=${{ maxWidth: 220 }} title=${r.model}>${r.model || '–'}</span>
      <span className="cell-sub text-caption mono">${r.endpoint}${r.stream ? ' · stream' : ''}</span></div>` },
    { key: 'prompt_n', header: 'Prompt', numeric: true, render: (r) => fmtInt(r.prompt_n) },
    { key: 'cache_n', header: 'Cached', numeric: true, render: (r) => fmtInt(r.cache_n) },
    { key: 'predicted_n', header: 'Generated', numeric: true, render: (r) => fmtInt(r.predicted_n) },
    { key: 'prompt_tps', header: 'Prefill t/s', numeric: true, render: (r) => fmtTps(r.prompt_tps) },
    { key: 'predicted_tps', header: 'Decode t/s', numeric: true, render: (r) => fmtTps(r.predicted_tps) },
    { key: 'ttft_ms', header: 'TTFT', numeric: true, render: (r) => fmtMs(r.ttft_ms) },
    { key: 'total_ms', header: 'Total', numeric: true, render: (r) => fmtMs(r.total_ms) },
    { key: 'draft', header: 'Draft', numeric: true, render: (r) => (r.draft_n ? fmtPct(r.draft_accepted / r.draft_n, 0) : '–') },
    { key: 'power_w', header: 'Power', numeric: true, render: (r) => (isNum(r.power_w) ? fmtInt(r.power_w) + ' W' : '–') },
    { key: 'status', header: 'Result', render: (r) => html`<div className="row" style=${{ flexWrap: 'nowrap' }}><${RequestStatus} r=${r} />${r.finish_reason && html`<span className="text-caption subtle">${r.finish_reason}</span>`}</div>` },
  ];

  return html`<div className="page">
    <header className="page-head">
      <div className="page-head__titles">
        <h1 className="text-headline">Requests</h1>
        <p className="text-body">${fmtInt(total)} recorded${model ? ' for ' + model : ''}. Times come from llama-server's own timings.</p>
      </div>
      <div className="page-head__actions">
        <${E.Button} variant="secondary" icon="download" onClick=${() => { location.href = `/api/requests.csv?range=all${model ? '&model=' + encodeURIComponent(model) : ''}`; }}>Export CSV<//>
      </div>
    </header>
    <div className="filters">
      <${E.SegmentedControl} label="Result" value=${statusF} onChange=${(v) => { setStatusF(v); setOffset(0); }}
        options=${[{ value: 'all', label: 'All' }, { value: 'ok', label: 'Succeeded' }, { value: 'errors', label: 'Failed' }]} />
      <${E.Select} label="Model" hideLabel options=${modelOptions} value=${model} onChange=${(e) => { setModel(e.target.value); setOffset(0); }} />
    </div>
    <${E.Card} padding="none" className=${data.loading ? 'is-refreshing' : ''}
      footer=${total > PAGE && html`<div className="pager text-caption" style=${{ width: '100%' }}>
        <span>${fmtInt(offset + 1)}–${fmtInt(Math.min(offset + PAGE, total))} of ${fmtInt(total)}</span>
        <${E.IconButton} icon="chevron-left" size="sm" label="Newer" disabled=${offset === 0} onClick=${() => setOffset(Math.max(0, offset - PAGE))} />
        <${E.IconButton} icon="chevron-right" size="sm" label="Older" disabled=${offset + PAGE >= total} onClick=${() => setOffset(offset + PAGE)} />
      </div>`}>
      <${E.Table} caption="Request log" density="compact" columns=${columns} rows=${data.data ? data.data.rows : []} rowKey=${(r) => String(r.id)}
        empty="No requests yet. They appear here as soon as a client calls the API port." />
    <//>
  </div>`;
}
