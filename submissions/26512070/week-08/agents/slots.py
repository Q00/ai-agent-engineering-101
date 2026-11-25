"""The call manager (HARNESS.md 7): at most SLOTS Claude Code calls at once across every process, L1 first.

The seller, market_buyer, restock, review, forum and the outer runtime are separate processes on one Claude
account. A slot is an OS lock on memory/bus/slots/slot-<i>.lock, held for the length of one call; a process
that dies drops its lock with it, so no slot is lost. While waiting, a caller leaves a ticket in
memory/bus/slots/wait/; a free slot goes to the caller with the best priority (lowest number), then the
oldest ticket. A ticket older than STALE_TICKET seconds is ignored (its process is gone).

    with slots.slot("seller", max_wait=20):      # raises slots.Busy after max_wait seconds
        ...call claude...

A seat that waits too long gets Busy: L1 seats then use their OpenRouter fallback (agents.model), the others
wait for the next turn.
"""
import contextlib
import os
import time

from core import atomic, paths

SLOTS = int(os.environ.get("HARNESS_SLOTS", "3"))
PRIORITY = {"seller": 0, "buyer": 1, "restock": 2, "strategy": 3, "analyst": 4, "forum": 5, "review": 5, "audit": 6}
MAX_WAIT = {"seller": 20, "buyer": 40, "restock": 60, "review": 60, "forum": 120, "analyst": 600, "strategy": 600}
STALE_TICKET = 900
POLL = 0.2


class Busy(Exception):
    pass


def _dir():
    d = paths.bus() / "slots"
    (d / "wait").mkdir(parents=True, exist_ok=True)
    return d


def _tickets():
    now, out = time.time(), []
    for f in (_dir() / "wait").glob("*.t"):
        try:
            prio, ts, pid, seq = f.stem.split("_")
            if now - float(ts) < STALE_TICKET:
                out.append((int(prio), float(ts), f.name))
        except (ValueError, OSError):
            continue
    return sorted(out)


def _try_lock(i):
    f = open(_dir() / f"slot-{i}.lock", "a+b")
    try:
        atomic._lock(f)
        return f
    except OSError:
        f.close()
        return None


_seq = 0


@contextlib.contextmanager
def slot(seat, max_wait=None):
    global _seq
    _seq += 1
    prio = PRIORITY.get(seat, 9)
    max_wait = MAX_WAIT.get(seat, 120) if max_wait is None else max_wait
    t0 = time.time()
    ticket = _dir() / "wait" / f"{prio}_{t0:.3f}_{os.getpid()}_{_seq}.t"
    ticket.write_text(seat)
    held = None
    try:
        while held is None:
            ahead = [t for t in _tickets() if t[2] != ticket.name and (t[0], t[1]) < (prio, t0)]
            got = [f for f in (_try_lock(i) for i in range(SLOTS)) if f]
            if len(got) > len(ahead):       # a free slot is left after one for each caller ahead of us
                held = got.pop(0)
            for f in got:
                atomic._unlock(f)
                f.close()
            if held is None:
                if time.time() - t0 > max_wait:
                    raise Busy(f"{seat}: no free model slot in {max_wait} s ({len(ahead)} ahead)")
                time.sleep(POLL)
        ticket.unlink(missing_ok=True)
        yield time.time() - t0
    finally:
        ticket.unlink(missing_ok=True)
        if held:
            try:
                atomic._unlock(held)
            finally:
                held.close()
