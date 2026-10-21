"""Lifecycle of the llama-server child process."""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import time
from collections import deque
from pathlib import Path

import httpx

from . import config, db, events, flags, models

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
BUFFER_RE = re.compile(r"\b(\S+?)\s+(model|KV|compute|RS|output|recurrent) buffer size\s*=\s*([\d.]+) MiB", re.I)


class ServerManager:
    def __init__(self) -> None:
        self.state = "stopped"  # stopped | loading | ready | stopping | crashed
        self.proc: asyncio.subprocess.Process | None = None
        self.run_id: int | None = None
        self.preset: dict | None = None
        self.model_path = ""
        self.model_name = ""
        self.argv: list[str] = []
        self.env: dict = {}
        self.started_at: float | None = None
        self.ready_at: float | None = None
        self.error: str | None = None
        self.blocked: str | None = None  # set while the tuner owns the GPU
        self.props: dict = {}
        self.buffers: dict[str, float] = {}
        self.logs: deque = deque(maxlen=5000)
        self._pending: list[list] = []
        self._logfile = None
        self._lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []
        self._client = httpx.AsyncClient(base_url=config.INTERNAL_URL, timeout=5)

    # ------------------------------------------------------------ status

    def status(self) -> dict:
        now = time.time()
        return {
            "state": self.state,
            "run_id": self.run_id,
            "pid": self.proc.pid if self.proc and self.proc.returncode is None else None,
            "preset": {"id": self.preset["id"], "name": self.preset["name"]} if self.preset else None,
            "model_path": self.model_path,
            "model": self.model_name,
            "argv": self.argv,
            "env": self.env,
            "started_at": self.started_at,
            "ready_at": self.ready_at,
            "uptime_s": now - self.ready_at if self.ready_at and self.state == "ready" else None,
            "loading_s": now - self.started_at if self.started_at and self.state == "loading" else None,
            "error": self.error,
            "n_ctx": self.props.get("n_ctx"),
            "slots": self.props.get("total_slots"),
            "build": self.props.get("build_info"),
            "modalities": self.props.get("modalities"),
            "buffers": self.buffers,
            "api_port": config.PUBLIC_API_PORT,
            "blocked": self.blocked,
        }

    def _set_state(self, state: str) -> None:
        self.state = state
        events.publish("status", self.status())

    # ------------------------------------------------------------ control

    async def start(self, model: str, flag_rows: list[dict], env: dict | None = None, preset: dict | None = None,
                    replace: bool = False) -> dict:
        """Start llama-server. With replace, stop whatever is running first (one call to switch)."""
        async with self._lock:
            if self.blocked:
                raise RuntimeError(self.blocked)
            if not model:
                raise ValueError("Choose a model to load.")
            if not Path(model).exists():
                raise FileNotFoundError(f"Model file not found: {model}")
            if self.state in ("loading", "ready", "stopping"):
                if not replace:
                    raise RuntimeError("The server is already running. Stop it first, or use restart.")
                await self._stop()
            await self._spawn(model, flag_rows, env or {}, preset)
            return self.status()

    async def _spawn(self, model, flag_rows, env, preset) -> None:
        argv = [config.LLAMA_SERVER, "-m", model, *flags.to_argv(flag_rows),
                "--host", "127.0.0.1", "--port", str(config.INTERNAL_PORT), "--metrics"]
        self.argv, self.env, self.preset = argv, dict(env), preset
        self.model_path, self.model_name = model, models.display_name(model)
        self.started_at, self.ready_at, self.error = time.time(), None, None
        self.props, self.buffers = {}, {}
        self.logs.clear()
        self.run_id = db.insert("runs", {
            "preset_id": preset["id"] if preset else None,
            "preset_name": preset["name"] if preset else None,
            "model": model, "argv": json.dumps(argv), "env": json.dumps(env),
            "started_at": self.started_at, "status": "loading",
        })
        self._logfile = open(config.LOG_DIR / f"run-{self.run_id}.log", "a", buffering=1)
        self._log(f"$ {' '.join(argv)}", source="kiln")
        full_env = {**os.environ, **{k: str(v) for k, v in env.items()}}
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL, env=full_env, start_new_session=True, limit=1 << 20,
            )
        except OSError as e:
            self.error = f"Could not start llama-server: {e}"
            db.update("runs", self.run_id, {"status": "crashed", "error": self.error, "stopped_at": time.time()})
            self._set_state("crashed")
            raise
        self._set_state("loading")
        self._tasks = [
            asyncio.create_task(self._read_output()),
            asyncio.create_task(self._watch_health()),
            asyncio.create_task(self._watch_exit()),
            asyncio.create_task(self._flush_logs()),
        ]

    async def stop(self) -> dict:
        async with self._lock:
            await self._stop()
            return self.status()

    async def _stop(self) -> None:
        if not self.proc or self.proc.returncode is not None:
            if self.state not in ("stopped", "crashed"):
                self._set_state("stopped")
            return
        self._set_state("stopping")
        self._log("Stopping llama-server", source="kiln")
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(self.proc.wait(), config.STOP_TIMEOUT_S)
        except asyncio.TimeoutError:
            self._log("llama-server did not exit in time; killing it", source="kiln")
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await self.proc.wait()
        # let _watch_exit record the run
        for _ in range(50):
            if self.state != "stopping":
                break
            await asyncio.sleep(0.05)

    async def restart(self) -> dict:
        async with self._lock:
            if self.blocked:
                raise RuntimeError(self.blocked)
            if not self.model_path:
                raise RuntimeError("Nothing to restart yet.")
            model, env, preset = self.model_path, self.env, self.preset
            flag_rows = _argv_to_rows(self.argv)
            await self._stop()
            if preset:  # pick up edits made to the preset since it started
                fresh = get_preset(preset["id"])
                if fresh:
                    preset, model, env, flag_rows = fresh, fresh["model"], fresh["env"], fresh["flags"]
            await self._spawn(model, flag_rows, env, preset)
            return self.status()

    async def shutdown(self) -> None:
        await self._stop()
        await self._client.aclose()

    # ------------------------------------------------------------ watchers

    def _log(self, line: str, source: str = "server") -> None:
        entry = [time.time(), line, source]
        self.logs.append(entry)
        self._pending.append(entry)
        if self._logfile:
            try:
                self._logfile.write(line + "\n")
            except ValueError:
                pass

    async def _flush_logs(self) -> None:
        while True:
            if self._pending:
                batch, self._pending = self._pending, []
                events.publish("log", {"run_id": self.run_id, "lines": batch})
            if self.proc is None or (self.proc.returncode is not None and not self._pending):
                return
            await asyncio.sleep(0.3)

    async def _read_output(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            try:
                raw = await self.proc.stdout.readline()
            except ValueError:  # a line longer than the buffer; asyncio drops it, keep draining
                self._log("[kiln: dropped an over-long output line]", source="kiln")
                continue
            if not raw:
                break
            line = ANSI.sub("", raw.decode("utf-8", "replace").rstrip())
            if not line:
                continue
            self._log(line)
            m = BUFFER_RE.search(line)
            if m:
                kind = m.group(2).upper() if m.group(2).lower() == "kv" else m.group(2).lower()
                key = f"{m.group(1)} {kind}"
                self.buffers[key] = self.buffers.get(key, 0) + float(m.group(3))

    async def _watch_health(self) -> None:
        run_id = self.run_id
        while self.state == "loading" and self.run_id == run_id:
            try:
                r = await self._client.get("/health")
                if r.status_code == 200:
                    await self._on_ready()
                    return
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.5)

    async def _on_ready(self) -> None:
        self.ready_at = time.time()
        try:
            p = (await self._client.get("/props")).json()
            gen = p.get("default_generation_settings", {})
            self.props = {
                "n_ctx": gen.get("n_ctx") or p.get("n_ctx"),
                "total_slots": p.get("total_slots"),
                "build_info": p.get("build_info"),
                "modalities": p.get("modalities"),
            }
        except Exception:
            self.props = {}
        load_ms = (self.ready_at - (self.started_at or self.ready_at)) * 1000
        db.update("runs", self.run_id, {
            "status": "ready", "ready_at": self.ready_at, "load_ms": load_ms, "n_ctx": self.props.get("n_ctx"),
        })
        self._log(f"Ready in {load_ms / 1000:.1f} s", source="kiln")
        self._set_state("ready")

    async def _watch_exit(self) -> None:
        proc, run_id = self.proc, self.run_id
        assert proc
        code = await proc.wait()
        await asyncio.sleep(0.2)  # let the reader drain
        expected = self.state == "stopping"
        if run_id != self.run_id:
            return
        if expected or code == 0:
            status, self.error = "stopped", None
        else:
            status = "crashed"
            tail = [l[1] for l in list(self.logs)[-40:] if l[2] == "server"]
            errs = [l for l in tail if re.search(r"error|failed|out of memory|abort|exception", l, re.I)]
            self.error = (errs[-1] if errs else (tail[-1] if tail else f"exit code {code}"))[:500]
        self._log(f"llama-server exited with code {code}", source="kiln")
        db.update("runs", run_id, {"status": status, "exit_code": code, "stopped_at": time.time(), "error": self.error})
        if self._logfile:
            self._logfile.close()
            self._logfile = None
        self.ready_at = None if status != "stopped" else self.ready_at
        self._set_state(status)


def _argv_to_rows(argv: list[str]) -> list[dict]:
    """Recover flag rows from a running argv (minus the parts Kiln adds)."""
    body = argv[3:]  # binary, -m, model
    for managed in ("--metrics",):
        if managed in body:
            body.remove(managed)
    for opt in ("--host", "--port"):
        if opt in body:
            i = body.index(opt)
            del body[i:i + 2]
    return flags.parse_command("llama-server " + " ".join(_shquote(a) for a in body))["flags"]


def _shquote(s: str) -> str:
    import shlex
    return shlex.quote(s)


def get_preset(pid: int) -> dict | None:
    r = db.row("SELECT * FROM presets WHERE id=?", (pid,))
    if not r:
        return None
    r["flags"] = json.loads(r["flags"])
    r["env"] = json.loads(r["env"])
    return r


manager = ServerManager()
