"""FIFO admission for local inference across threads and POSIX processes.

Only the small state transaction holds flock. Inference owns a persisted ticket,
so newly arriving requests cannot overtake waiting callers. A live owner remains
exclusive until its context exits, even if its admission deadline has elapsed.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import random
import tempfile
import threading
import time
import uuid


DEFAULT_INFERENCE_GAP_SECONDS = 5.0


def inference_gap_seconds(interval=None):
    """Resolve the shared pacing policy before queue mutation or inference."""
    if interval is None:
        raw = os.environ.get("A2A_INFERENCE_GAP_SECONDS", str(DEFAULT_INFERENCE_GAP_SECONDS))
        try:
            interval = float(raw)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("A2A_INFERENCE_GAP_SECONDS must be finite and nonnegative") from exc
    if isinstance(interval, bool) or not isinstance(interval, (int, float)):
        raise ValueError("inference gap must be a finite nonnegative number")
    try:
        resolved = float(interval)
    except (ValueError, OverflowError) as exc:
        raise ValueError("inference gap must be finite and nonnegative") from exc
    if not math.isfinite(resolved) or resolved < 0:
        raise ValueError("A2A_INFERENCE_GAP_SECONDS/inference gap must be finite and nonnegative")
    return resolved


class QueueTimeout(TimeoutError):
    """A caller's deadline expired before it obtained the inference slot."""


class QueueStateError(RuntimeError):
    """Shared state is invalid; fail closed instead of granting overlapping slots."""


def _process_identity(pid):
    """Linux start ticks distinguish a reused PID; zombies cannot own a slot."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19], fields[0] not in {"Z", "X"}
    except (OSError, IndexError):
        try:
            os.kill(pid, 0)
            return None, True
        except PermissionError:
            return None, True
        except ProcessLookupError:
            return None, False


def _alive(ticket):
    identity, alive = _process_identity(ticket["pid"])
    if not alive or (identity is not None and ticket["process_start"] is not None
                     and identity != ticket["process_start"]):
        return False
    if ticket["pid"] == os.getpid():
        return any(thread.ident == ticket["thread_id"] and thread.is_alive()
                   for thread in threading.enumerate())
    return True


class InferenceQueue:
    def __init__(self, state_path=None, *, interval=DEFAULT_INFERENCE_GAP_SECONDS, poll_interval=0.05):
        for name, value in (("interval", interval), ("poll_interval", poll_interval)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        if interval < 0 or poll_interval <= 0:
            raise ValueError("interval must be nonnegative and poll_interval positive")
        self.interval, self.poll_interval = float(interval), float(poll_interval)
        self.path = Path(state_path) if state_path is not None else (
            Path(tempfile.gettempdir()) / f"a2a-inference-{os.getuid()}" / "state.json")
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        trace_path = os.environ.get("A2A_QUEUE_TRACE")
        self.trace_path = Path(trace_path) if trace_path else None
        if self.trace_path is not None:
            if self.trace_path.resolve() in (self.path.resolve(), self.lock_path.resolve()):
                raise ValueError("Queue trace must not overwrite its state or control lock")
            self.trace_path.parent.mkdir(parents=True, exist_ok=True)

    def _trace(self, event, ticket=None, state=None, **fields):
        if self.trace_path is None:
            return
        record = {"time": datetime.now(timezone.utc).isoformat(), "monotonic": time.monotonic(),
                  "event": event, "pid": os.getpid(), "thread_id": threading.get_ident(),
                  "state_path": str(self.path), "interval_seconds": self.interval, **fields}
        if ticket is not None:
            record["ticket"] = ticket
        if state is not None:
            record["queue"] = [entry["ticket"] for entry in state["queue"]]
            record["owner"] = state["owner"]["ticket"] if state["owner"] else None
        data = (json.dumps(record, allow_nan=False) + "\n").encode("utf-8")
        descriptor = os.open(self.trace_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            written = os.write(descriptor, data)
            if written != len(data):
                raise OSError("Incomplete inference queue trace append")
        finally:
            os.close(descriptor)

    @contextmanager
    def _control(self, deadline=None, *, stage="queue control"):
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "a+") as handle:
            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    raise QueueTimeout(f"Inference deadline reached during {stage}")
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    # Control transactions are brief. Short jitter avoids
                    # synchronized pollers repeatedly colliding at the same phase.
                    pause = min(self.poll_interval, 0.005) * random.uniform(0.5, 1.5)
                    if deadline is not None:
                        pause = min(pause, max(0, deadline - time.monotonic()))
                    time.sleep(pause)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _load(self):
        if not self.path.exists():
            return {"version": 1, "queue": [], "owner": None, "last_completion": None}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(state, dict) or state.get("version") != 1
                    or not isinstance(state.get("queue"), list)):
                raise QueueStateError("Invalid inference queue structure")
            last = state.get("last_completion")
            if last is not None and (isinstance(last, bool) or not isinstance(last, (int, float))
                                     or not math.isfinite(last)):
                raise QueueStateError("Invalid inference completion timestamp")
            entries = state["queue"] + ([state["owner"]] if state.get("owner") is not None else [])
            identifiers = set()
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("ticket"), str):
                    raise QueueStateError("Invalid inference ticket")
                if entry["ticket"] in identifiers:
                    raise QueueStateError("Duplicate inference ticket")
                identifiers.add(entry["ticket"])
                if (isinstance(entry.get("pid"), bool) or not isinstance(entry.get("pid"), int)
                        or entry["pid"] <= 0 or not isinstance(entry.get("thread_id"), int)
                        or "process_start" not in entry):
                    raise QueueStateError("Invalid inference ticket identity")
                deadline = entry.get("deadline")
                if deadline is not None and (isinstance(deadline, bool) or not isinstance(deadline, (int, float))
                                             or not math.isfinite(deadline)):
                    raise QueueStateError("Invalid inference ticket deadline")
            return state
        except (OSError, ValueError, TypeError) as exc:
            raise QueueStateError("Cannot read shared inference queue") from exc

    def _save(self, state):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False,
                                             dir=self.path.parent, prefix=f".{self.path.name}.") as handle:
                temporary = handle.name
                json.dump(state, handle, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)

    def _prune(self, state, now):
        changed = False
        owner = state.get("owner")
        if owner is not None and not _alive(owner):
            state["owner"] = None
            # Unknown completion time: wait one full pacing interval from recovery.
            state["last_completion"] = now
            self._trace("dead_owner_recovered", owner["ticket"], state, owner_pid=owner["pid"])
            changed = True
        queued = [entry for entry in state["queue"] if _alive(entry)
                  and (entry["deadline"] is None or entry["deadline"] > now)]
        if queued != state["queue"]:
            removed = [entry for entry in state["queue"] if entry not in queued]
            state["queue"] = queued
            for entry in removed:
                event = "expired_waiter_removed" if entry["deadline"] is not None and entry["deadline"] <= now else "dead_waiter_removed"
                self._trace(event, entry["ticket"], state, ticket_pid=entry["pid"], deadline=entry["deadline"])
            changed = True
        # A monotonic clock restarts on reboot; no old timestamp may stall a new boot.
        if state["last_completion"] is not None and state["last_completion"] > now:
            state["last_completion"] = None
            changed = True
        return changed

    @contextmanager
    def slot(self, deadline=None):
        """Yield the ticket after FIFO admission; unregister on every exit path."""
        if deadline is not None and (isinstance(deadline, bool) or not isinstance(deadline, (int, float))
                                     or not math.isfinite(deadline)):
            raise ValueError("deadline must be a finite monotonic timestamp")
        ticket_id = uuid.uuid4().hex
        process_start, _ = _process_identity(os.getpid())
        ticket = {"ticket": ticket_id, "pid": os.getpid(), "process_start": process_start,
                  "thread_id": threading.get_ident(), "deadline": deadline,
                  "enqueued_at": time.monotonic()}
        enqueued = acquired = False
        try:
            with self._control(deadline, stage="queue registration"):
                state = self._load()
                self._prune(state, time.monotonic())
                state["queue"].append(ticket)
                self._save(state)
                enqueued = True
                self._trace("enqueued", ticket_id, state, deadline=deadline)
            while not acquired:
                with self._control(deadline, stage="FIFO admission"):
                    state = self._load()
                    now = time.monotonic()
                    changed = self._prune(state, now)
                    if deadline is not None and now >= deadline:
                        if changed:
                            self._save(state)
                        raise QueueTimeout("Inference deadline reached while waiting in FIFO queue")
                    if not any(entry["ticket"] == ticket_id for entry in state["queue"]):
                        raise QueueStateError("Live inference ticket disappeared from the queue")
                    last = state["last_completion"]
                    pacing_ready = last is None or now >= last + self.interval
                    if (state["owner"] is None and state["queue"][0]["ticket"] == ticket_id
                            and pacing_ready):
                        state["owner"] = state["queue"].pop(0)
                        state["owner"]["acquired_at"] = now
                        acquired = changed = True
                    if changed:
                        self._save(state)
                    if acquired:
                        self._trace("admitted", ticket_id, state,
                                    waited_seconds=round(now - ticket["enqueued_at"], 6), deadline=deadline)
                if not acquired:
                    pause = self.poll_interval
                    if deadline is not None:
                        pause = min(pause, max(0, deadline - time.monotonic()))
                    time.sleep(pause)
            yield ticket_id
        except QueueTimeout as exc:
            self._trace("deadline_expired", ticket_id, detail=str(exc), enqueued=enqueued)
            raise
        finally:
            if enqueued:
                # Cleanup itself must succeed even after the caller's deadline.
                with self._control():
                    state = self._load()
                    changed = self._prune(state, time.monotonic())
                    owner = state["owner"]
                    if acquired and owner is not None and owner["ticket"] == ticket_id:
                        state["owner"] = None
                        state["last_completion"] = time.monotonic()
                        changed = True
                        release_event = "released"
                    else:
                        release_event = "withdrawn"
                        queued = [entry for entry in state["queue"] if entry["ticket"] != ticket_id]
                        if queued != state["queue"]:
                            state["queue"] = queued
                            changed = True
                    if changed:
                        self._save(state)
                    self._trace(release_event, ticket_id, state)

    def snapshot(self):
        """Inspect a copied queue state and recover expired/dead participants."""
        with self._control():
            state = self._load()
            if self._prune(state, time.monotonic()):
                self._save(state)
            return copy.deepcopy(state)


@contextmanager
def inference_slot(deadline=None, *, state_path=None, interval=None, poll_interval=0.05):
    gap = inference_gap_seconds(interval)
    with InferenceQueue(state_path, interval=gap, poll_interval=poll_interval).slot(deadline) as ticket:
        yield ticket
