"""Where everything lives.

The root is the week-08 folder. HARNESS_ROOT overrides it, so tests and replays
can run against a temporary folder without touching the live memory.
"""
import os
from pathlib import Path


def root() -> Path:
    return Path(os.environ.get("HARNESS_ROOT") or Path(__file__).resolve().parent.parent)


def memory() -> Path:
    return root() / "memory"


def raw() -> Path:
    """Append-only source events, one file per writer per day (HARNESS.md 2)."""
    return memory() / "raw"


def bus() -> Path:
    """The only place the inner and outer runtimes talk through (HARNESS.md 2.2)."""
    return memory() / "bus"
