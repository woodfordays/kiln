"""The API port: a passthrough proxy to llama-server that records per-request stats."""
from __future__ import annotations

import json
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from . import config, db, events, gpu
from .server import manager

TRACKED = {
    "/v1/chat/completions", "/chat/completions", "/v1/completions", "/completions", "/completion",
    "/v1/messages", "/infill", "/v1/responses", "/responses",
    "/v1/embeddings", "/embeddings", "/embedding", "/v1/rerank", "/rerank", "/reranking", "/v1/reranking",
}
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length"}
MAX_JSON = 16 << 20

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_client = httpx.AsyncClient(
    base_url=config.INTERNAL_URL,
    timeout=httpx.Timeout(None, connect=5),
    limits=httpx.Limits(max_connections=256, max_keepalive_connections=32),
)

OFFLINE_PAGE = """<!doctype html><meta charset="utf-8"><title>No model loaded</title>
<body style="font:15px system-ui;background:#0f0c0a;color:#f5f0ec;display:grid;place-items:center;height:100vh;margin:0">
<div style="max-width:28rem"><h1 style="font-size:20px">No model is loaded</h1>
<p style="color:#c4bcb6">This port serves llama-server when a model is running. Start one from the Kiln control panel.</p>
<p><a style="color:#f5f0ec;text-decoration-color:#fc6012" href="{ui}">Open Kiln</a></p></div>"""


def _offline(request: Request) -> Response:
    state = manager.state
    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
        host = request.url.hostname or "localhost"
        return HTMLResponse(OFFLINE_PAGE.format(ui=f"//{host}:{config.PUBLIC_UI_PORT}/"), status_code=503)
    msg = ("The model is still loading." if state == "loading" else
           manager.blocked or "No model is loaded. Start one from the Kiln control panel.")
    return JSONResponse({"error": {"code": 503, "type": "unavailable_error", "message": msg}}, status_code=503)


class Recorder:
    """Watches a response body for llama-server's `timings` and `usage` objects."""

    def __init__(self, endpoint: str, req_body: bytes, client: str) -> None:
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
        self._buf = b""
        self._json = bytearray()
        self.run_id, self.preset, self.model = manager.run_id, manager.preset, manager.model_name
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
        row = {
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
        }
        row["id"] = db.insert("requests", row)
        events.publish("request", row)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy(request: Request, path: str):
    if manager.state != "ready":
        return _offline(request)
    url_path = "/" + path
    body = await request.body()
    tracked = request.method == "POST" and url_path in TRACKED
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
    if tracked:
        headers.pop("accept-encoding", None)
    upstream = _client.build_request(
        request.method, url_path, params=request.query_params.multi_items(), headers=headers, content=body,
    )
    rec = Recorder(url_path, body, request.client.host if request.client else "") if tracked else None
    try:
        resp = await _client.send(upstream, stream=True)
    except httpx.TransportError as e:
        if rec:
            rec.finish(502, f"upstream unavailable: {e.__class__.__name__}")
        return JSONResponse({"error": {"code": 502, "type": "unavailable_error", "message": "llama-server is not reachable."}}, status_code=502)

    out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in HOP}
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
            if rec:
                status = resp.status_code if error is None else (499 if error == "client disconnected" else 502)
                rec.finish(status, error)

    return StreamingResponse(body_iter(), status_code=resp.status_code, headers=out_headers)


async def close() -> None:
    await _client.aclose()
