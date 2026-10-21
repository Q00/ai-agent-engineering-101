"""Bounded process-safe inference; reserve one lane for the local buyer.

Kernel locks are released on process death. A burst of remote shop visitors
cannot occupy the buyer lane. This controls execution, never trade decisions.
"""
from contextlib import contextmanager, nullcontext
import fcntl
import json
import math
import os
from pathlib import Path
import time

from inference_queue import InferenceQueue, QueueTimeout, inference_gap_seconds


def pool_directory(directory=None):
    root = Path(directory or os.environ.get("A2A_INFERENCE_POOL_DIR",
        str(Path(__file__).resolve().parent / ".market" / "inference-pool")))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def defer_pool(seconds, *, directory=None):
    """Share provider throttling across roles instead of issuing more 429s."""
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Provider cooldown must be finite and nonnegative")
    path = pool_directory(directory) / "cooldown"
    with path.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        raw = stream.read().strip()
        # Wall time permits long Retry-After values and survives host restarts.
        # The legacy monotonic-only format is read until every process upgrades.
        wall_path = path.with_name("cooldown-wall")
        previous = float(wall_path.read_text()) if wall_path.exists() else 0
        until = max(previous, time.time() + seconds)
        wall_path.write_text(str(until))
        path.with_name("throttled-at").write_text(str(time.time()))
        stream.seek(0)
        stream.truncate()
        # Old generations may still be finishing quotes during a live reload.
        stream.write(str(max(_remaining(raw) + time.monotonic(),
                             time.monotonic() + min(seconds, 60))))
        stream.flush()


def _remaining(raw):
    if not raw:
        return 0
    value = json.loads(raw)
    if isinstance(value, dict):
        return max(0, value["until_unix"] - time.time())
    remaining = max(0, float(value) - time.monotonic())
    return remaining if remaining <= 65 else 0


def cooldown_remaining(directory):
    path = directory / "cooldown"
    if not path.exists():
        return 0
    with path.open() as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        raw = stream.read().strip()
        wall_path = path.with_name("cooldown-wall")
        wall_remaining = max(0, float(wall_path.read_text()) - time.time()) if wall_path.exists() else 0
        return max(_remaining(raw), wall_remaining)


def recovering(directory):
    path = directory / "throttled-at"
    if not path.exists():
        return False
    with (directory / "cooldown").open() as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        return 0 <= time.time() - float(path.read_text()) < 60


@contextmanager
def recovery_slot(directory, deadline):
    """After a 429, probe serially in FIFO order instead of releasing a burst."""
    gate = (InferenceQueue(directory / "recovery-queue.json", interval=1).slot(deadline)
            if recovering(directory) else nullcontext())
    with gate:
        while cooldown_remaining(directory):
            if deadline is not None and time.monotonic() >= deadline:
                raise QueueTimeout("Inference deadline reached during provider recovery")
            time.sleep(0.025)
        yield


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
                    if cooldown_remaining(directory):
                        fcntl.flock(handle, fcntl.LOCK_UN)
                        break
                    acquired = handle
                    break
                except BlockingIOError:
                    pass
            if acquired is None:
                time.sleep(min(0.025, max(0, deadline - time.monotonic())) if deadline else 0.025)
        with recovery_slot(directory, deadline):
            yield
    finally:
        if acquired is not None:
            fcntl.flock(acquired, fcntl.LOCK_UN)
        for handle in handles:
            os.close(handle)
