/* Tuner: measure flag candidates in a fixed order, keep the winner, save it as a preset. */
import { html, E, useState, useEffect, useMemo, api, useApp, useFetch, useEvent, useNow, fmtInt, fmtTps, fmtPct, fmtBytes, fmtWhen, fmtDuration, isNum, commandLine, modelLabel } from '../lib.js';
import { askDesktopPermission } from '../notify.js';

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
  context: 'Keeps the whole target context and its KV cache on the GPU, and moves model weights to system RAM until it fits: expert weights first on MoE models, whole layers on dense ones. Finds the smallest spill for each batch size, then scores like Balanced.',
};

/* What a configuration keeps in system RAM, for tables and the winner card. */
function spillText(cfg, params) {
  if (cfg.ncmoe) return `Experts of ${cfg.ncmoe} of ${params.n_layers} layers`;
  if (cfg.ngl && cfg.ngl !== '99' && cfg.ngl !== 'auto') return `${params.n_layers + 1 - Number(cfg.ngl)} of ${params.n_layers + 1} layers`;
  return 'Nothing';
}

function estimate(prm, hasMtp) {
  const spec = hasMtp && prm.speculative ? 5 : 0;
  if (prm.objective === 'context') {
    const kv = prm.kv_policy === 'all' ? 4 : 1;
    return kv * (4 + 8 + 3 * 3) + spec + 3; // a benchmark and a few load-only probes per batch size
  }
  if (prm.mode === 'quick') return 4 + 2 + 4 + spec;
  const kv = prm.kv_policy === 'all' ? 2 : 1; // typical: one or two KV types fit at a long context
  const fa = prm.kv_policy === 'all' ? 2 : 3;
  const batches = prm.vary_batch ? 11 : 4;
  return kv * fa * batches * (1 + spec) + 3;
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
  useEvent('tune', (t) => { setLive(t.live); setSelected(t.id); list.reload(); });

  const running = live && live.running;
  const current = list.data && list.data.tunes.find((t) => t.id === selected);

  return html`<div className="page">
    <header className="page-head">
      <div className="page-head__titles">
        <h1 className="text-headline">Tuner</h1>
        <p className="text-body">Measures flag combinations for one model on this box and saves the fastest as a preset.</p>
      </div>
    </header>

    ${running ? html`<${RunningCard} tune=${current} live=${live} />` : html`<${TuneForm} status=${status} onStarted=${(id) => { setSelected(id); list.reload(); }} />`}
    ${current && html`<${TuneResult} tune=${current} live=${running && live.tune_id === current.id ? live : null} />`}
    ${list.data && list.data.tunes.length > 1 && html`<${History} tunes=${list.data.tunes} selected=${selected} onSelect=${setSelected} />`}
  </div>`;
}

/* ------------------------------------------------------------------ form */

function TuneForm({ status, onStarted }) {
  const { fail, toast } = useApp();
  const models = useFetch('/api/models');
  const presets = useFetch('/api/presets');
  const lib = (models.data ? models.data.models : []).filter((m) => !m.is_projector);
  const [model, setModel] = useState('');
  const [ctx, setCtx] = useState('32768');
  const [objective, setObjective] = useState('decode');
  const [depth, setDepth] = useState('8192');
  const [speculative, setSpeculative] = useState(true);
  const [base, setBase] = useState('');
  const [name, setName] = useState('');
  const [startAfter, setStartAfter] = useState(true);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!model && lib.length) setModel(lib[0].path); }, [models.data]);
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
  const candidates = estimate({ objective, mode, kv_policy: kvPolicy, vary_batch: varyBatch, speculative }, m && m.nextn_layers);
  const running = status && (status.state === 'ready' || status.state === 'loading');

  const start = async () => {
    askDesktopPermission();
    setBusy(true);
    try {
      const r = await api.post('/api/tune', {
        model, ctx: Number(ctx), objective, depth: Number(depth), speculative,
        base_preset_id: base ? Number(base) : null, name, start_after: startAfter,
        mode, kv_policy: kvPolicy, vary_batch: varyBatch,
      });
      toast({ title: 'Tuning started', description: 'Each candidate loads the model, then runs the same benchmark. You will get a notification when it finishes.' });
      onStarted(r.id);
    } catch (e) { fail(e, 'Could not start tuning'); } finally { setBusy(false); }
  };

  return html`<${E.Card} title="New tuning run" description="Same inputs, same candidates in the same order, same benchmark: greedy decoding with a fixed seed and the prompt cache off.">
    <div className="stack">
      <div className="grid grid--2" style=${{ gap: 'var(--space-4)' }}>
        <${E.Select} label="Model" value=${model} onChange=${(e) => setModel(e.target.value)}
          options=${lib.map((x) => ({ value: x.path, label: `${x.name} · ${fmtBytes(x.size)}` }))}
          placeholder=${lib.length ? undefined : 'No models in the library'}
          hint=${m ? [m.arch, m.size_label, m.nextn_layers ? 'has an MTP head' : null].filter(Boolean).join(' · ') : undefined} />
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
          <p className="field-hint text-caption">${m && m.expert_count ? `MoE model: moves expert weights of the first N layers (--n-cpu-moe), so attention and the KV cache stay on the GPU.` : 'Dense model: moves whole layers (-ngl). The KV cache of those layers moves with them.'} Each N is found by bisection with load-only probes.</p>
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
      ${m && m.nextn_layers ? html`<${E.Switch} label="Try self-speculative decoding (MTP)" checked=${speculative} onChange=${(e) => setSpeculative(e.target.checked)}
        description="Tests --spec-type draft-mtp with 1 to 5 draft tokens. Acceptance is measured with greedy decoding, which can run higher than with sampling." />` : null}
      <div className="grid grid--2" style=${{ gap: 'var(--space-4)' }}>
        <${E.TextField} label="Preset name" optional value=${name} onChange=${(e) => setName(e.target.value)}
          placeholder=${m ? `${m.name} · ${ctxLabel(Number(ctx))} · tuned` : ''} />
        <div style=${{ alignSelf: 'end' }}>
          <${E.Checkbox} label="Start the winning preset when done" checked=${startAfter} onChange=${(e) => setStartAfter(e.target.checked)} />
        </div>
      </div>
      ${running && html`<${E.Alert} tone="warning" title="The running model stops while tuning.">
        ${startAfter ? 'The winner starts when tuning finishes.' : 'It starts again when tuning finishes.'} The API port answers 503 in between.
      <//>`}
      <div className="row row--between">
        <span className="text-caption muted">Up to ${candidates} candidates · roughly ${Math.round(candidates * (0.5 + Number(depth) / 20000))}–${Math.round(candidates * (0.8 + Number(depth) / 8000))} minutes</span>
        <${E.Button} variant="primary" icon="play" loading=${busy} disabled=${!model} onClick=${start}>Start tuning<//>
      </div>
    </div>
  <//>`;
}

/* ------------------------------------------------------------------ running + results */

function RunningCard({ tune, live }) {
  const { fail } = useApp();
  const now = useNow(1000);
  const done = tune ? (tune.trials || []).length : 0;
  const est = tune ? estimate(tune.params, tune.params.has_mtp) : 15;
  return html`<${E.Card} title=${tune ? `Tuning ${modelLabel(tune.model)}` : 'Tuning'}
    description=${tune ? `${ctxLabel(tune.params.ctx)} context · ${tune.params.objective} · running for ${fmtDuration(now - tune.created_at)}` : ''}
    actions=${html`<${E.Button} variant="secondary" icon="x" onClick=${() => api.post('/api/tune/cancel').catch(fail)}>Cancel<//>`}>
    <div className="stack">
      <${E.Progress} label=${live.stage ? `${live.stage}: ${live.trial}` : 'Starting'} value=${Math.min(done, est)} max=${est}
        valueText=${`${done} measured`} />
      <p className="text-caption muted">The server is unavailable while tuning. Each candidate starts its own llama-server on an internal port.</p>
    </div>
  <//>`;
}

function TuneResult({ tune, live }) {
  const { go, toast, fail, refreshStatus } = useApp();
  const trials = tune.trials || [];
  const obj = tune.params.objective;
  const first = trials.find((t) => t.ok);
  const best = tune.best;
  const startPreset = async () => {
    try { await api.post('/api/server/stop'); await api.post('/api/server/start', { preset_id: tune.preset_id }); refreshStatus(); go('server'); }
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
  if (c.spec) tuned.push({ flag: '--spec-type', value: c.spec }, { flag: '--spec-draft-n-max', value: c.nmax });
  return commandLine(tune.model, [...(tune.params.base ? tune.params.base.flags : []), ...tuned], tune.params.base ? tune.params.base.env : {});
}

function History({ tunes, selected, onSelect }) {
  const now = useNow(60000);
  const tone = { done: 'success', running: 'warning', failed: 'danger', cancelled: 'neutral', interrupted: 'neutral' };
  const columns = [
    { key: 'created_at', header: 'Started', render: (t) => fmtWhen(t.created_at, now) },
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
