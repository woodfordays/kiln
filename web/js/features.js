/* Per-model features that live in a preset's flags: the vision projector and speculative decoding
   (MTP built into the model, or a draft file such as DSpark). Pure functions over flag rows
   ({flag, value, enabled}); a row with enabled:false stays in the preset but is not passed to
   llama-server, so a feature can be switched off and back on without losing its settings. */

const SPEC = ['--spec-type'];
const DRAFT = ['-md', '--spec-draft-model', '--model-draft'];
const NMAX = ['--spec-draft-n-max'];
const MMPROJ = ['--mmproj', '-mm'];
const DEFAULT_NMAX = new Set(['3', '7', '15']);

export const DRAFT_LABEL = {
  'draft-mtp': 'MTP', 'draft-dspark': 'DSpark', 'draft-dflash': 'DFlash', 'draft-eagle3': 'EAGLE-3', 'draft-simple': 'Draft model',
};

const find = (rows, names) => rows.find((r) => names.includes((r.flag || '').trim()));
export const isOn = (row) => !!row && row.enabled !== false;
const enable = (row) => { delete row.enabled; };

/* How many tokens to draft by default. Defaults come from llama.cpp's speculative decoding docs:
   DSpark examples use 7, DFlash drafts a whole block (clamped to the draft's trained block size). */
export function defaultNMax(type, draft) {
  if (type === 'draft-dflash') return String((draft && draft.block_size) || 15);
  if (type === 'draft-dspark') return '7';
  return '3';
}

export function readFeatures(rows) {
  const mm = find(rows, MMPROJ);
  const spec = find(rows, SPEC);
  const md = find(rows, DRAFT);
  const types = isOn(spec) ? String(spec.value || '').split(',').map((t) => t.trim()).filter(Boolean) : [];
  const type = types.find((t) => t.startsWith('draft-')) || '';
  return {
    vision: { on: isOn(mm) && !!mm.value, path: mm ? mm.value || '' : '', present: !!mm },
    spec: {
      type,
      mode: !type ? 'off' : type === 'draft-mtp' ? 'mtp' : 'draft',
      draftPath: isOn(md) ? md.value || '' : '',
      nmax: find(rows, NMAX) && isOn(find(rows, NMAX)) ? find(rows, NMAX).value : null,
      others: types.filter((t) => !t.startsWith('draft-')),
    },
  };
}

export function setVision(rows, on, path) {
  const out = rows.map((r) => ({ ...r }));
  const row = find(out, MMPROJ);
  if (!on) {
    if (row) row.enabled = false;
  } else if (row) {
    enable(row);
    if (path) row.value = path;
  } else if (path) {
    out.push({ flag: '--mmproj', value: path });
  }
  return out;
}

function upsert(rows, names, flag, value) {
  const row = find(rows, names);
  if (row) { row.value = value; enable(row); } else rows.push({ flag, value });
}

/* mode: 'off' | 'mtp' | 'draft' (with the draft file as {path, type, block_size}). */
export function setSpec(rows, mode, draft) {
  const out = rows.map((r) => ({ ...r }));
  const spec = find(out, SPEC);
  const md = find(out, DRAFT);
  const nmax = find(out, NMAX);
  const was = readFeatures(rows).spec;
  const keep = was.others; // n-gram types can ride along; they are not what this switch is about
  if (mode === 'off') {
    if (spec) {
      if (keep.length) { spec.value = keep.join(','); enable(spec); } else spec.enabled = false;
    }
    if (md) md.enabled = false;
    if (nmax) nmax.enabled = false;
    return out;
  }
  const type = mode === 'mtp' ? 'draft-mtp' : draft.type;
  upsert(out, SPEC, '--spec-type', [type, ...keep].join(','));
  if (mode === 'mtp') {
    if (md) md.enabled = false;
  } else {
    upsert(out, DRAFT, '-md', draft.path);
  }
  // Keep a number the user chose; replace one that is just another mode's default.
  const wanted = defaultNMax(type, draft);
  const n = find(out, NMAX);
  if (!n) out.push({ flag: '--spec-draft-n-max', value: wanted });
  else {
    const cur = n.value == null ? '' : String(n.value);
    if (!cur || DEFAULT_NMAX.has(cur)) n.value = wanted;
    enable(n);
  }
  return out;
}

/* The switches to show for a preset: only what the model can do, or what the preset already uses.
   model: a library entry ({projectors, drafts, nextn_layers}) or undefined.
   opts.noVision: the role ignores images (a Perplexity decider scored by Kiln), so no switch. */
export function chipsFor(model, rows, opts = {}) {
  const f = readFeatures(rows);
  const chips = [];
  const projectors = (model && model.projectors) || [];
  if (!opts.noVision && (projectors.length || f.vision.present)) {
    chips.push({ id: 'vision', label: 'Vision', on: f.vision.on, hint: f.vision.on ? 'Images are enabled' : 'Images are off' });
  }
  if ((model && model.nextn_layers) || f.spec.type === 'draft-mtp') {
    chips.push({ id: 'mtp', label: 'MTP', on: f.spec.mode === 'mtp', hint: 'Multi-token prediction head built into the model' });
  }
  for (const d of (model && model.drafts) || []) {
    const on = f.spec.mode === 'draft' && f.spec.type === d.type && (!f.spec.draftPath || f.spec.draftPath === d.path);
    chips.push({ id: 'draft:' + d.path, label: DRAFT_LABEL[d.type] || 'Draft', on, draft: d, hint: d.name });
  }
  return chips;
}

/* Flip one chip. Returns the new rows. */
export function toggleChip(model, rows, chip) {
  if (chip.id === 'vision') {
    const projectors = (model && model.projectors) || [];
    return setVision(rows, !chip.on, projectors[0]);
  }
  if (chip.on) return setSpec(rows, 'off');
  if (chip.id === 'mtp') return setSpec(rows, 'mtp');
  return setSpec(rows, 'draft', chip.draft);
}
