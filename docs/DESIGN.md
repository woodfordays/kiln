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

Non-goals: multi-user accounts, running several models at once (router mode), a chat UI. The
built-in llama.cpp chat UI still works through the API port.

## Architecture

```
                 host :8090 (UI)             host :8080 (OpenAI / llama.cpp API)
                        │                                │
┌───────────────────────┼────────────────────────────────┼───────────────────────┐
│ container "kiln"      ▼                                ▼                       │
│   ┌──────────────────────────────┐   ┌───────────────────────────────────┐     │
│   │ Control app (FastAPI)        │   │ Recording proxy (FastAPI)         │     │
│   │  /api/*  REST + SSE events   │   │  passthrough of every path        │     │
│   │  /       Ember web UI        │   │  reads `timings` from responses   │     │
│   └──────┬─────────────┬─────────┘   └──────────┬────────────────────────┘     │
│          │ spawns/stops │ reads/writes           │ forwards to 127.0.0.1:8081   │
│          ▼              ▼                        ▼                              │
│   llama-server     SQLite /data/kiln.db    llama-server (Vulkan or CUDA)        │
│   (child process)  presets, runs, tunes,                                        │
│                    requests, gpu, downloads                                     │
└─────────────────────────────────────────────────────────────────────────────────┘
      volumes: /models ← host model dir (HF cache + downloads)   /data ← ./data
```

One container, one Python process running two uvicorn servers on one asyncio loop:

- **Control app (:8090)** serves the UI and `/api`. It owns the `llama-server` child process.
  The UI is one client of `/api`; scripts and the Zen/Firefox extension in `extension/` are
  others (see [API.md](API.md)). The extension is plain MV3 with no build step: a popup for quick
  actions, a background event page that polls `/api/quick` for the toolbar badge and notifications.
- **Recording proxy (:8080)** is the only way into `llama-server`, which binds to
  `127.0.0.1:8081` inside the container. Every completion response passes through the proxy,
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
| `server.py` | `llama-server` lifecycle: state machine, argv building, health polling, log ring buffer, run records. |
| `flags.py` | Parses `llama-server --help` into flag metadata for autocomplete and command import. |
| `proxy.py` | Streaming passthrough proxy with stats capture. |
| `stats.py` | Aggregates: range summary with deltas, time buckets, depth scatter, per-model table, lifetime totals. |
| `models.py` + `gguf.py` | Scans `/models` for GGUF files, groups split shards, reads GGUF headers (arch, context length, quant). |
| `hf.py` | Resolves a Hugging Face ID to downloadable GGUF variants; resumable streaming downloader with a queue. |
| `gpu.py` | Samples GPU busy %, VRAM, power and temperature every 2 s from amdgpu sysfs or `nvidia-smi` (all GPUs combined); keeps 10 min live, 1-minute averages in SQLite. |
| `events.py` | Fan-out of server-sent events to open browser tabs and other `/api/events` clients. |
| `tuner.py` | Flag tuner: fixed candidate order and benchmark, 2% keep margin, saves the winner as a preset. The context-length objective bisects the smallest spill of weights to system RAM (`--n-cpu-moe` or `-ngl`) that fits the target context. |
| `api.py` | REST routes, auth (Basic or Bearer token) and CORS for extension origins. Reference: [API.md](API.md). |

### llama-server lifecycle

`stopped → loading → ready → stopping → stopped`, with `crashed` when the process
exits unexpectedly. Starting with `replace` stops the running process first, under the same lock,
so a switch between presets is one request. `loading` is the period where `/health` returns 503. Kiln always adds
`--host 127.0.0.1 --port 8081 --metrics` and strips any user `--host`/`--port`. Stop sends
SIGTERM, then SIGKILL after 15 s. Each start writes a `runs` row (preset, model, argv, load time,
context size from `/props`, exit code) and a log file under `/data/logs/`.

### Presets

A preset is `{name, description, model, flags: [{flag, value}], env: {KEY: VALUE}}`. The editor
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
GET  /api/quick                       compact snapshot for quick-action clients
GET  /api/status                      server state, llama.cpp build, backend, GPU
POST /api/server/start                {preset_id, replace} or {model, flags, env, replace}
POST /api/server/stop | /restart
GET  /api/server/logs ; /api/runs ; /api/runs/{id}/log
GET  /api/flags                       parsed --help metadata
GET/POST/PUT/DELETE /api/presets[/id] presets CRUD; /duplicate; POST /api/presets/parse
GET  /api/models ; DELETE /api/models?path=
GET  /api/hf/resolve?id=              variants for a Hugging Face ID
GET/POST/DELETE /api/downloads[/id]   queue, start, forget; /cancel, /retry
POST /api/tune ; /api/tune/cancel ; GET /api/tune[/id]
GET  /api/stats/{summary,timeseries,scatter,models,lifetime,slots}?range=&model=
GET  /api/requests ; /api/requests.csv ; GET /api/gpu/{live,history}
GET/PUT /api/settings ; POST /api/stats/reset
GET  /api/events                      SSE: status, presets, tune, tune_note, download, models, request, log, gpu
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
