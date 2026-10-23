import test from 'node:test';
import assert from 'node:assert/strict';
import { stageStates, stageCount, stageFill } from '../js/tuneplan.js';

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
