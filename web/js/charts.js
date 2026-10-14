/* Hand-drawn SVG charts in Ember's chart language: 2px lines, 10% washes, hairline grids,
   chart-1..6 in fixed slots, text in ink tokens, crosshair and per-mark tooltips. */
import { html, useState, useEffect, useRef, useMemo, isNum, fmtTps } from './lib.js';

export const seriesColor = (slot) => (slot ? `var(--chart-${slot})` : 'var(--ink-subtle)');

function useWidth() {
  const ref = useRef(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.floor(e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

export function niceTicks(maxV, count = 4) {
  if (!(maxV > 0)) maxV = 1;
  const raw = maxV / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw - 1e-12);
  const top = Math.ceil(maxV / step - 1e-9) * step;
  const ticks = [];
  for (let v = 0; v <= top + step / 1000; v += step) ticks.push(+v.toPrecision(12));
  return { top, ticks };
}

function evenIndexes(n, want) {
  if (n <= 0) return [];
  if (n <= want) return [...Array(n).keys()];
  const out = [];
  for (let k = 0; k < want; k++) out.push(Math.round((k * (n - 1)) / (want - 1)));
  return [...new Set(out)];
}

export function Legend({ items, kind = 'line' }) {
  if (!items || items.length < 2) return null;
  return html`<ul className="chart-legend text-caption">
    ${items.map((it) => html`<li key=${it.label}>
      <span className=${'chart-key chart-key--' + (it.kind || kind)} style=${{ '--key': seriesColor(it.slot) }} aria-hidden="true"></span>
      <span>${it.label}</span>
    </li>`)}
  </ul>`;
}

function Tip({ x, width, top = 8, children }) {
  const flip = x > width * 0.6;
  return html`<div className=${'chart-tip text-caption' + (flip ? ' is-left' : '')} style=${{ left: x + 'px', top: top + 'px' }} role="presentation">${children}</div>`;
}

function TipRow({ slot, value, label, kind = 'line' }) {
  return html`<div className="chart-tip__row">
    <span className=${'chart-key chart-key--' + kind} style=${{ '--key': seriesColor(slot) }} aria-hidden="true"></span>
    <strong>${value}</strong><span className="chart-tip__label">${label}</span>
  </div>`;
}

const M = { l: 52, r: 14, t: 10, b: 28 };

function YAxis({ ticks, top, ph, w, format }) {
  return html`<g>
    ${ticks.map((v) => {
      const y = M.t + ph - (v / top) * ph;
      return html`<g key=${v}>
        <line x1=${M.l} x2=${w - M.r} y1=${y} y2=${y} className=${v === 0 ? 'chart-baseline' : 'chart-grid'} />
        <text x=${M.l - 8} y=${y} dy="0.32em" textAnchor="end" className="chart-axis">${format(v)}</text>
      </g>`;
    })}
  </g>`;
}

/* ------------------------------------------------------------------ line chart (time) */

export function LineChart({ xs, series, height = 200, yFormat = fmtTps, xFormat, unit = '', label, minTop = 0, empty = 'No data in this range yet.' }) {
  const [ref, w] = useWidth();
  const [hover, setHover] = useState(null);
  const n = xs.length;
  const vals = series.flatMap((s) => s.values.filter(isNum));
  const hasData = vals.length > 0;
  const { top, ticks } = niceTicks(Math.max(minTop, ...vals, 0));
  const ph = height - M.t - M.b;
  const pw = Math.max(10, w - M.l - M.r);
  const x0 = xs[0], x1 = xs[n - 1];
  const sx = (t) => M.l + (x1 > x0 ? ((t - x0) / (x1 - x0)) * pw : pw / 2);
  const sy = (v) => M.t + ph - (v / top) * ph;
  const single = series.length === 1;

  const paths = useMemo(() => series.map((s) => {
    let d = '', area = '', run = [];
    const dots = [];
    const flush = () => {
      if (run.length === 1) dots.push(run[0]);
      if (run.length > 1) {
        const seg = run.map((p, k) => (k ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join('');
        d += seg;
        area += seg + `L${run[run.length - 1][0].toFixed(1)} ${M.t + ph}L${run[0][0].toFixed(1)} ${M.t + ph}Z`;
      }
      run = [];
    };
    s.values.forEach((v, i) => { if (isNum(v)) run.push([sx(xs[i]), sy(v)]); else flush(); });
    flush();
    return { d, area, dots };
  }), [series, xs, w, top, height]);

  const pick = (clientX) => {
    const r = ref.current.getBoundingClientRect();
    const px = clientX - r.left;
    let best = 0, bd = Infinity;
    for (let i = 0; i < n; i++) { const dd = Math.abs(sx(xs[i]) - px); if (dd < bd) { bd = dd; best = i; } }
    setHover(best);
  };
  const onKey = (e) => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault();
      const cur = hover == null ? n - 1 : hover;
      setHover(Math.max(0, Math.min(n - 1, cur + (e.key === 'ArrowRight' ? 1 : -1))));
    } else if (e.key === 'Escape') setHover(null);
  };

  const xt = evenIndexes(n, Math.max(2, Math.min(6, Math.floor(pw / 90))));
  const hx = hover != null ? sx(xs[hover]) : 0;
  return html`<div className="chart" ref=${ref} style=${{ height: height + 'px' }}>
    ${!hasData && html`<p className="chart-empty text-body">${empty}</p>`}
    ${w > 0 && html`<svg width=${w} height=${height} role="img" aria-label=${label}
        tabIndex=${hasData ? 0 : -1} onPointerMove=${(e) => hasData && pick(e.clientX)} onPointerLeave=${() => setHover(null)}
        onKeyDown=${onKey} onBlur=${() => setHover(null)}>
      <${YAxis} ticks=${ticks} top=${top} ph=${ph} w=${w} format=${(v) => yFormat(v)} />
      ${xt.map((i) => html`<text key=${'x' + i} x=${sx(xs[i])} y=${height - 8} textAnchor=${i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'} className="chart-axis">${xFormat(xs[i])}</text>`)}
      ${hasData && series.map((s, k) => html`<g key=${s.key}>
        ${single && html`<path d=${paths[k].area} className="chart-area" style=${{ fill: seriesColor(s.slot) }} />`}
        <path d=${paths[k].d} className="chart-line" style=${{ stroke: seriesColor(s.slot) }} />
        ${paths[k].dots.map(([x, y], j) => html`<circle key=${j} cx=${x} cy=${y} r="4" className="chart-dot" style=${{ fill: seriesColor(s.slot) }} />`)}
      </g>`)}
      ${hover != null && html`<g>
        <line x1=${hx} x2=${hx} y1=${M.t} y2=${M.t + ph} className="chart-cross" />
        ${series.map((s) => isNum(s.values[hover]) && html`<circle key=${s.key} cx=${hx} cy=${sy(s.values[hover])} r="4.5" className="chart-dot" style=${{ fill: seriesColor(s.slot) }} />`)}
      </g>`}
    </svg>`}
    ${hover != null && html`<${Tip} x=${hx} width=${w}>
      <div className="chart-tip__title">${xFormat(xs[hover], true)}</div>
      ${series.map((s) => html`<${TipRow} key=${s.key} slot=${s.slot} label=${s.label} value=${isNum(s.values[hover]) ? yFormat(s.values[hover]) + (unit ? ' ' + unit : '') : 'no data'} />`)}
    <//>`}
  </div>`;
}

/* ------------------------------------------------------------------ stacked columns (time buckets) */

function roundedTop(x, y, w, h, r) {
  r = Math.max(0, Math.min(r, h, w / 2));
  return `M${x} ${y + h}L${x} ${y + r}Q${x} ${y} ${x + r} ${y}L${x + w - r} ${y}Q${x + w} ${y} ${x + w} ${y + r}L${x + w} ${y + h}Z`;
}

export function StackedColumns({ xs, stacks, height = 220, yFormat, xFormat, label, empty = 'No data in this range yet.' }) {
  const [ref, w] = useWidth();
  const [hover, setHover] = useState(null);
  const n = xs.length;
  const totals = xs.map((_, i) => stacks.reduce((a, s) => a + (s.values[i] || 0), 0));
  const hasData = totals.some((t) => t > 0);
  const { top, ticks } = niceTicks(Math.max(...totals, 0));
  const ph = height - M.t - M.b;
  const pw = Math.max(10, w - M.l - M.r);
  const band = pw / Math.max(1, n);
  const bw = Math.max(2, Math.min(24, band - 2));
  const GAP = 2;
  const base = M.t + ph;
  const xt = evenIndexes(n, Math.max(2, Math.min(6, Math.floor(pw / 90))));

  return html`<div className="chart" ref=${ref} style=${{ height: height + 'px' }}>
    ${!hasData && html`<p className="chart-empty text-body">${empty}</p>`}
    ${w > 0 && html`<svg width=${w} height=${height} role="img" aria-label=${label} onPointerLeave=${() => setHover(null)}>
      <${YAxis} ticks=${ticks} top=${top} ph=${ph} w=${w} format=${yFormat} />
      ${xt.map((i) => html`<text key=${'x' + i} x=${M.l + band * (i + 0.5)} y=${height - 8} textAnchor="middle" className="chart-axis">${xFormat(xs[i])}</text>`)}
      ${hasData && xs.map((_, i) => {
        const x = M.l + band * i + (band - bw) / 2;
        let cum = 0;
        const visible = stacks.map((s) => s.values[i] || 0);
        const topIdx = visible.reduce((acc, v, k) => (v > 0 ? k : acc), -1);
        const segs = stacks.map((s, k) => {
          const v = visible[k];
          if (!(v > 0)) return null;
          const h = (v / top) * ph;
          const y = base - cum - h;
          cum += h;
          const hh = k === visible.findIndex((q) => q > 0) ? h : h - GAP; // gap below every segment but the lowest
          if (hh < 0.75) return null;
          const d = k === topIdx ? roundedTop(x, y, bw, hh, 4) : `M${x} ${y}h${bw}v${hh}h${-bw}Z`;
          return html`<path key=${s.key} d=${d} style=${{ fill: seriesColor(s.slot) }} />`;
        });
        return html`<g key=${i} className=${'chart-col' + (hover != null && hover !== i ? ' is-dim' : '')}>
          ${segs}
          <rect x=${M.l + band * i} y=${M.t} width=${band} height=${ph} fill="transparent"
            tabIndex=${totals[i] > 0 ? 0 : -1} onPointerEnter=${() => setHover(i)} onFocus=${() => setHover(i)} onBlur=${() => setHover(null)}
            aria-label=${xFormat(xs[i], true) + ': ' + stacks.map((s) => s.label + ' ' + yFormat(s.values[i] || 0)).join(', ')} />
        </g>`;
      })}
    </svg>`}
    ${hover != null && html`<${Tip} x=${M.l + band * (hover + 0.5)} width=${w}>
      <div className="chart-tip__title">${xFormat(xs[hover], true)}</div>
      ${stacks.slice().reverse().map((s) => html`<${TipRow} key=${s.key} kind="rect" slot=${s.slot} label=${s.label} value=${yFormat(s.values[hover] || 0)} />`)}
      <div className="chart-tip__row chart-tip__total"><strong>${yFormat(totals[hover])}</strong><span className="chart-tip__label">total</span></div>
    <//>`}
  </div>`;
}

/* ------------------------------------------------------------------ scatter */

export function Scatter({ points, height = 260, xFormat, yFormat, xLabel, yLabel, label, tip, empty = 'No data in this range yet.' }) {
  const [ref, w] = useWidth();
  const [hover, setHover] = useState(null);
  const hasData = points.length > 0;
  const { top: xTop, ticks: xTicks } = niceTicks(Math.max(...points.map((p) => p.x), 0), 5);
  const { top: yTop, ticks: yTicks } = niceTicks(Math.max(...points.map((p) => p.y), 0));
  const ph = height - M.t - M.b - 6;
  const pw = Math.max(10, w - M.l - M.r);
  const sx = (v) => M.l + (v / xTop) * pw;
  const sy = (v) => M.t + ph - (v / yTop) * ph;
  const ordered = useMemo(() => points.slice().sort((a, b) => a.t - b.t), [points]);

  const pick = (e) => {
    const r = ref.current.getBoundingClientRect();
    const px = e.clientX - r.left, py = e.clientY - r.top;
    let best = null, bd = 24 * 24;
    for (const p of ordered) {
      const dx = sx(p.x) - px, dy = sy(p.y) - py, d = dx * dx + dy * dy;
      if (d <= bd) { bd = d; best = p; }
    }
    setHover(best);
  };
  const hx = hover ? sx(hover.x) : 0;
  return html`<div className="chart" ref=${ref} style=${{ height: height + 'px' }}>
    ${!hasData && html`<p className="chart-empty text-body">${empty}</p>`}
    ${w > 0 && html`<svg width=${w} height=${height} role="img" aria-label=${label} onPointerMove=${hasData ? pick : undefined} onPointerLeave=${() => setHover(null)}>
      <${YAxis} ticks=${yTicks} top=${yTop} ph=${ph} w=${w} format=${yFormat} />
      ${xTicks.map((v) => html`<text key=${v} x=${sx(v)} y=${M.t + ph + 18} textAnchor="middle" className="chart-axis">${xFormat(v)}</text>`)}
      <text x=${w - M.r} y=${height - 2} textAnchor="end" className="chart-axis chart-axis--title">${xLabel}</text>
      <text x=${M.l} y=${M.t - 1} dy="-0.2em" className="chart-axis chart-axis--title">${yLabel}</text>
      ${ordered.map((p) => html`<circle key=${p.id} cx=${sx(p.x)} cy=${sy(p.y)} r="4" className="chart-pt" style=${{ fill: seriesColor(p.slot) }} />`)}
      ${hover && html`<circle cx=${hx} cy=${sy(hover.y)} r="6" className="chart-pt is-hover" style=${{ fill: seriesColor(hover.slot) }} />`}
    </svg>`}
    ${hover && html`<${Tip} x=${hx} width=${w} top=${Math.max(4, sy(hover.y) - 70)}>${tip(hover)}<//>`}
  </div>`;
}

/* ------------------------------------------------------------------ meter */

export function Meter({ value, max, label, valueText, warnAt = 0.95, dangerAt = 0.99 }) {
  const f = max ? value / max : 0;
  const tone = f >= dangerAt ? 'danger' : f >= warnAt ? 'warning' : 'ember';
  return html`<${window.Ember.Progress} value=${value} max=${max} label=${label} valueText=${valueText} tone=${tone} />`;
}
