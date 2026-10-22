"""Process-safe seller admission: finish valid orders before starting new quotes.

The caller supplies trusted local priority. This scheduler only chooses who may
try a kernel-locked lane; it never makes a price, eligibility or trade decision.
Two consecutive completions yield to the oldest normal waiter. After fifteen
seconds, the oldest normal waiter also gets preference, with at most one such
normal admission in succession while completion waits. These are guarantees
about admission opportunities, not maximum latency. Running work is never
preempted, and time limits apply to waiting rather than lane ownership.
"""
from contextlib import contextmanager
import fcntl
import math
import os
import threading
import time
import uuid

from inference_queue import InferenceQueue, QueueStateError, QueueTimeout, _process_identity


COMPLETION_BURST_LIMIT = 2
NORMAL_MAX_WAIT_SECONDS = 15.0


def validate_priority(priority):
    if priority not in ("normal", "completion"):
        raise ValueError("Inference priority must be normal or completion")
    return priority


class SellerAdmission(InferenceQueue):
    """FIFO within each priority, bounded preference across seller processes."""

    def __init__(self, directory, *, max_wait_seconds=NORMAL_MAX_WAIT_SECONDS):
        if (isinstance(max_wait_seconds, bool) or not isinstance(max_wait_seconds, (int, float))
                or not math.isfinite(max_wait_seconds) or max_wait_seconds < 0):
            raise ValueError("Normal admission aging must be finite and nonnegative")
        super().__init__(directory / "seller-admission.json", interval=0, poll_interval=0.025)
        self.max_wait_seconds = max_wait_seconds

    def _load(self):
        state = super()._load()
        burst = state.setdefault("completion_burst", 0)
        completion_turn = state.setdefault("yield_to_completion", False)
        if (isinstance(burst, bool) or not isinstance(burst, int)
                or not 0 <= burst <= COMPLETION_BURST_LIMIT or state["owner"] is not None
                or not isinstance(completion_turn, bool)):
            raise QueueStateError("Invalid seller admission state")
        for entry in state["queue"]:
            if entry.get("priority") not in ("normal", "completion"):
                raise QueueStateError("Invalid seller admission priority")
            enqueued = entry.get("enqueued_at")
            if (isinstance(enqueued, bool) or not isinstance(enqueued, (int, float))
                    or not math.isfinite(enqueued)):
                raise QueueStateError("Invalid seller admission timestamp")
        return state

    def _next(self, state, now):
        normal = next((entry for entry in state["queue"] if entry["priority"] == "normal"), None)
        completion = next((entry for entry in state["queue"] if entry["priority"] == "completion"), None)
        if normal is not None and (completion is None
                or state["completion_burst"] >= COMPLETION_BURST_LIMIT
                or (now - normal["enqueued_at"] >= self.max_wait_seconds
                    and not state["yield_to_completion"])):
            return normal
        return completion

    @contextmanager
    def lane(self, handles, deadline=None, *, priority="normal", ready=lambda: True):
        """Own one supplied kernel lane; withdraw dead, expired or cancelled waits."""
        validate_priority(priority)
        if deadline is not None and (isinstance(deadline, bool) or not isinstance(deadline, (int, float))
                                     or not math.isfinite(deadline)):
            raise ValueError("deadline must be a finite monotonic timestamp")
        process_start, _ = _process_identity(os.getpid())
        ticket_id = uuid.uuid4().hex
        ticket = {"ticket": ticket_id, "pid": os.getpid(), "process_start": process_start,
                  "thread_id": threading.get_ident(), "deadline": deadline,
                  "enqueued_at": time.monotonic(), "priority": priority}
        enqueued, acquired = False, None
        try:
            with self._control(deadline, stage="seller registration"):
                state = self._load()
                self._prune(state, time.monotonic())
                state["queue"].append(ticket)
                self._save(state)
                enqueued = True
                self._trace("seller_enqueued", ticket_id, state, priority=priority)
            while acquired is None:
                with self._control(deadline, stage="seller admission"):
                    state = self._load()
                    now = time.monotonic()
                    changed = self._prune(state, now)
                    if not any(entry["ticket"] == ticket_id for entry in state["queue"]):
                        if deadline is not None and now >= deadline:
                            raise QueueTimeout("Inference deadline reached waiting for a seller lane")
                        raise QueueStateError("Live seller admission ticket disappeared")
                    chosen = self._next(state, now)
                    if chosen is not None and chosen["ticket"] == ticket_id and ready():
                        for handle in handles:
                            try:
                                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            except BlockingIOError:
                                continue
                            acquired = handle
                            # The provider may defer admission while this caller
                            # acquires the kernel lock. Always recheck its gate.
                            if not ready():
                                fcntl.flock(handle, fcntl.LOCK_UN)
                                acquired = None
                                break
                            normal_waiting = any(entry["priority"] == "normal" for entry in state["queue"])
                            completion_waiting = any(entry["priority"] == "completion" for entry in state["queue"])
                            state["completion_burst"] = (min(COMPLETION_BURST_LIMIT,
                                state["completion_burst"] + 1)
                                if priority == "completion" and normal_waiting else 0)
                            # An old quote backlog must not erase completion
                            # priority. After any normal admission while both
                            # classes wait, give completion its next opportunity.
                            state["yield_to_completion"] = priority == "normal" and completion_waiting
                            state["queue"] = [entry for entry in state["queue"] if entry["ticket"] != ticket_id]
                            changed = True
                            break
                    if changed:
                        self._save(state)
                    if acquired is not None:
                        self._trace("seller_admitted", ticket_id, state, priority=priority,
                                    waited_seconds=round(now - ticket["enqueued_at"], 6))
                if acquired is None:
                    pause = self.poll_interval
                    if deadline is not None:
                        pause = min(pause, max(0, deadline - time.monotonic()))
                    time.sleep(pause)
            yield acquired
        finally:
            # Even a failed state save/trace or BaseException must release the
            # lane. Kernel locks additionally release if this process is killed.
            if acquired is not None:
                fcntl.flock(acquired, fcntl.LOCK_UN)
            if enqueued:
                with self._control():
                    state = self._load()
                    changed = self._prune(state, time.monotonic())
                    queued = [entry for entry in state["queue"] if entry["ticket"] != ticket_id]
                    if queued != state["queue"]:
                        state["queue"] = queued
                        changed = True
                    if not queued and (state["completion_burst"] or state["yield_to_completion"]):
                        state["completion_burst"] = 0
                        state["yield_to_completion"] = False
                        changed = True
                    if changed:
                        self._save(state)
                    self._trace("seller_released" if acquired is not None else "seller_withdrawn",
                                ticket_id, state, priority=priority)
