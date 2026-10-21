import test from 'node:test';
import assert from 'node:assert/strict';
import { readFeatures, setVision, setSpec, chipsFor, toggleChip, defaultNMax } from '../js/features.js';

const argv = (rows) => rows.filter((r) => r.enabled !== false).flatMap((r) => (r.value == null || r.value === '' ? [r.flag] : [r.flag, r.value]));
const dspark = { path: '/m/dspark.gguf', type: 'draft-dspark', name: 'dspark', block_size: 15 };
const model = { projectors: ['/m/mmproj.gguf'], nextn_layers: 1, drafts: [dspark] };
const base = [{ flag: '-ngl', value: '99' }, { flag: '-c', value: '8192' }];

test('reads vision and speculative state from flag rows', () => {
  const f = readFeatures([...base, { flag: '--mmproj', value: '/m/mmproj.gguf' }, { flag: '--spec-type', value: 'draft-mtp' }, { flag: '--spec-draft-n-max', value: '3' }]);
  assert.equal(f.vision.on, true);
  assert.deepEqual([f.spec.mode, f.spec.type, f.spec.nmax], ['mtp', 'draft-mtp', '3']);
  const off = readFeatures([{ flag: '--mmproj', value: '/x', enabled: false }, { flag: '--spec-type', value: 'draft-mtp', enabled: false }]);
  assert.equal(off.vision.on, false);
  assert.equal(off.spec.mode, 'off');
});

test('turning MTP on adds the flags and off disables them without losing the number', () => {
  let rows = setSpec(base, 'mtp');
  assert.deepEqual(argv(rows), ['-ngl', '99', '-c', '8192', '--spec-type', 'draft-mtp', '--spec-draft-n-max', '3']);
  rows = rows.map((r) => (r.flag === '--spec-draft-n-max' ? { ...r, value: '5' } : r));
  const off = setSpec(rows, 'off');
  assert.deepEqual(argv(off), ['-ngl', '99', '-c', '8192']);
  assert.equal(off.find((r) => r.flag === '--spec-draft-n-max').value, '5'); // remembered
  const back = setSpec(off, 'mtp');
  assert.deepEqual(argv(back).slice(-4), ['--spec-type', 'draft-mtp', '--spec-draft-n-max', '5']);
});

test('a draft file sets the type, the file, and a sensible draft length', () => {
  const rows = setSpec(base, 'draft', dspark);
  assert.deepEqual(argv(rows).slice(4), ['--spec-type', 'draft-dspark', '-md', '/m/dspark.gguf', '--spec-draft-n-max', '7']);
  assert.equal(readFeatures(rows).spec.mode, 'draft');
});

test('switching from MTP to a draft replaces the default length but keeps a custom one', () => {
  const nmax = (rows) => rows.find((r) => r.flag === '--spec-draft-n-max').value;
  const mtp = setSpec(base, 'mtp');
  assert.equal(nmax(setSpec(mtp, 'draft', dspark)), '7');
  const custom = mtp.map((r) => (r.flag === '--spec-draft-n-max' ? { ...r, value: '5' } : r));
  assert.equal(nmax(setSpec(custom, 'draft', dspark)), '5');
  // and the draft file is dropped again when going back to MTP
  const back = setSpec(setSpec(mtp, 'draft', dspark), 'mtp');
  assert.ok(!argv(back).includes('-md'));
  assert.ok(argv(back).includes('draft-mtp'));
});

test('n-gram types stay when the draft changes', () => {
  const rows = setSpec([{ flag: '--spec-type', value: 'ngram-mod' }], 'mtp');
  assert.equal(rows[0].value, 'draft-mtp,ngram-mod');
  assert.equal(setSpec(rows, 'off')[0].value, 'ngram-mod');
});

test('vision toggles by disabling, and restores the projector', () => {
  const on = setVision(base, true, '/m/mmproj.gguf');
  assert.deepEqual(argv(on).slice(4), ['--mmproj', '/m/mmproj.gguf']);
  const off = setVision(on, false);
  assert.deepEqual(argv(off), ['-ngl', '99', '-c', '8192']);
  assert.equal(off.at(-1).value, '/m/mmproj.gguf');
  assert.deepEqual(argv(setVision(off, true)).slice(4), ['--mmproj', '/m/mmproj.gguf']);
  assert.equal(setVision(base, true).length, base.length); // no projector known, nothing to add
});

test('input rows are never mutated', () => {
  const rows = [{ flag: '--spec-type', value: 'draft-mtp' }];
  const copy = JSON.parse(JSON.stringify(rows));
  setSpec(rows, 'off');
  setVision(rows, true, '/x');
  assert.deepEqual(rows, copy);
});

test('chips show what the model can do and toggle it', () => {
  let rows = base;
  let chips = chipsFor(model, rows);
  assert.deepEqual(chips.map((c) => [c.label, c.on]), [['Vision', false], ['MTP', false], ['DSpark', false]]);
  rows = toggleChip(model, rows, chips[1]);
  chips = chipsFor(model, rows);
  assert.deepEqual(chips.map((c) => [c.label, c.on]), [['Vision', false], ['MTP', true], ['DSpark', false]]);
  rows = toggleChip(model, rows, chips[2]); // DSpark replaces MTP
  chips = chipsFor(model, rows);
  assert.deepEqual(chips.map((c) => c.on), [false, false, true]);
  rows = toggleChip(model, rows, chips[2]);
  assert.deepEqual(chipsFor(model, rows).map((c) => c.on), [false, false, false]);
  assert.deepEqual(chipsFor({}, base), []);
  assert.deepEqual(chipsFor(undefined, [{ flag: '--spec-type', value: 'draft-mtp' }]).map((c) => [c.label, c.on]), [['MTP', true]]);
});

test('draft length defaults', () => {
  assert.equal(defaultNMax('draft-mtp'), '3');
  assert.equal(defaultNMax('draft-dspark'), '7');
  assert.equal(defaultNMax('draft-dflash', { block_size: 15 }), '15');
});

test('the vision chip can be hidden for roles that ignore images', () => {
  assert.deepEqual(chipsFor(model, base, { noVision: true }).map((c) => c.label), ['MTP', 'DSpark']);
});
