import test from 'node:test';
import assert from 'node:assert/strict';
import { nestLibrary, libraryRows } from '../js/library.js';

const lib = [
  { path: '/m/a/Alpha-Q4', name: 'Alpha-Q4', repo: 'org/a', arch: 'llama', size: 5, mtime: 3, context_length: 8192, companion_of: null },
  { path: '/m/a/mmproj-Alpha', name: 'mmproj-Alpha', repo: 'org/a', arch: 'clip', size: 1, mtime: 9, context_length: null, is_projector: true, companion_of: '/m/a/Alpha-Q4' },
  { path: '/m/a/dspark-Alpha', name: 'dspark-Alpha', repo: 'org/a', arch: 'dflash', size: 1, mtime: 8, context_length: 4096, is_draft: true, companion_of: '/m/a/Alpha-Q4' },
  { path: '/m/b/Beta-Q8', name: 'Beta-Q8', repo: 'org/b', arch: 'qwen3', size: 9, mtime: 5, context_length: 4096, companion_of: null },
  { path: '/m/c/mmproj-Lonely', name: 'mmproj-Lonely', repo: 'org/c', arch: 'clip', size: 1, mtime: 1, context_length: null, is_projector: true, companion_of: null },
];
const names = (rows) => rows.map((r) => (r.depth ? '  ' : '') + r.name);
const opts = (o) => ({ sort: { key: 'name', direction: 'asc' }, filter: '', expanded: new Set(), ...o });

test('nestLibrary puts companions under their model and leaves the rest on top', () => {
  assert.deepEqual(nestLibrary(lib).map((m) => [m.name, m.children.map((c) => c.name)]),
    [['Alpha-Q4', ['dspark-Alpha', 'mmproj-Alpha']], ['Beta-Q8', []], ['mmproj-Lonely', []]]);
  assert.deepEqual(nestLibrary(lib)[0].children.map((c) => c.parentName), ['Alpha-Q4', 'Alpha-Q4']);
  assert.equal(lib[0].children, undefined); // the input is not changed
});

test('a companion whose model is missing from the list stays on top', () => {
  const lib2 = [...lib, { path: '/m/d/mmproj-Ghost', name: 'mmproj-Ghost', repo: 'org/d', arch: 'clip',
                          size: 1, mtime: 2, is_projector: true, companion_of: '/m/d/Ghost-Q4' }];
  const tops = nestLibrary(lib2);
  assert.deepEqual(tops.map((m) => [m.name, m.children.length]),
    [['Alpha-Q4', 2], ['Beta-Q8', 0], ['mmproj-Lonely', 0], ['mmproj-Ghost', 0]]);
});

test('models start collapsed', () => {
  const rows = libraryRows(nestLibrary(lib), opts());
  assert.deepEqual(names(rows), ['Alpha-Q4', 'Beta-Q8', 'mmproj-Lonely']);
  assert.deepEqual(rows.map((r) => [r.childCount, r.open]), [[2, false], [0, false], [0, false]]);
});

test('sorting orders the models and children stay under their parent', () => {
  const rows = libraryRows(nestLibrary(lib), opts({ sort: { key: 'mtime', direction: 'desc' }, expanded: new Set(['/m/a/Alpha-Q4']) }));
  assert.deepEqual(names(rows), ['Beta-Q8', 'Alpha-Q4', '  dspark-Alpha', '  mmproj-Alpha', 'mmproj-Lonely']);
  assert.equal(rows[1].open, true);
});

test('a filter matching only a companion shows its model opened with just that companion', () => {
  const rows = libraryRows(nestLibrary(lib), opts({ filter: 'DSPARK' }));
  assert.deepEqual(names(rows), ['Alpha-Q4', '  dspark-Alpha']);
  assert.equal(rows[0].open, true);
  assert.equal(rows[0].forcedOpen, true);
  assert.equal(rows[1].parentName, 'Alpha-Q4');
});

test('a row held open by its companions is forced open, and ordinary rows are not', () => {
  assert.deepEqual(libraryRows(nestLibrary(lib), opts()).map((r) => r.forcedOpen), [false, false, false]);
});

test('filtering does not match "null" in place of a missing arch', () => {
  const lib2 = lib.map((m) => ({ ...m, arch: undefined }));
  assert.deepEqual(libraryRows(nestLibrary(lib2), opts({ filter: 'null' })), []);
  assert.deepEqual(libraryRows(nestLibrary(lib2), opts({ filter: 'undefined' })), []);
});

test('a filter matching the model keeps its companions behind the toggle', () => {
  assert.deepEqual(names(libraryRows(nestLibrary(lib), opts({ filter: 'org/a' }))), ['Alpha-Q4']);
  assert.deepEqual(names(libraryRows(nestLibrary(lib), opts({ filter: 'beta' }))), ['Beta-Q8']);
});

test('a filter matching nothing gives no rows', () => {
  assert.deepEqual(libraryRows(nestLibrary(lib), opts({ filter: 'zzz' })), []);
});

test('missing values sort last in both directions', () => {
  for (const direction of ['asc', 'desc']) {
    const rows = libraryRows(nestLibrary(lib), opts({ sort: { key: 'context_length', direction } }));
    assert.equal(rows[rows.length - 1].name, 'mmproj-Lonely');
  }
});

test('the flat view is every file at the top level', () => {
  const rows = libraryRows(lib.map((m) => ({ ...m, children: [] })), opts({ sort: { key: 'size', direction: 'desc' } }));
  assert.equal(rows.length, 5);
  assert.ok(rows.every((r) => r.depth === 0 && r.childCount === 0));
  assert.equal(rows[0].name, 'Beta-Q8');
});
