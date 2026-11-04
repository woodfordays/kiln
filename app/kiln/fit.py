"""Will another model fit next to the ones that are running?

An estimate from the GGUF header and the flags (weights + KV cache + compute buffer + projector),
checked against the GPU memory that is free right now. Once Kiln has watched a model load, the VRAM
it really took replaces the estimate for the same model and flags.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from . import config, db, gpu, models, roles

MIB = 1 << 20
MARGIN_MIB = 512  # left free after loading: other processes, the desktop, the driver
PROCESS_MIB = 300  # backend context and driver allocations every llama-server process makes
KV_BYTES = {"f32": 4.0, "f16": 2.0, "bf16": 2.0, "q8_0": 34 / 32, "q5_1": 24 / 32, "q5_0": 22 / 32,
            "q4_1": 20 / 32, "q4_0": 18 / 32, "iq4_nl": 18 / 32}

ALIASES = {
    "ctx": ("-c", "--ctx-size"), "np": ("-np", "--parallel"), "ctk": ("-ctk", "--cache-type-k"),
    "ctv": ("-ctv", "--cache-type-v"), "ngl": ("-ngl", "--gpu-layers", "--n-gpu-layers"),
    "ub": ("-ub", "--ubatch-size"), "b": ("-b", "--batch-size"), "fit": ("-fit", "--fit"),
    "ncmoe": ("-ncmoe", "--n-cpu-moe"), "cmoe": ("-cmoe", "--cpu-moe"), "mmproj": ("-mm", "--mmproj"),
    "device": ("-dev", "--device"), "mmproj_off": ("--no-mmproj-offload",), "no_mmproj": ("--no-mmproj", "--no-mmproj-auto"),
    "swa_full": ("--swa-full",),
    "draft": ("-md", "--model-draft"), "ngld": ("-ngld", "--gpu-layers-draft", "--n-gpu-layers-draft"),
    "ctxd": ("-cd", "--ctx-size-draft"), "ctkd": ("-ctkd", "--cache-type-k-draft"),
    "ctvd": ("-ctvd", "--cache-type-v-draft"), "devd": ("-devd", "--device-draft"),
}
FLAG_ONLY = ("cmoe", "mmproj_off", "no_mmproj", "swa_full")


def effective(rows: list[dict]) -> dict:
    """The memory-relevant settings in a list of flag rows, by canonical name. Disabled rows don't count."""
    out: dict = {}
    for r in rows:
        flag = (r.get("flag") or "").strip()
        if r.get("enabled") is False:
            continue
        for key, names in ALIASES.items():
            if flag in names:
                v = r.get("value")
                out[key] = True if v in (None, "") and key in FLAG_ONLY else v
    return out


def _int(v, default=None):
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return default


def _kv_bytes(name: str | None) -> float:
    return KV_BYTES.get((name or "f16").lower(), 2.0)


def file_size(path: str) -> int:
    """Bytes in a model file, all its shards, or every file under a package directory."""
    try:
        if Path(path).is_dir():
            return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())
        return sum(f.stat().st_size for f in models.files_for(path))
    except OSError:
        return 0


def signature(model: str, rows: list[dict], role: str) -> str:
    """Identifies a model + flags + role, so a measured footprint is reused only for the same launch."""
    live = sorted((r.get("flag", ""), str(r.get("value") or "")) for r in rows if r.get("enabled") is not False)
    return hashlib.sha1(json.dumps([model, live, role]).encode()).hexdigest()[:16]


def estimate_audio(model: str, rows: list[dict]) -> dict:
    """The same figures for an audio.cpp model: the files plus a fifth for activations, on the GPU unless --backend cpu."""
    backend = next((str(r.get("value") or "") for r in rows
                    if (r.get("flag") or "").strip() == "--backend" and r.get("enabled") is not False and r.get("value")),
                   config.BACKEND)
    size = file_size(model) * 1.2
    on_gpu = backend.lower() != "cpu"
    return {
        "gpu_mib": round(size / MIB + PROCESS_MIB) if on_gpu else 0, "ram_mib": 0 if on_gpu else round(size / MIB),
        "adaptive": False, "context": 0,
        "parts": {"weights": round(size / MIB) if on_gpu else 0, "kv": 0, "compute": 0, "projector": 0},
    }


def estimate(model: str, rows: list[dict], role: str = "chat") -> dict:
    """MiB the model will want on the GPU (and in RAM) with these flags. All figures are estimates."""
    if role in roles.AUDIO_ROLES:
        return estimate_audio(model, rows)
    meta = models.info(model)
    eff = effective(rows)
    size = file_size(model)
    n_layers = int(meta.get("block_count") or 0)
    ngl = str(eff.get("ngl") or "").lower()
    on_gpu = str(eff.get("device") or "").lower() != "none" and ngl != "0"
    if not on_gpu:
        frac = 0.0
    elif ngl in ("", "all", "auto") or not n_layers or _int(ngl, 999) >= n_layers + 1:
        frac = 1.0
    else:
        frac = min(1.0, _int(ngl, 0) / (n_layers + 1))

    # Experts kept on the CPU (--n-cpu-moe N / --cpu-moe) are most of an MoE model's weights.
    expert_share = 0.9 if meta.get("expert_count") else 0.0
    n_cpu_moe = n_layers if eff.get("cmoe") else min(_int(eff.get("ncmoe"), 0) or 0, n_layers)
    cpu_moe = expert_share * (n_cpu_moe / n_layers) if n_layers else 0.0
    # The token-embedding table stays in system memory whatever -ngl says.
    embed = int(meta.get("vocab_size") or 0) * int(meta.get("embedding_length") or 0) * 0.6
    weights_gpu = max(0.0, size - embed) * frac * (1 - cpu_moe)

    ctx = _int(eff.get("ctx"), 0) or int(meta.get("context_length") or 4096)
    kv_total = _kv_tokens(meta, eff, ctx, _int(eff.get("np"), 1) or 1, _int(eff.get("ub"), 512) or 512) \
        * _kv_per_token(meta, eff)
    kv_gpu = kv_total * frac

    ub = _int(eff.get("ub"), 512) or 512
    n_embd = int(meta.get("embedding_length") or 4096)
    vocab = int(meta.get("vocab_size") or 32000)
    compute = (vocab * ub * 4 + ub * n_embd * 4 * 6) if frac else 0

    mm = 0
    if eff.get("mmproj") and not eff.get("mmproj_off") and frac:
        mm = file_size(str(eff["mmproj"])) + 256 * MIB

    gpu_mib = (weights_gpu + kv_gpu + compute + mm) / MIB + (PROCESS_MIB if frac else 0)
    ram_mib = (size - weights_gpu + kv_total - kv_gpu) / MIB
    ram_kv = (kv_total - kv_gpu) / MIB
    adaptive = str(eff.get("fit") or "on").lower() != "off" and ngl in ("", "auto") and not eff.get("ctx")
    return {
        "gpu_mib": round(gpu_mib), "ram_mib": round(ram_mib), "adaptive": adaptive, "context": ctx,
        "parts": {"weights": round(weights_gpu / MIB), "kv": round(kv_gpu / MIB),
                  "compute": round(compute / MIB), "projector": round(mm / MIB)},
        "ram_parts": {"weights": round(ram_mib - ram_kv), "kv": round(ram_kv)},
    }


GPU_KEYS = (("weights", "Weights"), ("mtp", "MTP head"), ("draft", "Draft model"), ("projector", "Projector"),
            ("kv", "KV cache"), ("compute", "Compute buffer"), ("overhead", "Process overhead"))
RAM_KEYS = (("weights", "Weights"), ("draft", "Draft model"), ("kv", "KV cache"))
BUFFER_KEYS = {"model": "weights", "kv": "kv", "rs": "kv", "recurrent": "kv", "compute": "compute", "output": "compute"}


def breakdown(model: str, rows: list[dict], buffers: dict | None = None, measured_mib: int | None = None) -> dict:
    """Where the memory goes, in MiB: estimated segments on the GPU and in RAM, what llama.cpp reported
    allocating (`buffers`, if the model is loaded), and per-GPU telemetry. estimate() is left as it was:
    its totals don't count a draft model; the draft shows up here only."""
    est, eff = estimate(model, rows), effective(rows)
    meta, p = models.info(model), est["parts"]
    gpu_seg = {k: 0 for k, _ in GPU_KEYS}
    ram_seg = {k: 0 for k, _ in RAM_KEYS}
    gpu_seg.update(weights=p["weights"], projector=p["projector"], kv=p["kv"], compute=p["compute"])
    gpu_seg["overhead"] = est["gpu_mib"] - p["weights"] - p["projector"] - p["kv"] - p["compute"]
    ram_seg.update(est["ram_parts"])
    layers, nextn = int(meta.get("block_count") or 0), int(meta.get("nextn_layers") or 0)
    if nextn > 0 and layers:  # the MTP layer's KV is already in kv; only its weights come out of the body
        gpu_seg["mtp"] = round(p["weights"] * nextn / layers)
        gpu_seg["weights"] -= gpu_seg["mtp"]
    draft = str(eff.get("draft") or "")
    if draft and Path(draft).exists():
        ctx = eff.get("ctxd") or est["context"]
        synth = [{"flag": f, "value": v} for f, v in (
            ("-c", ctx), ("-ngl", eff.get("ngld") or eff.get("ngl")), ("-ctk", eff.get("ctkd") or "f16"),
            ("-ctv", eff.get("ctvd") or "f16"), ("-dev", eff.get("devd")), ("-np", eff.get("np") or 1),
            ("-ub", eff.get("ub")), ("--fit", "off")) if v not in (None, "")]
        d = estimate(draft, synth)
        dp = d["parts"]
        gpu_seg["draft"] = dp["weights"] + dp["kv"] + dp["compute"]  # same process: no second overhead
        ram_seg["draft"] = d["ram_mib"]
    segs = lambda keys, seg: [{"key": k, "label": label, "mib": seg[k]} for k, label in keys]
    return {
        "context": est["context"], "np": _int(eff.get("np"), 1) or 1,
        "ctk": str(eff.get("ctk") or "f16"), "ctv": str(eff.get("ctv") or "f16"),
        "gpu": segs(GPU_KEYS, gpu_seg), "ram": segs(RAM_KEYS, ram_seg),
        "gpu_mib": sum(gpu_seg.values()), "ram_mib": sum(ram_seg.values()),
        "measured_mib": measured_mib, "actual": _actual(buffers), "devices": gpu_devices(),
    }


def _actual(buffers: dict | None) -> dict | None:
    """llama.cpp's own buffer sizes ("Vulkan0 model" -> MiB) grouped by memory and by segment."""
    if not buffers:
        return None
    out: dict = {"gpu": {}, "ram": {}, "devices": {}}
    for key, mib in buffers.items():
        dev, _, kind = key.rpartition(" ")
        seg = BUFFER_KEYS.get(kind.lower())
        if not seg:
            continue
        where = out["ram"] if dev.startswith("CPU") or "_Host" in dev else out["gpu"]
        where[seg] = where.get(seg, 0) + round(mib)
        if where is out["gpu"]:
            d = out["devices"].setdefault(dev, {})
            d[seg] = d.get(seg, 0) + round(mib)
    return out


def _kv_tokens(meta: dict, eff: dict, ctx: int, n_seq: int, ub: int) -> float:
    """Cache cells per full-attention layer, averaged over the layers: sliding-window layers only keep
    their window (plus a micro-batch), so a model with 3 window layers in 4 needs a fraction of the cache.
    The pattern is either one number (every Nth layer is full attention) or a flag per layer (true = window);
    anything else, or a list that is not one flag per layer, counts as full attention everywhere."""
    window, pattern = _int(meta.get("sliding_window"), 0) or 0, meta.get("sliding_window_pattern")
    n_layers = _int(meta.get("block_count"), 0) or 0
    if not window or not n_layers or eff.get("swa_full"):
        return ctx
    if isinstance(pattern, list):
        if len(pattern) != n_layers:
            return ctx
        full = sum(1 for windowed in pattern if not windowed)
    elif (every := _int(pattern, 0) or 0) >= 2:
        full = math.ceil(n_layers / every)
    else:
        return ctx
    swa_cells = min(ctx, math.ceil((window * n_seq + ub) / 256) * 256)
    return (full * ctx + (n_layers - full) * swa_cells) / n_layers


def _kv_per_token(meta: dict, eff: dict) -> float:
    n_layers = int(meta.get("block_count") or 0)
    heads_kv = meta.get("head_count_kv")
    heads = meta.get("head_count")
    n_embd = int(meta.get("embedding_length") or 0)
    first_heads = next((h for h in heads if h), 0) if isinstance(heads, list) else (heads or 0)
    key_len = int(meta.get("key_length") or (n_embd // first_heads if first_heads else 128))
    val_len = int(meta.get("value_length") or key_len)
    kb, vb = _kv_bytes(eff.get("ctk")), _kv_bytes(eff.get("ctv"))
    if isinstance(heads_kv, list):
        kv_heads_sum = sum(h for h in heads_kv if h)
    else:
        interval = int(meta.get("full_attention_interval") or 0)
        attn_layers = math.ceil(n_layers / interval) if interval else n_layers
        kv_heads_sum = (heads_kv or heads or 0) * attn_layers
    # an MTP head runs one more attention layer against its own cache
    kv_heads_sum += (heads_kv if isinstance(heads_kv, int) else 0) * int(meta.get("nextn_layers") or 0)
    return kv_heads_sum * (key_len * kb + val_len * vb)


# ---------------------------------------------------------------- what is free

def gpu_memory() -> dict | None:
    """Total and free VRAM in MiB from the newest telemetry sample, or None without GPU telemetry."""
    s = gpu.sampler.live[-1] if gpu.sampler.live else None
    if not s or s.get("vram_total") is None or s.get("vram_used") is None:
        return None
    return {"total_mib": round(s["vram_total"] / MIB), "free_mib": round((s["vram_total"] - s["vram_used"]) / MIB),
            "used_mib": round(s["vram_used"] / MIB)}


def gpu_devices() -> list[dict]:
    """Per-GPU total/used/free VRAM in MiB from the newest telemetry sample; [] without telemetry."""
    s = gpu.sampler.live[-1] if gpu.sampler.live else None
    cards = (s or {}).get("cards")
    if s and not cards and (mem := gpu_memory()):
        cards = [{"name": gpu.sampler.name or None, "vram_used": mem["used_mib"] * MIB, "vram_total": mem["total_mib"] * MIB}]
    return [{"name": c.get("name"), "total_mib": round(c["vram_total"] / MIB), "used_mib": round(c["vram_used"] / MIB),
             "free_mib": round((c["vram_total"] - c["vram_used"]) / MIB)}
            for c in cards or [] if c.get("vram_total") is not None and c.get("vram_used") is not None]


def ram_available_mib() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(re.sub(r"\D", "", line)) // 1024
    except OSError:
        pass
    return None


def measured_mib(sig: str) -> int | None:
    """VRAM a launch with this signature took last time, if Kiln watched it load on its own."""
    return db.scalar("SELECT gpu_mib FROM runs WHERE sig=? AND gpu_mib IS NOT NULL ORDER BY id DESC LIMIT 1", (sig,))


def is_tuned(model: str) -> bool:
    """Whether the model has a completed tuning run: the tuner measured it on this machine,
    so a spill to system RAM that looks too big for the free RAM starts without a confirmation."""
    return db.scalar("SELECT 1 FROM tunes WHERE model=? AND status='done' LIMIT 1", (model,)) is not None


def check(model: str, rows: list[dict], role: str, reclaim_mib: int = 0, reserved_mib: int = 0) -> dict:
    """Would this launch fit, counting `reclaim_mib` freed by models that would be stopped first and
    setting aside `reserved_mib` that models still loading have yet to take? A model that has been
    through tuning is exempt from the spill to system RAM check: the tuner already proved it runs."""
    est = estimate(model, rows, role)
    sig = signature(model, rows, role)
    measured = measured_mib(sig)
    need = measured if measured is not None else est["gpu_mib"]
    mem = gpu_memory()
    ram = ram_available_mib()
    out = {**est, "need_mib": need, "basis": "measured" if measured is not None else "estimated",
           "signature": sig, "reclaim_mib": reclaim_mib, "memory": mem, "fits": None, "reason": ""}
    if need and mem is not None:
        free = mem["free_mib"] + reclaim_mib - reserved_mib
        out["free_mib"] = free
        if need + MARGIN_MIB <= free:
            out["fits"] = True
        elif est["adaptive"] and measured is None:
            out["fits"], out["reason"] = True, "llama.cpp will shrink it to fit (--fit on); add --fit off to keep the settings exact."
        else:
            out["fits"] = False
            out["reason"] = (f"Needs about {need / 1024:.1f} GiB of VRAM plus {MARGIN_MIB} MiB to spare, "
                             f"and {free / 1024:.1f} GiB is free.")
    if out["fits"] is not False and ram is not None and est["ram_mib"] > max(0, ram - 1024):
        if is_tuned(model):
            out["fits"] = True
            out["reason"] = (f"Spills about {est['ram_mib'] / 1024:.1f} GiB to system RAM, more than the "
                             f"{ram / 1024:.1f} GiB that is available, but this model has been through tuning, "
                             f"so it starts without a confirmation.")
        else:
            out["fits"] = False
            out["reason"] = (f"Needs about {est['ram_mib'] / 1024:.1f} GiB of system RAM for the weights and cache that "
                             f"stay off the GPU, and {ram / 1024:.1f} GiB is available.")
    return out
