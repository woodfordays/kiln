"""Builders for tiny GGUF files, so tests can exercise the real header reader."""
import struct
from pathlib import Path


def _s(x: str) -> bytes:
    b = x.encode()
    return struct.pack("<Q", len(b)) + b


def _value(v):
    if isinstance(v, bool):
        return 7, struct.pack("<?", v)
    if isinstance(v, int):
        return 4, struct.pack("<I", v)
    if isinstance(v, float):
        return 6, struct.pack("<f", v)
    if isinstance(v, str):
        return 8, _s(v)
    if isinstance(v, list):
        if v and isinstance(v[0], bool):
            return 9, struct.pack("<IQ", 7, len(v)) + b"".join(struct.pack("<?", x) for x in v)
        if v and isinstance(v[0], str):
            return 9, struct.pack("<IQ", 8, len(v)) + b"".join(_s(x) for x in v)
        return 9, struct.pack("<IQ", 5, len(v)) + b"".join(struct.pack("<i", x) for x in v)
    raise TypeError(v)


def q8_0_row(values: list[float]) -> bytes:
    """Quantize one row (a multiple of 32 values) the way ggml's Q8_0 does."""
    out = b""
    for i in range(0, len(values), 32):
        block = values[i:i + 32]
        d = max(abs(x) for x in block) / 127 or 1.0
        out += struct.pack("<e", d) + struct.pack("<32b", *[round(x / d) for x in block])
    return out


def write_gguf(path, kv: dict, tensors: list[tuple[str, tuple[int, ...], int, bytes]] = ()) -> str:
    """kv: key -> bool/int/float/str/list. tensors: (name, dims, ggml type, raw bytes)."""
    head = b"GGUF" + struct.pack("<IQQ", 3, len(tensors), len(kv))
    body = b""
    for k, v in kv.items():
        t, raw = _value(v)
        body += _s(k) + struct.pack("<I", t) + raw
    infos, data, off = b"", b"", 0
    for name, dims, ttype, raw in tensors:
        infos += _s(name) + struct.pack("<I", len(dims)) + struct.pack(f"<{len(dims)}Q", *dims) + struct.pack("<IQ", ttype, off)
        pad = (-len(raw)) % 32
        data += raw + b"\0" * pad
        off += len(raw) + pad
    pre = head + body + infos
    pre += b"\0" * ((-len(pre)) % 32)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(pre + data)
    return str(p)


def f32_rows(rows: list[list[float]]) -> bytes:
    return b"".join(struct.pack(f"<{len(r)}f", *r) for r in rows)


def llm(path, arch="llama", layers=4, embd=64, heads=8, kv_heads=2, ctx=4096, vocab=1000, **extra) -> str:
    """A header-only text model: enough for the library scan and the memory estimate."""
    kv = {"general.architecture": arch, "general.name": Path(path).stem, f"{arch}.block_count": layers,
          f"{arch}.embedding_length": embd, f"{arch}.attention.head_count": heads,
          f"{arch}.attention.head_count_kv": kv_heads, f"{arch}.context_length": ctx,
          "tokenizer.ggml.tokens": ["x"] * vocab, "general.file_type": 15, **extra}
    return write_gguf(path, kv)


import socket
import subprocess
import sys
import time
import urllib.request


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeServer:
    """A fake llama-server process on a free port, for tests that talk to one directly."""

    def __init__(self, *args: str, load_s: float = 0.0, model: str = "fake.gguf") -> None:
        from pathlib import Path
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        script = str(Path(__file__).with_name("fake_llama.py"))
        import os
        self.proc = subprocess.Popen([sys.executable, script, "-m", model, "--port", str(self.port), *args],
                                     env={**os.environ, "FAKE_LOAD_S": str(load_s)}, stdout=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen(self.url + "/health", timeout=1)
                return
            except Exception:
                time.sleep(0.05)
        raise RuntimeError("fake llama-server did not start")

    def close(self) -> None:
        self.proc.terminate()
        self.proc.wait(5)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def decider_checkpoint(path, codes=("A", "B", "C", "D"), temperature=2.0, rows=None) -> str:
    """A tiny pplx-decider protocol-v3 GGUF: 4-wide hidden states, readout rows at token ids 10..13."""
    n = len(codes)
    rows = rows or [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]][:n]
    vocab = 20
    table = [[0.0] * 4 for _ in range(vocab)]
    for i, r in enumerate(rows):
        table[10 + i] = r
    return write_gguf(path, {
        "general.architecture": "qwen35", "general.name": "Fake decider", "pplx_decider.protocol_version": 3,
        "pplx_decider.template": "joint-question-inline-readout-v1", "pplx_decider.marker": "<|fim_suffix|>",
        "pplx_decider.temperature": temperature, "pplx_decider.codes": list(codes),
        "pplx_decider.token_ids": list(range(10, 10 + n)), "pplx_decider.escape_tokens": ["<|im_end|>", "<|im_start|>"],
        "qwen35.block_count": 2, "qwen35.embedding_length": 4, "qwen35.context_length": 4096,
    }, [("output.weight", (4, vocab), 0, f32_rows(table))])


import json as _json
import time as _time


def make_preset(name, model, role="chat", alias="", pinned=0, flags=None, env=None) -> dict:
    """Insert a preset row and return it the way get_preset() does."""
    from kiln import db
    from kiln.server import get_preset
    pid = db.insert("presets", {"name": name, "model": str(model), "role": role, "alias": alias, "pinned": pinned,
                                "flags": _json.dumps(flags or []), "env": _json.dumps(env or {}),
                                "created_at": _time.time(), "updated_at": _time.time()})
    return get_preset(pid)


def fake_gpu(fleet, total=24000, per_model=6000):
    """Patch fit so every live instance 'uses' per_model MiB of a total-MiB GPU, and every launch needs per_model."""
    from unittest import mock
    from kiln import fit

    def mem():
        used = per_model * sum(1 for i in fleet.live() if i.state != "stopping")
        return {"total_mib": total, "free_mib": total - used, "used_mib": used}

    est = {"gpu_mib": per_model, "ram_mib": 0, "adaptive": False, "context": 4096, "parts": {}}
    return [mock.patch.object(fit, "gpu_memory", side_effect=mem),
            mock.patch.object(fit, "estimate", return_value=est),
            mock.patch.object(fit, "ram_available_mib", return_value=64000)]
