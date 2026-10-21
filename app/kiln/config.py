"""Runtime configuration, read once from the environment."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _find_llama_server() -> str:
    explicit = os.environ.get("KILN_LLAMA_SERVER")
    if explicit:
        return explicit
    for cand in ("/app/llama/llama-server", str(Path.home() / "llama.cpp/build/bin/llama-server")):
        if Path(cand).exists():
            return cand
    return shutil.which("llama-server") or "/app/llama/llama-server"


PKG_DIR = Path(__file__).resolve().parent

DATA_DIR = Path(_env("KILN_DATA_DIR", "/data"))
MODELS_DIR = Path(_env("KILN_MODELS_DIR", "/models"))
DOWNLOAD_DIR = Path(_env("KILN_DOWNLOAD_DIR", str(MODELS_DIR / "gguf")))
EXTRA_SCAN_DIRS = [Path(p) for p in _env("KILN_SCAN_DIRS", "").split(":") if p]
WEB_DIR = Path(_env("KILN_WEB_DIR", str(PKG_DIR.parent.parent / "web")))
DB_PATH = DATA_DIR / "kiln.db"
LOG_DIR = DATA_DIR / "logs"

LLAMA_SERVER = _find_llama_server()

UI_HOST = _env("KILN_UI_HOST", "0.0.0.0")
UI_PORT = int(_env("KILN_UI_PORT", "8090"))
API_HOST = _env("KILN_API_HOST", "0.0.0.0")
API_PORT = int(_env("KILN_API_PORT", "8080"))
INTERNAL_PORT = int(_env("KILN_INTERNAL_PORT", "8081"))
INTERNAL_URL = f"http://127.0.0.1:{INTERNAL_PORT}"
# Each running model gets its own llama-server on the next free port from here. The tuner's trial
# server lives far enough away that it never collides with them.
INSTANCE_PORTS = range(INTERNAL_PORT, INTERNAL_PORT + 32)
TUNER_PORT = int(_env("KILN_TUNER_PORT", str(INTERNAL_PORT + 100)))

# Ports the browser should use to reach the API, when they differ from the
# container's (for example a compose mapping of 18080:8080).
PUBLIC_API_PORT = int(_env("KILN_PUBLIC_API_PORT", str(API_PORT)))
PUBLIC_UI_PORT = int(_env("KILN_PUBLIC_UI_PORT", str(UI_PORT)))

UI_USER = _env("KILN_UI_USER", "kiln")
UI_PASSWORD = os.environ.get("KILN_UI_PASSWORD", "")

HF_HOME = Path(_env("HF_HOME", str(MODELS_DIR / "hf")))
HF_ENDPOINT = _env("HF_ENDPOINT", "https://huggingface.co").rstrip("/")

STOP_TIMEOUT_S = float(_env("KILN_STOP_TIMEOUT", "15"))
# A request that needs a model swapped in waits this long for it to load, and gives running
# requests this long to finish before the model they are using is stopped to make room.
SWAP_TIMEOUT_S = float(_env("KILN_SWAP_TIMEOUT", "900"))
DRAIN_TIMEOUT_S = float(_env("KILN_DRAIN_TIMEOUT", "60"))
GPU_SAMPLE_S = float(_env("KILN_GPU_SAMPLE_INTERVAL", "2"))


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
