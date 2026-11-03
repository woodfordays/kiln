# Tuning queue: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user queue tuning runs. Starting a run while another one is going adds it to a queue
instead of refusing it, and queued runs start one after another without anyone at the keyboard.

**Architecture:** A queued run is a row in the existing `tunes` table with `status = 'queued'`. `Tuner.start()`
validates the request, inserts the row and starts it right away only if nothing is running. When a run
finishes, `Tuner._run()` starts the oldest queued row. The models that were running before the *first* run of a
batch are stopped once and brought back once, after the *last* one, not between runs.

**Tech stack:** Python 3 / asyncio / FastAPI / SQLite (backend), React 18 + `htm` + the Ember components in
`web/vendor/ember.js` (frontend, no build step). Tests: `unittest` with the fake `llama-server`.

**Spec:** the user's request: "I want to be able to queue tuning jobs." Everything below is how this plan reads
it. If you disagree with a decision, raise it in the PR. Don't silently change it.

## What the user sees when this is done

1. Idle Tuner page: same as today (form, last result, past runs).
2. While a run is going, the page shows, top to bottom: the running card, a **Queue** card (only if anything is
   queued), the live candidates table, then the form, now titled **Queue another run** with an **Add to queue**
   button, then **Past runs**.
3. Pressing **Add to queue** shows a toast "Added to the queue" with its position. The run appears in the Queue
   card with its model, context, objective, search mode and a rough time estimate, and a remove (×) button.
4. When the running run finishes (done, failed or cancelled), the next queued run starts within a second.
5. **Cancel** on the running card cancels only that run. The queue goes on. With anything queued, a second
   button **Cancel all** empties the queue and then cancels the running run.
6. The models that were running before the first run come back once, after the queue is empty. Every queued
   run that asked to "start the winning preset when done" gets its winner started at that point too.
7. A restart of the container while runs are queued: the run that was going is marked *interrupted* (as today),
   and the queued runs start after the boot presets have started.
8. `POST /api/tune` never answers `409 already in progress` any more. It answers `{id, status, position}`.

## Global constraints

- Only one tuning run uses the GPU at a time. The queue changes *when* runs start, nothing about *how* they run.
- `POST /api/tune` keeps its request body exactly (see `TuneBody` in `app/kiln/api.py:634-648`). The response
  gains fields. It loses none.
- Validation errors (missing model, context under 512, too many candidates, …) are still returned by
  `POST /api/tune` *at the moment of queueing*, as `400`, not discovered later when the run starts.
- No new dependencies. No new tables; one new column (`tunes.started_at`).
- Order is first in, first out by `tunes.id`. Reordering queued runs and editing them are **out of scope**.

## Review focus

The cases most likely to break, which the tests below pin:

1. **Two `start()` calls at almost the same time while idle** must not start the same row twice or run two
   tuners at once. Nothing may `await` between "pick the next queued row" and "create its task"
   (`test_two_starts_at_once_run_one_after_the_other`).
2. **Shutdown must not start the next queued run.** `__main__` cancels the running run when the container
   stops. A plain `cancel()` would let the next run start on a dying process (`test_shutdown_leaves_the_queue_for_the_next_boot`).
3. **The model file of a queued run deleted before it starts** gives that run status `failed` with
   "Model file not found", and the queue moves on (`test_a_queued_run_whose_model_was_deleted_fails_and_the_queue_goes_on`).
4. **The servers that were running before** are stopped once and restored once per batch, not once per run
   (`test_what_was_running_comes_back_once_after_the_whole_queue`).
5. **A run queued while the last one is restoring the previous servers** (a slow, awaited step) is still picked up
   (covered by the `while` loop in `_next`. See Task 2 step 3).

## How the code works today (read this first)

- `app/kiln/tuner.py:103-200`: `class Tuner`. `start(params)` (line 137) does three things in order:
  1. **Validates and fills in `params`** (lines 138-183): model exists, context ≥ 512, objective and mode are known,
     reads the model's metadata (`has_mtp`, `drafts`, `n_layers`, …), loads the base preset's flags, and runs
     `tunespace.check` (which refuses more than 4,096 candidates).
  2. **Takes the GPU** (lines 185-197): `manager.snapshot()` remembers what was running, `manager.blocked` is set
     so nothing else can start a model, and `manager.stop()` stops everything.
  3. **Starts the search** in a background task: `self.task = asyncio.create_task(self._run(...))` (line 198).
- `self.running` (line 120) is `True` while `self.task` exists and isn't done. `start()` raises
  `RuntimeError("A tuning run is already in progress.")` when it is. `api.py:81-83` turns `RuntimeError` into `409`.
- `Tuner._run()` (line 209) runs the search and, in its `finally:` block (lines 380-387), kills the trial
  process, clears `manager.blocked`, and calls `self._after(p, best)` (line 389), which starts the winner if
  `start_after` was ticked and then restores what was running before (`manager.restore(previous)`).
- `Tuner._publish()` (line 806) sends a `tune` server-sent event with the whole run row plus `live`.
- `db.init()` (`app/kiln/db.py`, last line of `init`) marks any `running` tune as `interrupted` on boot.
- The download queue in `app/kiln/hf.py:180-300` (`class Downloader`) is a working example of a queue in this
  codebase: rows with `status='queued'`, picked `ORDER BY id LIMIT 1`. Skim it.

## File map

| File | Change |
|---|---|
| `app/kiln/db.py` | Schema v4: `ALTER TABLE tunes ADD COLUMN started_at REAL;` |
| `app/kiln/tuner.py` | Split `start()` into `_prepare()` + `_begin()`; add `_next()`, `_end_batch()`, `remove()`, `clear_queue()`, `shutdown()`, `resume()`, `wait_idle()`; `status()` gains `queued`; `_after()` replaced by `_end_batch()` |
| `app/kiln/api.py` | `POST /api/tune` response; `GET /api/tune` gains `queue`; new `DELETE /api/tune/queue` and `DELETE /api/tune/{tid}` |
| `app/kiln/__main__.py` | `await tuner.resume()` at the end of `_autostart()`; `tuner.shutdown()` instead of `tuner.cancel()` |
| `app/tests/test_tuner.py` | New `QueueTests` and `QueueApiTests`; one existing assertion updated |
| `web/js/pages/tuner.js` | `QueueCard`, form in "queue" mode, Cancel all, page order, `started_at` |
| `web/js/notify.js` | Ignore removal events; better cancel message; `started_at` |
| `docs/GUIDE.md`, `docs/API.md` | Describe the queue |

---

### Task 1: Queue runs in the backend

**Files:**
- Modify: `app/kiln/db.py` (append to `SCHEMA`)
- Modify: `app/kiln/tuner.py:103-205` (class state, `start`, `cancel`), `:209-403` (`_run`'s `finally`, `_after`)
- Modify: `app/kiln/__main__.py:49-60` (`_autostart`) and `:99` (shutdown)
- Test: `app/tests/test_tuner.py` (new class `QueueTests`, and the existing line 287)

**Interfaces (Produces):**

```python
class Tuner:
    # new state, set in __init__
    _batch: dict | None   # {"previous": list[dict] | None, "winners": dict[str, int]} while a batch runs, else None
    _paused: bool         # True after shutdown(): nothing new starts

    def status(self) -> dict          # adds "queued": int (number of rows with status 'queued')
    async def start(self, params: dict) -> int   # validate, insert, start now if idle; returns the tune id
    def _prepare(self, params: dict) -> dict     # today's lines 138-183, unchanged; raises ValueError/FileNotFoundError
    def _begin(self, tid: int, p: dict) -> None  # NOT async: marks the row running and creates the task
    async def _next(self) -> None                # start the oldest queued row, or end the batch
    async def _end_batch(self) -> None           # unblock, start winners, restore previous, publish notes
    def remove(self, tid: int) -> None           # drop one queued row
    def clear_queue(self) -> int                 # drop every queued row; returns how many
    async def cancel(self) -> None               # unchanged: cancels the running run only
    async def shutdown(self) -> None             # pause, cancel, wait for the task
    async def resume(self) -> None               # unpause and start the next queued run if idle
    async def wait_idle(self) -> None            # returns when nothing runs (the queue drains first)
```

- [ ] **Step 1: Write the failing tests**

First, make every tuner test start from a clean tuner. The tuner is one shared object (`tuner = Tuner()` at the
bottom of `tuner.py`), so a queue test that fails half-way could leave it paused and break every test after it. Add
these lines at the end of `Case.asyncSetUp` (`app/tests/test_tuner.py:26-33`), and add
`from kiln.server import manager` to the imports at the top:

```python
        tuner._paused = False
        tuner._batch = None
        manager.blocked = None
```

Then add this class after `PlanTests`. It reuses `Case` (line 25), which also empties the `tunes` and `presets`
tables and makes the fake server load instantly.

```python
class QueueTests(Case):
    """Runs started while one is going wait in a queue and run in order."""

    def params(self, name, **kw):
        return {"ctx": 4096, "depth": 1024, "mode": "quick", "speculative": False,
                "model": llm(DIR / "queue" / name / f"{name}-Q4.gguf"), **kw}

    async def test_a_run_started_while_one_is_going_is_queued_and_runs_after_it(self):
        a = await tuner.start(self.params("a"))
        b = await tuner.start(self.params("b"))
        self.assertEqual((get_tune(a)["status"], get_tune(b)["status"]), ("running", "queued"))
        self.assertEqual(tuner.status()["queued"], 1)
        await tuner.wait_idle()
        ta, tb = get_tune(a), get_tune(b)
        self.assertEqual((ta["status"], tb["status"]), ("done", "done"), tb.get("error"))
        self.assertLessEqual(ta["finished_at"], tb["started_at"])
        self.assertEqual(tuner.status()["queued"], 0)
        self.assertIsNone(manager.blocked)

    async def test_two_starts_at_once_run_one_after_the_other(self):
        a, b = await asyncio.gather(tuner.start(self.params("a")), tuner.start(self.params("b")))
        self.assertEqual(sorted([get_tune(a)["status"], get_tune(b)["status"]]), ["queued", "running"])
        await tuner.wait_idle()
        self.assertEqual({get_tune(a)["status"], get_tune(b)["status"]}, {"done"})

    async def test_a_bad_request_is_refused_when_queued_not_later(self):
        await tuner.start(self.params("a"))
        with self.assertRaisesRegex(ValueError, "at least 512"):
            await tuner.start(self.params("b", ctx=100))
        self.assertEqual(tuner.status()["queued"], 0)
        await tuner.wait_idle()

    async def test_cancel_stops_the_running_run_and_the_queue_goes_on(self):
        a = await tuner.start(self.params("a"))
        b = await tuner.start(self.params("b"))
        await tuner.cancel()
        await tuner.wait_idle()
        self.assertEqual((get_tune(a)["status"], get_tune(b)["status"]), ("cancelled", "done"))

    async def test_queued_runs_can_be_removed_one_or_all(self):
        a = await tuner.start(self.params("a"))
        b = await tuner.start(self.params("b"))
        c = await tuner.start(self.params("c"))
        tuner.remove(b)
        self.assertIsNone(get_tune(b))
        with self.assertRaisesRegex(RuntimeError, "Only a queued run"):
            tuner.remove(a)
        with self.assertRaises(LookupError):
            tuner.remove(9999)
        self.assertEqual(tuner.clear_queue(), 1)
        self.assertIsNone(get_tune(c))
        await tuner.wait_idle()
        self.assertEqual(get_tune(a)["status"], "done")

    async def test_what_was_running_comes_back_once_after_the_whole_queue(self):
        snap = [{"model": "/m/chat.gguf", "rows": [], "env": {}, "preset_id": None, "role": "chat"}]
        with mock.patch.object(manager, "snapshot", return_value=snap), \
             mock.patch.object(manager, "stop", new=mock.AsyncMock()) as stop, \
             mock.patch.object(manager, "restore", new=mock.AsyncMock(return_value=[])) as restore:
            await tuner.start(self.params("a"))
            b = await tuner.start(self.params("b"))
            await tuner.wait_idle()
        stop.assert_awaited_once()
        restore.assert_awaited_once_with(snap)
        self.assertEqual(get_tune(b)["params"]["previous"], snap)

    async def test_each_winner_that_asked_to_start_starts_when_the_queue_empties(self):
        with mock.patch.object(manager, "start", new=mock.AsyncMock()) as start, \
             mock.patch.object(manager, "restore", new=mock.AsyncMock(return_value=[])):
            a = await tuner.start(self.params("a", start_after=True))
            await tuner.start(self.params("b"))  # didn't ask
            c = await tuner.start(self.params("c", start_after=True))
            await tuner.wait_idle()
        self.assertEqual([call.args[0] for call in start.await_args_list],
                         [get_tune(a)["model"], get_tune(c)["model"]])

    async def test_a_queued_run_whose_model_was_deleted_fails_and_the_queue_goes_on(self):
        await tuner.start(self.params("a"))
        gone = self.params("gone")
        b = await tuner.start(gone)
        c = await tuner.start(self.params("c"))
        os.remove(gone["model"])
        await tuner.wait_idle()
        self.assertEqual(get_tune(b)["status"], "failed")
        self.assertIn("Model file not found", get_tune(b)["error"])
        self.assertEqual(get_tune(c)["status"], "done")

    async def test_shutdown_leaves_the_queue_for_the_next_boot(self):
        a = await tuner.start(self.params("a"))
        b = await tuner.start(self.params("b"))
        await tuner.shutdown()
        self.assertEqual((get_tune(a)["status"], get_tune(b)["status"]), ("cancelled", "queued"))
        await tuner.resume()
        await tuner.wait_idle()
        self.assertEqual(get_tune(b)["status"], "done")
```

Add `import asyncio` to the imports at the top of the file too.

Also update the existing assertion at `app/tests/test_tuner.py:287`. `live()` returns `status()` plus `plan`, and
`status()` gains `queued`:

```python
self.assertEqual(set(tuner.live()), {"running", "tune_id", "stage", "trial", "plan", "queued"})
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `cd app && ../.venv/bin/python -m unittest tests.test_tuner.QueueTests -v` (or the Docker command from
[README.md](README.md), with `tests.test_tuner.QueueTests` in place of `discover -s tests -t .`)
Expected: errors such as `AttributeError: 'Tuner' object has no attribute 'wait_idle'` and
`RuntimeError: A tuning run is already in progress.`

- [ ] **Step 3: Implement**

1. **`db.py`**: append a fourth string to `SCHEMA`, with a `# v4: …` comment like the others:
   `ALTER TABLE tunes ADD COLUMN started_at REAL;`. `db.init()` applies it automatically to existing databases
   (it runs every schema entry above the stored `user_version`). Leave queued rows alone in `init()`. Only
   `running` becomes `interrupted`, as today.

2. **`Tuner.__init__`**: add `self._batch = None` and `self._paused = False`.

3. **Split `start()`**:
   - Delete lines 138-139 (`if self.running: raise RuntimeError("A tuning run is already in progress.")`).
   - Move lines 140-183 (everything from `model = params.get("model")` through `tunespace.check(...)`) into
     `_prepare(self, params) -> dict`, which returns `params`.
   - `start()` becomes: `p = self._prepare(params)`, insert the row with `status='queued'`
     (`created_at`, `model`, `params` as JSON, `trials='[]'`), publish it (see step 6), then
     `if not self.running and not self._paused: await self._next()`, and return the id.
   - `_begin(tid, p)` is **synchronous**. That's what makes two `start()` calls at once safe: nothing can run
     between choosing the row and creating its task. It does, in this order:
     1. If `self._batch is None`, open a batch: `self._batch = {"previous": manager.snapshot() or None, "winners": {}}`.
        Remember whether this call opened it (`opening = True`).
     2. `p["previous"] = self._batch["previous"]` (the browser's cancel toast reads it).
     3. `db.update("tunes", tid, {"status": "running", "started_at": time.time(), "params": json.dumps(p)})`.
     4. The state that lines 192-195 set today: `self.tune_id = tid`, `self.cancelled = False`,
        `self._filler_ids = None`, `self.plan = _plan(p)`, `manager.blocked = "Kiln is tuning flags. …"`.
     5. `self.task = asyncio.create_task(self._run(tid, p, stop_first=opening and bool(self._batch["previous"])))`
     6. `self._publish()`
   - Give `_run` a new keyword parameter `stop_first: bool = False`. Its very first lines, before the existing
     `try:`, become: `if stop_first: await manager.stop()`. (Today `start()` awaited this. Now the task does it,
     because `_begin` may not await.) Then, as the first line inside the `try:`, add
     `if not os.path.exists(p["model"]): raise FileNotFoundError(f"Model file not found: {p['model']}")`. The
     existing `except Exception` turns it into status `failed`.

4. **`_next()`**: start the oldest queued run, or end the batch when there is none:

   ```python
   async def _next(self) -> None:
       """Start the oldest queued run; with none left, end the batch. Call only when nothing is running
       (from start()/resume() after checking self.running, or as the last thing _run does)."""
       while not self._paused:
           row = db.row("SELECT id, params FROM tunes WHERE status='queued' ORDER BY id LIMIT 1")
           if row:
               self._begin(row["id"], json.loads(row["params"]))
               return
           if self._batch is None:
               return
           await self._end_batch()  # slow (restarts models); a run queued meanwhile is picked up by the loop
   ```

   `_next()` must **not** check `self.running`: when `_run` calls it at the end, the calling task is itself still
   running.

5. **The end of a run**: in `_run`'s `finally:` (lines 380-387) **remove** `manager.blocked = None` and
   `await self._after(p, best)`. Add: if `best` and `p.get("start_after")`, read the run's `preset_id` from the
   row and record `self._batch["winners"][p["model"]] = preset_id` (a later winner for the same model replaces an
   earlier one). Keep `_kill()`, the reset of `stage`/`trial_label`/`plan`, `self._publish()` and the `presets`
   event. Then, **after** the `try/except/finally` (not inside it), add `await self._next()`.

6. **`_end_batch()`** replaces `_after()` (lines 389-403). Delete `_after()`. `previous` is
   `self._batch["previous"] or []` (it is `None` when nothing was running). In order: `manager.blocked = None`.
   For each `model, preset_id` in `self._batch["winners"]`, start the preset the way `_after` does today
   (`get_preset(preset_id)`, then `manager.start(pr["model"], pr["flags"], pr["env"], preset=pr, role=pr["role"], force=True)`).
   Then `manager.restore(previous)` with every snapshot entry whose `model` is a winner's model left out. Collect
   the error messages from both into one `tune_note` event, as `_after` does. Wrap all of it in the same
   `try/except` `_after` has ("never let a restore failure mask the tuning result"). In a `finally`, set
   `self._batch = None`.

7. **`_publish_row(tid)`**: move the body of `_publish()` (line 806) into a new `_publish_row(self, tid: int)`,
   and make `_publish()` call `self._publish_row(self.tune_id)` when `tune_id` isn't `None`. Call
   `self._publish_row(tid)` from `start()` right after the insert. **Don't** give `_publish()` itself a
   parameter: `PlanTests.watch` (test_tuner.py, line ~235) patches `_publish` with a lambda that takes no
   arguments, and calling it with one would break those tests. From `remove()`/`clear_queue()` publish
   `events.publish("tune", {"id": tid, "removed": True})` for each removed row (the same shape downloads use).

8. **`remove(tid)`**: `LookupError("No such tuning run.")` if the row doesn't exist,
   `RuntimeError("Only a queued run can be removed. Cancel the running one instead.")` if its status isn't
   `queued`, else `DELETE FROM tunes WHERE id=?` and publish the removal. **`clear_queue()`** removes every
   queued row and returns the count.

9. **`status()`** adds `"queued": db.scalar("SELECT COUNT(*) FROM tunes WHERE status='queued'")`.

10. **`shutdown()`**: `self._paused = True`, `await self.cancel()`, `await self.wait_idle()`.
    **`resume()`**: `self._paused = False`, and `if not self.running: await self._next()`.
    **`wait_idle()`**: `while self.running: await self.task`. This waits for the whole queue, because each run
    creates the next run's task before its own task finishes.

11. **`__main__.py`**: at the end of `_autostart()` (after the `for` loop), add `await tuner.resume()` so a queue
    left from before a restart starts once the boot presets are up (its snapshot then includes them, and they
    come back afterwards). In `_shutdown_when_signalled()`, replace `await tuner.cancel()` with
    `await tuner.shutdown()`.

- [ ] **Step 4: Run the tests to see them pass**

Run: `cd app && ../.venv/bin/python -m unittest tests.test_tuner -v`
Expected: every test passes, including the existing `DraftFileTests`, `PlanTests` and `SearchSpaceRunTests`.
Then run the whole suite (`discover -s tests -t .`): `test_api` also uses the `manager`.

- [ ] **Step 5: Commit**

```bash
git add app/kiln/db.py app/kiln/tuner.py app/kiln/__main__.py app/tests/test_tuner.py
git commit -m "Tuner: queue runs that start while one is going, and restore the servers once per batch"
```

---

### Task 2: The API

**Files:**
- Modify: `app/kiln/api.py:651-675`
- Test: `app/tests/test_tuner.py` (new class `QueueApiTests`)

**Interfaces:**
- Consumes: `tuner.start`, `tuner.remove`, `tuner.clear_queue` from Task 1.
- Produces (HTTP):
  - `POST /api/tune` → `{"id": int, "status": "running" | "queued", "position": int}`. `position` is 0 for a run
    that started, else its 1-based place in the queue.
  - `GET /api/tune` → `{"live": {...}, "queue": [run, ...], "tunes": [run, ...]}`. `queue` holds every queued
    run, oldest first. `tunes` no longer contains queued runs.
  - `DELETE /api/tune/queue` → `{"removed": int}`
  - `DELETE /api/tune/{tid}` → `{"ok": true}`. `404` for an unknown id, `409` for a run that isn't queued
    (the existing exception handlers at `api.py:74-83` already map `LookupError`/`RuntimeError` like that).

- [ ] **Step 1: Write the failing tests**

```python
import httpx
from kiln import api


class QueueApiTests(Case):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://kiln", timeout=60)
        self.addAsyncCleanup(self.http.aclose)
        self.addAsyncCleanup(tuner.wait_idle)

    def body(self, name):
        return {"ctx": 4096, "depth": 1024, "mode": "quick", "speculative": False,
                "model": llm(DIR / "queue-api" / name / f"{name}-Q4.gguf")}

    async def test_queueing_listing_and_removing_over_http(self):
        r1 = (await self.http.post("/api/tune", json=self.body("a"))).json()
        r2 = (await self.http.post("/api/tune", json=self.body("b"))).json()
        r3 = (await self.http.post("/api/tune", json=self.body("c"))).json()
        self.assertEqual([(r["status"], r["position"]) for r in (r1, r2, r3)], [("running", 0), ("queued", 1), ("queued", 2)])
        listing = (await self.http.get("/api/tune")).json()
        self.assertEqual([t["id"] for t in listing["queue"]], [r2["id"], r3["id"]])
        self.assertNotIn(r2["id"], [t["id"] for t in listing["tunes"]])
        self.assertEqual(listing["live"]["queued"], 2)
        self.assertEqual((await self.http.delete(f"/api/tune/{r1['id']}")).status_code, 409)
        self.assertEqual((await self.http.delete("/api/tune/424242")).status_code, 404)
        self.assertEqual((await self.http.delete(f"/api/tune/{r2['id']}")).json(), {"ok": True})
        self.assertEqual((await self.http.delete("/api/tune/queue")).json(), {"removed": 1})
        self.assertEqual((await self.http.get("/api/tune")).json()["queue"], [])

    async def test_a_bad_request_is_a_400_and_queues_nothing(self):
        await self.http.post("/api/tune", json=self.body("a"))
        r = await self.http.post("/api/tune", json={**self.body("b"), "ctx": 100})
        self.assertEqual(r.status_code, 400)
        self.assertEqual((await self.http.get("/api/tune")).json()["queue"], [])
```

- [ ] **Step 2: Run to see them fail**

Run: `cd app && ../.venv/bin/python -m unittest tests.test_tuner.QueueApiTests -v`
Expected: FAIL. The response has no `status`, and `GET /api/tune` has no `queue`.

- [ ] **Step 3: Implement**

- `tune_start`: after `tid = await tuner.start(...)`, read the row's status. `position` is
  `db.scalar("SELECT COUNT(*) FROM tunes WHERE status='queued' AND id<=?", (tid,))` for a queued run, else 0.
- `tune_list`: `tunes` comes from `SELECT id FROM tunes WHERE status != 'queued' ORDER BY id DESC LIMIT ?`,
  and `queue` from `SELECT id FROM tunes WHERE status='queued' ORDER BY id`, both through `get_tune`.
- **Declare `DELETE /api/tune/queue` before `DELETE /api/tune/{tid}`.** FastAPI tries routes in the order they
  are declared, and `{tid}` is an `int`. If the `{tid}` route comes first, `/api/tune/queue` matches it and
  fails validation with `422` instead of reaching the queue route.

- [ ] **Step 4: Run to see them pass**, then the whole suite.

- [ ] **Step 5: Commit**: `git commit -m "API: list, add to and remove from the tuning queue"`

---

### Task 3: The Tuner page

**Files:**
- Modify: `web/js/pages/tuner.js` (`TunerPage` 55-82, `TuneForm` 86-209, `RunningCard` 250-265, `History` 363-378)
- Modify: `web/js/notify.js:23-65`

**Interfaces:**
- Consumes: the HTTP API from Task 2. The `tune` event can now be `{id, removed: true}`. `live.queued` is a count.
- Produces: `function QueueCard({ queue })` in `tuner.js`. `TuneForm` takes a new prop `queueing: boolean`, and
  `onStarted` now receives the whole `POST /api/tune` response (`{id, status, position}`).

There are no browser tests in this repo for page components. Verify by hand (step 2). Keep logic you might want
to test (e.g. the estimate text) small and inline. Put it in a pure function in `web/js/tuneplan.js` with a
`node --test` test only if it grows past a line or two.

- [ ] **Step 1: Implement**

1. **`TunerPage`**:
   - `const queue = (list.data && list.data.queue) || [];`
   - The `tune` event handler: `if (t.live) setLive(t.live);`, then `if (!t.removed && t.status !== 'queued') setSelected(t.id);`,
     then `list.reload()`. Today it always calls `setSelected(t.id)`, which would jump the page to a run that was
     just queued.
   - Layout: when `running`: `RunningCard` (pass `queued=${queue.length}`), then `QueueCard` if `queue.length`,
     then `TuneResult` for the current run, then `TuneForm` with `queueing`. When idle: `TuneForm`, then
     `TuneResult`, as today. `History` stays last in both.
   - `onStarted={(r) => { if (r.status === 'running') setSelected(r.id); list.reload(); }}`
2. **`TuneForm`** with `queueing`: card title `Queue another run`, button `Add to queue` with icon `plus`
   (else `Start tuning` / `play` as today). After `POST`, call `onStarted(r)` with the whole response. The toast for a
   queued run: title `Added to the queue`, description
   `` `Position ${r.position}. It starts when the run${r.position > 1 ? 's' : ''} before it finish${r.position > 1 ? '' : 'es'}.` ``.
   Hide the "The running model stops while tuning" alert when `queueing` (the models are already stopped).
3. **`QueueCard({ queue })`**: an `E.Card` titled `Queue` with description
   `Runs start in this order, each when the one before finishes. The models that were running come back after the last one.`
   and `padding="none"`. Inside it, an `E.Table` (`density="compact"`, `rowKey=${(t) => String(t.id)}`) with columns:
   `#` (position, 1-based), `Model` (`modelLabel(t.model)`), `Context` (`ctxLabel(t.params.ctx)`), `Objective` (the
   label from `OBJECTIVES`), `Search` (`Every combination`, `One flag at a time`, or `Smallest spill` when the
   objective is `context`), `Estimate`, and a last column with an `E.IconButton` (icon `x`, label
   `Remove from queue`) that calls `api.del('/api/tune/' + t.id)`. Compute the estimate the way `RunningCard`
   already does: `const [lo, hi] = estimateMinutes(countCandidates(t.params, { nextn_layers: t.params.has_mtp }), t.params.depth)`
   → `` `${lo}–${hi} min` ``. Card `actions`: a ghost `E.Button` `Clear queue` that calls `api.del('/api/tune/queue')`.
   Handle errors with `fail` from `useApp()`, as the other buttons on the page do.
4. **`RunningCard`**: new prop `queued`. When `queued > 0`, the actions are two buttons: secondary `Cancel this run`
   (today's `POST /api/tune/cancel`) and ghost `Cancel all`. `Cancel all` runs
   `await api.del('/api/tune/queue'); await api.post('/api/tune/cancel');` **in that order**: emptying the
   queue first means the cancelled run can't hand the GPU to the next one in between. Use
   `tune.started_at || tune.created_at` for "running for".
5. **`History`**: the `Started` column shows `t.started_at || t.created_at`. Rows from before this change have no
   `started_at`.
6. **`notify.js`**: at the top of the `tune` handler, `if (t.removed) return;`. In `tuneSummary`, use
   `t.started_at || t.created_at`. For `cancelled`, the description becomes
   `t.live && t.live.queued ? 'The next queued run starts now.' : (t.params && t.params.previous ? 'Restarting the server that was running before.' : undefined)`.

- [ ] **Step 2: Verify by hand**

Start Kiln against the fake server (see "Seeing your change in the browser" in [README.md](README.md),
with `FAKE_LOAD_S=3`). On the Tuner page:
1. Start a run on `Demo-8B-Q4_K_M` (One flag at a time). The form moves below the candidates and says **Queue another run**.
2. Queue two more (`Demo-8B-Q8_0`, then `Demo-8B-Q4_K_M` again with a different context). The Queue card lists both
   with positions 1 and 2. The toast said "Position 1…" then "Position 2…".
3. Remove the second with ×. It disappears, in this tab and in a second tab open on the same page.
4. Wait. When the first finishes, the queued one starts by itself, and you get the "Tuning finished" toast for the first.
5. Queue one more, then press **Cancel all**: the queue empties and the running one is cancelled. Nothing starts afterwards.
6. Check the browser console for errors.

- [ ] **Step 3: Commit**: `git commit -m "Tuner page: queue card, add-to-queue form and cancel all"`

---

### Task 4: Docs

**Files:** `docs/GUIDE.md` (Tuner section, after "Setting up a run"), `docs/API.md` (Tuner table near line 524,
the `tune` row of the events table near line 258, the `tune` status example near line 130 and the paragraph near
line 177)

- [ ] **Step 1:** In `GUIDE.md`, add a short **Queueing runs.** paragraph: starting a run while one is going adds it
  to the queue, runs start in order, Cancel only cancels the running one, and the models come back after the last.
  Change the "Start the winning preset when done" row to say it happens when the queue is empty.
- [ ] **Step 2:** In `API.md`, document the new responses and routes from Task 2. Remove "`409` if a run is in
  progress". Add `queued` to the `tune` status example, and the `{id, removed: true}` shape to the `tune` event row.
- [ ] **Step 3: Commit**: `git commit -m "Docs: the tuning queue"`

## Out of scope (say so in the PR)

- Reordering or editing queued runs.
- **The browser extension** (`extension/background.js:77`) notifies "Tuning finished" when it sees
  `tune.running` go from `true` to `false` in `/api/quick`. It polls once a minute, so with a queue it will usually
  only notify when the *whole queue* is done, and only for the last run. A follow-up can make it compare
  `tune.tune_id` too. Leave the extension alone in this change.

## Done when

- [ ] All Python tests and `node --test web/test/` pass.
- [ ] Every item under "What the user sees when this is done" works in the browser against the fake server.
- [ ] `docs/GUIDE.md` and `docs/API.md` describe the queue.
