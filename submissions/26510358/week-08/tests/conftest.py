"""Keep every test away from live runtime state (.market/r3: the decision-call budget, the memory DB).
On 2026-11-03 test runs filled the live hourly budget and blocked the running agent's decision model."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _isolate_runtime_state(tmp_path, monkeypatch):
    from r3lib import config
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "_state")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "_state" / "memory.sqlite")
