# Configuration

Everything you set per machine lives in `.env` (copy `.env.example`, or let `./scripts/setup-env.sh`
write it). Settings you change while Kiln runs (Hugging Face token, swapping, idle timeout, boot
presets, history) are in the Settings page and stored in the database.

## `.env`

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
stopped to make room), `KILN_STOP_TIMEOUT` (15 s before SIGKILL), `KILN_IDLE_CHECK_INTERVAL` (30 s between idle-timeout checks) and `KILN_INTERNAL_PORT` (8081; models
take the next free port from here, and the tuner uses `KILN_INTERNAL_PORT + 100`).

The API port has no auth of its own. To require a key, add `--api-key …` to a preset; llama-server
enforces it and Kiln passes the header through.

## Access control

Kiln has two ports, and they are protected differently.

| Port | What it serves | Protection |
|---|---|---|
| Control panel, `8090` | The web UI and the control API under `/api` | Off by default. Set `KILN_UI_PASSWORD` and every path except `/healthz` asks for HTTP Basic credentials: user `kiln`, that password. |
| API, `8080` | The OpenAI-compatible endpoints | None of its own. A preset with `--api-key …` makes llama-server enforce a key for that model. |

- **Scripts and the browser extension** can use a token instead of the UI password. Set
  `KILN_API_TOKEN` (for example `openssl rand -hex 24`) and send `Authorization: Bearer <token>`. The token
  only works while `KILN_UI_PASSWORD` is set, because without a password nothing is checked.
  See [API.md → Authentication](API.md#authentication).
- **Cross-origin calls** to `/api` are accepted from browser extensions by default and from no websites.
  `KILN_CORS_ORIGINS` changes that. See [API.md → CORS](API.md#cross-origin-access-cors).
- **Keeping it on one machine:** `KILN_BIND=127.0.0.1` publishes both ports on the loopback interface only.
- **Anyone who can reach the API port can make Kiln load a preset** while *Load models when a request names
  them* is on (Settings). Presets with `--api-key` are only loaded, and only listed in `/v1/models`, for
  requests that carry one of their keys. See [Running several models](MULTI-MODEL.md#routing-fit-and-swapping).
- Kiln does not terminate TLS. For access over an untrusted network, put it behind a reverse proxy that does.

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

## Upgrading llama.cpp

```bash
docker compose build --build-arg LLAMA_CPP_REF=b11200   # or set LLAMA_CPP_REF in .env
docker compose up -d
```

`LLAMA_CPP_REF` in `.env` overrides the default pinned in `docker-compose.yml` and the Dockerfile.
Presets, stats and run history live in `./data/kiln.db` and are untouched by rebuilds.
After an upgrade, open a preset in the editor: flags the new build no longer knows are marked.

A model whose architecture is newer than your llama.cpp build fails to load with
`unknown model architecture: '<name>'`, and no flag can fix that. Move `LLAMA_CPP_REF` to a commit at or
after the one that added the architecture, then rebuild as above.

## Data

| Path | What |
|---|---|
| `data/kiln.db` | SQLite: presets, runs (with each model's measured GPU footprint), tuning runs, requests, GPU minutes, downloads, settings |
| `data/logs/run-<id>.log` | Full llama-server output per run |
| `data/cache/` | Mesa shader cache |

To back Kiln up, stop the container and copy the whole `data/` folder: the database keeps a write-ahead
log next to `kiln.db`, and the two have to stay together. Model files live in `MODELS_DIR`, not in `data/`.

Export requests as CSV from the Overview or Requests page, or query the database directly:

```bash
sqlite3 data/kiln.db "select model, count(*), sum(predicted_n), sum(predicted_n)/(sum(predicted_ms)/1000) from requests group by model"
```
