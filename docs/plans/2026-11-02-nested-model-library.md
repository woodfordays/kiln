# Nested model library: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** By default, the Models page's Library lists models, with each model's companion files (vision
projectors `mmproj-*`, draft files such as DSpark, DFlash, EAGLE-3) folded under it. That way you can see at a
glance which models you have.

**Architecture:** The backend already knows, per folder, which files are companions (`is_projector`,
`is_draft`) and ranks them by how well their names match each model. It adds one field per file,
`companion_of`: the path of the single model a companion belongs to, or `null`. A pure frontend helper turns the
flat list into "model, then its companions" rows, sorted and filtered. The page renders those rows in the
existing Ember `Table`, with an expand toggle per model and a switch back to the flat list.

**Tech stack:** Python (library scan, `app/kiln/models.py`), React 18 + `htm` + Ember `Table` (frontend),
`unittest` and `node --test`.

**Spec:** the user's request: "I want the default view of model files to nest things like dspark, mmproj, etc.
so that its easier to see what models are there."

## What the user sees when this is done

- The Library shows one row per model (and per companion file that has no model in its folder). It's sorted by
  date added, newest first, as today.
- A model with companion files has a chevron (`›`) before its name. The line under the name still says
  `+ vision projector · + DSpark file`. Pressing the chevron shows the companion rows under it, indented with a
  line on the left, each with its own badges, size, date and Copy/Delete buttons. Pressing it again hides them.
  Models start collapsed.
- Sorting by any column sorts the models. Companions always stay directly under their model, sorted by name.
- Filtering: a model that matches is shown (its companions stay behind the chevron). A model whose *companion*
  matches but which doesn't match itself is shown opened, with just the matching companions. So typing `mmproj`
  lists every model that has a projector, with the projectors visible.
- The card's description reads `5 models · 8 companion files · 110.9 GiB`.
- A **Grouped / All files** control in the card header switches to today's flat list. The choice is remembered
  in this browser (`localStorage`).
- Nothing else changes: the Presets editor, the Tuner's model list and `/api/models` consumers keep working.
  `companion_of` is an extra field.

## Global constraints

- A companion belongs to **at most one** model, and **only to a model in the same folder**. That's the rule the
  existing projector and draft matching uses (`models.py:136-140`).
- `GET /api/models` stays backward compatible: one new field per entry, nothing removed or reordered.
- Don't change how `e["projectors"]` and `e["drafts"]` are ranked. The Tuner and the preset editor pick the
  first of each, and changing that is a separate decision.
- No new dependencies. `web/js/library.js` imports nothing, so Node can test it.

## Review focus

1. **A projector whose name matches no model** (`mmproj-F16.gguf` next to `Qwen3.8-27B-UD-Q4_K_XL.gguf`, a real
   unsloth layout) still nests under a model in its folder (`test_each_companion_names_the_model_it_belongs_to`).
2. **A DSpark file named `dspark-<model>`** matches its model even though `dspark` is the first word. Today's
   draft ranking doesn't strip it, so the new rule must (`_companion_stem` in the same test).
3. **A companion alone in its folder** (downloaded without its model) is a top-level row, not lost
   (`test_a_companion_alone_in_its_folder_belongs_to_nothing`, and the `mmproj-Lonely` JS case).
4. **Sorting by a column some rows have no value for** (Trained context is `null` for projectors): `null` sorts
   last in both directions instead of jumping around (`missing values sort last` test).
5. **Deleting a model with companions** deletes only that model's own files, as today. Its companions move to
   another model in the folder or become top-level rows on the next scan. Check this by hand.

## How the code works today (read this first)

- `app/kiln/models.py:87-143`, `scan()`: walks the model folders and groups split files into one entry. Then,
  **per folder**, it sorts entries into `projectors`, `drafts` and `entries` (models) (line 135). It gives every
  model a `projectors` list and a `drafts` list, ranked by `_shared_prefix` (line 66: how many leading `-_.`-separated
  name tokens two names share). The projector ranking strips `mmproj-` first; the draft ranking doesn't. The
  result is sorted models first, then companions (line 142).
- `app/tests/test_models.py`: one test, `test_companions_are_attached_to_their_model`, which builds a folder of
  fake GGUF files with `tests/helpers.py` (`llm()`, `write_gguf()`). Copy its style.
- `web/js/pages/models.js:148-202`, `LibraryCard`: filters with a `useMemo` (line 154) and renders an `E.Table`
  with `defaultSort={{ key: 'mtime', direction: 'desc' }}` (line 196). The Table sorts internally.
- `web/vendor/ember.js:664-703`, the Ember `Table`: if you pass a `sort` prop, it does **not** sort the rows
  itself. It renders them in the order given, shows the sort arrows from `sort`, and calls `onSortChange(next)`
  when a header is clicked (lines 677-695). That "controlled" mode is what lets us keep children under their
  parent. The table can't style a whole row, so the indentation goes inside the Model cell.
- A column without `render` shows `row[column.key]` (line 702), so rows must stay model-shaped objects.

## File map

| File | Change |
|---|---|
| `app/kiln/models.py` | `_companion_stem()`, `_owner()`, and `companion_of` on every entry in `scan()` |
| `app/tests/test_models.py` | Two tests |
| `web/js/library.js` (new) | `nestLibrary(models)`, `libraryRows(groups, opts)` |
| `web/test/library.test.mjs` (new) | Tests for both |
| `web/js/pages/models.js` | `LibraryCard` renders grouped rows, the toggle, the Grouped / All files control |
| `web/css/app.css` | `.lib-name`, `.lib-child`, `.lib-toggle-space` |
| `docs/GUIDE.md`, `docs/API.md` | Library paragraph; `companion_of` |

---

### Task 1: The backend says which model each companion belongs to

**Files:**
- Modify: `app/kiln/models.py` (new helpers after `_shared_prefix`, line 73; `scan()` lines 136-141)
- Test: `app/tests/test_models.py`

**Interfaces (Produces):**

```python
def _companion_stem(name: str) -> str
    # the name without a leading kind word: mmproj, dspark, dflash, eagle, eagle3, draft, mtp (any case),
    # followed by '-', '_' or '.': "dspark-Alpha-8B" -> "Alpha-8B", "mmproj-F16" -> "F16", "Alpha-8B" -> "Alpha-8B"
def _owner(companion: dict, models: list[dict]) -> str | None
    # path of the model in `models` whose name shares the most leading tokens with _companion_stem(companion name);
    # ties go to the model whose name sorts first (case-insensitive); None when `models` is empty
# every entry from scan() gains "companion_of": str | None (None for models, and for companions with no model)
```

- [ ] **Step 1: Write the failing tests** (add to `LibraryTests` in `app/tests/test_models.py`, with
  `from pathlib import Path` at the top)

```python
    def test_each_companion_names_the_model_it_belongs_to(self):
        d = ROOT / "models" / "gguf" / "org" / "nest"
        llm(d / "Alpha-8B-Q4_K_M.gguf")
        llm(d / "Beta-3B-Q8_0.gguf")
        write_gguf(d / "mmproj-Beta-3B-f16.gguf", {"general.architecture": "clip", "general.type": "mmproj"})
        write_gguf(d / "dspark-Alpha-8B.gguf", {"general.architecture": "dflash", "general.name": "Alpha DSpark",
                                                "dflash.has_confidence_head": True})
        write_gguf(d / "mmproj-F16.gguf", {"general.architecture": "clip", "general.type": "mmproj"})
        by = {m["name"]: m for m in models.scan() if Path(m["path"]).parent.name == "nest"}
        self.assertIsNone(by["Alpha-8B-Q4_K_M"]["companion_of"])
        self.assertIsNone(by["Beta-3B-Q8_0"]["companion_of"])
        self.assertEqual(by["mmproj-Beta-3B-f16"]["companion_of"], by["Beta-3B-Q8_0"]["path"])
        self.assertEqual(by["dspark-Alpha-8B"]["companion_of"], by["Alpha-8B-Q4_K_M"]["path"])  # "dspark-" ignored
        self.assertEqual(by["mmproj-F16"]["companion_of"], by["Alpha-8B-Q4_K_M"]["path"])  # no match: first by name

    def test_a_companion_alone_in_its_folder_belongs_to_nothing(self):
        write_gguf(ROOT / "models" / "gguf" / "org" / "lonely" / "mmproj-Solo-f16.gguf",
                   {"general.architecture": "clip", "general.type": "mmproj"})
        solo = next(m for m in models.scan() if m["name"] == "mmproj-Solo-f16")
        self.assertTrue(solo["is_projector"])
        self.assertIsNone(solo["companion_of"])

    def test_the_stem_drops_only_a_leading_kind_word(self):
        self.assertEqual(models._companion_stem("dspark-Alpha-8B"), "Alpha-8B")
        self.assertEqual(models._companion_stem("MMPROJ_Beta"), "Beta")
        self.assertEqual(models._companion_stem("eagle3-Gamma"), "Gamma")
        self.assertEqual(models._companion_stem("Alpha-draft-Q4"), "Alpha-draft-Q4")
        self.assertEqual(models._companion_stem("mmproj"), "mmproj")  # nothing after it: unchanged
```

- [ ] **Step 2: Run to see them fail**

Run: `cd app && ../.venv/bin/python -m unittest tests.test_models -v` (or the Docker command in
[README.md](README.md) with `tests.test_models`)
Expected: `KeyError: 'companion_of'` and `AttributeError: … has no attribute '_companion_stem'`.

- [ ] **Step 3: Implement**

- `_companion_stem`: one `re.sub` with `^(?:mmproj|dspark|dflash|eagle3?|draft|mtp)[-_.](?=.)` and `re.I`. The
  `(?=.)` keeps a bare `mmproj` as it is.
- `_owner`: score each model with `_shared_prefix(_companion_stem(companion["name"]), model["name"])`, then pick
  the best with `min(models, key=lambda m: (-score(m), m["name"].lower()))`. Return its `"path"`, or `None` for an
  empty list.
- In `scan()`, inside the per-folder loop, right after the `for e in entries:` loop (line 136-140), add:
  `e["companion_of"] = None` for every model, and `c["companion_of"] = _owner(c, entries)` for every `c` in
  `projectors + drafts`.

- [ ] **Step 4: Run to see them pass**: `tests.test_models`, then the whole suite. `test_api` and `test_tuner`
  call `scan()` too.

- [ ] **Step 5: Commit**

```bash
git add app/kiln/models.py app/tests/test_models.py
git commit -m "Library: say which model each projector and draft file belongs to"
```

---

### Task 2: Turn the flat list into nested, sorted, filtered rows

**Files:**
- Create: `web/js/library.js`
- Test: `web/test/library.test.mjs`

**Interfaces (Produces):**

```js
// Each top-level entry (models, and companions without a model) with `children`: its companions, by name.
// Top-level order is the input order. Entries are copied, never mutated.
export function nestLibrary(models)        // -> [{ ...model, children: [model, ...] }]

// The rows to render, in order. Each row is the model object plus three fields:
//   depth: 0 (top level) or 1 (a companion), childCount: how many companions it has, open: shown expanded.
// sort: { key, direction: 'asc'|'desc' } on a model field (name and strings compare case-insensitively;
//   null/undefined last in both directions; ties keep input order). Children always follow their parent, by name.
// filter: text matched case-insensitively against `${name} ${repo} ${arch}`. A parent that matches shows (its
//   children only when open). A parent that doesn't match but has matching children shows opened, with only those.
// expanded: a Set of parent paths the user opened.
export function libraryRows(groups, { sort, filter, expanded })   // -> [{ ...model, depth, childCount, open }]
```

- [ ] **Step 1: Write the failing tests** in `web/test/library.test.mjs`

```js
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
  assert.equal(lib[0].children, undefined); // the input is not changed
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
  assert.deepEqual(names(libraryRows(nestLibrary(lib), opts({ filter: 'DSPARK' }))), ['Alpha-Q4', '  dspark-Alpha']);
  assert.equal(libraryRows(nestLibrary(lib), opts({ filter: 'dspark' }))[0].open, true);
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
```

- [ ] **Step 2: Run to see them fail**: `node --test web/test/` → `Cannot find module '…/library.js'`.

- [ ] **Step 3: Implement** both functions. Notes:
  - `nestLibrary`: build a `Map` from path to a copy with `children: []` for every entry whose `companion_of`
    is `null`. Then push each companion into its parent's `children`. A companion whose parent isn't in the list
    (shouldn't happen) becomes top-level too. Then sort each `children` by `name` (case-insensitive).
  - `libraryRows`: filter, then sort a copy of the top-level list (`Array.prototype.sort` is stable, so ties keep
    input order), then emit each parent followed by its children when open. For the comparison, `null`/`undefined`
    go last *before* the direction is applied; strings compare with `toLowerCase()`.

- [ ] **Step 4: Run to see them pass**: `node --test web/test/`.

- [ ] **Step 5: Commit**: `git commit -m "Library: nest companion files under their model, with sorting and filtering"`

---

### Task 3: The Library card

**Files:**
- Modify: `web/js/pages/models.js:148-202` (`LibraryCard`) and its imports (line 2-6)
- Modify: `web/css/app.css` (the misc block, ~line 178)

**Interfaces:**
- Consumes: `nestLibrary`, `libraryRows` from Task 2; `companion_of` from Task 1.

- [ ] **Step 1: Implement**

1. State in `LibraryCard`:
   - `const [sort, setSort] = useState({ key: 'mtime', direction: 'desc' });`
   - `const [expanded, setExpanded] = useState(() => new Set());` and a `toggle(path)` that makes a **new**
     `Set` (React only re-renders when the state object changes).
   - `const [grouped, setGrouped] = useState(() => localStorage.getItem('kiln-library-grouped') !== 'false');`
     with a `useEffect` that saves it. Wrap both `localStorage` calls in `try { … } catch {}`, as
     `web/js/pages/audio.js:14-16` and `:115` do (private windows can throw).
2. Replace the `shown` `useMemo` with:
   `const rows = useMemo(() => libraryRows(grouped ? nestLibrary(models) : models.map((m) => ({ ...m, children: [] })), { sort, filter, expanded }), [models, grouped, sort, filter, expanded]);`
3. The `E.Table`: `rows=${rows}`, `sort=${sort}`, `onSortChange=${setSort}`. Remove `defaultSort`. Passing `sort`
   switches the table to controlled mode, so it won't re-sort our rows and pull children away from their parents.
4. The **Model** column's render. Every other column's render is unchanged, because rows are still model-shaped:
   - Wrap today's content in `<div className=${r.depth ? 'lib-name lib-child' : 'lib-name'}>`.
   - Before the name, for a top-level row with `r.childCount > 0`:
     `` html`<${E.IconButton} icon=${r.open ? 'chevron-down' : 'chevron-right'} size="sm" label=${(r.open ? 'Hide' : 'Show') + ` the files that go with ${r.name}`} aria-expanded=${r.open} onClick=${() => toggle(r.path)} />` ``.
     For a top-level row without children, `<span className="lib-toggle-space" aria-hidden="true"></span>` so the
     names line up. Child rows get neither. Their indent comes from `.lib-child`.
   - Keep the existing `+ vision projector · + DSpark file` sub-line. It's the summary of what's folded.
5. The card description when `grouped` counts over the whole library (like today's, it ignores the filter):
   entries that are neither `is_projector` nor `is_draft` are models, the rest are companion files:
   `` `${fmtInt(nModels)} models · ${fmtInt(nCompanions)} companion files · ${fmtBytes(total)}` ``. When flat, keep
   today's `${n} files · ${size}`.
6. Card actions: before the filter field, an `E.SegmentedControl` with `size="sm"`, `label="Library view"`,
   `value=${grouped ? 'grouped' : 'flat'}`, `onChange=${(v) => setGrouped(v === 'grouped')}` and options
   `[{ value: 'grouped', label: 'Grouped' }, { value: 'flat', label: 'All files' }]`.
7. CSS in `web/css/app.css`:

   ```css
   .lib-name { display: flex; align-items: flex-start; gap: var(--space-2); }
   .lib-toggle-space { flex: none; width: var(--control-sm); }
   .lib-child { margin-left: calc(var(--control-sm) / 2); padding-left: calc(var(--control-sm) / 2 + var(--space-2)); border-left: 2px solid var(--line); }
   ```

   Check that `--control-sm` exists in `web/css/tokens.css` (`app.css:144` already uses it). If the chevron
   button turns out a different width, size the spacer to match.

- [ ] **Step 2: Verify by hand**

Run Kiln against the fake server (see [README.md](README.md). Its seed puts two models, a projector and a
DSpark file in one folder). On the Models page:
1. The Library shows `Demo-8B-Q4_K_M` and `Demo-8B-Q8_0` (and `Demo-MTP-Q4_K_M`), but not the `mmproj` or
   `dspark` files. The description says `3 models · 2 companion files · …`.
2. `Demo-8B-Q4_K_M` has a chevron (both Demo-8B quants match the companions equally well, so the one whose name
   sorts first gets them). Press it: `dspark-Demo-8B` and `mmproj-Demo-8B-f16` appear under it, indented. Press it
   again and they're gone.
3. Sort by Size and by Added, both directions: the companions stay under their model.
4. Type `mmproj` in the filter: the model shows opened, with only the projector. Clear the filter.
5. Switch to **All files**: today's flat list of every file. Reload the page: it stays on All files. Switch back.
6. Copy path and Delete work on a companion row (delete the projector, check it's gone from disk and the list).
7. The Presets editor's model picker and the Tuner's model list still list what they did before.
8. Keyboard: Tab to a chevron, press Enter or Space, and it toggles. A screen reader announces expanded/collapsed
   (`aria-expanded`).

- [ ] **Step 3: Commit**: `git commit -m "Models page: show companion files under their model by default"`

---

### Task 4: Docs

- [ ] `docs/GUIDE.md`, **Library** paragraph of the Models section: models are listed with their projector and
  draft files folded under them, the chevron, how the filter treats companions, and Grouped / All files.
- [ ] `docs/API.md`: add `companion_of` (path of the model a projector or draft file belongs to, or `null`) where
  the `GET /api/models` entry fields are described.
- [ ] Commit: `git commit -m "Docs: the nested model library"`

## Done when

- [ ] All Python tests and `node --test web/test/` pass.
- [ ] Every step of Task 3's manual check works.
- [ ] Docs updated.
