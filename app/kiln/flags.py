"""Flag metadata parsed from `llama-server --help`, plus command-line import."""
from __future__ import annotations

import re
import shlex
import subprocess

from . import config

DESC_COL = 40
_cache: dict | None = None

# Flags Kiln sets itself; they are dropped from presets and imported commands.
MANAGED = {"--host", "--port", "-m", "--model", "--metrics"}


def _parse_spec(spec: str) -> tuple[list[str], str]:
    names, rest = [], []
    for tok in spec.split():
        clean = tok.rstrip(",")
        if not rest and clean.startswith("-") and len(clean) > 1 and not clean[1].isdigit():
            names.append(clean)
        else:
            rest.append(tok)
    return names, " ".join(rest)


def _choices(arg: str) -> list[str]:
    m = re.fullmatch(r"[\[{<]?([A-Za-z0-9_.\-|,]+)[\]}>]?", arg)
    if not m:
        return []
    body = m.group(1)
    for sep in ("|", ","):
        if sep in body:
            parts = [p for p in body.split(sep) if p]
            if all(re.fullmatch(r"[A-Za-z0-9_.\-]+", p) for p in parts):
                return parts
    return []


def parse_help(text: str) -> list[dict]:
    flags: list[dict] = []
    section = ""
    cur: dict | None = None
    for line in text.splitlines():
        if line.startswith("-----"):
            section = line.strip("- ").replace(" params", "")
            cur = None
            continue
        if line.startswith("-"):
            if len(line) > DESC_COL and line[DESC_COL - 2:DESC_COL] == "  ":
                spec, desc = line[:DESC_COL].strip(), line[DESC_COL:].strip()
            else:
                spec, desc = line.strip(), ""
            names, arg = _parse_spec(spec)
            if not names:
                continue
            cur = {
                "names": names,
                "arg": arg,
                "takes_value": bool(arg) and not arg.startswith("["),
                "optional_value": arg.startswith("["),
                "choices": _choices(arg),
                "desc": desc,
                "env": None,
                "section": section,
            }
            flags.append(cur)
        elif cur is not None and line.startswith(" " * (DESC_COL - 4)):
            part = line.strip()
            m = re.fullmatch(r"\(env: ([A-Z0-9_]+)\)", part)
            if m:
                cur["env"] = m.group(1)
            else:
                cur["desc"] = (cur["desc"] + " " + part).strip()
    for f in flags:
        if not f["choices"] and f["takes_value"]:
            m = re.search(r"allowed values: ([\w.\-]+(?:, [\w.\-]+)+)", f["desc"])
            if m:
                f["choices"] = m.group(1).split(", ")
    return flags


def load() -> dict:
    """Run `llama-server --help` once and cache the parsed result."""
    global _cache
    if _cache is not None:
        return _cache
    try:
        out = subprocess.run(
            [config.LLAMA_SERVER, "--help"], capture_output=True, text=True, timeout=30
        )
        text = out.stdout + out.stderr
        flags = parse_help(text)
        version, devices = "", []
        try:
            v = subprocess.run([config.LLAMA_SERVER, "--version"], capture_output=True, text=True, timeout=30)
            vm = re.search(r"^version: (.+)$", v.stdout + v.stderr, re.M)
            version = vm.group(1).strip() if vm else ""
            d = subprocess.run([config.LLAMA_SERVER, "--list-devices"], capture_output=True, text=True, timeout=60)
            text_d = d.stdout + d.stderr
            if "Available devices:" in text_d:
                tail = text_d.split("Available devices:", 1)[1]
                devices = [ln.strip() for ln in tail.splitlines() if re.match(r"^\s+\S+: ", ln)]
        except Exception:
            pass
        _cache = {"flags": flags, "version": version, "binary": config.LLAMA_SERVER, "error": None,
                  "devices": devices}
    except Exception as e:  # binary missing or broken
        _cache = {"flags": [], "version": "", "binary": config.LLAMA_SERVER, "error": str(e), "devices": []}
    return _cache


def lookup(name: str) -> dict | None:
    for f in load()["flags"]:
        if name in f["names"]:
            return f
    return None


def to_argv(flag_rows: list[dict]) -> list[str]:
    argv: list[str] = []
    for r in flag_rows:
        flag = (r.get("flag") or "").strip()
        if not flag or flag in MANAGED or r.get("enabled") is False:
            continue
        argv.append(flag)
        val = r.get("value")
        if val is not None and str(val) != "":
            argv.append(str(val))
    return argv


def parse_command(cmd: str) -> dict:
    """Turn a pasted `llama-server ...` command into {model, flags, env, dropped}."""
    cmd = cmd.replace("\\\n", " ")
    tokens = shlex.split(cmd, comments=False)
    env: dict[str, str] = {}
    # leading VAR=value assignments
    while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
        k, v = tokens.pop(0).split("=", 1)
        env[k] = v
    if tokens and not tokens[0].startswith("-"):
        tokens.pop(0)  # the binary
    model = ""
    rows: list[dict] = []
    dropped: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if not tok.startswith("-"):
            dropped.append(tok)
            i += 1
            continue
        name, inline = (tok.split("=", 1) + [None])[:2] if tok.startswith("--") and "=" in tok else (tok, None)
        meta = lookup(name)
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if inline is not None:
            value, step = inline, 1
        elif meta is not None:
            if meta["takes_value"]:
                value, step = nxt, 2
            elif meta["optional_value"] and nxt is not None and nxt in meta["choices"]:
                value, step = nxt, 2
            else:
                value, step = None, 1
        else:
            # Unknown flag: take the next token as its value unless it looks like a flag.
            looks_value = nxt is not None and (not nxt.startswith("-") or re.fullmatch(r"-\d+(\.\d+)?", nxt))
            value, step = (nxt, 2) if looks_value else (None, 1)
        if name in ("-m", "--model"):
            model = value or ""
        elif name in MANAGED:
            dropped.append(name + (f" {value}" if value else ""))
        else:
            rows.append({"flag": name, "value": value})
        i += step
    return {"model": model, "flags": rows, "env": env, "dropped": dropped}
