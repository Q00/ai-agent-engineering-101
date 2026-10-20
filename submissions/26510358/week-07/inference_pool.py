"""Bounded process-safe inference; reserve one lane for the local buyer.

Kernel locks are released on process death. A burst of remote shop visitors
cannot occupy the buyer lane. This controls execution, never trade decisions.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import time

from inference_queue import QueueTimeout, inference_gap_seconds


def pool_directory(directory=None):
    root = Path(directory or os.environ.get("A2A_INFERENCE_POOL_DIR",
        str(Path(__file__).resolve().parent / ".market" / "inference-pool")))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def defer_pool(seconds, *, directory=None):
    """Share provider throttling across roles instead of issuing more 429s."""
    path = pool_directory(directory) / "cooldown"
    with path.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        raw = stream.read().strip()
        until = max(float(raw) if raw else 0, time.monotonic() + seconds)
        stream.seek(0)
        stream.truncate()
        stream.write(str(until))
        stream.flush()


def cooldown_remaining(directory):
    path = directory / "cooldown"
    if not path.exists():
        return 0
    with path.open() as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        raw = stream.read().strip()
        remaining = max(0, (float(raw) if raw else 0) - time.monotonic())
        # Our backoff is at most 60 s. A timestamp far ahead can survive a
        # host reboot with a reset monotonic clock; it is not a live cooldown.
        return remaining if remaining <= 65 else 0


@contextmanager
def pool_slot(role, deadline=None, *, directory=None, capacity=None):
    capacity = int(capacity or os.environ.get("A2A_INFERENCE_CAPACITY", "3"))
    if not 2 <= capacity <= 8:
        raise ValueError("A2A_INFERENCE_CAPACITY must be between 2 and 8")
    # Validate old pacing overrides rather than silently accepting invalid values.
    if "A2A_INFERENCE_GAP_SECONDS" in os.environ:
        inference_gap_seconds()
    directory = pool_directory(directory)
    lanes = range(capacity - 1) if role == "seller" else [capacity - 1]
    handles = [os.open(directory / f"lane-{n}.lock", os.O_RDWR | os.O_CREAT, 0o600) for n in lanes]
    acquired = None
    try:
        while acquired is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise QueueTimeout("Inference deadline reached waiting for a pool lane")
            if cooldown_remaining(directory):
                time.sleep(0.025)
                continue
            for handle in handles:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = handle
                    break
                except BlockingIOError:
                    pass
            if acquired is None:
                time.sleep(min(0.025, max(0, deadline - time.monotonic())) if deadline else 0.025)
        yield
    finally:
        if acquired is not None:
            fcntl.flock(acquired, fcntl.LOCK_UN)
        for handle in handles:
            os.close(handle)
