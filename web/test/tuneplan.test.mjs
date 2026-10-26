import test from 'node:test';
import assert from 'node:assert/strict';
import { stageStates, stageCount, stageFill } from '../js/tuneplan.js';
import { DIMS, parseValues, formatValues, toSpace, countCandidates, estimateMinutes } from '../js/tunespace.js';
import { draftLengths } from '../js/features.js';

const quick = [
  { id: 'kv', label: 'KV cache', stages: ['KV cache'], max: null },
  { id: 'fa', label: 'Flash attention', stages: ['Flash attention'], max: 2 },
  { id: 'batch', label: 'Batch size', stages: ['Batch size'], max: 3 },
  { id: 'spec:MTP', label: 'Speculative: MTP', stages: ['Speculative (MTP)'], max: 5 },
];
const spill = [
  { id: 'spill', label: 'Spill search', stages: ['Fit', 'Spill'], max: null },
  { id: 'threads', label: 'CPU threads', stages: ['Threads'], max: 5 },
  { id: 'confirm', label: 'Confirm', stages: ['Confirm'], max: 3 },
];
const tr = (...stages) => stages.map((stage) => ({ stage }));
const view = (s) => s.map((x) => `${x.id}:${x.state}`).join(' ');

test('before the first trial everything is waiting', () => {
  assert.equal(view(stageStates(quick, [], '')), 'kv:upcoming fa:upcoming batch:upcoming spec:MTP:upcoming');
});

test('stages before the current one are done, after it upcoming', () => {
  const s = stageStates(quick, tr('KV cache', 'Flash attention', 'Flash attention', 'Batch size'), 'Batch size');
  assert.equal(view(s), 'kv:done fa:done batch:running spec:MTP:upcoming');
  assert.deepEqual([s[1].n, s[2].n, s[2].max], [2, 1, 3]);
});

test('a stage that was passed without trials, or is known to be empty, is skipped', () => {
  const noFa = [{ ...quick[0] }, { ...quick[1], max: 0 }, quick[2], quick[3]];
  assert.equal(view(stageStates(noFa, tr('KV cache'), 'KV cache')), 'kv:running fa:skipped batch:upcoming spec:MTP:upcoming');
  assert.equal(view(stageStates(quick, tr('KV cache', 'Batch size'), 'Batch size')), 'kv:done fa:skipped batch:running spec:MTP:upcoming');
});

test('stages that share trials are one step, and a stage can outgrow its estimate', () => {
  const s = stageStates(spill, tr('Fit', 'Spill', 'Fit', 'Fit', 'Spill'), 'Fit');
  assert.equal(view(s), 'spill:running threads:upcoming confirm:upcoming');
  assert.equal(s[0].n, 5);
  const over = stageStates(spill, tr('Fit', 'Spill', 'Threads', 'Threads', 'Threads', 'Threads', 'Threads', 'Threads'), 'Threads');
  assert.deepEqual([over[1].n, over[1].max], [6, 6]);
});

test('the last stage running leaves the rest done', () => {
  const s = stageStates(spill, tr('Fit', 'Spill', 'Threads', 'Confirm'), 'Confirm');
  assert.equal(view(s), 'spill:done threads:done confirm:running');
});

test('an empty plan, or a stage outside it, does not break', () => {
  assert.deepEqual(stageStates(undefined, [], 'Grid'), []);
  assert.deepEqual(stageStates([], [], 'Grid'), []);
  assert.equal(view(stageStates(quick, tr('KV cache'), 'Something new')), 'kv:done fa:upcoming batch:upcoming spec:MTP:upcoming');
});

test('counts and bar fill read well in each state', () => {
  const s = stageStates(quick, tr('KV cache', 'Flash attention', 'Batch size'), 'Batch size');
  assert.deepEqual(s.map(stageCount), ['1 measured', '1 measured', '1 of 3', 'up to 5']);
  assert.deepEqual(s.map(stageFill), [1, 1, 1 / 3, 0]);
  assert.equal(stageCount({ state: 'skipped' }), 'skipped');
  assert.equal(stageCount({ state: 'upcoming', max: null }), 'waiting');
  assert.equal(stageCount({ state: 'running', n: 4, max: null }), '4 measured');
  assert.equal(stageFill({ state: 'running', n: 4, max: null }), 0);
});

/* ---- tunespace ---- */

// The estimate() of pages/tuner.js as it was, the reference for an empty space.
function oldEstimate(prm, hasMtp) {
  const spec = (hasMtp && prm.speculative ? 5 : 0) + (prm.drafts || []).reduce((n, d) => n + draftLengths(d).length, 0);
  if (prm.objective === 'context') {
    const kv = prm.kv_policy === 'all' ? 4 : 1;
    return kv * (4 + 8 + 3 * 3) + 5 + spec + 3;
  }
  if (prm.mode === 'quick') return 4 + 2 + 4 + spec;
  const kv = prm.kv_policy === 'all' ? 2 : 1;
  const fa = prm.kv_policy === 'all' ? 2 : 3;
  const batches = prm.vary_batch ? 11 : 4;
  return kv * fa * batches * (1 + spec) + 3;
}

const lists = (state) => toSpace(state);
const npList = { mode: 'list', text: '1, 2, 4' };
const DRAFTS = [
  { type: 'draft-dspark', path: 'a.gguf', name: 'a', size: 1 },
  { type: 'draft-dflash', path: 'b.gguf', name: 'b', size: 1, block_size: 8 },
  { type: 'draft-eagle3', path: 'c.gguf', name: 'c', size: 1 },
];

test('parseValues reads lists, separated by commas or spaces, and drops duplicates', () => {
  assert.deepEqual(parseValues('ub', '256, 1024'), { values: ['256', '1024'], error: null });
  assert.deepEqual(parseValues('ub', '256 512'), { values: ['256', '512'], error: null });
  assert.deepEqual(parseValues('ub', '512, 512, 1024, 512').values, ['512', '1024']);
  assert.deepEqual(parseValues(byKeyForTest('ub'), '  7  ').values, ['7']);
});

function byKeyForTest(key) { return DIMS.find((d) => d.key === key); }

test('parseValues expands every range form and mixes them with plain values', () => {
  assert.deepEqual(parseValues('np', '1..3').values, ['1', '2', '3']);
  assert.deepEqual(parseValues('ub', '256..1024 +256').values, ['256', '512', '768', '1024']);
  assert.deepEqual(parseValues('ub', '256..2048 x2').values, ['256', '512', '1024', '2048']);
  assert.deepEqual(parseValues('ub', '256..2048 x2, 3000').values, ['256', '512', '1024', '2048', '3000']);
  assert.deepEqual(parseValues('ub', '256..2048x2').values, ['256', '512', '1024', '2048']);
  assert.deepEqual(parseValues('ub', '256..1000+300').values, ['256', '556', '856']);
  assert.deepEqual(parseValues('prio', '-1..1').values, ['-1', '0', '1']);
  assert.deepEqual(parseValues('ngl', 'auto, 99').values, ['auto', '99']);
});

test('parseValues checks choices without regard to case', () => {
  assert.deepEqual(parseValues('ctk', 'Q8_0, f16').values, ['q8_0', 'f16']);
  assert.deepEqual(parseValues('kvu', 'ON').values, ['on']);
  assert.deepEqual(parseValues('load_mode', 'mmap+mlock').values, ['mmap+mlock']);
  assert.equal(parseValues('fa', 'fast').error, '"fast" is not one of on, off, auto');
});

test('parseValues validates text by its pattern and keeps commas inside a value', () => {
  assert.deepEqual(parseValues('tensor_split', '3,1; 1,1').values, ['3,1', '1,1']);
  assert.deepEqual(parseValues('override_tensor', 'blk\\.1=CPU\nblk\\.2=CPU').values, ['blk\\.1=CPU', 'blk\\.2=CPU']);
  assert.equal(parseValues('override_tensor', '=CPU').error, '"=CPU" is not a valid override tensor value');
  assert.equal(parseValues('tensor_split', 'a,b').error, '"a,b" is not a valid tensor split value');
});

test('parseValues reports the value that is wrong', () => {
  assert.equal(parseValues('ub', '0').error, '"0" is below the minimum 1');
  assert.equal(parseValues('ub', '70000').error, '"70000" is above the maximum 65536');
  assert.equal(parseValues('ub', 'abc').error, '"abc" is not a whole number');
  assert.equal(parseValues('ub', '512..256').error, '"512..256" starts above where it ends');
  assert.equal(parseValues('ub', '1..x').error, '"1..x" is not a valid range');
  assert.equal(parseValues('ub', '256..2048 x1').error, '"256..2048x1": the factor must be 2 or more');
  assert.equal(parseValues('ub', '256..2048 +0').error, '"256..2048+0": the step must be at least 1');
  assert.equal(parseValues('ngl', '0..8 x2').error, '"0..8x2": a factor needs a start of 1 or more');
  assert.equal(parseValues('ub', '1..60000').error, '"1..60000" makes more than 32 values');
  assert.equal(parseValues('ub', '2..4096').error, '"2..4096" makes more than 32 values');
  assert.equal(parseValues('ub', '4000..70000').error, '"4000..70000": 70000 is above the maximum 65536');
  assert.equal(parseValues('pmin', '0..1').error, '"0..1" is a range; Draft p-min takes single values');
  assert.equal(parseValues('pmin', '1.5').error, '"1.5" is outside 0 to 1');
  assert.equal(parseValues('ub', '').error, 'Enter at least one value.');
  assert.equal(parseValues('ub', ' , ').error, 'Enter at least one value.');
  assert.equal(parseValues('ub', '1..32, 33').error, '33 values; the most is 32');
  assert.equal(parseValues('ub', '1..32').values.length, 32);
});

test('parseValues accepts a dimension by key or by object, and formats values for a box', () => {
  assert.deepEqual(parseValues('ub', '512'), parseValues(byKeyForTest('ub'), '512'));
  assert.equal(formatValues(['256', '1024', '3000']), '256, 1024, 3000');
  assert.equal(formatValues([]), '');
});

test('toSpace keeps only the non-auto dimensions, with their values', () => {
  const s = toSpace({
    ub: { mode: 'list', text: '256..1024 x2' },
    np: { mode: 'fixed', text: '2' },
    fa: { mode: 'off', text: '' },
    ctx: { mode: 'auto', text: '8192' },
    kvu: { mode: 'auto' },
  });
  assert.deepEqual(s, {
    ub: { mode: 'list', values: ['256', '512', '1024'] },
    np: { mode: 'fixed', values: ['2'] },
    fa: { mode: 'off' },
  });
  assert.deepEqual(toSpace({}), {});
  assert.deepEqual(toSpace(undefined), {});
});

test('toSpace throws the parse message for an invalid dimension', () => {
  assert.throws(() => toSpace({ ub: { mode: 'list', text: '0, 512' } }), /"0" is below the minimum 1/);
  assert.throws(() => toSpace({ fa: { mode: 'list', text: 'on, fast' } }), /"fast" is not one of/);
  assert.throws(() => toSpace({ np: { mode: 'fixed', text: '1, 2' } }), /Parallel slots is fixed, so it takes one value, not 2/);
  assert.throws(() => toSpace({ spec: { mode: 'list', text: 'mtp' } }), /Speculative can only be off/);
});

test('countCandidates with an empty space equals the old estimate', () => {
  let checked = 0;
  for (const objective of ['decode', 'balanced', 'prefill', 'context']) {
    for (const mode of ['grid', 'quick']) {
      for (const kv_policy of ['precise', 'all']) {
        for (const vary_batch of [false, true]) {
          for (const speculative of [false, true]) {
            for (const drafts of [[], DRAFTS.slice(0, 1), DRAFTS]) {
              for (const nextn_layers of [0, 1]) {
                const prm = { objective, mode, kv_policy, vary_batch, speculative, drafts, space: {} };
                assert.equal(countCandidates(prm, { nextn_layers }), oldEstimate(prm, nextn_layers), JSON.stringify(prm));
                checked++;
              }
            }
          }
        }
      }
    }
  }
  assert.equal(checked, 4 * 2 * 2 * 2 * 2 * 3 * 2);
  assert.equal(countCandidates({ objective: 'decode', mode: 'grid', kv_policy: 'precise', vary_batch: false }, {}), 15);
});

test('countCandidates: ub list times np list in the grid', () => {
  const space = lists({ ub: { mode: 'list', text: '256, 512' }, np: npList });
  // 1 KV pair x 3 flash-attention values x 2 micro-batches (logical batch auto) x 3 slots + confirm
  const prm = { objective: 'decode', mode: 'grid', kv_policy: 'precise', vary_batch: false, space };
  assert.equal(countCandidates(prm, {}), 1 * 3 * 2 * 3 + 3);
  // the same with vary batch: a micro-batch of 256 gets 3 logical batches, 512 gets 3
  const varied = countCandidates({ ...prm, vary_batch: true }, {});
  assert.equal(varied, 1 * 3 * (3 + 3) * 3 + 3);
});

test('countCandidates: quick with a list of 3 threads adds two trials', () => {
  const base = countCandidates({ objective: 'decode', mode: 'quick', kv_policy: 'precise' }, {});
  assert.equal(base, 10);
  const space = lists({ threads: { mode: 'list', text: '4, 8, 16' } });
  assert.equal(countCandidates({ objective: 'decode', mode: 'quick', kv_policy: 'precise', space }, {}), base + 2);
});

test('countCandidates: a list of draft lengths replaces the defaults', () => {
  const nmax = lists({ nmax: { mode: 'list', text: '2, 4' } });
  const prm = (space) => ({ objective: 'decode', mode: 'grid', kv_policy: 'precise', vary_batch: false, speculative: true, drafts: [], space });
  // MTP at 2 lengths instead of 5: 1 x 3 x 4 x (1 + 2) + 3
  assert.equal(countCandidates(prm({}), { nextn_layers: 1 }), 1 * 3 * 4 * (1 + 5) + 3);
  assert.equal(countCandidates(prm(nmax), { nextn_layers: 1 }), 1 * 3 * 4 * (1 + 2) + 3);
  // a draft file with block size 8 clamps 4 and 16 to 4 and 8: two lengths
  const draft = { ...prm(nmax), drafts: [DRAFTS[1]] };
  assert.equal(countCandidates(draft, {}), 1 * 3 * 4 * (1 + 2) + 3);
  const off = countCandidates({ ...prm({}), speculative: true, space: lists({ spec: { mode: 'off' } }) }, { nextn_layers: 1 });
  assert.equal(off, 1 * 3 * 4 + 3);
});

test('countCandidates: the context objective with a list of threads', () => {
  const base = countCandidates({ objective: 'context', kv_policy: 'precise' }, {});
  assert.equal(base, 21 + 5 + 3);
  const space = lists({ threads: { mode: 'list', text: '4, 8' }, threads_batch: { mode: 'fixed', text: '8' } });
  assert.equal(countCandidates({ objective: 'context', kv_policy: 'precise', space }, {}), 21 + 2 + 3);
});

test('estimateMinutes gives the range the page shows', () => {
  assert.deepEqual(estimateMinutes(100, 2000), [60, 105]);
  assert.deepEqual(estimateMinutes(0, 2000), [0, 0]);
  assert.deepEqual(estimateMinutes(100, '2000'), [60, 105]);
  assert.deepEqual(estimateMinutes(100), [50, 80]);
});
