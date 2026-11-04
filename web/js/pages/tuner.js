/* Tuner: measure flag candidates in a fixed order, keep the winner, save it as a preset. */
import { html, E, useState, useEffect, useMemo, api, useApp, useFetch, useEvent, useNow, fmtInt, fmtTps, fmtPct, fmtBytes, fmtWhen, fmtDuration, isNum, commandLine, modelLabel, liveOf } from '../lib.js';
import { askDesktopPermission } from '../notify.js';
import { DRAFT_LABEL, draftLengths } from '../features.js';
import { stageStates, stageCount, stageFill, STAGE_WORD } from '../tuneplan.js';
import { DIMS, parseValues, toSpace, countCandidates, estimateMinutes } from '../tunespace.js';

const CTX_OPTIONS = [4096, 8192, 16384, 32768, 65536, 131072, 196608, 262144];
const ctxLabel = (n) => (n % 1024 === 0 ? `${n / 1024}K` : fmtInt(n));
const OBJECTIVES = [
  { value: 'decode', label: 'Generation' },
  { value: 'balanced', label: 'Balanced' },
  { value: 'prefill', label: 'Prompt processing' },
  { value: 'context', label: 'Context length' },
];
const OBJ_HINT = {
  decode: 'Score: decode speed, geometric mean of a short prompt and the deep prompt.',
  balanced: 'Score: decode^0.7 × prefill^0.3, for long-document and agent work.',
  prefill: 'Score: prompt processing speed at the benchmark depth.',
  context: 'Keeps the whole target context and its KV cache on the GPU, and moves model weights to system RAM until it fits: expert weights first on MoE models, whole layers on dense ones. Finds the smallest spill for each batch size, then tries CPU thread counts (-t, -tb) on the fastest, since the CPU reads the spilled weights. Scores like Balanced.',
};

/* What a configuration keeps in system RAM, for tables and the winner card. */
function spillText(cfg, params) {
  if (cfg.ncmoe) return `Experts of ${cfg.ncmoe} of ${params.n_layers} layers`;
  if (cfg.ngl && cfg.ngl !== '99' && cfg.ngl !== 'auto') return `${params.n_layers + 1 - Number(cfg.ngl)} of ${params.n_layers + 1} layers`;
  return 'Nothing';
}

/* The nearest draft file of each kind next to the model: what the tuner tries by default. */
const draftsOf = (m) => {
  const seen = new Set();
  return ((m && m.drafts) || []).filter((d) => !seen.has(d.type) && seen.add(d.type));
};

const MAX_CANDIDATES = 4096; // the server refuses a run with more
const SPACE_MODE_LABEL = { auto: 'Auto', off: 'Off', fixed: 'Fixed', list: 'Search' };
const GROUPS = DIMS.reduce((g, d) => g.set(d.group, [...(g.get(d.group) || []), d]), new Map()); // group => dims, in DIMS order
const blankSpace = () => Object.fromEntries(DIMS.map((d) => [d.key, { mode: 'auto', text: '' }]));
const dimPlaceholder = (d) => ({ choice: d.choices && d.choices.join(', '), int: '256..2048 x2', float: '0.1, 0.5', text: 'one per line or ;' })[d.kind];

/* A dimension's parsed values; fixed takes exactly one. */
function dimValues(d, s) {
  const r = parseValues(d, s.text);
  if (!r.error && s.mode === 'fixed' && r.values.length !== 1) return { values: r.values, error: 'Fixed takes one value.' };
  return r;
}

function dimCaption(d, s) {
  if (s.mode === 'auto') return d.auto;
  if (s.mode === 'off') return d.key === 'spec' ? 'Not searched: no speculative options' : d.key === 'ctx' ? 'Not searched: held at the target context' : `Not searched: ${d.off ? `held at ${d.off}` : 'flag left out'}`;
  return s.mode === 'fixed' ? 'Every candidate uses this value.' : 'Only these values are tried, in the order given.';
}

export function TunerPage() {
  const { toast, fail, go, status } = useApp();
  const list = useFetch('/api/tune');
  const [selected, setSelected] = useState(null);
  const [live, setLive] = useState(null);
  useEffect(() => {
    if (!list.data) return;
    setLive(list.data.live);
    if (selected == null && list.data.tunes.length) setSelected(list.data.tunes[0].id);
  }, [list.data]);
  useEvent('tune', (t) => {
    if (t.live) setLive(t.live);
    if (!t.removed && t.status !== 'queued') setSelected(t.id);  // a run that is only queued doesn't take the page
    list.reload();
  });

  const running = live && live.running;
  const queue = (list.data && list.data.queue) || [];
  const current = list.data && list.data.tunes.find((t) => t.id === selected);
  const onStarted = (r) => { if (r.status === 'running') setSelected(r.id); list.reload(); };

  return html`<div className="page">
    <header className="page-head">
      <div className="page-head__titles">
        <h1 className="text-headline">Tuner</h1>
        <p className="text-body">Measures flag combinations for one model on this box and saves the fastest as a preset.</p>
      </div>
    </header>

    ${running
      ? html`<${RunningCard} tune=${current} live=${live} queued=${queue.length} />
        ${queue.length ? html`<${QueueCard} queue=${queue} />` : null}
        ${current && html`<${TuneResult} tune=${current} live=${live.tune_id === current.id ? live : null} />`}
        <${TuneForm} status=${status} queueing onStarted=${onStarted} />`
      : html`<${TuneForm} status=${status} onStarted=${onStarted} />
        ${current && html`<${TuneResult} tune=${current} live=${null} />`}`}
    ${list.data && list.data.tunes.length > 1 && html`<${History} tunes=${list.data.tunes} selected=${selected} onSelect=${setSelected} />`}
  </div>`;
}

/* The queued runs: what will happen, in order, and how to take one out before its turn. */
function QueueCard({ queue }) {
  const { fail } = useApp();
  const searchLabel = (t) => t.params.objective === 'context' ? 'Smallest spill' : t.params.mode === 'quick' ? 'One flag at a time' : 'Every combination';
  const estimate = (t) => {
    const [lo, hi] = estimateMinutes(countCandidates(t.params, { nextn_layers: t.params.has_mtp }), t.params.depth);
    return lo ? `${lo}–${hi} min` : '–';
  };
  const columns = [
    { key: 'pos', header: '#', numeric: true, render: (t, i) => i + 1 },
    { key: 'model', header: 'Model', render: (t) => modelLabel(t.model) },
    { key: 'ctx', header: 'Context', numeric: true, render: (t) => ctxLabel(t.params.ctx) },
    { key: 'obj', header: 'Objective', render: (t) => (OBJECTIVES.find((o) => o.value === t.params.objective) || { label: t.params.objective }).label },
    { key: 'search', header: 'Search', render: (t) => searchLabel(t) },
    { key: 'est', header: 'Estimate', numeric: true, render: (t) => estimate(t) },
    { key: 'remove', header: '', align: 'end', render: (t) => html`<${E.IconButton} icon="x" label="Remove from queue"
      onClick=${() => api.del('/api/tune/' + t.id).catch((e) => fail(e, 'Could not remove the run from the queue'))} />` },
  ];
  return html`<${E.Card} title="Queue" padding="none"
    description="Runs start in this order, each when the one before finishes. The models that were running come back after the last one."
    actions=${html`<${E.Button} variant="ghost" icon="trash" onClick=${() => api.del('/api/tune/queue').catch((e) => fail(e, 'Could not clear the queue'))}>Clear queue<//>`}>
    <${E.Table} caption="Queued tuning runs" density="compact" columns=${columns} rows=${queue} rowKey=${(t) => String(t.id)}
      empty="Nothing is queued." />
  <//>`;
}

/* ------------------------------------------------------------------ form */

function TuneForm({ status, queueing, onStarted }) {
  const { fail, toast } = useApp();
  const models = useFetch('/api/models');
  const presets = useFetch('/api/presets');
  const lib = (models.data ? models.data.models : []).filter((m) => !m.is_projector);
  const [model, setModel] = useState('');
  const [ctx, setCtx] = useState('32768');
  const [objective, setObjective] = useState('decode');
  const [depth, setDepth] = useState('8192');
  const [speculative, setSpeculative] = useState(true);
  const [skipped, setSkipped] = useState([]); // draft files switched off
  const [base, setBase] = useState('');
  const [name, setName] = useState('');
  const [startAfter, setStartAfter] = useState(true);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!model && lib.length) setModel(lib[0].path); }, [models.data]);
  useEffect(() => { setSkipped([]); }, [model]);
  const m = lib.find((x) => x.path === model);
  const maxCtx = m && m.context_length ? m.context_length : 262144;
  const ctxOpts = CTX_OPTIONS.filter((n) => n <= maxCtx).map((n) => ({ value: String(n), label: ctxLabel(n) + (n === maxCtx ? ' (trained maximum)' : '') }));
  useEffect(() => { if (m && Number(ctx) > maxCtx) setCtx(String(ctxOpts[ctxOpts.length - 1].value)); }, [model]);
  const depthOpts = [2048, 8192, 16384, 32768].filter((d) => d < Number(ctx)).map((d) => ({ value: String(d), label: ctxLabel(d) + ' tokens' }));
  useEffect(() => { if (Number(depth) >= Number(ctx) && depthOpts.length) setDepth(depthOpts[depthOpts.length - 1].value); }, [ctx]);
  const [mode, setMode] = useState('grid');
  const [kvPolicy, setKvPolicy] = useState('precise');
  const [varyBatch, setVaryBatch] = useState(false);
  const isContext = objective === 'context';
  const pickObjective = (o) => {
    setObjective(o);
    if (o === 'context' && ctxOpts.length) setCtx(ctxOpts[ctxOpts.length - 1].value);
  };
  const draftFiles = draftsOf(m);
  const tryDrafts = draftFiles.filter((d) => !skipped.includes(d.path));
  const [space, setSpace] = useState(blankSpace); // {key: {mode, text}}, kept across model changes
  const specOff = space.spec.mode === 'off';
  const sendSpec = speculative && !specOff;
  const sendDrafts = specOff ? [] : tryDrafts;
  let spaceSend = null;
  let spaceError = null;
  try { spaceSend = toSpace(space); } catch (e) { spaceError = e.message; }
  const count = spaceError ? 0 : countCandidates({ objective, mode, kv_policy: kvPolicy, vary_batch: varyBatch, speculative: sendSpec, drafts: sendDrafts, space: spaceSend }, m);
  const tooMany = !spaceError && count > MAX_CANDIDATES;
  const [lo, hi] = count ? estimateMinutes(count, depth) : [0, 0];
  const running = liveOf(status).length > 0;

  const start = async () => {
    askDesktopPermission();
    setBusy(true);
    try {
      const r = await api.post('/api/tune', {
        model, ctx: Number(ctx), objective, depth: Number(depth), speculative: sendSpec, drafts: sendDrafts.map((d) => d.path),
        base_preset_id: base ? Number(base) : null, name, start_after: startAfter,
        mode, kv_policy: kvPolicy, vary_batch: varyBatch, space: spaceSend,
      });
      if (queueing) {
        toast({ title: 'Added to the queue', description: `Position ${r.position}. It starts when the run${r.position > 1 ? 's' : ''} before it finish${r.position > 1 ? '' : 'es'}.` });
      } else {
        toast({ title: 'Tuning started', description: 'Each candidate loads the model, then runs the same benchmark. You will get a notification when it finishes.' });
      }
      onStarted(r);
    } catch (e) { fail(e, 'Could not start tuning'); } finally { setBusy(false); }
  };

  return html`<${E.Card} title=${queueing ? 'Queue another run' : 'New tuning run'} description="Same inputs, same candidates in the same order, same benchmark: greedy decoding with a fixed seed and the prompt cache off.">
    <div className="stack">
      <div className="grid grid--2" style=${{ gap: 'var(--space-4)' }}>
        <${E.Select} label="Model" value=${model} onChange=${(e) => setModel(e.target.value)}
          options=${lib.map((x) => ({ value: x.path, label: `${x.name} · ${fmtBytes(x.size)}` }))}
          placeholder=${lib.length ? undefined : 'No models in the library'}
          hint=${m ? [m.arch, m.size_label, m.nextn_layers ? 'has an MTP head' : null, draftFiles.length ? 'draft files: ' + draftFiles.map((d) => DRAFT_LABEL[d.type] || 'draft').join(', ') : null].filter(Boolean).join(' · ') : undefined} />
        <${E.Select} label="Target context" value=${ctx} onChange=${(e) => setCtx(e.target.value)} options=${ctxOpts}
          hint=${isContext ? 'The tuner keeps this fixed and moves as little of the model to system RAM as it takes to fit.' : 'The tuner keeps this fixed and finds the fastest way to run it.'} />
        <${E.Select} label="Benchmark depth" value=${depth} onChange=${(e) => setDepth(e.target.value)} options=${depthOpts}
          hint="Length of the deep prompt. Deeper is more realistic and slower to measure." />
        <${E.Select} label="Keep other flags from" optional value=${base} onChange=${(e) => setBase(e.target.value)}
          options=${[{ value: '', label: 'Nothing (only --jinja)' }, ...((presets.data || []).map((p) => ({ value: String(p.id), label: p.name })))]}
          hint="Sampling, templates and env vars carry over; memory and speed flags are re-tuned." />
      </div>
      <div>
        <p className="field-label text-label">Optimize for</p>
        <${E.SegmentedControl} label="Optimize for" value=${objective} onChange=${pickObjective} options=${OBJECTIVES} />
        <p className="field-hint text-caption">${OBJ_HINT[objective]}</p>
      </div>
      ${isContext && m && !m.block_count ? html`<${E.Alert} tone="warning" title="This model's layer count is unknown.">The tuner can't spill it to system RAM. Pick another objective.<//>` : null}
      <div className="grid grid--2" style=${{ gap: 'var(--space-4)' }}>
        ${isContext ? html`<div>
          <p className="field-label text-label">Search</p>
          <p className="text-body">Smallest spill per batch size</p>
          <p className="field-hint text-caption">${m && m.expert_count ? `MoE model: moves expert weights of the first N layers (--n-cpu-moe), so attention and the KV cache stay on the GPU.` : 'Dense model: moves whole layers (-ngl). The KV cache of those layers moves with them.'} Each N is found by bisection with load-only probes. The CPU reads the spilled weights, so the fastest spill is then measured at a few thread counts (-t, -tb).</p>
        </div>` : html`<div>
          <p className="field-label text-label">Search</p>
          <${E.SegmentedControl} label="Search" value=${mode} onChange=${setMode}
            options=${[{ value: 'grid', label: 'Every combination' }, { value: 'quick', label: 'One flag at a time' }]} />
          <p className="field-hint text-caption">${mode === 'grid'
            ? 'Measures every combination of the values below, so interactions between flags show up. The best three are measured again before one is picked.'
            : 'Tunes each flag in turn and keeps a change only if it wins by more than 2%. Faster, but can miss combinations that only work together.'}</p>
        </div>`}
        <${E.Select} label="KV cache types" value=${kvPolicy} onChange=${(e) => setKvPolicy(e.target.value)}
          options=${[{ value: 'precise', label: 'Most precise type that fits' }, { value: 'all', label: 'Every type that fits' }]}
          hint=${kvPolicy === 'all' ? 'Lower-precision KV can win on speed; it costs some output quality at long context.' : 'f16, then q8_0, then q4_0: the first that leaves 512 MiB spare.'} />
      </div>
      ${mode === 'grid' && !isContext && html`<${E.Checkbox} label="Also vary the logical batch (-b)" checked=${varyBatch} onChange=${(e) => setVaryBatch(e.target.checked)}
        description="Tries -b equal to -ub, 2048 and 4096 for every -ub. Nearly triples the run." />`}
      ${m && m.nextn_layers ? html`<${E.Switch} label="Try self-speculative decoding (MTP)" checked=${sendSpec} disabled=${specOff} onChange=${(e) => setSpeculative(e.target.checked)}
        description="Tests --spec-type draft-mtp with 1 to 5 draft tokens. Acceptance is measured with greedy decoding, which can run higher than with sampling." />` : null}
      ${draftFiles.map((d) => html`<${E.Switch} key=${d.path} label=${`Try the ${DRAFT_LABEL[d.type] || 'draft'} file`} checked=${!skipped.includes(d.path) && !specOff} disabled=${specOff}
        onChange=${(e) => setSkipped(e.target.checked ? skipped.filter((x) => x !== d.path) : [...skipped, d.path])}
        description=${`Tests --spec-type ${d.type} with ${d.name} (${fmtBytes(d.size)}) at ${draftLengths(d).join(', ')} draft tokens. The file needs VRAM of its own, and acceptance is measured with greedy decoding.`} />`)}
      <${SearchSpace} space=${space} setSpace=${setSpace} />
      <div className="grid grid--2" style=${{ gap: 'var(--space-4)' }}>
        <${E.TextField} label="Preset name" optional value=${name} onChange=${(e) => setName(e.target.value)}
          placeholder=${m ? `${m.name} · ${ctxLabel(Number(ctx))} · tuned` : ''} />
        <div style=${{ alignSelf: 'end' }}>
          <${E.Checkbox} label="Start the winning preset when done" checked=${startAfter} onChange=${(e) => setStartAfter(e.target.checked)} />
        </div>
      </div>
      ${!queueing && running && html`<${E.Alert} tone="warning" title=${liveOf(status).length > 1 ? 'The running models stop while tuning.' : 'The running model stops while tuning.'}>
        ${startAfter ? 'The winner starts when tuning finishes.' : 'It starts again when tuning finishes.'} The API port answers 503 in between.
      <//>`}
      <div className="row row--between">
        ${spaceError || tooMany
          ? html`<span className="text-caption space-error">${spaceError || 'too many: narrow it to at most 4,096'}</span>`
          : html`<span className="text-caption muted">Up to ${fmtInt(count)} candidates · roughly ${lo}–${hi} minutes</span>`}
        <${E.Button} variant="primary" icon=${queueing ? 'plus' : 'play'} loading=${busy} disabled=${!model || !!spaceError || tooMany} onClick=${start}>${queueing ? 'Add to queue' : 'Start tuning'}<//>
      </div>
    </div>
  <//>`;
}

/* The search space: every setting in a collapsed disclosure, grouped as the tuner groups them. */
function SearchSpace({ space, setSpace }) {
  const changed = DIMS.filter((d) => space[d.key].mode !== 'auto').length;
  const setDim = (key, patch) => setSpace((s) => ({ ...s, [key]: { ...s[key], ...patch } }));
  return html`<details className="space-search">
    <summary className="space-search__summary"><span className="text-label">Search space</span>
      ${changed > 0 && html`<span className="text-caption muted">${changed} ${changed === 1 ? 'dimension' : 'dimensions'} changed</span>`}</summary>
    <div className="stack">
      <div className="row row--between">
        <p className="field-hint text-caption">Auto is what the tuner does today. Off holds a setting out of the search. Fixed uses one value; Search tries each value given.</p>
        <${E.Button} variant="ghost" size="sm" disabled=${!changed} onClick=${() => setSpace(blankSpace())}>Reset to defaults<//>
      </div>
      ${[...GROUPS].map(([group, dims]) => html`<div key=${group} className="stack stack--sm">
        <h4 className="text-label space-group">${group}</h4>
        ${dims.map((d) => html`<${DimRow} key=${d.key} d=${d} s=${space[d.key]} onChange=${(patch) => setDim(d.key, patch)} />`)}
      </div>`)}
    </div>
  </details>`;
}

/* One setting: its name and flag, its mode, the values when fixed or searched, and what auto or off means. */
function DimRow({ d, s, onChange }) {
  const opts = d.modes.map((m) => ({ value: m, label: SPACE_MODE_LABEL[m] }));
  const typed = s.mode === 'fixed' || s.mode === 'list';
  const error = typed ? dimValues(d, s).error : null;
  return html`<div className="space-dim">
    <div className="space-dim__head">
      <span className="space-dim__name text-label">${d.label}</span>
      ${d.flag && html`<span className="text-code subtle">${d.flag}</span>`}
      <${E.SegmentedControl} size="sm" label=${d.label} value=${s.mode} onChange=${(mode) => onChange({ mode })} options=${opts} />
    </div>
    ${typed && html`<${E.TextField} size="sm" hideLabel label=${d.label} mono=${d.kind === 'text'} value=${s.text}
      onChange=${(e) => onChange({ text: e.target.value })} placeholder=${dimPlaceholder(d)} error=${error} />`}
    <p className="field-hint text-caption">${dimCaption(d, s)}</p>
  </div>`;
}

/* ------------------------------------------------------------------ running + results */

function RunningCard({ tune, live, queued }) {
  const { fail } = useApp();
  const now = useNow(1000);
  const done = tune ? (tune.trials || []).length : 0;
  const est = tune ? countCandidates(tune.params, { nextn_layers: tune.params.has_mtp }) : 15;
  const cancelOne = () => api.post('/api/tune/cancel').catch(fail);
  // Empty the queue before cancelling: the cancelled run must not hand the GPU to the next one in between.
  const cancelAll = async () => { try { await api.del('/api/tune/queue'); await api.post('/api/tune/cancel'); } catch (e) { fail(e, 'Could not cancel tuning'); } };
  return html`<${E.Card} title=${tune ? `Tuning ${modelLabel(tune.model)}` : 'Tuning'}
    description=${tune ? `${ctxLabel(tune.params.ctx)} context · ${tune.params.objective} · running for ${fmtDuration(now - (tune.started_at || tune.created_at))}` : ''}
    actions=${queued > 0
      ? html`<${E.Button} variant="secondary" icon="x" onClick=${cancelOne}>Cancel this run<//><${E.Button} variant="ghost" icon="trash" onClick=${cancelAll}>Cancel all<//>`
      : html`<${E.Button} variant="secondary" icon="x" onClick=${cancelOne}>Cancel<//>`}>
    <div className="stack">
      <${E.Progress} label=${live.stage ? `${live.stage}: ${live.trial}` : 'Starting'} value=${Math.min(done, est)} max=${est}
        valueText=${`${done} measured`} />
      <${StageTracker} plan=${live.plan} trials=${tune ? tune.trials || [] : []} stage=${live.stage} />
      <p className="text-caption muted">The server is unavailable while tuning. Each candidate starts its own llama-server on an internal port.</p>
    </div>
  <//>`;
}

/* Every stage of the run in order: finished ones checked, the one in progress with its count, the rest
   waiting. Stages that turn out to have nothing to run are marked skipped. */
function StageTracker({ plan, trials, stage }) {
  const steps = stageStates(plan, trials, stage);
  if (!steps.length) return null;
  return html`<ol className="stages" aria-label="Tuning stages">
    ${steps.map((s) => html`<li key=${s.id} className=${'stage is-' + s.state} aria-current=${s.state === 'running' ? 'step' : undefined}>
      <span className="stage__mark" aria-hidden="true">
        ${s.state === 'done' ? html`<${E.Icon} name="check-circle" size=${16} />`
          : s.state === 'running' ? html`<${E.Spinner} size=${14} />`
          : s.state === 'skipped' ? html`<${E.Icon} name="minus" size=${16} />`
          : html`<span className="stage__dot"></span>`}
      </span>
      <span className="stage__body">
        <span className="stage__label text-label">${s.label}</span>
        <span className="stage__count text-caption">${stageCount(s)}</span>
        <span className="stage__bar" aria-hidden="true"><span style=${{ width: `${stageFill(s) * 100}%` }}></span></span>
      </span>
      <span className="em-sr-only">${STAGE_WORD[s.state]}</span>
    </li>`)}
  </ol>`;
}

function TuneResult({ tune, live }) {
  const { go, toast, fail, refreshStatus } = useApp();
  const trials = tune.trials || [];
  const obj = tune.params.objective;
  const first = trials.find((t) => t.ok);
  const best = tune.best;
  const startPreset = async () => {
    try { await api.post('/api/server/start', { preset_id: tune.preset_id, replace: 'auto' }); refreshStatus(); go('server'); }
    catch (e) { fail(e, 'Could not start the preset'); }
  };
  const columns = [
    { key: 'n', header: '#', numeric: true },
    { key: 'stage', header: 'Stage' },
    { key: 'label', header: 'Candidate', mono: true, render: (t) => html`<span className="nowrap">${t.label}</span>` },
    { key: 'result', header: 'Result', render: (t) => (t.probe
      ? html`<span className="row" style=${{ flexWrap: 'nowrap' }}>${t.fits ? html`<${E.Badge} size="sm" tone="neutral" icon="check">Fits<//>` : html`<${E.Badge} size="sm" tone="neutral" icon="minus">Too big<//>`}${t.error && html`<span className="text-caption subtle truncate" style=${{ maxWidth: 220 }} title=${t.error}>${t.error}</span>`}</span>`
      : t.ok
      ? (t.best ? html`<${E.Badge} size="sm" tone="ember" icon="check">Winner<//>`
        : t.finalist ? html`<${E.Badge} size="sm" tone="info">Finalist<//>`
        : t.stage === 'Confirm' ? html`<${E.Badge} size="sm" tone="neutral" icon="refresh">Re-run<//>`
        : t.chosen ? html`<${E.Badge} size="sm" tone="success" icon="check-circle">Kept<//>` : html`<${E.Badge} size="sm" tone="neutral">Slower<//>`)
      : html`<span className="row" style=${{ flexWrap: 'nowrap' }}>${t.pruned ? html`<${E.Badge} size="sm" tone="neutral" icon="minus">Pruned<//>` : html`<${E.Badge} size="sm" tone="warning" icon="alert-triangle">Skipped<//>`}<span className="text-caption subtle truncate" style=${{ maxWidth: 220 }} title=${t.error}>${t.error}</span></span>`) },
    { key: 'decode0', sortable: true, sortValue: (t) => (t.ok ? t.decode0 : -1), header: 'Decode', numeric: true, render: (t) => (t.ok ? fmtTps(t.decode0) : '–') },
    { key: 'decodeD', sortable: true, sortValue: (t) => (t.ok ? t.decodeD : -1), header: 'Decode deep', numeric: true, render: (t) => (t.ok ? fmtTps(t.decodeD) : '–') },
    { key: 'prefillD', sortable: true, sortValue: (t) => (t.ok ? t.prefillD : -1), header: 'Prefill deep', numeric: true, render: (t) => (t.ok ? fmtTps(t.prefillD) : '–') },
    { key: 'draft', header: 'Draft', numeric: true, render: (t) => (t.ok && t.draft != null ? fmtPct(t.draft, 0) : '–') },
    { key: 'vram', header: 'VRAM free', numeric: true, render: (t) => (isNum(t.vram_free_mib) ? fmtBytes(t.vram_free_mib * 2 ** 20) : '–') },
    { key: 'final', header: 'Confirmed', numeric: true, render: (t) => (t.final_score != null && first ? (t.final_score / first.score * 100 - 100 >= 0 ? '+' : '') + (t.final_score / first.score * 100 - 100).toFixed(1) + '%' : '') },
    { key: 'score', header: 'Score', numeric: true, sortable: true, sortValue: (t) => (t.ok ? t.score : -1), render: (t) => (t.ok && first ? html`<span title=${fmtTps(t.score)}>${((t.score / first.score - 1) * 100 >= 0 ? '+' : '') + ((t.score / first.score - 1) * 100).toFixed(1)}%</span>` : '–') },
  ];
  const bestFlags = best ? tuneCommand(tune, best) : '';
  return html`<div className="stack" style=${{ gap: 'var(--space-6)' }}>
    ${tune.status === 'done' && best && html`<${E.Card} title=${'Winner for ' + modelLabel(tune.model)}
      description=${`${ctxLabel(tune.params.ctx)} context · optimized for ${OBJECTIVES.find((o) => o.value === obj).label.toLowerCase()} · ${fmtWhen(tune.finished_at)}`}
      actions=${tune.preset_id ? html`<div className="card-actions"><${E.Button} variant="secondary" icon="file" onClick=${() => go('presets')}>View preset<//><${E.Button} variant=${live ? 'primary' : 'secondary'} icon="play" onClick=${startPreset}>Start preset<//></div>` : null}>
      <div className="stack">
        <dl className="facts facts--tight">
          <div><dt className="text-caption">Decode</dt><dd className="text-subtitle">${fmtTps(best.decode0)} t/s</dd></div>
          <div><dt className="text-caption">Decode at ${fmtInt(tune.params.depth)}</dt><dd className="text-subtitle">${fmtTps(best.decodeD)} t/s</dd></div>
          <div><dt className="text-caption">Prefill at ${fmtInt(tune.params.depth)}</dt><dd className="text-subtitle">${fmtTps(best.prefillD)} t/s</dd></div>
          <div><dt className="text-caption">Draft acceptance</dt><dd className="text-subtitle">${best.draft != null ? fmtPct(best.draft) : '–'}</dd></div>
          ${obj === 'context' && html`<div><dt className="text-caption">In system RAM</dt><dd className="text-subtitle">${spillText(best.cfg, tune.params)}</dd></div>`}
          <div><dt className="text-caption">VRAM free</dt><dd className="text-subtitle">${isNum(best.vram_free_mib) ? fmtBytes(best.vram_free_mib * 2 ** 20) : '–'}</dd></div>
          <div><dt className="text-caption">vs first fit</dt><dd className="text-subtitle">${first && first !== best ? ((best.score / first.score - 1) * 100 >= 0 ? '+' : '') + ((best.score / first.score - 1) * 100).toFixed(1) + '%' : 'same'}</dd></div>
        </dl>
        <pre className="code text-code">${bestFlags}</pre>
      </div>
    <//>`}
    ${tune.status === 'failed' && html`<${E.Alert} tone="danger" title="Tuning failed.">${tune.error}<//>`}
    ${(tune.status === 'cancelled' || tune.status === 'interrupted') && html`<${E.Alert} tone="info" title=${tune.status === 'cancelled' ? 'Tuning was cancelled.' : 'Tuning was interrupted by a restart.'}>Measured candidates are kept below. Nothing was saved.<//>`}
    <${E.Card} title="Candidates" padding="none"
      description=${obj === 'context' ? `${trials.filter((t) => t.probe).length} load-only probes found the smallest spill for each batch size; ${trials.filter((t) => !t.probe).length} benchmarks measured them. The top three are re-run, and the simplest stays unless another beats it by more than 2%. Sort by any column.` : tune.params.mode === 'quick' ? `${trials.length} measured one flag at a time. A change is kept only if it beats the current choice by more than 2%.` : `${trials.filter((t) => !t.pruned).length} measured, ${trials.filter((t) => t.pruned).length} pruned. Every combination in a fixed order; the top three are re-run and ranked on both runs, and the simplest stays unless another beats it by more than 2%. Sort by any column.`}>
      <${E.Table} caption="Tuning candidates" density="compact" columns=${columns} rows=${trials} rowKey=${(t) => String(t.n)}
        empty=${live ? 'Loading the first candidate…' : 'No candidates measured.'} />
    <//>
  </div>`;
}

function tuneCommand(tune, best) {
  const c = best.cfg;
  const tuned = c.ngl === 'auto' ? [{ flag: '--fit', value: 'on' }] : [{ flag: '-ngl', value: c.ngl }, { flag: '--fit', value: 'off' }];
  if (c.ncmoe) tuned.push({ flag: '--n-cpu-moe', value: c.ncmoe });
  tuned.push(...[['-c', c.ctx], ['-np', c.np], ['-fa', c.fa], ['-ctk', c.ctk], ['-ctv', c.ctv], ['-ub', c.ub], ['-b', c.b]].map(([flag, value]) => ({ flag, value })));
  if (c.spec) {
    tuned.push({ flag: '--spec-type', value: c.spec });
    if (c.draft) tuned.push({ flag: '-md', value: c.draft });
    tuned.push({ flag: '--spec-draft-n-max', value: c.nmax });
  }
  if (c.threads) tuned.push({ flag: '-t', value: c.threads });
  if (c.threads_batch) tuned.push({ flag: '-tb', value: c.threads_batch });
  return commandLine(tune.model, [...(tune.params.base ? tune.params.base.flags : []), ...tuned], tune.params.base ? tune.params.base.env : {});
}

function History({ tunes, selected, onSelect }) {
  const now = useNow(60000);
  const tone = { done: 'success', running: 'warning', failed: 'danger', cancelled: 'neutral', interrupted: 'neutral' };
  const columns = [
    { key: 'created_at', header: 'Started', render: (t) => fmtWhen(t.started_at || t.created_at, now) },  // queued runs have no started_at until they run
    { key: 'model', header: 'Model', render: (t) => modelLabel(t.model) },
    { key: 'ctx', header: 'Context', numeric: true, render: (t) => ctxLabel(t.params.ctx) },
    { key: 'obj', header: 'Objective', render: (t) => (OBJECTIVES.find((o) => o.value === t.params.objective) || { label: t.params.objective }).label },
    { key: 'decode', header: 'Winner decode', numeric: true, render: (t) => (t.best ? fmtTps(t.best.decode0) + ' t/s' : '–') },
    { key: 'status', header: 'Result', render: (t) => html`<${E.Badge} size="sm" tone=${tone[t.status] || 'neutral'}>${t.status}<//>` },
    { key: 'view', header: '', align: 'end', render: (t) => html`<${E.Button} size="sm" variant=${t.id === selected ? 'secondary' : 'ghost'} onClick=${() => onSelect(t.id)}>${t.id === selected ? 'Showing' : 'Show'}<//>` },
  ];
  return html`<${E.Card} title="Past runs" padding="none">
    <${E.Table} caption="Past tuning runs" density="compact" columns=${columns} rows=${tunes} rowKey=${(t) => String(t.id)} />
  <//>`;
}
