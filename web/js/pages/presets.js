/* Presets: saved model + flags + env, with an editor that knows every llama-server flag. */
import {
  html, E, useState, useEffect, useMemo, useRef, api, useApp, useFetch, useEvent, useNow,
  fmtWhen, fmtBytes, copyText, commandLine, modelLabel, RoleBadge, ROLE_LABEL, liveOf, fmtMiB,
} from '../lib.js';
import { chipsFor, toggleChip, readFeatures, setVision, setSpec, DRAFT_LABEL } from '../features.js';

let flagMetaPromise = null;
function useFlagMeta() {
  const [meta, setMeta] = useState(null);
  useEffect(() => {
    flagMetaPromise = flagMetaPromise || api.get('/api/flags');
    flagMetaPromise.then(setMeta).catch(() => setMeta({ flags: [] }));
  }, []);
  return useMemo(() => {
    if (!meta) return null;
    const byName = {};
    for (const f of meta.flags) for (const n of f.names) byName[n] = f;
    const names = meta.flags.flatMap((f) => f.names.filter((n) => n.startsWith('--') || f.names.length === 1));
    return { ...meta, byName, names };
  }, [meta]);
}

const QUICK = ['-ngl', '-c', '-fa', '-np', '-ctk', '-ctv', '-b', '-ub', '-t', '--jinja', '--spec-type', '--spec-draft-n-max', '--temp', '--top-p', '--top-k', '--mlock', '--no-mmap', '--api-key'];
/* What a new preset starts with. Kiln adds the rest of what a role needs when the model starts. */
const DEFAULT_FLAGS = {
  chat: [{ flag: '-ngl', value: '99' }, { flag: '-fa', value: 'on' }, { flag: '-c', value: '16384' }, { flag: '-np', value: '1' }, { flag: '--jinja', value: null }],
  embedding: [{ flag: '-ngl', value: '99' }, { flag: '-c', value: '8192' }],
  rerank: [{ flag: '-ngl', value: '99' }, { flag: '-c', value: '8192' }],
  decision: [{ flag: '-ngl', value: '99' }],
};
const flagsFor = (role) => DEFAULT_FLAGS[role] || DEFAULT_FLAGS.chat;
const sig = (flags) => JSON.stringify((flags || []).map((f) => [f.flag, f.value == null ? null : String(f.value), f.enabled === false]));
const isDefaultFlags = (flags) => Object.keys(DEFAULT_FLAGS).some((r) => sig(flags) === sig(DEFAULT_FLAGS[r]));
const body = (p, over = {}) => ({ name: p.name, description: p.description, model: p.model, flags: p.flags, env: p.env, role: p.role || 'chat', alias: p.alias || '', pinned: !!p.pinned, ...over });

export function PresetsPage({ arg }) {
  const { toast, dismiss, fail, go, status, refreshStatus } = useApp();
  const presets = useFetch('/api/presets');
  const settings = useFetch('/api/settings');
  const library = useFetch('/api/models');
  useEvent('presets', () => presets.reload());
  useEvent('models', () => library.reload());
  const [editing, setEditing] = useState(null);
  const [importing, setImporting] = useState(false);
  const [deleting, setDeleting] = useState(null);
  const [noFit, setNoFit] = useState(null);
  const now = useNow(60000);

  useEffect(() => {
    if (arg && arg.startsWith('new')) {
      const model = arg.length > 4 ? decodeURIComponent(arg.slice(4)) : '';
      setEditing({ name: model ? modelLabel(model) : '', description: '', model, flags: flagsFor('chat'), env: {}, role: 'chat' });
      history.replaceState(null, '', '#/presets');
    }
  }, [arg]);

  const live = liveOf(status);
  const runningOf = (p) => live.find((i) => i.preset && i.preset.id === p.id);
  const libOf = (p) => (library.data ? library.data.models.find((m) => m.path === p.model) : undefined);

  const start = async (p, extra = {}) => {
    try {
      await api.post('/api/server/start', { preset_id: p.id, ...extra });
      toast({ tone: 'success', title: `Starting ${p.name}` });
      setNoFit(null);
      refreshStatus();
      go('server');
    } catch (e) {
      if (e.data && e.data.code === 'no_fit') setNoFit({ preset: p, detail: e.message, evict: e.data.evict || [], plan: e.data.plan });
      else fail(e, 'Could not start the preset');
    }
  };
  const stop = async (inst) => {
    try { await api.post('/api/server/stop', { run_id: inst.run_id }); toast({ title: `${inst.name} stopped` }); refreshStatus(); } catch (e) { fail(e); }
  };
  const duplicate = async (p) => {
    try { const n = await api.post(`/api/presets/${p.id}/duplicate`); toast({ title: `Created ${n.name}` }); presets.reload(); } catch (e) { fail(e); }
  };
  const remove = async () => {
    try { await api.del(`/api/presets/${deleting.id}`); toast({ title: `Deleted ${deleting.name}` }); setDeleting(null); presets.reload(); } catch (e) { fail(e); }
  };
  const flip = async (p, chip) => {
    try {
      await api.put(`/api/presets/${p.id}`, body(p, { flags: toggleChip(libOf(p), p.flags, chip) }));
      presets.reload();
      const inst = runningOf(p);
      let id;
      id = toast({
        title: `${chip.label} ${chip.on ? 'off' : 'on'} for ${p.name}`,
        description: inst ? 'Restart it to apply.' : undefined,
        action: inst && html`<${E.Button} size="sm" variant="ghost" onClick=${() => { dismiss(id); api.post('/api/server/restart', { run_id: inst.run_id }).then(refreshStatus).catch((e) => fail(e)); }}>Restart<//>`,
      });
    } catch (e) { fail(e, 'Could not change the preset'); }
  };
  const autostart = settings.data ? settings.data.autostart_preset_ids || [] : [];

  const columns = [
    { key: 'name', header: 'Preset', sortable: true, render: (p) => html`<div className="cell-main">
      <span className="row">${p.name}
        ${runningOf(p) && html`<${E.Badge} size="sm" tone="success" live>${runningOf(p).state === 'ready' ? 'Running' : 'Loading'}<//>`}
        ${autostart.includes(p.id) && html`<${E.Badge} size="sm" tone="ember">Autostart<//>`}
        ${p.pinned && html`<${E.Badge} size="sm" tone="neutral">Pinned<//>`}
        ${p.role && p.role !== 'chat' && html`<${RoleBadge} role=${p.role} />`}</span>
      <span className="cell-sub text-caption">${p.alias ? html`<span className="mono">${p.alias}</span>` : ''}${p.alias && p.description ? ' · ' : ''}${p.description}</span>
    </div>` },
    { key: 'model', header: 'Model', render: (p) => html`<span className="truncate" style=${{ maxWidth: 190, display: 'inline-block' }} title=${p.model}>${modelLabel(p.model) || html`<span className="subtle">None</span>`}</span>` },
    { key: 'features', header: 'Features', render: (p) => {
      const lib = libOf(p);
      const chips = chipsFor(lib, p.flags, { noVision: p.role === 'decision' && lib && lib.decision && lib.decision.kind === 'pplx-v3' });
      return chips.length ? html`<div className="chips">${chips.map((c) => html`<${FeatureChip} key=${c.id} chip=${c} onToggle=${() => flip(p, c)} />`)}</div>` : html`<span className="subtle">–</span>`;
    } },
    { key: 'flags', header: 'Flags', mono: true, render: (p) => html`<span className="truncate subtle" style=${{ maxWidth: 200, display: 'inline-block' }} title=${commandLine('', p.flags, p.env)}>${commandLine('', p.flags, p.env).replace(/^llama-server ?/, '') || '–'}</span>` },
    { key: 'last_used', header: 'Last used', sortable: true, sortValue: (p) => p.last_used || 0, render: (p) => (p.last_used ? fmtWhen(p.last_used, now) : html`<span className="subtle">Never</span>`) },
    { key: 'actions', header: '', align: 'end', render: (p) => html`<div className="row" style=${{ justifyContent: 'flex-end', flexWrap: 'nowrap' }}>
      ${runningOf(p) ? html`<${E.IconButton} icon="pause" label=${'Stop ' + p.name} size="sm" onClick=${() => stop(runningOf(p))} />`
        : html`<${E.IconButton} icon="play" label=${'Start ' + p.name} size="sm" onClick=${() => start(p)} />`}
      <${E.IconButton} icon="edit" label=${'Edit ' + p.name} size="sm" onClick=${() => setEditing(p)} />
      <${E.IconButton} icon="copy" label=${'Duplicate ' + p.name} size="sm" onClick=${() => duplicate(p)} />
      <${E.IconButton} icon="trash" label=${'Delete ' + p.name} size="sm" onClick=${() => setDeleting(p)} />
    </div>` },
  ];

  return html`<div className="page">
    <header className="page-head">
      <div className="page-head__titles">
        <h1 className="text-headline">Presets</h1>
        <p className="text-body">A model plus the flags and environment to run it with. Several can run at once when they fit.</p>
      </div>
      <div className="page-head__actions">
        <${E.Button} variant="secondary" icon="upload" onClick=${() => setImporting(true)}>Import command<//>
        <${E.Button} variant="primary" icon="plus" onClick=${() => setEditing({ name: '', description: '', model: '', flags: flagsFor('chat'), env: {}, role: 'chat' })}>New preset<//>
      </div>
    </header>

    <${E.Card} padding="none">
      ${presets.data && presets.data.length === 0 ? html`<${E.EmptyState} icon="file" title="No presets yet"
          description="Create one from scratch, or paste a llama-server command you already use."
          actions=${html`<${E.Button} variant="secondary" icon="upload" onClick=${() => setImporting(true)}>Import command<//>`} />`
        : html`<${E.Table} caption="Presets" columns=${columns} rows=${presets.data || []} rowKey=${(p) => String(p.id)} />`}
    <//>

    ${editing && html`<${PresetEditor} preset=${editing} onClose=${() => setEditing(null)} onSaved=${(p) => { setEditing(null); presets.reload(); toast({ tone: 'success', title: `Saved ${p.name}` }); }} />`}
    ${importing && html`<${ImportDialog} onClose=${() => setImporting(false)} onParsed=${(p) => { setImporting(false); setEditing(p); }} />`}
    <${E.Dialog} open=${!!deleting} onClose=${() => setDeleting(null)} size="sm" title=${deleting ? `Delete ${deleting.name}?` : ''}
      description="The preset is removed. Its run history and request stats stay."
      footer=${html`<${E.Button} variant="secondary" onClick=${() => setDeleting(null)}>Cancel<//><${E.Button} variant="danger" icon="trash" onClick=${remove}>Delete preset<//>`} />
    <${NoFitDialog} info=${noFit} onClose=${() => setNoFit(null)} onStart=${start} />
  </div>`;
}

function FeatureChip({ chip, onToggle }) {
  return html`<button type="button" className=${'feature-chip' + (chip.on ? ' is-on' : '')} aria-pressed=${chip.on}
    title=${chip.hint + (chip.on ? '. Click to turn off.' : '. Click to turn on.')} onClick=${onToggle}>
    <span className="feature-chip__dot" aria-hidden="true"></span>${chip.label}<span className="em-sr-only">${chip.on ? ' on' : ' off'}</span>
  </button>`;
}

function NoFitDialog({ info, onClose, onStart }) {
  const p = info && info.preset;
  const evict = info ? info.evict : [];
  return html`<${E.Dialog} open=${!!info} onClose=${onClose} size="sm" title=${p ? `${p.name} doesn't fit` : ''}
    description=${info ? info.detail : ''}
    footer=${info && html`<${E.Button} variant="secondary" onClick=${onClose}>Cancel<//>
      <${E.Button} variant="ghost" onClick=${() => onStart(p, { force: true })}>Start anyway<//>
      ${evict.length > 0 && html`<${E.Button} variant="primary" icon="play" onClick=${() => onStart(p, { replace: evict.map((e) => e.run_id) })}>Stop ${evict.length === 1 ? evict[0].name : evict.length + ' models'} and start<//>`}`}>
    ${info && (evict.length > 0
      ? html`<p className="text-body">Stopping ${evict.map((e) => html`<strong key=${e.run_id}>${e.name}</strong>`).reduce((a, b) => [a, ', ', b])} frees about ${fmtMiB(evict.reduce((a, e) => a + (e.gpu_mib || 0), 0))}.</p>`
      : html`<p className="text-body">Stopping the other running models wouldn't free enough, or they are pinned.</p>`)}
  <//>`;
}

/* ------------------------------------------------------------------ editor */

let rowSeq = 0;
const withIds = (flags) => (flags || []).map((f) => ({ ...f, _id: ++rowSeq }));
const ensureIds = (rows) => rows.map((r) => (r._id ? r : { ...r, _id: ++rowSeq }));
const envToText = (env) => Object.entries(env || {}).map(([k, v]) => `${k}=${v}`).join('\n');
function textToEnv(text) {
  const env = {};
  for (const line of text.split('\n')) {
    const m = line.trim().match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (m) env[m[1]] = m[2];
  }
  return env;
}

export function PresetEditor({ preset, onClose, onSaved }) {
  const { fail } = useApp();
  const meta = useFlagMeta();
  const models = useFetch('/api/models');
  const roleInfo = useFetch('/api/roles');
  const [name, setName] = useState(preset.name || '');
  const [description, setDescription] = useState(preset.description || '');
  const [model, setModel] = useState(preset.model || '');
  const [role, setRole] = useState(preset.role || 'chat');
  const [alias, setAlias] = useState(preset.alias || '');
  const [pinned, setPinned] = useState(!!preset.pinned);
  const [rows, setRows] = useState(withIds(preset.flags));
  const [envText, setEnvText] = useState(envToText(preset.env));
  const [saving, setSaving] = useState(false);
  const [nameError, setNameError] = useState(null);
  const [aliasError, setAliasError] = useState(null);
  // A new preset follows the model it is for until you choose a role or edit the flags yourself.
  const roleTouched = useRef(!!preset.id || !!preset.role && preset.role !== 'chat');
  const flagsTouched = useRef(!!preset.id || !isDefaultFlags(preset.flags));

  const library = models.data ? models.data.models : [];
  const known = library.some((m) => m.path === model);
  const [customPath, setCustomPath] = useState(!!preset.model && models.data && !known);
  useEffect(() => { if (models.data && preset.model && !models.data.models.some((m) => m.path === preset.model)) setCustomPath(true); }, [models.data]);
  const current = library.find((m) => m.path === model);
  const modelOpts = [
    ...library.filter((m) => !m.is_projector && !m.is_draft).map((m) => ({ value: m.path, label: `${m.name} · ${fmtBytes(m.size)}${m.repo ? ' · ' + m.repo : ''}` })),
    { value: '__custom', label: 'Another path…' },
  ];

  const pickRole = (r, byHand) => {
    if (byHand) roleTouched.current = true;
    setRole(r);
    if (!flagsTouched.current) setRows(withIds(flagsFor(r)));
  };
  useEffect(() => {
    if (!roleTouched.current && current && current.role_hint && current.role_hint !== role) pickRole(current.role_hint, false);
  }, [current && current.path, models.data]);

  const touchRows = (fn) => { flagsTouched.current = true; setRows(fn); };
  const setRow = (id, patch) => touchRows((rs) => rs.map((r) => (r._id === id ? { ...r, ...patch } : r)));
  const addFlag = (flag, value = '') => touchRows((rs) => [...rs, { _id: ++rowSeq, flag, value: defaultValue(meta, flag, value) }]);
  const present = new Set(rows.filter((r) => r.enabled !== false).map((r) => r.flag));
  const isPresent = (f) => { const m = meta && meta.byName[f]; return m ? m.names.some((n) => present.has(n)) : present.has(f); };

  const env = textToEnv(envText);
  const flags = rows.filter((r) => r.flag.trim()).map((r) => ({ flag: r.flag.trim(), value: r.value === '' ? null : r.value, ...(r.enabled === false ? { enabled: false } : {}) }));
  const preview = commandLine(model, flags, env);
  const roles = roleInfo.data || {};

  const save = async () => {
    if (!name.trim()) { setNameError('Give the preset a name.'); return; }
    setSaving(true);
    try {
      const b = { name, description, model, flags, env, role, alias, pinned };
      const saved = preset.id ? await api.put(`/api/presets/${preset.id}`, b) : await api.post('/api/presets', b);
      onSaved(saved);
    } catch (e) {
      if (/already exists/.test(e.message)) setNameError(e.message);
      else if (/alias|already used/.test(e.message)) setAliasError(e.message);
      else fail(e, 'Could not save the preset');
    } finally { setSaving(false); }
  };

  const decision = current && current.decision;
  return html`<${E.Dialog} open=${true} onClose=${onClose} size="lg" dismissible=${false}
    title=${preset.id ? `Edit ${preset.name}` : 'New preset'}
    footer=${html`<${E.Button} variant="secondary" onClick=${onClose}>Cancel<//><${E.Button} variant="primary" icon="check" loading=${saving} onClick=${save}>Save preset<//>`}>
    <div className="stack">
      <div className="grid grid--2" style=${{ gap: 'var(--space-4)' }}>
        <${E.TextField} label="Name" value=${name} error=${nameError} onChange=${(e) => { setName(e.target.value); setNameError(null); }} placeholder="Qwen3.8 27B, 128K, MTP" />
        <${E.TextField} label="Description" optional value=${description} onChange=${(e) => setDescription(e.target.value)} placeholder="What this preset is for" />
      </div>
      ${customPath ? html`<${E.TextField} label="Model path" mono value=${model} onChange=${(e) => setModel(e.target.value)}
          hint=${html`A GGUF path inside the container. <a href="#" onClick=${(e) => { e.preventDefault(); setCustomPath(false); }}>Choose from the library</a>`} placeholder="/models/…/model.gguf" />`
        : html`<${E.Select} label="Model" value=${known ? model : ''} placeholder=${library.length ? 'Choose a model' : 'No models in the library yet'}
          options=${modelOpts} onChange=${(e) => { if (e.target.value === '__custom') setCustomPath(true); else setModel(e.target.value); }}
          hint=${current ? [current.arch, current.size_label, current.quant, current.context_length ? 'trained to ' + current.context_length.toLocaleString('en-US') + ' tokens' : null, current.nextn_layers ? 'has an MTP head' : null, decision ? 'decision model' : null].filter(Boolean).join(' · ') : undefined} />`}

      <div className="grid grid--2" style=${{ gap: 'var(--space-4)' }}>
        <${E.Select} label="Role" value=${role} onChange=${(e) => pickRole(e.target.value, true)}
          options=${Object.keys(ROLE_LABEL).map((r) => ({ value: r, label: ROLE_LABEL[r] }))}
          hint=${(roles[role] && roles[role].help) || undefined} />
        <${E.TextField} label="API name" optional value=${alias} error=${aliasError} onChange=${(e) => { setAlias(e.target.value); setAliasError(null); }}
          placeholder=${name || 'qwen-27b'} hint=${html`What clients put in <span className="mono">model</span>. Defaults to the preset name.`} />
      </div>
      ${roles[role] && roles[role].adds && html`<p className="text-caption muted">Kiln adds when it starts: <span className="mono">${roles[role].adds}</span></p>`}
      <${RoleNote} role=${role} decision=${decision} />
      <${E.Switch} label="Keep loaded" checked=${pinned} onChange=${(e) => setPinned(e.target.checked)}
        description="Never stopped to make room for another model, by a request or by Start." />

      <${FeaturesPanel} model=${current} role=${role} rows=${rows} onChange=${(next) => touchRows(() => ensureIds(next))} />

      <hr className="divider" />
      <div className="row row--between">
        <h3 className="section-title text-subtitle">Flags</h3>
        <span className="text-caption subtle">${meta ? `${meta.flags.length} flags known from ${meta.version || 'llama-server'}` : 'Reading llama-server --help…'}</span>
      </div>
      <datalist id="kiln-flag-names">${meta && meta.names.map((n) => html`<option key=${n} value=${n}>${(meta.byName[n].desc || '').slice(0, 80)}</option>`)}</datalist>
      <div className="flags">
        ${rows.map((r) => html`<${FlagRow} key=${r._id} row=${r} meta=${meta} onChange=${(p) => setRow(r._id, p)} onRemove=${() => touchRows((rs) => rs.filter((x) => x._id !== r._id))} />`)}
      </div>
      <div className="chips">
        <${E.Button} variant="secondary" size="sm" icon="plus" onClick=${() => addFlag('')}>Add flag<//>
        ${QUICK.filter((f) => !isPresent(f) && (!meta || meta.byName[f])).map((f) => html`<${E.Button} key=${f} variant="ghost" size="sm" onClick=${() => addFlag(f)}><span className="mono">${f}</span><//>`)}
      </div>

      <hr className="divider" />
      <div>
        <label className="field-label text-label" htmlFor="preset-env">Environment <span className="text-caption subtle">Optional</span></label>
        <textarea id="preset-env" className="textarea text-code" rows="3" value=${envText} onChange=${(e) => setEnvText(e.target.value)}
          placeholder=${'GGML_VK_VISIBLE_DEVICES=0\nRADV_PERFTEST=coop_matrix'} spellCheck=${false}></textarea>
        <p className="field-hint text-caption">One KEY=value per line, set only for this preset's llama-server process.</p>
      </div>
      <div>
        <p className="field-label text-label">Command</p>
        <div className="code-wrap">
          <pre className="code text-code">${preview}</pre>
          <${E.IconButton} icon="copy" label="Copy command" size="sm" variant="secondary" onClick=${() => copyText(preview)} />
        </div>
      </div>
    </div>
  <//>`;
}

function RoleNote({ role, decision }) {
  if (role === 'decision') {
    if (!decision) return html`<${E.Alert} tone="warning" title="This doesn't look like a decision model.">A decision model scores options instead of generating text. Its GGUF says so (llama.cpp's <span className="mono">decision.type</span>, or Perplexity's <span className="mono">pplx_decider</span> keys). Pick the role that fits the model, or continue if you know better.<//>`;
    if (decision.kind === 'pplx-v3') return html`<${E.Alert} tone="info" title="Kiln scores this checkpoint itself.">
      llama-server's own <span className="mono">/v1/systemone</span> speaks an older protocol than this Perplexity decider (v3), so Kiln runs llama-server only to compute hidden states and applies the checkpoint's readout. Send requests to <span className="mono">/v1/systemone</span> on the API port. Text only: images can't be scored through llama-server yet, so the projector is not loaded.
    <//>`;
    if (decision.kind === 'unsupported') return html`<${E.Alert} tone="danger" title="Unsupported decision protocol.">This checkpoint uses protocol ${String(decision.protocol)}. Neither llama-server nor Kiln implements it.<//>`;
    return html`<${E.Alert} tone="info" title="Served by llama-server.">This model has llama.cpp's decision metadata, so llama-server answers <span className="mono">/v1/systemone</span> itself. Kiln routes and records the requests.<//>`;
  }
  if (decision) return html`<${E.Alert} tone="info" title="This is a decision model.">Set the role to Decision to route <span className="mono">/v1/systemone</span> to it.<//>`;
  return null;
}

/* Vision and speculative decoding for the chosen model. Switching one off keeps its flags in the preset
   (greyed out in the list below) so it comes back as it was. */
function FeaturesPanel({ model, role, rows, onChange }) {
  const f = readFeatures(rows);
  const projectors = (model && model.projectors) || [];
  const drafts = (model && model.drafts) || [];
  const hasMtp = !!(model && model.nextn_layers) || f.spec.type === 'draft-mtp';
  const adapter = model && model.decision && model.decision.kind === 'pplx-v3' && role === 'decision';
  const special = !!(projectors.length || drafts.length || hasMtp || f.vision.present || f.spec.type);
  if (!special || adapter) return null;

  const draftOf = (path) => drafts.find((d) => d.path === path);
  const specValue = f.spec.mode === 'off' ? 'off' : f.spec.mode === 'mtp' ? 'mtp' : 'draft:' + (f.spec.draftPath || (drafts[0] && drafts[0].path) || '');
  const specOpts = [{ value: 'off', label: 'Off' }];
  if (hasMtp) specOpts.push({ value: 'mtp', label: 'MTP: the head built into this model' });
  for (const d of drafts) specOpts.push({ value: 'draft:' + d.path, label: `${DRAFT_LABEL[d.type] || 'Draft'}: ${d.name} (${fmtBytes(d.size)})` });
  if (f.spec.mode === 'draft' && !draftOf(f.spec.draftPath)) {
    specOpts.push({ value: specValue, label: `${DRAFT_LABEL[f.spec.type] || f.spec.type}: ${modelLabel(f.spec.draftPath) || 'file in the preset'}` });
  }
  const onSpec = (v) => {
    if (v === 'off') return onChange(setSpec(rows, 'off'));
    if (v === 'mtp') return onChange(setSpec(rows, 'mtp'));
    const path = v.slice(6);
    const d = draftOf(path) || { path, type: f.spec.type || 'draft-simple' };
    return onChange(setSpec(rows, 'draft', d));
  };
  const projectorValue = f.vision.path || projectors[0] || '';
  return html`<${E.Card} title="Model features" description="Extra files that come with this model. Switch them on or off here; the flags stay in the preset.">
    <div className="stack">
      ${(projectors.length > 0 || f.vision.present) && html`<div className="stack stack--sm">
        <${E.Switch} label="Vision" checked=${f.vision.on} onChange=${(e) => onChange(setVision(rows, e.target.checked, projectorValue))}
          description=${projectors.length ? 'Loads the vision projector so the model can read images.' : 'No projector found next to this model.'}
          disabled=${!projectors.length && !f.vision.present} />
        ${projectors.length > 1 && html`<${E.Select} label="Projector" size="sm" value=${projectorValue} onChange=${(e) => onChange(setVision(rows, true, e.target.value))}
          options=${projectors.map((p) => ({ value: p, label: modelLabel(p) }))} />`}
      </div>`}
      ${(hasMtp || drafts.length > 0 || f.spec.type) && html`<${E.Select} label="Speculative decoding" value=${specValue} onChange=${(e) => onSpec(e.target.value)} options=${specOpts}
        hint=${f.spec.mode === 'off' ? 'Off: tokens are generated one at a time.' : `--spec-type ${f.spec.type}${f.spec.mode === 'draft' ? ' with the draft file above' : ''}. Tune the draft length with --spec-draft-n-max below.`} />`}
    </div>
  <//>`;
}

function defaultValue(meta, flag, value) {
  if (value) return value;
  const m = meta && meta.byName[flag];
  if (!m) return '';
  const d = (m.desc.match(/default: ([^,)]+)/) || [])[1];
  if (m.choices && m.choices.length) {
    const dflt = d && d.trim().replace(/^'(.*)'$/, '$1');
    return m.choices.includes('on') ? 'on' : m.choices.includes(dflt) ? dflt : m.choices[0];
  }
  if (m.takes_value && d && !/^(none|unused|''|"")$/.test(d.trim())) return d.trim().replace(/^'(.*)'$/, '$1');
  return '';
}

function FlagRow({ row, meta, onChange, onRemove }) {
  const m = meta && row.flag ? meta.byName[row.flag.trim()] : null;
  const unknown = meta && row.flag.trim() && !m;
  const noValue = m && !m.takes_value && !m.optional_value;
  const off = row.enabled === false;
  let valueField;
  if (noValue) {
    valueField = html`<span className="flag-none text-caption">No value</span>`;
  } else if (m && m.choices && m.choices.length && !/,/.test(row.value || '') ) {
    const opts = m.choices.map((c) => ({ value: c, label: c }));
    if (m.optional_value) opts.unshift({ value: '', label: '(flag only)' });
    if (row.value && !m.choices.includes(row.value)) opts.push({ value: row.value, label: row.value });
    valueField = html`<${E.Select} label="Value" hideLabel options=${opts} value=${row.value || ''} onChange=${(e) => onChange({ value: e.target.value })} />`;
  } else {
    valueField = html`<${E.TextField} label="Value" hideLabel mono value=${row.value || ''} placeholder=${m ? m.arg : 'value'} onChange=${(e) => onChange({ value: e.target.value })} />`;
  }
  return html`<div className=${'flag-row' + (off ? ' is-off' : '')}>
    <input type="checkbox" className="flag-on" checked=${!off} aria-label=${(off ? 'Pass ' : 'Skip ') + (row.flag || 'this flag') + (off ? ' to llama-server' : '')}
      title=${off ? 'Skipped: kept in the preset but not passed to llama-server' : 'Passed to llama-server'}
      onChange=${(e) => onChange({ enabled: e.target.checked ? undefined : false })} />
    <${E.TextField} label="Flag" hideLabel mono list="kiln-flag-names" value=${row.flag} placeholder="--flag" autoFocus=${!row.flag}
      onChange=${(e) => onChange({ flag: e.target.value.trim() })} />
    ${valueField}
    <${E.IconButton} icon="x" label=${'Remove ' + (row.flag || 'flag')} onClick=${onRemove} />
    ${m ? html`<p className="flag-row__help text-caption">${m.desc}${m.env ? html` <span className="mono">${m.env}</span>` : null}</p>`
      : unknown ? html`<p className="flag-row__help is-warn text-caption"><${E.Icon} name="alert-triangle" size=${14} />Unknown flag. This llama-server build doesn't list it, so it will likely refuse to start.</p>` : null}
  </div>`;
}

/* ------------------------------------------------------------------ import */

function ImportDialog({ onClose, onParsed }) {
  const { fail } = useApp();
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const parse = async () => {
    setBusy(true);
    try {
      const r = await api.post('/api/presets/parse', { command: text });
      onParsed({ name: r.model ? modelLabel(r.model) : '', description: r.dropped.length ? 'Imported. Dropped: ' + r.dropped.join(', ') : 'Imported from a command.', model: r.model, flags: r.flags, env: r.env, role: 'chat' });
    } catch (e) { fail(e, 'Could not read that command'); } finally { setBusy(false); }
  };
  return html`<${E.Dialog} open=${true} onClose=${onClose} size="md" title="Import a llama-server command"
    description="Paste the full command. Kiln keeps the model and flags, and drops --host and --port because it manages those."
    footer=${html`<${E.Button} variant="secondary" onClick=${onClose}>Cancel<//><${E.Button} variant="primary" icon="arrow-right" loading=${busy} disabled=${!text.trim()} onClick=${parse}>Review preset<//>`}>
    <textarea className="textarea text-code" rows="8" autoFocus value=${text} onChange=${(e) => setText(e.target.value)} spellCheck=${false}
      placeholder=${'llama-server -m /models/…/model.gguf \\\n  -ngl 99 -fa on -c 131072 -np 1 --jinja'}></textarea>
  <//>`;
}
