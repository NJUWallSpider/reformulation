"""Optional research models are never bundled with the distribution."""
import os
from pathlib import Path

import pytest


def model_path(name):
    root = Path(os.environ.get("MIPFRAME_MODEL_DATA_ROOT", Path(__file__).resolve().parents[1] / "data"))
    path = root / name
    if not path.is_file():
        pytest.skip(f"External model {name} unavailable; set MIPFRAME_MODEL_DATA_ROOT")
    return path
