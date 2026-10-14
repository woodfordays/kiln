# Kiln

A Docker deployment that runs llama.cpp's `llama-server` on the Vulkan backend behind a web
control panel in the Ember design:

- **Start, stop, restart and switch models** from the browser, with the live log, the exact
  command line and a history of every run.
- **Presets**: a model plus flags and environment variables. The editor autocompletes and
  explains every flag the compiled `llama-server` reports in `--help`, and can import a
  command you already use.
- **Hugging Face downloads**: enter `org/repo`, `org/repo:Q4_K_M` or a huggingface.co link,
  pick a quant (split files and vision projectors are grouped for you), and watch it download
  with resume support.
- **Tuner**: pick a model and a target context, and Kiln measures flag candidates in a fixed
  order (KV cache type, flash attention, batch size, MTP speculative decoding) with a fixed,
  greedy benchmark, keeps a change only if it wins by more than 2%, and saves the winner as a preset.
- **Dashboard**: decode and prefill speed, time to first token, decode speed by context depth,
  tokens per period, draft acceptance for speculative decoding, GPU VRAM, power and load, per-model
  breakdowns and all-time totals. Every request is stored in SQLite under `./data`, so the
  history survives restarts, rebuilds and upgrades.

Design and architecture notes are in [docs/DESIGN.md](docs/DESIGN.md).

## Quick start

```bash
./scripts/install-docker.sh      # once per box, if Docker isn't installed (uses sudo)
./scripts/setup-env.sh           # writes .env with your UID/GID and GPU group IDs
docker compose up -d --build     # first build compiles llama.cpp: about 5 minutes on 24 threads
```

Then open:

| | URL | |
|---|---|---|
| Control panel | `http://<host>:8090` | Ember UI |
| API | `http://<host>:8080/v1` | OpenAI-compatible; also serves llama.cpp's own chat UI at `/` |

Point any OpenAI client at the API port. Kiln forwards every request to llama-server and
records the `timings` llama-server returns, so the dashboard counts every client.

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `MODELS_DIR` | `/opt/models` | Host folder mounted at `/models`. The HF cache is read from `<MODELS_DIR>/hf`; downloads go to `<MODELS_DIR>/gguf/<org>/<repo>/`. |
| `KILN_UID`, `KILN_GID` | `1000` | The container runs as you, so downloaded files belong to you. |
| `VIDEO_GID`, `RENDER_GID` | `44`, `993` | Host groups that own `/dev/dri` (`getent group video render`). |
| `KILN_BIND` | `0.0.0.0` | Interface to publish on. Use `127.0.0.1` to keep it on this box. |
| `KILN_UI_PORT`, `KILN_API_PORT` | `8090`, `8080` | Host ports. |
| `KILN_UI_PASSWORD` | empty | Turns on HTTP Basic auth for the control panel (user `kiln`). |
| `HF_TOKEN` | empty | Token for gated models. You can also save one in Settings, or rely on `huggingface-cli login` stored in `<MODELS_DIR>/hf/token`. |
| `LLAMA_CPP_REF` | `ff0dbb975…` | llama.cpp commit, tag or branch to build. |

The API port has no auth of its own. To require a key, add `--api-key …` to a preset; llama-server
enforces it and Kiln passes the header through.

## How the tuner decides

1. **KV cache**: tries `f16/f16`, `q8_0/q8_0`, `q8_0/q4_0`, `q4_0/q4_0` at full offload and keeps the
   first that loads at the target context with at least 512 MiB of VRAM to spare. KV precision is
   never traded for speed. If nothing fits fully, it repeats with `--fit on` to offload part of the model.
2. **Flash attention** (`off`, `auto`), only when the V cache is f16.
3. **Physical batch** `-ub` 256 / 512 / 1024 / 2048.
4. **MTP self-speculative decoding**, `--spec-draft-n-max` 1 to 5, when the GGUF has an MTP head.

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

## Vulkan notes

- The image uses Mesa's RADV driver. Per-preset environment variables are the place for driver
  knobs such as `RADV_PERFTEST=…` or `GGML_VK_VISIBLE_DEVICES=0`.
- RADV's shader cache is kept in `./data/cache/mesa`, so only the first load after a rebuild pays
  for shader compilation.
- The CPU backend is built in every variant and picked at runtime, so one image runs on any x86-64 box.

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
| `data/kiln.db` | SQLite: presets, runs, requests, GPU minutes, downloads, settings |
| `data/logs/run-<id>.log` | Full llama-server output per run |
| `data/cache/` | Mesa shader cache |

Export requests as CSV from the Overview or Requests page, or query the database directly:

```bash
sqlite3 data/kiln.db "select model, count(*), sum(predicted_n), sum(predicted_n)/(sum(predicted_ms)/1000) from requests group by model"
```
