"""The tuner's search space: which dimensions a run may vary, and how each is set.

A request's `space` maps dimension keys to {"mode": ..., "values": [...]}. Modes:
- auto: the tuner's built-in behaviour (the key is left out of the normalized space).
- off: excluded from the search and held at the dimension's `off` value (None: flag not passed).
- fixed: exactly one value, applied to every candidate.
- list: candidates in the order given (1 to 32, duplicates removed keeping the first).

This module only validates and describes the space; the search itself lives in tuner.py.
"""
from __future__ import annotations

import math
import re

MODES = ("auto", "off", "fixed", "list")
MAX_LIST = 32
MAX_CANDIDATES = 4096
AUTO_KV = ["f16", "q8_0", "q4_0"]  # ctk / ctv candidates when left on auto but the other side is set
KV_TYPES = ["f32", "f16", "bf16", "q8_0", "q5_1", "q5_0", "iq4_nl", "q4_1", "q4_0"]
KV_RANK = {"f32": 9, "f16": 8, "bf16": 8, "q8_0": 7, "q5_1": 6, "q5_0": 5, "iq4_nl": 4, "q4_1": 4, "q4_0": 3}
ONOFF = ["on", "off"]

# Dimensions in the order of the spec table. cls: MEM (memory-first), SPEED (searched for speed),
# SPEC (speculative sub-setting), DRAFT (only with a draft file), None (the speculative option itself).
# emit: the flag for a valued dimension, or {value: flag} for a toggle (a missing value emits nothing).
# off: the held value; None means the flag is not passed (ctx off means the target context, stored empty).
DIMS: dict[str, dict] = {
    "ctx": {"label": "Context", "flags": ["-c", "--ctx-size"], "kind": "int", "min": 512, "max": 1048576,
            "cls": "MEM", "off": None, "emit": "-c"},
    "ngl": {"label": "GPU layers", "flags": ["-ngl", "--gpu-layers", "--n-gpu-layers", "-fit", "--fit"],
            "kind": "int", "min": 0, "max": 999, "extra": ["auto"], "cls": "MEM", "off": "99", "emit": "-ngl"},
    "ncmoe": {"label": "Experts in RAM", "flags": ["--n-cpu-moe", "-ncmoe", "-cmoe", "--cpu-moe"],
              "kind": "int", "min": 0, "max": 999, "cls": "MEM", "off": None, "emit": "--n-cpu-moe"},
    "ctk": {"label": "K cache", "flags": ["-ctk", "--cache-type-k"], "kind": "choice", "choices": KV_TYPES,
            "cls": "MEM", "off": "f16", "emit": "-ctk"},
    "ctv": {"label": "V cache", "flags": ["-ctv", "--cache-type-v"], "kind": "choice", "choices": KV_TYPES,
            "cls": "MEM", "off": "f16", "emit": "-ctv"},
    "fa": {"label": "Flash attention", "flags": ["-fa", "--flash-attn"], "kind": "choice",
           "choices": ["on", "off", "auto"], "cls": "SPEED", "off": "on", "emit": "-fa"},
    "ub": {"label": "Micro-batch", "flags": ["-ub", "--ubatch-size"], "kind": "int", "min": 1, "max": 65536,
           "cls": "SPEED", "off": "512", "emit": "-ub"},
    "b": {"label": "Logical batch", "flags": ["-b", "--batch-size"], "kind": "int", "min": 1, "max": 65536,
          "cls": "SPEED", "off": "2048", "emit": "-b"},
    "np": {"label": "Parallel slots", "flags": ["-np", "--parallel"], "kind": "int", "min": 1, "max": 256,
           "cls": "SPEED", "off": "1", "emit": "-np"},
    "kvu": {"label": "Unified KV", "flags": ["-kvu", "--kv-unified", "-no-kvu", "--no-kv-unified"],
            "kind": "choice", "choices": ONOFF, "cls": "SPEED", "off": None,
            "emit": {"on": "--kv-unified", "off": "--no-kv-unified"}},
    "threads": {"label": "Threads", "flags": ["-t", "--threads"], "kind": "int", "min": 1, "max": 1024,
                "cls": "SPEED", "off": None, "emit": "-t"},
    "threads_batch": {"label": "Batch threads", "flags": ["-tb", "--threads-batch"], "kind": "int",
                      "min": 1, "max": 1024, "cls": "SPEED", "off": None, "emit": "-tb"},
    "load_mode": {"label": "Load mode", "flags": ["-lm", "--load-mode"], "kind": "choice",
                  "choices": ["auto", "none", "mmap", "mlock", "mmap+mlock", "dio"], "cls": "SPEED",
                  "off": None, "emit": "-lm"},
    "numa": {"label": "NUMA", "flags": ["--numa"], "kind": "choice",
             "choices": ["none", "distribute", "isolate", "numactl"], "cls": "SPEED", "off": None, "emit": "--numa"},
    "split_mode": {"label": "Split mode", "flags": ["-sm", "--split-mode"], "kind": "choice",
                   "choices": ["none", "layer", "row", "tensor"], "cls": "SPEED", "off": None, "emit": "-sm"},
    "tensor_split": {"label": "Tensor split", "flags": ["-ts", "--tensor-split"], "kind": "text",
                     "pattern": r"^\d+(\.\d+)?(,\d+(\.\d+)?)*$", "cls": "SPEED", "off": None, "emit": "-ts"},
    "main_gpu": {"label": "Main GPU", "flags": ["-mg", "--main-gpu"], "kind": "int", "min": 0, "max": 63,
                 "cls": "SPEED", "off": None, "emit": "-mg"},
    "kv_offload": {"label": "KV offload", "flags": ["-kvo", "--kv-offload", "-nkvo", "--no-kv-offload"],
                   "kind": "choice", "choices": ONOFF, "cls": "SPEED", "off": None,
                   "emit": {"on": "-kvo", "off": "-nkvo"}},
    "op_offload": {"label": "Op offload", "flags": ["--op-offload", "--no-op-offload"], "kind": "choice",
                   "choices": ONOFF, "cls": "SPEED", "off": None,
                   "emit": {"on": "--op-offload", "off": "--no-op-offload"}},
    "swa_full": {"label": "Full SWA cache", "flags": ["--swa-full"], "kind": "choice", "choices": ONOFF,
                 "cls": "SPEED", "off": None, "emit": {"on": "--swa-full"}},
    "repack": {"label": "Weight repacking", "flags": ["--repack", "-nr", "--no-repack"], "kind": "choice",
               "choices": ONOFF, "cls": "SPEED", "off": None, "emit": {"on": "--repack", "off": "--no-repack"}},
    "override_tensor": {"label": "Override tensor", "flags": ["-ot", "--override-tensor"], "kind": "text",
                        "pattern": r"^\S+=\S+$", "cls": "SPEED", "off": None, "emit": "-ot"},
    "poll": {"label": "Polling", "flags": ["--poll"], "kind": "int", "min": 0, "max": 100, "cls": "SPEED",
             "off": None, "emit": "--poll"},
    "prio": {"label": "Priority", "flags": ["--prio"], "kind": "int", "min": -1, "max": 3, "cls": "SPEED",
             "off": None, "emit": "--prio"},
    "cache_reuse": {"label": "Cache reuse", "flags": ["--cache-reuse"], "kind": "int", "min": 0, "max": 65536,
                    "cls": "SPEED", "off": None, "emit": "--cache-reuse"},
    "spec": {"label": "Speculative decoding", "flags": ["--spec-type", "-md", "--spec-draft-model", "--model-draft"],
             "kind": "spec", "cls": None, "off": None, "emit": {}},
    "nmax": {"label": "Draft max", "flags": ["--spec-draft-n-max"], "kind": "int", "min": 1, "max": 64,
             "cls": "SPEC", "off": "3", "emit": "--spec-draft-n-max"},
    "nmin": {"label": "Draft min", "flags": ["--spec-draft-n-min"], "kind": "int", "min": 0, "max": 64,
             "cls": "SPEC", "off": None, "emit": "--spec-draft-n-min"},
    "pmin": {"label": "Draft p-min", "flags": ["--spec-draft-p-min", "--draft-p-min"], "kind": "float",
             "min": 0.0, "max": 1.0, "cls": "SPEC", "off": None, "emit": "--spec-draft-p-min"},
    "ngld": {"label": "Draft GPU layers", "flags": ["-ngld", "--spec-draft-ngl", "--gpu-layers-draft",
                                                    "--n-gpu-layers-draft"],
             "kind": "int", "min": 0, "max": 999, "cls": "DRAFT", "off": None, "emit": "-ngld"},
    "ctkd": {"label": "Draft K cache", "flags": ["-ctkd", "--spec-draft-type-k", "--cache-type-k-draft"],
             "kind": "choice", "choices": KV_TYPES, "cls": "DRAFT", "off": None, "emit": "-ctkd"},
    "ctvd": {"label": "Draft V cache", "flags": ["-ctvd", "--spec-draft-type-v", "--cache-type-v-draft"],
             "kind": "choice", "choices": KV_TYPES, "cls": "DRAFT", "off": None, "emit": "-ctvd"},
}
GRID_ORDER = ["fa", "ub", "b", "np", "kvu", "threads", "threads_batch", "load_mode", "numa", "split_mode",
              "tensor_split", "main_gpu", "kv_offload", "op_offload", "swa_full", "repack", "override_tensor",
              "poll", "prio", "cache_reuse"]
SPEC_ORDER = ["nmin", "pmin", "ngld", "ctkd", "ctvd"]
SPEC_SUB = [k for k in SPEC_ORDER if DIMS[k]["cls"] == "SPEC"]  # the nmax list is the draft lengths, not a sub-dim
DRAFT_SUB = [k for k in SPEC_ORDER if DIMS[k]["cls"] == "DRAFT"]


def _float_str(x: float) -> str:
    s = f"{x:.10f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _value(key: str, raw) -> str:
    """One value as the string the request and the candidate configs use, or ValueError."""
    d = DIMS[key]
    label = d["label"]
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        raise ValueError(f"{label}: {raw!r} is not a value.")
    if isinstance(raw, str) and raw.strip() in d.get("extra", []):
        return raw.strip()
    kind = d["kind"]
    if kind == "choice":
        s = str(raw).strip()
        if s not in d["choices"]:
            raise ValueError(f"{label}: {s} is not one of {', '.join(d['choices'])}.")
        return s
    if kind == "text":
        s = str(raw).strip()
        if not re.fullmatch(d["pattern"], s):
            raise ValueError(f"{label}: '{s}' is not valid.")
        return s
    if kind == "int":
        if isinstance(raw, float):
            if not raw.is_integer():
                raise ValueError(f"{label}: {raw} is not a whole number.")
            raw = int(raw)
        s = str(raw).strip()
        if not re.fullmatch(r"-?\d+", s):
            raise ValueError(f"{label}: '{s}' is not a whole number.")
        n = int(s)
    else:  # float
        try:
            x = float(raw)
        except ValueError:
            raise ValueError(f"{label}: '{raw}' is not a number.") from None
        if not math.isfinite(x):
            raise ValueError(f"{label}: {raw} is not a number.")
        if x < d["min"]:
            raise ValueError(f"{label}: {_float_str(x)} is below {_float_str(d['min'])}.")
        if x > d["max"]:
            raise ValueError(f"{label}: {_float_str(x)} is above {_float_str(d['max'])}.")
        return _float_str(x)
    if n < d["min"]:
        raise ValueError(f"{label}: {n} is below {d['min']}.")
    if n > d["max"]:
        raise ValueError(f"{label}: {n} is above {d['max']}.")
    return str(n)


def normalize(space: dict | None) -> dict:
    """Validate a request's space and return {key: {"mode", "values"}} for every non-auto dimension.

    Raises ValueError with a short message naming the dimension. Auto dimensions are left out.
    off stores [off] (or [] when the dimension has no off value), whatever values were sent.
    """
    if space is None:
        return {}
    if not isinstance(space, dict):
        raise ValueError("The search space must be an object.")
    out: dict[str, dict] = {}
    for key, entry in space.items():
        if key not in DIMS:
            raise ValueError(f"Unknown search dimension: {key}.")
        label = DIMS[key]["label"]
        if not isinstance(entry, dict):
            raise ValueError(f"{label}: give a mode and values.")
        mode = entry.get("mode")
        if mode not in MODES:
            raise ValueError(f"{label}: unknown mode {mode!r}.")
        if mode == "auto":
            continue
        if key == "spec" and mode not in ("auto", "off"):
            raise ValueError(f"{label}: only off or auto.")
        if mode == "off":
            off = DIMS[key]["off"]
            out[key] = {"mode": mode, "values": [] if off is None else [off]}
            continue
        raw = entry.get("values") or []
        if not isinstance(raw, list):
            raise ValueError(f"{label}: values must be a list.")
        vals = list(dict.fromkeys(_value(key, v) for v in raw))
        if mode == "fixed" and len(vals) != 1:
            raise ValueError(f"{label}: fixed needs exactly one value.")
        if mode == "list" and not 1 <= len(vals) <= MAX_LIST:
            raise ValueError(f"{label}: a list needs 1 to {MAX_LIST} values.")
        out[key] = {"mode": mode, "values": vals}
    return out


def values(space: dict, key: str, auto: list[str]) -> list[str]:
    """The candidate values for a key in a normalized space: `auto` when the key is absent."""
    entry = space.get(key)
    return list(auto) if entry is None else list(entry["values"])


def kv_pairs(space: dict, kv_ladder: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The (ctk, ctv) pairs to try, most precise first. Both auto: the ladder, unchanged."""
    if "ctk" not in space and "ctv" not in space:
        return [tuple(p) for p in kv_ladder]
    pairs = [(k, v) for k in values(space, "ctk", AUTO_KV) for v in values(space, "ctv", AUTO_KV)]
    # Stable: equal ranks keep the order of the pairs above.
    return sorted(pairs, key=lambda kv: (KV_RANK[kv[0]] + KV_RANK[kv[1]], KV_RANK[kv[0]]), reverse=True)


def stripped_flags(space: dict) -> set[str]:
    """Every flag spelling of every non-auto dimension: removed from a base preset before the search."""
    return {f for key in space for f in DIMS[key]["flags"]}


def emit(key: str, value: str | None) -> list[dict]:
    """The preset flag rows for one dimension value (empty when the value passes no flag)."""
    d = DIMS[key]
    if value is None:
        return []
    flag = d["emit"]
    if isinstance(flag, dict):  # a toggle: its flag, or nothing
        f = flag.get(value)
        return [] if f is None else [{"flag": f, "value": None}]
    if key == "numa" and value == "none":
        return []
    return [{"flag": flag, "value": str(value)}]


def _listed(space: dict, key: str) -> int:
    """How many candidates a dimension multiplies by: its list length, else 1."""
    entry = space.get(key)
    return len(entry["values"]) if entry and entry["mode"] == "list" else 1


def _product(space: dict, keys: list[str]) -> int:
    n = 1
    for k in keys:
        n *= _listed(space, k)
    return n


def _spec_lengths(space: dict, g: dict) -> list[str]:
    """Draft lengths of one speculative option group: the group's own unless nmax is set."""
    nmax = space.get("nmax")
    if nmax is None:
        return list(g["lengths"])
    vals = nmax["values"]
    if g.get("file") and g.get("block"):  # a draft file's block size caps the length
        vals = [str(min(int(v), g["block"])) for v in vals]
    return list(dict.fromkeys(vals))


def _spec_count(space: dict, groups: list[dict], quick: bool) -> int:
    """Speculative options. Grid and context multiply the SPEC/DRAFT sub-dimensions in; quick (and
    the stages that follow it) add them one at a time, only when some option is speculative."""
    if space.get("spec", {}).get("mode") == "off":
        return 0
    options = 0
    has_file = False
    for g in groups:
        n = len(_spec_lengths(space, g))
        has_file = has_file or bool(g.get("file"))
        if quick:
            options += n
        else:
            options += n * _product(space, SPEC_SUB) * (_product(space, DRAFT_SUB) if g.get("file") else 1)
    if quick and options:
        options += sum(_listed(space, k) - 1 for k in SPEC_SUB) + (
            sum(_listed(space, k) - 1 for k in DRAFT_SUB) if has_file else 0)
    return options


def count(p: dict) -> int:
    """How many candidates a run measures (the typical count, as the Tuner page estimates it).

    p is the tuner's params dict, with these keys:
      objective    "context" or any other objective
      mode         "grid" or "quick"
      kv_policy    "precise" or "all"
      vary_batch   bool
      space        the normalized space (normalize()); {} for the default search
      kv_ladder    the tuner's KV_LADDER: [(ctk, ctv), ...]
      ubatches     the tuner's UBATCHES: ["256", ...] (the auto micro-batch values)
      spec_groups  one entry per speculative option group, [] when speculative is off:
                   {"lengths": ["1", "2", ...],  # the draft lengths for this group (MTP_NMAX, or
                                                 #   _draft_lengths(d) for a draft file)
                    "file": bool,                # True for a draft file (it has DRAFT sub-dims)
                    "block": int | None}         # a draft file's block size (0/None: no cap)
    """
    space = p.get("space") or {}
    precise = p["kv_policy"] == "precise"
    groups = p.get("spec_groups") or []
    kv_auto = "ctk" not in space and "ctv" not in space
    kv = 1 if precise else len(kv_pairs(space, p["kv_ladder"]))
    if p["objective"] == "context":
        # Per KV pair: a benchmark and a few load-only probes per batch size, then thread counts.
        thread_keys = ("threads", "threads_batch")
        if any(space.get(k, {}).get("mode") == "list" for k in thread_keys):
            threads = sum(len(space[k]["values"]) for k in thread_keys if space.get(k, {}).get("mode") == "list")
        else:
            threads = 5 if any(k not in space for k in thread_keys) else 0  # today's sweep while either is auto
        others = sum(_listed(space, k) - 1 for k in GRID_ORDER if k not in ("threads", "threads_batch"))
        # Speculative options run one at a time on the lead, like quick.
        return kv * (4 + 8 + 3 * 3) + threads + others + _spec_count(space, groups, quick=True) + 3
    if p["mode"] == "quick":
        if "fa" not in space:
            fa_extra = 2  # today's flash-attention stage
        else:
            fa_extra = len(space["fa"]["values"]) - 1 if space["fa"]["mode"] == "list" else 0
        ub_extra = len(values(space, "ub", p["ubatches"]))  # the page has always counted the first fit's -ub too
        b_extra = _listed(space, "b") if space.get("b", {}).get("mode") == "list" else 0
        speed = sum(_listed(space, k) - 1 for k in GRID_ORDER if k not in ("fa", "ub", "b"))
        return 4 + fa_extra + ub_extra + b_extra + speed + _spec_count(space, groups, quick=True)
    # Grid: every combination of KV pair, flash attention, (ub, b), the other SPEED lists and speculation.
    if kv_auto and not precise:
        kv = 2  # typical: one or two KV types fit at a long context
    fa = _listed(space, "fa") if "fa" in space else 3 if precise else 2
    batch = 0
    for ub in (int(v) for v in values(space, "ub", p["ubatches"])):
        if "b" in space:
            bs = [int(v) for v in space["b"]["values"]]
        else:  # today's rule: 2048 or ub, and 4096 as well when varying the batch
            bs = {max(2048, ub), *([ub, 4096] if p["vary_batch"] else [])}
        batch += sum(1 for b in bs if b >= ub)
    speed = _product(space, [k for k in GRID_ORDER if k not in ("fa", "ub", "b")])
    spec = _spec_count(space, groups, quick=False)
    return kv * fa * batch * speed * (1 + spec) + 3


def check(p: dict) -> int:
    """count(p), or ValueError when the run is too big to start."""
    n = count(p)
    if n > MAX_CANDIDATES:
        raise ValueError(f"This search has {n} combinations; narrow it to at most {MAX_CANDIDATES}.")
    return n
