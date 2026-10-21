"""Which model answers a request: by the model name in the body, else by what the endpoint is for."""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import models
from .roles import ENDPOINT_ROLE


@dataclass
class Route:
    kind: str  # "instance" (use it, wait for it if still loading) | "swap" (load a preset first) | "none"
    instance: object | None = None
    preset: dict | None = None
    reason: str = ""


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _eq(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def match_instance(name: str, instances: list) -> object | None:
    """Exact names (alias, preset name, --alias) beat the slug of a name, which beats the model file's name."""
    tiers = (
        lambda i: i.aliases(),
        lambda i: [slug(a) for a in i.aliases()],
        lambda i: [i.model_name, i.model_path],
    )
    for names in tiers:
        hits = [i for i in instances if any(_eq(name, n) for n in names(i) if n)]
        if hits:
            return max(hits, key=lambda i: (i.state == "ready", i.last_used))
    return None


def match_preset(name: str, presets: list[dict]) -> dict | None:
    tiers = (
        lambda p: [p.get("alias") or "", p["name"]],
        lambda p: [slug(p["name"]), slug(p.get("alias") or "")],
        lambda p: [models.display_name(p["model"])] if p.get("model") else [],
    )
    for names in tiers:
        hits = [p for p in presets if any(n and _eq(name, n) for n in names(p))]
        if hits:
            return max(hits, key=lambda p: p.get("last_started") or 0)
    return None


def _newest(instances: list) -> object:
    return max(instances, key=lambda i: (i.state == "ready", i.last_used, -(i.run_id or 0)))


def resolve(name: str | None, path: str, instances: list, presets: list[dict], swap: bool) -> Route:
    """`instances` are the Fleet's live ones; `presets` carry `last_started` (when each last ran)."""
    usable = [i for i in instances if i.state in ("starting", "loading", "ready") and not i.draining]
    if name:
        inst = match_instance(name, usable)
        if inst:
            return Route("instance", inst)
        if swap:
            p = match_preset(name, presets)
            if p:
                return Route("swap", preset=p)
    want = ENDPOINT_ROLE.get(path)
    if want:
        cands = [i for i in usable if i.role == want]
        if cands:
            return Route("instance", _newest(cands))
        if swap:
            ps = [p for p in presets if (p.get("role") or "chat") == want]
            if ps:
                return Route("swap", preset=max(ps, key=lambda p: p.get("last_started") or 0))
        if want == "decision":
            return Route("none", reason="No decision model is loaded.")
        # embeddings and reranking fall through to the default model, which answers or explains
    chat = [i for i in usable if i.role == "chat"]
    if chat:
        return Route("instance", _newest(chat))
    if want and usable:
        return Route("instance", _newest(usable))
    return Route("none", reason="No chat model is loaded." if usable else "")
