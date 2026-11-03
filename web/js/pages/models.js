/* Models: download from Hugging Face, watch the queue, browse and prune the local library. */
import {
  html, E, useState, useEffect, useMemo, useRef, api, useApp, useFetch, useEvent, useNow,
  fmtInt, fmtBytes, fmtCompact, fmtDuration, fmtWhen, copyText, liveOf,
} from '../lib.js';
import { DRAFT_LABEL } from '../features.js';
import { nestLibrary, libraryRows } from '../library.js';

export function ModelsPage() {
  const library = useFetch('/api/models');
  useEvent('models', () => library.reload());
  return html`<div className="page">
    <header className="page-head">
      <div className="page-head__titles">
        <h1 className="text-headline">Models</h1>
        <p className="text-body">GGUF files under ${library.data ? html`<span className="mono">${library.data.roots.join(', ')}</span>` : 'the models folder'}.</p>
      </div>
    </header>
    <${DownloadCard} onDone=${library.reload} />
    <${QueueCard} />
    <${LibraryCard} data=${library.data} reload=${library.reload} loading=${library.loading} />
  </div>`;
}

/* ------------------------------------------------------------------ Hugging Face */

function DownloadCard() {
  const { fail, toast } = useApp();
  const [id, setId] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [found, setFound] = useState(null);
  const [picked, setPicked] = useState(new Set());
  const listRef = useRef(null);
  useEffect(() => {
    // bring the preselected quant into view; the list is sorted by size
    const box = listRef.current;
    const el = box && box.querySelector('input:checked');
    if (!el) return;
    const r = el.closest('tr').getBoundingClientRect(), b = box.getBoundingClientRect();
    box.scrollTop += r.top - b.top - b.height / 2 + r.height / 2;
  }, [found]);

  const find = async (e) => {
    if (e) e.preventDefault();
    if (!id.trim()) { setError('Enter a model ID, for example unsloth/Qwen3-8B-GGUF.'); return; }
    setBusy(true); setError(null);
    try {
      const r = await api.get('/api/hf/resolve?id=' + encodeURIComponent(id.trim()));
      setFound(r);
      setPicked(new Set(r.selected ? [r.selected] : []));
      if (!r.variants.length) setError('That repository has no GGUF files. Look for a repo whose name ends in -GGUF.');
    } catch (err) { setFound(null); setError(err.message); } finally { setBusy(false); }
  };
  const toggle = (vid) => setPicked((s) => { const n = new Set(s); n.has(vid) ? n.delete(vid) : n.add(vid); return n; });
  const chosen = found ? found.variants.filter((v) => picked.has(v.id)) : [];
  const total = chosen.reduce((a, v) => a + v.size, 0);

  const download = async () => {
    try {
      for (const v of chosen) {
        await api.post('/api/downloads', { repo: found.repo, revision: found.revision, variant: v.name, files: v.files });
      }
      toast({ tone: 'success', title: chosen.length === 1 ? `Queued ${chosen[0].name}` : `Queued ${chosen.length} downloads` });
      setPicked(new Set());
    } catch (err) { fail(err, 'Could not queue the download'); }
  };

  const columns = [
    { key: 'pick', header: html`<span className="em-sr-only">Select</span>`, width: 44, render: (v) => html`<span className="variant-pick">
      <input type="checkbox" checked=${picked.has(v.id)} onChange=${() => toggle(v.id)} aria-label=${'Select ' + v.name} /></span>` },
    { key: 'name', header: 'File', render: (v) => html`<div className="cell-main"><span className="mono">${v.name}</span>
      ${v.files.length > 1 && html`<span className="cell-sub text-caption">${v.files.length} parts</span>`}</div>` },
    { key: 'quant', header: 'Quant', render: (v) => (v.kind === 'projector' ? html`<${E.Badge} size="sm" tone="info">Vision projector<//>`
      : v.kind === 'draft' ? html`<${E.Badge} size="sm" tone="info">Draft model<//>` : v.quant || '–') },
    { key: 'size', header: 'Size', numeric: true, sortable: true, render: (v) => fmtBytes(v.size) },
    { key: 'downloaded', header: '', align: 'end', render: (v) => (v.downloaded ? html`<${E.Badge} size="sm" tone="success" icon="check-circle">On disk<//>` : null) },
  ];

  return html`<${E.Card} title="Download from Hugging Face" description="Paste an ID, an ID with a quant tag, or a huggingface.co link.">
    <div className="stack">
      <form className="filters" onSubmit=${find}>
        <${E.TextField} label="Model ID" hideLabel icon="search" mono value=${id} error=${error}
          onChange=${(e) => { setId(e.target.value); setError(null); }} placeholder="unsloth/Qwen3-8B-GGUF:Q4_K_M" style=${{ minWidth: 0 }} className="grow" />
        <${E.Button} type="submit" variant=${found ? 'secondary' : 'primary'} icon="search" loading=${busy}>Find files<//>
      </form>
      ${found && found.variants.length > 0 && html`
        <div className="row text-caption muted">
          <a href=${'https://huggingface.co/' + found.repo} target="_blank" rel="noopener" className="mono">${found.repo}</a>
          ${found.downloads != null && html`<span>· ${fmtCompact(found.downloads)} downloads</span>`}
          ${found.likes != null && html`<span>· ${fmtCompact(found.likes)} likes</span>`}
          ${found.gated && html`<${E.Badge} size="sm" tone="warning" icon="alert-triangle">Gated<//>`}
          ${found.gated && !found.has_token && html`<span>Add a Hugging Face token in Settings to download it.</span>`}
        </div>
        <div className="table-scroll" style=${{ maxHeight: 360 }} ref=${listRef}>
          <${E.Table} caption="GGUF files" density="compact" columns=${columns} rows=${found.variants} rowKey=${(v) => v.id} stickyHeader />
        </div>
        <div className="row row--between">
          <span className="text-body muted">${chosen.length ? `${chosen.length} selected · ${fmtBytes(total)}` : 'Select a file to download.'}</span>
          <${E.Button} variant="primary" icon="download" disabled=${!chosen.length} onClick=${download}>${chosen.length ? `Download ${fmtBytes(total)}` : 'Download'}<//>
        </div>`}
    </div>
  <//>`;
}

function QueueCard() {
  const { fail } = useApp();
  const list = useFetch('/api/downloads');
  const [rows, setRows] = useState([]);
  useEffect(() => { if (list.data) setRows(list.data); }, [list.data]);
  useEvent('download', (d) => setRows((rs) => {
    if (d.removed) return rs.filter((r) => r.id !== d.id);
    const i = rs.findIndex((r) => r.id === d.id);
    if (i < 0) return [d, ...rs];
    const next = rs.slice(); next[i] = { ...rs[i], ...d }; return next;
  }));
  const visible = rows.filter((r) => r.status !== 'cancelled').slice(0, 12);
  if (!visible.length) return null;
  const call = (fn) => fn().catch((e) => fail(e));
  return html`<${E.Card} title="Downloads">
    ${visible.map((d) => html`<div key=${d.id} className="dl-item">
      <div className="dl-item__head">
        <div className="cell-main"><span className="mono truncate">${d.variant}</span><span className="cell-sub text-caption">${d.repo}</span></div>
        <div className="row" style=${{ flexWrap: 'nowrap' }}>
          ${d.status === 'done' && html`<${E.Badge} tone="success" icon="check-circle">Done<//>`}
          ${d.status === 'failed' && html`<${E.Badge} tone="danger" icon="alert-circle">Failed<//>`}
          ${d.status === 'queued' && html`<${E.Badge} tone="neutral" icon="clock">Queued<//>`}
          ${(d.status === 'downloading' || d.status === 'queued') && html`<${E.Button} variant="ghost" size="sm" icon="x" onClick=${() => call(() => api.post(`/api/downloads/${d.id}/cancel`))}>Cancel<//>`}
          ${d.status === 'failed' && html`<${E.Button} variant="secondary" size="sm" icon="refresh" onClick=${() => call(() => api.post(`/api/downloads/${d.id}/retry`))}>Retry<//>`}
          ${(d.status === 'done' || d.status === 'failed') && html`<${E.IconButton} icon="x" size="sm" label="Remove from list" onClick=${() => call(() => api.del(`/api/downloads/${d.id}`))} />`}
        </div>
      </div>
      ${d.status === 'downloading' && html`<${E.Progress} value=${d.done_bytes} max=${d.total_bytes}
        valueText=${`${fmtBytes(d.done_bytes)} of ${fmtBytes(d.total_bytes)}${d.speed ? ' · ' + fmtBytes(d.speed) + '/s' : ''}${d.eta_s != null ? ' · ' + fmtDuration(d.eta_s) + ' left' : ''}`} />`}
      ${d.status === 'failed' && html`<p className="text-caption" style=${{ color: 'var(--danger)' }}>${d.error}</p>`}
      ${d.status === 'done' && html`<p className="text-caption subtle">${fmtBytes(d.total_bytes)} saved to <span className="mono">${d.dest}</span></p>`}
    </div>`)}
  <//>`;
}

/* ------------------------------------------------------------------ library */

function decisionNote(d) {
  if (d.kind === 'pplx-v3') return 'Perplexity decider (protocol v3). Kiln scores it through llama-server; stock llama-server cannot.';
  if (d.kind === 'native') return 'llama-server serves this one itself at /v1/systemone.';
  return 'Decision protocol ' + d.protocol + ' is not supported.';
}

const LIBRARY_GROUPED_KEY = 'kiln-library-grouped';

function LibraryCard({ data, reload, loading }) {
  const { go, toast, fail, status } = useApp();
  const [deleting, setDeleting] = useState(null);
  const [filter, setFilter] = useState('');
  const [sort, setSort] = useState({ key: 'mtime', direction: 'desc' });
  const [expanded, setExpanded] = useState(() => new Set());
  const [grouped, setGrouped] = useState(() => { try { return localStorage.getItem(LIBRARY_GROUPED_KEY) !== 'false'; } catch { return true; } });
  useEffect(() => { try { localStorage.setItem(LIBRARY_GROUPED_KEY, String(grouped)); } catch {} }, [grouped]);
  const toggle = (path) => setExpanded((s) => { const n = new Set(s); n.has(path) ? n.delete(path) : n.add(path); return n; });
  const now = useNow(60000);
  const models = data ? data.models : [];
  const rows = useMemo(() =>
    libraryRows(grouped ? nestLibrary(models) : models.map((m) => ({ ...m, children: [] })), { sort, filter, expanded }),
    [models, grouped, sort, filter, expanded]);
  const total = models.reduce((a, m) => a + m.size, 0);
  const nCompanions = models.filter((m) => m.is_projector || m.is_draft).length;
  const description = !data ? 'Scanning…' : grouped
    ? `${fmtInt(models.length - nCompanions)} models · ${fmtInt(nCompanions)} companion files · ${fmtBytes(total)}`
    : `${fmtInt(models.length)} files · ${fmtBytes(total)}`;
  const loadedPaths = new Set(liveOf(status).map((i) => i.model_path));

  const remove = async () => {
    try { const r = await api.del('/api/models?path=' + encodeURIComponent(deleting.path)); toast({ title: `Deleted ${deleting.name}`, description: `${r.removed.length} file${r.removed.length === 1 ? '' : 's'} removed.` }); setDeleting(null); reload(); }
    catch (e) { fail(e, 'Could not delete the model'); }
  };

  const columns = [
    { key: 'name', header: 'Model', sortable: true, render: (r) => html`<div className=${r.depth ? 'lib-name lib-child' : 'lib-name'}>
      ${r.depth === 0 && r.childCount > 0 && html`<${E.IconButton} icon=${r.open ? 'chevron-down' : 'chevron-right'} size="sm"
        label=${(r.open ? 'Hide' : 'Show') + ` the files that go with ${r.name}`} aria-expanded=${r.open} onClick=${() => toggle(r.path)} />`}
      ${r.depth === 0 && r.childCount === 0 && html`<span className="lib-toggle-space" aria-hidden="true"></span>`}
      <div className="cell-main">
        <span className="row">${r.name}${loadedPaths.has(r.path) && html`<${E.Badge} size="sm" tone="success" live>Loaded<//>`}
          ${r.is_projector && html`<${E.Badge} size="sm" tone="info">Vision projector<//>`}
          ${r.is_draft && html`<${E.Badge} size="sm" tone="info">${DRAFT_LABEL[r.draft.type] || 'Draft'} draft<//>`}
          ${r.decision && html`<${E.Badge} size="sm" tone="ember" title=${decisionNote(r.decision)}>Decision model<//>`}
          ${!r.is_projector && !r.is_draft && r.role_hint === 'embedding' && html`<${E.Badge} size="sm" tone="neutral">Embeddings<//>`}
          ${!r.is_projector && !r.is_draft && r.role_hint === 'rerank' && html`<${E.Badge} size="sm" tone="neutral">Reranker<//>`}
          ${r.nextn_layers > 0 && html`<${E.Badge} size="sm" tone="neutral" title="Has a multi-token prediction head">MTP<//>`}
          ${!r.complete && html`<${E.Badge} size="sm" tone="warning" icon="alert-triangle">Missing parts<//>`}</span>
        <span className="cell-sub text-caption">${[r.repo || r.path.split('/').slice(-2, -1)[0],
          ...(r.projectors && r.projectors.length ? ['+ vision projector'] : []),
          ...((r.drafts || []).map((d) => `+ ${DRAFT_LABEL[d.type] || 'draft'} file`))].join(' · ')}</span>
      </div>
    </div>` },
    { key: 'quant', header: 'Quant', sortable: true },
    { key: 'arch', header: 'Arch', sortable: true, render: (m) => [m.arch, m.size_label].filter(Boolean).join(' · ') || '–' },
    { key: 'context_length', header: 'Trained context', numeric: true, sortable: true, render: (m) => fmtInt(m.context_length) },
    { key: 'size', header: 'Size', numeric: true, sortable: true, render: (m) => fmtBytes(m.size) },
    { key: 'mtime', header: 'Added', sortable: true, render: (m) => html`<span className="nowrap">${fmtWhen(m.mtime, now)}</span>` },
    { key: 'actions', header: '', align: 'end', render: (m) => html`<div className="row" style=${{ justifyContent: 'flex-end', flexWrap: 'nowrap' }}>
      <${E.IconButton} icon="copy" size="sm" label="Copy path" onClick=${() => copyText(m.path).then(() => toast({ title: 'Path copied' }))} />
      ${!m.is_projector && !m.is_draft && html`<${E.IconButton} icon="plus" size="sm" label="Create a preset" onClick=${() => go('presets', 'new/' + encodeURIComponent(m.path))} />`}
      <${E.IconButton} icon="trash" size="sm" label=${'Delete ' + m.name} onClick=${() => setDeleting(m)} />
    </div>` },
  ];
  return html`<${E.Card} title="Library" description=${description} padding="none"
    actions=${html`<div className="card-actions">
      <${E.SegmentedControl} size="sm" label="Library view" value=${grouped ? 'grouped' : 'flat'} onChange=${(v) => setGrouped(v === 'grouped')}
        options=${[{ value: 'grouped', label: 'Grouped' }, { value: 'flat', label: 'All files' }]} />
      <${E.TextField} label="Filter models" hideLabel icon="search" size="sm" placeholder="Filter" value=${filter} onChange=${(e) => setFilter(e.target.value)} />
      <${E.IconButton} icon="refresh" size="sm" label="Rescan" onClick=${reload} disabled=${loading} />
    </div>`}>
    <${E.Table} caption="Model library" columns=${columns} rows=${rows} rowKey=${(m) => m.path} sort=${sort} onSortChange=${setSort}
      empty=${data ? 'No GGUF files yet. Downloads land here when they finish.' : 'Scanning…'} />
    <${E.Dialog} open=${!!deleting} onClose=${() => setDeleting(null)} size="sm" title=${deleting ? `Delete ${deleting.name}?` : ''}
      description=${deleting ? `This removes ${fmtBytes(deleting.size)} from disk${deleting.files > 1 ? ` across ${deleting.files} files` : ''}. Presets that use it will fail to start until you download it again.` : ''}
      footer=${html`<${E.Button} variant="secondary" onClick=${() => setDeleting(null)}>Cancel<//><${E.Button} variant="danger" icon="trash" onClick=${remove}>Delete model<//>`} />
  <//>`;
}
