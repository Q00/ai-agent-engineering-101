"""Ledger and catalog reader for the shadow memory layers (memory.run). Read only, no network.

    rows, off = read_new(<R3_STATE>/events.jsonl, off)   # [(end offset of the line, event)], new byte offset
    cat = load_catalog(<market dir>)                      # catalog.json: sku -> {category, name, list_price, ...}
    evs = events_by_seq(<R3_STATE>/events.jsonl, seqs)    # {seq: event} for proposal evidence checks

events.jsonl rows are ledger.py events {seq, ts, type, src, key, role, mine, data}. A half-written last line is
left for the next call (hooks/forum.py read_new_settles); a line that is not JSON is skipped.
"""

import json
from pathlib import Path


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_new(events, offset, limit=None):
    """Events appended after byte `offset` -> ([(end offset, event)], new offset). At most `limit` events."""
    rows = []
    try:
        with open(events, "rb") as f:
            f.seek(offset)
            for line in f:
                if not line.endswith(b"\n") or (
                    limit is not None and len(rows) >= limit
                ):
                    break
                offset += len(line)
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict):
                    rows.append((offset, e))
    except OSError:
        pass
    return rows, offset


def load_catalog(md):
    """<market dir>/catalog.json as a mapping sku -> info (the kit writes it once at start); {} if missing."""
    c = read_json(Path(md) / "catalog.json", {})
    return c if isinstance(c, dict) else {}


def events_by_seq(events, seqs):
    """{seq: event} for the wanted seqs (one pass over the file; used only for rare proposal checks)."""
    want, out = set(seqs), {}
    try:
        with open(events, "rb") as f:
            for line in f:
                if len(out) == len(want) or not line.endswith(b"\n"):
                    break
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict) and e.get("seq") in want:
                    out[e["seq"]] = e
    except OSError:
        pass
    return out
