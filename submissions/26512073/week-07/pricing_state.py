"""Persistent asking prices and review checkpoints for the student's strategy."""
import json
from pathlib import Path
import tempfile
from threading import RLock
import os

DATA_DIR = Path(
    os.environ.get(
        "SELLER_DATA_DIR",
        str(Path(__file__).resolve().parent),
    )
).resolve()

STATE_PATH = DATA_DIR / "pricing_state.json"
POLICY = "adaptive-15-percent-six-results-v1"
_lock = RLock()


def _validate(state, reserve):
    if (type(state.get("ask")) is not int or state["ask"] < reserve
            or state.get("policy") != POLICY):
        raise ValueError("Invalid saved asking price or pricing policy")
    checkpoint = state.get("last_reviewed_task")
    if checkpoint is not None and (not isinstance(checkpoint, str) or not checkpoint):
        raise ValueError("Review checkpoint must be a task ID or null")


def load_item_state(item, reserve, path=None):
    """Missing state starts at 15% above reserve; corrupt state is not reset."""
    path = Path(path) if path is not None else STATE_PATH
    with _lock:
        states = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        state = states.get(item, {
            "ask": (reserve * 115 + 50) // 100,
            "last_reviewed_task": None,
            "policy": POLICY,
        })
        _validate(state, reserve)
        return dict(state)


def save_item_state(item, reserve, state, path=None):
    """Replace the state file atomically, preserving the other items' state.

    This lock protects file operations in one seller process. The later pricing
    integration must also serialize each item's load/decide/save transaction.
    """
    _validate(state, reserve)
    path = Path(path) if path is not None else STATE_PATH
    with _lock:
        states = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        states[item] = dict(state)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="pricing-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(states, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
