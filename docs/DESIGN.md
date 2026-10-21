# Kiln: design and plan

Kiln is a Docker deployment that puts llama.cpp's `llama-server` behind a web control panel. From the
UI you can start and stop the server, save presets of flags, download GGUF models by Hugging Face ID,
and watch inference performance on a dashboard built with the Ember design system. Request stats
are kept in SQLite, so the history survives restarts and rebuilds.

## Goals

1. Control `llama-server` (start, stop, restart, live logs) from a browser.
2. Save, edit, duplicate and import **presets**: a model plus a list of flags and optional env vars.
3. Enter a **Hugging Face model ID**, pick a quant, and have it downloaded with live progress.
4. A **dashboard** that visualizes inference performance (decode/prefill speed, TTFT, depth,
   draft acceptance) and totals (requests, tokens, time), and **retains** them.
5. Run on AMD/Intel boxes with the **Vulkan** backend (Mesa RADV) or on NVIDIA with **CUDA**, and stay
   portable to other machines.
6. **Tune** flags per model and box, and save the winner as a preset.
7. A documented **control API** that other clients (scripts, a browser extension) can use.

Non-goals: multi-user accounts, a chat UI. The built-in llama.cpp chat UI still works through the API port.

Several models can run at once (an LLM next to an embedding or decision model), each in its own
`llama-server` process. Kiln does this itself rather than with llama.cpp's router mode, because it needs the
per-request stats, its own fit check and the tuner's exclusive use of the GPU.

## Architecture

```
                 host :8090 (UI)             host :8080 (OpenAI / llama.cpp API)
                        │                                │
┌───────────────────────┼────────────────────────────────┼───────────────────────┐
│ container "kiln"      ▼                                ▼                       │
│   ┌──────────────────────────────┐   ┌───────────────────────────────────┐     │
│   │ Control app (FastAPI)        │   │ Routing proxy (FastAPI)           │     │
│   │  /api/*  REST + SSE events   │   │  picks the model for each request │     │
│   │  /       Ember web UI        │   │  swaps models in, reads `timings` │     │
│   └──────┬─────────────┬─────────┘   └──────────┬────────────────────────┘     │
│          │ spawns/stops │ reads/writes           │ forwards to 127.0.0.1:8081…  │
│          ▼              ▼                        ▼                              │
│   llama-server ×N  SQLite /data/kiln.db    llama-servers (Vulkan or CUDA)       │
│   (child processes) presets, runs, tunes,                                       │
│                    requests, gpu, downloads                                     │
└─────────────────────────────────────────────────────────────────────────────────┘
      volumes: /models ← host model dir (HF cache + downloads)   /data ← ./data
```

One container, one Python process running two uvicorn servers on one asyncio loop:

- **Control app (:8090)** serves the UI and `/api`. It owns the `llama-server` child processes.
  The UI is one client of `/api`; scripts and the Zen/Firefox extension in `extension/` are
  others (see [API.md](API.md)). The extension is plain MV3 with no build step: a popup for quick
  actions, a background event page that polls `/api/quick` for the toolbar badge and notifications.
- **Routing proxy (:8080)** is the only way into the `llama-server` processes, which bind to
  `127.0.0.1:8081` and up inside the container, one port per running model. Every completion response passes through the proxy,
  which parses the `timings` object llama-server attaches to the final JSON body or SSE event
  (`prompt_n`, `cache_n`, `prompt_ms`, `predicted_n`, `predicted_ms`, `draft_n`,
  `draft_n_accepted`) and writes one row per request. It also measures wall time and time to
  first byte. Because nothing can bypass the proxy, every client is counted: OpenAI SDKs,
  the llama.cpp web chat, curl.

Why a child process instead of a sibling container driven over the Docker socket: no socket
mount (that is root on the host), one image to build, and the controller sees stdout directly
for logs and crash detection.

### Backends: Vulkan (AMD/Intel) and CUDA (NVIDIA)

One Dockerfile builds either backend from `KILN_BACKEND`; the vendor-specific GPU passthrough is a
compose override (`compose.amd.yml`, `compose.nvidia.yml`) chosen by `COMPOSE_FILE`. The CUDA image is
built on `nvidia/cuda:12.8.1` and gets the GPU through the NVIDIA Container Toolkit. The Vulkan
details follow.


The image compiles llama.cpp from source with `-DGGML_VULKAN=ON` on Ubuntu 26.04 and runs it
with `mesa-vulkan-drivers` (RADV). The GPU is passed in with `/dev/dri` alone, and the
container runs as your UID with the host's `video` and `render` GIDs. The llama.cpp ref is a
build arg, pinned by default to the commit already in use on this box (`ff0dbb975`, b11081+13),
so `--spec-type draft-mtp` and the rest of your tuned flags keep working.

## Components (`app/kiln/`)

| Module | Job |
|---|---|
| `__main__.py` | Starts the DB, background tasks and both uvicorn servers. |
| `config.py` | Env-driven paths and ports. |
| `db.py` | SQLite (WAL) schema, migrations, small query helpers. |
| `server.py` | `Instance` (one `llama-server`: state machine, argv, health polling, log ring buffer, run record, measured GPU footprint) and `Fleet` (all instances: start, stop, restart, fit plan, eviction, swap-in, tuner snapshot). |
| `roles.py` | What a preset is for (chat, embedding, reranker, decision): the flags Kiln adds and the API paths served. |
| `route.py` | Which model answers a request: by name (alias, preset name, slug, file), by endpoint role, else the default chat model. Pure, tested. |
| `fit.py` | Memory estimate from the GGUF header and flags, checked against free VRAM and RAM; measured footprints replace estimates. |
| `decider.py` | Perplexity pplx-decider protocol v3 on top of `llama-server`'s per-token embeddings: prompt, readout, softmax. |
| `flags.py` | Parses `llama-server --help` into flag metadata for autocomplete and command import. |
| `proxy.py` | Streaming passthrough proxy with stats capture. |
| `stats.py` | Aggregates: range summary with deltas, time buckets, depth scatter, per-model table, lifetime totals. |
| `models.py` + `gguf.py` | Scans `/models` for GGUF files, groups split shards, reads GGUF headers (arch, context length, quant, KV geometry, decision and draft markers) and tensor rows (the decider's readout). Groups companion files (vision projector, DSpark/DFlash/EAGLE-3 drafts) with their model. |
| `hf.py` | Resolves a Hugging Face ID to downloadable GGUF variants; resumable streaming downloader with a queue. |
| `gpu.py` | Samples GPU busy %, VRAM, power and temperature every 2 s from amdgpu sysfs or `nvidia-smi` (all GPUs combined); keeps 10 min live, 1-minute averages in SQLite. |
| `events.py` | Fan-out of server-sent events to open browser tabs and other `/api/events` clients. |
| `tuner.py` | Flag tuner: fixed candidate order and benchmark, 2% keep margin, saves the winner as a preset. The context-length objective bisects the smallest spill of weights to system RAM (`--n-cpu-moe` or `-ngl`) that fits the target context. |
| `api.py` | REST routes, auth (Basic or Bearer token) and CORS for extension origins. Reference: [API.md](API.md). |

### llama-server lifecycle

Each instance goes `starting → loading → ready → stopping → stopped`, with `crashed` when its process exits
unexpectedly (a crash stays listed with its error until dismissed or the model is started again).
`loading` is the period where `/health` returns 503. Kiln always adds `--host 127.0.0.1 --port <free port>
--metrics` and strips any user `--host`/`--port`; it also adds `--alias <API name>` unless the preset sets
one, and what the role needs (`roles.apply`). Stop sends SIGTERM, then SIGKILL after 15 s. Each start writes
a `runs` row (preset, role, port, model, argv, load time, context size from `/props`, exit code, measured
GPU memory) and a log file under `/data/logs/`.

**Starting a model** (`Fleet.start`): validate the model and role, then under one lock: refuse a preset that
is already running, estimate whether it fits (`Fleet.plan`: free VRAM, plus what the models to be stopped
hold, minus what models still loading have yet to take), stop the chosen models, wait for the driver to show
the memory as free, take the next free port, spawn. `replace` is `true` (stop every other model first, under the same lock, so one request switches between
presets: what the extension sends), a list of run ids, or `"auto"` (idle, unpinned, least recently used first). An unfit start raises `NoFit`, which the API returns as a 409 with the
models that could be stopped.

**Idle timeout** (`Fleet.unload_idle`, run every `KILN_IDLE_CHECK_INTERVAL` = 30 s by `__main__._idle_loop`): the
`idle_timeout_min` setting (default 120, 0 = off) stops a ready model that has no request in flight and whose
last request ended (or which became ready, if none came) that long ago. Only proxied inference requests count;
`/health`, `/props` and the like use `begin(touch=False)`. Pinned presets, models still loading and anything
while the tuner holds the GPU are skipped. It publishes an `idle_unload` event for the UI's toast.

**Swap on request** (`Fleet.ensure`): the proxy asks for the preset a request names; one load is shared by
every waiting request, models being stopped drain their in-flight requests first, and the proxy forwards when
the instance is ready.

**GPU footprint**: when a model is the only thing starting or stopping, Kiln compares VRAM before the spawn
with VRAM two samples after it is ready and stores the difference on the run. `fit.check` prefers that
number for the same model, flags and role.

### Presets

A preset is `{name, description, model, role, alias, pinned, flags: [{flag, value, enabled?}], env: {KEY: VALUE}}`.
A flag row with `enabled: false` stays in the preset but is not passed to `llama-server`; the vision,
MTP and draft-file switches work by flipping those rows. The editor
offers autocomplete and inline help for every flag the compiled `llama-server` reports in
`--help`, so it always matches the binary. Pasting a full `llama-server …` command imports the
model and flags. One preset can be marked to autostart when the container starts.

### Model downloads

Input accepts `org/repo`, `org/repo:Q4_K_M` (llama.cpp's `-hf` form), or a huggingface.co URL.
Kiln lists the repo's GGUF files through the Hub API, groups split shards
(`-00001-of-00003.gguf`) and quant subfolders into variants, flags `mmproj` projectors, and
preselects the quant you named. Downloads stream with `httpx` to `*.part` files, resume with
HTTP Range after interruption, and land in `/models/gguf/<org>/<repo>/`. A Hugging Face token
comes from `HF_TOKEN`, the Settings page, or `$HF_HOME/token` (your existing CLI login).

### Stats model

Each tracked request stores: timestamp, run, preset, model, endpoint, HTTP status, stream flag,
`prompt_n`, `cache_n`, `predicted_n`, `prompt_ms`, `predicted_ms`, derived t/s, time to first token (first content event when streaming; wall time minus decode time plus one token otherwise), time to first byte, total
wall time, draft tokens/accepted, finish reason, and mean GPU power during the request.

- Averages are token-weighted (`Σtokens / Σtime`), so a 2-token reply doesn't count as much as a
  2,000-token one. Percentiles (p50/p95) use per-request rates.
- Context depth for the depth chart is `cache_n + prompt_n`, the KV position decode starts at.
- History is kept forever by default; Settings can set a retention window.

## Dashboard (Ember)

Ember rules applied: dark-first, one ember action per view, hairlines over boxes, `chart-1…6` in
fixed order, text in ink tokens only, status with icon and word, Bricolage for titles,
Instrument Sans for UI and numbers, JetBrains Mono for flags, paths and logs.

**Overview**: one filter row (time range 1 h, 24 h, 7 d, 30 d, all; model filter) scoping:
- Live server strip: state badge (pulsing ember dot while running), model, preset, uptime,
  start/stop actions.
- Hero number (`text-metric-lg`): decode speed in t/s for the range.
- One row of four stat cards with deltas vs the previous period and 12-point trends:
  requests, tokens generated, prefill speed, median TTFB.
- Charts: decode speed over time and prefill speed over time (two charts, never a dual axis);
  tokens per bucket (stacked: generated, prefilled, from cache); decode speed vs context depth
  (scatter, one color per model, max 6 then "Other"); live GPU (VRAM, busy, power).
- Per-model table (requests, tokens, mean/p50/p95 decode, prefill, draft acceptance) and
  all-time totals that ignore the filter.

**Server**: status, start/stop/restart, the exact command line, live log tail, run history.
**Presets**: list, editor, import. **Models**: library, Hugging Face download, queue with progress.
**Tuner**: new run, live progress, every trial, run history. **Requests**: full request log,
paged, with CSV export. **Settings**: HF token, autostart, retention, export and reset.

Toasts report actions you take and things that finish in the background (tuning runs,
downloads, the server becoming ready or crashing, the live connection dropping), with a desktop
notification as well when the tab is hidden and permission was given.

Charts are hand-rolled SVG React components (no CDN dependency, offline-safe): 2 px lines,
10% area washes, hairline grids, crosshair tooltips on lines, per-mark tooltips on bars and
dots, and a table view toggle on every chart.

## API surface (control port)

Everything the UI does goes through `/api` on the control port, so it is also the API for scripts
and the browser extension. The full reference, with auth, CORS, quick actions, events and every
route, is [API.md](API.md). In short:

```
GET  /api/quick                       compact snapshot for quick-action clients (adds `instances`)
GET  /api/status                      fleet state, `instances` (one per model), GPU memory, llama.cpp build, backend, and the
                                      primary model's fields (state, model, preset, n_ctx …) as before
POST /api/server/start                {preset_id} or {model, flags, env}, plus role, force and replace (true | run ids | "auto");
                                      409 {code: "no_fit", plan, evict} when it doesn't fit
POST /api/server/plan                 same body: does it fit, what it needs, what would be stopped
POST /api/server/stop | /restart      {run_id} for one model; empty body = all
POST /api/server/dismiss              {run_id} forgets a crashed model
GET  /api/server/logs?tail=500&run_id= recent log lines of one model (default: the primary one)
GET  /api/runs ; /api/runs/{id}/log
GET  /api/roles                       roles, the flags each adds, help text
GET  /api/flags                       parsed --help metadata
GET/POST/PUT/DELETE /api/presets[/id] presets CRUD; /duplicate; POST /api/presets/parse
GET  /api/models ; DELETE /api/models?path=
GET  /api/hf/resolve?id=              variants for a Hugging Face ID
GET/POST/DELETE /api/downloads[/id]   queue, start, forget; /cancel, /retry
POST /api/tune ; /api/tune/cancel ; GET /api/tune[/id]
GET  /api/stats/{summary,timeseries,scatter,models,lifetime,slots}?range=&model=
GET  /api/requests ; /api/requests.csv ; GET /api/gpu/{live,history}
GET/PUT /api/settings                 hf_token, autostart_preset_ids, swap_on_request, max_instances, idle_timeout_min, retention_days
POST /api/stats/reset
GET  /api/events                      SSE: status, presets, tune, tune_note, download, idle_unload, models, request, log, gpu
```

## API surface (API port)

```
POST /v1/chat/completions … /v1/embeddings /v1/rerank   routed by `model`, X-Kiln-Model or ?model=; recorded
POST /v1/systemone                    decision models: llama-server's own, or Kiln's decider adapter
GET  /v1/models                       loaded models, and presets that a request could swap in
anything else                         passed through to the default chat model
```

## Deployment

- `docker compose up -d --build`. Two ports: `8090` UI, `8080` API (point OpenAI clients at
  `http://host:8080/v1`).
- Volumes: `${MODELS_DIR}` → `/models` (defaults to `/opt/models`, which already holds the HF
  cache), `./data` → `/data` (SQLite + logs).
- `.env` holds UID/GID, the `video`/`render` GIDs, ports, the models path, an optional UI
  password (HTTP Basic), an optional API token for scripts and the extension, and the CORS origins.

## Build plan

1. Backend core: config, DB, flags parser, server manager, proxy with stats capture.
2. Models: GGUF reader, library scan, Hugging Face resolver and downloader.
3. Stats queries, GPU sampler, SSE events, REST API.
4. Frontend: Ember tokens/fonts/components vendored; React 18 + htm, no build step; SVG charts.
5. Native end-to-end test against the host's llama.cpp build and a small model.
6. Dockerfile (Vulkan build stage, slim runtime), compose file, README.
7. Build the image and verify GPU access and stats inside the container (needs Docker installed).
