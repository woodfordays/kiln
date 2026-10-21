"""What a preset is for. The role decides the flags Kiln adds and which API paths the model serves."""
from __future__ import annotations

from . import flags, models

ROLES = {
    "chat": {
        "label": "Chat",
        "adds": "",
        "help": "Completions, chat and responses. The default for requests that name no model.",
    },
    "embedding": {
        "label": "Embeddings",
        "adds": "--embeddings",
        "help": "Serves /v1/embeddings. Kiln adds --embeddings.",
    },
    "rerank": {
        "label": "Reranker",
        "adds": "--rerank",
        "help": "Serves /v1/rerank. Kiln adds --rerank.",
    },
    "decision": {
        "label": "Decision",
        "adds": "-b and -ub set to the context size; for Perplexity decider checkpoints also --embeddings --pooling none "
                "and non-causal attention",
        "help": "Scores options instead of generating text, through /v1/systemone.",
    },
}

# Paths that belong to one kind of model. Anything else goes to the model named in the request,
# or to the default chat model.
ENDPOINT_ROLE = {
    "/v1/embeddings": "embedding", "/embeddings": "embedding", "/embedding": "embedding",
    "/v1/rerank": "rerank", "/rerank": "rerank", "/reranking": "rerank", "/v1/reranking": "rerank",
    "/v1/systemone": "decision",
}

# Decision models read their whole prompt in one micro-batch, so the batch must hold the context.
DECISION_DEFAULT_CTX = 4096


def valid(role: str) -> str:
    if role not in ROLES:
        raise ValueError(f"Unknown role “{role}”. Use one of: {', '.join(ROLES)}.")
    return role


def decision_kind(model_path: str) -> str | None:
    info = models.info(model_path)
    return (info.get("decision") or {}).get("kind")


def _has(rows: list[dict], *names: str) -> bool:
    return any((r.get("flag") or "").strip() in names and r.get("enabled") is not False for r in rows)


def _value(rows: list[dict], *names: str) -> str | None:
    for r in rows:
        if (r.get("flag") or "").strip() in names and r.get("enabled") is not False and r.get("value") not in (None, ""):
            return str(r["value"])
    return None


def apply(role: str, model_path: str, rows: list[dict]) -> tuple[list[dict], list[str]]:
    """The flag rows to launch with, and notes for the log about what Kiln changed.

    Rows the user wrote are never edited; Kiln only appends what the role needs and drops what the
    role cannot use.
    """
    rows = [dict(r) for r in rows]
    notes: list[str] = []

    def add(flag: str, value: str | None = None, why: str = "") -> None:
        rows.append({"flag": flag, "value": value})
        notes.append(f"Added {flag}{' ' + value if value else ''}" + (f" ({why})" if why else ""))

    if role == "embedding" and not _has(rows, "--embedding", "--embeddings"):
        add("--embeddings", why="embedding role")
    elif role == "rerank" and not _has(rows, "--rerank", "--reranking"):
        add("--rerank", why="reranker role")
    elif role == "decision":
        info = models.info(model_path)
        kind = (info.get("decision") or {}).get("kind")
        if kind == "unsupported":
            raise ValueError("This decision checkpoint uses a request protocol Kiln does not implement, "
                             "and llama-server cannot serve it either.")
        ctx = _value(rows, "-c", "--ctx-size")
        if ctx is None:
            add("-c", str(DECISION_DEFAULT_CTX), "decision prompts are short")
            ctx = str(DECISION_DEFAULT_CTX)
        for flag in ("-b", "-ub"):
            if _value(rows, flag, {"-b": "--batch-size", "-ub": "--ubatch-size"}[flag]) is None:
                add(flag, ctx, "the prompt is read in one micro-batch")
        if kind == "pplx-v3":
            if not _has(rows, "--embeddings", "--embedding"):
                add("--embeddings", why="per-token hidden states")
            if _value(rows, "--pooling") is None:
                add("--pooling", "none", "the readout needs every position")
            add("--override-kv", f"{info['arch']}.attention.causal=bool:false", "full attention is non-causal")
            if _value(rows, "-np", "--parallel") is None:
                add("-np", "1", "one request at a time")
            if _has(rows, "--mmproj", "-mm"):
                rows = [r for r in rows if (r.get("flag") or "").strip() not in ("--mmproj", "-mm")]
                notes.append("Dropped --mmproj: images cannot be scored through llama-server yet")
    return rows, notes


def argv(role: str, model_path: str, rows: list[dict]) -> tuple[list[str], list[str]]:
    final, notes = apply(role, model_path, rows)
    return flags.to_argv(final), notes
