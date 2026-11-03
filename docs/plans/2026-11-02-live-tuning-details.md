# Live tuning details: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** While a tuning run is going, show what is happening *inside* the current candidate (loading, warm-up,
which benchmark prompt, the speeds it just got, the llama-server output) plus the best result so far, a time
estimate and the GPU's readings, all updating live.

**Architecture:** The tuner already knows all of this but only tells the browser when a candidate starts and when
it finishes. Add a small, frequent `tune_live` server-sent event carrying `Tuner.live()` with new fields (phase,
step, last request's speeds, log tail, when the candidate started). The page combines it with data it already has:
the finished trials (for best-so-far and the average time per candidate) and the existing `gpu` event.

**Tech stack:** Python/asyncio (backend), React 18 + `htm` + Ember components (frontend), `node --test` for the
pure functions in `web/js/tuneplan.js`.

**Spec:** the user's request: "when tuning jobs are running I want more live details". Everything below is how
this plan reads it.

## What the user sees when this is done

The running card on the Tuner page, top to bottom:

1. The progress bar, as today.
2. **Now measuring**: the candidate's flags (wrapping, never cut off), then a line such as
   `Short prompt 2 of 3 · 41 s on this candidate`, then, once a request has finished,
   `Last request: 63.2 t/s generation · 701 t/s prompt`.
3. A row of facts: **Best so far** (`+12.4%` with the candidate number, the full label as a tooltip),
   **Best decode** (`63.7 t/s`), **Measured** (`23 · 5 pruned`), **Per candidate** (average time, `1m 12s`),
   **Time left** (`about 18 min`, or `at least 18 min` while some stage's size isn't known yet), and **GPU**
   (`21.3 GiB of 24.0 GiB · 312 W · 98% busy`, hidden on a box without GPU readings).
4. The stage tracker, as today.
5. A collapsed **llama-server output** disclosure with the last 8 lines of the candidate's output, updating
   about once a second.

The phase line and log update at least once a second while a candidate loads. Everything else updates when a
candidate finishes.

## Global constraints

- **The benchmark itself must not change.** Same requests, same order, same timing brackets (`t0`/`t1` in
  `_measure`). Publishing an event is a synchronous, non-blocking queue put (`events.publish`). Never `await`
  anything new between the timed requests.
- The new event is throttled to **at most once per second** for log lines (`LIVE_S = 1.0`). Phase changes are
  sent immediately. The existing `tune` event (the whole run row with every trial) keeps firing only at
  candidate boundaries. It can be large, so don't send it more often.
- `/api/status` and `/api/quick` (`tuner.status()`) stay as they are. New fields go in `live()` only.
- No new dependencies.

## Review focus

1. **A run with no GPU readings** (the fake server, a CPU box): the GPU fact is hidden, not `NaN` or `undefined`
   (`timeLeft`/`gpuText` tests, and by hand).
2. **The first candidate, before anything has finished**: best-so-far shows `–`, time left shows `–` (there's
   no average yet), and nothing throws (`bestSoFar([])`, `timeLeft(..., [])` tests).
3. **A probe** (the context objective's load-only checks) has no prompts: its phase is `check` after loading,
   and it never shows a "Last request" line (`test_a_probe_reports_load_then_check`).
4. **Pruned trials** have no time. They must not drag the average down (`timeLeft` test with pruned rows).
5. **After the run** the live fields are cleared, so the next run doesn't show the old log for a moment
   (`test_live_is_cleared_after_the_run`).

## How the code works today (read this first)

- `app/kiln/tuner.py:123-128`: `status()` returns `{running, tune_id, stage, trial}`, and `live()` adds `plan`.
- `app/kiln/tuner.py:220-254`: inside `_run`, the closures `trial()` (a benchmark) and `probe()` (load-only) set
  `self.stage`/`self.trial_label`, call `_measure`/`_probe`, append the result to `trials`, and `_publish()`.
- `app/kiln/tuner.py:602-662`: `_measure` launches llama-server (`_launch`, line 714), runs one warm-up
  request, three short prompts (line 634, a list comprehension) and two deep prompts (line 635), each through
  `_complete` (line 700), which returns llama-server's `timings` dict (`predicted_per_second`,
  `prompt_per_second`, …).
- `app/kiln/tuner.py:738-750`: `_read()` reads the server's output line by line into `self.log` (a deque of 400).
- `app/kiln/tuner.py:806-812`: `_publish()` sends the `tune` event (the whole run row plus `live`).
- `web/js/lib.js:41`: the browser subscribes to a **fixed list** of event names. A new name must be added there.
- `web/js/tuneplan.js`: pure functions behind the stage tracker (`stageStates`, `stageCount`, `stageFill`),
  tested in `web/test/tuneplan.test.mjs`. **It imports nothing**, so Node can test it without a browser. Keep
  it that way: don't import `lib.js` there (it needs React on `window`).
- `web/js/pages/tuner.js:250-265`: `RunningCard`, the card to extend.
- The `gpu` event (`app/kiln/gpu.py:189`) already arrives every 2 s with
  `{t, name, busy, vram_used, vram_total, power_w, temp_c, sclk_mhz}` (bytes, watts, percent). Any field can be
  `null`. `web/js/pages/overview.js:295` listens to it.

## File map

| File | Change |
|---|---|
| `app/kiln/tuner.py` | Live state, `_set_phase`, `_publish_live`, `elapsed_s` on trials, phases in `_launch`/`_measure`/`_probe`, throttled publish in `_read` |
| `app/tests/test_tuner.py` | New `LiveTests`; update the `live()` key set at line 287 |
| `web/js/lib.js` | Subscribe to `tune_live` |
| `web/js/tuneplan.js` | `phaseText`, `bestSoFar`, `timeLeft`, `gpuText` |
| `web/test/tuneplan.test.mjs` | Tests for those four |
| `web/js/pages/tuner.js` | `RunningCard` shows the new details. `TunerPage` listens to `tune_live` |
| `web/css/app.css` | A few classes for the "Now measuring" block and the log |
| `docs/GUIDE.md`, `docs/API.md` | Describe the new display, event and fields |

---

### Task 1: Report the phase, speeds and log while a candidate runs

**Files:**
- Modify: `app/kiln/tuner.py` (constants near line 44-49; `Tuner.__init__` 104-115; `live()` 126-128; `trial()`
  and `probe()` closures 220-254; `_run`'s `finally` 380-387; `_measure` 602-662; `_probe` 664-691;
  `_complete` 700-712; `_launch` 714-736; `_read` 738-750)
- Test: `app/tests/test_tuner.py`

**Interfaces (Produces):**

```python
LIVE_S = 1.0   # log lines refresh the live display at most this often
LOG_TAIL = 8   # lines of llama-server output in live()

# Tuner.live() returns status() plus:
{
  "plan": [...],                        # unchanged
  "trial_started_at": float | None,     # time.time() when the current candidate started loading
  "phase": str,                         # "" | "load" | "warmup" | "short" | "deep" | "check"
  "step": int, "steps": int,            # e.g. 2 and 3 for the second of three short prompts; 0 and 0 otherwise
  "last": {"phase": str, "decode_tps": float | None, "prefill_tps": float | None} | None,
                                        # the most recent finished request of this candidate
  "log": list[str],                     # last LOG_TAIL lines of this candidate's output; [] when not running
}

def _set_phase(self, phase: str, step: int = 0, steps: int = 0) -> None   # sets the three fields, publishes now
def _publish_live(self, force: bool = True) -> None   # events.publish("tune_live", self.live()); when not
                                                       # force, at most once per LIVE_S (time.monotonic())
# every trial dict that ran (benchmark or probe) gains "elapsed_s": float (seconds, rounded to 0.1)
```

- [ ] **Step 1: Write the failing tests**

```python
class LiveTests(Case):
    """The live view of a running candidate: phase by phase, with speeds and the log."""

    async def run_and_watch(self, model, **params):
        seen = []
        real = tuner_mod.events.publish
        def spy(kind, data):
            if kind == "tune_live":
                seen.append(json.loads(json.dumps(data)))  # a copy, as the browser would get it
            real(kind, data)
        with mock.patch.object(tuner_mod.events, "publish", side_effect=spy):
            t = await self.tune(model, **params)
        self.assertEqual(t["status"], "done", t.get("error"))
        return t, seen

    @staticmethod
    def phases(seen):
        """(phase, step) in order, without repeats."""
        out = []
        for d in seen:
            p = (d["phase"], d["step"])
            if not out or out[-1] != p:
                out.append(p)
        return out

    async def test_a_benchmark_reports_each_phase_in_order(self):
        t, seen = await self.run_and_watch(llm(DIR / "live" / "plain-Q4.gguf"), mode="quick", speculative=False)
        self.assertEqual(self.phases(seen)[:7], [("load", 0), ("warmup", 0), ("short", 1), ("short", 2),
                                                 ("short", 3), ("deep", 1), ("deep", 2)])
        short2 = next(d for d in seen if d["phase"] == "short" and d["step"] == 2)
        self.assertEqual(short2["steps"], 3)
        self.assertEqual(short2["last"]["phase"], "short")  # the first short prompt's speeds
        self.assertGreater(short2["last"]["decode_tps"], 0)
        self.assertIsNotNone(short2["trial_started_at"])

    async def test_the_log_tail_is_short_and_comes_from_this_candidate(self):
        t, seen = await self.run_and_watch(llm(DIR / "live" / "log-Q4.gguf"), mode="quick", speculative=False)
        logs = [d["log"] for d in seen if d["log"]]
        self.assertTrue(logs)
        self.assertTrue(all(len(lines) <= tuner_mod.LOG_TAIL for lines in logs))
        self.assertTrue(any("fake llama-server" in line for lines in logs for line in lines))

    async def test_a_probe_reports_load_then_check(self):
        model = llm(DIR / "live" / "moe-Q4.gguf", arch="qwen35moe", layers=8, **{"qwen35moe.expert_count": 8})
        t, seen = await self.run_and_watch(model, objective="context")
        self.assertIn(("check", 0), self.phases(seen))
        self.assertTrue(all(d["last"] is None for d in seen if d["phase"] == "check"))

    async def test_trials_record_how_long_they_took(self):
        t = await self.tune(llm(DIR / "live" / "time-Q4.gguf"), objective="decode")
        ran = [x for x in t["trials"] if not x.get("pruned")]
        self.assertTrue(ran)
        self.assertTrue(all(isinstance(x["elapsed_s"], float) and x["elapsed_s"] >= 0 for x in ran))
        self.assertTrue(all("elapsed_s" not in x for x in t["trials"] if x.get("pruned")))

    async def test_live_is_cleared_after_the_run(self):
        await self.tune(llm(DIR / "live" / "after-Q4.gguf"), mode="quick", speculative=False)
        live = tuner.live()
        self.assertEqual((live["phase"], live["step"], live["steps"], live["last"], live["trial_started_at"], live["log"]),
                         ("", 0, 0, None, None, []))
```

Update `app/tests/test_tuner.py:287`, which lists `live()`'s keys, to the new set:
`{"running", "tune_id", "stage", "trial", "plan", "trial_started_at", "phase", "step", "steps", "last", "log"}`
(plus `"queued"` if the [tuning queue](2026-11-02-tuning-queue.md) has already been merged). Keep line 288
(`assertNotIn("plan", tuner.status())`) and add `self.assertNotIn("log", tuner.status())`.

- [ ] **Step 2: Run them to see them fail**

Run: `cd app && ../.venv/bin/python -m unittest tests.test_tuner.LiveTests -v`
Expected: `KeyError: 'phase'` (and similar) from the assertions.

- [ ] **Step 3: Implement**

1. Constants `LIVE_S` and `LOG_TAIL` next to `POLL_S` (line 47). In `__init__`: `self.trial_started_at = None`,
   `self.phase = ""`, `self.step = self.steps = 0`, `self.last = None`, `self._live_at = 0.0`.
2. `live()` returns `{**self.status(), "plan": self.plan, "trial_started_at": …, "phase": …, "step": …,
   "steps": …, "last": …, "log": list(self.log)[-LOG_TAIL:] if self.running else []}`.
3. `_set_phase()` sets `phase/step/steps` and calls `self._publish_live()`. `_publish_live(force)` returns early
   when `self.tune_id is None`. When `force` is false, it also returns early if less than `LIVE_S` has passed
   since `self._live_at`. Otherwise it records `self._live_at = time.monotonic()` and calls
   `events.publish("tune_live", self.live())`.
4. **Where each phase starts:**
   - `_launch()` (line 714): `self._set_phase("load")` right **after** `self.log.clear()` and
     `self.threads_used = None` (its first two lines). Earlier, and the "load" update would still carry the
     previous candidate's log. Both benchmarks and probes go through `_launch`.
   - `_measure()`: `self._set_phase("warmup")` right before the warm-up `_complete` (line 632). Rewrite the
     short-prompt list comprehension (line 634) as a `for i, pr in enumerate(SHORT_PROMPTS, 1):` loop that calls
     `self._set_phase("short", i, len(SHORT_PROMPTS))` and then appends `await self._complete(...)` to `short`.
     Do the same for the deep prompts (line 635) with `"deep"` and `DEEP_RUNS`. **Keep `t0 = time.time()`
     before the short loop and `t1 = time.time()` after the deep loop**, exactly where they are.
   - `_probe()`: `self._set_phase("check")` right after `_launch` returns.
5. `_complete()` (line 700): after the timings check passes and before returning `t`, set
   `self.last = {"phase": self.phase, "decode_tps": t.get("predicted_per_second"), "prefill_tps": t.get("prompt_per_second")}`.
   You don't need another publish there, because the next `_set_phase` publishes it. (For the last deep prompt,
   the `tune` event at the end of the candidate carries it.)
6. In the `trial()` closure (line 220), right before `t.update(await self._measure(c, p))`: record
   `started = time.time()`, set `self.trial_started_at = started` and `self.last = None`. After it:
   `t["elapsed_s"] = round(time.time() - started, 1)`. Do the same in `probe()` around `self._probe(c, p)`. Leave
   `skip()` alone: pruned rows get no `elapsed_s`.
7. `_read()` (line 738): after `self.log.append(line)`, call `self._publish_live(force=False)`.
8. `_run`'s `finally:` (line 380): next to `self.stage, self.trial_label = "", ""`, reset `phase`, `step`, `steps`,
   `last` and `trial_started_at`.

- [ ] **Step 4: Run the tests to see them pass**

Run: `cd app && ../.venv/bin/python -m unittest tests.test_tuner -v`. All pass, including the existing ones.

- [ ] **Step 5: Commit**

```bash
git add app/kiln/tuner.py app/tests/test_tuner.py
git commit -m "Tuner: publish the phase, last speeds and log tail of the running candidate"
```

---

### Task 2: Pure helpers for the live display

**Files:**
- Modify: `web/js/tuneplan.js` (append)
- Test: `web/test/tuneplan.test.mjs` (append)

**Interfaces (Produces):**

```js
// The phase line, without the time: 'Loading the model' | 'Warm-up' | 'Short prompt 2 of 3' |
// 'Deep prompt (8,192 tokens) 1 of 2' | 'Checking free VRAM' | 'Starting' (no phase yet).
export function phaseText(live, depth)
// The ok trial with the highest score (ties: the earlier n) and its gain over the first ok trial, or null.
export function bestSoFar(trials)            // -> { trial, gain } | null   (gain: 0.124 for +12.4%)
// Rough time left: remaining trials of running/upcoming stages x the mean elapsed_s of trials that ran.
export function timeLeft(steps, trials)      // steps: stageStates(...) output -> { seconds, atLeast } | null
// 'VRAM 21.3 GiB of 24.0 GiB · 312 W · 98% busy', leaving out what's null; '' when there is nothing.
export function gpuText(sample, fmtBytes)    // fmtBytes passed in, so this file still imports nothing
```

- [ ] **Step 1: Write the failing tests** (append to `web/test/tuneplan.test.mjs` and add the four names to its import from `../js/tuneplan.js`)

```js
test('phaseText names each phase', () => {
  assert.equal(phaseText({ phase: 'load' }, 8192), 'Loading the model');
  assert.equal(phaseText({ phase: 'warmup' }, 8192), 'Warm-up');
  assert.equal(phaseText({ phase: 'short', step: 2, steps: 3 }, 8192), 'Short prompt 2 of 3');
  assert.equal(phaseText({ phase: 'deep', step: 1, steps: 2 }, 8192), 'Deep prompt (8,192 tokens) 1 of 2');
  assert.equal(phaseText({ phase: 'check' }, 8192), 'Checking free VRAM');
  assert.equal(phaseText({ phase: '' }, 8192), 'Starting');
  assert.equal(phaseText(null, 8192), 'Starting');
});

test('bestSoFar picks the top score, earlier on a tie, and compares with the first fit', () => {
  const trials = [
    { n: 1, ok: false }, { n: 2, ok: true, score: 50 }, { n: 3, ok: true, score: 60 }, { n: 4, ok: true, score: 60 },
  ];
  const b = bestSoFar(trials);
  assert.equal(b.trial.n, 3);
  assert.ok(Math.abs(b.gain - 0.2) < 1e-9);
  assert.equal(bestSoFar([]), null);
  assert.equal(bestSoFar([{ n: 1, ok: false }]), null);
});

test('timeLeft multiplies what is left by the average, ignoring pruned rows', () => {
  const steps = [
    { state: 'done', n: 2, max: 2 },
    { state: 'running', n: 1, max: 4 },
    { state: 'upcoming', n: 0, max: 2 },
  ];
  const trials = [{ elapsed_s: 10 }, { elapsed_s: 20 }, { pruned: true }, { elapsed_s: 30 }];
  assert.deepEqual(timeLeft(steps, trials), { seconds: 5 * 20, atLeast: false });
  assert.deepEqual(timeLeft([...steps, { state: 'upcoming', n: 0, max: null }], trials), { seconds: 100, atLeast: true });
  assert.equal(timeLeft(steps, []), null);          // no average yet
  assert.equal(timeLeft(steps, [{ pruned: true }]), null);
});

test('gpuText leaves out what the GPU does not report', () => {
  const fmt = (b) => `${(b / 2 ** 30).toFixed(1)} GiB`;
  assert.equal(gpuText({ vram_used: 2 ** 30, vram_total: 4 * 2 ** 30, power_w: 312.4, busy: 98 }, fmt), 'VRAM 1.0 GiB of 4.0 GiB · 312 W · 98% busy');
  assert.equal(gpuText({ vram_used: null, vram_total: null, power_w: 80, busy: null }, fmt), '80 W');
  assert.equal(gpuText(null, fmt), '');
});
```

- [ ] **Step 2: Run to see them fail**: `node --test web/test/` → `SyntaxError: ... does not provide an export named 'phaseText'`.

- [ ] **Step 3: Implement** the four functions in `web/js/tuneplan.js`, each with a one-line comment like the
  existing ones. Format the depth with `Number(depth).toLocaleString('en-US')`. In `timeLeft`, "what's left" is,
  for each step whose `state` is `running` or `upcoming`, `Math.max(0, max - n)`, with `atLeast: true` as soon as
  one of those has `max == null`. Watts are rounded to whole numbers.

- [ ] **Step 4: Run to see them pass**: `node --test web/test/`, all green.

- [ ] **Step 5: Commit**: `git commit -m "Tuner page: helpers for the live phase, best so far, time left and GPU line"`

---

### Task 3: Show it on the running card

**Files:**
- Modify: `web/js/lib.js:41` (event list)
- Modify: `web/js/pages/tuner.js` (`TunerPage` 55-82, `RunningCard` 250-265; imports at line 2 and 5)
- Modify: `web/css/app.css` (after the stage tracker block, ~line 283)

**Interfaces:**
- Consumes: `live` fields from Task 1; `phaseText`, `bestSoFar`, `timeLeft`, `gpuText`, `stageStates` from `tuneplan.js`.

- [ ] **Step 1: Implement**

1. `web/js/lib.js:41`: add `'tune_live'` to the array of event kinds.
2. `TunerPage`: `useEvent('tune_live', (l) => setLive(l));`. That's all it needs: `live` already flows into `RunningCard`.
3. `RunningCard`:
   - `const [gpu, setGpu] = useState(null); useEvent('gpu', setGpu);`
   - `const trials = tune ? tune.trials || [] : [];` and `const steps = stageStates(live.plan, trials, live.stage);`
   - Under the `E.Progress`, a block `<div className="tune-now">` with: `<p className="text-label">Now measuring</p>`,
     `<p className="text-code tune-now__label">${live.trial || '…'}</p>`, a caption with
     `phaseText(live, tune && tune.params.depth)` and, when `live.trial_started_at` is set,
     `` ` · ${fmtDuration(now - live.trial_started_at)} on this candidate` ``, and, when `live.last` is set, a caption
     `Last request: ${fmtTps(live.last.decode_tps)} t/s generation · ${fmtTps(live.last.prefill_tps)} t/s prompt`.
   - A `<dl className="facts facts--tight">` with the facts listed under "What the user sees", in that order.
     Each one is a `<div><dt className="text-caption">…</dt><dd className="text-subtitle">…</dd></div>`, as in the
     winner card (`tuner.js:326-334`). Use `fmtTps`, `fmtInt`, `fmtDuration`, and `'–'` when a value is missing.
     Show the gain as the table does (`+12.4%`). Render the GPU fact only when `gpuText(gpu, fmtBytes)` isn't empty.
     "Time left" reads `about ${fmtDuration(x.seconds)}` or `at least ${…}`.
   - Below the `StageTracker`, a `<details className="tune-log">` with
     `<summary className="text-label">llama-server output</summary>` and
     `<pre className="code text-code">${(live.log || []).join('\n') || 'Nothing yet.'}</pre>`. `.code` already wraps
     long lines (`app.css:140`), so the log never scrolls sideways.
4. `web/css/app.css`: `.tune-now { display: flex; flex-direction: column; gap: var(--space-1); }`,
   `.tune-now__label { color: var(--ink); overflow-wrap: anywhere; }`,
   `.tune-log > summary { cursor: pointer; color: var(--ink-muted); }`,
   `.tune-log .code { margin-top: var(--space-2); max-height: 12rem; overflow-y: auto; }`.
   Use only the existing CSS variables (`web/css/tokens.css`). Don't edit `tokens.css`: it is generated.

- [ ] **Step 2: Verify by hand**

Run Kiln against the fake server with `FAKE_LOAD_S=3` (see [README.md](README.md)) and start a run:
1. The phase line goes Loading the model → Warm-up → Short prompt 1 of 3 … → Deep prompt … 2 of 2, and the
   seconds count up.
2. "Last request" appears after the warm-up and changes with each prompt.
3. Open **llama-server output**: lines appear while the model "loads", at most a second late.
4. Best so far, Measured and Per candidate change after each candidate. Time left appears after the first one.
5. The fake server has no GPU readings, so the GPU fact isn't shown. Check that it doesn't say "undefined".
6. Narrow the window to a phone width: nothing overflows the card sideways.
7. No errors in the browser console.

- [ ] **Step 3: Commit**: `git commit -m "Tuner page: live details of the candidate being measured"`

---

### Task 4: Docs

- [ ] `docs/GUIDE.md`, "While it runs" paragraph in the Tuner section: describe the Now measuring block, the
  facts and the output disclosure.
- [ ] `docs/API.md`: add a `tune_live` row to the events table (data: the `live` object; when: each phase change
  and, while output arrives, up to once a second). List the new `live` fields where the `tune` status is described
  (~line 177). Mention `elapsed_s` on trials in the `GET /api/tune` row.
- [ ] Commit: `git commit -m "Docs: live tuning details"`

## Done when

- [ ] Python tests and `node --test web/test/` pass.
- [ ] Every point under "What the user sees when this is done" works against the fake server.
- [ ] Docs updated.
