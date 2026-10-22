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
_WANT_PREFIX = ("general.", "split.", "pplx_decider.")
_WANT_SUFFIX = (
    ".context_length", ".block_count", ".embedding_length", ".expert_count",
    ".expert_used_count", ".attention.head_count", ".attention.head_count_kv",
    ".nextn_predict_layers", ".attention.key_length", ".attention.value_length",
    ".full_attention_interval", ".attention.causal", ".pooling_type", ".decision.type",
    ".attention.sliding_window", ".attention.sliding_window_pattern", ".block_size", ".has_confidence_head",
    ".vocab_size",
)
_WANT_EXACT = ("tokenizer.ggml.tokens",)


def _want(key: str) -> bool:
    return key.startswith(_WANT_PREFIX) or key.endswith(_WANT_SUFFIX) or key in _WANT_EXACT


def read_metadata(path: str, arrays: tuple[str, ...] = ()) -> dict:
    """Scalar metadata. Arrays are reduced to {"array": count} unless the key is listed in `arrays`
    (an exact key, or a suffix starting with a dot such as ".attention.head_count_kv")."""
    with open(path, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            return _parse(mm, arrays)[0]
        finally:
            mm.close()


def _parse(mm, arrays: tuple[str, ...] = ()) -> tuple[dict, int]:
    """Returns (metadata, offset of the first tensor-info record)."""
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

    def skip_or_read(t, o, keep, whole=False):
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
                fmt, size = _SCALARS[et]
                if whole:
                    return list(struct.unpack_from(f"<{count}{fmt[1]}", mm, o)), o + size * count
                return {"array": count}, o + size * count
            if et == _STRING:
                items = []
                for _ in range(count):
                    s, o = read_str(o) if whole else (None, o + 8 + struct.unpack_from("<Q", mm, o)[0])
                    items.append(s)
                return (items if whole else {"array": count}), o
            for _ in range(count):
                _, o = skip_or_read(et, o, False)
            return {"array": count}, o
        raise ValueError(f"unknown GGUF value type {t}")

    for _ in range(n_kv):
        key, off = read_str(off)
        t = struct.unpack_from("<I", mm, off)[0]
        off += 4
        whole = key in arrays or any(a.startswith(".") and key.endswith(a) for a in arrays)
        keep = _want(key) or whole
        val, off = skip_or_read(t, off, keep, whole=whole)
        if keep:
            meta[key] = val
    return meta, off


# ggml tensor type id -> (name, values per block, bytes per block)
_TENSOR_TYPES = {0: ("F32", 1, 4), 1: ("F16", 1, 2), 8: ("Q8_0", 32, 34), 30: ("BF16", 1, 2)}


def tensor_rows(path: str, name: str, rows: list[int]) -> list[list[float]]:
    """Dequantized rows of a 2-D tensor (`rows` index the second GGUF dimension, the vocabulary
    for output.weight). Only F32, F16, BF16 and Q8_0 are read; anything else is a ValueError."""
    with open(path, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            meta, off = _parse(mm)
            found = None
            for _ in range(meta["gguf.tensor_count"]):
                n = struct.unpack_from("<Q", mm, off)[0]
                tname = mm[off + 8:off + 8 + n].decode("utf-8", "replace")
                off += 8 + n
                n_dims = struct.unpack_from("<I", mm, off)[0]
                dims = struct.unpack_from(f"<{n_dims}Q", mm, off + 4)
                off += 4 + 8 * n_dims
                ttype, toff = struct.unpack_from("<IQ", mm, off)
                off += 12
                if tname == name:
                    found = (dims, ttype, toff)
            if not found:
                raise ValueError(f"{name} is not in this GGUF file")
            dims, ttype, toff = found
            if len(dims) != 2:
                raise ValueError(f"{name} is not a matrix")
            if ttype not in _TENSOR_TYPES:
                raise ValueError(f"{name} uses tensor type {ttype}; only F32, F16, BF16 and Q8_0 can be read")
            align = int(meta.get("general.alignment") or 32)
            data = (off + align - 1) // align * align
            tname_, per_block, block_bytes = _TENSOR_TYPES[ttype]
            row_bytes = dims[0] // per_block * block_bytes
            out = []
            for r in rows:
                if not 0 <= r < dims[1]:
                    raise ValueError(f"row {r} is outside {name} ({dims[1]} rows)")
                out.append(_dequant(mm, data + toff + r * row_bytes, dims[0], tname_))
            return out
        finally:
            mm.close()


def _dequant(mm, o: int, n: int, kind: str) -> list[float]:
    if kind == "F32":
        return list(struct.unpack_from(f"<{n}f", mm, o))
    if kind == "F16":
        return list(struct.unpack_from(f"<{n}e", mm, o))
    if kind == "BF16":
        raw = struct.unpack_from(f"<{n}H", mm, o)
        return list(struct.unpack(f"<{n}f", b"".join(struct.pack("<I", v << 16) for v in raw)))
    vals: list[float] = []
    for b in range(n // 32):
        d = struct.unpack_from("<e", mm, o + b * 34)[0]
        vals.extend(d * q for q in struct.unpack_from("<32b", mm, o + b * 34 + 2))
    return vals


EMBEDDING_ARCHS = {"bert", "nomic-bert", "nomic-bert-moe", "jina-bert-v2", "jina-bert-v3", "modern-bert",
                   "neo-bert", "eurobert", "gemma-embedding"}
PROTOCOL_V3_TEMPLATE = "joint-question-inline-readout-v1"
# Keys whose values are lists but that the library needs element-wise.
ARRAY_KEYS = (".attention.head_count_kv", ".attention.head_count", ".attention.sliding_window_pattern")


def decision_info(meta: dict) -> dict | None:
    """Is this a decision model, and who can serve it?

    native:   llama.cpp's own /v1/systemone (the GGUF carries <arch>.decision.type)
    pplx-v3:  Perplexity's decider with the joint inline-readout protocol, which Kiln serves itself
    unsupported: a pplx_decider checkpoint with a protocol Kiln does not implement
    """
    for k, v in meta.items():
        if k.endswith(".decision.type") and v:
            return {"kind": "native", "type": str(v)}
    if "pplx_decider.protocol_version" in meta:
        proto = meta.get("pplx_decider.protocol_version")
        ok = proto == 3 and meta.get("pplx_decider.template") == PROTOCOL_V3_TEMPLATE
        return {"kind": "pplx-v3" if ok else "unsupported", "type": "pplx-decider", "protocol": proto,
                "template": meta.get("pplx_decider.template"), "base_model": meta.get("pplx_decider.base_model")}
    return None


def draft_info(meta: dict, arch: str, filename: str = "") -> dict | None:
    """A companion file for speculative decoding (loaded with -md and --spec-type), or None."""
    name = " ".join(str(meta.get(k) or "") for k in ("general.name", "general.finetune", "general.basename")) + " " + filename
    name = name.lower()
    if arch == "dflash":
        spark = bool(meta.get("dflash.has_confidence_head")) or "dspark" in name
        return {"type": "draft-dspark" if spark else "draft-dflash", "block_size": meta.get("dflash.block_size")}
    if "eagle" in arch or "eagle3" in name:
        return {"type": "draft-eagle3"}
    if "draft" in name:
        return {"type": "draft-simple"}
    return None


def role_hint(info: dict, filename: str = "") -> str:
    """The role a new preset for this model should probably get."""
    if info.get("decision"):
        return "decision"
    name = f"{info.get('name') or ''} {filename}".lower()
    if "rerank" in name:
        return "rerank"
    if info.get("arch") in EMBEDDING_ARCHS or info.get("pooling_type") is not None or "embed" in name:
        return "embedding"
    return "chat"


def summarize(meta: dict, filename: str = "") -> dict:
    arch = meta.get("general.architecture", "")
    ft = meta.get("general.file_type")
    toks = meta.get("tokenizer.ggml.tokens")
    info = {
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
        # what the memory estimate needs
        "embedding_length": meta.get(f"{arch}.embedding_length"),
        "head_count": meta.get(f"{arch}.attention.head_count"),
        "head_count_kv": meta.get(f"{arch}.attention.head_count_kv"),
        "key_length": meta.get(f"{arch}.attention.key_length"),
        "value_length": meta.get(f"{arch}.attention.value_length"),
        "full_attention_interval": meta.get(f"{arch}.full_attention_interval"),
        "sliding_window": meta.get(f"{arch}.attention.sliding_window"),
        "sliding_window_pattern": meta.get(f"{arch}.attention.sliding_window_pattern"),
        "vocab_size": meta.get(f"{arch}.vocab_size") or (toks.get("array") if isinstance(toks, dict) else None),
        "pooling_type": meta.get(f"{arch}.pooling_type"),
        "causal": meta.get(f"{arch}.attention.causal"),
        "decision": decision_info(meta),
        "draft": None if arch == "clip" else draft_info(meta, arch, filename),
    }
    info["role_hint"] = role_hint(info, filename)
    return info
