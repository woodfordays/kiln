# Kiln

A Docker deployment that runs llama.cpp's `llama-server` behind a web control panel in the
Ember design. It builds for **AMD/Intel GPUs (Vulkan)** or **NVIDIA GPUs (CUDA)**, picked per host:

- **Start, stop, restart and switch models** from the browser, with the live log, the exact
  command line and a history of every run.
- **Presets**: a model plus flags and environment variables. The editor autocompletes and
  explains every flag the compiled `llama-server` reports in `--help`, and can import a
  command you already use.
- **Hugging Face downloads**: enter `org/repo`, `org/repo:Q4_K_M` or a huggingface.co link,
  pick a quant (split files and vision projectors are grouped for you), and watch it download
  with resume support.
- **Tuner**: pick a model, a target context and what to optimize for, and Kiln measures flag
  combinations (KV cache type, flash attention, batch size, MTP speculative decoding, or how much
  of the model to keep in system RAM) with a fixed, greedy benchmark, and saves the winner as a preset.
- **Control API** for scripts and browser extensions: one call for a status-and-presets snapshot,
  one call to switch presets, token auth, and live events. Reference: [docs/API.md](docs/API.md).
- **Browser extension** for Zen and Firefox ([extension/](extension/README.md)): see what's running,
  switch presets, stop or restart from the toolbar, with a status badge and notifications.
- **Dashboard**: decode and prefill speed, time to first token, decode speed by context depth,
  tokens per period, draft acceptance for speculative decoding, GPU VRAM, power and load, per-model
  breakdowns and all-time totals. Every request is stored in SQLite under `./data`, so the
  history survives restarts, rebuilds and upgrades.

Design and architecture notes are in [docs/DESIGN.md](docs/DESIGN.md); the control API is
documented in [docs/API.md](docs/API.md).

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

Point any OpenAI client at the API port. Kiln forwards every request to llama-server and
records the `timings` llama-server returns, so the dashboard counts every client.

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `KILN_BACKEND` | `vulkan` | `vulkan` (AMD/Intel) or `cuda` (NVIDIA). Also the image tag, `kiln:<backend>`. |
| `COMPOSE_FILE` | `docker-compose.yml:compose.amd.yml` | Adds the GPU passthrough for the backend. |
| `CUDA_VERSION` | `12.8.1` | CUDA toolkit for the image; the host driver must support it. |
| `CUDA_ARCHITECTURES` | `default` | GPU architectures to compile, e.g. `89` or `86;89`. `default` = all (slow build). |
| `MODELS_DIR` | `/opt/models` | Host folder mounted at `/models`. The HF cache is read from `<MODELS_DIR>/hf`; downloads go to `<MODELS_DIR>/gguf/<org>/<repo>/`. |
| `KILN_UID`, `KILN_GID` | `1000` | The container runs as you, so downloaded files belong to you. |
| `VIDEO_GID`, `RENDER_GID` | `44`, `993` | AMD only: host groups that own `/dev/dri` (`getent group video render`). |
| `KILN_BIND` | `0.0.0.0` | Interface to publish on. Use `127.0.0.1` to keep it on this box. |
| `KILN_UI_PORT`, `KILN_API_PORT` | `8090`, `8080` | Host ports. |
| `KILN_UI_PASSWORD` | empty | Turns on auth for the control panel and its API: HTTP Basic, user `kiln`. |
| `KILN_API_TOKEN` | empty | With auth on, also accepts `Authorization: Bearer <token>`, for scripts and the browser extension. |
| `KILN_CORS_ORIGINS` | browser extensions | Regex of origins allowed to call `/api` cross-origin. The default admits `moz-extension://` and `chrome-extension://`, no websites. |
| `HF_TOKEN` | empty | Token for gated models. You can also save one in Settings, or rely on `huggingface-cli login` stored in `<MODELS_DIR>/hf/token`. |
| `LLAMA_CPP_REF` | `ff0dbb975…` | llama.cpp commit, tag or branch to build. |

The API port has no auth of its own. To require a key, add `--api-key …` to a preset; llama-server
enforces it and Kiln passes the header through.

## How the tuner decides

Every search starts with the **KV cache**: it tries `f16/f16`, `q8_0/q8_0`, `q8_0/q4_0`, `q4_0/q4_0`
at full offload and keeps the first that loads at the target context with at least 512 MiB of VRAM
to spare, so KV precision is never traded for speed (*Every type that fits* keeps all of them as
candidates instead). If nothing fits fully, it repeats with `--fit on` to offload part of the model.
Then, depending on the search:

- **Every combination** (default): runs every combination of the fitting KV types, flash attention
  (`on`, `off`, `auto`; only `on` for a quantized V cache), physical batch `-ub` 256 / 512 / 1024 /
  2048 (plus `-b` variants with *Also vary the logical batch*) and, when the GGUF has an MTP head,
  `--spec-draft-n-max` 1 to 5. A configuration that doesn't fit skips the ones that need more memory.
  The best three are measured again and ranked by the mean of both runs; the simplest finalist wins
  unless another beats it by more than 2%.
- **One flag at a time**: flash attention (`off`, `auto`), then `-ub`, then MTP draft length, each
  change kept only if it beats the current best by more than 2%.

**Optimize for: Context length** works differently. The target context is the requirement, and
model weights may spill out of VRAM into system RAM to reach it, so the KV cache and the full
context stay on the GPU:

1. **Spill**: for each `-ub` (and each KV type, if *Every type that fits* is chosen), find the
   smallest spill that loads with 512 MiB of VRAM to spare (plus 128 MiB the benchmark itself
   needs). MoE models spill expert weights with `--n-cpu-moe N` (attention and KV stay on the
   GPU); dense models spill whole layers with `-ngl`.
   N is found by bisection with load-only probes (a few seconds each), starting each batch size
   from the previous answer. Spill is capped by free system RAM (keeping 4 GiB).
2. **Benchmark** the smallest spill for each batch size, then MTP on the fastest, then re-run the
   top three. Scored like balanced, since long prompts are the point.

Every candidate runs a full-batch warm-up, three fixed 128-token generations and a fixed deep prompt
(seeded filler cut to an exact token count) twice, with temperature 0, seed 42, `ignore_eos`
and the prompt cache off. `--fit off` stops llama.cpp from quietly shrinking a configuration. Scores:
generation = geometric mean of short and deep decode t/s; prompt processing = deep prefill t/s;
balanced = decode^0.7 × prefill^0.3. A later candidate replaces the current one only above +2%, so
run-to-run noise (under 1% in testing) doesn't change the outcome. The tuner stops the running
model while it works and starts the winner, or the previous model, afterwards. Every trial is
kept on the Tuner page.

## Upgrading llama.cpp

```bash
docker compose build --build-arg LLAMA_CPP_REF=b11200   # or set LLAMA_CPP_REF in .env
docker compose up -d
```

Presets, stats and run history live in `./data/kiln.db` and are untouched by rebuilds.
After an upgrade, open a preset in the editor: flags the new build no longer knows are marked.

## GPU notes

**AMD / Intel (Vulkan)**
- The image uses Mesa's RADV driver. Per-preset environment variables are the place for driver
  knobs such as `RADV_PERFTEST=…` or `GGML_VK_VISIBLE_DEVICES=0`.
- RADV's shader cache is kept in `./data/cache/mesa`, so only the first load after a rebuild pays
  for shader compilation.

**NVIDIA (CUDA)**
- GPU telemetry comes from `nvidia-smi`, which the container toolkit mounts in. On multi-GPU
  hosts the dashboard shows combined VRAM and power and the hottest card's temperature.
- All GPUs are passed in. To pin a preset to some of them, set `CUDA_VISIBLE_DEVICES=0` (or `0,1`)
  in that preset's environment; use `-sm`/`-ts` flags to control how a model is split across cards.
- An image built for one architecture won't run on a different GPU generation. Rebuild after
  `setup-env.sh` on each box, or set `CUDA_ARCHITECTURES=default` once for a portable image.

**Both**
- The CPU backend is built in every variant and picked at runtime, so the image runs on any x86-64 CPU.
- The tuner works the same way on both. On CUDA, quantized KV caches usually cost less speed than on
  AMD, so "Every type that fits" is worth a run there.

## Running without Docker (development)

```bash
python3 -m venv .venv && .venv/bin/pip install -r app/requirements.txt
cd app && KILN_DATA_DIR=../data KILN_MODELS_DIR=/opt/models \
  KILN_LLAMA_SERVER=$HOME/llama.cpp/build/bin/llama-server ../.venv/bin/python -m kiln
```

The web UI is plain ES modules with React 18, htm and the Ember component bundle in
`web/vendor/`; there is no build step. `scripts/gen_tokens_css.py` regenerates
`web/css/tokens.css` from `design/ember-tokens.json`.

## Data

| Path | What |
|---|---|
| `data/kiln.db` | SQLite: presets, runs, tuning runs, requests, GPU minutes, downloads, settings |
| `data/logs/run-<id>.log` | Full llama-server output per run |
| `data/cache/` | Mesa shader cache |

Export requests as CSV from the Overview or Requests page, or query the database directly:

```bash
sqlite3 data/kiln.db "select model, count(*), sum(predicted_n), sum(predicted_n)/(sum(predicted_ms)/1000) from requests group by model"
```
