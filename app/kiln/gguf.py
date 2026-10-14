"""Minimal GGUF header reader: returns scalar metadata, skips arrays."""
from __future__ import annotations

import mmap
import struct

GGUF_MAGIC = b"GGUF"

# type id -> (struct format, size)
_SCALARS = {
    0: ("<B", 1), 1: ("<b", 1), 2: ("<H", 2), 3: ("<h", 2), 4: ("<I", 4), 5: ("<i", 4),
    6: ("<f", 4), 7: ("<?", 1), 10: ("<Q", 8), 11: ("<q", 8), 12: ("<d", 8),
}
_STRING, _ARRAY = 8, 9

FILE_TYPES = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K",
    11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S",
    17: "Q5_K_M", 18: "Q6_K", 19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS",
    23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL", 26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S",
    29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16", 36: "TQ1_0", 37: "TQ2_0", 38: "MXFP4_MOE",
}

# Keys worth keeping; everything else (tokenizer vocab, chat template) is skipped.
_WANT_PREFIX = ("general.", "split.")
_WANT_SUFFIX = (
    ".context_length", ".block_count", ".embedding_length", ".expert_count",
    ".expert_used_count", ".attention.head_count", ".attention.head_count_kv",
    ".nextn_predict_layers",
)


def _want(key: str) -> bool:
    return key.startswith(_WANT_PREFIX) or key.endswith(_WANT_SUFFIX)


def read_metadata(path: str) -> dict:
    with open(path, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            return _parse(mm)
        finally:
            mm.close()


def _parse(mm) -> dict:
    if mm[:4] != GGUF_MAGIC:
        raise ValueError("not a GGUF file")
    version = struct.unpack_from("<I", mm, 4)[0]
    if version < 2:
        raise ValueError(f"GGUF v{version} is not supported")
    n_tensors, n_kv = struct.unpack_from("<QQ", mm, 8)
    off = 24
    meta: dict = {"gguf.version": version, "gguf.tensor_count": n_tensors}

    def read_str(o):
        n = struct.unpack_from("<Q", mm, o)[0]
        return mm[o + 8:o + 8 + n].decode("utf-8", "replace"), o + 8 + n

    def skip_or_read(t, o, keep):
        if t in _SCALARS:
            fmt, size = _SCALARS[t]
            return (struct.unpack_from(fmt, mm, o)[0] if keep else None), o + size
        if t == _STRING:
            if keep:
                return read_str(o)
            n = struct.unpack_from("<Q", mm, o)[0]
            return None, o + 8 + n
        if t == _ARRAY:
            et, count = struct.unpack_from("<IQ", mm, o)
            o += 12
            if et in _SCALARS:
                return {"array": count}, o + _SCALARS[et][1] * count
            if et == _STRING:
                for _ in range(count):
                    n = struct.unpack_from("<Q", mm, o)[0]
                    o += 8 + n
                return {"array": count}, o
            for _ in range(count):
                _, o = skip_or_read(et, o, False)
            return {"array": count}, o
        raise ValueError(f"unknown GGUF value type {t}")

    for _ in range(n_kv):
        key, off = read_str(off)
        t = struct.unpack_from("<I", mm, off)[0]
        off += 4
        keep = _want(key)
        val, off = skip_or_read(t, off, keep)
        if keep:
            meta[key] = val
    return meta


def summarize(meta: dict) -> dict:
    arch = meta.get("general.architecture", "")
    ft = meta.get("general.file_type")
    return {
        "arch": arch,
        "name": meta.get("general.name") or meta.get("general.basename") or "",
        "size_label": meta.get("general.size_label") or "",
        "file_type": FILE_TYPES.get(ft, str(ft) if ft is not None else ""),
        "context_length": meta.get(f"{arch}.context_length"),
        "block_count": meta.get(f"{arch}.block_count"),
        "expert_count": meta.get(f"{arch}.expert_count"),
        "expert_used_count": meta.get(f"{arch}.expert_used_count"),
        "nextn_layers": meta.get(f"{arch}.nextn_predict_layers"),
        "split_count": meta.get("split.count"),
        "is_projector": arch == "clip" or meta.get("general.type") == "mmproj",
    }
