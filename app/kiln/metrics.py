"""Prometheus metrics for the control port's /metrics.

Request counters live in memory and start at zero with the process, as Prometheus expects: deleting
history (retention, Reset statistics) never makes a counter go backwards. GPU readings and the running
models are read when scraped, and each llama-server's own /metrics is passed through with a `model`
label added, so one scrape covers every model.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict

import httpx

from . import gpu

TTFT_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)
DURATION_BUCKETS = (0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600)
WAIT_BUCKETS = (0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)
UPSTREAM_TIMEOUT_S = 2

STARTED = time.time()

COUNTERS = {
    "requests": ("kiln_requests_total", "Requests through the API port, by result: ok, error or aborted (client hung up)."),
    "prompt": ("kiln_prompt_tokens_total", "New prompt tokens processed, not counting tokens reused from the prompt cache."),
    "cached": ("kiln_prompt_cached_tokens_total", "Prompt tokens reused from the prompt cache."),
    "generated": ("kiln_generated_tokens_total", "Tokens generated."),
    "prefill_tok": ("kiln_prefill_timed_tokens_total", "Prompt tokens of requests llama-server timed; divide by kiln_prefill_seconds_total for prefill speed."),
    "prefill_s": ("kiln_prefill_seconds_total", "Time llama-server spent processing prompts."),
    "decode_tok": ("kiln_decode_timed_tokens_total", "Generated tokens of requests llama-server timed; divide by kiln_decode_seconds_total for decode speed."),
    "decode_s": ("kiln_decode_seconds_total", "Time llama-server spent generating."),
    "draft": ("kiln_draft_tokens_total", "Tokens drafted by speculative decoding."),
    "draft_ok": ("kiln_draft_accepted_tokens_total", "Drafted tokens that were accepted."),
}
HISTOGRAMS = {
    "ttft": ("kiln_ttft_seconds", "Time to first token of successful requests, not counting time waiting for a model to load.", TTFT_BUCKETS),
    "duration": ("kiln_request_duration_seconds", "Wall time of requests.", DURATION_BUCKETS),
    "wait": ("kiln_model_wait_seconds", "Time requests waited for their model to be loaded or swapped in, for requests that had to wait.", WAIT_BUCKETS),
}


class _Histogram:
    def __init__(self, buckets: tuple) -> None:
        self.buckets = buckets
        self.counts = [0] * len(buckets)
        self.sum = 0.0
        self.count = 0

    def observe(self, v: float) -> None:
        for i, b in enumerate(self.buckets):
            if v <= b:
                self.counts[i] += 1
        self.sum += v
        self.count += 1


_counters: dict[str, dict[tuple, float]] = {k: defaultdict(float) for k in COUNTERS}
_histograms: dict[str, dict[tuple, _Histogram]] = {k: {} for k in HISTOGRAMS}


def _result(status: int) -> str:
    return "ok" if status < 400 else "aborted" if status == 499 else "error"


def _hist(kind: str, labels: tuple, v: float) -> None:
    h = _histograms[kind].get(labels)
    if h is None:
        h = _histograms[kind][labels] = _Histogram(HISTOGRAMS[kind][2])
    h.observe(v)


def observe(row: dict) -> None:
    """Count one row as it is written to the requests table."""
    model = row.get("model") or ""
    m = (model,)
    c = _counters
    c["requests"][(model, _result(row["status"]))] += 1
    for key, col in (("prompt", "prompt_n"), ("cached", "cache_n"), ("generated", "predicted_n"),
                     ("draft", "draft_n"), ("draft_ok", "draft_accepted")):
        if row.get(col):
            c[key][m] += row[col]
    # Speeds use only requests with both a count and a time, like the Overview page.
    if row.get("prompt_n") and row.get("prompt_ms"):
        c["prefill_tok"][m] += row["prompt_n"]
        c["prefill_s"][m] += row["prompt_ms"] / 1000
    if row.get("predicted_n") and row.get("predicted_ms"):
        c["decode_tok"][m] += row["predicted_n"]
        c["decode_s"][m] += row["predicted_ms"] / 1000
    if row.get("ttft_ms") is not None and row["status"] < 400:
        _hist("ttft", m, row["ttft_ms"] / 1000)
    if row.get("total_ms") is not None:
        _hist("duration", m, row["total_ms"] / 1000)
    if row.get("swap_ms"):
        _hist("wait", m, row["swap_ms"] / 1000)


def reset() -> None:
    """For tests."""
    for d in _counters.values():
        d.clear()
    for d in _histograms.values():
        d.clear()


# ---------------------------------------------------------------- exposition


def _esc(v) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(**kw) -> str:
    return "{" + ",".join(f'{k}="{_esc(v)}"' for k, v in kw.items()) + "}" if kw else ""


def _num(v: float) -> str:
    v = float(v)
    return str(int(v)) if v.is_integer() else repr(v)


class _Out:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def family(self, name: str, kind: str, help: str) -> None:
        self.lines += [f"# HELP {name} {help}", f"# TYPE {name} {kind}"]

    def sample(self, name: str, value, /, **labels) -> None:
        if value is not None:
            self.lines.append(f"{name}{_labels(**labels)} {_num(value)}")

    def gauge(self, name: str, help: str, value, /, **labels) -> None:
        if value is not None:
            self.family(name, "gauge", help)
            self.sample(name, value, **labels)


def _requests(out: _Out) -> None:
    for key, (name, help) in COUNTERS.items():
        out.family(name, "counter", help)
        for labels, v in sorted(_counters[key].items()):
            if key == "requests":
                out.sample(name, v, model=labels[0], result=labels[1])
            else:
                out.sample(name, v, model=labels[0])
    for key, (name, help, _) in HISTOGRAMS.items():
        out.family(name, "histogram", help)
        for (model,), h in sorted(_histograms[key].items()):
            for b, n in zip(h.buckets, h.counts):
                out.sample(f"{name}_bucket", n, model=model, le=_num(b))
            out.sample(f"{name}_bucket", h.count, model=model, le="+Inf")
            out.sample(f"{name}_sum", h.sum, model=model)
            out.sample(f"{name}_count", h.count, model=model)


def _gpu(out: _Out) -> None:
    s = gpu.sampler
    if not s.available:
        return
    out.gauge("kiln_gpu_info", "The GPU Kiln reads, and where the readings come from.", 1, name=s.name, source=s.source)
    last = s.live[-1] if s.live else {}
    busy = last.get("busy")
    out.gauge("kiln_gpu_busy_ratio", "GPU load, 0 to 1 (averaged over GPUs).", busy / 100 if busy is not None else None)
    out.gauge("kiln_gpu_vram_used_bytes", "GPU memory in use (summed over GPUs).", last.get("vram_used"))
    out.gauge("kiln_gpu_vram_total_bytes", "GPU memory (summed over GPUs).", last.get("vram_total"))
    out.gauge("kiln_gpu_power_watts", "GPU power draw (summed over GPUs).", last.get("power_w"))
    out.gauge("kiln_gpu_temperature_celsius", "GPU temperature (the hottest GPU).", last.get("temp_c"))
    sclk = last.get("sclk_mhz")
    out.gauge("kiln_gpu_clock_hertz", "GPU core clock.", sclk * 1e6 if sclk is not None else None)
    out.family("kiln_gpu_energy_joules_total", "counter", "GPU energy used since Kiln started, from the power readings.")
    out.sample("kiln_gpu_energy_joules_total", s.energy_j)


INSTANCE_GAUGES = [
    ("kiln_model_state", "1 for the state each model is in: starting, loading, ready, stopping or crashed."),
    ("kiln_model_gpu_memory_bytes", "GPU memory a running model uses, measured when it loaded (else estimated)."),
    ("kiln_model_context_size", "Context size llama-server reports."),
    ("kiln_model_inflight_requests", "Requests the model is answering now."),
    ("kiln_model_idle_seconds", "Seconds since the model's last request finished, or since it became ready."),
]


def _instances(out: _Out, insts: list) -> None:
    now = time.time()
    rows = {name: [] for name, _ in INSTANCE_GAUGES}
    for i in insts:
        if i.state == "stopped":
            continue
        lab = {"model": i.model_name, "name": i.name, "role": i.role}
        rows["kiln_model_state"].append((1, {**lab, "state": i.state}))
        mib = i.footprint_mib()
        rows["kiln_model_gpu_memory_bytes"].append((mib * 2**20 if mib else None, lab))
        rows["kiln_model_context_size"].append((i.props.get("n_ctx"), lab))
        rows["kiln_model_inflight_requests"].append((i.inflight, lab))
        rows["kiln_model_idle_seconds"].append((i.idle_s(now) if i.state == "ready" else None, lab))
    for name, help in INSTANCE_GAUGES:
        out.family(name, "gauge", help)
        for v, lab in rows[name]:
            out.sample(name, v, **lab)


_SAMPLE = re.compile(r"^([A-Za-z_:][A-Za-z0-9_:]*)(\{.*\})?\s+(\S.*)$")


def merge_upstream(texts: list[tuple[dict, str]]) -> list[str]:
    """Combine several llama-servers' /metrics into one exposition, each sample labelled with its model.

    Families keep the order they first appear in, with one HELP and TYPE each."""
    families: dict[str, dict] = {}
    for labels, text in texts:
        extra = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items())
        fam = None
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                parts = line.split(None, 3)
                if len(parts) >= 3 and parts[1] in ("HELP", "TYPE"):
                    fam = families.setdefault(parts[2], {"help": None, "type": None, "samples": []})
                    fam["help" if parts[1] == "HELP" else "type"] = line
                continue
            m = _SAMPLE.match(line)
            if not m:
                continue
            name, lab, value = m.groups()
            lab = "{" + extra + ("," + lab[1:] if lab and lab != "{}" else "}")
            if fam is None:
                fam = families.setdefault(name, {"help": None, "type": None, "samples": []})
            fam["samples"].append(f"{name}{lab} {value}")
    out = []
    for fam in families.values():
        out += [x for x in (fam["help"], fam["type"]) if x] + fam["samples"]
    return out


async def _upstream(insts: list) -> list[str]:
    ready = [i for i in insts if i.state == "ready" and not i.adapter]
    if not ready:
        return []
    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_S) as client:
        async def one(i):
            try:
                r = await client.get(f"{i.url}/metrics")
                return r.text if r.status_code == 200 else ""
            except httpx.HTTPError:
                return ""
        texts = await asyncio.gather(*(one(i) for i in ready))
    return merge_upstream([({"model": i.model_name, "name": i.name}, t) for i, t in zip(ready, texts) if t])


async def render(insts: list, info: dict) -> str:
    out = _Out()
    out.gauge("kiln_info", "Kiln and the llama.cpp build it runs.", 1, **info)
    out.gauge("kiln_start_time_seconds", "When Kiln started, as a Unix time.", STARTED)
    _requests(out)
    _gpu(out)
    _instances(out, insts)
    out.lines += await _upstream(insts)
    return "\n".join(out.lines) + "\n"
