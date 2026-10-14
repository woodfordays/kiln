"""The local model library: GGUF files under the models directory."""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import config, gguf

SHARD_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.I)
QUANT_RE = re.compile(
    r"(?:^|[-_.])((?:UD-)?(?:I?Q\d(?:_[A-Z0-9]+)*|F16|BF16|F32|MXFP4(?:_MOE)?|TQ\d_\d))(?=[-_.]|$)",
    re.I,
)
HF_CACHE_RE = re.compile(r"models--([^/]+)--([^/]+)/snapshots/[^/]+/")

_meta_cache: dict[tuple, dict] = {}


def quant_from_name(name: str) -> str:
    stem = SHARD_RE.sub(".gguf", name)
    stem = re.sub(r"\.gguf$", "", stem, flags=re.I)
    found = QUANT_RE.findall(stem)
    return found[-1].upper() if found else ""


def display_name(path: str) -> str:
    base = os.path.basename(path)
    base = SHARD_RE.sub("", base)
    return re.sub(r"\.gguf$", "", base, flags=re.I)


def repo_of(path: str) -> str:
    m = HF_CACHE_RE.search(path)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    try:
        rel = Path(path).relative_to(config.DOWNLOAD_DIR)
        parts = rel.parts
        if len(parts) >= 3:
            return f"{parts[0]}/{parts[1]}"
    except ValueError:
        pass
    return ""


def _metadata(path: str, st: os.stat_result) -> dict:
    key = (path, st.st_size, int(st.st_mtime))
    if key not in _meta_cache:
        try:
            _meta_cache[key] = gguf.summarize(gguf.read_metadata(path))
        except Exception as e:
            _meta_cache[key] = {"error": str(e)}
    return _meta_cache[key]


def scan_roots() -> list[Path]:
    roots = [config.MODELS_DIR, config.DOWNLOAD_DIR, *config.EXTRA_SCAN_DIRS]
    out: list[Path] = []
    for r in roots:
        r = r.resolve() if r.exists() else r
        if r.exists() and not any(r == o or o in r.parents for o in out):
            out = [o for o in out if r not in o.parents]
            out.append(r)
    return out


def scan() -> list[dict]:
    files: dict[str, list[str]] = {}
    for root in scan_roots():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "blobs"]
            for fn in filenames:
                if fn.lower().endswith(".gguf"):
                    files.setdefault(dirpath, []).append(fn)

    models: list[dict] = []
    for dirpath, names in files.items():
        names.sort()
        shards: dict[str, list[str]] = {}
        singles: list[str] = []
        for fn in names:
            m = SHARD_RE.search(fn)
            if m:
                shards.setdefault(SHARD_RE.sub("", fn), []).append(fn)
            else:
                singles.append(fn)
        groups = [[fn] for fn in singles] + list(shards.values())
        projectors = []
        entries = []
        for group in groups:
            first = os.path.join(dirpath, group[0])
            try:
                stats = [os.stat(os.path.join(dirpath, g)) for g in group]
            except OSError:
                continue  # dangling symlink in an HF cache
            meta = _metadata(first, stats[0])
            expected = meta.get("split_count") or (int(SHARD_RE.search(group[0]).group(2)) if SHARD_RE.search(group[0]) else 1)
            entry = {
                "path": first,
                "name": display_name(first),
                "files": len(group),
                "complete": len(group) >= (expected or 1),
                "size": sum(s.st_size for s in stats),
                "mtime": max(s.st_mtime for s in stats),
                "repo": repo_of(first),
                "quant": quant_from_name(group[0]) or meta.get("file_type", ""),
                "is_projector": bool(meta.get("is_projector")) or "mmproj" in group[0].lower(),
                **{k: meta.get(k) for k in ("arch", "size_label", "context_length", "block_count",
                                            "expert_count", "nextn_layers", "error")},
                "title": meta.get("name") or "",
            }
            (projectors if entry["is_projector"] else entries).append(entry)
        for e in entries:
            e["projectors"] = [p["path"] for p in projectors]
        models.extend(entries + projectors)
    models.sort(key=lambda m: (m["is_projector"], m["repo"].lower(), m["name"].lower()))
    return models


def files_for(path: str) -> list[Path]:
    """All files belonging to a model entry (every shard of a split model)."""
    p = Path(path)
    m = SHARD_RE.search(p.name)
    if not m:
        return [p]
    base = SHARD_RE.sub("", p.name)
    return sorted(q for q in p.parent.iterdir() if SHARD_RE.search(q.name) and SHARD_RE.sub("", q.name) == base)


def delete(path: str) -> list[str]:
    known = {m["path"] for m in scan()}
    if path not in known:
        raise ValueError("Not a model in the library.")
    removed = []
    for f in files_for(path):
        target = f.resolve() if f.is_symlink() else None
        f.unlink(missing_ok=True)
        removed.append(str(f))
        if target is not None and target.exists():
            target.unlink()  # the HF cache blob behind the snapshot link
            removed.append(str(target))
    # tidy empty download folders
    parent = Path(path).parent
    try:
        while parent != config.DOWNLOAD_DIR and config.DOWNLOAD_DIR in parent.parents and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    except OSError:
        pass
    return removed
