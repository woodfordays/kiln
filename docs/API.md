# Kiln control API

The control port (`http://<host>:8090`) serves the web UI and a JSON API under `/api`. The web UI
uses nothing else, so anything the UI does, a script or browser extension can do too. This page
is the reference for building such a client. The quick-action endpoints a browser extension needs
come first, then the full endpoint list.

FastAPI also serves a generated schema at `/api/openapi.json` and an explorer at `/api/docs`.
They list every route and body field. This page adds what the schema can't: semantics,
states, errors and how the pieces fit.

The OpenAI-compatible inference API is a different port (`:8080`, `/v1/...`) and is not covered
here. It is llama-server's own API behind Kiln's recording proxy.

## Contents

- [Conventions](#conventions): base URL, JSON, errors, status codes
- [Authentication](#authentication): Basic, Bearer token, the event-stream token
- [Cross-origin access (CORS)](#cross-origin-access-cors): extensions are allowed, websites aren't
- [Quick actions](#quick-actions): `GET /api/quick`, run a preset, stop, restart
- [Live events](#live-events): `GET /api/events` (server-sent events)
- [Building the Zen extension](#building-the-zen-extension): manifest, flows, sample code
- [Endpoint reference](#endpoint-reference): every route

## Conventions

- **Base URL**: `http://<host>:<KILN_UI_PORT>` (default port `8090`). All API paths start with `/api/`.
- **Bodies** are JSON (`Content-Type: application/json`). A `POST` with no body fields still needs `{}`
  or an empty body.
- **Times** are Unix seconds as floats (`1792026466.29`). Durations are named `*_s` (seconds) or
  `*_ms` (milliseconds).
- **Errors** return `{"detail": "<sentence>"}`. The sentence is written for people, so a client can
  show it as is ("The server is already running. Stop it first, or use restart.").

| Status | Meaning |
|---|---|
| `200` | OK |
| `204` | CORS preflight answered |
| `400` | Bad input: missing field, unknown option, model file not found |
| `401` | Missing or wrong credentials (only when auth is on) |
| `404` | That preset, run, download or tuning run doesn't exist |
| `409` | Busy or conflicting: server already running, tuner holds the server, name taken |
| `500` | Unexpected server-side error |
| `507` | Not enough disk space for a download |

## Authentication

Auth is **off** unless `KILN_UI_PASSWORD` is set. With it off, every request is allowed. With it
on, every path except `/healthz` needs one of these credentials:

| Credential | Header | Notes |
|---|---|---|
| UI login | `Authorization: Basic base64(kiln:<KILN_UI_PASSWORD>)` | Same login the browser asks for. |
| API token | `Authorization: Bearer <KILN_API_TOKEN>` | For scripts and the extension, so they never hold the UI password. Only works if `KILN_API_TOKEN` is set. |
| Event-stream token | `GET /api/events?access_token=<KILN_API_TOKEN>` | `EventSource` can't send headers. The query form is accepted on `/api/events` only. |

A good token is long and random:

```bash
echo "KILN_API_TOKEN=$(openssl rand -hex 24)" >> .env && docker compose up -d
```

Setting `KILN_API_TOKEN` without `KILN_UI_PASSWORD` does nothing, because auth stays off.

A `401` sent to a cross-origin caller (an extension) omits `WWW-Authenticate`, so the browser
doesn't pop up its own login dialog over the extension.

## Cross-origin access (CORS)

`/api/*` and `/healthz` answer cross-origin requests from origins matching `KILN_CORS_ORIGINS`, a regular
expression. The default, `^(moz|chrome)-extension://[0-9a-zA-Z-]+$`, admits any browser
extension (Firefox and Zen use `moz-extension://<uuid>`, Chromium browsers `chrome-extension://<id>`)
and **no websites**, so a page you visit can't read Kiln's API through your browser.

- Preflight `OPTIONS` requests are answered (`204`) before auth, since browsers never attach
  credentials to them. Allowed methods: `GET, POST, PUT, DELETE`. Allowed headers: `Authorization, Content-Type`.
- Responses to matching origins carry `Access-Control-Allow-Origin: <origin>`, including `401`s,
  so the extension can read the status and prompt for a token.
- To pin Kiln to one extension, set the exact origin, for example
  `KILN_CORS_ORIGINS=^moz-extension://1a2b3c4d-…$`. Firefox and Zen give every install its own
  UUID, which you can see in `about:debugging`. An empty value means the default; to turn CORS
  off, set a pattern that matches no origin, `KILN_CORS_ORIGINS=^$`.

In Firefox and Zen, an extension that has been granted host permission for the Kiln URL can also
make requests without CORS. The headers matter when that permission isn't granted, and for
Chromium-based builds.

## Quick actions

These cover a quick-action popup: show what's running, switch to a preset, stop, restart.

### `GET /api/quick`

Everything the popup needs in one call. Poll it while the popup is open (every 2 s is plenty),
or call it once and then follow [live events](#live-events).

```json
{
  "server": {
    "state": "ready",
    "model": "Qwen3-30B-A3B-Q4_K_M",
    "model_path": "/models/gguf/unsloth/Qwen3-30B-A3B-GGUF/Qwen3-30B-A3B-Q4_K_M.gguf",
    "preset": { "id": 2, "name": "Qwen3 30B · 64K" },
    "uptime_s": 5423.1,
    "loading_s": null,
    "error": null,
    "blocked": null,
    "n_ctx": 65536,
    "api_port": 8080
  },
  "presets": [
    {
      "id": 2,
      "name": "Qwen3 30B · 64K",
      "description": "Tuned Oct 14, 2026 for decode: …",
      "model": "/models/gguf/unsloth/Qwen3-30B-A3B-GGUF/Qwen3-30B-A3B-Q4_K_M.gguf",
      "model_name": "Qwen3-30B-A3B-Q4_K_M",
      "model_missing": false,
      "last_used": 1792026466.29,
      "active": true,
      "autostart": false
    }
  ],
  "instances": [
    { "run_id": 31, "name": "qwen", "role": "chat", "state": "ready", "preset": { "id": 2, "name": "Qwen3 30B · 64K" },
      "model": "Qwen3-30B-A3B-Q4_K_M", "n_ctx": 65536, "error": null }
  ],
  "tune": { "running": false, "tune_id": 7, "stage": "", "trial": "" },
  "downloads": { "active": 0 },
  "gpu": { "t": 1792031889.2, "busy": 0.97, "vram_used": 21474836480, "vram_total": 25753026560,
           "power_w": 241.0, "temp_c": 71.0, "sclk_mhz": 2480.0, "gpus": 1 },
  "server_time": 1792031889.6
}
```

Several models can run at once, each in its own llama-server. **`instances`** lists them (`name` is the API
name clients put in `model`; `role` is `chat`, `embedding`, `rerank` or `decision`). **`server`** keeps the
fields a single-model client expects and describes the *primary* model, the chat model used last; its `state`
is `ready` if any model is, else `loading`, `stopping`, `crashed` or `stopped`.

**`server`**

| Field | Meaning |
|---|---|
| `state` | See the server states table below. |
| `model` | Display name of the loaded model, `""` if none has run yet. |
| `preset` | `{id, name}` of the preset that was started, or `null` for an ad-hoc model. The value stays after a stop, so check `state`. |
| `uptime_s` | Seconds since the model became ready. Set only in `ready`. |
| `loading_s` | Seconds since the start. Set only in `loading`. |
| `error` | Last crash line from the log when `state` is `crashed`, otherwise `null`. |
| `blocked` | A sentence while the tuner holds the server, otherwise `null`. Start and restart return `409` while it's set. Show the sentence and disable the run buttons. |
| `n_ctx` | Context size llama-server reported, once ready. |
| `api_port` | Public port of the OpenAI-compatible API, so a client can show `http://<host>:<api_port>/v1`. |

**Server states**

| `state` | Meaning | Quick actions that make sense |
|---|---|---|
| `stopped` | Nothing running | Run a preset |
| `loading` | Process started, model loading (`/health` not OK yet) | Stop, or run another preset |
| `ready` | Serving requests | Stop, restart, run another preset |
| `stopping` | Shutting down (SIGTERM, SIGKILL after 15 s) | Wait |
| `crashed` | Exited unexpectedly. `error` says why | Run a preset, restart |

**`presets`**: every preset, most recently started first, then never-used ones by name.

| Field | Meaning |
|---|---|
| `active` | A model started from this preset is loading or running now (several can be). |
| `role` | `chat`, `embedding`, `rerank` or `decision`. |
| `model_missing` | The model file isn't on disk (deleted or moved). Starting it returns `400`, so grey it out. |
| `autostart` | Starts when the container starts (set in Settings; several presets can). |
| `last_used` | Last start time, `null` if never started. |

**`tune`**: the tuner. `running` is `true` during a run, and then `server.blocked` is set too.
`stage` and `trial` describe the current candidate, for example `"Grid"` and `"-ctk q8_0 -ctv q8_0 · -fa on · -ub 512 -b 2048 · no spec"`.

**`downloads.active`**: queued plus downloading Hugging Face downloads.

**`gpu`**: the latest 2-second GPU sample, `null` if no GPU telemetry is available. `busy` is
0–1, VRAM is in bytes, and every field can be `null` when the driver doesn't report it.

### Run a preset: `POST /api/server/start`

```json
{ "preset_id": 2, "replace": true }
```

`replace` says what happens to the models that are already running:

| `replace` | Effect |
|---|---|
| `true` | Stops every running model, then starts the preset, in one locked operation. It's the one call a "switch to this preset" button needs, and what the extension sends. Also restarts the preset if it is the one running. |
| absent / `false` | Starts it next to the others if it fits in GPU memory. `409` if the preset is already running. |
| `[31, 32]` | Stops those run ids first (as listed in `instances`). |
| `"auto"` | Stops idle, unpinned models, least recently used first, until it fits. |

Kiln estimates what the model needs (from the GGUF header and the flags, or the VRAM it took last time)
and compares it with the GPU memory that is free after the stops. If it doesn't fit the call returns `409`
with `code: "no_fit"`, the `plan`, and `evict`, the models whose stopping would make room. Send `force: true` to
start anyway. `role` overrides the preset's role. `POST /api/server/plan` takes the same body and only
answers whether it fits.

The model file is checked **before** anything is stopped. If the preset points to a missing file,
the call returns `400` and what is running keeps running.

Returns the fleet status (the `server` object above, plus `instances`, `vram`, and for a quick client the
primary model's `run_id`, `pid`, `argv`, `env`, `started_at`, `ready_at`, `slots`, `build`, `modalities`,
`buffers`) and `started`, the new model's own status. The state is `loading` at this point. Loading takes
seconds to minutes, so follow `/api/quick` or the `status` event until the state is `ready` or `crashed`.

Other body forms: `{"model": "/models/x.gguf", "flags": [{"flag": "-c", "value": "8192"}], "env": {}}`
starts an ad-hoc model. Adding `"model"` alongside `preset_id` overrides the preset's model.

| Error | When |
|---|---|
| `404` | The preset no longer exists, or a `replace` run id is gone. |
| `400` | The preset has no model, its model file is missing, or its role can't be used with that model. |
| `409` | The tuner holds the server (`blocked`), the preset is already running, or it doesn't fit (`code: "no_fit"`). |

### Stop: `POST /api/server/stop`

Body `{"run_id": 31}` stops one model; no body (or `{}`) stops all of them. Waits for the processes to exit
(up to 15 s, then kills them) and returns the status. Stopping what is already stopped is fine.

### Restart: `POST /api/server/restart`

Body `{"run_id": 31}`, or none for the primary model. Stops and starts the same configuration again. If it
was a preset, edits saved to that preset since it started are picked up. Returns the status. `409` if
nothing has run yet or the tuner holds the server.

### Forget a crash: `POST /api/server/dismiss`

Body `{"run_id": 31}`. A crashed model stays in `instances` with its `error` until dismissed or started again.

### Health: `GET /healthz`

`{"ok": true, "server": "<state>"}`. Never needs auth, so an extension can use it to tell
"Kiln unreachable" apart from "wrong token" (`/api/quick` returns `401`).

## Live events

`GET /api/events` is a server-sent event stream (`text/event-stream`). It sends a `status` event
on connect and a `: keepalive` comment every 15 s. Each event's `data` is JSON.

```js
const es = new EventSource(`${base}/api/events?access_token=${encodeURIComponent(token)}`);
es.addEventListener('status', (e) => render(JSON.parse(e.data)));
```

| Event | Data | When |
|---|---|---|
| `status` | Full fleet status (as from `POST /api/server/start`), `instances` included | Any state change of any model |
| `presets` | `{"changed": true}` | A preset was created, edited, duplicated, deleted, or saved by the tuner. Refetch `/api/quick`. |
| `tune` | Tuning run row plus `live` (`{running, tune_id, stage, trial}`) | Each tuner step, and once at the end with `status` set to `done`, `failed` or `cancelled` |
| `tune_note` | `{"message": "..."}` | After tuning, if the previous server couldn't be restarted |
| `download` | Download row (`id, repo, variant, status, done_bytes, total_bytes, speed, eta_s, error`), or `{id, removed: true}` | Queue changes and progress (up to twice a second). `status`: `queued`, `downloading`, `done`, `failed`, `cancelled` |
| `idle_unload` | `{"run_id", "name", "idle_s"}` | Kiln is stopping a model because it was idle for the idle timeout. The `status` events that follow show it leaving `instances` |
| `models` | `{"changed": true}` | The model library changed |
| `request` | One recorded inference request (tokens, speeds, timings), or `{"reset": true}` | Every completion through the API port |
| `log` | `{"run_id", "lines": [[ts, text, source], ...]}` | llama-server output, batched |
| `gpu` | GPU sample (as `gpu` above) | Every 2 s |

`request`, `log` and `gpu` are high-volume. A popup that only needs state can ignore them.

The UI's background notifications use these transitions: a `tune` event whose `status` changes
from `running` to `done`/`failed`/`cancelled`; a `download` event whose `status` turns `done` or
`failed`; an entry of a `status` event's `instances` going `loading → ready` or to `crashed` (track them
by `run_id`). A notifying extension can use the same rules.

## Building the Zen extension

Kiln ships one: [extension/](../extension/README.md) is a working Zen/Firefox extension built on
exactly this API, and is the reference implementation for what follows (`extension/lib/kiln.js` is the
client, `extension/background.js` the notification rules). This section explains the pattern for
anyone extending it or writing another client.

Zen is Firefox-based, so it runs Firefox WebExtensions.

**Settings to store** (`browser.storage.local`): the Kiln base URL (for example `http://ai-box:8090`)
and, if auth is on, the API token.

**Manifest** (MV3 shown):

```json
{
  "manifest_version": 3,
  "name": "Kiln quick actions",
  "version": "0.1.0",
  "action": { "default_popup": "popup.html" },
  "permissions": ["storage", "notifications"],
  "optional_host_permissions": ["http://*/*", "https://*/*"],
  "content_security_policy": { "extension_pages": "script-src 'self'" },
  "background": { "scripts": ["background.js"], "type": "module" },
  "browser_specific_settings": { "gecko": { "id": "kiln@example.local" } }
}
```

The `content_security_policy` line matters: Firefox's default MV3 policy adds
`upgrade-insecure-requests`, which silently turns `http://ai-box:8090` into `https://…` and breaks
a plain-HTTP Kiln. Zen's HTTPS-Only Mode does the same unless the Kiln host has an exception.

Request host permission for the configured URL from the options page
(`browser.permissions.request({ origins: ['http://ai-box/*'] })`; match patterns take a host,
not a port, and the call must happen directly in a click handler, before any `await`). Firefox makes MV3 host permissions
optional, and without them the request falls back to CORS, which Kiln allows by default anyway.

**Client:**

```js
async function kiln(path, { method = 'GET', body } = {}) {
  const { base, token } = await browser.storage.local.get(['base', 'token']);
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const r = await fetch(base + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await r.json().catch(() => null);
  if (!r.ok) throw Object.assign(new Error(data?.detail || `HTTP ${r.status}`), { status: r.status });
  return data;
}

const snapshot = () => kiln('/api/quick');
const runPreset = (id) => kiln('/api/server/start', { method: 'POST', body: { preset_id: id, replace: true } });
const stop = () => kiln('/api/server/stop', { method: 'POST', body: {} });
const restart = () => kiln('/api/server/restart', { method: 'POST', body: {} });
```

**Popup flow:**

1. On open, call `snapshot()`. On a network error, Kiln is unreachable (`/healthz` needs no
   token, so the settings page can use it to tell "wrong address" from "wrong token"). On `401`,
   ask for a token.
2. Render the server line (`state`, `model`, `preset.name`, uptime) and the preset list
   (`active` gets a check, `model_missing` greyed out). If `server.blocked` is set, show it and
   disable the actions.
3. Clicking a preset calls `runPreset(id)`. Poll `snapshot()` every 1–2 s until `state` is
   `ready` or `crashed`. On `crashed`, show `server.error`.
4. Show Stop when `state` is `loading` or `ready`. Show Restart when `ready` or `crashed`.
5. Show `err.message` from any failed call: it is Kiln's `detail` sentence.

**Notifications (optional):** an MV3 background script in Firefox is an event page that can be
suspended, which closes an open `EventSource`. Either reconnect in `runtime.onStartup` and on
popup open, or poll `/api/quick` with `browser.alarms` (once a minute) and notify on `tune.running`
going from `true` to `false` or `state` turning `crashed`.

## Endpoint reference

All routes on the control port. "Body" lists the JSON fields. Optional ones show their default.

### Status and server

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/healthz` | | `{ok, server}`. No auth. |
| GET | `/api/status` | | Fleet status (the primary model's fields plus `instances`, `vram`) and `llama` (version, binary, devices), `backend`, `gpu` (availability and last sample), `auth`, `server_time` |
| GET | `/api/quick` | | [Quick snapshot](#get-apiquick) |
| POST | `/api/server/start` | `preset_id` or `model`+`flags`+`env`; `role`, `replace` (`true`, run ids or `"auto"`), `force=false` | Status and `started` ([details](#run-a-preset-post-apiserverstart)) |
| POST | `/api/server/plan` | Same as start | `{fits, need_mib, free_mib, reason, evict, evictable, …}`: does it fit, and what stopping would free |
| POST | `/api/server/stop` | `run_id` (none = all) | Status |
| POST | `/api/server/restart` | `run_id` (none = the primary model) | Status |
| POST | `/api/server/dismiss` | `run_id` | Status. Forgets a crashed model. |
| GET | `/api/server/logs` | `?tail=1000&run_id=` | `{run_id, lines: [[ts, text, source]]}` for one model (default: the primary one) |
| GET | `/api/roles` | | Roles (`chat`, `embedding`, `rerank`, `decision`), the flags each adds, help text |
| GET | `/api/runs` | `?limit=50` | Run history: preset, model, argv, env, start/ready/stop times, exit code, error |
| GET | `/api/runs/{id}/log` | | The run's full log as a text file |
| GET | `/api/flags` | | `llama-server --help` parsed: version, devices, flag metadata |
| GET | `/api/events` | `?access_token=` | [Server-sent events](#live-events) |

### Presets

A preset is `{id, name, description, model, role, alias, pinned, flags: [{flag, value, enabled?}], env: {KEY: VALUE},
created_at, updated_at, last_used, runs}`. `role` picks the flags Kiln adds and the API paths the model serves;
`alias` is the short name clients put in `model`; `pinned` keeps it from being stopped to make room. A flag
with `enabled: false` stays in the preset but isn't passed to llama-server. Flags Kiln manages itself (`--host`, `--port`,
`-m`/`--model`, `--metrics`) are dropped on save.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/presets` | | All presets, by name |
| POST | `/api/presets` | `name`, `description=""`, `model=""`, `flags=[]`, `env={}`, `role="chat"`, `alias=""`, `pinned=false` | The preset. `409` if the name or alias is taken. |
| PUT | `/api/presets/{id}` | Same as POST | The preset |
| POST | `/api/presets/{id}/duplicate` | | The copy (`"<name> copy"`) |
| DELETE | `/api/presets/{id}` | | `{ok}`. Also removes it from the boot list. |
| POST | `/api/presets/parse` | `command` (a full `llama-server …` line) | `{model, flags, env, dropped}`: what to import, and the Kiln-managed flags it left out |

### Models and downloads

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/models` | | `{models, roots, download_dir}`. Each model: path, name, repo, size, quant, arch, context length, `is_projector`, `is_draft`, `role_hint`, `decision`, and for a main model its `projectors` and `drafts` (DSpark, DFlash, EAGLE-3 files), … |
| DELETE | `/api/models` | `?path=` | `{removed: [paths]}`. `409` if that model is loaded. |
| GET | `/api/hf/resolve` | `?id=org/repo[:QUANT]` or a huggingface.co URL | `{repo, revision, gated, has_token, selected, variants, …}`: the downloadable quants |
| GET | `/api/downloads` | | The last 50 download rows, newest first |
| POST | `/api/downloads` | `repo`, `variant`, `files: [{path, size}]`, `revision="main"` | `{id}`. `507` if the disk is too small. |
| POST | `/api/downloads/{id}/cancel` | | `{ok}` |
| POST | `/api/downloads/{id}/retry` | | `{ok}` |
| DELETE | `/api/downloads/{id}` | | `{ok}`. Removes a finished, failed or cancelled row from the list. |

### Tuner

| Method | Path | Body / query | Returns |
|---|---|---|---|
| POST | `/api/tune` | `model`, `ctx`; `objective="decode"` (`decode`, `balanced`, `prefill`, `context`), `depth=8192`, `speculative=true`, `base_preset_id=null`, `headroom_mib=512`, `name=""`, `start_after=false`, `mode="grid"` (`grid`, `quick`), `kv_policy="precise"` (`precise`, `all`), `vary_batch=false` | `{id}`. `409` if a run is in progress. |
| POST | `/api/tune/cancel` | | `{ok}` |
| GET | `/api/tune` | `?limit=20` | `{live, tunes}`: recent runs with params, trials, best, status |
| GET | `/api/tune/{id}` | | One run, plus `live` if it's the current one |

### Stats and requests

`range` is one of `1h`, `24h`, `7d`, `30d`, `all` (default `24h`). `model` filters to one model.

| Method | Path | Query | Returns |
|---|---|---|---|
| GET | `/api/stats/summary` | `range`, `model` | `{current, previous, trend}`: totals and token-weighted speeds, the same for the period before (for deltas), and a 12-point trend |
| GET | `/api/stats/timeseries` | `range`, `model` | Bucketed decode/prefill speed, tokens, requests |
| GET | `/api/stats/scatter` | `range`, `model` | Decode speed vs context depth points |
| GET | `/api/stats/models` | `range` | Per-model table |
| GET | `/api/stats/lifetime` | | All-time totals |
| GET | `/api/stats/slots` | | `{model: slot}`: each model's fixed chart color (1–6, all-time top six) |
| POST | `/api/stats/reset` | body `{confirm: true}` | `{deleted}`. Deletes all request and GPU history. |
| GET | `/api/requests` | `limit=50` (max 500), `offset=0`, `model`, `status` (`ok`, `errors`) | `{total, rows}` |
| GET | `/api/requests.csv` | `range=all`, `model` | CSV download |
| GET | `/api/gpu/live` | | `{available, name, samples}` (last 10 min at 2 s) |
| GET | `/api/gpu/history` | `range` | 1-minute GPU averages |

### Settings

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/settings` | | `autostart_preset_ids` (started in order on boot), `swap_on_request`, `max_instances`, `idle_timeout_min` (minutes before an unused model is unloaded, default 120, `0` = never), `retention_days`, HF token state (never the token), paths, ports |
| PUT | `/api/settings` | Any of `hf_token`, `autostart_preset_ids`, `swap_on_request`, `max_instances`, `idle_timeout_min`, `retention_days` | The settings |
