# Kiln

A Docker deployment that runs llama.cpp's `llama-server` behind a web control panel in the
Ember design. It builds for **AMD/Intel GPUs (Vulkan)** or **NVIDIA GPUs (CUDA)**, picked per host:

- **Start, stop, restart and switch models** from the browser, with the live log, the exact
  command line and a history of every run.
- **Several models at once**: run a chat model next to an embedding or decision model when they fit
  in GPU memory, route each API request by its `model` name, and let a request load a preset that
  isn't running yet (see [Running several models](#running-several-models)).
- **Presets**: a model plus flags and environment variables. The editor autocompletes and
  explains every flag the compiled `llama-server` reports in `--help`, and can import a
  command you already use. Vision, MTP and draft files such as DSpark switch on and off with a
  click and keep their flags.
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

Advanced, set in the `environment:` of `docker-compose.yml` if you need them: `KILN_SWAP_TIMEOUT` (900 s a
request waits for a model to load), `KILN_DRAIN_TIMEOUT` (60 s running requests get before their model is
stopped to make room), `KILN_STOP_TIMEOUT` (15 s before SIGKILL) and `KILN_INTERNAL_PORT` (8081; models
take the next free port from here, and the tuner uses `KILN_INTERNAL_PORT + 100`).

The API port has no auth of its own. To require a key, add `--api-key …` to a preset; llama-server
enforces it and Kiln passes the header through.

## Running several models

Each running model is its own `llama-server` process on its own internal port. The API port routes
every request:

1. **By name.** The request's `model` field is matched, ignoring case, against a running model's API
   name (set on the preset, otherwise its name), the preset name, its slug, any `--alias` flag, and
   finally the model file's name. `X-Kiln-Model: <name>` or `?model=<name>` works too, and every response
   says which model answered in `X-Kiln-Model`.
2. **By endpoint.** `/v1/embeddings` goes to the embedding model, `/v1/rerank` to the reranker,
   `/v1/systemone` to the decision model.
3. **Otherwise** to the chat model used last. A name that matches nothing (`gpt-4o`, `default`) falls
   through to that, as before, so existing clients keep working.

`GET /v1/models` lists what is loaded, and (with swapping on) every preset that could be.

**A preset has a role**: *Chat*, *Embeddings* (Kiln adds `--embeddings`), *Reranker* (`--rerank`) or
*Decision*. The role decides the flags Kiln adds and which paths the model serves; the Server page shows
the exact command line. New presets pick the role from the model (the library marks embedding, reranker
and decision models). A preset can also be **pinned**, so it is never stopped to make room.

**Do they fit?** Before another model starts, Kiln estimates what it needs from the GGUF header and
the flags (weights, KV cache with sliding-window and hybrid layers accounted for, compute buffer,
projector) and compares it with the VRAM that is free right now, keeping 512 MiB spare. After a model
has loaded on its own, the measured footprint replaces the estimate for that model and those flags.
Against the 87 successful trials the tuner has recorded on one AMD box, the estimate was within -9%
to +11% of the VRAM actually used, 1.6% high on average. If it doesn't fit, Start offers to stop idle,
unpinned models (least recently used first), or to start anyway. `--fit on`, llama.cpp's default,
shrinks a model to whatever is free instead of refusing; add `--fit off` for exact settings. Without
GPU telemetry Kiln can't check and starts the model.

**Swap on request** (Settings, on by default). When a request names a preset that isn't running, Kiln
loads it first: it waits for running requests on a model to finish (up to 60 s) before stopping it to
make room, shares one load between requests that want the same preset, and answers when the model is ready. Streaming
requests get SSE comment lines while they wait, so a long load doesn't hit a first-byte timeout, and a
failure arrives as an error event. The Requests page shows the load time in its own column and keeps it
out of time to first token. Settings also caps how many models run at once, and lets several presets
start on boot, one after another. The API port has no auth of its own, so anyone who can reach it can
make Kiln load any preset: a preset with `--api-key` is only loaded (and only listed in `/v1/models`) for
requests that carry one of its keys, and you can turn swapping off in Settings.

The tuner stops everything for the GPU and starts what was running again when it finishes.

## Decision models

A decision model reads a request (a `state` and typed `questions`) and scores each question's options
in one forward pass. It generates no text. The API is llama.cpp's `/v1/systemone`:

```bash
curl http://localhost:8080/v1/systemone -H 'Content-Type: application/json' -d '{
  "state": "My integration keeps failing with a 401 since we rotated our API key. Please help.",
  "questions": {
    "team":    {"type": "choice", "instructions": "Which team should handle this request?",
                "criteria": {"billing": "Charges and refunds", "support": "Technical integration errors",
                             "sales": "Questions about buying a product"}},
    "urgent":  {"type": "noul", "instructions": "Is the customer blocked right now?"},
    "mood":    {"type": "score", "criteria": ["calm", "mildly annoyed", "frustrated", "angry"]}
  }}'
```

- Models whose GGUF carries llama.cpp's `<arch>.decision.type` (OpenJev, lev, Laya, Kev and the like) are
  served by `llama-server` itself. Kiln routes and records the requests.
- **Perplexity's pplx-decider checkpoints (protocol v3)**, such as `decider-flash-v1.2`, are not: stock
  `llama-server` answers `501 This model is not a decision model`, because they have no language-model
  head and need non-causal attention and a readout at each question's marker. Kiln serves them itself.
  Give the preset the **Decision** role and Kiln starts `llama-server` with `--embeddings --pooling none`,
  `attention.causal=false` and a micro-batch that holds the whole prompt, then builds the checkpoint's
  joint prompt, asks `llama-server` for the hidden state at every position, multiplies the state at each
  `<|fim_suffix|>` marker with that question's rows of the checkpoint's readout (read from `output.weight`
  in the GGUF), divides by the calibration temperature in the GGUF and takes a softmax. The answer
  follows the model card's response shape: `answers` per question (`choice`, `probabilities`,
  `confidence`; `noul`; `score`, `probabilities`, `legend`, `confidence`) and `usage`. `confidence` is the
  probability of the top option.

  Checked against the model card's own example on the real `llama-server`: `team` = support (99.8%),
  `urgent` = true (97%), and a refund request routes to billing, a pricing question to sales. Limits to
  know about:
  - **Text only.** `llama-server` decodes image chunks separately, but this checkpoint has to see the
    whole prompt, image included, in one micro-batch, so requests with `images` get a 400 and the
    projector isn't loaded.
  - The prompt layout is the community reproduction of the reference encoder published with
    `Jakevin/pplx-decider-flash-v1.2-GGUF` (Apache-2.0); Perplexity's own source for protocol v3 is in a private
    repository. It agrees with the model card's description and gave sensible answers in testing, but
    it is not the reference implementation. Treat probabilities from here as unverified against Perplexity's.
  - The whole prompt must fit the context (`-c`, 4096 unless you set it). `llama-server` returns one
    vector per token, so very long states are slow and use memory.

## Speculative decoding and other model files

Models often come with extra files, and the library groups them with the model they belong to:

| File | Flag | How Kiln finds it |
|---|---|---|
| Vision projector (`mmproj-*.gguf`) | `--mmproj` | GGUF architecture `clip`, nearest name first |
| **MTP** head | `--spec-type draft-mtp` | built into the model; the GGUF has `nextn_predict_layers` |
| **DSpark**, DFlash, EAGLE-3 or a plain draft model | `--spec-type draft-dspark` (…) with `-md <file>` | a `dflash`/`eagle` architecture file, or "draft" in the name, next to the model |

In the preset editor, **Model features** has a switch for vision and a *Speculative decoding* choice
(off, MTP, or each draft file found), and the Presets list shows the same as clickable chips. Switching a
feature off doesn't delete anything: its flags stay in the preset, greyed out and skipped (every flag
row also has its own checkbox), so switching it back on restores `--spec-draft-n-max` and the rest as
they were. If the preset's model is running, the toast offers a restart.

Verified on the real `llama-server` (b11699, on CPU, with a short greedy generation):

| Model | Setting | Decode |
|---|---|---|
| Qwen3.8 27B | MTP on (`--spec-draft-n-max 3`) | 2.88 t/s, 26 of 37 drafted tokens accepted |
| Qwen3.8 27B | MTP off | 1.88 t/s, no `--spec-type` on the command line |
| MiniCPM-V 4.7 35B-A3B + its DSpark file | DSpark on (`-md`, `--spec-draft-n-max 7`) | 17.6 t/s, 41 of 41 accepted |
| MiniCPM-V 4.7 35B-A3B | DSpark off | 11.4 t/s, no `-md` on the command line |

The counting prompt is easy to draft, so the acceptance rates flatter; use the tuner or your own
traffic for real numbers. `--spec-type draft-dspark` and its siblings need a recent llama.cpp
(`LLAMA_CPP_REF=b11699` was used here). DFlash and DSpark clamp `--spec-draft-n-max` to the draft's
block size. The tuner still tries only MTP.

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

Tests (no GPU needed; they run a fake `llama-server`, `app/tests/fake_llama.py`):

```bash
cd app && ../.venv/bin/python -m unittest discover -s tests -t .
node --test web/test/features.test.mjs
```

The web UI is plain ES modules with React 18, htm and the Ember component bundle in
`web/vendor/`; there is no build step. `scripts/gen_tokens_css.py` regenerates
`web/css/tokens.css` from `design/ember-tokens.json`.

## Data

| Path | What |
|---|---|
| `data/kiln.db` | SQLite: presets, runs (with each model's measured GPU footprint), tuning runs, requests, GPU minutes, downloads, settings |
| `data/logs/run-<id>.log` | Full llama-server output per run |
| `data/cache/` | Mesa shader cache |

Export requests as CSV from the Overview or Requests page, or query the database directly:

```bash
sqlite3 data/kiln.db "select model, count(*), sum(predicted_n), sum(predicted_n)/(sum(predicted_ms)/1000) from requests group by model"
```
