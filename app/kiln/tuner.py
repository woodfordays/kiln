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
- The "context" objective treats the target context as the requirement and lets model weights
  spill out of VRAM into system RAM to reach it: MoE expert weights first (--n-cpu-moe, which keeps
  attention and the KV cache on the GPU), whole layers (-ngl) for dense models. For each KV type
  and micro-batch it finds the smallest spill that fits by bisection (load-only probes; VRAM use
  only grows with the spill shrinking), then benchmarks that configuration. Weights in system RAM
  are read by the CPU, so that objective then also sweeps the thread counts (-t, then -tb).
- Speculative decoding covers the MTP head and every draft file found next to the model (DSpark,
  DFlash, EAGLE-3, a plain draft model), each at a few draft lengths.
Measured speeds still vary by a percent or two between runs; the margin keeps the outcome stable.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
import random
import re
import signal
import statistics
import time
from collections import deque

import httpx

from . import config, db, events, flags, gpu, models, tunespace
from .tunespace import DIMS, GRID_ORDER, SPEC_ORDER
from .server import manager

PORT = config.TUNER_PORT
URL = f"http://127.0.0.1:{PORT}"
NOISE = 0.02
POLL_S = 0.5  # how often a starting llama-server is asked if it is ready
PROBE_MARGIN_MIB = 128  # a benchmark needs ~100 MiB more VRAM than loading did (measured on Vulkan and ROCm)
SEED = 42

KV_LADDER = [("f16", "f16"), ("q8_0", "q8_0"), ("q8_0", "q4_0"), ("q4_0", "q4_0")]
UBATCHES = ["256", "512", "1024", "2048"]
MTP_NMAX = ["1", "2", "3", "4", "5"]
SPEC_LABEL = {"draft-mtp": "MTP", "draft-dspark": "DSpark", "draft-dflash": "DFlash", "draft-eagle3": "EAGLE-3",
              "draft-simple": "Draft model"}
NO_SPEC = {"spec": None, "draft": None, "nmax": None}
TUNED_FLAGS = {"-ngl", "--gpu-layers", "--n-gpu-layers", "-c", "--ctx-size", "-np", "--parallel",
               "-fa", "--flash-attn", "-ctk", "--cache-type-k", "-ctv", "--cache-type-v",
               "-ub", "--ubatch-size", "-b", "--batch-size", "--spec-type", "--spec-draft-n-max",
               "-md", "--spec-draft-model", "--model-draft",
               "-fit", "--fit", "-ncmoe", "--n-cpu-moe", "-cmoe", "--cpu-moe"}
# Tuned only by the context objective, where weights in system RAM make the CPU part of the benchmark.
THREAD_FLAGS = {"-t", "--threads", "-tb", "--threads-batch"}
# Dimensions the candidate configs carry only when the search space sets them (see tunespace.py).
EXTRA_KEYS = [k for k in GRID_ORDER if k not in ("fa", "ub", "b", "np", "threads", "threads_batch")]
SPEC_KEYS = ("spec", "draft", "nmax")
UNQUANTIZED = ("f16", "f32", "bf16")
# _needs_more: these have no VRAM effect; these only grow memory with their value; everything else must be equal.
NO_MEMORY_EFFECT = {"threads", "threads_batch", "numa", "poll", "prio", "cache_reuse", "nmin", "pmin", "load_mode", "repack"}
GROWS = ("ub", "b", "ngld")
THREADS_RE = re.compile(r"threadpool init, n_threads = (\d+)")

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
        self.plan: list[dict] = []  # the stages of the run, for the progress display (see _plan)
        self.proc: asyncio.subprocess.Process | None = None
        self.log: deque = deque(maxlen=400)
        self.threads_used: int | None = None  # the thread count llama-server logged: its own choice unless -t was given
        self._filler_ids: list[int] | None = None
        self._task_ids: list[int] | None = None

    # ------------------------------------------------------------ public

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def status(self) -> dict:
        return {"running": self.running, "tune_id": self.tune_id, "stage": self.stage, "trial": self.trial_label}

    def live(self) -> dict:
        """The status plus the stage plan: what the Tuner page shows while a run is going."""
        return {**self.status(), "plan": self.plan}

    def _plan_max(self, step_id: str, n: int) -> None:
        """A stage's size became known (or exact); tell the page."""
        for step in self.plan:
            if step["id"] == step_id:
                step["max"] = n
        self._publish()

    async def start(self, params: dict) -> int:
        if self.running:
            raise RuntimeError("A tuning run is already in progress.")
        model = params.get("model") or ""
        if not os.path.exists(model):
            raise FileNotFoundError(f"Model file not found: {model}")
        ctx = int(params.get("ctx") or 0)
        if ctx < 512:
            raise ValueError("Choose a target context of at least 512 tokens.")
        params["space"] = space = tunespace.normalize(params.get("space"))
        depth = int(params.get("depth") or 8192)
        smallest = min([int(v) for v in _vals(space, "ctx")] or [ctx])  # every context in the space must hold the deep prompt
        params["depth"] = max(256, min(depth, smallest - N_DEEP - 64))
        params["objective"] = params.get("objective") or "decode"
        if params["objective"] not in ("decode", "balanced", "prefill", "context"):
            raise ValueError("Unknown objective.")
        params["headroom_mib"] = int(params.get("headroom_mib", 512))
        params["mode"] = params.get("mode") or "grid"
        if params["mode"] not in ("grid", "quick"):
            raise ValueError("Unknown search mode.")
        params["kv_policy"] = params.get("kv_policy") or "precise"
        meta = next((m for m in models.scan() if m["path"] == model), {})
        params["has_mtp"] = bool(meta.get("nextn_layers"))
        params["drafts"] = _chosen_drafts(meta, params.get("drafts"), params.get("speculative", True))
        params["n_layers"] = int(meta.get("block_count") or 0)
        params["is_moe"] = bool(meta.get("expert_count"))
        params["model_size"] = int(meta.get("size") or 0)
        if params["objective"] == "context" and not params["n_layers"]:
            raise ValueError("This model's layer count is unknown, so the tuner can't spill it to system RAM.")
        base = {"flags": [], "env": {}}
        if params.get("base_preset_id"):
            row = db.row("SELECT * FROM presets WHERE id=?", (int(params["base_preset_id"]),))
            if row:
                base = {"flags": json.loads(row["flags"]), "env": json.loads(row["env"])}
        else:
            base["flags"] = [{"flag": "--jinja", "value": None}]
        tuned = (TUNED_FLAGS | THREAD_FLAGS if params["objective"] == "context" else TUNED_FLAGS) | tunespace.stripped_flags(space)
        # What the preset already used is measured too, so a hand-picked thread count gets a fair test.
        params["base_threads"] = [n for n in (_flag_int(base["flags"], ("-t", "--threads")),
                                              _flag_int(base["flags"], ("-tb", "--threads-batch"))) if n]
        base["flags"] = [f for f in base["flags"] if f["flag"] not in tuned]
        params["base"] = base
        if not _batch_pairs(space, params.get("vary_batch")):
            raise ValueError("No micro-batch size fits under the logical batch size: the logical batch must be at least as large.")
        tunespace.check({"objective": params["objective"], "mode": params["mode"], "kv_policy": params["kv_policy"],
                         "vary_batch": bool(params.get("vary_batch")), "space": space, "kv_ladder": KV_LADDER,
                         "ubatches": UBATCHES, "spec_groups": _spec_groups(params)})

        previous = manager.snapshot() or None  # everything running now comes back afterwards
        params["previous"] = previous

        self.tune_id = db.insert("tunes", {
            "created_at": time.time(), "status": "running", "model": model,
            "params": json.dumps(params), "trials": "[]",
        })
        self.cancelled = False
        self._filler_ids = None
        self.plan = _plan(params)
        manager.blocked = "Kiln is tuning flags. The server is unavailable until it finishes or you cancel it."
        if previous:
            await manager.stop()  # the tuner needs the whole GPU
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
            space = p.get("space") or {}
            cfg = {"ngl": "99", "ctx": str(p["ctx"]), "np": "1", "fa": "on", "ctk": "f16", "ctv": "f16",
                   "ub": "512", "b": "2048", **NO_SPEC, "ncmoe": None, "threads": None, "threads_batch": None,
                   **_baseline(p)}

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

            better = _better

            async def probe(label: str, c: dict) -> bool:
                """Load only: does this configuration start with the VRAM headroom? Cached."""
                key = "probe:" + json.dumps(c, sort_keys=True)
                if key not in cache:
                    self.stage, self.trial_label = "Fit", label
                    self._publish()
                    t = {"n": len(trials) + 1, "stage": "Fit", "label": label, "cfg": dict(c), "score": None}
                    t.update(await self._probe(c, p))
                    trials.append(t)
                    cache[key] = t
                    db.update("tunes", tid, {"trials": json.dumps(trials)})
                    self._publish()
                return cache[key]["fits"]

            async def confirm() -> dict:
                # With many candidates the top score is partly luck, so the three best are
                # measured again and ranked by the mean of both runs.
                ranked = sorted((t for t in trials if t["ok"]), key=lambda t: (-t["score"], t["n"]))
                finalists = ranked[:3]
                for f in finalists:
                    r = await trial("Confirm", f["label"], f["cfg"], fresh=True)
                    f["confirm_score"] = r["score"]
                    f["final_score"] = (f["score"] + r["score"]) / 2 if r["ok"] else f["score"] / 2
                # Start from the simplest finalist and switch only for a >NOISE gain, so
                # run-to-run jitter can't flip the outcome between near-equal configurations.
                finalists.sort(key=lambda t: (_complexity(t["cfg"]), t["n"]))
                inc = finalists[0]
                for f in finalists[1:]:
                    if f["final_score"] > inc["final_score"] * (1 + NOISE):
                        inc = f
                for f in finalists:
                    f["finalist"] = True
                inc["chosen"] = True
                return inc

            if p["objective"] == "context":
                incumbent = await self._spill_search(p, cfg, trial, probe, confirm, skip)
            else:
                # 1. KV cache: which types fit at the target context with headroom (f16 first). The space
                #    can widen this to contexts x offload x KV pairs; the first context and offload with a fit wins.
                kv_fit: list[dict] = []
                ctxs = _contexts(p)
                fa0, (cfg["ub"], cfg["b"]) = _fit_fa(space), _fit_batch(space, p.get("vary_batch"))
                pairs = tunespace.kv_pairs(space, KV_LADDER)
                for ctx in ctxs:
                    for ngl in tunespace.values(space, "ngl", ["99", "auto"]):
                        for ncmoe in _vals(space, "ncmoe") or [None]:
                            for k, v in pairs:
                                c = {**cfg, "ctx": str(ctx), "ngl": ngl, "ncmoe": ncmoe, "ctk": k, "ctv": v, "fa": fa0}
                                pre = (f"-c {ctx} · " if ctx != ctxs[0] else "") + ("-ngl auto · " if ngl == "auto" else "")
                                t = await trial("KV cache", pre + _combo_label(c), c)
                                if t["ok"]:
                                    kv_fit.append(t)
                                    if p["kv_policy"] == "precise":
                                        break
                            if kv_fit:
                                break
                        if kv_fit:
                            break
                    if kv_fit:
                        break
                if not kv_fit:
                    raise RuntimeError(f"No configuration fits {ctxs[-1]:,} tokens of context with "
                                       f"{p['headroom_mib']} MiB to spare. Lower the target context.")
                incumbent = kv_fit[0]
                incumbent["chosen"] = True
                spec_opts = [NO_SPEC, *_spec_options(p)]

                if p["mode"] == "quick":
                    # One flag at a time, each change kept only if it wins by more than NOISE.
                    fa_sp = space.get("fa")
                    if fa_sp is None:
                        self._plan_max("fa", 2 if incumbent["cfg"]["ctv"] == "f16" else 0)
                        fas = ["off", "auto"] if incumbent["cfg"]["ctv"] == "f16" else []
                    else:
                        fas = [v for v in fa_sp["values"] if v != incumbent["cfg"]["fa"]] if fa_sp["mode"] == "list" else []
                        self._plan_max("fa", len(fas))
                    for fa in fas:
                        c = {**incumbent["cfg"], "fa": fa}
                        if why := _prune_reason(c, p, slots=False):
                            skip("Flash attention", f"-fa {fa}", c, why)
                            continue
                        t = await trial("Flash attention", f"-fa {fa}", c)
                        if better(t, incumbent):
                            incumbent = t
                            t["chosen"] = True
                    if "ub" not in space or _listmode(space, "ub"):
                        for ub in tunespace.values(space, "ub", UBATCHES):
                            if (b := _b_for(space, ub)) is None:
                                continue
                            c = {**incumbent["cfg"], "ub": ub, "b": b}
                            t = await trial("Batch size", f"-ub {ub} -b {b}", c)
                            if better(t, incumbent):
                                incumbent = t
                                t["chosen"] = True
                    incumbent = await self._sweep(p, incumbent, _speed_dims(p, ("fa", "ub")), trial, skip, mark=True)
                    for s in spec_opts[1:]:
                        c = _with_spec(incumbent["cfg"], s)
                        t = await trial(f"Speculative ({SPEC_LABEL.get(s['spec'], s['spec'])})", _spec_flags(s), c)
                        if better(t, incumbent):
                            incumbent = t
                            t["chosen"] = True
                    incumbent = await self._sweep(p, incumbent, _sub_dims(p, incumbent), trial, skip, mark=True)
                else:
                    # 2. Every combination, in a fixed nested order. A configuration that doesn't fit
                    #    prunes the ones that need strictly more memory (same KV/FA, larger or equal
                    #    batches and draft length), which are recorded without being run.
                    too_big: list[dict] = []
                    # Each fitting KV type already ran its first combination (-fa on, -ub 512, no speculation).
                    ran = lambda c: json.dumps(c, sort_keys=True) in cache
                    self._plan_max("grid", sum(1 for c in _grid(kv_fit, spec_opts, p.get("vary_batch"), space) if not ran(c)))
                    for c in _grid(kv_fit, spec_opts, p.get("vary_batch"), space):
                        label = _combo_label(c)
                        if not ran(c) and (why := _prune_reason(c, p)):
                            skip("Grid", label, c, why)
                            continue
                        if any(_needs_more(c, f) for f in too_big):
                            skip("Grid", label, c, "not run: a smaller configuration already didn't fit")
                            continue
                        t = await trial("Grid", label, c)
                        if not t["ok"] and "fit" in (t.get("error") or ""):  # memory, not a crash
                            too_big.append(c)

                    # 3. Confirm the top three.
                    incumbent = await confirm()

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
            self.plan = []
            manager.blocked = None
            await self._after(p, best)
            self._publish()
            events.publish("presets", {"changed": True})

    async def _after(self, p: dict, best: dict | None) -> None:
        """Start the winner if asked, and bring back what was running before."""
        try:
            from .server import get_preset
            previous = p.get("previous") or []
            row = db.row("SELECT preset_id FROM tunes WHERE id=?", (self.tune_id,))
            if best and p.get("start_after") and row and row["preset_id"]:
                pr = get_preset(row["preset_id"])
                await manager.start(pr["model"], pr["flags"], pr["env"], preset=pr, role=pr["role"], force=True)
                previous = [s for s in previous if s["model"] != p["model"]]  # the winner replaces its own model
            errors = await manager.restore(previous)
            if errors:
                events.publish("tune_note", {"message": "Could not restart: " + "; ".join(errors)})
        except Exception as e:  # never let a restore failure mask the tuning result
            events.publish("tune_note", {"message": f"Could not restart the previous server: {e}"})

    async def _spill_search(self, p: dict, cfg: dict, trial, probe, confirm, skip) -> dict:
        """Context objective: keep the target context, spill weights to system RAM until it fits.

        For each KV type (f16 first; every type with kv_policy "all") and each micro-batch, bisect
        the smallest spill that loads with the VRAM headroom, then benchmark it. Bigger micro-batches
        need at least the previous spill, so each search starts where the last one ended. When the
        space gives the spill axis fixed or listed values, those are probed instead, fewest layers in
        system RAM first, and the first that loads is benchmarked. A list of contexts is tried from the
        largest; the first with a measurable configuration is used.
        """
        space = p.get("space") or {}
        axis = _spill_axis(p)
        max_n = _spill_max(p, axis)
        ram_cap = _ram_cap(p, max_n) if max_n else None
        if ram_cap is not None and ram_cap < max_n:
            max_n = ram_cap
        pairs = tunespace.kv_pairs(space, KV_LADDER)
        kv_opts = pairs if p["kv_policy"] == "all" else pairs[:1]
        fa0 = _fit_fa(space)
        explicit = [v for v in _vals(space, axis) if space[axis]["mode"] in ("fixed", "list")] if axis else []
        explicit.sort(key=lambda v: _spill_value(p, axis, v))  # stable: the given order breaks ties
        batches = _batch_pairs(space, False)
        for other in ("ngl", "ncmoe"):  # the axis that is not spilled is held at its first value
            if other != axis and _vals(space, other):
                cfg = {**cfg, other: _vals(space, other)[0]}

        async def smallest(c: dict, lo: int) -> int | None:
            def at(n: int) -> dict:
                return _spilled(c, n, p, axis)
            if await probe(_combo_label(at(lo)), at(lo)):
                return lo
            # From an earlier answer the next one is usually a layer or two up: gallop 1, 2, 4…
            # before bisecting. From zero, bisect the whole range.
            bad, good, step = lo, None, 1
            while lo and bad + step < max_n:
                if await probe(_combo_label(at(bad + step)), at(bad + step)):
                    good = bad + step
                    break
                bad, step = bad + step, step * 2
            if good is None:
                if bad >= max_n or not await probe(_combo_label(at(max_n)), at(max_n)):
                    return None
                good = max_n
            # bad doesn't fit, good does; VRAM use falls as n grows
            while good - bad > 1:
                mid = (bad + good) // 2
                if await probe(_combo_label(at(mid)), at(mid)):
                    good = mid
                else:
                    bad = mid
            return good

        async def spill_at(cfg: dict) -> list[dict]:
            measured: list[dict] = []
            for k, v in kv_opts:
                lo = 0
                for ub, b in batches:
                    c = {**cfg, "ctk": k, "ctv": v, "fa": fa0, "ub": ub, "b": b}
                    if explicit:
                        for x in explicit:
                            c2 = _with_spill(c, x, axis)
                            if await probe(_combo_label(c2), c2):
                                t = await trial("Spill", _combo_label(c2), c2)
                                if t["ok"]:
                                    measured.append(t)
                                break
                        continue
                    n = await smallest(c, lo)
                    if n is None:
                        if "ub" not in space:
                            break  # larger micro-batches need even more VRAM
                        continue
                    lo = n
                    # The benchmark can need a little more than the load did; step the spill up twice at most.
                    for extra in (0, 1, max(2, max_n // 16)):
                        if n + extra > max_n:
                            break
                        c2 = _spilled(c, n + extra, p, axis)
                        t = await trial("Spill", _combo_label(c2), c2)
                        if t["ok"]:
                            measured.append(t)
                            break
                        if "VRAM" not in (t.get("error") or ""):
                            break
            return measured

        ctxs = _contexts(p)
        for ctx in ctxs:
            measured = await spill_at({**cfg, "ctx": str(ctx)})
            if measured:
                break
        if not measured:
            unit = "the expert weights of every layer" if axis == "ncmoe" else "every layer"
            full = _spill_max(p, axis)
            ram = f" ({ram_cap} of {full} layers fit in free system RAM)" if ram_cap is not None and ram_cap < full else ""
            where = f"even with {unit} in system RAM{ram}" if axis else "without spilling anything to system RAM"
            raise RuntimeError(f"{ctxs[-1]:,} tokens of context doesn't fit with {p['headroom_mib']} MiB of VRAM "
                               f"to spare {where}. Lower the target context.")

        lead = await self._thread_search(p, max(measured, key=lambda t: (t["score"], -t["n"])), trial)
        lead = await self._sweep(p, lead, _speed_dims(p, ("ub", "b", "threads", "threads_batch")), trial, skip)

        # Speculative decoding on the fastest configuration so far. The MTP head and a draft file need
        # a little VRAM, so one that doesn't load steps the spill up, as a benchmark does; a length that
        # worked keeps its step for the longer ones.
        steps = (0,) if explicit else (0, 1, max(2, max_n // 16))
        start_at: dict[tuple, int] = {}
        spec_trials: list[dict] = []
        for s in _spec_options(p):
            c = _with_spec(lead["cfg"], s)
            key = (s["spec"], s["draft"])
            for i in range(start_at.get(key, 0), len(steps)):
                n = _spill_of(c, p, axis) + steps[i]
                if n > max_n:
                    break
                c2 = _spilled(c, n, p, axis) if steps[i] else c
                t = await trial(f"Speculative ({SPEC_LABEL.get(s['spec'], s['spec'])})", _combo_label(c2), c2)
                if t["ok"]:
                    start_at[key] = i
                    spec_trials.append(t)
                if t["ok"] or "VRAM" not in (t.get("error") or ""):
                    break
        # The draft settings are tried on the best speculative option, if it beat the lead.
        best_spec = lead
        for t in spec_trials:
            if _better(t, best_spec):
                best_spec = t
        await self._sweep(p, best_spec, _sub_dims(p, best_spec), trial, skip)
        return await confirm()

    async def _thread_search(self, p: dict, lead: dict, trial) -> dict:
        """CPU threads on the fastest spill; returns the fastest configuration found.

        Weights in system RAM are read by the CPU, so the thread count shows up in the benchmark.
        llama.cpp's default (-t -1) is the baseline. Fewer threads can be as fast when memory bandwidth
        is the limit, and the logical CPUs can help prompt processing. -t is swept first (-tb follows
        it unless set), then -tb on the best -t. A change is kept only if it wins by more than NOISE.
        The space can fix a count or switch the sweep off for either flag, or replace the candidates
        with its own list (exactly those, nothing added).
        """
        space = p.get("space") or {}
        gen_mode = space.get("threads", {}).get("mode", "auto")
        batch_mode = space.get("threads_batch", {}).get("mode", "auto")
        auto = (lead.get("threads_used") if not lead["cfg"].get("threads") else None) or _default_threads()
        logical = _cpu_count()
        known = set(p.get("base_threads") or [])
        generation_steps = (_thread_candidates(auto, logical, known) if gen_mode == "auto"
                            else [int(v) for v in space["threads"]["values"]] if gen_mode == "list" else [])
        batch_pool = {n for n in {auto, logical, *known} if 1 <= n <= logical}
        self._plan_max("threads", len(generation_steps) + (len(batch_pool) if batch_mode == "auto" else
                                                           len(space["threads_batch"]["values"]) if batch_mode == "list" else 0))
        best = lead
        for n in generation_steps:
            c = {**lead["cfg"], "threads": str(n)}
            t = await trial("Threads", _combo_label(c), c)
            if t["ok"] and t["score"] > best["score"] * (1 + NOISE):
                best = t
        generation = int(best["cfg"]["threads"] or auto)
        batch_steps = (sorted(batch_pool - {generation}) if batch_mode == "auto"
                       else [int(v) for v in space["threads_batch"]["values"]] if batch_mode == "list" else [])
        self._plan_max("threads", len(generation_steps) + len(batch_steps))
        for n in batch_steps:
            c = {**best["cfg"], "threads_batch": str(n)}
            t = await trial("Threads", _combo_label(c), c)
            if t["ok"] and t["score"] > best["score"] * (1 + NOISE):
                best = t
        return best

    async def _sweep(self, p: dict, inc: dict, keys: list[str], trial, skip, mark: bool = False) -> dict:
        """One list-mode dimension at a time on the incumbent: each value it doesn't already have, kept
        only for a >NOISE win. The stage is named after the dimension."""
        space = p.get("space") or {}
        for key in keys:
            label = DIMS[key]["label"]
            vals = [v for v in space[key]["values"] if v != inc["cfg"].get(key)
                    and not (key == "b" and int(v) < int(inc["cfg"]["ub"]))]
            self._plan_max("dim:" + key, len(vals))
            for v in vals:
                c = {**inc["cfg"], key: v}
                name = _flags_text(key, v) or f"{label} {v}"
                if why := _prune_reason(c, p, slots=False):
                    skip(label, name, c, why)
                    continue
                t = await trial(label, name, c)
                if _better(t, inc):
                    inc = t
                    if mark:
                        t["chosen"] = True
        return inc

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
            vram_free = self._vram_free()
            out.update({
                "decode0": statistics.median(x["predicted_per_second"] for x in short),
                "decode_each": [x["predicted_per_second"] for x in short],
                "decodeD": deep["predicted_per_second"],
                "prefillD": deep["prompt_per_second"],
                "draft": _draft_rate(short + deeps),
                "power_w": gpu.sampler.mean_power(t0, t1),
                "vram_free_mib": vram_free,
                "threads_used": self.threads_used,
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

    async def _probe(self, c: dict, p: dict) -> dict:
        """Start llama-server with this configuration and check that it loaded with the full
        context and the VRAM headroom; no benchmark."""
        if self.cancelled:
            raise TuneCancelled()
        out = {"ok": False, "probe": True, "fits": False, "error": None}
        t_load = time.time()
        try:
            await self._launch(self._argv(c, p), p["base"]["env"])
            out["load_s"] = time.time() - t_load
            async with httpx.AsyncClient(base_url=URL, timeout=60) as cl:
                props = (await cl.get("/props")).json()
            n_ctx = (props.get("default_generation_settings") or {}).get("n_ctx")
            if n_ctx and int(n_ctx) < int(c["ctx"]):
                raise RuntimeError(f"loaded with {n_ctx:,} context instead of {int(c['ctx']):,}")
            out["vram_free_mib"] = vram_free = self._vram_free()
            need = p["headroom_mib"] + PROBE_MARGIN_MIB
            if vram_free is not None and vram_free < need:
                out["error"] = f"loads, but only {vram_free:.0f} MiB of VRAM left (needs {need} to benchmark)"
            else:
                out["fits"] = True
        except TuneCancelled:
            raise
        except Exception as e:
            out["error"] = self._explain(e)
        finally:
            await self._kill()
        return out

    def _vram_free(self) -> float | None:
        if gpu.sampler.available:
            s = gpu.sampler.sample()
            if s and s["vram_total"]:
                return (s["vram_total"] - s["vram_used"]) / 2**20
        return None

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
        self.threads_used = None
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
                await asyncio.sleep(POLL_S)

    async def _read(self) -> None:
        proc = self.proc
        while proc and proc.stdout:
            try:
                raw = await proc.stdout.readline()
            except ValueError:
                continue
            if not raw:
                break
            line = raw.decode("utf-8", "replace").rstrip()
            self.log.append(line)
            if self.threads_used is None and (m := THREADS_RE.search(line)):
                self.threads_used = int(m.group(1))

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
        if best["cfg"].get("ncmoe"):
            desc += f"; expert weights of {best['cfg']['ncmoe']} of {p['n_layers']} layers in system RAM"
        elif best["cfg"].get("ngl") not in (None, "99", "auto"):
            desc += f"; {best['cfg']['ngl']} of {p['n_layers'] + 1} layers on the GPU"
        if best["cfg"].get("threads") or best["cfg"].get("threads_batch"):
            desc += "; " + " ".join(f"{flag} {best['cfg'][key]}" for flag, key in (("-t", "threads"), ("-tb", "threads_batch"))
                                    if best["cfg"].get(key))
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
            row["live"] = self.live()
            events.publish("tune", row)


def _tuned_rows(c: dict) -> list[dict]:
    """The flag rows of a candidate: the saved preset's tuned part, and (flattened) the command line.
    A row's value is None for a flag without one (--kv-unified)."""
    # Full offload is measured exactly as given (--fit off). Only when nothing fits fully does the
    # tuner let llama.cpp's --fit choose how many layers go to the GPU; context stays fixed by -c.
    pairs = [("--fit", "on")] if c["ngl"] == "auto" else [("-ngl", c["ngl"]), ("--fit", "off")]
    if c.get("ncmoe"):
        pairs.append(("--n-cpu-moe", c["ncmoe"]))
    pairs += [("-c", c["ctx"]), ("-np", c["np"]), ("-fa", c["fa"]), ("-ctk", c["ctk"]), ("-ctv", c["ctv"]),
              ("-ub", c["ub"]), ("-b", c["b"])]
    if c.get("spec"):
        pairs.append(("--spec-type", c["spec"]))
        if c.get("draft"):
            pairs.append(("-md", c["draft"]))
        pairs.append(("--spec-draft-n-max", c["nmax"]))
    if c.get("threads"):
        pairs.append(("-t", c["threads"]))
    if c.get("threads_batch"):
        pairs.append(("-tb", c["threads_batch"]))
    rows = [{"flag": f, "value": v} for f, v in pairs]
    for key in [*EXTRA_KEYS, *SPEC_ORDER]:  # the dimensions only a search space sets, after today's flags
        if _applies(c, key):
            rows += tunespace.emit(key, c[key])
    return rows


def _tuned_args(c: dict) -> list[str]:
    return [x for r in _tuned_rows(c) for x in ((r["flag"],) if r["value"] is None else (r["flag"], r["value"]))]


def _applies(c: dict, key: str) -> bool:
    """Is this dimension set on the candidate (draft settings only count with the speculative option they belong to)?"""
    if c.get(key) is None:
        return False
    cls = DIMS[key]["cls"]
    return bool(c.get("spec")) if cls == "SPEC" else bool(c.get("draft")) if cls == "DRAFT" else True


def _flags_text(key: str, value: str) -> str:
    return " ".join(r["flag"] if r["value"] is None else f"{r['flag']} {r['value']}" for r in tunespace.emit(key, value))


def _combo_label(c: dict) -> str:
    parts = [f"-ctk {c['ctk']} -ctv {c['ctv']}", f"-fa {c['fa']}", f"-ub {c['ub']} -b {c['b']}"]
    if c.get("ncmoe"):
        parts.insert(0, f"--n-cpu-moe {c['ncmoe']}")
    elif c.get("ngl") not in (None, "99", "auto"):
        parts.insert(0, f"-ngl {c['ngl']}")
    threads = " ".join(f"{flag} {c[key]}" for flag, key in (("-t", "threads"), ("-tb", "threads_batch")) if c.get(key))
    if threads:
        parts.append(threads)
    if c.get("np") not in (None, "1"):
        parts.append(f"-np {c['np']}")
    parts += [t for k in EXTRA_KEYS if _applies(c, k) and (t := _flags_text(k, c[k]))]
    parts.append(f"{SPEC_LABEL.get(c['spec'], c['spec'])} {c['nmax']}" if c.get("spec") else "no spec")
    parts += [t for k in SPEC_ORDER if _applies(c, k) and (t := _flags_text(k, c[k]))]
    return " · ".join(parts)


def _complexity(c: dict) -> int:
    """How far a configuration is from llama.cpp's defaults; fewer changes win ties."""
    return ((c["fa"] != "on") + (c["ub"] != "512") + (c["b"] != "2048") + bool(c.get("spec"))
            + (c["ctk"] != "f16") + (c["ctv"] != "f16") + bool(c.get("ncmoe"))
            + (c.get("ngl") not in (None, "99", "auto")) + bool(c.get("threads")) + bool(c.get("threads_batch"))
            + (c.get("np") not in (None, "1")) + sum(_applies(c, k) and bool(tunespace.emit(k, c[k])) for k in [*EXTRA_KEYS, *SPEC_ORDER]))


def _vals(space: dict, key: str) -> list[str]:
    """The values the space gives a dimension (fixed, listed, or its off value); [] when it is on auto."""
    entry = space.get(key)
    return list(entry["values"]) if entry else []


def _listmode(space: dict, key: str) -> bool:
    return space.get(key, {}).get("mode") == "list"


def _contexts(p: dict) -> list[int]:
    """Contexts to try, largest first: the space's, else the target."""
    return sorted({int(v) for v in _vals(p.get("space") or {}, "ctx")} or {int(p["ctx"])}, reverse=True)


def _baseline(p: dict) -> dict:
    """What the space puts into every candidate from the start: its fixed values and the first value of
    each list. The context objective sweeps listed thread counts against llama.cpp's default instead."""
    space = p.get("space") or {}
    return {k: v[0] for k in GRID_ORDER if k not in ("fa", "ub", "b") and (v := _vals(space, k))
            and not (p["objective"] == "context" and k in ("threads", "threads_batch") and _listmode(space, k))}


def _fit_fa(space: dict) -> str:
    """The flash attention the fit stage uses: on when the space allows it."""
    fa = _vals(space, "fa") or ["on"]
    return "on" if "on" in fa else fa[0]


def _b_for(space: dict, ub: str) -> str | None:
    """The logical batch to run with a micro-batch: today's max(2048, ub), or the space's first that holds it."""
    bs = _vals(space, "b")
    return str(max(2048, int(ub))) if not bs else next((b for b in bs if int(b) >= int(ub)), None)


def _fit_batch(space: dict, vary_batch: bool) -> tuple[str, str]:
    """The (-ub, -b) of the fit stage: 512/2048 unless the space says otherwise."""
    ubs = tunespace.values(space, "ub", UBATCHES)
    for ub in ubs if "ub" in space else ["512", *(u for u in ubs if u != "512")]:
        if (b := _b_for(space, ub)) is not None:
            return ub, b
    raise ValueError("No micro-batch size fits under the logical batch size.")


def _batch_pairs(space: dict, vary_batch: bool) -> list[tuple[str, str]]:
    """Every (-ub, -b) to try, micro-batch outer. A logical batch below the micro-batch is never generated."""
    out = []
    for ub in tunespace.values(space, "ub", UBATCHES):
        bs = _vals(space, "b") if "b" in space else \
            [str(b) for b in sorted({max(2048, int(ub)), *([int(ub), 4096] if vary_batch else [])})]
        out += [(ub, b) for b in bs if int(b) >= int(ub)]
    return out


def _better(t: dict, inc: dict) -> bool:
    return t["ok"] and t["score"] is not None and t["score"] > inc["score"] * (1 + NOISE)


def _prune_reason(c: dict, p: dict, slots: bool = True) -> str | None:
    """Why a combination isn't worth running at all, or None."""
    if c["fa"] == "off" and c["ctv"] not in UNQUANTIZED:
        return "a quantized V cache needs flash attention"
    n = int(c.get("np") or 1)
    if slots and n > 1 and c.get("kvu") != "on" and int(c["ctx"]) // n < p["depth"] + N_DEEP + 64:
        return f"the deep prompt doesn't fit in one of {n} slots"
    return None


def _with_spec(c: dict, s: dict) -> dict:
    """The configuration with another speculative option; the previous option's draft settings go."""
    return {**{k: v for k, v in c.items() if k not in SPEC_KEYS and k not in SPEC_ORDER}, **s}


def _speed_dims(p: dict, skip: tuple) -> list[str]:
    space = p.get("space") or {}
    return [k for k in GRID_ORDER if k not in skip and _listmode(space, k)]


def _sub_dims(p: dict, inc: dict) -> list[str]:
    """Listed draft settings worth trying on this incumbent: none unless it uses speculative decoding."""
    space = p.get("space") or {}
    return [k for k in SPEC_ORDER if _listmode(space, k) and _applies({**inc["cfg"], k: ""}, k)]


def _grid(kv_fit: list[dict], spec_opts: list[dict], vary_batch: bool, space: dict | None = None):
    """Every combination of the grid search in its fixed nested order: KV type, flash attention, -ub,
    -b, the other speed dimensions the space sets, speculative option (and its draft settings).
    Combinations a flag can't express (-b below -ub) are not generated; duplicates are dropped."""
    space = space or {}
    keys = [k for k in GRID_ORDER if k not in ("fa", "ub", "b") and _vals(space, k)]
    seen: set[str] = set()
    for kvt in kv_fit:
        base_c = kvt["cfg"]
        fas = _vals(space, "fa") or (["on", "off", "auto"] if base_c["ctv"] == "f16" else ["on"])
        for fa in fas:
            for ub, bb in _batch_pairs(space, vary_batch):
                for combo in itertools.product(*(_vals(space, k) for k in keys)):
                    for s in spec_opts:
                        for sv in _spec_variants(space, s):
                            c = {**base_c, "fa": fa, "ub": ub, "b": bb, **dict(zip(keys, combo)), **sv}
                            if (key := json.dumps(c, sort_keys=True)) not in seen:
                                seen.add(key)
                                yield c


def _spec_variants(space: dict, s: dict):
    """A speculative option at every combination of its listed draft settings (SPEC ones for any,
    DRAFT ones only with a draft file); the option itself when none is listed."""
    keys = [k for k in SPEC_ORDER if _listmode(space, k) and (DIMS[k]["cls"] == "SPEC" and s.get("spec")
                                                              or DIMS[k]["cls"] == "DRAFT" and s.get("draft"))]
    for combo in itertools.product(*(space[k]["values"] for k in keys)):
        yield {**s, **dict(zip(keys, combo))}


def _plan(p: dict) -> list[dict]:
    """The stages of a run in the order they happen, for the progress display on the Tuner page.

    Each has an id, a label, `stages` (the stage names its trials carry) and `max`: the most trials it
    will measure, or None while that isn't known. The run narrows some of them as it learns more (the
    grid's size once the KV types are known), see Tuner._plan_max. Quick and context runs get one
    `dim:<key>` stage per dimension the space lists.
    """
    def step(step_id: str, label: str, stages: list[str], n: int | None = None) -> dict:
        return {"id": step_id, "label": label, "stages": stages, "max": n}

    def dim(k: str) -> dict:
        return step("dim:" + k, DIMS[k]["label"], [DIMS[k]["label"]], len(space[k]["values"]))

    space = p.get("space") or {}
    options = _spec_options(p)
    counts: dict[str, int] = {}
    for s in options:
        name = SPEC_LABEL.get(s["spec"], s["spec"])
        counts[name] = counts.get(name, 0) + 1
    spec = [step("spec:" + name, f"Speculative: {name}", [f"Speculative ({name})"], n) for name, n in counts.items()]
    subs = [dim(k) for k in SPEC_ORDER if _listmode(space, k) and any(
        s.get("draft") or DIMS[k]["cls"] == "SPEC" for s in options)]
    confirm = step("confirm", "Confirm", ["Confirm"], 3)
    if p["objective"] == "context":
        sweeps = [space.get(k, {}).get("mode", "auto") in ("auto", "list") for k in ("threads", "threads_batch")]
        threads = [step("threads", "CPU threads", ["Threads"])] if any(sweeps) else []
        return [step("spill", "Spill search", ["Fit", "Spill"]), *threads,
                *map(dim, _speed_dims(p, ("ub", "b", "threads", "threads_batch"))), *spec, *subs, confirm]
    kv = step("kv", "KV cache", ["KV cache"])
    if p["mode"] == "quick":  # the first fit already ran -ub 512, so one batch size less
        fa, ub = space.get("fa"), space.get("ub")
        n_fa = 2 if fa is None else len(fa["values"]) - 1 if fa["mode"] == "list" else 0
        n_ub = len(UBATCHES) - 1 if ub is None else len(ub["values"]) - 1 if ub["mode"] == "list" else 0
        return [kv, step("fa", "Flash attention", ["Flash attention"], n_fa),
                step("batch", "Batch size", ["Batch size"], n_ub),
                *map(dim, _speed_dims(p, ("fa", "ub"))), *spec, *subs]
    return [kv, step("grid", "Every combination", ["Grid"]), confirm]


def _spec_groups(p: dict) -> list[dict]:
    """The speculative option kinds and their draft lengths, as tunespace.count wants them."""
    space = p.get("space") or {}
    if space.get("spec", {}).get("mode") == "off":
        return []
    groups = [{"lengths": list(MTP_NMAX), "file": False, "block": None}] if p["has_mtp"] and p.get("speculative", True) else []
    return groups + [{"lengths": _draft_lengths(d), "file": True, "block": int(d.get("block_size") or 0) or None}
                     for d in p.get("drafts") or []]


def _spec_options(p: dict) -> list[dict]:
    """The speculative-decoding candidates for this run: the MTP head and each chosen draft file,
    each at every draft length worth measuring (the space's nmax values replace them, clamped to a
    draft file's block size). Draft settings the space fixes ride along; listed ones are varied by
    the grid (_spec_variants) or one at a time (quick, context)."""
    space = p.get("space") or {}
    if space.get("spec", {}).get("mode") == "off":
        return []
    fixed = {k: v[0] for k in SPEC_ORDER if not _listmode(space, k) and (v := _vals(space, k))}
    opts = []
    sources = ([None] if p["has_mtp"] and p.get("speculative", True) else []) + list(p.get("drafts") or [])  # None: the MTP head
    for g, d in zip(_spec_groups(p), sources):
        sub = {k: v for k, v in fixed.items() if d or DIMS[k]["cls"] == "SPEC"}
        opts += [{"spec": d["type"] if d else "draft-mtp", "draft": d["path"] if d else None,
                  "nmax": n, **sub} for n in tunespace._spec_lengths(space, g)]
    return opts


def _spec_flags(s: dict) -> str:
    return f"--spec-type {s['spec']}" + (f" -md {os.path.basename(s['draft'])}" if s.get("draft") else "") + \
        f" --spec-draft-n-max {s['nmax']}"


def _draft_lengths(d: dict) -> list[str]:
    """Draft lengths to try for a draft file. DSpark and DFlash clamp --spec-draft-n-max to the block
    size the draft was trained with, so nothing longer is worth a run."""
    block = int(d.get("block_size") or 0)
    if d["type"] == "draft-dflash":  # drafts a whole block; quarter and half of it are the shorter options
        n = block or 15
        steps = {max(1, n // 4), max(1, n // 2), n}
    elif d["type"] == "draft-dspark":  # the llama.cpp examples use 7
        steps = {3, 5, 7, 10}
    elif d["type"] == "draft-eagle3":
        steps = {2, 3, 4, 5}
    else:  # a plain draft model is much cheaper per token than the target, so longer runs can pay
        steps = {4, 8, 16}
    if block:
        steps = {min(n, block) for n in steps}
    return [str(n) for n in sorted(steps)]


def _chosen_drafts(meta: dict, wanted: list[str] | None, speculative: bool) -> list[dict]:
    """The draft files to measure. By default the nearest file of each kind found next to the model
    (none when speculative decoding is switched off); an explicit list names files from the library."""
    found = meta.get("drafts") or []
    if wanted is None:
        picked: dict[str, dict] = {}
        for d in found if speculative else []:
            picked.setdefault(d["type"], d)  # the library lists the best name match first
        chosen = list(picked.values())
    else:
        by_path = {d["path"]: d for d in found}
        missing = [w for w in wanted if w not in by_path]
        if missing:
            raise ValueError(f"{missing[0]} is not a draft file next to this model.")
        chosen = [by_path[w] for w in wanted]
    return [{k: d.get(k) for k in ("path", "name", "type", "block_size", "size")} for d in chosen]


def _flag_int(rows: list[dict], names: tuple[str, ...]) -> int | None:
    for r in rows:
        if (r.get("flag") or "").strip() in names and r.get("enabled") is not False:
            try:
                return int(r.get("value"))
            except (TypeError, ValueError):
                return None
    return None


def _needs_more(c: dict, f: dict) -> bool:
    """Does c need at least the memory of f, a configuration that didn't fit? Everything that takes memory
    is equal (layers, KV types, flash attention, slots, offload flags...; a missing key is None), the
    batches and the draft model's GPU layers are no smaller, settings with no VRAM effect are ignored, and
    either f has no speculative decoding or c uses the same one at no shorter a length (the MTP head and
    each draft file take different amounts)."""
    for k in set(c) | set(f):
        if k in SPEC_KEYS or k in NO_MEMORY_EFFECT:
            continue
        if k in GROWS:
            if int(c.get(k) or 0) < int(f.get(k) or 0):
                return False
        elif c.get(k) != f.get(k):
            return False
    return not f.get("spec") or (c.get("spec") == f["spec"] and c.get("draft") == f.get("draft")
                                 and int(c["nmax"] or 0) >= int(f["nmax"] or 0))


def _cpu_count() -> int:
    """Logical CPUs this process may run on (a container can be limited to fewer than the host has)."""
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 1


def _default_threads() -> int:
    """llama.cpp's default thread count when its log doesn't say: one per physical core, which is half
    the logical CPUs on a machine with SMT."""
    n = _cpu_count()
    return max(1, n // 2) if n >= 4 else n


def _thread_candidates(auto: int, logical: int, known: set[int]) -> list[int]:
    """Generation thread counts to measure against llama.cpp's default (auto): half and three quarters
    of it, since decode over weights in system RAM is bound by memory bandwidth and more threads only
    contend for it; every logical CPU; and whatever the preset already used."""
    steps = {max(1, auto // 2), max(1, round(auto * 3 / 4)), logical, *known}
    steps.discard(auto)
    return sorted(n for n in steps if 1 <= n <= logical)


def _spill_axis(p: dict) -> str | None:
    """What spills weights to system RAM: the experts (--n-cpu-moe) of a MoE model, whole layers (-ngl)
    otherwise; a MoE model whose experts the space holds in VRAM falls back to layers, and None when
    that is held too."""
    space = p.get("space") or {}
    off = lambda k: space.get(k, {}).get("mode") == "off"
    if p["is_moe"] and not off("ncmoe"):
        return "ncmoe"
    return None if off("ngl") else "ngl"


def _spill_max(p: dict, axis: str | None) -> int:
    """Largest spill: every layer's experts (MoE), or every layer plus the output layer (dense)."""
    return p["n_layers"] if axis == "ncmoe" else p["n_layers"] + 1 if axis == "ngl" else 0


def _spill_of(c: dict, p: dict, axis: str | None) -> int:
    """How many layers' weights this configuration keeps in system RAM (the inverse of _spilled)."""
    if axis == "ncmoe":
        return int(c.get("ncmoe") or 0)
    if axis == "ngl":
        return _spill_max(p, axis) - int(c["ngl"]) if c.get("ngl") not in (None, "99", "auto") else 0
    return 0


def _spilled(c: dict, n: int, p: dict, axis: str | None) -> dict:
    """The configuration with n layers' weights in system RAM instead of VRAM."""
    if axis == "ncmoe":
        return {**c, "ncmoe": str(n) if n else None}
    if axis == "ngl":
        return {**c, "ngl": str(_spill_max(p, axis) - n) if n else "99"}
    return c


def _spill_value(p: dict, axis: str, v: str) -> int:
    """The spill a given -n-cpu-moe or -ngl value means ("auto" lets llama.cpp spill as much as it needs)."""
    return int(v) if axis == "ncmoe" else _spill_max(p, axis) if v == "auto" else max(0, _spill_max(p, axis) - int(v))


def _with_spill(c: dict, v: str, axis: str) -> dict:
    return {**c, "ncmoe": v if int(v) else None} if axis == "ncmoe" else {**c, "ngl": v}


def _ram_cap(p: dict, max_n: int) -> int | None:
    """How many layers' weights fit in free system RAM, keeping 4 GiB for everything else.
    The weights are mmapped, so page cache already holding the model file counts as free."""
    try:
        with open("/proc/meminfo") as f:
            info = {l.split(":")[0]: int(l.split()[1]) * 1024 for l in f}
    except (OSError, ValueError, IndexError):
        return None
    avail = info.get("MemAvailable")
    if not avail or not p.get("model_size"):
        return None
    per_layer = p["model_size"] / max_n
    return max(0, min(max_n, int((avail - 4 * 2**30) / per_layer)))


def _draft_rate(ts: list[dict]) -> float | None:
    n = sum(t.get("draft_n") or 0 for t in ts)
    return (sum(t.get("draft_n_accepted") or 0 for t in ts) / n) if n else None


def _score(t: dict, objective: str) -> float:
    decode = (t["decode0"] * t["decodeD"]) ** 0.5
    if objective == "prefill":
        return t["prefillD"]
    if objective in ("balanced", "context"):
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
