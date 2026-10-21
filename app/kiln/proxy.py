"""The API port: sends each request to the right llama-server, swaps models in on demand, and records
per-request stats."""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from . import config, db, decider, events, gpu
from .route import resolve
from .server import NoFit, get_preset, manager

TRACKED = {
    "/v1/chat/completions", "/chat/completions", "/v1/completions", "/completions", "/completion",
    "/v1/messages", "/infill", "/v1/responses", "/responses",
    "/v1/embeddings", "/embeddings", "/embedding", "/v1/rerank", "/rerank", "/reranking", "/v1/reranking",
    "/v1/systemone",
}
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length"}
MAX_JSON = 16 << 20
PARSE_LIMIT = 4 << 20  # bigger bodies (images) are searched for the model name instead of parsed
KEEPALIVE_S = 5

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_client = httpx.AsyncClient(
    timeout=httpx.Timeout(None, connect=5),
    limits=httpx.Limits(max_connections=256, max_keepalive_connections=32),
)

OFFLINE_PAGE = """<!doctype html><meta charset="utf-8"><title>No model loaded</title>
<body style="font:15px system-ui;background:#0f0c0a;color:#f5f0ec;display:grid;place-items:center;height:100vh;margin:0">
<div style="max-width:28rem"><h1 style="font-size:20px">No model is loaded</h1>
<p style="color:#c4bcb6">This port serves llama-server when a model is running. Start one from the Kiln control panel.</p>
<p><a style="color:#f5f0ec;text-decoration-color:#fc6012" href="{ui}">Open Kiln</a></p></div>"""


CORS = {"Access-Control-Allow-Origin": "*"}  # llama-server sends this on its own answers; Kiln's must match


def _error(status: int, message: str, kind: str = "unavailable_error") -> JSONResponse:
    return JSONResponse({"error": {"code": status, "type": kind, "message": message}}, status_code=status, headers=CORS)


def _offline(request: Request, reason: str = "") -> Response:
    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
        host = request.url.hostname or "localhost"
        return HTMLResponse(OFFLINE_PAGE.format(ui=f"//{host}:{config.PUBLIC_UI_PORT}/"), status_code=503)
    state = manager.state
    msg = (manager.blocked or reason or ("The model is still loading." if state == "loading" else
                                         "No model is loaded. Start one from the Kiln control panel."))
    return _error(503, msg)


# ---------------------------------------------------------------- routing inputs

def _model_name(request: Request, body: bytes) -> str | None:
    named = request.headers.get("x-kiln-model") or request.query_params.get("model")
    if named:
        return named
    if request.method != "POST" or not body or body[:1] != b"{":
        return None
    if len(body) <= PARSE_LIMIT:
        try:
            m = json.loads(body).get("model")
            return m if isinstance(m, str) and m else None
        except (ValueError, AttributeError):
            return None
    m = re.search(rb'"model"\s*:\s*"((?:[^"\\]|\\.)*)"', body[:8192]) or re.search(rb'"model"\s*:\s*"((?:[^"\\]|\\.)*)"', body[-8192:])
    try:
        return json.loads(b'"' + m.group(1) + b'"') if m else None
    except ValueError:
        return None


def _wants_stream(body: bytes) -> bool:
    if len(body) > PARSE_LIMIT:
        return False
    try:
        return bool(json.loads(body).get("stream"))
    except (ValueError, AttributeError):
        return False


def _presets() -> list[dict]:
    return db.rows("SELECT id, name, alias, role, model, pinned, "
                   "(SELECT MAX(started_at) FROM runs WHERE preset_id = presets.id) AS last_started FROM presets")


def _swap_enabled() -> bool:
    return bool(db.get_settings().get("swap_on_request"))


def _api_keys(flag_rows: list[dict]) -> list[str]:
    """Keys a preset's llama-server would require (--api-key, --api-key-file); none means it is open."""
    keys: list[str] = []
    for r in flag_rows:
        if r.get("enabled") is False or not r.get("value"):
            continue
        flag = (r.get("flag") or "").strip()
        if flag == "--api-key":
            keys += [k.strip() for k in str(r["value"]).split(",") if k.strip()]
        elif flag == "--api-key-file":
            try:
                with open(str(r["value"])) as f:
                    keys += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
            except OSError:
                pass
    return keys


def _may_load(request: Request, preset: dict) -> bool:
    """A request may make Kiln load a preset only if it would be let in once the model is up: presets
    with an API key stay unloaded for requests without it, so a stranger can't churn the GPU."""
    p = get_preset(preset["id"])
    keys = _api_keys(p["flags"]) if p else []
    if not keys:
        return True
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("x-api-key", "").strip()
    return any(secrets.compare_digest(token, k) for k in keys)


# ---------------------------------------------------------------- stats

def _insert(row: dict) -> None:
    row["id"] = db.insert("requests", row)
    events.publish("request", row)


class Recorder:
    """Watches a response body for llama-server's `timings` and `usage` objects."""

    def __init__(self, endpoint: str, req_body: bytes, client: str, inst, swap_ms: float | None = None) -> None:
        self.endpoint = endpoint
        self.client = client
        self.t0 = time.time()
        self.p0 = time.perf_counter()
        self.ttft_ms: float | None = None
        self.ttfb_ms: float | None = None
        self.timings: dict | None = None
        self.usage: dict | None = None
        self.finish_reason: str | None = None
        self.sse = False
        self.swap_ms = swap_ms
        self._buf = b""
        self._json = bytearray()
        self.run_id, self.preset, self.model = inst.run_id, inst.preset, inst.model_name
        try:
            body = json.loads(req_body) if req_body else {}
            self.stream = bool(body.get("stream")) if isinstance(body, dict) else False
        except ValueError:
            self.stream = False

    def _elapsed(self) -> float:
        return (time.perf_counter() - self.p0) * 1000

    def _take(self, obj) -> None:
        if not isinstance(obj, dict):
            return
        if "timings" in obj and isinstance(obj["timings"], dict):
            self.timings = obj["timings"]
        u = obj.get("usage")
        if isinstance(u, dict) and u:
            self.usage = u
        for ch in obj.get("choices") or []:
            if isinstance(ch, dict) and ch.get("finish_reason"):
                self.finish_reason = ch["finish_reason"]
        if obj.get("stop_type"):
            self.finish_reason = obj["stop_type"]
        if obj.get("stop_reason"):
            self.finish_reason = obj["stop_reason"]
        d = obj.get("delta")
        if isinstance(d, dict) and d.get("stop_reason"):
            self.finish_reason = d["stop_reason"]

    @staticmethod
    def _has_content(obj) -> bool:
        if not isinstance(obj, dict):
            return False
        if obj.get("content") or obj.get("text"):
            return True
        for ch in obj.get("choices") or []:
            if not isinstance(ch, dict):
                continue
            d = ch.get("delta") or {}
            if ch.get("text") or d.get("content") or d.get("reasoning_content") or d.get("tool_calls"):
                return True
        d = obj.get("delta")
        if isinstance(d, dict) and (d.get("text") or d.get("thinking") or d.get("partial_json")):
            return True
        return obj.get("type") in ("response.output_text.delta", "response.reasoning_text.delta")

    def start(self, content_type: str) -> None:
        self.sse = "text/event-stream" in content_type

    def feed(self, chunk: bytes) -> None:
        if self.ttfb_ms is None:
            self.ttfb_ms = self._elapsed()
        if not self.sse:
            if len(self._json) < MAX_JSON:
                self._json.extend(chunk)
            return
        self._buf += chunk
        *lines, self._buf = self._buf.split(b"\n")
        for line in lines:
            if not line.startswith(b"data:"):
                continue
            need_first = self.ttft_ms is None
            if not need_first and b'"timings"' not in line and b'"usage"' not in line and b"finish_reason" not in line and b"stop_reason" not in line:
                continue
            try:
                obj = json.loads(line[5:].strip())
            except ValueError:
                continue
            if need_first and self._has_content(obj):
                self.ttft_ms = self._elapsed()
            self._take(obj)

    def finish(self, status: int, error: str | None = None) -> None:
        total_ms = self._elapsed()
        if not self.sse and self._json:
            try:
                self._take(json.loads(bytes(self._json)))
            except ValueError:
                pass
        t = self.timings or {}
        u = self.usage or {}
        prompt_n = t.get("prompt_n")
        predicted_n = t.get("predicted_n")
        if prompt_n is None:
            prompt_n = u.get("prompt_tokens") or u.get("input_tokens")
        if predicted_n is None:
            predicted_n = u.get("completion_tokens") or u.get("output_tokens")
        prompt_ms = t.get("prompt_ms")
        predicted_ms = t.get("predicted_ms")
        ttft = self.ttft_ms
        if ttft is None and predicted_ms is not None and predicted_n:
            # non-streaming: wall time minus generation, plus one token
            ttft = max(0.0, total_ms - predicted_ms + predicted_ms / predicted_n)
        _insert({
            "ts": self.t0,
            "run_id": self.run_id,
            "preset_id": self.preset["id"] if self.preset else None,
            "preset_name": self.preset["name"] if self.preset else None,
            "model": self.model,
            "endpoint": self.endpoint,
            "status": status,
            "stream": int(self.sse),
            "client": self.client,
            "prompt_n": prompt_n,
            "cache_n": t.get("cache_n"),
            "predicted_n": predicted_n,
            "prompt_ms": prompt_ms,
            "predicted_ms": predicted_ms,
            "prompt_tps": t.get("prompt_per_second") if prompt_n else None,
            "predicted_tps": t.get("predicted_per_second") if predicted_n else None,
            "ttfb_ms": self.ttfb_ms,
            "ttft_ms": ttft,
            "total_ms": total_ms,
            "draft_n": t.get("draft_n"),
            "draft_accepted": t.get("draft_n_accepted"),
            "finish_reason": self.finish_reason,
            "power_w": gpu.sampler.mean_power(self.t0, time.time()),
            "error": error,
            "swap_ms": self.swap_ms or None,
        })


def _record_failure(endpoint: str, client: str, status: int, error: str, model: str | None, swap_ms: float | None = None) -> None:
    now = time.time()
    _insert({"ts": now, "endpoint": endpoint, "status": status, "stream": 0, "client": client, "model": model,
             "total_ms": swap_ms, "error": error, "swap_ms": swap_ms or None})


# ---------------------------------------------------------------- getting a ready instance

async def _acquire(route) -> tuple[object, float]:
    """The instance to use, waiting for it to load (or swapping it in), and how long that took in ms."""
    t0 = time.perf_counter()
    if route.kind == "swap":
        inst = await manager.ensure(route.preset)
    else:
        inst = route.instance
        if inst.state != "ready":
            await inst.wait_ready(config.SWAP_TIMEOUT_S)
    if inst.state != "ready" or inst.draining:
        raise RuntimeError(f"{inst.name} was stopped before it could answer.")
    waited = (time.perf_counter() - t0) * 1000
    return inst, waited if waited > 50 else 0.0


def _why(e: BaseException) -> str:
    if isinstance(e, NoFit):
        return f"{e} Stop a model in Kiln or free GPU memory, then retry."
    return str(e) or e.__class__.__name__


# ---------------------------------------------------------------- the model list

@app.get("/v1/models")
@app.get("/models")
async def model_list(request: Request):
    if manager.blocked:
        return _offline(request)
    live = {i.name: i for i in manager.live()}
    swap = _swap_enabled()

    async def one(i):
        try:
            r = await _client.get(f"{i.url}/v1/models", timeout=2)
            entry = r.json()["data"][0]
        except Exception:
            entry = {"object": "model", "created": int(i.started_at or time.time()), "owned_by": "llamacpp"}
        entry["id"] = i.name
        entry["kiln"] = {"state": "loaded" if i.state == "ready" else i.state, "role": i.role,
                         "preset_id": i.preset["id"] if i.preset else None}
        return entry

    data = list(await asyncio.gather(*(one(i) for i in live.values() if i.state == "ready")))
    for i in live.values():
        if i.state != "ready":
            data.append({"id": i.name, "object": "model", "created": int(i.started_at or 0), "owned_by": "kiln",
                         "kiln": {"state": i.state, "role": i.role, "preset_id": i.preset["id"] if i.preset else None}})
    if swap:
        for p in _presets():
            if not manager.by_preset(p["id"]) and _may_load(request, p):
                data.append({"id": p["alias"] or p["name"], "object": "model", "created": 0, "owned_by": "kiln",
                             "kiln": {"state": "unloaded", "role": p["role"], "preset_id": p["id"]}})
    if not data:
        return _offline(request)
    return JSONResponse({"object": "list", "data": data}, headers=CORS)


# ---------------------------------------------------------------- Kiln's own /v1/systemone for decider checkpoints

async def _decide(request: Request, inst, body: bytes, swap_ms: float) -> Response:
    t0 = time.time()
    client = request.client.host if request.client else ""
    inst.begin()
    status, error, info, payload = 200, None, {}, None
    try:
        try:
            payload = json.loads(body) if body else None
        except ValueError:
            raise decider.RequestError("The request body is not valid JSON.")
        result, info = await decider.decide(inst.url, inst.model_path, payload, inst.props.get("n_ctx"), _client)
        resp: Response = JSONResponse(result, headers={"X-Kiln-Model": inst.name, **CORS})
    except decider.RequestError as e:
        status, error = 400, str(e)
        resp = _error(400, error, "invalid_request_error")
    except decider.Unavailable as e:
        status, error = 502, str(e)
        resp = _error(502, error)
    except Exception as e:  # a checkpoint Kiln cannot read, for example
        status, error = 500, f"{e.__class__.__name__}: {e}"
        resp = _error(500, error, "server_error")
    finally:
        inst.end()
    total = info.get("total_ms") or (time.time() - t0) * 1000
    pn, pms = info.get("prompt_n"), info.get("prompt_ms")
    _insert({
        "ts": t0, "run_id": inst.run_id, "preset_id": inst.preset["id"] if inst.preset else None,
        "preset_name": inst.preset["name"] if inst.preset else None, "model": inst.model_name,
        "endpoint": "/v1/systemone", "status": status, "stream": 0, "client": client,
        "prompt_n": pn, "predicted_n": 0 if pn else None, "prompt_ms": pms,
        "prompt_tps": pn / (pms / 1000) if pn and pms else None, "total_ms": total, "ttfb_ms": total,
        "power_w": gpu.sampler.mean_power(t0, time.time()), "error": error, "swap_ms": swap_ms or None,
        "finish_reason": "decision" if status == 200 else None,
    })
    return resp


# ---------------------------------------------------------------- the passthrough

async def _forward(request: Request, url_path: str, body: bytes, inst, tracked: bool, swap_ms: float) -> Response:
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP and k.lower() != "x-kiln-model"}
    if tracked:
        headers.pop("accept-encoding", None)
    params = [(k, v) for k, v in request.query_params.multi_items() if k != "model"]  # ?model= only picks the route
    upstream = _client.build_request(request.method, inst.url + url_path, params=params, headers=headers, content=body)
    rec = Recorder(url_path, body, request.client.host if request.client else "", inst, swap_ms) if tracked else None
    inst.begin(touch=tracked)  # /health, /props and the like don't count as use
    closed = False

    def release() -> None:
        nonlocal closed
        if not closed:
            closed = True
            inst.end(touch=tracked)

    try:
        resp = await _client.send(upstream, stream=True)
    except httpx.TransportError as e:
        release()
        if rec:
            rec.finish(502, f"upstream unavailable: {e.__class__.__name__}")
        return _error(502, "llama-server is not reachable.")

    out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in HOP}
    out_headers["X-Kiln-Model"] = inst.name
    if rec:
        rec.start(resp.headers.get("content-type", ""))

    async def body_iter():
        error = None
        try:
            async for chunk in resp.aiter_raw():
                if rec:
                    rec.feed(chunk)
                yield chunk
        except BaseException as e:  # client went away (cancel) or upstream broke
            error = "client disconnected" if not isinstance(e, httpx.HTTPError) else f"upstream error: {e.__class__.__name__}"
            raise
        finally:
            await resp.aclose()
            release()
            if rec:
                status = resp.status_code if error is None else (499 if error == "client disconnected" else 502)
                rec.finish(status, error)

    return StreamingResponse(body_iter(), status_code=resp.status_code, headers=out_headers,
                             background=BackgroundTask(release))


async def _waiting_stream(request: Request, route, url_path: str, body: bytes, tracked: bool, name: str | None) -> Response:
    """A streaming request whose model is still loading: answer at once and keep the connection warm with
    SSE comments (clients ignore them), so a long load doesn't hit their first-byte timeout."""
    client = request.client.host if request.client else ""

    async def gen():
        task = asyncio.ensure_future(_acquire(route))
        label = route.preset["name"] if route.preset else route.instance.name
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=KEEPALIVE_S)
                if done:
                    break
                yield f": kiln is loading {label}\n\n".encode()
            inst, waited = task.result()
        except asyncio.CancelledError:
            task.cancel()
            raise
        except Exception as e:
            msg = _why(e)
            _record_failure(url_path, client, 503, msg, name)
            yield f"data: {json.dumps({'error': {'code': 503, 'type': 'unavailable_error', 'message': msg}})}\n\ndata: [DONE]\n\n".encode()
            return
        inner = await _forward(request, url_path, body, inst, tracked, waited)
        if isinstance(inner, StreamingResponse):
            async for chunk in inner.body_iterator:
                yield chunk
            if inner.background:
                await inner.background()
        else:
            yield f"data: {bytes(inner.body).decode('utf-8', 'replace')}\n\ndata: [DONE]\n\n".encode()

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy(request: Request, path: str):
    url_path = "/" + path
    if manager.blocked:
        return _offline(request)
    body = await request.body()
    name = _model_name(request, body)
    tracked = request.method == "POST" and url_path in TRACKED
    # Only completions and the like wait for a model to load or be swapped in; a status check answers at once.
    route = resolve(name, url_path, manager.live(), _presets(), _swap_enabled() and tracked)
    if route.kind == "none":
        return _offline(request, route.reason)
    if route.kind == "swap" and not _may_load(request, route.preset):
        return _error(401, "Incorrect API key provided.", "authentication_error")
    waiting = route.kind == "swap" or route.instance.state != "ready"
    if waiting and not tracked:
        return _offline(request)
    if waiting and _wants_stream(body):
        return await _waiting_stream(request, route, url_path, body, tracked, name)
    swap_ms = 0.0
    if waiting:
        try:
            inst, swap_ms = await _acquire(route)
        except Exception as e:
            msg = _why(e)
            if tracked:
                _record_failure(url_path, request.client.host if request.client else "", 503, msg, name)
            return _error(503, msg)
    else:
        inst = route.instance
    if tracked and url_path == "/v1/systemone" and inst.adapter == "pplx-v3":
        return await _decide(request, inst, body, swap_ms)
    return await _forward(request, url_path, body, inst, tracked, swap_ms)


async def close() -> None:
    await _client.aclose()
