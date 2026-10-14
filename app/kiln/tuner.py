"""Flag tuner: measures llama-server configurations in a fixed order with a fixed workload,
picks the winner by fixed rules, and saves it as a preset.

Deterministic by construction:
- The candidate list and its order depend only on the inputs (model, target context, objective,
  depth, options) and on what fits in memory.
- The workload is identical for every candidate: a full-batch warm-up, three fixed short prompts
  (128 tokens each) and the same deep prompt twice (a seeded filler document cut to an exact token
  count), all with greedy decoding (temperature 0, seed 42), ignore_eos and the prompt cache off.
- `--fit off` is passed, so llama.cpp never silently shrinks a configuration to make it load;
  a candidate either runs exactly as specified or is recorded as not fitting.
- Selection rules are fixed: KV precision is never traded for speed (the most precise KV type
  that fits with the VRAM headroom wins), and any other change replaces the incumbent only when
  its score beats it by more than NOISE (2%), with earlier candidates winning ties.
Measured speeds still vary by a percent or two between runs; the margin keeps the outcome stable.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import signal
import statistics
import time
from collections import deque

import httpx

from . import config, db, events, flags, gpu, models
from .server import manager

PORT = config.INTERNAL_PORT + 1
URL = f"http://127.0.0.1:{PORT}"
NOISE = 0.02
SEED = 42

KV_LADDER = [("f16", "f16"), ("q8_0", "q8_0"), ("q8_0", "q4_0"), ("q4_0", "q4_0")]
UBATCHES = ["256", "512", "1024", "2048"]
MTP_NMAX = ["1", "2", "3", "4", "5"]
TUNED_FLAGS = {"-ngl", "--gpu-layers", "--n-gpu-layers", "-c", "--ctx-size", "-np", "--parallel",
               "-fa", "--flash-attn", "-ctk", "--cache-type-k", "-ctv", "--cache-type-v",
               "-ub", "--ubatch-size", "-b", "--batch-size", "--spec-type", "--spec-draft-n-max",
               "-fit", "--fit"}

SHORT_PROMPTS = [
    "Write a Python function that parses an ISO 8601 date string without using any libraries, with comments.",
    "Write a short essay about why pottery kilns changed how villages traded goods.",
    "Write a bash script that backs up a directory to a dated tarball and keeps the last seven copies.",
]
DEEP_TASK = "\n\nSummarize the document above in a detailed paragraph."
N_SHORT = 128
N_DEEP = 64
DEEP_RUNS = 2
FILLER_WORDS = ("the kiln reached cone ten after a long reduction while the potter logged temperature "
                "every hour and noted how the glaze moved across the shoulder of each jar near the flue "
                "where ash settled thickly and the clay body tightened into stoneware").split()

OOM_RE = re.compile(r"out of (device )?memory|OutOfDeviceMemory|failed to allocate|unable to allocate|"
                    r"not enough memory|bad_alloc|alloc.*failed", re.I)


def _filler_text() -> str:
    rng = random.Random(1234)
    sentences = []
    for _ in range(9000):
        n = rng.randint(8, 18)
        sentences.append(" ".join(rng.choice(FILLER_WORDS) for _ in range(n)).capitalize() + ".")
    return " ".join(sentences)


class TuneCancelled(Exception):
    pass


class Tuner:
    def __init__(self) -> None:
        self.tune_id: int | None = None
        self.task: asyncio.Task | None = None
        self.cancelled = False
        self.stage = ""
        self.trial_label = ""
        self.proc: asyncio.subprocess.Process | None = None
        self.log: deque = deque(maxlen=400)
        self._filler_ids: list[int] | None = None
        self._task_ids: list[int] | None = None

    # ------------------------------------------------------------ public

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def status(self) -> dict:
        return {"running": self.running, "tune_id": self.tune_id, "stage": self.stage, "trial": self.trial_label}

    async def start(self, params: dict) -> int:
        if self.running:
            raise RuntimeError("A tuning run is already in progress.")
        model = params.get("model") or ""
        if not os.path.exists(model):
            raise FileNotFoundError(f"Model file not found: {model}")
        ctx = int(params.get("ctx") or 0)
        if ctx < 512:
            raise ValueError("Choose a target context of at least 512 tokens.")
        depth = int(params.get("depth") or 8192)
        params["depth"] = max(256, min(depth, ctx - N_DEEP - 64))
        params["objective"] = params.get("objective") or "decode"
        if params["objective"] not in ("decode", "balanced", "prefill"):
            raise ValueError("Unknown objective.")
        params["headroom_mib"] = int(params.get("headroom_mib", 512))
        params["mode"] = params.get("mode") or "grid"
        if params["mode"] not in ("grid", "quick"):
            raise ValueError("Unknown search mode.")
        params["kv_policy"] = params.get("kv_policy") or "precise"
        meta = next((m for m in models.scan() if m["path"] == model), {})
        params["has_mtp"] = bool(meta.get("nextn_layers"))
        base = {"flags": [], "env": {}}
        if params.get("base_preset_id"):
            row = db.row("SELECT * FROM presets WHERE id=?", (int(params["base_preset_id"]),))
            if row:
                base = {"flags": json.loads(row["flags"]), "env": json.loads(row["env"])}
        else:
            base["flags"] = [{"flag": "--jinja", "value": None}]
        base["flags"] = [f for f in base["flags"] if f["flag"] not in TUNED_FLAGS]
        params["base"] = base

        previous = None
        if manager.state in ("loading", "ready"):
            previous = {"model": manager.model_path, "env": manager.env,
                        "preset_id": manager.preset["id"] if manager.preset else None,
                        "argv": manager.argv}
        params["previous"] = previous

        self.tune_id = db.insert("tunes", {
            "created_at": time.time(), "status": "running", "model": model,
            "params": json.dumps(params), "trials": "[]",
        })
        self.cancelled = False
        self._filler_ids = None
        manager.blocked = "Kiln is tuning flags. The server is unavailable until it finishes or you cancel it."
        if previous:
            await manager.stop()
        self.task = asyncio.create_task(self._run(self.tune_id, params))
        self._publish()
        return self.tune_id

    async def cancel(self) -> None:
        if self.running:
            self.cancelled = True
            await self._kill()

    # ------------------------------------------------------------ the search

    async def _run(self, tid: int, p: dict) -> None:
        trials: list[dict] = []
        cache: dict[str, dict] = {}
        error = None
        best = None
        try:
            cfg = {"ngl": "99", "ctx": str(p["ctx"]), "np": "1", "fa": "on", "ctk": "f16", "ctv": "f16",
                   "ub": "512", "b": "2048", "spec": None, "nmax": None}

            async def trial(stage: str, label: str, c: dict, fresh: bool = False) -> dict:
                key = json.dumps(c, sort_keys=True)
                if key in cache and not fresh:
                    return cache[key]
                self.stage, self.trial_label = stage, label
                self._publish()
                t = {"n": len(trials) + 1, "stage": stage, "label": label, "cfg": dict(c)}
                t.update(await self._measure(c, p))
                t["score"] = _score(t, p["objective"]) if t["ok"] else None
                trials.append(t)
                if not fresh:
                    cache[key] = t
                db.update("tunes", tid, {"trials": json.dumps(trials)})
                self._publish()
                return t

            def skip(stage: str, label: str, c: dict, reason: str) -> None:
                trials.append({"n": len(trials) + 1, "stage": stage, "label": label, "cfg": dict(c),
                               "ok": False, "fits": False, "pruned": True, "error": reason, "score": None})

            def better(t: dict, inc: dict) -> bool:
                return t["ok"] and t["score"] is not None and t["score"] > inc["score"] * (1 + NOISE)

            # 1. KV cache: which types fit at the target context with headroom (f16 first).
            kv_fit: list[dict] = []
            for ngl in ("99", "auto"):
                for k, v in KV_LADDER:
                    c = {**cfg, "ngl": ngl, "ctk": k, "ctv": v, "fa": "on"}
                    t = await trial("KV cache", (f"-ngl {ngl} · " if ngl != "99" else "") + _combo_label(c), c)
                    if t["ok"]:
                        kv_fit.append(t)
                        if p["kv_policy"] == "precise":
                            break
                if kv_fit:
                    break
            if not kv_fit:
                raise RuntimeError(f"No configuration fits {p['ctx']:,} tokens of context with "
                                   f"{p['headroom_mib']} MiB to spare. Lower the target context.")
            incumbent = kv_fit[0]
            incumbent["chosen"] = True
            spec_opts = [None] + (MTP_NMAX if p["has_mtp"] and p.get("speculative", True) else [])

            if p["mode"] == "quick":
                # One flag at a time, each change kept only if it wins by more than NOISE.
                if incumbent["cfg"]["ctv"] == "f16":
                    for fa in ("off", "auto"):
                        t = await trial("Flash attention", f"-fa {fa}", {**incumbent["cfg"], "fa": fa})
                        if better(t, incumbent):
                            incumbent = t
                            t["chosen"] = True
                for ub in UBATCHES:
                    c = {**incumbent["cfg"], "ub": ub, "b": str(max(2048, int(ub)))}
                    t = await trial("Batch size", f"-ub {ub} -b {c['b']}", c)
                    if better(t, incumbent):
                        incumbent = t
                        t["chosen"] = True
                for n in spec_opts[1:]:
                    c = {**incumbent["cfg"], "spec": "draft-mtp", "nmax": n}
                    t = await trial("Speculative (MTP)", f"--spec-type draft-mtp --spec-draft-n-max {n}", c)
                    if better(t, incumbent):
                        incumbent = t
                        t["chosen"] = True
            else:
                # 2. Every combination, in a fixed nested order. A configuration that doesn't fit
                #    prunes the ones that need strictly more memory (same KV/FA, larger or equal
                #    batches and draft length), which are recorded without being run.
                too_big: list[dict] = []

                def needs_more(c: dict, f: dict) -> bool:
                    return (c["ngl"] == f["ngl"] and c["ctk"] == f["ctk"] and c["ctv"] == f["ctv"] and c["fa"] == f["fa"]
                            and int(c["ub"]) >= int(f["ub"]) and int(c["b"]) >= int(f["b"])
                            and int(c["nmax"] or 0) >= int(f["nmax"] or 0))

                for kvt in kv_fit:
                    base_c = kvt["cfg"]
                    fa_opts = ["on", "off", "auto"] if base_c["ctv"] == "f16" else ["on"]
                    for fa in fa_opts:
                        for ub in UBATCHES:
                            bs = sorted({max(2048, int(ub)), *( [int(ub), 4096] if p.get("vary_batch") else [] )})
                            for bb in [b for b in bs if b >= int(ub)]:
                                for n in spec_opts:
                                    c = {**base_c, "fa": fa, "ub": ub, "b": str(bb),
                                         "spec": "draft-mtp" if n else None, "nmax": n}
                                    label = _combo_label(c)
                                    if any(needs_more(c, f) for f in too_big):
                                        skip("Grid", label, c, "not run: a smaller configuration already didn't fit")
                                        continue
                                    t = await trial("Grid", label, c)
                                    if not t["ok"] and "fit" in (t.get("error") or ""):  # memory, not a crash
                                        too_big.append(c)

                # 3. Confirm: with many candidates the top score is partly luck, so the three best
                #    are measured again and ranked by the mean of both runs.
                ranked = sorted((t for t in trials if t["ok"]), key=lambda t: (-t["score"], t["n"]))
                finalists = ranked[:3]
                for f in finalists:
                    r = await trial("Confirm", f["label"], f["cfg"], fresh=True)
                    f["confirm_score"] = r["score"]
                    f["final_score"] = (f["score"] + r["score"]) / 2 if r["ok"] else f["score"] / 2
                # Start from the simplest finalist and switch only for a >NOISE gain, so
                # run-to-run jitter can't flip the outcome between near-equal configurations.
                finalists.sort(key=lambda t: (_complexity(t["cfg"]), t["n"]))
                incumbent = finalists[0]
                for f in finalists[1:]:
                    if f["final_score"] > incumbent["final_score"] * (1 + NOISE):
                        incumbent = f
                for f in finalists:
                    f["finalist"] = True
                incumbent["chosen"] = True

            best = incumbent
            for t in trials:
                t["best"] = t is best
            db.update("tunes", tid, {"trials": json.dumps(trials)})
            preset_id = self._save_preset(p, best, trials)
            db.update("tunes", tid, {"status": "done", "finished_at": time.time(),
                                     "best": json.dumps(best), "preset_id": preset_id})
        except TuneCancelled:
            db.update("tunes", tid, {"status": "cancelled", "finished_at": time.time()})
        except Exception as e:
            error = str(e) or e.__class__.__name__
            db.update("tunes", tid, {"status": "failed", "finished_at": time.time(), "error": error})
        finally:
            await self._kill()
            self.stage, self.trial_label = "", ""
            manager.blocked = None
            await self._after(p, best)
            self._publish()
            events.publish("presets", {"changed": True})

    async def _after(self, p: dict, best: dict | None) -> None:
        """Start the winner if asked, otherwise bring back what was running before."""
        try:
            row = db.row("SELECT preset_id FROM tunes WHERE id=?", (self.tune_id,))
            if best and p.get("start_after") and row and row["preset_id"]:
                from .server import get_preset
                pr = get_preset(row["preset_id"])
                await manager.start(pr["model"], pr["flags"], pr["env"], preset=pr)
            elif p.get("previous"):
                prev = p["previous"]
                from .server import get_preset, _argv_to_rows
                pr = get_preset(prev["preset_id"]) if prev.get("preset_id") else None
                if pr:
                    await manager.start(pr["model"], pr["flags"], pr["env"], preset=pr)
                else:
                    await manager.start(prev["model"], _argv_to_rows(prev["argv"]), prev["env"])
        except Exception as e:  # never let a restore failure mask the tuning result
            events.publish("tune_note", {"message": f"Could not restart the previous server: {e}"})

    # ------------------------------------------------------------ one candidate

    def _argv(self, c: dict, p: dict) -> list[str]:
        argv = [config.LLAMA_SERVER, "-m", p["model"], *flags.to_argv(p["base"]["flags"]),
                *_tuned_args(c), "--host", "127.0.0.1", "--port", str(PORT)]
        return argv

    async def _measure(self, c: dict, p: dict) -> dict:
        if self.cancelled:
            raise TuneCancelled()
        out = {"ok": False, "error": None}
        argv = self._argv(c, p)
        t_load = time.time()
        try:
            await self._launch(argv, p["base"]["env"])
        except TuneCancelled:
            raise
        except Exception as e:
            out["error"] = self._explain(e)
            out["fits"] = False
            await self._kill()
            return out
        out["load_s"] = time.time() - t_load
        try:
            async with httpx.AsyncClient(base_url=URL, timeout=900) as cl:
                props = (await cl.get("/props")).json()
                n_ctx = (props.get("default_generation_settings") or {}).get("n_ctx")
                if n_ctx and int(n_ctx) < int(c["ctx"]):
                    raise RuntimeError(f"loaded with {n_ctx:,} context instead of {int(c['ctx']):,}")
                if self._filler_ids is None:
                    self._filler_ids = (await cl.post("/tokenize", json={"content": _filler_text()})).json()["tokens"]
                    self._task_ids = (await cl.post("/tokenize", json={"content": DEEP_TASK})).json()["tokens"]
                ids = self._filler_ids
                while len(ids) < p["depth"]:
                    ids = ids + self._filler_ids
                # Warm-up, not measured: one full physical batch, so Vulkan compiles the
                # large-batch pipelines before anything is timed.
                await self._complete(cl, ids[:min(p["depth"], 2 * int(c["ub"]))], 8)
                t0 = time.time()
                short = [await self._complete(cl, pr, N_SHORT) for pr in SHORT_PROMPTS]
                deeps = [await self._complete(cl, ids[:p["depth"]] + self._task_ids, N_DEEP) for _ in range(DEEP_RUNS)]
                deep = {k: statistics.mean(d[k] for d in deeps) for k in ("predicted_per_second", "prompt_per_second")}
                t1 = time.time()
            vram_free = None
            if gpu.sampler.available:
                s = gpu.sampler.sample()
                if s and s["vram_total"]:
                    vram_free = (s["vram_total"] - s["vram_used"]) / 2**20
            out.update({
                "decode0": statistics.median(x["predicted_per_second"] for x in short),
                "decode_each": [x["predicted_per_second"] for x in short],
                "decodeD": deep["predicted_per_second"],
                "prefillD": deep["prompt_per_second"],
                "draft": _draft_rate(short + deeps),
                "power_w": gpu.sampler.mean_power(t0, t1),
                "vram_free_mib": vram_free,
            })
            if vram_free is not None and vram_free < p["headroom_mib"]:
                out["error"] = f"fits, but only {vram_free:.0f} MiB of VRAM left (needs {p['headroom_mib']})"
                out["fits"] = False
            else:
                out["ok"] = True
                out["fits"] = True
        except TuneCancelled:
            raise
        except Exception as e:
            out["error"] = self._explain(e)
            out["fits"] = False
        finally:
            await self._kill()
        return out

    async def _complete(self, cl: httpx.AsyncClient, prompt, n: int) -> dict:
        if self.cancelled:
            raise TuneCancelled()
        r = await cl.post("/completion", json={
            "prompt": prompt, "n_predict": n, "temperature": 0, "seed": SEED, "top_k": 1,
            "ignore_eos": True, "cache_prompt": False, "stream": False,
        })
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        t = r.json().get("timings") or {}
        if not t.get("predicted_n"):
            raise RuntimeError("no timings in response")
        return t

    async def _launch(self, argv: list[str], env: dict) -> None:
        self.log.clear()
        self.proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL, env={**os.environ, **env}, start_new_session=True, limit=1 << 20)
        reader = asyncio.create_task(self._read())
        deadline = time.time() + 900
        async with httpx.AsyncClient(base_url=URL, timeout=5) as cl:
            while True:
                if self.cancelled:
                    raise TuneCancelled()
                if self.proc.returncode is not None:
                    await asyncio.sleep(0.2)
                    raise RuntimeError(self._last_error() or f"exited with code {self.proc.returncode}")
                try:
                    if (await cl.get("/health")).status_code == 200:
                        return
                except httpx.TransportError:
                    pass
                if time.time() > deadline:
                    raise RuntimeError("did not finish loading within 15 minutes")
                await asyncio.sleep(0.5)

    async def _read(self) -> None:
        proc = self.proc
        while proc and proc.stdout:
            try:
                raw = await proc.stdout.readline()
            except ValueError:
                continue
            if not raw:
                break
            self.log.append(raw.decode("utf-8", "replace").rstrip())

    def _last_error(self) -> str:
        lines = list(self.log)
        hits = [l for l in lines if OOM_RE.search(l)]
        if hits:
            return "does not fit in VRAM"
        errs = [l for l in lines if re.search(r"error|failed|abort", l, re.I)]
        return (errs[-1] if errs else (lines[-1] if lines else ""))[-300:]

    def _explain(self, e: Exception) -> str:
        msg = str(e) or e.__class__.__name__
        if OOM_RE.search(msg) or OOM_RE.search("\n".join(list(self.log)[-30:])):
            return "does not fit in VRAM"
        return msg

    async def _kill(self) -> None:
        proc, self.proc = self.proc, None
        if not proc or proc.returncode is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            await asyncio.wait_for(proc.wait(), 20)
        except (ProcessLookupError, asyncio.TimeoutError):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()

    # ------------------------------------------------------------ results

    def _save_preset(self, p: dict, best: dict, trials: list[dict]) -> int:
        label = models.display_name(p["model"])
        name = (p.get("name") or "").strip() or f"{label} · {_ctx_label(int(p['ctx']))} · tuned"
        base_name, i = name, 2
        while db.scalar("SELECT 1 FROM presets WHERE name=?", (name,)):
            name, i = f"{base_name} ({i})", i + 1
        rows = list(p["base"]["flags"]) + _tuned_rows(best["cfg"])
        baseline = next((t for t in trials if t["ok"]), best)
        desc = (f"Tuned {time.strftime('%b %-d, %Y')} for {p['objective']}: decode {best['decode0']:.1f} t/s "
                f"({best['decodeD']:.1f} at {p['depth']:,} deep), prefill {best['prefillD']:.0f} t/s")
        if baseline is not best and baseline.get("score"):
            desc += f"; {(best['score'] / baseline['score'] - 1) * 100:+.0f}% over the first fit"
        now = time.time()
        return db.insert("presets", {"name": name, "description": desc + ".", "model": p["model"],
                                     "flags": json.dumps(rows), "env": json.dumps(p["base"]["env"]),
                                     "created_at": now, "updated_at": now})

    def _publish(self) -> None:
        if self.tune_id is None:
            return
        row = get_tune(self.tune_id)
        if row:
            row["live"] = self.status()
            events.publish("tune", row)


def _tuned_args(c: dict) -> list[str]:
    # Full offload is measured exactly as given (--fit off). Only when nothing fits fully does the
    # tuner let llama.cpp's --fit choose how many layers go to the GPU; context stays fixed by -c.
    gpu = ["--fit", "on"] if c["ngl"] == "auto" else ["-ngl", c["ngl"], "--fit", "off"]
    args = [*gpu, "-c", c["ctx"], "-np", c["np"], "-fa", c["fa"], "-ctk", c["ctk"], "-ctv", c["ctv"],
            "-ub", c["ub"], "-b", c["b"]]
    if c.get("spec"):
        args += ["--spec-type", c["spec"], "--spec-draft-n-max", c["nmax"]]
    return args


def _tuned_rows(c: dict) -> list[dict]:
    a = _tuned_args(c)
    return [{"flag": a[i], "value": a[i + 1]} for i in range(0, len(a), 2)]


def _combo_label(c: dict) -> str:
    parts = [f"-ctk {c['ctk']} -ctv {c['ctv']}", f"-fa {c['fa']}", f"-ub {c['ub']} -b {c['b']}"]
    parts.append(f"MTP {c['nmax']}" if c.get("spec") else "no spec")
    return " · ".join(parts)


def _complexity(c: dict) -> int:
    """How far a configuration is from llama.cpp's defaults; fewer changes win ties."""
    return ((c["fa"] != "on") + (c["ub"] != "512") + (c["b"] != "2048") + bool(c.get("spec"))
            + (c["ctk"] != "f16") + (c["ctv"] != "f16"))


def _draft_rate(ts: list[dict]) -> float | None:
    n = sum(t.get("draft_n") or 0 for t in ts)
    return (sum(t.get("draft_n_accepted") or 0 for t in ts) / n) if n else None


def _score(t: dict, objective: str) -> float:
    decode = (t["decode0"] * t["decodeD"]) ** 0.5
    if objective == "prefill":
        return t["prefillD"]
    if objective == "balanced":
        return decode ** 0.7 * t["prefillD"] ** 0.3
    return decode


def _ctx_label(n: int) -> str:
    return f"{n // 1024}K" if n % 1024 == 0 else f"{n:,}"


def get_tune(tid: int) -> dict | None:
    r = db.row("SELECT * FROM tunes WHERE id=?", (tid,))
    if not r:
        return None
    for k in ("params", "trials", "best"):
        r[k] = json.loads(r[k]) if r.get(k) else None
    return r


tuner = Tuner()
