/* Memory and context visualisations: segmented memory bars, the per-model breakdown,
   live context fill per slot. Pure presentation plus the few fetches they need. */
import { html, useState, useEffect, useRef, useCallback, E, api, useFetch, useEvent, useDebounced, isNum, fmtMiB, fmtInt } from './lib.js';

export const MEM_COLORS = {
  weights: 'var(--chart-1)',
  mtp: 'var(--chart-6)',
  draft: 'var(--chart-3)',
  projector: 'var(--chart-4)',
  kv: 'var(--chart-2)',
  compute: 'var(--chart-5)',
  overhead: 'var(--ink-subtle)',
  other: 'var(--line-strong)',
};
export const MEM_LABELS = {
  weights: 'Model weights', mtp: 'MTP head', draft: 'Draft model', projector: 'Vision projector',
  kv: 'KV cache', compute: 'Compute buffers', overhead: 'Overhead', other: 'Other use', free: 'Free',
};

const sumOf = (o) => Object.values(o || {}).reduce((a, v) => a + (isNum(v) ? v : 0), 0);

/* ------------------------------------------------------------------ segmented bar */

export function StackedMeter({ segments, total, label, valueText, compareMib, compareLabel = 'Measured', height, compact }) {
  const segs = (segments || []).filter((s) => isNum(s.mib) && s.mib > 0);
  const sum = segs.reduce((a, s) => a + s.mib, 0);
  const hasTotal = isNum(total) && total > 0;
  const scale = hasTotal ? total : sum;
  const over = hasTotal && sum > total ? sum - total : 0;
  const free = hasTotal ? Math.max(0, total - sum) : 0;
  const pct = (m) => (scale > 0 ? Math.min(100, (m / scale) * 100) : 0);
  const showCompare = isNum(compareMib) && compareMib > 0 && scale > 0;

  const legend = segs.map((s) => ({ key: s.key, label: s.label || MEM_LABELS[s.key] || s.key, mib: s.mib, color: s.color || MEM_COLORS[s.key] || 'var(--ink-subtle)', muted: s.muted }));
  const summary = [label, valueText, ...legend.map((l) => `${l.label} ${fmtMiB(l.mib)}`), hasTotal ? `Free ${fmtMiB(free)}` : null,
    over ? `over by ${fmtMiB(over)}` : null, showCompare ? `${compareLabel} ${fmtMiB(compareMib)}` : null].filter(Boolean).join(', ');

  return html`<div className=${'memviz-meter' + (compact ? ' is-compact' : '') + (over ? ' is-over' : '')}>
    ${(label || valueText) && html`<div className="memviz-meter__head text-caption">
      <span className="memviz-meter__label">${label}</span>
      <span className="memviz-meter__value">${valueText}${over > 0 && html` <strong className="memviz-over">over by ${fmtMiB(over)}</strong>`}</span>
    </div>`}
    <div className="memviz-bar" role="img" aria-label=${summary} style=${height ? { height: height + 'px' } : undefined}>
      ${legend.map((l) => {
        // When over, shrink every segment proportionally so the bar still ends at 100%.
        const w = over ? (l.mib / sum) * 100 : pct(l.mib);
        return html`<span key=${l.key} className=${'memviz-seg' + (l.muted ? ' is-muted' : '')} style=${{ width: w + '%', background: l.color }}
          title=${`${l.label} · ${fmtMiB(l.mib)}`}></span>`;
      })}
      ${hasTotal && free > 0 && html`<span className="memviz-seg memviz-seg--free" style=${{ flex: '1 1 0' }} title=${`Free · ${fmtMiB(free)}`}></span>`}
      ${showCompare && html`<span className="memviz-marker" style=${{ left: pct(compareMib) + '%' }} title=${`${compareLabel} · ${fmtMiB(compareMib)}`}></span>`}
    </div>
    <ul className="memviz-legend text-caption">
      ${legend.map((l) => html`<li key=${l.key}><span className="memviz-swatch" style=${{ background: l.color }} aria-hidden="true"></span>
        <span>${l.label}</span><span className="memviz-legend__v">${fmtMiB(l.mib)}</span></li>`)}
      ${hasTotal && free > 0 && html`<li><span className="memviz-swatch memviz-swatch--free" aria-hidden="true"></span><span>${MEM_LABELS.free}</span><span className="memviz-legend__v">${fmtMiB(free)}</span></li>`}
      ${showCompare && html`<li><span className="memviz-swatch memviz-swatch--marker" aria-hidden="true"></span><span>${compareLabel}</span><span className="memviz-legend__v">${fmtMiB(compareMib)}</span></li>`}
    </ul>
  </div>`;
}

/* ------------------------------------------------------------------ per-model breakdown */

const toSeg = (key, mib, label) => ({ key, label: label || MEM_LABELS[key] || key, mib });

function deviceBars(data, preview) {
  const est = (data.gpu || []).filter((s) => s.mib > 0);
  const devs = (data.devices || []).filter((d) => isNum(d.total_mib) && d.total_mib > 0);
  // the bar draws the estimate, so "other" is what the estimate (or a measured load) leaves unexplained
  const footprint = isNum(data.measured_mib) ? data.measured_mib : data.gpu_mib;
  if (!devs.length) {
    return { note: null, bars: [{ name: 'GPU (no telemetry)', total: null, segs: est.map((s) => toSeg(s.key, s.mib, s.label)), compare: null }] };
  }
  const sorted = [...devs].sort((a, b) => String(a.name).localeCompare(String(b.name)));
  if (sorted.length === 1) {
    const d = sorted[0];
    const other = preview ? d.used_mib : Math.max(0, d.used_mib - footprint);
    const cleaned = [...(other > 0 ? [toSeg('other', other)] : []), ...est.map((s) => toSeg(s.key, s.mib, s.label))];
    return { note: null, bars: [{ name: d.name, total: d.total_mib, segs: cleaned,
      compare: isNum(data.measured_mib) ? (other > 0 ? other : 0) + data.measured_mib : null }] };
  }
  // llama.cpp names devices Vulkan0, CUDA1...; telemetry names cards by model, so pair them in order
  const byDev = data.actual && data.actual.devices;
  const devKeys = byDev ? Object.keys(byDev).sort() : [];
  const paired = devKeys.length === sorted.length;
  const bars = sorted.map((d, i) => {
    let mine;
    if (paired) mine = Object.entries(byDev[devKeys[i]]).map(([k, v]) => toSeg(k, v));
    else mine = est.map((s) => toSeg(s.key, s.mib / sorted.length, s.label));
    const own = mine.reduce((a, s) => a + s.mib, 0);
    const other = preview ? d.used_mib : Math.max(0, d.used_mib - own);
    return { name: d.name, total: d.total_mib, segs: [...(other > 0 ? [toSeg('other', other)] : []), ...mine], compare: null };
  });
  return { note: paired ? null : 'Estimate split evenly across the GPUs.', bars };
}

export function MemoryBreakdown({ data, compact, preview }) {
  if (!data) return null;
  const { bars, note } = deviceBars(data, preview);
  const actual = data.actual;
  const keys = [];
  for (const s of data.gpu || []) if (!keys.includes(s.key)) keys.push(s.key);
  if (actual) for (const k of Object.keys(actual.gpu || {})) if (!keys.includes(k)) keys.push(k);
  const estOf = (k) => ((data.gpu || []).find((s) => s.key === k) || {}).mib;
  const ramSegs = (data.ram || []).filter((s) => s.mib > 0).map((s) => toSeg(s.key, s.mib, s.label));
  const caption = [
    isNum(data.context) ? `ctx ${fmtInt(data.context)}${isNum(data.np) ? ` × ${data.np} ${data.np === 1 ? 'slot' : 'slots'}` : ''}` : null,
    data.ctk || data.ctv ? `KV ${data.ctk || 'f16'}/${data.ctv || data.ctk || 'f16'}` : null,
  ].filter(Boolean).join(' · ');
  const showTable = actual && !compact && keys.length > 0;

  return html`<div className=${'memviz stack stack--sm' + (compact ? ' is-compact' : '')}>
    ${bars.map((b) => html`<${StackedMeter} key=${b.name} compact=${compact} label=${b.name}
      segments=${b.segs} total=${b.total} compareMib=${b.compare} compareLabel="Measured"
      valueText=${b.total ? `${fmtMiB(b.segs.reduce((a, s) => a + s.mib, 0))} of ${fmtMiB(b.total)}` : fmtMiB(data.gpu_mib)} />`)}
    ${note && html`<p className="text-caption muted">${note}</p>`}
    ${preview && bars.some((b) => b.total) && html`<p className="text-caption muted">The bar adds this model to what is already in use.</p>`}
    ${showTable && html`<table className="memviz-table text-caption">
      <caption className="memviz-table__cap">Estimated vs. actual (from llama.cpp log)</caption>
      <thead><tr><th scope="col">Part</th><th scope="col">Estimated</th><th scope="col">Actual</th></tr></thead>
      <tbody>${keys.map((k) => html`<tr key=${k}><th scope="row">${MEM_LABELS[k] || k}</th>
        <td>${isNum(estOf(k)) ? fmtMiB(estOf(k)) : '–'}</td><td>${isNum(actual.gpu && actual.gpu[k]) ? fmtMiB(actual.gpu[k]) : '–'}</td></tr>`)}</tbody>
    </table>`}
    ${isNum(data.ram_mib) && data.ram_mib > 0 && ramSegs.length > 0 && html`<${StackedMeter} compact=${compact} label="System memory"
      segments=${ramSegs} valueText=${fmtMiB(data.ram_mib)} />`}
    ${caption && html`<p className="text-caption muted memviz-caption">${caption}</p>`}
  </div>`;
}

/* ------------------------------------------------------------------ cards around the breakdown */

export function BreakdownCard({ runId, title = 'Memory use', compact }) {
  const url = '/api/server/breakdown' + (runId != null ? `?run_id=${encodeURIComponent(runId)}` : '');
  const r = useFetch(url);
  const reloadRef = useRef(r.reload);
  reloadRef.current = r.reload;
  useEvent('status', () => reloadRef.current());
  useEffect(() => {
    const id = setInterval(() => { if (!document.hidden) reloadRef.current(); }, 5000);
    return () => clearInterval(id);
  }, []);
  return html`<${E.Card} title=${title}>
    ${r.data ? html`<${MemoryBreakdown} data=${r.data} compact=${compact} />`
      : r.error && r.error.status === 404 ? html`<p className="text-body muted">No model loaded.</p>`
      : r.error ? html`<p className="text-body muted">${r.error.message}</p>`
      : html`<p className="text-body muted">Loading…</p>`}
  <//>`;
}

export function PreviewBreakdown({ model, rows, presetId }) {
  const [state, setState] = useState({ data: null, error: null, loading: false });
  const seq = useRef(0);
  const run = useDebounced(async (body) => {
    const mine = ++seq.current;
    try {
      const data = await api.post('/api/fit/breakdown', body);
      if (mine === seq.current) setState({ data, error: null, loading: false });
    } catch (error) {
      if (mine === seq.current) setState((s) => ({ data: s.data, error, loading: false }));
    }
  }, 400);
  const key = JSON.stringify([model, rows, presetId]);
  useEffect(() => {
    if (!model) return;
    setState((s) => ({ ...s, loading: true }));
    const flags = (rows || []).map((r) => Object.fromEntries(Object.entries(r).filter(([k]) => !k.startsWith('_'))));
    run({ model, flags, preset_id: presetId == null ? undefined : presetId });
  }, [key]);
  if (!model) return null;
  return html`<div className=${'memviz-preview' + (state.loading && state.data ? ' is-updating' : '')} aria-busy=${state.loading}>
    ${state.data && html`<${MemoryBreakdown} data=${state.data} preview />`}
    ${!state.data && !state.error && html`<p className="text-caption muted">Estimating…</p>`}
    ${state.error && html`<p className="text-caption muted">${state.error.message}</p>`}
    ${state.loading && state.data && html`<p className="text-caption muted memviz-updating" role="status">updating…</p>`}
  </div>`;
}

/* ------------------------------------------------------------------ live context fill */

export function useSlots(intervalMs = 1000) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const busy = useRef(false);
  const tick = useCallback(async () => {
    if (busy.current || document.hidden) return;
    busy.current = true;
    try {
      const data = await api.get('/api/server/slots');
      setState({ data, error: null, loading: false });
    } catch (error) {
      setState((s) => ({ data: s.data, error, loading: false }));
    } finally { busy.current = false; }
  }, []);
  useEffect(() => {
    let id = null;
    const start = () => { if (id == null) { tick(); id = setInterval(tick, intervalMs); } };
    const stop = () => { if (id != null) { clearInterval(id); id = null; } };
    const onVis = () => (document.hidden ? stop() : start());
    document.addEventListener('visibilitychange', onVis);
    if (!document.hidden) start();
    return () => { document.removeEventListener('visibilitychange', onVis); stop(); };
  }, [intervalMs, tick]);
  return state;
}

function FillBar({ label, used, max, processing }) {
  const f = max > 0 ? Math.min(1, used / max) : 0;
  const tone = f >= 0.95 ? 'danger' : f >= 0.8 ? 'warning' : 'ok';
  return html`<div className="memviz-fill">
    <div className="memviz-fill__head text-caption">
      <span className="memviz-fill__label">${label}${processing && html`<span className="memviz-pulse" role="img" aria-label="processing" title="Processing"></span>`}</span>
      <span className="memviz-fill__value">${fmtInt(used)} / ${fmtInt(max)} (${Math.round(f * 100)}%)</span>
    </div>
    <div className="memviz-fill__track" role="meter" aria-label=${label + ' context used'} aria-valuemin="0" aria-valuemax=${max} aria-valuenow=${used}>
      <span className=${'memviz-fill__bar is-' + tone} style=${{ width: f * 100 + '%' }}></span>
    </div>
  </div>`;
}

export function ContextFill({ instance }) {
  const slots = instance.slots || [];
  const used = slots.reduce((a, s) => a + (s.used || 0), 0);
  const max = slots.reduce((a, s) => a + (s.n_ctx || 0), 0);
  return html`<div className="memviz-ctx stack stack--sm">
    ${instance.name && html`<p className="text-subtitle truncate">${instance.name}</p>`}
    ${instance.error ? html`<p className="text-caption muted">${instance.error}</p>`
      : !slots.length ? html`<p className="text-caption muted">No slot information yet.</p>`
      : html`${slots.length > 1 && html`<${FillBar} label="Whole context" used=${used} max=${max} processing=${slots.some((s) => s.processing)} />`}
        ${slots.map((s) => html`<${FillBar} key=${s.id} label=${'Slot ' + s.id} used=${s.used || 0} max=${s.n_ctx} processing=${s.processing} />`)}`}
  </div>`;
}

export function ContextFillCard({ title = 'Context in use' }) {
  const { data, error } = useSlots(1000);
  const list = (data && data.instances) || [];
  return html`<${E.Card} title=${title}>
    ${list.length ? html`<div className="stack">${list.map((i) => html`<${ContextFill} key=${i.run_id} instance=${i} />`)}</div>`
      : error && !data ? html`<p className="text-body muted">${error.message}</p>`
      : data ? html`<p className="text-body muted">No model loaded.</p>`
      : html`<p className="text-body muted">Loading…</p>`}
  <//>`;
}
