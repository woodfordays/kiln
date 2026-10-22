# Kiln

**A control panel for llama.cpp.** Kiln runs `llama-server` in Docker behind a web UI built on the Ember design
system, so you can download models, save presets, run several models on one GPU, tune flags, and see how fast
everything really is. It builds for **AMD/Intel GPUs (Vulkan)** or **NVIDIA GPUs (CUDA)**, picked per host, and
exposes an OpenAI-compatible API that every client can use.

<picture>
  <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/overview-light.png">
  <img src="docs/screenshots/overview.png" alt="Kiln's Overview page: three running models, decode and prefill speed, and per-model history">
</picture>

<sub>The screenshots in this repository show a demo instance. The model names come from real Hugging Face repositories; the
requests, speeds, tuning results and GPU readings are sample data made up to show the layout, not benchmarks.</sub>

## What it does

- **Start, stop, restart and switch models** from the browser, with the live log, the exact command line and a
  history of every run.
- **Several models at once**: run a chat model next to an embedding or decision model when they fit in GPU
  memory, route each API request by its `model` name, and let a request load a preset that isn't running yet
  (see [Running several models](docs/MULTI-MODEL.md)).
- **Presets**: a model plus flags and environment variables. The editor autocompletes and explains every flag
  the compiled `llama-server` reports in `--help`, and can import a command you already use. Vision, MTP and
  draft files such as DSpark switch on and off with a click and keep their flags.
- **Hugging Face downloads**: enter `org/repo`, `org/repo:Q4_K_M` or a huggingface.co link, pick a quant (split
  files and vision projectors are grouped for you), and watch it download with resume support.
- **Tuner**: pick a model, a target context and what to optimize for, and Kiln measures flag combinations (KV
  cache type, flash attention, batch size, MTP speculative decoding, or how much of the model to keep in system
  RAM) with a fixed, greedy benchmark, and saves the winner as a preset.
- **Dashboard**: decode and prefill speed, time to first token, decode speed by context depth, tokens per period,
  draft acceptance for speculative decoding, GPU VRAM, power and load, per-model breakdowns and all-time totals.
  Every request is stored in SQLite under `./data`, so the history survives restarts, rebuilds and upgrades.
- **Control API** for scripts and browser extensions: one call for a status-and-presets snapshot, one call to
  switch presets, token auth, and live events. Reference: [docs/API.md](docs/API.md).
- **Browser extension** for Zen and Firefox ([extension/](extension/README.md)): see what's running, switch
  presets, stop or restart from the toolbar, with a status badge and notifications.

## A tour

### Overview

Every request through the API port, measured by llama-server and kept on disk. Choose a time range and a model;
the headline decode speed is token-weighted, the stat cards compare with the previous period, and every chart can
be shown as a table.

![Decode speed, prefill speed, tokens, and decode speed by context depth](docs/screenshots/overview-charts.png)

The depth chart plots one dot per request against how far into the context it started, which is the quickest way
to see how much a model slows down as a conversation grows.

### Server

The running models with their measured GPU memory, a start card that checks whether another model fits *before*
starting it, the exact command line, and the live log.

![Server page](docs/screenshots/server.png)

Kiln estimates what a model needs from its GGUF header and your flags, compares that with the VRAM that is free
right now, and offers to stop an idle model to make room:

![Start card: a preset that doesn't fit, with the option to stop an idle model](docs/screenshots/server-start.png)

### Presets

A preset is a model, its flags and environment, a role (chat, embeddings, reranker or decision) and an API name.
Feature chips switch vision, MTP and draft files on and off without losing their settings.

![Presets page](docs/screenshots/presets.png)

The editor knows every flag your `llama-server` build supports, with its description, and shows the command it
will run:

![Preset editor](docs/screenshots/preset-editor.png)

### Models

Download from Hugging Face by ID or link, with the quant picker, a resumable queue, and a library that shows what
each file is.

![Models page](docs/screenshots/models.png)

### Tuner

Measures flag combinations for one model on *your* box and saves the winner as a preset. Every trial is kept so
you can see what was tried and why the winner won.

![Tuner page](docs/screenshots/tuner.png)

### Browser extension, light theme and phones

The extension puts the running models and a preset switcher in the toolbar. The control panel has a light theme
and works on a phone.

<table>
  <tr>
    <td><img src="docs/screenshots/extension-popup.png" alt="Browser extension popup" width="250"></td>
    <td><img src="docs/screenshots/mobile-overview.png" alt="Overview on a phone" width="250"></td>
    <td><img src="docs/screenshots/mobile-charts.png" alt="Overview charts on a phone" width="250"></td>
  </tr>
</table>

More screens (requests, settings, the model library, the tuner's candidate table) are in the
[user guide](docs/GUIDE.md).

## Quick start

```bash
./scripts/install-docker.sh            # once per box, if Docker isn't installed (uses sudo)
./scripts/install-nvidia-toolkit.sh    # NVIDIA hosts only: lets containers use the GPU
./scripts/setup-env.sh                 # detects the GPU and writes .env
docker compose up -d --build           # first build compiles llama.cpp
```

`setup-env.sh` picks the backend from the hardware: NVIDIA (`nvidia-smi` works) gets CUDA,
everything else gets Vulkan. It also sets `COMPOSE_FILE` to add the matching GPU passthrough
(`compose.nvidia.yml` or `compose.amd.yml`), and on NVIDIA it compiles only for the GPUs in that
box (`CUDA_ARCHITECTURES`, e.g. `89` for an RTX 4090). Force a choice with
`KILN_BACKEND=vulkan ./scripts/setup-env.sh`; Vulkan also runs on NVIDIA if you want to compare.

| Backend | Base image | First build | Host needs |
|---|---|---|---|
| Vulkan | Ubuntu 26.04 + Mesa RADV | about 5 min, 786 MB image | `/dev/dri` (any recent kernel) |
| CUDA | `nvidia/cuda:12.8.1` (Ubuntu 24.04) | 10–20 min for one architecture | NVIDIA driver 570+, NVIDIA Container Toolkit |

Then open:

| | URL | |
|---|---|---|
| Control panel | `http://<host>:8090` | Ember UI; control API under `/api` ([docs/API.md](docs/API.md)) |
| API | `http://<host>:8080/v1` | OpenAI-compatible; also serves llama.cpp's own chat UI at `/` |

Then, in the control panel: **Models** to download one, the **+** in the library to make a preset, and **Start**.
The [user guide](docs/GUIDE.md#your-first-ten-minutes) walks through it.

## Using the API

Point any OpenAI client at the API port. Kiln forwards every request to llama-server and records the `timings`
llama-server returns, so the dashboard counts every client.

```bash
curl http://localhost:8080/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "qwen",
  "messages": [{"role": "user", "content": "Say hello in five words."}]
}'
```

`model` is a preset's API name. With several models running, Kiln picks the model by that name (or by the path:
`/v1/embeddings` goes to the embedding model), and a name that matches nothing goes to the chat model used last.
`GET /v1/models` lists what is loaded and what a request could load. The rules are in
[docs/MULTI-MODEL.md](docs/MULTI-MODEL.md).

## Configuration at a glance

Per-machine settings live in `.env` (written by `setup-env.sh`; `.env.example` lists them all):

| Variable | Default | Meaning |
|---|---|---|
| `MODELS_DIR` | `/opt/models` | Host folder mounted at `/models`. Downloads go to `<MODELS_DIR>/gguf/<org>/<repo>/`. |
| `KILN_UI_PORT`, `KILN_API_PORT` | `8090`, `8080` | Host ports for the control panel and the API. |
| `KILN_BIND` | `0.0.0.0` | Interface to publish on. `127.0.0.1` keeps Kiln on this box. |
| `KILN_UI_PASSWORD` | empty | Turns on a login for the control panel and its API (HTTP Basic, user `kiln`). |
| `KILN_API_TOKEN` | empty | With a password set, a bearer token for scripts and the browser extension. |
| `HF_TOKEN` | empty | Hugging Face token for gated models (or save one in Settings). |
| `LLAMA_CPP_REF` | pinned commit | llama.cpp commit, tag or branch to build. |

The full table, access control, GPU notes and upgrading llama.cpp are in
[docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## Documentation

| | |
|---|---|
| [User guide](docs/GUIDE.md) | Every page of the control panel, and recipes for common set-ups. |
| [Running several models](docs/MULTI-MODEL.md) | Routing by name, the memory fit check, loading on request, idle unloading, decision models, vision projectors and speculative decoding. |
| [The tuner](docs/TUNER.md) | What it tries, in what order, and how it scores. |
| [Configuration](docs/CONFIGURATION.md) | `.env`, access control, GPU notes, upgrading llama.cpp, and where data lives. |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Messages you may see, and what to do about them. |
| [Control API](docs/API.md) | Every route of the control panel's API, auth, CORS and live events. |
| [Design and architecture](docs/DESIGN.md) | How Kiln is built. |
| [Browser extension](extension/README.md) | Installing and using the Zen/Firefox extension. |

## Running without Docker (development)

```bash
python3 -m venv .venv && .venv/bin/pip install -r app/requirements.txt
cd app && KILN_DATA_DIR=../data KILN_MODELS_DIR=/opt/models \
  KILN_LLAMA_SERVER=$HOME/llama.cpp/build/bin/llama-server ../.venv/bin/python -m kiln
```

Tests (no GPU needed; they run a fake `llama-server`, `app/tests/fake_llama.py`):

```bash
cd app && ../.venv/bin/python -m unittest discover -s tests -t .
node --test web/test/features.test.mjs
```

The web UI is plain ES modules with React 18, htm and the Ember component bundle in
`web/vendor/`; there is no build step. `scripts/gen_tokens_css.py` regenerates
`web/css/tokens.css` from `design/ember-tokens.json`.

## Data

Presets, request history, run history, tuning runs and settings live in `data/kiln.db`; full llama-server output
for every run is in `data/logs/`. Rebuilding or upgrading leaves them alone. Export requests as CSV from the
Overview or Requests page. Details and a backup note are in
[docs/CONFIGURATION.md](docs/CONFIGURATION.md#data).
