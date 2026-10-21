"""The control port: REST API, server-sent events and the web UI."""
from __future__ import annotations

import asyncio
import base64
import csv
import io
import json
import os
import re
import secrets
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, events, fit, flags, gpu, hf, models, roles, stats
from .server import NoFit, get_preset, manager
from .tuner import get_tune, tuner

app = FastAPI(title="Kiln", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")


# ---------------------------------------------------------------- plumbing

_cors_origin = re.compile(config.CORS_ORIGINS) if config.CORS_ORIGINS else None
_CORS_HEADERS = {
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Max-Age": "600",
    "Vary": "Origin",
}


def _authorized(request: Request) -> bool:
    """Basic (UI user + password) or Bearer KILN_API_TOKEN. EventSource can't send headers, so
    the event stream also takes the token as ?access_token=."""
    h = request.headers.get("authorization", "")
    scheme, _, cred = h.partition(" ")
    scheme = scheme.lower()
    if scheme == "basic":
        try:
            user, _, pw = base64.b64decode(cred).decode().partition(":")
        except Exception:
            return False
        return secrets.compare_digest(user, config.UI_USER) and secrets.compare_digest(pw, config.UI_PASSWORD)
    token = cred.strip() if scheme == "bearer" else (
        request.query_params.get("access_token", "") if request.url.path == "/api/events" else "")
    return bool(config.API_TOKEN and token) and secrets.compare_digest(token, config.API_TOKEN)


@app.middleware("http")
async def auth_and_cors(request: Request, call_next):
    origin = request.headers.get("origin", "")
    path = request.url.path
    cors = bool(origin and _cors_origin and (path.startswith("/api/") or path == "/healthz") and _cors_origin.match(origin))
    if cors and request.method == "OPTIONS":  # preflight carries no credentials; answer it before auth
        return Response(status_code=204, headers={**_CORS_HEADERS, "Access-Control-Allow-Origin": origin})
    if config.UI_PASSWORD and request.url.path != "/healthz" and not _authorized(request):
        # No WWW-Authenticate for cross-origin callers: it would pop the browser's login dialog.
        resp = Response(status_code=401, headers={} if cors else {"WWW-Authenticate": 'Basic realm="Kiln"'})
    else:
        resp = await call_next(request)
    if cors:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
    return resp


@app.exception_handler(ValueError)
@app.exception_handler(FileNotFoundError)
@app.exception_handler(LookupError)
async def _bad_request(request: Request, exc: Exception):
    return JSONResponse({"detail": str(exc)}, status_code=404 if isinstance(exc, LookupError) else 400)


@app.exception_handler(RuntimeError)
async def _conflict(request: Request, exc: Exception):
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(PermissionError)
async def _forbidden(request: Request, exc: Exception):
    return JSONResponse({"detail": str(exc)}, status_code=403)


@app.exception_handler(OSError)
async def _os_error(request: Request, exc: Exception):
    return JSONResponse({"detail": str(exc)}, status_code=507 if "space" in str(exc) else 500)


# ---------------------------------------------------------------- status & server

@app.get("/healthz")
async def healthz():
    return {"ok": True, "server": manager.state}


@app.get("/api/status")
async def status():
    meta = flags.load()
    return {
        **manager.status(),
        "llama": {"version": meta["version"], "binary": meta["binary"], "error": meta["error"], "devices": meta["devices"]},
        "backend": os.environ.get("KILN_BACKEND") or _backend_from_devices(meta["devices"]),
        "gpu": {"available": gpu.sampler.available, "name": gpu.sampler.name, "source": gpu.sampler.source,
                "last": gpu.sampler.live[-1] if gpu.sampler.live else None},
        "auth": bool(config.UI_PASSWORD),
        "server_time": time.time(),
    }


def _backend_from_devices(devices: list[str]) -> str:
    joined = " ".join(devices)
    for key, name in (("CUDA", "cuda"), ("Vulkan", "vulkan"), ("ROCm", "rocm"), ("Metal", "metal")):
        if key in joined:
            return name
    return "cpu"


class StartBody(BaseModel):
    preset_id: int | None = None
    model: str | None = None
    flags: list[dict] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    role: str | None = None
    # true: stop every running model first, so one call switches to this preset (what the browser extension
    # sends); false or absent: start alongside; a list of run ids: stop those first; "auto": stop idle,
    # unpinned models, least recently used first, until it fits.
    replace: bool | list[int] | str | None = None
    force: bool = False  # start even if the memory estimate says it won't fit


def _launch_args(body: StartBody) -> dict:
    replace = body.replace
    if replace is True:
        replace = [i.run_id for i in manager.live() if i.state != "stopping"]
    elif replace is False:
        replace = None
    elif replace is not None and replace != "auto" and not isinstance(replace, list):
        raise ValueError('replace must be true, a list of run ids, or "auto".')
    if body.preset_id is not None:
        p = get_preset(body.preset_id)
        if not p:
            raise LookupError("That preset no longer exists.")
        return {"model": body.model or p["model"], "rows": p["flags"], "env": p["env"], "preset": p,
                "role": body.role or p["role"], "replace": replace}
    return {"model": body.model or "", "rows": body.flags, "env": body.env, "preset": None, "role": body.role,
            "replace": replace}


@app.post("/api/server/start")
async def server_start(body: StartBody):
    """Start a preset ({preset_id}) or an ad-hoc model, next to what is running if it fits. 409 with
    code "no_fit" when it doesn't (the answer lists the models that could be stopped)."""
    a = _launch_args(body)
    try:
        inst = await manager.start(a["model"], a["rows"], a["env"], preset=a["preset"], role=a["role"],
                                   force=body.force, replace=a["replace"])
    except NoFit as e:
        return JSONResponse({"detail": str(e), "code": "no_fit", "plan": e.plan, "evict": e.evict}, status_code=409)
    return {**manager.status(), "started": inst.status()}


@app.post("/api/server/plan")
async def server_plan(body: StartBody):
    """Whether a launch fits next to what is running, and what it would take to make room."""
    a = _launch_args(body)
    return manager.plan(a["model"], a["rows"], a["preset"], a["role"], a["replace"])


class RunBody(BaseModel):
    run_id: int | None = None  # none = every server


@app.get("/api/quick")
async def quick():
    """Everything a quick-action client (the browser extension) needs, in one small call."""
    s = manager.status()
    active = {i["preset"]["id"] for i in s["instances"] if i["preset"] and i["state"] in ("loading", "ready")}
    autostart = set(db.get_settings().get("autostart_preset_ids") or [])
    presets = []
    for r in db.rows("SELECT id, name, description, model, role FROM presets"):
        r["model_name"] = models.display_name(r["model"]) if r["model"] else ""
        r["model_missing"] = not (r["model"] and os.path.exists(r["model"]))
        r["last_used"] = db.scalar("SELECT MAX(started_at) FROM runs WHERE preset_id=?", (r["id"],))
        r["active"] = r["id"] in active
        r["autostart"] = r["id"] in autostart
        presets.append(r)
    presets.sort(key=lambda r: (-(r["last_used"] or 0), r["name"].lower()))
    return {
        "server": {k: s[k] for k in ("state", "model", "model_path", "preset", "uptime_s", "loading_s",
                                      "error", "blocked", "n_ctx", "api_port")},
        "instances": [{k: i[k] for k in ("run_id", "name", "role", "state", "preset", "model", "n_ctx", "error")}
                      for i in s["instances"]],
        "presets": presets,
        "tune": tuner.status(),
        "downloads": {"active": db.scalar("SELECT COUNT(*) FROM downloads WHERE status IN ('queued','downloading')")},
        "gpu": gpu.sampler.live[-1] if gpu.sampler.live else None,
        "server_time": time.time(),
    }


@app.post("/api/server/stop")
async def server_stop(body: RunBody | None = None):
    await manager.stop(body.run_id if body else None)
    return manager.status()


@app.post("/api/server/restart")
async def server_restart(body: RunBody | None = None):
    await manager.restart(body.run_id if body else None)
    return manager.status()


@app.post("/api/server/dismiss")
async def server_dismiss(body: RunBody):
    if body.run_id is not None:
        manager.dismiss(body.run_id)
    return manager.status()


@app.get("/api/server/logs")
async def server_logs(tail: int = 1000, run_id: int | None = None):
    return manager.logs(run_id, tail)


@app.get("/api/roles")
async def roles_list():
    return roles.ROLES


@app.get("/api/runs")
async def runs(limit: int = 50):
    rows = db.rows("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        r["argv"] = json.loads(r["argv"])
        r["env"] = json.loads(r["env"])
    return rows


@app.get("/api/runs/{run_id}/log")
async def run_log(run_id: int):
    path = config.LOG_DIR / f"run-{run_id}.log"
    if not path.exists():
        raise LookupError("No log saved for that run.")
    return FileResponse(path, media_type="text/plain", filename=f"llama-server-run-{run_id}.log")


@app.get("/api/flags")
async def flag_meta():
    return flags.load()


# ---------------------------------------------------------------- presets

class PresetBody(BaseModel):
    name: str
    description: str = ""
    model: str = ""
    flags: list[dict] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    role: str = "chat"
    alias: str = ""  # the short name API clients put in "model"
    pinned: bool = False  # never stopped to make room for another model


ALIAS_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/\- ]{0,63}")


def _check_alias(alias: str, pid: int | None) -> None:
    if not alias:
        return
    if not ALIAS_RE.fullmatch(alias):
        raise ValueError("An alias can use letters, digits and . _ : / - and spaces, up to 64 characters.")
    clash = db.scalar("SELECT name FROM presets WHERE id IS NOT ? AND (lower(name)=lower(?) OR lower(alias)=lower(?))",
                      (pid, alias, alias))
    if clash:
        raise RuntimeError(f"“{alias}” is already used by the preset “{clash}”.")


def _clean_preset(body: PresetBody, pid: int | None = None) -> dict:
    name = body.name.strip()
    if not name:
        raise ValueError("Give the preset a name.")
    roles.valid(body.role)
    alias = body.alias.strip()
    _check_alias(alias, pid)
    if db.scalar("SELECT 1 FROM presets WHERE id IS NOT ? AND lower(alias)=lower(?) AND alias != ''", (pid, name)):
        raise RuntimeError(f"“{name}” is already used as an alias by another preset.")
    rows = []
    for r in body.flags:
        flag = str(r.get("flag") or "").strip()
        if not flag:
            continue
        if not flag.startswith("-"):
            raise ValueError(f"“{flag}” isn't a flag. Flags start with - or --.")
        if flag in flags.MANAGED:
            continue
        val = r.get("value")
        rows.append({"flag": flag, "value": None if val in (None, "") else str(val),
                     **({"enabled": False} if r.get("enabled") is False else {})})
    return {"name": name, "description": body.description.strip(), "model": body.model.strip(),
            "flags": json.dumps(rows), "env": json.dumps({k.strip(): v for k, v in body.env.items() if k.strip()}),
            "role": body.role, "alias": alias, "pinned": int(body.pinned)}


def _preset_out(r: dict) -> dict:
    r["flags"] = json.loads(r["flags"]) if isinstance(r["flags"], str) else r["flags"]
    r["env"] = json.loads(r["env"]) if isinstance(r["env"], str) else r["env"]
    r["pinned"] = bool(r.get("pinned"))
    r["last_used"] = db.scalar("SELECT MAX(started_at) FROM runs WHERE preset_id=?", (r["id"],))
    r["runs"] = db.scalar("SELECT COUNT(*) FROM runs WHERE preset_id=?", (r["id"],))
    return r


@app.get("/api/presets")
async def presets_list():
    return [_preset_out(r) for r in db.rows("SELECT * FROM presets ORDER BY name COLLATE NOCASE")]


@app.post("/api/presets")
async def presets_create(body: PresetBody):
    data = _clean_preset(body)
    if db.scalar("SELECT 1 FROM presets WHERE name=?", (data["name"],)):
        raise RuntimeError(f"A preset named “{data['name']}” already exists.")
    now = time.time()
    pid = db.insert("presets", {**data, "created_at": now, "updated_at": now})
    events.publish("presets", {"changed": True})
    return _preset_out(db.row("SELECT * FROM presets WHERE id=?", (pid,)))


@app.put("/api/presets/{pid}")
async def presets_update(pid: int, body: PresetBody):
    if not db.scalar("SELECT 1 FROM presets WHERE id=?", (pid,)):
        raise LookupError("That preset no longer exists.")
    data = _clean_preset(body, pid)
    if db.scalar("SELECT 1 FROM presets WHERE name=? AND id!=?", (data["name"], pid)):
        raise RuntimeError(f"A preset named “{data['name']}” already exists.")
    db.update("presets", pid, {**data, "updated_at": time.time()})
    events.publish("presets", {"changed": True})
    return _preset_out(db.row("SELECT * FROM presets WHERE id=?", (pid,)))


@app.post("/api/presets/{pid}/duplicate")
async def presets_duplicate(pid: int):
    src = db.row("SELECT * FROM presets WHERE id=?", (pid,))
    if not src:
        raise LookupError("That preset no longer exists.")
    name, i = f"{src['name']} copy", 2
    while db.scalar("SELECT 1 FROM presets WHERE name=?", (name,)):
        name, i = f"{src['name']} copy {i}", i + 1
    now = time.time()
    new_id = db.insert("presets", {"name": name, "description": src["description"], "model": src["model"],
                                   "flags": src["flags"], "env": src["env"], "role": src["role"], "pinned": src["pinned"],
                                   "created_at": now, "updated_at": now})
    events.publish("presets", {"changed": True})
    return _preset_out(db.row("SELECT * FROM presets WHERE id=?", (new_id,)))


@app.delete("/api/presets/{pid}")
async def presets_delete(pid: int):
    db.execute("DELETE FROM presets WHERE id=?", (pid,))
    ids = db.get_settings().get("autostart_preset_ids") or []
    if pid in ids:
        db.set_settings({"autostart_preset_ids": [i for i in ids if i != pid]})
    events.publish("presets", {"changed": True})
    return {"ok": True}


class ParseBody(BaseModel):
    command: str


@app.post("/api/presets/parse")
async def presets_parse(body: ParseBody):
    return flags.parse_command(body.command)


# ---------------------------------------------------------------- models & downloads

@app.get("/api/models")
async def models_list():
    items = await asyncio.to_thread(models.scan)
    return {"models": items, "roots": [str(r) for r in models.scan_roots()], "download_dir": str(config.DOWNLOAD_DIR)}


@app.delete("/api/models")
async def models_delete(path: str):
    target = models.files_for(path)[0]
    if any(models.files_for(i.model_path)[0] == target for i in manager.live()):
        raise RuntimeError("That model is loaded. Stop it before deleting it.")
    removed = await asyncio.to_thread(models.delete, path)
    events.publish("models", {"changed": True})
    return {"removed": removed}


@app.get("/api/hf/resolve")
async def hf_resolve(id: str):
    return await hf.resolve(id)


class DownloadBody(BaseModel):
    repo: str
    revision: str = "main"
    variant: str
    files: list[dict]


@app.get("/api/downloads")
async def downloads_list():
    return hf.downloader.rows()


@app.post("/api/downloads")
async def downloads_create(body: DownloadBody):
    if not body.files:
        raise ValueError("Pick at least one file to download.")
    for f in body.files:
        if ".." in f["path"] or f["path"].startswith("/"):
            raise ValueError("Invalid file path.")
    did = hf.downloader.enqueue(body.repo, body.revision, body.variant, body.files)
    return {"id": did}


@app.post("/api/downloads/{did}/cancel")
async def downloads_cancel(did: int):
    hf.downloader.cancel(did)
    return {"ok": True}


@app.post("/api/downloads/{did}/retry")
async def downloads_retry(did: int):
    hf.downloader.retry(did)
    return {"ok": True}


@app.delete("/api/downloads/{did}")
async def downloads_forget(did: int):
    db.execute("DELETE FROM downloads WHERE id=? AND status IN ('done','failed','cancelled')", (did,))
    events.publish("download", {"id": did, "removed": True})
    return {"ok": True}


# ---------------------------------------------------------------- stats

def _rng(range: str) -> str:
    if range not in stats.RANGES and range != "all":
        raise ValueError("Unknown range.")
    return range


@app.get("/api/stats/summary")
async def stats_summary(range: str = "24h", model: str | None = None):
    return stats.summary(_rng(range), model or None)


@app.get("/api/stats/timeseries")
async def stats_timeseries(range: str = "24h", model: str | None = None):
    return stats.timeseries(_rng(range), model or None)


@app.get("/api/stats/scatter")
async def stats_scatter(range: str = "24h", model: str | None = None):
    return stats.scatter(_rng(range), model or None)


@app.get("/api/stats/models")
async def stats_models(range: str = "24h"):
    return stats.models_table(_rng(range))


@app.get("/api/stats/lifetime")
async def stats_lifetime():
    return stats.lifetime()


@app.get("/api/stats/slots")
async def stats_slots():
    return stats.model_slots()


class ResetBody(BaseModel):
    confirm: bool = False


@app.post("/api/stats/reset")
async def stats_reset(body: ResetBody):
    if not body.confirm:
        raise ValueError("Confirm the reset.")
    n = db.scalar("SELECT COUNT(*) FROM requests")
    db.execute("DELETE FROM requests")
    db.execute("DELETE FROM gpu_minutes")
    events.publish("request", {"reset": True})
    return {"deleted": n}


@app.get("/api/requests")
async def requests_list(limit: int = 50, offset: int = 0, model: str | None = None, status: str | None = None):
    where, params = ["1=1"], []
    if model:
        where.append("model=?")
        params.append(model)
    if status == "errors":
        where.append("(status >= 400 OR error IS NOT NULL)")
    elif status == "ok":
        where.append("status < 400 AND error IS NULL")
    w = " AND ".join(where)
    total = db.scalar(f"SELECT COUNT(*) FROM requests WHERE {w}", params)
    rows = db.rows(f"SELECT * FROM requests WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?", [*params, min(limit, 500), offset])
    return {"total": total, "rows": rows}


@app.get("/api/requests.csv")
async def requests_csv(range: str = "all", model: str | None = None):
    t0, t1, _ = stats.window(_rng(range))
    params: list = [t0, t1]
    sql = f"SELECT {', '.join(stats.CSV_COLUMNS)} FROM requests WHERE ts >= ? AND ts < ?"
    if model:
        sql += " AND model=?"
        params.append(model)
    rows = db.rows(sql + " ORDER BY id", params)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=stats.CSV_COLUMNS)
    w.writeheader()
    for r in rows:
        r["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(r["ts"]))
        w.writerow(r)
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="kiln-requests-{range}.csv"'})


@app.get("/api/gpu/live")
async def gpu_live():
    return {"available": gpu.sampler.available, "name": gpu.sampler.name, "samples": list(gpu.sampler.live)}


@app.get("/api/gpu/history")
async def gpu_history(range: str = "24h"):
    return stats.gpu_history(_rng(range))


# ---------------------------------------------------------------- tuner

class TuneBody(BaseModel):
    model: str
    ctx: int
    objective: str = "decode"
    depth: int = 8192
    speculative: bool = True
    base_preset_id: int | None = None
    headroom_mib: int = 512
    name: str = ""
    start_after: bool = False
    mode: str = "grid"
    kv_policy: str = "precise"
    vary_batch: bool = False


@app.post("/api/tune")
async def tune_start(body: TuneBody):
    return {"id": await tuner.start(body.model_dump())}


@app.post("/api/tune/cancel")
async def tune_cancel():
    await tuner.cancel()
    return {"ok": True}


@app.get("/api/tune")
async def tune_list(limit: int = 20):
    rows = db.rows("SELECT id FROM tunes ORDER BY id DESC LIMIT ?", (limit,))
    out = [get_tune(r["id"]) for r in rows]
    return {"live": tuner.status(), "tunes": out}


@app.get("/api/tune/{tid}")
async def tune_get(tid: int):
    t = get_tune(tid)
    if not t:
        raise LookupError("No such tuning run.")
    t["live"] = tuner.status() if tuner.tune_id == tid else None
    return t


# ---------------------------------------------------------------- settings

def _settings_out() -> dict:
    s = db.get_settings()
    tok = s.pop("hf_token", "") or ""
    s["hf_token_set"] = bool(tok)
    s["hf_token_hint"] = (tok[:3] + "…" + tok[-4:]) if len(tok) > 8 else ""
    s["hf_token_source"] = "env" if os.environ.get("HF_TOKEN") else ("settings" if tok else ("hf_home" if hf.token() else ""))
    s["paths"] = {"data": str(config.DATA_DIR), "models": str(config.MODELS_DIR), "downloads": str(config.DOWNLOAD_DIR),
                  "hf_home": str(config.HF_HOME), "llama_server": config.LLAMA_SERVER}
    s["ports"] = {"ui": config.UI_PORT, "api": config.PUBLIC_API_PORT}
    return s


@app.get("/api/settings")
async def settings_get():
    return _settings_out()


@app.put("/api/settings")
async def settings_put(values: dict):
    allowed = {}
    if "hf_token" in values:
        allowed["hf_token"] = str(values["hf_token"] or "").strip()
    if "autostart_preset_ids" in values:
        allowed["autostart_preset_ids"] = [int(v) for v in (values["autostart_preset_ids"] or [])]
    if "swap_on_request" in values:
        allowed["swap_on_request"] = bool(values["swap_on_request"])
    if "max_instances" in values:
        allowed["max_instances"] = min(16, max(1, int(values["max_instances"] or 1)))
    if "idle_timeout_min" in values:
        allowed["idle_timeout_min"] = max(0, int(values["idle_timeout_min"] or 0))
    if "retention_days" in values:
        allowed["retention_days"] = max(0, int(values["retention_days"] or 0))
    db.set_settings(allowed)
    if allowed.get("retention_days"):
        stats.purge(allowed["retention_days"])
    return _settings_out()


# ---------------------------------------------------------------- events

@app.get("/api/events")
async def event_stream(request: Request):
    q = events.subscribe()

    async def gen():
        try:
            yield f"event: status\ndata: {json.dumps(manager.status())}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            events.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- web UI

@app.get("/")
async def index():
    return FileResponse(config.WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=config.WEB_DIR), name="web")
