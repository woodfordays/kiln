"""Lifecycle of the llama-server child processes: one Instance per running model, kept by the Fleet."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import signal
import socket
import time
from collections import deque
from pathlib import Path

import httpx

from . import config, db, events, fit, flags, models, roles
from .route import slug

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
BUFFER_RE = re.compile(r"\b(\S+?)\s+(model|KV|compute|RS|output|recurrent) buffer size\s*=\s*([\d.]+) MiB", re.I)
LIVE = ("starting", "loading", "ready", "stopping")
SETTLE_S = 2.0  # a model that just became ready is left alone this long, so the request that loaded it gets to run


class NoFit(RuntimeError):
    """The model would not fit next to what is running. `plan` says why; `evict` lists what could be stopped."""

    def __init__(self, message: str, plan: dict, evict: list[dict]) -> None:
        super().__init__(message)
        self.plan, self.evict = plan, evict


def _port_free(port: int) -> bool:
    """True unless something is listening. SO_REUSEADDR, as llama-server sets it, ignores lingering TIME_WAIT sockets."""
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


class Instance:
    """One llama-server process serving one model."""

    def __init__(self, fleet: "Fleet", port: int, model: str, rows: list[dict], env: dict, preset: dict | None, role: str) -> None:
        self.fleet = fleet
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.model_path, self.model_name = model, models.display_name(model)
        self.rows, self.env, self.preset, self.role = rows, dict(env), preset, role
        self.state = "starting"  # starting | loading | ready | stopping | stopped | crashed
        self.proc: asyncio.subprocess.Process | None = None
        self.run_id: int | None = None
        self.argv: list[str] = []
        self.notes: list[str] = []
        self.started_at: float | None = None
        self.ready_at: float | None = None
        self.error: str | None = None
        self.props: dict = {}
        self.buffers: dict[str, float] = {}
        self.logs: deque = deque(maxlen=5000)
        self.last_used = 0.0
        self.inflight = 0
        self.draining = False
        self.adapter: str | None = None  # "pplx-v3" when Kiln itself implements /v1/systemone for this model
        self.est = {}  # fit.estimate()
        self.need_mib = 0
        self.gpu_mib: int | None = None
        self.vram_at_spawn: int | None = None
        self.sig = ""
        self.gen = 0
        self.ready_evt = asyncio.Event()
        self.exit_evt = asyncio.Event()
        self._pending: list[list] = []
        self._logfile = None
        self._tasks: list[asyncio.Task] = []
        self._client = httpx.AsyncClient(base_url=self.url, timeout=5)

    # ------------------------------------------------------------ naming

    @property
    def name(self) -> str:
        """What clients call this model: the preset's alias, else its name, else the model file."""
        p = self.preset
        return (p.get("alias") or p["name"]) if p else self.model_name

    def aliases(self) -> list[str]:
        out = [self.name]
        if self.preset:
            out += [self.preset["name"], slug(self.preset["name"])]
        out += _alias_values(self.rows)
        return list(dict.fromkeys(a for a in out if a))

    @property
    def draining_or_stopping(self) -> bool:
        return self.draining or self.state in ("stopping", "stopped", "crashed")

    def begin(self) -> None:
        self.inflight += 1
        self.last_used = time.time()

    def end(self) -> None:
        self.inflight = max(0, self.inflight - 1)
        self.last_used = time.time()

    def footprint_mib(self) -> int:
        return self.gpu_mib if self.gpu_mib is not None else self.need_mib

    @property
    def protected(self) -> bool:
        """Still loading, or ready only a moment ago: stopping it now would throw away the load somebody is waiting for."""
        return self.state in ("starting", "loading") or (self.state == "ready" and bool(self.ready_at) and time.time() - self.ready_at < SETTLE_S)

    # ------------------------------------------------------------ status

    def status(self) -> dict:
        now = time.time()
        return {
            "run_id": self.run_id,
            "state": self.state,
            "name": self.name,
            "role": self.role,
            "adapter": self.adapter,
            "pid": self.proc.pid if self.proc and self.proc.returncode is None else None,
            "port": self.port,
            "preset": {"id": self.preset["id"], "name": self.preset["name"]} if self.preset else None,
            "pinned": bool(self.preset and self.preset.get("pinned")),
            "model_path": self.model_path,
            "model": self.model_name,
            "argv": self.argv,
            "env": self.env,
            "notes": self.notes,
            "started_at": self.started_at,
            "ready_at": self.ready_at,
            "uptime_s": now - self.ready_at if self.ready_at and self.state == "ready" else None,
            "loading_s": now - self.started_at if self.started_at and self.state in ("starting", "loading") else None,
            "error": self.error,
            "n_ctx": self.props.get("n_ctx"),
            "slots": self.props.get("total_slots"),
            "build": self.props.get("build_info"),
            "modalities": self.props.get("modalities"),
            "buffers": self.buffers,
            "gpu_mib": self.gpu_mib,
            "need_mib": self.need_mib,
            "inflight": self.inflight,
            "last_used": self.last_used or None,
        }

    def _set_state(self, state: str) -> None:
        self.state = state
        if state == "ready":
            self.ready_evt.set()
        self.fleet.publish()

    # ------------------------------------------------------------ launch

    async def spawn(self) -> None:
        final_rows, self.notes = roles.apply(self.role, self.model_path, self.rows)
        if not any((r.get("flag") or "").strip() in ("-a", "--alias") and r.get("enabled") is not False for r in final_rows):
            final_rows.append({"flag": "--alias", "value": self.name})
        self.adapter = "pplx-v3" if self.role == "decision" and roles.decision_kind(self.model_path) == "pplx-v3" else None
        self.argv = [config.LLAMA_SERVER, "-m", self.model_path, *flags.to_argv(final_rows),
                     "--host", "127.0.0.1", "--port", str(self.port), "--metrics"]
        self.started_at = time.time()
        mem = fit.gpu_memory()
        self.vram_at_spawn = mem["used_mib"] if mem else None
        self.run_id = db.insert("runs", {
            "preset_id": self.preset["id"] if self.preset else None,
            "preset_name": self.preset["name"] if self.preset else None,
            "model": self.model_path, "argv": json.dumps(self.argv), "env": json.dumps(self.env),
            "started_at": self.started_at, "status": "loading", "role": self.role, "port": self.port, "sig": self.sig,
        })
        self.fleet.instances[self.run_id] = self
        self._logfile = open(config.LOG_DIR / f"run-{self.run_id}.log", "a", buffering=1)
        self._log(f"$ {' '.join(shlex.quote(a) for a in self.argv)}", source="kiln")
        for n in self.notes:
            self._log(n, source="kiln")
        full_env = {**os.environ, **{k: str(v) for k, v in self.env.items()}}
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *self.argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL, env=full_env, start_new_session=True, limit=1 << 20,
            )
        except OSError as e:
            self.error = f"Could not start llama-server: {e}"
            db.update("runs", self.run_id, {"status": "crashed", "error": self.error, "stopped_at": time.time()})
            await self._client.aclose()
            self._finish("crashed")
            raise
        self.gen = self.fleet.bump()
        self._set_state("loading")
        self._tasks = [
            asyncio.create_task(self._read_output()),
            asyncio.create_task(self._watch_health()),
            asyncio.create_task(self._watch_exit()),
            asyncio.create_task(self._flush_logs()),
        ]

    async def wait_ready(self, timeout: float) -> None:
        """Returns when the server answers /health; raises if it exits first or the wait runs out."""
        ready = asyncio.create_task(self.ready_evt.wait())
        gone = asyncio.create_task(self.exit_evt.wait())
        try:
            done, _ = await asyncio.wait({ready, gone}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            ready.cancel()
            gone.cancel()
        if self.state == "ready":
            return
        if not done:
            raise TimeoutError(f"{self.name} did not finish loading in {int(timeout)} s.")
        raise RuntimeError(f"{self.name} stopped while loading: {self.error or 'see its log'}")

    # ------------------------------------------------------------ stop

    async def stop(self, drain: bool = False) -> None:
        if drain and self.inflight:
            self.draining = True
            self._log(f"Waiting up to {int(config.DRAIN_TIMEOUT_S)} s for {self.inflight} running request(s)", source="kiln")
            end = time.monotonic() + config.DRAIN_TIMEOUT_S
            while self.inflight and time.monotonic() < end:
                await asyncio.sleep(0.25)
        if not self.proc or self.proc.returncode is not None:
            if self.state in LIVE:
                self._finish("stopped")
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
        await self.exit_evt.wait()  # _watch_exit records the run

    def _finish(self, state: str) -> None:
        self.exit_evt.set()
        self.state = state
        self.fleet.bump()
        self.fleet.settle(self)

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
        while self.state in ("starting", "loading"):
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
        alone = self.fleet._gen == self.gen
        self.fleet.bump()
        self._set_state("ready")
        if alone:
            self._tasks.append(asyncio.create_task(self._measure(self.fleet._gen)))

    async def _measure(self, gen: int) -> None:
        """What this model really took from the GPU, when nothing else was starting or stopping around it."""
        await asyncio.sleep(config.GPU_SAMPLE_S * 1.5)
        mem = fit.gpu_memory()
        if mem is None or self.vram_at_spawn is None or self.fleet._gen != gen:
            return
        took = mem["used_mib"] - self.vram_at_spawn
        if took < max(64, self.need_mib * 0.25):  # the baseline was stale, or the model sits in system memory
            return
        self.gpu_mib = took
        db.update("runs", self.run_id, {"gpu_mib": took})
        self.fleet.publish()

    async def _watch_exit(self) -> None:
        proc = self.proc
        assert proc
        code = await proc.wait()
        await asyncio.sleep(0.2)  # let the reader drain
        expected = self.state == "stopping"
        if expected or code == 0:
            status, self.error = "stopped", None
        else:
            status = "crashed"
            tail = [l[1] for l in list(self.logs)[-40:] if l[2] == "server"]
            errs = [l for l in tail if re.search(r"error|failed|out of memory|abort|exception", l, re.I)]
            self.error = (errs[-1] if errs else (tail[-1] if tail else f"exit code {code}"))[:500]
        self._log(f"llama-server exited with code {code}", source="kiln")
        db.update("runs", self.run_id, {"status": status, "exit_code": code, "stopped_at": time.time(), "error": self.error})
        if self._logfile:
            self._logfile.close()
            self._logfile = None
        await self._client.aclose()
        self._finish(status)


def _alias_values(rows: list[dict]) -> list[str]:
    out: list[str] = []
    for r in rows:
        if (r.get("flag") or "").strip() in ("-a", "--alias") and r.get("enabled") is not False and r.get("value"):
            out += [v.strip() for v in str(r["value"]).split(",") if v.strip()]
    return out


class Fleet:
    """All the llama-server processes Kiln runs, and the rules for starting, stopping and swapping them."""

    def __init__(self) -> None:
        self.instances: dict[int, Instance] = {}
        self.blocked: str | None = None  # set while the tuner owns the GPU
        self._start_lock = asyncio.Lock()
        self._swaps: dict[int, asyncio.Future] = {}
        self._tasks: set[asyncio.Task] = set()
        self._gen = 0

    # ------------------------------------------------------------ views

    def live(self) -> list[Instance]:
        return [i for i in self.instances.values() if i.state in LIVE]

    def ready(self) -> list[Instance]:
        return [i for i in self.instances.values() if i.state == "ready" and not i.draining]

    def get(self, run_id: int) -> Instance:
        inst = self.instances.get(run_id)
        if not inst:
            raise LookupError("That server is no longer running.")
        return inst

    def primary(self) -> Instance | None:
        """The model the legacy single-server fields describe: the chat model used last, else any."""
        order = {"ready": 0, "loading": 1, "starting": 1, "stopping": 2, "crashed": 3}
        pool = [i for i in self.instances.values() if i.state in order]
        if not pool:
            return None
        return min(pool, key=lambda i: (order[i.state], i.role != "chat", -i.last_used, -(i.run_id or 0)))

    def by_preset(self, preset_id: int) -> Instance | None:
        return next((i for i in self.live() if i.preset and i.preset["id"] == preset_id), None)

    @property
    def state(self) -> str:
        states = {i.state for i in self.instances.values()}
        for s in ("ready", "loading", "starting", "stopping", "crashed"):
            if s in states:
                return "loading" if s == "starting" else s
        return "stopped"

    def status(self) -> dict:
        insts = sorted(self.instances.values(), key=lambda i: i.run_id or 0)
        p = self.primary()
        legacy = p.status() if p else {}
        mem = fit.gpu_memory()
        return {
            "state": self.state,
            "run_id": legacy.get("run_id"),
            "pid": legacy.get("pid"),
            "preset": legacy.get("preset"),
            "model_path": legacy.get("model_path", ""),
            "model": legacy.get("model", ""),
            "argv": legacy.get("argv", []),
            "env": legacy.get("env", {}),
            "started_at": legacy.get("started_at"),
            "ready_at": legacy.get("ready_at"),
            "uptime_s": legacy.get("uptime_s"),
            "loading_s": legacy.get("loading_s"),
            "error": legacy.get("error"),
            "n_ctx": legacy.get("n_ctx"),
            "slots": legacy.get("slots"),
            "build": legacy.get("build"),
            "modalities": legacy.get("modalities"),
            "buffers": legacy.get("buffers", {}),
            "api_port": config.PUBLIC_API_PORT,
            "blocked": self.blocked,
            "instances": [i.status() for i in insts],
            "primary": p.run_id if p else None,
            "vram": mem,
            "swap": [{"preset_id": pid} for pid in self._swaps],
        }

    def publish(self) -> None:
        events.publish("status", self.status())

    def bump(self) -> int:
        """Counts spawns, exits and readiness changes, so a GPU measurement can tell it was alone."""
        self._gen += 1
        return self._gen

    def settle(self, inst: Instance) -> None:
        """A process ended: clean stops leave the list, crashes stay until dismissed."""
        if inst.state == "stopped":
            self.instances.pop(inst.run_id, None)
        self.publish()

    def logs(self, run_id: int | None = None, tail: int = 1000) -> dict:
        inst = self.instances.get(run_id) if run_id else self.primary()
        if inst is None:
            return {"run_id": run_id, "lines": []}
        return {"run_id": inst.run_id, "lines": list(inst.logs)[-tail:]}

    def dismiss(self, run_id: int) -> None:
        inst = self.instances.get(run_id)
        if inst and inst.state == "crashed":
            del self.instances[run_id]
            self.publish()

    # ------------------------------------------------------------ planning

    def outstanding_mib(self) -> int:
        """VRAM still to be taken by models that are loading, so a second launch doesn't count it as free."""
        loading = [i for i in self.instances.values() if i.state in ("starting", "loading")]
        mem = fit.gpu_memory()
        if not loading or mem is None:
            return 0
        base = min((i.vram_at_spawn for i in loading if i.vram_at_spawn is not None), default=mem["used_mib"])
        return max(0, sum(i.need_mib for i in loading) - max(0, mem["used_mib"] - base))

    def max_instances(self) -> int:
        return max(1, int(db.get_settings().get("max_instances") or 4))

    def _resolve(self, model: str, rows: list[dict], preset: dict | None, role: str | None) -> tuple[str, list[dict], str]:
        if not model:
            raise ValueError("Choose a model to load.")
        if not Path(model).exists():
            raise FileNotFoundError(f"Model file not found: {model}")
        role = roles.valid(role or (preset or {}).get("role") or "chat")
        return model, rows, role

    def plan(self, model: str, rows: list[dict], preset: dict | None = None, role: str | None = None,
             replace: list[int] | str | None = None) -> dict:
        """Whether a launch fits, and which running models would have to stop to make room.

        `replace` is a list of run ids to count as stopped, or "auto" to pick idle, unpinned models
        (least recently used first) until it fits.
        """
        model, rows, role = self._resolve(model, rows, preset, role)
        final_rows, _ = roles.apply(role, model, rows)
        live = self.live()
        stopping = [i for i in live if i.state == "stopping"]
        keep = [i for i in live if i.state != "stopping"]
        chosen: list[Instance] = []
        if isinstance(replace, list):
            chosen = [self.get(r) for r in replace]
        reserved = self.outstanding_mib()

        def verdict(evicted: list[Instance]) -> dict:
            reclaim = sum(i.footprint_mib() for i in evicted + stopping)
            slots_ok = len(keep) - len(evicted) < self.max_instances()
            out = fit.check(model, final_rows, role, reclaim_mib=reclaim, reserved_mib=reserved)
            if not slots_ok:
                n = len(keep) - len(evicted)
                out["fits"] = False
                out["reason"] = (f"{n} model{' is' if n == 1 else 's are'} already running, "
                                 f"which is the limit in Settings ({self.max_instances()}).")
            return out

        result = verdict(chosen)
        candidates = sorted((i for i in keep if i not in chosen and not i.protected and not (i.preset and i.preset.get("pinned"))),
                            key=lambda i: (i.inflight > 0, i.last_used or i.started_at or 0))
        if replace == "auto":
            while result["fits"] is False and candidates:
                chosen.append(candidates.pop(0))
                result = verdict(chosen)
        return {**result, "evict": [{"run_id": i.run_id, "name": i.name, "gpu_mib": i.footprint_mib(), "idle": i.inflight == 0}
                                    for i in chosen],
                "evictable": [{"run_id": i.run_id, "name": i.name, "gpu_mib": i.footprint_mib(), "idle": i.inflight == 0}
                              for i in candidates], "role": role}

    # ------------------------------------------------------------ control

    async def start(self, model: str, rows: list[dict], env: dict | None = None, preset: dict | None = None,
                    role: str | None = None, force: bool = False, replace: list[int] | str | None = None,
                    drain: bool = False, reuse: bool = False) -> Instance:
        if self.blocked:
            raise RuntimeError(self.blocked)
        model, rows, role = self._resolve(model, rows, preset, role)
        roles.apply(role, model, rows)  # fail early on a role the model cannot take
        async with self._start_lock:
            running = self.by_preset(preset["id"]) if preset else None
            if running:
                if reuse:
                    return running
                raise RuntimeError(f"{preset['name']} is already running.")
            plan = self.plan(model, rows, preset, role, replace)
            if plan["fits"] is False and not force:
                auto = self.plan(model, rows, preset, role, "auto")
                raise NoFit(plan["reason"] or "That model doesn't fit next to the ones that are running.", plan,
                            auto["evict"] if auto["fits"] is not False else [])
            for e in plan["evict"]:
                victim = self.instances.get(e["run_id"])
                if victim:
                    await victim.stop(drain=drain)
            if plan["evict"]:
                await self._await_vram(plan["need_mib"])
            self.crashed_forget(model, preset)
            port = self._free_port()
            inst = Instance(self, port, model, rows, env or {}, preset, role)
            inst.est, inst.need_mib, inst.sig = plan, plan["need_mib"], plan["signature"]
            inst.last_used = time.time()
            await inst.spawn()
            return inst

    def crashed_forget(self, model: str, preset: dict | None) -> None:
        for rid, i in list(self.instances.items()):
            if i.state == "crashed" and (i.model_path == model or (preset and i.preset and i.preset["id"] == preset["id"])):
                del self.instances[rid]

    def _free_port(self) -> int:
        taken = {i.port for i in self.instances.values() if i.state in LIVE}
        for p in config.INSTANCE_PORTS:
            if p not in taken and p != config.TUNER_PORT and _port_free(p):
                return p
        raise RuntimeError("No free port for another llama-server.")

    async def _await_vram(self, need_mib: int, timeout: float = 15.0) -> None:
        """After stopping models, give the driver and the 2 s telemetry a moment to show the memory as free."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            mem = fit.gpu_memory()
            if mem is None or mem["free_mib"] >= need_mib + fit.MARGIN_MIB:
                return
            await asyncio.sleep(0.5)

    async def stop(self, run_id: int | None = None) -> None:
        targets = [self.get(run_id)] if run_id else list(self.instances.values())
        await asyncio.gather(*(i.stop() for i in targets if i.state in LIVE))
        for i in targets:
            if i.state == "crashed":
                self.dismiss(i.run_id)

    async def restart(self, run_id: int | None = None) -> Instance:
        if self.blocked:
            raise RuntimeError(self.blocked)
        inst = self.get(run_id) if run_id else self.primary()
        if inst is None:
            raise RuntimeError("Nothing to restart yet.")
        model, rows, env, preset, role = inst.model_path, inst.rows, inst.env, inst.preset, inst.role
        await inst.stop()
        self.instances.pop(inst.run_id, None)
        if preset:  # pick up edits made to the preset since it started
            fresh = get_preset(preset["id"])
            if fresh:
                preset, model, env, rows, role = fresh, fresh["model"], fresh["env"], fresh["flags"], fresh["role"]
        return await self.start(model, rows, env, preset=preset, role=role, force=True)

    def snapshot(self) -> list[dict]:
        """What is running, in start order, so it can be brought back after the tuner has had the GPU."""
        return [{"model": i.model_path, "rows": i.rows, "env": i.env, "preset_id": i.preset["id"] if i.preset else None,
                 "role": i.role} for i in sorted(self.live(), key=lambda i: i.run_id or 0) if i.state != "stopping"]

    async def restore(self, snapshot: list[dict]) -> list[str]:
        errors = []
        for s in snapshot:
            try:
                p = get_preset(s["preset_id"]) if s.get("preset_id") else None
                if p:
                    await self.start(p["model"], p["flags"], p["env"], preset=p, role=p["role"], force=True)
                else:
                    await self.start(s["model"], s["rows"], s["env"], role=s["role"], force=True)
            except Exception as e:
                errors.append(str(e))
        return errors

    async def shutdown(self) -> None:
        for fut in self._swaps.values():
            fut.cancel()
        await asyncio.gather(*(i.stop() for i in self.live()), return_exceptions=True)

    # ------------------------------------------------------------ swap on request

    async def ensure(self, preset: dict) -> Instance:
        """The ready instance for a preset, loading it (and stopping others to make room) if needed.
        Concurrent callers for the same preset share one load."""
        inst = self.by_preset(preset["id"])
        if inst is not None and inst.draining_or_stopping:
            # Asked for while it is being stopped to make room for another model: wait, then load it again.
            await inst.exit_evt.wait()
            inst = self.by_preset(preset["id"])
        if inst is None:
            fut = self._swaps.get(preset["id"])
            if fut is None:
                fut = asyncio.get_running_loop().create_future()
                self._swaps[preset["id"]] = fut
                task = asyncio.create_task(self._swap_in(preset, fut))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
                self.publish()
            return await asyncio.shield(fut)
        await inst.wait_ready(config.SWAP_TIMEOUT_S)
        return inst

    async def _swap_in(self, preset: dict, fut: asyncio.Future) -> None:
        try:
            fresh = get_preset(preset["id"]) or preset
            deadline = time.monotonic() + config.SWAP_TIMEOUT_S
            while True:
                try:
                    inst = await self.start(fresh["model"], fresh["flags"], fresh["env"], preset=fresh, role=fresh["role"],
                                            replace="auto", drain=True, reuse=True)
                    break
                except NoFit:
                    # The only thing in the way may be a load that another request is waiting for: let it finish first.
                    if time.monotonic() > deadline or not any(i.protected for i in self.live()):
                        raise
                    await asyncio.sleep(0.25)
            await inst.wait_ready(max(1.0, deadline - time.monotonic()))
            fut.set_result(inst)
        except BaseException as e:
            if not fut.done():
                fut.set_exception(e if isinstance(e, Exception) else RuntimeError("cancelled"))
            if isinstance(e, asyncio.CancelledError):
                raise
        finally:
            self._swaps.pop(preset["id"], None)
            if fut.done() and not fut.cancelled():
                fut.exception()  # mark retrieved; callers that were waiting get it from shield()
            self.publish()


def get_preset(pid: int) -> dict | None:
    r = db.row("SELECT * FROM presets WHERE id=?", (pid,))
    if not r:
        return None
    r["flags"] = json.loads(r["flags"])
    r["env"] = json.loads(r["env"])
    return r


manager = Fleet()
