/* Decide: build a /v1/systemone call for a decision model, run it, and copy it as code. */
import { html, E, useState, useEffect, useMemo, useRef, useCallback, useApp, useFetch, useEvent, liveOf, copyText, fmtMs, fmtInt, fmtPct } from '../lib.js';
import { TYPES, TYPE_HELP, TEMPLATES, blankQuestion, defaultDraft, rekey, validate, toBody, fromBody, snippets, readAnswer } from '../decide.js';

const DRAFT_KEY = 'kiln-decide-draft';
const CODE_TABS = [
  { value: 'curl', label: 'curl' },
  { value: 'python', label: 'Python' },
  { value: 'javascript', label: 'JavaScript' },
  { value: 'json', label: 'JSON' },
];

function loadDraft() {
  try { return rekey(JSON.parse(localStorage.getItem(DRAFT_KEY))) || defaultDraft(); } catch { return defaultDraft(); }
}

/* Where clients reach /v1/systemone. Behind a reverse proxy that serves /v1 on this origin it is the page's own
   origin; otherwise the API port on this host. */
function useApiUrl(status) {
  const [sameOrigin, setSameOrigin] = useState(false);
  useEffect(() => {
    if (location.port) return; // a port of its own means no reverse proxy in front, so /v1 is on the API port
    fetch('/v1/models', { headers: { accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setSameOrigin(!!(d && d.object === 'list')))
      .catch(() => {});
  }, []);
  const base = sameOrigin ? location.origin : `${location.protocol}//${location.hostname}:${status ? status.api_port : 8080}`;
  return base + '/v1/systemone';
}

export function DecidePage() {
  const { status, toast, go } = useApp();
  const presets = useFetch('/api/presets');
  useEvent('presets', () => presets.reload());
  const [draft, setDraft] = useState(loadDraft);
  const [codeTab, setCodeTab] = useState('curl');
  const [run, setRun] = useState(null); // { body, ms, status, model, data } of the last run
  const [running, setRunning] = useState(false);
  const [importing, setImporting] = useState(false);
  const url = useApiUrl(status);

  useEffect(() => { try { localStorage.setItem(DRAFT_KEY, JSON.stringify(draft)); } catch {} }, [draft]);

  const deciders = useMemo(() => (presets.data || []).filter((p) => p.role === 'decision'), [presets.data]);
  const keyed = deciders.some((p) => (p.flags || []).some((f) => f.enabled !== false && ['--api-key', '--api-key-file'].includes((f.flag || '').trim())));
  const live = liveOf(status).filter((i) => i.role === 'decision');
  const check = useMemo(() => validate(draft), [draft]);
  const body = useMemo(() => toBody(draft), [draft]);
  const bodyText = JSON.stringify(body);
  const code = useMemo(() => snippets(url, body), [url, bodyText]);
  const stale = run && run.bodyText !== bodyText;

  const set = (patch) => setDraft((d) => ({ ...d, ...patch }));
  const setQ = (uid, patch) => setDraft((d) => ({ ...d, questions: d.questions.map((q) => (q.uid === uid ? { ...q, ...patch } : q)) }));
  const removeQ = (uid) => setDraft((d) => ({ ...d, questions: d.questions.filter((q) => q.uid !== uid) }));
  const moveQ = (uid, by) => setDraft((d) => {
    const qs = [...d.questions];
    const i = qs.findIndex((q) => q.uid === uid);
    const j = i + by;
    if (j < 0 || j >= qs.length) return d;
    [qs[i], qs[j]] = [qs[j], qs[i]];
    return { ...d, questions: qs };
  });
  const addQ = (type) => setDraft((d) => {
    const used = new Set(d.questions.map((q) => q.id));
    let n = d.questions.length + 1;
    while (used.has('q' + n)) n++;
    return { ...d, questions: [...d.questions, blankQuestion(type, 'q' + n)] };
  });

  const modelOptions = [
    { value: '', label: live.length ? `Any decision model (now ${live[0].name})` : 'Any decision model' },
    ...deciders.map((p) => {
      const name = p.alias || p.name;
      const up = live.some((i) => i.preset && i.preset.id === p.id);
      return { value: name, label: name + (up ? ' · running' : ' · loads on first call') };
    }),
  ];

  const doRun = useCallback(async () => {
    if (!check.ok || running) return;
    setRunning(true);
    const t0 = performance.now();
    const sent = bodyText;
    try {
      const r = await fetch('/api/decide', { method: 'POST', headers: { 'content-type': 'application/json' }, body: sent });
      const text = await r.text();
      let data;
      try { data = JSON.parse(text); } catch { data = { error: { message: text || `HTTP ${r.status}` } }; }
      setRun({ bodyText: sent, questions: draft.questions, ms: performance.now() - t0, status: r.status, model: r.headers.get('x-kiln-model'), data });
    } catch (e) {
      setRun({ bodyText: sent, questions: draft.questions, ms: performance.now() - t0, status: 0, data: { error: { message: 'Kiln is not reachable: ' + e.message } } });
    } finally {
      setRunning(false);
    }
  }, [check.ok, running, bodyText, draft.questions]);

  // Ctrl/Cmd+Enter runs from anywhere on the page
  const runRef = useRef(doRun);
  runRef.current = doRun;
  useEffect(() => {
    const on = (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); runRef.current(); } };
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, []);

  const startFrom = (id) => {
    const t = TEMPLATES.find((x) => x.id === id);
    if (t) { setDraft({ ...t.draft(), model: draft.model }); setRun(null); }
  };

  return html`<div className="page">
    <header className="page-head">
      <div className="page-head__titles">
        <h1 className="text-headline">Decide</h1>
        <p className="text-body">Build a call for a decision model, try it on the loaded model, and copy it as code. Each question's options are scored in one pass; nothing is generated.</p>
      </div>
      <div className="page-head__actions">
        <${E.Select} label="Start from a template" hideLabel value="" onChange=${(e) => startFrom(e.target.value)}
          options=${[{ value: '', label: 'Start from…' }, ...TEMPLATES.map((t) => ({ value: t.id, label: t.label }))]} />
        <${E.Button} variant="secondary" icon="upload" onClick=${() => setImporting(true)}>Paste JSON<//>
        <${E.Button} className="decide__run-top" variant="primary" icon="play" loading=${running} disabled=${!check.ok || running}
          onClick=${() => doRun().then(() => document.querySelector('.decide__side').scrollIntoView({ behavior: 'smooth' }))}>Run<//>
      </div>
    </header>

    ${presets.data && !deciders.length && html`<${E.Alert} tone="info" title="No decision model yet"
      action=${html`<${E.Button} size="sm" variant="secondary" onClick=${() => go('presets')}>Open Presets<//>`}>
      Give a preset the Decision role (for example a Perplexity decider-flash GGUF) to run calls here. You can still build one and copy the code.
    <//>`}

    <div className="grid grid--side decide">
      <div className="stack">
        <${E.Card} title="State" description="What the model reads and classifies. It is treated as data, not instructions.">
          <div className="stack">
            <div className="row row--between">
              <${E.SegmentedControl} label="State format" size="sm" value=${draft.stateMode} onChange=${(v) => set(convertState(draft, v))}
                options=${[{ value: 'text', label: 'Text' }, { value: 'json', label: 'JSON' }]} />
              <${E.Select} label="Model" hideLabel size="sm" value=${draft.model} onChange=${(e) => set({ model: e.target.value })} options=${modelOptions} />
            </div>
            <div>
              <textarea aria-label="State" className=${'textarea ' + (draft.stateMode === 'json' ? 'text-code' : 'text-body')} rows="5" value=${draft.state}
                aria-invalid=${!!check.state || undefined} spellCheck=${draft.stateMode !== 'json'}
                placeholder=${draft.stateMode === 'json' ? '{ "ticket": 4411, "body": "…" }' : 'A support ticket, a comment, a log line…'}
                onChange=${(e) => set({ state: e.target.value })}></textarea>
              ${check.state && html`<p className="field-hint text-caption is-error">${check.state}</p>`}
            </div>
          </div>
        <//>

        ${draft.questions.map((q, i) => html`<${QuestionCard} key=${q.uid} q=${q} index=${i} count=${draft.questions.length}
          errors=${check.byUid[q.uid] || {}} onChange=${(p) => setQ(q.uid, p)} onRemove=${() => removeQ(q.uid)} onMove=${(by) => moveQ(q.uid, by)} />`)}
        ${check.questions.map((m) => html`<p key=${m} className="text-caption is-error">${m}</p>`)}

        <div className="row">
          <span className="text-label muted">Add a question</span>
          ${TYPES.map((t) => html`<${E.Button} key=${t.value} size="sm" variant="secondary" icon="plus" onClick=${() => addQ(t.value)}>${t.label}<//>`)}
        </div>
      </div>

      <div className="stack decide__side">
        <${E.Card} title="Answer" description=${run ? null : 'Runs on this Kiln. The call is recorded under Requests like any client’s.'}
          actions=${html`<${E.Button} variant="primary" icon="play" loading=${running} disabled=${!check.ok || running} onClick=${doRun}
            title="Ctrl+Enter">${run ? 'Run again' : 'Run'}<//>`}>
          <${Answer} run=${run} stale=${stale} invalid=${!check.ok} />
        <//>

        <${E.Card} title="Code" description=${html`<span>POST <span className="mono">${url}</span></span>`}>
          <${E.Tabs} label="Language" items=${CODE_TABS} value=${codeTab} onChange=${setCodeTab}>
            <div className="code-wrap">
              <pre className="code text-code decide__code">${code[codeTab]}</pre>
              <${E.IconButton} icon="copy" label="Copy code" size="sm" variant="secondary"
                onClick=${() => copyText(code[codeTab]).then(() => toast({ title: 'Copied' }))} />
            </div>
          <//>
          ${keyed && html`<p className="text-caption subtle decide__note">A decision preset sets <span className="mono">--api-key</span>: add <span className="mono">-H 'Authorization: Bearer …'</span> when you call it from outside.</p>`}
        <//>
      </div>
    </div>

    <${ImportDialog} open=${importing} onClose=${() => setImporting(false)} onLoad=${(d) => { setDraft(d); setRun(null); setImporting(false); toast({ title: 'Request loaded' }); }} />
  </div>`;
}

/* Switching Text ⇄ JSON keeps what was typed where it can. */
function convertState(draft, mode) {
  if (mode === draft.stateMode) return {};
  if (mode === 'json') {
    const t = draft.state.trim();
    let ok = false;
    try { const v = JSON.parse(t); ok = v !== null && typeof v === 'object'; } catch {}
    return { stateMode: 'json', state: ok || !t ? draft.state : JSON.stringify({ text: draft.state }, null, 2) };
  }
  try {
    const v = JSON.parse(draft.state);
    if (v && typeof v === 'object' && Object.keys(v).length === 1 && typeof v.text === 'string') return { stateMode: 'text', state: v.text };
  } catch {}
  return { stateMode: 'text' };
}

function QuestionCard({ q, index, count, errors, onChange, onRemove, onMove }) {
  const actions = html`<div className="card-actions">
    <${E.IconButton} icon="chevron-up" size="sm" label="Move up" disabled=${index === 0} onClick=${() => onMove(-1)} />
    <${E.IconButton} icon="chevron-down" size="sm" label="Move down" disabled=${index === count - 1} onClick=${() => onMove(1)} />
    <${E.IconButton} icon="trash" size="sm" label="Remove question" onClick=${onRemove} />
  </div>`;
  return html`<${E.Card} title=${`Question ${index + 1}`} description=${TYPE_HELP[q.type]} actions=${actions}>
    <div className="stack">
      <div className="decide__q-head">
        <${E.TextField} label="Name" mono value=${q.id} error=${errors.id} hint=${errors.id ? undefined : 'The key in the answer'}
          onChange=${(e) => onChange({ id: e.target.value })} />
        <div className="em-field">
          <span className="field-label text-label">Type</span>
          <${E.SegmentedControl} label="Question type" value=${q.type} onChange=${(v) => onChange({ type: v })} options=${TYPES} />
        </div>
      </div>
      <${E.TextField} label="Question" optional value=${q.instructions} placeholder="Choose the best matching option."
        onChange=${(e) => onChange({ instructions: e.target.value })} />
      ${q.type === 'choice' && html`<${OptionList} q=${q} error=${errors.options} onChange=${onChange} />`}
      ${q.type === 'score' && html`<${LevelList} q=${q} error=${errors.levels} onChange=${onChange} />`}
      ${q.type === 'noul' && html`<div className="grid grid--2">
        <${E.TextField} label="What yes means" optional value=${q.noTrue} placeholder="Yes / true" onChange=${(e) => onChange({ noTrue: e.target.value })} />
        <${E.TextField} label="What no means" optional value=${q.noFalse} placeholder="No / false" onChange=${(e) => onChange({ noFalse: e.target.value })} />
      </div>`}
    </div>
  <//>`;
}

function OptionList({ q, error, onChange }) {
  const setOpt = (i, patch) => onChange({ options: q.options.map((o, j) => (j === i ? { ...o, ...patch } : o)) });
  return html`<div className="stack stack--sm">
    <span className="field-label text-label">Options</span>
    ${q.options.map((o, i) => html`<div key=${i} className="decide__opt">
      <${E.TextField} label=${`Option ${i + 1} name`} hideLabel mono placeholder="name" value=${o.key} onChange=${(e) => setOpt(i, { key: e.target.value })} />
      <${E.TextField} label=${`Option ${i + 1} description`} hideLabel placeholder="What it means (optional)" value=${o.desc} onChange=${(e) => setOpt(i, { desc: e.target.value })} />
      <${E.IconButton} icon="x" size="sm" label=${`Remove option ${i + 1}`} disabled=${q.options.length <= 2}
        onClick=${() => onChange({ options: q.options.filter((_, j) => j !== i) })} />
    </div>`)}
    ${error && html`<p className="text-caption is-error">${error}</p>`}
    <div><${E.Button} size="sm" variant="ghost" icon="plus" onClick=${() => onChange({ options: [...q.options, { key: '', desc: '' }] })}>Add option<//></div>
  </div>`;
}

function LevelList({ q, error, onChange }) {
  const setLvl = (i, v) => onChange({ levels: q.levels.map((l, j) => (j === i ? v : l)) });
  return html`<div className="stack stack--sm">
    <span className="field-label text-label">Levels, lowest first</span>
    ${q.levels.map((l, i) => html`<div key=${i} className="decide__lvl">
      <span className="text-code subtle decide__lvl-n">${i}</span>
      <${E.TextField} label=${`Level ${i}`} hideLabel placeholder=${i === 0 ? 'lowest' : i === q.levels.length - 1 ? 'highest' : ''} value=${l} onChange=${(e) => setLvl(i, e.target.value)} />
      <${E.IconButton} icon="x" size="sm" label=${`Remove level ${i}`} disabled=${q.levels.length <= 2}
        onClick=${() => onChange({ levels: q.levels.filter((_, j) => j !== i) })} />
    </div>`)}
    ${error && html`<p className="text-caption is-error">${error}</p>`}
    <div><${E.Button} size="sm" variant="ghost" icon="plus" onClick=${() => onChange({ levels: [...q.levels, ''] })}>Add level<//></div>
  </div>`;
}

function Answer({ run, stale, invalid }) {
  const [raw, setRaw] = useState(false);
  if (!run) {
    return html`<p className="text-body muted">${invalid ? 'Fix the marked fields, then run the call.' : 'Press Run (or Ctrl+Enter) to score this request.'}</p>`;
  }
  const { data } = run;
  const err = data && data.error;
  return html`<div className=${'stack' + (stale ? ' is-refreshing' : '')}>
    ${stale && html`<p className="text-caption muted">Edited since this run. Run again to update.</p>`}
    ${err ? html`<${E.Alert} tone="danger" title=${run.status ? `HTTP ${run.status}` : 'Not sent'}>${err.message || JSON.stringify(err)}<//>`
      : run.questions.map((q) => {
        const a = readAnswer(q, data.answers && data.answers[q.id.trim()]);
        return a && html`<div key=${q.uid} className="decide__ans">
          <div className="row row--between">
            <span className="text-code">${q.id}</span>
            <span><strong className="text-subtitle">${a.headline}</strong>${a.sub && html` <span className="muted">${a.sub}</span>`}
              <span className="muted num"> · ${fmtPct(a.p)}</span></span>
          </div>
          ${a.rows.map((r) => html`<div key=${r.label} className=${'prob' + (r.top ? ' is-top' : '')}>
            <span className="prob__label text-caption truncate" title=${r.label}>${r.label}</span>
            <span className="prob__track"><span className="prob__fill" style=${{ width: Math.max(0.5, r.p * 100) + '%' }}></span></span>
            <span className="prob__pct text-caption num">${fmtPct(r.p)}</span>
          </div>`)}
        </div>`;
      })}
    <div className="row row--between text-caption muted">
      <span>${[run.model, data && data.usage ? fmtInt(data.usage.input_tokens) + ' tokens' : null, fmtMs(run.ms)].filter(Boolean).join(' · ')}</span>
      <${E.Button} size="sm" variant="ghost" onClick=${() => setRaw(!raw)}>${raw ? 'Hide' : 'Show'} raw response<//>
    </div>
    ${raw && html`<pre className="code text-code decide__code">${JSON.stringify(data, null, 2)}</pre>`}
  </div>`;
}

function ImportDialog({ open, onClose, onLoad }) {
  const [text, setText] = useState('');
  const [error, setError] = useState(null);
  useEffect(() => { if (open) { setText(''); setError(null); } }, [open]);
  const load = () => {
    try { onLoad(fromBody(text)); } catch (e) { setError(e.message); }
  };
  return html`<${E.Dialog} open=${open} onClose=${onClose} title="Paste a request" description="A /v1/systemone body, for example from your code or the Requests log. It replaces what is in the builder."
    footer=${html`<${E.Button} variant="ghost" onClick=${onClose}>Cancel<//><${E.Button} variant="primary" disabled=${!text.trim()} onClick=${load}>Load<//>`}>
    <textarea aria-label="Request JSON" className="textarea text-code" rows="12" autoFocus value=${text} spellCheck=${false}
      placeholder=${'{\n  "state": "…",\n  "questions": { "team": { "type": "choice", "criteria": { … } } }\n}'}
      onChange=${(e) => { setText(e.target.value); setError(null); }}></textarea>
    ${error && html`<p className="field-hint text-caption is-error">${error}</p>`}
  <//>`;
}
