# Troubleshooting

Start with what Kiln itself tells you: the banner on the **Server** page, the model's **Log** card, and
**Settings → llama.cpp**. Most problems show up in one of those. On the host, the container's own output is:

```bash
docker compose ps                 # is it running, and is it healthy?
docker compose logs -f kiln       # Kiln's output (llama-server's is in the Server page and data/logs/)
```

**Contents**

- [The panel won't load, or a port is taken](#the-panel-wont-load-or-a-port-is-taken)
- [The GPU isn't used or isn't shown](#the-gpu-isnt-used-or-isnt-shown)
- [A model won't start](#a-model-wont-start)
- [A request fails or hangs](#a-request-fails-or-hangs)
- [Downloads](#downloads)
- [Dashboard and statistics](#dashboard-and-statistics)
- [The tuner](#the-tuner)
- [Asking for help](#asking-for-help)

## The panel won't load, or a port is taken

| Symptom | Cause and fix |
|---|---|
| The browser can't connect to `:8090`. | Check `docker compose ps`. If the container is restarting, read `docker compose logs kiln`. Ports are `KILN_UI_PORT` (control panel) and `KILN_API_PORT` (API) in `.env`; if something else holds them, change them and run `docker compose up -d`. |
| Works on the host but not from another machine. | `KILN_BIND` is `127.0.0.1`. Set it to `0.0.0.0` (the default) to publish on every interface, and check the host's firewall. |
| The browser asks for a login. | `KILN_UI_PASSWORD` is set. The user is `kiln`, the password is that value. Scripts and the browser extension can use `KILN_API_TOKEN` instead (see [Access control](CONFIGURATION.md#access-control)). |
| A browser tab at the API port (`:8080`) says "No model is loaded". | Nothing is running. Start a model from the Server page. The API port only serves a model while one is running; the control panel is on `:8090`. |
| The dashboard is empty and says "No requests yet". | Expected until the first completion. Make sure your client calls `:8080/v1`, not the control panel port. |
| A red banner on Server says "llama-server can't run". | The binary in the image is missing or broken, which usually means a failed or partial build. Rebuild with `docker compose build --no-cache` and watch for errors. |

## The GPU isn't used or isn't shown

First look at **Settings → llama.cpp**. *Devices* lists what llama-server found and *GPU telemetry* where the
readings come from. The Overview GPU card says "No GPU telemetry" when Kiln can't read the card.

**AMD or Intel (Vulkan)**

- *Devices* says "None detected": the container doesn't have the GPU. Check that `COMPOSE_FILE` in `.env`
  includes `compose.amd.yml`, which passes `/dev/dri` in (`./scripts/setup-env.sh` writes this for you).
- The container runs as your user with the host's `video` and `render` groups. If those group IDs differ from
  the defaults, set `VIDEO_GID` and `RENDER_GID` from `getent group video render`, then `docker compose up -d`.
- GPU readings come from amdgpu sysfs, so they are only available for AMD cards. Intel GPUs work through
  Vulkan too, but the dashboard has no telemetry for them.

**NVIDIA (CUDA)**

- `COMPOSE_FILE` must include `compose.nvidia.yml`, and the host needs the NVIDIA Container Toolkit
  (`./scripts/install-nvidia-toolkit.sh`) and a driver that supports the CUDA version in the image (570 or newer
  for the default 12.8).
- An image compiled for one GPU architecture won't run on another generation. Rebuild after `setup-env.sh`
  on each machine, or build once with `CUDA_ARCHITECTURES=default`. See [GPU notes](CONFIGURATION.md#gpu-notes).
- Readings come from `nvidia-smi`, which the toolkit mounts into the container.

**Without telemetry** Kiln can't tell how much VRAM is free, so it can't check whether another model fits and
starts models without asking.

## A model won't start

| Symptom | Cause and fix |
|---|---|
| **"Doesn't fit right now"** on the Start card, or a *"<preset> doesn't fit"* dialog. | Kiln estimates what the model needs and keeps 512 MiB spare. Options: stop an idle model (the toggle on the Start card does this for unpinned ones), lower `-c`, use a quantized KV cache (`-ctk q8_0 -ctv q8_0`), offload less (`-ngl`, or `--n-cpu-moe` for a mixture-of-experts model), or **Start anyway** if you know better. Run the [tuner](TUNER.md) with *Context length* to find the largest context that fits. |
| A red banner says the model **exited unexpectedly** (and a notification says it crashed or failed to load). | Open the model's log on Server: the last lines are llama-server's own explanation. An out-of-memory error (for Vulkan, an allocation that *failed*) means the settings were too big: use the options above, and add `--fit off` only when you want exact settings. The banner stays until you **Dismiss** it or start the model again. |
| The log says **`unknown model architecture: '<name>'`**. | The model is newer than the llama.cpp build in your image. No flag fixes this. Set `LLAMA_CPP_REF` in `.env` to a commit at or after the one that added the architecture, and rebuild. See [Upgrading llama.cpp](CONFIGURATION.md#upgrading-llamacpp). |
| Start is greyed out, or a banner says **"The tuner has the GPU"**. | A tuning run is in progress. Wait for it or cancel it on the Tuner page. |
| "Model file not found: …". | The preset's model was deleted or moved. Pick another model in the preset editor, or download it again. |
| The editor marks a flag as **unknown**. | This `llama-server` build doesn't list it, so it will probably refuse to start. The flag list is read from the binary's `--help`, so it follows upgrades: after a llama.cpp upgrade, open your presets and look for marked flags. |
| `/v1/systemone` answers `501` "This model is not a decision model". | The request reached a model without decision metadata, or a Perplexity decider checkpoint that stock llama-server can't score. Give the preset the **Decision** role: Kiln then routes `/v1/systemone` to it and scores those checkpoints itself. See [Decision models](MULTI-MODEL.md#decision-models). |
| Loading is slow only the first time after a rebuild (Vulkan). | Shader compilation. RADV's cache is kept in `./data/cache/mesa`, so later loads are quicker. |
| The first start after a long time takes minutes. | Large models read from disk. Kiln waits up to 15 minutes (`KILN_SWAP_TIMEOUT`) for a model a request is waiting on. |

## A request fails or hangs

| Symptom | Cause and fix |
|---|---|
| `503` with "No model is loaded. Start one from the Kiln control panel." | Nothing is running, or the one the request needs isn't. Start it, or turn on **Load models when a request names them** in Settings. |
| `503` with "The model is still loading." | Loading takes a while for large models. Retry, or send requests only after the Overview shows the model as Running. |
| `503` and a message about the tuner. | The tuner holds the GPU while it runs, and the API port answers `503` until it finishes. |
| A request names a preset and waits. | **Load models when a request names them** is on: the request waits while the preset loads, and stops idle unpinned models first if there isn't room. Streaming clients get keep-alive comment lines meanwhile. The wait shows in the **Model load** column on Requests. |
| `model` is "ignored", and the wrong model answers. | A name that matches nothing falls through to the chat model used last, so old clients keep working. Match the preset's **API name**, the preset name, its slug, an `--alias`, or the model file name, or send `X-Kiln-Model: <name>` or `?model=<name>`. Every response carries `X-Kiln-Model` naming the model that answered. |
| A model you were using is suddenly gone. | **Unload idle models after** (Settings, 2 hours by default) stopped it, or another model needed its room. Tick **Keep loaded** on the preset to prevent both. You'll get a notification when an idle model is unloaded. |
| `401` from `:8080`. | The preset has `--api-key …` and the request has no matching key. Kiln passes the header through. |
| `401` from `:8090/api`. | `KILN_UI_PASSWORD` is on. Use Basic auth (user `kiln`), or `Authorization: Bearer <KILN_API_TOKEN>`. |

## Downloads

| Message | Meaning |
|---|---|
| "Hugging Face refused access. If the repo is gated or private, add a token in Settings." | The repository needs a token. Create one at huggingface.co/settings/tokens (read access is enough) and save it in **Settings → Hugging Face**. |
| "Hugging Face refused the download. Accept the model's terms on huggingface.co and add a token in Settings." | A gated model: accept its terms on the model page first. |
| "No model repository named …" | The ID is wrong. Copy it from the repository page, or paste its link. |
| "That repository has no GGUF files. Look for a repo whose name ends in -GGUF." | It holds weights in another format. |
| "Not enough disk space: needs … GB, … GB free." | Free space on the disk that holds `MODELS_DIR`. |
| "Network error: … Retry when the connection is back." | Downloads resume from the `.part` file. Press **Retry**. |

Downloaded files belong to the user the container runs as (`KILN_UID` and `KILN_GID`, your own by default).
If `MODELS_DIR` isn't writable by that user, downloads fail; fix the folder's ownership.

## Dashboard and statistics

- **Numbers mix several models.** Pick a model on the Overview filter for a like-for-like speed. Embedding and
  decision requests have no decode speed and don't appear in the decode charts.
- **The graphs look sparse.** Lines connect consecutive time buckets, so a model with no requests in a bucket
  leaves a gap. Choose a longer range.
- **Speeds look lower than the tuner's.** The tuner measures a fixed benchmark with greedy decoding and an
  empty prompt cache. Real traffic has long prompts, sampling, and context that grows, which is why the depth chart
  exists.
- **History disappeared.** *Keep request history* in Settings deletes old records on a schedule, and
  **Reset statistics** deletes everything. Run history is never deleted.
- **GPU graphs stop.** The GPU card shows the last ten minutes from the sampler; if Kiln can't read the card it
  says "No GPU telemetry". See [the GPU section](#the-gpu-isnt-used-or-isnt-shown).

## The tuner

| Message | Meaning |
|---|---|
| "No configuration fits … tokens of context with 512 MiB to spare. Lower the target context." | Even the smallest KV cache type didn't fit. Lower the context, or pick *Context length* as the objective, which moves weights to system RAM. |
| "This model's layer count is unknown." | The tuner can't spill that model to system RAM. Use another objective. |
| Tuning was "interrupted by a restart". | The container restarted mid-run. Measured candidates are kept; nothing was saved. |
| "Could not restart the previous server". | The tuner stopped your models and couldn't bring one back. Start it from Server. |

The tuner stops every running model while it works and restores them afterwards, so don't start one while
people are using the API.

## Asking for help

When you open an issue, include:

1. **Settings → llama.cpp**: backend, version, devices.
2. The model's **log** from the Server page (the download button saves `data/logs/run-<id>.log`) and the
   **Command** card.
3. `docker compose logs kiln` if the problem is Kiln itself rather than a model.
4. Your `.env`, with `HF_TOKEN`, `KILN_UI_PASSWORD` and `KILN_API_TOKEN` removed.
