# Implementation plans

Step-by-step plans for upcoming work. Each one is written so that someone new to Kiln can pick it up: it says
what the user should see when it's done, which files change, what each function is called and what it returns,
which tests prove it, and the traps in the existing code that are easy to fall into.

| Plan | Feature | Touches |
|---|---|---|
| [Tuning queue](2026-11-02-tuning-queue.md) | Queue several tuning runs; they run one after another | `app/kiln/tuner.py`, `db.py`, `api.py`, `__main__.py`, `web/js/pages/tuner.js` |
| [Live tuning details](2026-11-02-live-tuning-details.md) | Show what the tuner is doing *inside* a candidate while it runs | `app/kiln/tuner.py`, `web/js/tuneplan.js`, `web/js/pages/tuner.js`, `web/js/lib.js` |
| [Tuner tables without sideways scrolling](2026-11-02-tuner-tables-fit.md) | Resize the Tuner cards and tables so results fit the page width | `web/js/pages/tuner.js`, `web/css/app.css` |
| [Nested model library](2026-11-02-nested-model-library.md) | Show vision projectors and draft files (DSpark, …) under the model they belong to | `app/kiln/models.py`, new `web/js/library.js`, `web/js/pages/models.js` |

**Order.** The library plan is independent of the other three and can be done at any time. The three tuner plans
all edit `web/js/pages/tuner.js`, so do them one at a time, each on its own branch from an up-to-date `main`:
the queue first (it changes how the page is laid out while a run is going), then live details (it rewrites the
running card), then the table layout. Doing them in a different order works, but expect merge conflicts in
`RunningCard` and `TunerPage`.

## Before you start: how to run things

Kiln's backend is Python (FastAPI, one asyncio event loop), the web UI is plain ES modules with React 18 and
`htm` templates and the Ember component bundle in `web/vendor/` — **there is no build step**: edit a `.js` or
`.css` file and reload the page.

### Tests

The Python tests start a fake `llama-server` (`app/tests/fake_llama.py`) and need no GPU, but they **need
Linux or macOS**: the tuner uses POSIX process groups (`os.killpg`) and the fake server is a script with a
`#!/usr/bin/env python3` line. On Windows, run them in Docker (works from PowerShell or a Linux shell, from the
repository root):

```bash
docker run --rm -v "${PWD}:/src:ro" python:3.13-slim sh -c "cp -r /src /work && cd /work/app && pip install -q --root-user-action=ignore -r requirements.txt && python -m unittest discover -s tests -t ."
```

The whole suite takes about four minutes and ends with `OK (skipped=1)` on a clean `main`. To run one test
class, replace `discover -s tests -t .` with its dotted name, for example `tests.test_tuner.PlanTests`.

On Linux or macOS, the usual way (from the README):

```bash
python3 -m venv .venv && .venv/bin/pip install -r app/requirements.txt
cd app && ../.venv/bin/python -m unittest discover -s tests -t .
```

Run a single test class or test while you work, for example:

```bash
cd app && ../.venv/bin/python -m unittest tests.test_tuner.QueueTests -v
```

The web tests are plain Node (version 18 or newer) and run anywhere:

```bash
node --test web/test/
```

### Seeing your change in the browser without a GPU

You can run the whole control panel against the fake `llama-server`, so the Tuner actually runs (with made-up
speeds) and the Models page has files to show. On Windows, start a Linux container with the repository mounted
(edits you make on Windows show up inside it; reload the page for web changes, restart Kiln for Python changes):

```bash
docker run --rm -it -p 8090:8090 -v "${PWD}:/src" -w /src python:3.13-slim bash
```

Inside it, run `pip install -q -r app/requirements.txt` once, then the commands below with `python` wherever
they say `../.venv/bin/python`. On Linux or macOS, run them directly:

```bash
mkdir -p /tmp/kiln-dev/models/gguf/org/Demo-GGUF
cd app && ../.venv/bin/python -c "
from tests.helpers import llm, write_gguf
d = '/tmp/kiln-dev/models/gguf/org/Demo-GGUF/'
llm(d + 'Demo-8B-Q4_K_M.gguf'); llm(d + 'Demo-8B-Q8_0.gguf')
write_gguf(d + 'mmproj-Demo-8B-f16.gguf', {'general.architecture': 'clip', 'general.type': 'mmproj'})
write_gguf(d + 'dspark-Demo-8B.gguf', {'general.architecture': 'dflash', 'general.name': 'Demo DSpark', 'dflash.has_confidence_head': True, 'dflash.block_size': 15})
llm('/tmp/kiln-dev/models/gguf/org/Demo-MTP-GGUF/Demo-MTP-Q4_K_M.gguf', arch='qwen35', **{'qwen35.nextn_predict_layers': 1})
llm('/tmp/kiln-dev/models/gguf/org/Broken-GGUF/Demo-crash-Q4_K_M.gguf')
"
KILN_DATA_DIR=/tmp/kiln-dev/data KILN_MODELS_DIR=/tmp/kiln-dev/models KILN_LLAMA_SERVER=$PWD/tests/fake_llama.py \
  FAKE_LOAD_S=3 ../.venv/bin/python -m kiln
```

Open <http://localhost:8090>. What the seed gives you:

| File | Useful for |
|---|---|
| `Demo-8B-Q4_K_M`, `Demo-8B-Q8_0` + `mmproj-Demo-8B-f16` + `dspark-Demo-8B` | Two quants sharing a vision projector and a DSpark draft file in one folder (Library nesting; a Tuner run with a DSpark candidate) |
| `Demo-MTP-Q4_K_M` | A model with an MTP head (Tuner runs with speculative candidates) |
| `Demo-crash-Q4_K_M` | The fake server exits while loading any path containing `crash`, so every candidate fails with a reason (error rows in the Tuner) |

`FAKE_LOAD_S=3` makes every candidate take a few seconds to "load", which gives you time to watch the Tuner's
live display. Restart with `FAKE_LOAD_S=0` when you only need runs to finish fast. The fake server reports no
GPU, so nothing ever runs out of VRAM and there are no VRAM readings. (Importing `tests.helpers` from the `app`
directory works because `tests` is a package there. Its `__init__.py` only touches a scratch directory.)

If a fake server fails to start with `env: 'python3\r': No such file or directory`, your checkout turned the
line endings into CRLF: run `git config core.autocrlf input` and check the file out again
(`git checkout -- app/tests/fake_llama.py`).

### House style

- **Match the code around you.** Short functions, a one-line docstring on anything non-obvious, comments that
  say *why*. No new dependencies.
- **UI copy** is sentence case, short and plain: "Tuning started", "Add to queue", "Nothing measured yet."
  Numbers go through the formatters in `web/js/lib.js` (`fmtInt`, `fmtTps`, `fmtBytes`, `fmtDuration`, …).
- **Server-sent events.** The UI listens on one `EventSource`; the list of event names it subscribes to is
  hard-coded in `web/js/lib.js` (`ensureSource`). A new event kind published by the backend does nothing in the
  browser until its name is added to that list.
- **Docs.** User-facing behaviour is described in `docs/GUIDE.md`, the API in `docs/API.md`, the tuner's search
  in `docs/TUNER.md`. Each plan says which of these to update.
- **Commits.** Small, one per task, message in the imperative ("Tuner: queue runs that start while one is going").
