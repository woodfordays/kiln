"""Hugging Face Hub: resolve an ID to GGUF variants, and a resumable download queue."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

from . import config, db, events, models

SPLIT_RE = models.SHARD_RE


def token() -> str:
    if os.environ.get("HF_TOKEN"):
        return os.environ["HF_TOKEN"]
    s = db.get_settings().get("hf_token") or ""
    if s:
        return s
    try:
        return (config.HF_HOME / "token").read_text().strip()
    except OSError:
        return ""


def _headers() -> dict:
    h = {"User-Agent": "kiln/1.0"}
    t = token()
    if t:
        h["Authorization"] = f"Bearer {t}"
    return h


def parse_ref(text: str) -> dict:
    """Accept org/repo, org/repo:QUANT, or a huggingface.co URL (repo, tree, blob or resolve)."""
    s = text.strip()
    out = {"repo": "", "revision": "main", "quant": "", "file": ""}
    if re.match(r"^(https?://)?(www\.)?(huggingface\.co|hf\.co)/", s):
        if not s.startswith("http"):
            s = "https://" + s
        parts = [p for p in urlparse(s).path.split("/") if p]
        if len(parts) < 2:
            raise ValueError("That link doesn't name a model repository.")
        out["repo"] = f"{parts[0]}/{parts[1]}"
        if len(parts) >= 4 and parts[2] in ("blob", "resolve", "tree"):
            out["revision"] = parts[3]
            if parts[2] in ("blob", "resolve") and len(parts) > 4:
                out["file"] = "/".join(parts[4:])
        return out
    m = re.fullmatch(r"([\w.\-]+/[\w.\-]+)(?::([\w.\-]+))?", s)
    if not m:
        raise ValueError("Enter a model ID like unsloth/Qwen3-8B-GGUF, optionally with :Q4_K_M.")
    out["repo"], out["quant"] = m.group(1), (m.group(2) or "")
    return out


async def _list_tree(client: httpx.AsyncClient, repo: str, revision: str) -> list[dict]:
    url = f"{config.HF_ENDPOINT}/api/models/{repo}/tree/{quote(revision, safe='')}?recursive=true&expand=false"
    items: list[dict] = []
    while url:
        r = await client.get(url, headers=_headers())
        if r.status_code == 401 or r.status_code == 403:
            raise PermissionError("Hugging Face refused access. If the repo is gated or private, add a token in Settings.")
        if r.status_code == 404:
            raise LookupError(f"No model repository named {repo} (revision {revision}).")
        r.raise_for_status()
        items.extend(r.json())
        nxt = r.links.get("next", {}).get("url")
        url = nxt or ""
    return items


def _variant_key(path: str) -> str:
    return SPLIT_RE.sub(".gguf", path)


def _local_dest(repo: str, path: str) -> Path:
    return config.DOWNLOAD_DIR / repo / path


def _hf_cache_has(repo: str, path: str, size: int) -> bool:
    org, name = repo.split("/", 1)
    snaps = config.HF_HOME / "hub" / f"models--{org}--{name}" / "snapshots"
    if not snaps.exists():
        return False
    for snap in snaps.iterdir():
        f = snap / path
        try:
            if f.exists() and f.stat().st_size == size:
                return True
        except OSError:
            pass
    return False


async def resolve(text: str) -> dict:
    ref = parse_ref(text)
    repo, rev = ref["repo"], ref["revision"]
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        info_r = await client.get(f"{config.HF_ENDPOINT}/api/models/{repo}", headers=_headers())
        info = info_r.json() if info_r.status_code == 200 else {}
        tree = await _list_tree(client, repo, rev)

    ggufs = [t for t in tree if t.get("type") == "file" and t["path"].lower().endswith(".gguf")]
    groups: dict[str, list[dict]] = {}
    for t in ggufs:
        groups.setdefault(_variant_key(t["path"]), []).append(t)

    variants = []
    for key, files in groups.items():
        files.sort(key=lambda f: f["path"])
        base = os.path.basename(key)
        is_proj = "mmproj" in key.lower()
        size = sum(int(f.get("lfs", {}).get("size") or f.get("size") or 0) for f in files)
        local = all(
            (_local_dest(repo, f["path"]).exists() and _local_dest(repo, f["path"]).stat().st_size == int(f.get("size") or 0))
            or _hf_cache_has(repo, f["path"], int(f.get("size") or 0))
            for f in files
        )
        variants.append({
            "id": key,
            "name": re.sub(r"\.gguf$", "", base, flags=re.I),
            "quant": models.quant_from_name(base) or (key.split("/")[0] if "/" in key else ""),
            "kind": "projector" if is_proj else "model",
            "files": [{"path": f["path"], "size": int(f.get("size") or 0)} for f in files],
            "size": size,
            "downloaded": local,
        })
    variants.sort(key=lambda v: (v["kind"] != "model", v["size"]))

    want = ref["quant"].upper()
    selected = None
    if ref["file"]:
        selected = next((v["id"] for v in variants if any(f["path"] == ref["file"] for f in v["files"])), None)
    elif want:
        selected = next((v["id"] for v in variants if v["quant"].upper() == want), None) or next(
            (v["id"] for v in variants if want in v["name"].upper()), None)
    if selected is None:
        # llama.cpp's own -hf default
        selected = next((v["id"] for v in variants if v["kind"] == "model" and v["quant"].upper() == "Q4_K_M"), None)

    return {
        "repo": repo,
        "revision": rev,
        "requested_quant": ref["quant"],
        "selected": selected,
        "gated": bool(info.get("gated")),
        "private": bool(info.get("private")),
        "downloads": info.get("downloads"),
        "likes": info.get("likes"),
        "pipeline_tag": info.get("pipeline_tag"),
        "has_token": bool(token()),
        "variants": variants,
    }


# ---------------------------------------------------------------- downloads

class Downloader:
    def __init__(self) -> None:
        self.wake = asyncio.Event()
        self.progress: dict[int, dict] = {}
        self.cancelled: set[int] = set()
        self.current: int | None = None

    def rows(self) -> list[dict]:
        rows = db.rows("SELECT * FROM downloads ORDER BY id DESC LIMIT 50")
        for r in rows:
            r["files"] = json.loads(r["files"])
            p = self.progress.get(r["id"])
            if p:
                r.update(p)
        return rows

    def enqueue(self, repo: str, revision: str, variant: str, files: list[dict]) -> int:
        total = sum(int(f["size"]) for f in files)
        dest = str(config.DOWNLOAD_DIR / repo)
        free = shutil.disk_usage(_existing_parent(config.DOWNLOAD_DIR)).free
        if total > free:
            raise OSError(f"Not enough disk space: needs {total / 1e9:.1f} GB, {free / 1e9:.1f} GB free.")
        did = db.insert("downloads", {
            "repo": repo, "revision": revision, "variant": variant, "files": json.dumps(files),
            "dest": dest, "total_bytes": total, "done_bytes": 0, "status": "queued", "created_at": time.time(),
        })
        self._publish(did)
        self.wake.set()
        return did

    def cancel(self, did: int) -> None:
        row = db.row("SELECT status FROM downloads WHERE id=?", (did,))
        if not row:
            return
        if row["status"] in ("queued", "failed"):
            db.update("downloads", did, {"status": "cancelled", "finished_at": time.time()})
            self._cleanup_parts(did)
        elif row["status"] == "downloading":
            self.cancelled.add(did)
        self._publish(did)

    def retry(self, did: int) -> None:
        db.update("downloads", did, {"status": "queued", "error": None})
        self._publish(did)
        self.wake.set()

    def _cleanup_parts(self, did: int) -> None:
        row = db.row("SELECT * FROM downloads WHERE id=?", (did,))
        for f in json.loads(row["files"]):
            part = _local_dest(row["repo"], f["path"]).with_suffix(".gguf.part")
            part.unlink(missing_ok=True)

    def _publish(self, did: int) -> None:
        row = db.row("SELECT * FROM downloads WHERE id=?", (did,))
        if row:
            row["files"] = json.loads(row["files"])
            row.update(self.progress.get(did, {}))
            events.publish("download", row)

    async def run(self) -> None:
        while True:
            job = db.row("SELECT * FROM downloads WHERE status='queued' ORDER BY id LIMIT 1")
            if not job:
                self.wake.clear()
                await self.wake.wait()
                continue
            await self._run_job(job)

    async def _run_job(self, job: dict) -> None:
        did = job["id"]
        self.current = did
        files = json.loads(job["files"])
        total = job["total_bytes"]
        db.update("downloads", did, {"status": "downloading", "error": None})
        state = {"done": 0, "speed": 0.0, "file": ""}
        last_pub = 0.0
        last_bytes, last_t = 0, time.monotonic()

        def tick(force=False):
            nonlocal last_pub, last_bytes, last_t
            now = time.monotonic()
            if not force and now - last_pub < 0.5:
                return
            dt = now - last_t
            if dt >= 1.0:
                inst = (state["done"] - last_bytes) / dt
                state["speed"] = inst if state["speed"] == 0 else 0.6 * state["speed"] + 0.4 * inst
                last_bytes, last_t = state["done"], now
            self.progress[did] = {
                "done_bytes": state["done"], "speed": state["speed"], "current_file": state["file"],
                "eta_s": (total - state["done"]) / state["speed"] if state["speed"] > 0 else None,
            }
            last_pub = now
            self._publish(did)

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=30), follow_redirects=True) as client:
                for f in files:
                    dest = _local_dest(job["repo"], f["path"])
                    state["file"] = f["path"]
                    if dest.exists() and dest.stat().st_size == int(f["size"]):
                        state["done"] += int(f["size"])
                        continue
                    base_done = state["done"]
                    for attempt in range(5):
                        try:
                            await self._fetch(client, job, f, dest, did, lambda n: (state.__setitem__("done", base_done + n), tick()))
                            break
                        except (httpx.TransportError, httpx.RemoteProtocolError) as e:
                            if did in self.cancelled or attempt == 4:
                                raise
                            await asyncio.sleep(2 * (attempt + 1))
                    state["done"] = base_done + int(f["size"])
            tick(force=True)
            db.update("downloads", did, {"status": "done", "done_bytes": total, "finished_at": time.time()})
            events.publish("models", {"changed": True})
        except _Cancelled:
            self._cleanup_parts(did)
            db.update("downloads", did, {"status": "cancelled", "done_bytes": state["done"], "finished_at": time.time()})
        except Exception as e:
            db.update("downloads", did, {"status": "failed", "done_bytes": state["done"], "error": _explain(e)})
        finally:
            self.cancelled.discard(did)
            self.progress.pop(did, None)
            self.current = None
            self._publish(did)

    async def _fetch(self, client, job, f, dest: Path, did: int, on_progress) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(".gguf.part")
        have = part.stat().st_size if part.exists() else 0
        url = f"{config.HF_ENDPOINT}/{job['repo']}/resolve/{quote(job['revision'], safe='')}/{quote(f['path'])}"
        headers = _headers()
        if have:
            headers["Range"] = f"bytes={have}-"
        async with client.stream("GET", url, headers=headers) as r:
            if r.status_code in (401, 403):
                raise PermissionError("Hugging Face refused the download. Accept the model's terms on huggingface.co and add a token in Settings.")
            if r.status_code == 416:  # already complete
                have_ok = have == int(f["size"])
                if not have_ok:
                    part.unlink(missing_ok=True)
                    raise httpx.RemoteProtocolError("range not satisfiable; restarting file")
            else:
                r.raise_for_status()
                if r.status_code == 200 and have:
                    have = 0  # server ignored the range
                mode = "ab" if have else "wb"
                with open(part, mode) as out:
                    done = have
                    on_progress(done)
                    async for chunk in r.aiter_bytes(1 << 20):
                        if did in self.cancelled:
                            raise _Cancelled()
                        out.write(chunk)
                        done += len(chunk)
                        on_progress(done)
        size = part.stat().st_size
        if size != int(f["size"]):
            raise IOError(f"{f['path']}: got {size} bytes, expected {f['size']}.")
        part.rename(dest)


class _Cancelled(Exception):
    pass


def _existing_parent(p: Path) -> Path:
    while not p.exists():
        p = p.parent
    return p


def _explain(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"Hugging Face returned HTTP {e.response.status_code} for {e.request.url.path}."
    if isinstance(e, httpx.TransportError):
        return f"Network error: {e.__class__.__name__}. Retry when the connection is back."
    return str(e) or e.__class__.__name__


downloader = Downloader()
