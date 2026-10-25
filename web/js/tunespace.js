/* The tuner's search space: every setting the tuner can search, with its flag, the values it takes and
   what auto does, plus the text a user types for a list ("256..2048 x2, 3000"). Pure functions. The page
   sends toSpace(state) as the request's `space`, and shows countCandidates() and estimateMinutes() under
   the form. The counts mirror the search in app/kiln/tuner.py, so keep the two in step. */

import { draftLengths } from './features.js';

const ALL_MODES = ['auto', 'fixed', 'list', 'off'];
const SPEC_MODES = ['auto', 'off'];
const MAX_VALUES = 32;
const UBATCHES = ['256', '512', '1024', '2048']; // the micro-batch ladder (UBATCHES in tuner.py)
const MTP_LENGTHS = 5; // MTP draft lengths 1 to 5 (MTP_NMAX in tuner.py)
const KV_AUTO = ['f16', 'q8_0', 'q4_0']; // K or V values while the cache type is auto
const KV_TYPES = ['f32', 'f16', 'bf16', 'q8_0', 'q5_1', 'q5_0', 'iq4_nl', 'q4_1', 'q4_0'];
const ON_OFF = ['on', 'off'];
const MEM = 'Memory and offload';
const KVG = 'KV cache';
const BATCH = 'Batching and CPU';
const SPECG = 'Speculative decoding';

const intDim = (key, label, group, min, max, flag, auto, off = null) =>
  ({ key, label, group, kind: 'int', min, max, choices: null, flag, auto, off, modes: ALL_MODES });
const choiceDim = (key, label, group, choices, flag, auto, off = null) =>
  ({ key, label, group, kind: 'choice', min: null, max: null, choices, flag, auto, off, modes: ALL_MODES });
const textDim = (key, label, group, pattern, flag, auto) =>
  ({ key, label, group, kind: 'text', min: null, max: null, choices: null, pattern, flag, auto, off: null, modes: ALL_MODES });

/* Every dimension, in the order of the spec's table. `flag` is the short display flag; `auto` is what
   auto does; `off` is the value off holds (null: the flag is not passed). A text dimension's values are
   separated by ';' or new lines, since a value can hold commas (tensor_split). */
export const DIMS = [
  intDim('ctx', 'Context', MEM, 512, 1048576, '-c', 'The target context.'),
  intDim('ngl', 'GPU layers', MEM, 0, 999, '-ngl', 'Tries 99 and auto (--fit on). The context objective bisects the spill instead.', '99'),
  intDim('ncmoe', 'Experts in RAM', MEM, 0, 999, '--n-cpu-moe', 'Not used. The context objective bisects it for MoE models.'),
  choiceDim('ctk', 'K cache', KVG, KV_TYPES, '-ctk', 'The KV ladder (f16, q8_0, q4_0 pairs), as today.', 'f16'),
  choiceDim('ctv', 'V cache', KVG, KV_TYPES, '-ctv', 'The KV ladder (f16, q8_0, q4_0 pairs), as today.', 'f16'),
  choiceDim('fa', 'Flash attention', KVG, ['on', 'off', 'auto'], '-fa', 'Today: on, off and auto in the grid when V is f16, else on.', 'on'),
  intDim('ub', 'Micro-batch', BATCH, 1, 65536, '-ub', `The micro-batch ladder: ${UBATCHES.join(', ')}.`, '512'),
  intDim('b', 'Logical batch', BATCH, 1, 65536, '-b', 'Today: max(2048, micro-batch), plus micro-batch and 4096 with vary batch.', '2048'),
  intDim('np', 'Parallel slots', KVG, 1, 256, '-np', 'Today: 1.', '1'),
  choiceDim('kvu', 'Unified KV', KVG, ON_OFF, '--kv-unified', 'Not passed.'),
  intDim('threads', 'Threads', BATCH, 1, 1024, '-t', 'Context objective: today\'s thread sweep. Other objectives: the preset\'s -t is kept.'),
  intDim('threads_batch', 'Batch threads', BATCH, 1, 1024, '-tb', 'Context objective: today\'s thread sweep. Other objectives: the preset\'s -tb is kept.'),
  choiceDim('load_mode', 'Load mode', BATCH, ['auto', 'none', 'mmap', 'mlock', 'mmap+mlock', 'dio'], '-lm', 'Not passed.'),
  choiceDim('numa', 'NUMA', BATCH, ['none', 'distribute', 'isolate', 'numactl'], '--numa', 'Not passed.'),
  choiceDim('split_mode', 'Split mode', MEM, ['none', 'layer', 'row', 'tensor'], '-sm', 'Not passed.'),
  textDim('tensor_split', 'Tensor split', MEM, /^\d+(\.\d+)?(,\d+(\.\d+)?)*$/, '-ts', 'Not passed.'),
  intDim('main_gpu', 'Main GPU', MEM, 0, 63, '-mg', 'Not passed.'),
  choiceDim('kv_offload', 'KV offload', MEM, ON_OFF, '-kvo', 'Not passed.'),
  choiceDim('op_offload', 'Op offload', MEM, ON_OFF, '--op-offload', 'Not passed.'),
  choiceDim('swa_full', 'Full SWA cache', KVG, ON_OFF, '--swa-full', 'Not passed.'),
  choiceDim('repack', 'Weight repacking', BATCH, ON_OFF, '--repack', 'Not passed.'),
  textDim('override_tensor', 'Override tensor', MEM, /^\S+=\S+$/, '-ot', 'Not passed.'),
  intDim('poll', 'Polling', BATCH, 0, 100, '--poll', 'Not passed.'),
  intDim('prio', 'Priority', BATCH, -1, 3, '--prio', 'Not passed.'),
  intDim('cache_reuse', 'Cache reuse', KVG, 0, 65536, '--cache-reuse', 'Not passed. The benchmark sends cache_prompt off, so this cannot change the measured speed.'),
  { key: 'spec', label: 'Speculative', group: SPECG, kind: 'choice', min: null, max: null, choices: [], flag: null,
    auto: 'The speculative options as today: MTP and any draft files.', off: null, modes: SPEC_MODES },
  intDim('nmax', 'Draft max', SPECG, 1, 64, '--spec-draft-n-max', 'Today\'s draft lengths for each kind.', '3'),
  intDim('nmin', 'Draft min', SPECG, 0, 64, '--spec-draft-n-min', 'Not passed.'),
  { key: 'pmin', label: 'Draft p-min', group: SPECG, kind: 'float', min: 0, max: 1, choices: null, flag: '--spec-draft-p-min',
    auto: 'Not passed.', off: null, modes: ALL_MODES },
  intDim('ngld', 'Draft GPU layers', SPECG, 0, 999, '-ngld', 'Not passed.'),
  choiceDim('ctkd', 'Draft K cache', SPECG, KV_TYPES, '-ctkd', 'Not passed.'),
  choiceDim('ctvd', 'Draft V cache', SPECG, KV_TYPES, '-ctvd', 'Not passed.'),
];

/* Dimensions the search runs one at a time or in a grid (cls SPEED in the spec). */
const SPEED = ['fa', 'ub', 'b', 'np', 'kvu', 'threads', 'threads_batch', 'load_mode', 'numa', 'split_mode', 'tensor_split',
  'main_gpu', 'kv_offload', 'op_offload', 'swa_full', 'repack', 'override_tensor', 'poll', 'prio', 'cache_reuse'];
const SPEC_SUB = ['nmax', 'nmin', 'pmin'];
const DRAFT_SUB = ['ngld', 'ctkd', 'ctvd'];

const byKey = (key) => DIMS.find((d) => d.key === key);

/* A range token: lo..hi, lo..hi +step, or lo..hi xfactor. */
const RANGE = /^(-?\d+)\.\.(-?\d+)(?:(\+|x)(\d+))?$/i;
const RANGE_START = /^-?\d+\.\.-?\d+$/;
const MODIFIER = /^(\+|x)\d+$/i;

/* Splits the typed text into words. Commas separate entries; a range followed by a modifier word
   ("256..2048 x2") is one entry. Text dimensions split on ';' and new lines only. */
function tokenize(d, raw) {
  if (d.kind === 'text') return raw.split(/[;\n]/).map((s) => s.trim()).filter(Boolean);
  const out = [];
  for (const part of raw.split(',')) {
    const words = part.trim().split(/\s+/).filter(Boolean);
    for (let i = 0; i < words.length; i++) {
      let w = words[i];
      if (RANGE_START.test(w) && i + 1 < words.length && MODIFIER.test(words[i + 1])) w += words[++i];
      out.push(w);
    }
  }
  return out;
}

/* The values of one range token, or an error naming it. */
function expandRange(d, t, m) {
  const lo = Number(m[1]);
  const hi = Number(m[2]);
  const op = (m[3] || '').toLowerCase();
  const step = op ? Number(m[4]) : 1;
  if (lo > hi) return { values: [], error: `"${t}" starts above where it ends` };
  for (const n of [lo, hi]) {
    if (n < d.min) return { values: [], error: `"${t}": ${n} is below the minimum ${d.min}` };
    if (n > d.max) return { values: [], error: `"${t}": ${n} is above the maximum ${d.max}` };
  }
  if (op === '+' && step < 1) return { values: [], error: `"${t}": the step must be at least 1` };
  if (op === 'x' && step < 2) return { values: [], error: `"${t}": the factor must be 2 or more` };
  if (op === 'x' && lo < 1) return { values: [], error: `"${t}": a factor needs a start of 1 or more` };
  const mul = op === 'x';
  const values = [];
  for (let v = lo; v <= hi; mul ? (v *= step) : (v += step)) {
    values.push(String(v));
    if (values.length > MAX_VALUES) return { values: [], error: `"${t}" makes more than ${MAX_VALUES} values` };
  }
  return { values, error: null };
}

/* One typed value, checked against the dimension, as its canonical string. */
function checkValue(d, t) {
  const bad = (error) => ({ values: [], error });
  if (d.kind === 'int') {
    if (d.key === 'ngl' && t.toLowerCase() === 'auto') return { values: ['auto'], error: null };
    if (!/^-?\d+$/.test(t)) return bad(`"${t}" is not a whole number`);
    const n = Number(t);
    if (n < d.min) return bad(`"${t}" is below the minimum ${d.min}`);
    if (n > d.max) return bad(`"${t}" is above the maximum ${d.max}`);
    return { values: [String(n)], error: null };
  }
  if (d.kind === 'float') {
    if (!/^-?\d+(\.\d+)?$/.test(t)) return bad(`"${t}" is not a number`);
    const n = Number(t);
    if (n < d.min || n > d.max) return bad(`"${t}" is outside ${d.min} to ${d.max}`);
    return { values: [String(n)], error: null };
  }
  if (d.kind === 'choice') {
    const hit = d.choices.find((c) => c === t.toLowerCase());
    if (!hit) return bad(`"${t}" is not one of ${d.choices.join(', ')}`);
    return { values: [hit], error: null };
  }
  if (!d.pattern.test(t)) return bad(`"${t}" is not a valid ${d.label.toLowerCase()} value`);
  return { values: [t], error: null };
}

/* The values a user typed for a dimension: { values: string[], error: string|null }. Duplicates are
   removed, keeping the first. Accepts the dimension object or its key. */
export function parseValues(dim, text) {
  const d = typeof dim === 'string' ? byKey(dim) : dim;
  if (!d) return { values: [], error: 'Unknown setting.' };
  const raw = text == null ? '' : String(text);
  const tokens = tokenize(d, raw);
  if (!tokens.length) return { values: [], error: 'Enter at least one value.' };
  const out = [];
  for (const t of tokens) {
    let got;
    if (d.kind === 'int' && RANGE.test(t)) got = expandRange(d, t, RANGE.exec(t));
    else if (t.includes('..')) {
      got = { values: [], error: d.kind === 'int' ? `"${t}" is not a valid range` : `"${t}" is a range; ${d.label} takes single values` };
    } else got = checkValue(d, t);
    if (got.error) return { values: [], error: got.error };
    out.push(...got.values);
  }
  const values = [...new Set(out)];
  if (values.length > MAX_VALUES) return { values: [], error: `${values.length} values; the most is ${MAX_VALUES}` };
  return { values, error: null };
}

/* The values as they are shown in a box: "256, 512, 1024". */
export function formatValues(values) {
  return (values || []).join(', ');
}

/* The request's `space` from the form's state ({key: {mode, text}}). Auto dimensions are left out; off
   has no values; fixed and list carry their parsed values. Throws Error with the parse message. */
export function toSpace(state) {
  const out = {};
  for (const d of DIMS) {
    const s = (state || {})[d.key];
    if (!s || !s.mode || s.mode === 'auto') continue;
    if (!d.modes.includes(s.mode)) throw new Error(`${d.label} can only be ${d.modes.filter((m) => m !== 'auto').join(' or ')}.`);
    if (s.mode === 'off') { out[d.key] = { mode: 'off' }; continue; }
    const { values, error } = parseValues(d, s.text);
    if (error) throw new Error(error);
    if (s.mode === 'fixed' && values.length !== 1) throw new Error(`${d.label} is fixed, so it takes one value, not ${values.length}.`);
    out[d.key] = { mode: s.mode, values };
  }
  return out;
}

/* How many candidates a run will measure, as today's estimate() in pages/tuner.js counts them when the
   space is empty. prm = {objective, mode, kv_policy, vary_batch, speculative, drafts, space};
   model = {nextn_layers}. The space changes the count as the search does (see the spec's Candidate count). */
export function countCandidates(prm, model) {
  const space = prm.space || {};
  const get = (key) => space[key] || { mode: 'auto' };
  const isAuto = (key) => get(key).mode === 'auto';
  const isList = (key) => get(key).mode === 'list';
  // The strings a dimension takes: its own list in auto mode, else its off value or its values.
  const values = (key, auto) => {
    const s = get(key);
    if (s.mode === 'auto') return auto;
    if (s.mode === 'off') return [byKey(key).off];
    return s.values;
  };
  const len = (key) => (isList(key) ? get(key).values.length : 1);
  const extra = (key) => (isList(key) ? get(key).values.length - 1 : 0);
  const all = prm.kv_policy === 'all';

  let speculative = !!prm.speculative;
  let drafts = prm.drafts || [];
  if (get('spec').mode === 'off') { speculative = false; drafts = []; }
  const hasMtp = !!(model && model.nextn_layers);
  const nmaxSet = isAuto('nmax') ? null : values('nmax', null);
  const mtpN = hasMtp && speculative ? (nmaxSet ? nmaxSet.length : MTP_LENGTHS) : 0;
  const draftN = drafts.reduce((n, d) => n + (nmaxSet
    ? new Set(nmaxSet.map((v) => { const b = Number(d.block_size) || 0; const x = Number(v); return b ? Math.min(x, b) : x; })).size
    : draftLengths(d).length), 0);
  // Sub-dimensions multiply every option of their kind; the grid counts them, the other stages only
  // sweep them one at a time.
  const specMul = SPEC_SUB.filter((k) => k !== 'nmax').reduce((n, k) => n * len(k), 1);
  const draftMul = DRAFT_SUB.reduce((n, k) => n * len(k), specMul);
  const specGrid = mtpN * specMul + draftN * draftMul;
  const specOne = mtpN + draftN;
  const subExtra = specOne > 0
    ? SPEC_SUB.filter((k) => k !== 'nmax').reduce((n, k) => n + extra(k), 0) + (draftN > 0 ? DRAFT_SUB.reduce((n, k) => n + extra(k), 0) : 0)
    : 0;

  const kvCount = (grid) => {
    if (isAuto('ctk') && isAuto('ctv')) return all ? (grid ? 2 : 4) : 1;
    if (!all) return 1;
    return values('ctk', KV_AUTO).length * values('ctv', KV_AUTO).length;
  };

  if (prm.objective === 'context') {
    // Today: a benchmark and a few load-only probes per KV type, then the thread sweep.
    const threadsList = isList('threads') || isList('threads_batch');
    const threads = threadsList
      ? (isList('threads') ? len('threads') : 0) + (isList('threads_batch') ? len('threads_batch') : 0)
      : (isAuto('threads') || isAuto('threads_batch') ? 5 : 0);
    const generic = SPEED.filter((k) => k !== 'threads' && k !== 'threads_batch').reduce((n, k) => n + extra(k), 0);
    return kvCount(false) * (4 + 8 + 3 * 3) + threads + generic + specOne + subExtra + 3;
  }

  if (prm.mode === 'quick') {
    const faTerm = isAuto('fa') ? 2 : extra('fa');
    const ubTerm = values('ub', UBATCHES).length;
    const bTerm = isList('b') ? len('b') : 0;
    const generic = SPEED.filter((k) => k !== 'fa' && k !== 'ub' && k !== 'b').reduce((n, k) => n + extra(k), 0);
    return 4 + faTerm + ubTerm + bTerm + generic + specOne + subExtra;
  }

  const faN = isAuto('fa') ? (all ? 2 : 3) : len('fa');
  const ubs = values('ub', UBATCHES);
  const bAuto = isAuto('b');
  const bs = bAuto ? null : values('b', null);
  let batchN = 0;
  for (const u of ubs) {
    const n = Number(u);
    if (bAuto) {
      const set = new Set(prm.vary_batch ? [Math.max(2048, n), n, 4096] : [Math.max(2048, n)]);
      batchN += [...set].filter((b) => b >= n).length;
    } else {
      batchN += bs.filter((b) => Number(b) >= n).length;
    }
  }
  const others = SPEED.filter((k) => k !== 'fa' && k !== 'ub' && k !== 'b').reduce((n, k) => n * len(k), 1);
  return kvCount(true) * faN * batchN * others * (1 + specGrid) + 3;
}

/* Minutes for a count at a prompt depth, the range the page shows: [low, high], whole minutes. */
export function estimateMinutes(count, depth) {
  const d = Number(depth) || 0;
  return [Math.round(count * (0.5 + d / 20000)), Math.round(count * (0.8 + d / 8000))];
}
