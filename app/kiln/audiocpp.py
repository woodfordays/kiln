"""audio.cpp's audiocpp_server: the config file Kiln writes for one model, and the command line."""
from __future__ import annotations

from . import config

# Kiln sets these itself, so a preset row for them is ignored.
MANAGED = {"--config", "--host", "--port", "--ui", "--no-ui", "-m", "--model"}
# Preset rows that go into the config file instead of onto the command line.
OPTIONS = {"--load-option": "load_options", "--session-option": "session_options",
           "--request-option": "default_request_options"}
MAPPED = {"--family", "--task", "--mode", "--device", "--threads", "--voice", "--backend", *OPTIONS}


def _split(flag: str, value: str) -> tuple[str, str]:
    key, eq, val = value.partition("=")
    if not eq or not key.strip():
        raise ValueError(f"{flag} needs key=value, not “{value}”.")
    return key.strip(), val


def config_for(name: str, model_path: str, rows: list[dict], role: str, port: int) -> tuple[dict, list[str]]:
    """The config file for one model, and the flags that go on the command line as they are."""
    entry: dict = {"id": name, "path": model_path, "mode": "offline",
                   "task": "tts" if role == "tts" else "asr"}
    top: dict = {"host": "127.0.0.1", "port": port, "backend": config.BACKEND, "lazy_load": False}
    extra: list[str] = []
    for r in rows:
        flag = (r.get("flag") or "").strip()
        if not flag or r.get("enabled") is False or flag in MANAGED:
            continue
        value = r.get("value")
        value = "" if value is None else str(value).strip()
        if flag not in MAPPED:
            extra.append(flag)
            if value:
                extra.append(value)
        elif flag in OPTIONS:
            k, v = _split(flag, value)
            entry.setdefault(OPTIONS[flag], {})[k] = v
        elif not value:
            continue
        elif flag == "--family":
            entry["family"] = value
        elif flag == "--task":
            entry["task"] = value
        elif flag == "--mode":
            entry["mode"] = value
        elif flag == "--device":
            entry["device"] = int(value) if value.lstrip("-").isdigit() else value
        elif flag == "--threads":
            top["threads"] = int(value) if value.isdigit() else value
        elif flag == "--voice":
            entry["default_voice_preset"] = {"voice_id": value}
        elif flag == "--backend":
            top["backend"] = value
    if "family" not in entry:
        raise ValueError("Add a row “--family <name>” (for example kokoro_tts or qwen3_asr).")
    return {**top, "models": [entry]}, extra


def argv(config_path: str, extra_argv: list[str]) -> list[str]:
    return [config.AUDIOCPP_SERVER, "--config", str(config_path), "--no-ui", *extra_argv]
