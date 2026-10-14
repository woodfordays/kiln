"""The control port: REST API, server-sent events and the web UI."""
import asyncio
import base64
import csv
import io
import json
import os
import secrets
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, events, flags, gpu, hf, models, stats
from .server import get_preset, manager
from .tuner import get_tune, tuner

app = FastAPI(title="Kiln", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")


# ---------------------------------------------------------------- plumbing

@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if config.UI_PASSWORD and request.url.path != "/healthz":
        ok = False
        h = request.headers.get("authorization", "")
        if h.lower().startswith("basic "):
            try:
                user, _, pw = base64.b64decode(h[6:]).decode().partition(":")
                ok = secrets.compare_digest(user, config.UI_USER) and secrets.compare_digest(pw, config.UI_PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Kiln"'})
    return await call_next(request)


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
        "gpu": {"available": gpu.sampler.available, "name": gpu.sampler.name,
                "last": gpu.sampler.live[-1] if gpu.sampler.live else None},
        "auth": bool(config.UI_PASSWORD),
        "server_time": time.time(),
    }


class StartBody(BaseModel):
    preset_id: int | None = None
    model: str | None = None
    flags: list[dict] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


@app.post("/api/server/start")
async def server_start(body: StartBody):
    if body.preset_id is not None:
        p = get_preset(body.preset_id)
        if not p:
            raise LookupError("That preset no longer exists.")
        model = body.model or p["model"]
        return await manager.start(model, p["flags"], p["env"], preset=p)
    return await manager.start(body.model or "", body.flags, body.env)


@app.post("/api/server/stop")
async def server_stop():
    return await manager.stop()


@app.post("/api/server/restart")
async def server_restart():
    return await manager.restart()


@app.get("/api/server/logs")
async def server_logs(tail: int = 1000):
    return {"run_id": manager.run_id, "lines": list(manager.logs)[-tail:]}


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


def _clean_preset(body: PresetBody) -> dict:
    name = body.name.strip()
    if not name:
        raise ValueError("Give the preset a name.")
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
            "flags": json.dumps(rows), "env": json.dumps({k.strip(): v for k, v in body.env.items() if k.strip()})}


def _preset_out(r: dict) -> dict:
    r["flags"] = json.loads(r["flags"]) if isinstance(r["flags"], str) else r["flags"]
    r["env"] = json.loads(r["env"]) if isinstance(r["env"], str) else r["env"]
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
    data = _clean_preset(body)
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
                                   "flags": src["flags"], "env": src["env"], "created_at": now, "updated_at": now})
    events.publish("presets", {"changed": True})
    return _preset_out(db.row("SELECT * FROM presets WHERE id=?", (new_id,)))


@app.delete("/api/presets/{pid}")
async def presets_delete(pid: int):
    db.execute("DELETE FROM presets WHERE id=?", (pid,))
    s = db.get_settings()
    if s.get("autostart_preset_id") == pid:
        db.set_settings({"autostart_preset_id": None})
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
    if manager.state in ("loading", "ready") and models.files_for(manager.model_path)[0] == models.files_for(path)[0]:
        raise RuntimeError("That model is loaded. Stop the server before deleting it.")
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
    return hf.downloader.list()


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
    if "autostart_preset_id" in values:
        v = values["autostart_preset_id"]
        allowed["autostart_preset_id"] = int(v) if v not in (None, "", 0) else None
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
