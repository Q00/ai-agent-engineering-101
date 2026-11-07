"""Raw events: the record layer (HARNESS.md 2).

Every part of the system writes what happened as events and never edits them. Every
event carries the strategy it was made under and the keys that join it to others.

    w = EventWriter("seller", source="seller")
    w.context(strategy_version=3, epoch="e3", card_version=1)
    w.emit("deal.closed", {"price": 120}, task="t1", sku="G00001")

Files: raw/<source>/<writer>-<YYYYMMDD>.jsonl, one per writer per day. A writer name must
not run in two processes at once (the kit runs market_buyer, restock and review one at a
time); appends are locked anyway (core.atomic).

Readers keep a cursor in bus/cursors/<reader>.json: for every file, the byte offset of the
next unread line. Delivery is at least once. A reader that stops after handling an event
but before commit() sees it again, so whatever a reader writes is keyed by event_id
(HARNESS.md 2.2, S2).
"""
import itertools
import json
import os
import time
from pathlib import Path

from core import atomic, paths, schema

JOIN_KEYS = tuple(schema.KEYS["properties"])


# one sequence for the whole process (11/7: per-writer counters made equal event_ids when two writers of the same
# name - the role's events and its tool calls - wrote in the same millisecond, and index.events dropped one as a repeat)
_SEQ = itertools.count(1)


class EventWriter:
    def __init__(self, writer: str, source: str):
        self.writer, self.source = writer, source
        self.pid = os.getpid()
        self.seq = 0
        self.out = atomic.Appender()          # the day's file stays open; a new day opens the next one
        self.ctx = {"strategy_version": None, "epoch": None, "card_version": None}

    def context(self, strategy_version=None, epoch=None, card_version=None):
        """The strategy, epoch and system card in force; stamped on every event from now on."""
        self.ctx = {"strategy_version": strategy_version, "epoch": epoch, "card_version": card_version}

    def path(self, ts) -> Path:
        return paths.raw() / self.source / f"{self.writer}-{time.strftime('%Y%m%d', time.localtime(ts))}.jsonl"

    def emit(self, kind: str, data=None, **keys) -> dict:
        unknown = set(keys) - set(JOIN_KEYS)
        if unknown:
            raise ValueError(f"unknown join keys {sorted(unknown)}; use {list(JOIN_KEYS)}")
        ts = time.time()
        self.seq = next(_SEQ)
        ev = {"schema_version": schema.SCHEMA_VERSION,
              "event_id": f"{self.writer}-{self.pid}-{int(ts * 1000)}-{self.seq:06d}",
              "ts": ts, "writer": self.writer, "source": self.source, "kind": kind, **self.ctx,
              "keys": {k: v for k, v in keys.items() if v is not None}, "data": data or {}}
        errors = schema.validate(ev, schema.EVENT)
        if errors:
            raise ValueError(f"event does not fit the schema: {errors}")
        self.out.append(self.path(ts), json.dumps(ev, ensure_ascii=False, separators=(",", ":")))
        return ev

    def close(self):
        self.out.close()


class EventReader:
    """Reads new events from one or more sources, in file order, line by line."""

    def __init__(self, reader: str, sources):
        self.reader = reader
        self.sources = [sources] if isinstance(sources, str) else list(sources)
        self.cursor_path = paths.bus() / "cursors" / f"{reader}.json"
        cur = atomic.read_json(self.cursor_path, default=None)
        if cur is not None and schema.validate(cur, schema.CURSOR):
            raise ValueError(f"cursor {self.cursor_path} does not fit the schema; fix or remove it")
        self.offsets = dict((cur or {}).get("files", {}))
        self.pending = dict(self.offsets)
        self.last_event_id = (cur or {}).get("last_event_id")
        self.bad = []      # (file, offset, reason) of lines that are not valid events; skipped

    def files(self):
        for src in self.sources:
            d = paths.raw() / src
            if d.is_dir():
                yield from sorted(d.glob("*.jsonl"))

    def iter_new(self, limit=None):
        """Yield events not yet committed. Only whole lines: a line still being written waits."""
        n = 0
        for f in self.files():
            rel = f.relative_to(paths.raw()).as_posix()
            start = self.pending.get(rel, 0)
            with open(f, "rb") as fh:
                fh.seek(start)
                chunk = fh.read()
            pos = start
            for raw_line in chunk.split(b"\n")[:-1]:      # the part after the last newline is unfinished
                line_start, pos = pos, pos + len(raw_line) + 1
                try:
                    ev = json.loads(raw_line.decode("utf-8"))
                    errors = schema.validate(ev, schema.EVENT)
                except (UnicodeDecodeError, ValueError) as e:
                    errors = [repr(e)]
                if errors:
                    self.bad.append((rel, line_start, errors))
                    self.pending[rel] = pos
                    continue
                self.pending[rel] = pos
                self.last_event_id = ev["event_id"]
                yield ev
                n += 1
                if limit is not None and n >= limit:
                    return

    def commit(self):
        """Save how far this reader got. Events yielded before commit() are not delivered again."""
        self.offsets = dict(self.pending)
        atomic.write_json(self.cursor_path, {"schema_version": schema.SCHEMA_VERSION, "reader": self.reader,
                                             "files": self.offsets, "last_event_id": self.last_event_id})
