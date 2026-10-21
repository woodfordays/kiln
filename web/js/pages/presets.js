/* Presets: saved model + flags + env, with an editor that knows every llama-server flag. */
import {
  html, E, useState, useEffect, useMemo, api, useApp, useFetch, useEvent, useNow,
  fmtWhen, fmtBytes, copyText, commandLine, modelLabel,
} from '../lib.js';

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
const DEFAULT_FLAGS = [{ flag: '-ngl', value: '99' }, { flag: '-fa', value: 'on' }, { flag: '-c', value: '16384' }, { flag: '-np', value: '1' }, { flag: '--jinja', value: null }];

export function PresetsPage({ arg }) {
  const { toast, fail, go, status, refreshStatus } = useApp();
  const presets = useFetch('/api/presets');
  const settings = useFetch('/api/settings');
  useEvent('presets', () => presets.reload());
  const [editing, setEditing] = useState(null);
  const [importing, setImporting] = useState(false);
  const [deleting, setDeleting] = useState(null);
  const now = useNow(60000);

  useEffect(() => {
    if (arg && arg.startsWith('new')) {
      const model = arg.length > 4 ? decodeURIComponent(arg.slice(4)) : '';
      setEditing({ name: model ? modelLabel(model) : '', description: '', model, flags: DEFAULT_FLAGS, env: {} });
      history.replaceState(null, '', '#/presets');
    }
  }, [arg]);

  const start = async (p) => {
    try {
      await api.post('/api/server/start', { preset_id: p.id, replace: true });
      toast({ tone: 'success', title: `Starting ${p.name}` });
      refreshStatus();
      go('server');
    } catch (e) { fail(e, 'Could not start the preset'); }
  };
  const duplicate = async (p) => {
    try { const n = await api.post(`/api/presets/${p.id}/duplicate`); toast({ title: `Created ${n.name}` }); presets.reload(); } catch (e) { fail(e); }
  };
  const remove = async () => {
    try { await api.del(`/api/presets/${deleting.id}`); toast({ title: `Deleted ${deleting.name}` }); setDeleting(null); presets.reload(); } catch (e) { fail(e); }
  };
  const autostart = settings.data ? settings.data.autostart_preset_id : null;
  const running = status && status.preset && (status.state === 'ready' || status.state === 'loading') ? status.preset.id : null;

  const columns = [
    { key: 'name', header: 'Preset', sortable: true, render: (p) => html`<div className="cell-main">
      <span className="row">${p.name}
        ${p.id === running && html`<${E.Badge} size="sm" tone="success" live>Running<//>`}
        ${p.id === autostart && html`<${E.Badge} size="sm" tone="ember">Autostart<//>`}</span>
      ${p.description && html`<span className="cell-sub text-caption">${p.description}</span>`}
    </div>` },
    { key: 'model', header: 'Model', render: (p) => html`<span className="truncate" style=${{ maxWidth: 240, display: 'inline-block' }} title=${p.model}>${modelLabel(p.model) || html`<span className="subtle">None</span>`}</span>` },
    { key: 'flags', header: 'Flags', mono: true, render: (p) => html`<span className="truncate subtle" style=${{ maxWidth: 360, display: 'inline-block' }} title=${commandLine('', p.flags, p.env)}>${commandLine('', p.flags, p.env).replace(/^llama-server ?/, '') || '–'}</span>` },
    { key: 'last_used', header: 'Last used', sortable: true, sortValue: (p) => p.last_used || 0, render: (p) => (p.last_used ? fmtWhen(p.last_used, now) : html`<span className="subtle">Never</span>`) },
    { key: 'actions', header: '', align: 'end', render: (p) => html`<div className="row" style=${{ justifyContent: 'flex-end', flexWrap: 'nowrap' }}>
      <${E.IconButton} icon="play" label=${'Start ' + p.name} size="sm" onClick=${() => start(p)} />
      <${E.IconButton} icon="edit" label=${'Edit ' + p.name} size="sm" onClick=${() => setEditing(p)} />
      <${E.IconButton} icon="copy" label=${'Duplicate ' + p.name} size="sm" onClick=${() => duplicate(p)} />
      <${E.IconButton} icon="trash" label=${'Delete ' + p.name} size="sm" onClick=${() => setDeleting(p)} />
    </div>` },
  ];

  return html`<div className="page">
    <header className="page-head">
      <div className="page-head__titles">
        <h1 className="text-headline">Presets</h1>
        <p className="text-body">A model plus the flags and environment to run it with.</p>
      </div>
      <div className="page-head__actions">
        <${E.Button} variant="secondary" icon="upload" onClick=${() => setImporting(true)}>Import command<//>
        <${E.Button} variant="primary" icon="plus" onClick=${() => setEditing({ name: '', description: '', model: '', flags: DEFAULT_FLAGS, env: {} })}>New preset<//>
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
  </div>`;
}

/* ------------------------------------------------------------------ editor */

let rowSeq = 0;
const withIds = (flags) => (flags || []).map((f) => ({ ...f, _id: ++rowSeq }));
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
  const [name, setName] = useState(preset.name || '');
  const [description, setDescription] = useState(preset.description || '');
  const [model, setModel] = useState(preset.model || '');
  const [rows, setRows] = useState(withIds(preset.flags));
  const [envText, setEnvText] = useState(envToText(preset.env));
  const [saving, setSaving] = useState(false);
  const [nameError, setNameError] = useState(null);

  const library = models.data ? models.data.models : [];
  const known = library.some((m) => m.path === model);
  const [customPath, setCustomPath] = useState(!!preset.model && models.data && !known);
  useEffect(() => { if (models.data && preset.model && !models.data.models.some((m) => m.path === preset.model)) setCustomPath(true); }, [models.data]);
  const current = library.find((m) => m.path === model);
  const modelOpts = [
    ...library.filter((m) => !m.is_projector).map((m) => ({ value: m.path, label: `${m.name} · ${fmtBytes(m.size)}${m.repo ? ' · ' + m.repo : ''}` })),
    { value: '__custom', label: 'Another path…' },
  ];

  const setRow = (id, patch) => setRows((rs) => rs.map((r) => (r._id === id ? { ...r, ...patch } : r)));
  const addFlag = (flag, value = '') => setRows((rs) => [...rs, { _id: ++rowSeq, flag, value: defaultValue(meta, flag, value) }]);
  const present = new Set(rows.map((r) => r.flag));
  const isPresent = (f) => { const m = meta && meta.byName[f]; return m ? m.names.some((n) => present.has(n)) : present.has(f); };

  const env = textToEnv(envText);
  const flags = rows.filter((r) => r.flag.trim()).map((r) => ({ flag: r.flag.trim(), value: r.value === '' ? null : r.value }));
  const preview = commandLine(model, flags, env);

  const save = async () => {
    if (!name.trim()) { setNameError('Give the preset a name.'); return; }
    setSaving(true);
    try {
      const body = { name, description, model, flags, env };
      const saved = preset.id ? await api.put(`/api/presets/${preset.id}`, body) : await api.post('/api/presets', body);
      onSaved(saved);
    } catch (e) {
      if (/already exists/.test(e.message)) setNameError(e.message); else fail(e, 'Could not save the preset');
    } finally { setSaving(false); }
  };

  const projectors = current && current.projectors ? current.projectors : [];
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
          hint=${current ? [current.arch, current.size_label, current.quant, current.context_length ? 'trained to ' + current.context_length.toLocaleString('en-US') + ' tokens' : null, current.nextn_layers ? 'has an MTP head' : null].filter(Boolean).join(' · ') : undefined} />`}

      <hr className="divider" />
      <div className="row row--between">
        <h3 className="section-title text-subtitle">Flags</h3>
        <span className="text-caption subtle">${meta ? `${meta.flags.length} flags known from ${meta.version || 'llama-server'}` : 'Reading llama-server --help…'}</span>
      </div>
      <datalist id="kiln-flag-names">${meta && meta.names.map((n) => html`<option key=${n} value=${n}>${(meta.byName[n].desc || '').slice(0, 80)}</option>`)}</datalist>
      <div className="flags">
        ${rows.map((r) => html`<${FlagRow} key=${r._id} row=${r} meta=${meta} onChange=${(p) => setRow(r._id, p)} onRemove=${() => setRows((rs) => rs.filter((x) => x._id !== r._id))} />`)}
      </div>
      <div className="chips">
        <${E.Button} variant="secondary" size="sm" icon="plus" onClick=${() => addFlag('')}>Add flag<//>
        ${QUICK.filter((f) => !isPresent(f) && (!meta || meta.byName[f])).map((f) => html`<${E.Button} key=${f} variant="ghost" size="sm" onClick=${() => addFlag(f)}><span className="mono">${f}</span><//>`)}
        ${projectors.length > 0 && !isPresent('--mmproj') && html`<${E.Button} variant="ghost" size="sm" icon="plus" onClick=${() => addFlag('--mmproj', projectors[0])}>Vision projector<//>`}
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
  return html`<div className="flag-row">
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
      onParsed({ name: r.model ? modelLabel(r.model) : '', description: r.dropped.length ? 'Imported. Dropped: ' + r.dropped.join(', ') : 'Imported from a command.', model: r.model, flags: r.flags, env: r.env });
    } catch (e) { fail(e, 'Could not read that command'); } finally { setBusy(false); }
  };
  return html`<${E.Dialog} open=${true} onClose=${onClose} size="md" title="Import a llama-server command"
    description="Paste the full command. Kiln keeps the model and flags, and drops --host and --port because it manages those."
    footer=${html`<${E.Button} variant="secondary" onClick=${onClose}>Cancel<//><${E.Button} variant="primary" icon="arrow-right" loading=${busy} disabled=${!text.trim()} onClick=${parse}>Review preset<//>`}>
    <textarea className="textarea text-code" rows="8" autoFocus value=${text} onChange=${(e) => setText(e.target.value)} spellCheck=${false}
      placeholder=${'llama-server -m /models/…/model.gguf \\\n  -ngl 99 -fa on -c 131072 -np 1 --jinja'}></textarea>
  <//>`;
}
