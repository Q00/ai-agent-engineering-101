"""The new layers of the harness (design/ideal-harness.md section 20): record, judgement, knowledge, wisdom,
meta-harness and operations. The trading side (seller, buyer, restock, review) keeps its own code and meets these
layers only at the touchpoints T1-T5 (design 20-2).

Everything here reads the live data under paths.memory() and writes only under out_dir() (memory/harness/ unless
HARNESS_OUT says otherwise), so it can run beside the live harness before the move.
"""
import os
from pathlib import Path

from core import paths


def out_dir() -> Path:
    d = Path(os.environ.get("HARNESS_OUT") or paths.memory() / "harness")
    d.mkdir(parents=True, exist_ok=True)
    return d
