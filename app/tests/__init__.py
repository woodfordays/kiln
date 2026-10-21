"""Tests run against a scratch data directory and a fake llama-server (tests/fake_llama.py)."""
import os
import tempfile
from pathlib import Path

_root = Path(tempfile.mkdtemp(prefix="kiln-test-"))
os.environ.update({
    "KILN_DATA_DIR": str(_root / "data"),
    "KILN_MODELS_DIR": str(_root / "models"),
    "KILN_LLAMA_SERVER": str(Path(__file__).with_name("fake_llama.py")),
    "KILN_STOP_TIMEOUT": "5",
    "KILN_GPU_SAMPLE_INTERVAL": "0.2",
    "KILN_SWAP_TIMEOUT": "30",
    "KILN_DRAIN_TIMEOUT": "3",
})
(_root / "models").mkdir(parents=True, exist_ok=True)
Path(os.environ["KILN_LLAMA_SERVER"]).chmod(0o755)
ROOT = _root

from kiln import db  # noqa: E402  (after the environment is set)

db.init()
