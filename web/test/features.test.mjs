import test from 'node:test';
import assert from 'node:assert/strict';
import { readFeatures, setVision, setSpec, setKvUnified, kvUnifiedNote, chipsFor, toggleChip, defaultNMax } from '../js/features.js';

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

const np = (n, extra = {}) => ({ flag: '-np', value: n, ...extra });
const kvOf = (rows) => readFeatures(rows).kv;

test('unified KV follows -np: always on while auto, off by default once -np is set', () => {
  assert.deepEqual(kvOf(base), { on: true, forced: true, slots: null, present: false });
  assert.equal(kvOf([...base, np('-1')]).on, true);
  assert.deepEqual(kvOf([...base, np('1')]), { on: false, forced: false, slots: 1, present: false });
  assert.equal(kvOf([...base, np('1', { enabled: false })]).forced, true); // a skipped -np is no -np
  // --no-kv-unified is ignored by llama-server while -np is auto; the last flag wins otherwise
  assert.equal(kvOf([{ flag: '--no-kv-unified' }]).on, true);
  assert.equal(kvOf([np('2'), { flag: '--kv-unified' }]).on, true);
  assert.equal(kvOf([np('2'), { flag: '-kvu' }, { flag: '--no-kv-unified' }]).on, false);
  assert.equal(kvOf([np('2'), { flag: '--no-kv-unified' }, { flag: '-kvu' }]).on, true);
  assert.equal(kvOf([np('2'), { flag: '--kv-unified', enabled: false }]).on, false);
  assert.equal(kvOf([np('2'), { flag: '--kv-unified', enabled: false }]).present, true);
});

test('turning unified KV on adds the flag, off skips it and keeps it for later', () => {
  const fixed = [...base, np('1')];
  const on = setKvUnified(fixed, true);
  assert.deepEqual(argv(on), ['-ngl', '99', '-c', '8192', '-np', '1', '--kv-unified']);
  assert.equal(kvOf(on).on, true);
  const off = setKvUnified(on, false);
  assert.deepEqual(argv(off), ['-ngl', '99', '-c', '8192', '-np', '1']);
  assert.equal(off.at(-1).flag, '--kv-unified'); // remembered, greyed out
  assert.deepEqual(argv(setKvUnified(off, true)).slice(-1), ['--kv-unified']);
  assert.equal(setKvUnified(off, true).length, off.length); // re-enabled, not added twice
});

test('turning unified KV on replaces a --no-kv-unified, whichever spelling', () => {
  const rows = [np('2'), { flag: '-no-kvu' }];
  assert.deepEqual(argv(setKvUnified(rows, true)), ['-np', '2', '--kv-unified']);
  assert.equal(kvOf(setKvUnified(rows, true)).on, true);
  const both = [np('2'), { flag: '-kvu' }, { flag: '--no-kv-unified' }];
  assert.equal(kvOf(setKvUnified(both, true)).on, true);
  assert.equal(kvOf(setKvUnified([np('2'), { flag: '-kvu' }, { flag: '-kvu' }], false)).on, false);
});

test('turning unified KV off while -np is auto pins the slot count auto would use', () => {
  const off = setKvUnified(base, false);
  assert.deepEqual(argv(off), ['-ngl', '99', '-c', '8192', '-np', '4']);
  assert.equal(kvOf(off).on, false);
  // an -np the preset had before comes back instead
  const was = [...base, np('2', { enabled: false })];
  assert.deepEqual(argv(setKvUnified(was, false)).slice(-2), ['-np', '2']);
  // and -np -1 is replaced
  assert.deepEqual(argv(setKvUnified([np('-1')], false)), ['-np', '4']);
  // with --kv-unified given and -np auto it is the same story
  const forced = setKvUnified([{ flag: '--kv-unified' }], false);
  assert.deepEqual(argv(forced), ['-np', '4']);
  assert.equal(kvOf(forced).on, false);
});

test('the unified KV chip appears only for models with an attention cache, or presets already using the flag', () => {
  const dense = { kv_cache: true };
  const rows = [...base, np('1')];
  const chips = chipsFor(dense, rows);
  assert.deepEqual(chips.map((c) => [c.label, c.on]), [['KV unified', false]]);
  const flipped = toggleChip(dense, rows, chips[0]);
  assert.deepEqual(chipsFor(dense, flipped).map((c) => c.on), [true]);
  assert.deepEqual(chipsFor(dense, toggleChip(dense, flipped, chipsFor(dense, flipped)[0])).map((c) => c.on), [false]);
  assert.deepEqual(chipsFor({ kv_cache: false }, rows), []);
  assert.deepEqual(chipsFor({}, rows), []);
  assert.deepEqual(chipsFor({ kv_cache: false }, [{ flag: '--kv-unified' }]).map((c) => c.label), ['KV unified']);
  assert.deepEqual(chipsFor(dense, rows, { noKvUnified: true }), []);
  // sits after the other switches
  assert.deepEqual(chipsFor({ ...model, kv_cache: true }, rows).map((c) => c.label), ['Vision', 'MTP', 'DSpark', 'KV unified']);
});

test('the note says what the switch does for the slot count in use', () => {
  assert.match(kvUnifiedNote(kvOf(base)), /always does while -np is auto.*-np 4/);
  assert.match(kvUnifiedNote(kvOf([np('1')])), /single slot.*no difference/);
  assert.match(kvUnifiedNote(kvOf([np('1'), { flag: '-kvu' }])), /single slot.*changes nothing/);
  assert.match(kvUnifiedNote(kvOf([np('3')])), /split evenly between the 3 slots/);
  assert.match(kvUnifiedNote(kvOf([np('3'), { flag: '-kvu' }])), /3 slots share one KV pool/);
});
