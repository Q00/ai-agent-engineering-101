"""L1 of the memory (HARNESS.md 2.4): the raw events, filed by kind and day, kept up to date from a cursor.

The raw files (memory/raw/<source>/*.jsonl, L0) hold every event and are dominated by tool.call/tool.result
and model.call lines. The evaluation tools want a few kinds over a time slice. update() reads only the lines
added since the last update (core.events.EventReader, byte cursor) and appends each event to
memory/index/<kind>/<YYYYMMDD>.jsonl; events() then opens only the kinds and days asked for.

- One process updates at a time (an OS lock on memory/index/.lock); a process that finds it taken waits up
  to LOCK_WAIT seconds, then reads what is there.
- At-least-once: lines are filed before the cursor is saved, so a crash in between files them twice;
  events() drops repeated event_ids.
- The index can be deleted at any time: the next update() rebuilds it from the raw files.
"""
import json
import time

from core import atomic, paths
from core.events import EventReader
from core.kinds import SOURCES

LOCK_WAIT = 5.0


def root():
    return paths.memory() / "index"


def _day(ts):
    return time.strftime("%Y%m%d", time.localtime(ts))


def update():
    """File the events added since the last update. Returns how many were filed (0 when another process holds it)."""
    root().mkdir(parents=True, exist_ok=True)
    lock = open(root() / ".lock", "a+b")
    t0 = time.time()
    try:
        while True:
            try:
                atomic._lock(lock)
                break
            except OSError:
                if time.time() - t0 > LOCK_WAIT:
                    return 0
                time.sleep(0.05)
        reader, batch, n = EventReader("index", SOURCES), {}, 0
        for ev in reader.iter_new():
            batch.setdefault(root() / ev["kind"] / f"{_day(ev['ts'])}.jsonl", []).append(json.dumps(ev, ensure_ascii=False))
            n += 1
        for path, lines in batch.items():               # one write per file: only this process writes the index
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        if n:
            reader.commit()
        return n
    finally:
        try:
            atomic._unlock(lock)
        except OSError:
            pass
        lock.close()


def events(kinds=None, since=None, until=None, epoch=None):
    """Events of the given kinds in a slice, oldest first, from the index (updated first)."""
    update()
    if kinds is None:
        kinds = [d.name for d in root().iterdir() if d.is_dir()] if root().exists() else []
    lo = _day(since) if since is not None else None
    hi = _day(until) if until is not None else None
    out, seen = [], set()
    for k in kinds:
        d = root() / k
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.jsonl")):
            if (lo and f.stem < lo) or (hi and f.stem > hi):
                continue
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e["event_id"] in seen:
                        continue
                    if since is not None and e["ts"] < since or until is not None and e["ts"] >= until:
                        continue
                    if epoch is not None and e.get("epoch") != epoch:
                        continue
                    seen.add(e["event_id"])
                    out.append(e)
    return sorted(out, key=lambda e: e["ts"])
