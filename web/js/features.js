/* Per-model features that live in a preset's flags: the vision projector, speculative decoding
   (MTP built into the model, or a draft file such as DSpark) and the unified KV cache. Pure functions
   over flag rows ({flag, value, enabled}); a row with enabled:false stays in the preset but is not
   passed to llama-server, so a feature can be switched off and back on without losing its settings. */

const SPEC = ['--spec-type'];
const DRAFT = ['-md', '--spec-draft-model', '--model-draft'];
const NMAX = ['--spec-draft-n-max'];
const MMPROJ = ['--mmproj', '-mm'];
const KVU_ON = ['--kv-unified', '-kvu'];
const KVU_OFF = ['--no-kv-unified', '-no-kvu'];
const SLOTS = ['-np', '--parallel'];
const AUTO_SLOTS = '4'; // what llama-server uses while -np is auto
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

const named = (row, names) => names.includes((row.flag || '').trim());

/* llama-server shares one KV pool between the slots when asked to, and always while -np is auto
   (it ignores --no-kv-unified then). With a fixed -np the default is a separate cache per slot, -c
   split evenly. slots is null for auto. The last flag wins, as on the command line. */
function readKv(rows) {
  const np = find(rows, SLOTS);
  const slots = isOn(np) && Number(np.value) > 0 ? Number(np.value) : null;
  let asked = false;
  for (const r of rows) {
    if (!isOn(r)) continue;
    if (named(r, KVU_ON)) asked = true;
    else if (named(r, KVU_OFF)) asked = false;
  }
  return { on: slots === null || asked, forced: slots === null, slots, present: rows.some((r) => named(r, KVU_ON) || named(r, KVU_OFF)) };
}

/* One sentence for the switch and the chip hint. */
export function kvUnifiedNote(kv) {
  if (kv.forced) return `All slots share one KV pool, which llama-server always does while -np is auto. Turning it off sets -np ${AUTO_SLOTS}.`;
  if (kv.slots === 1) return kv.on
    ? 'All slots share one KV pool (--kv-unified). With a single slot there is nothing to share, so it changes nothing.'
    : 'Each slot has its own KV cache, llama-server\'s default with -np set. With a single slot it makes no difference.';
  return kv.on
    ? `The ${kv.slots} slots share one KV pool, so one long request can use most of -c (--kv-unified).`
    : `-c is split evenly between the ${kv.slots} slots, and a slot can't borrow room from another.`;
}

export function readFeatures(rows) {
  const mm = find(rows, MMPROJ);
  const spec = find(rows, SPEC);
  const md = find(rows, DRAFT);
  const types = isOn(spec) ? String(spec.value || '').split(',').map((t) => t.trim()).filter(Boolean) : [];
  const type = types.find((t) => t.startsWith('draft-')) || '';
  return {
    kv: readKv(rows),
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

/* On adds --kv-unified and skips any --no-kv-unified. Off skips --kv-unified; while -np is auto that is
   not enough (llama-server forces the pool on), so it also pins -np to the slot count auto would use,
   keeping a number the preset had before. */
export function setKvUnified(rows, on) {
  const out = rows.map((r) => ({ ...r }));
  const ask = out.filter((r) => named(r, KVU_ON));
  if (on) {
    out.filter((r) => named(r, KVU_OFF)).forEach((r) => { r.enabled = false; });
    if (ask.length) ask.forEach(enable); else out.push({ flag: '--kv-unified', value: null });
    return out;
  }
  ask.forEach((r) => { r.enabled = false; });
  if (readKv(out).forced) {
    const np = find(out, SLOTS);
    if (np && Number(np.value) > 0) enable(np); else upsert(out, SLOTS, '-np', AUTO_SLOTS);
  }
  return out;
}

/* The switches to show for a preset: only what the model can do, or what the preset already uses.
   model: a library entry ({projectors, drafts, nextn_layers, kv_cache}) or undefined.
   opts.noVision: the role ignores images (a Perplexity decider scored by Kiln), so no switch.
   opts.noKvUnified: Kiln runs it on one slot itself, so no switch. */
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
  if (!opts.noKvUnified && ((model && model.kv_cache) || f.kv.present)) {
    chips.push({ id: 'kvu', label: 'KV unified', on: f.kv.on, hint: kvUnifiedNote(f.kv).replace(/\.$/, '') });
  }
  return chips;
}

/* Flip one chip. Returns the new rows. */
export function toggleChip(model, rows, chip) {
  if (chip.id === 'vision') {
    const projectors = (model && model.projectors) || [];
    return setVision(rows, !chip.on, projectors[0]);
  }
  if (chip.id === 'kvu') return setKvUnified(rows, !chip.on);
  if (chip.on) return setSpec(rows, 'off');
  if (chip.id === 'mtp') return setSpec(rows, 'mtp');
  return setSpec(rows, 'draft', chip.draft);
}
