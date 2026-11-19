"""The public board, read safely (yms979 11-11).

Since the kit moves the shop between two hostnames to stay alive through market outages, the board shows our shop
for about a minute after each move with deals 0, revenue 0 and stars_rank 3.0 (the history is re-keyed to the new URL
a little later; the ledger confirms nothing is lost: +646 board deals vs 644 settles at both URLs, 12:41-19:11 UTC).
A reader that takes that snapshot sees class shop .506 and total 1.246 (20th) instead of .973 and 1.713, and code that
filters shops by `deals` no longer finds us at all. fetch() returns a board where our shop's history is intact: it
re-reads a few times, then falls back to the last good board kept in .market/board_good.json (at most MAX_AGE old).
"""
import json
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
URL = "https://market.ourolabs.ai/api/board?round=3"
GOOD = HERE / ".market" / "board_good.json"
NAME = "yms979"
MAX_AGE = 1800


def sane(b):
    """Our shop is on the board with its history (deals and revenue above zero)."""
    try:
        s = next(x for x in b["shops"] if x.get("name") == NAME)
        return (s.get("deals") or 0) > 0 and (s.get("revenue") or 0) > 0
    except (StopIteration, KeyError, TypeError):
        return False


def _get(timeout):
    with urllib.request.urlopen(URL, timeout=timeout) as r:
        return json.load(r)


def fetch(timeout=20, tries=3, wait=20):
    """The board as a dict. tries=1 never waits (per-order code): a transient board is replaced by the last good one."""
    b = None
    for i in range(max(1, tries)):
        b = _get(timeout)
        if sane(b):
            try:
                tmp = GOOD.with_suffix(".tmp")
                tmp.write_text(json.dumps({"t": time.time(), "b": b}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                tmp.replace(GOOD)
            except OSError:
                pass
            return b
        if i + 1 < tries:
            time.sleep(wait)
    try:
        c = json.loads(GOOD.read_text(encoding="utf-8"))
        if time.time() - c["t"] <= MAX_AGE:
            return c["b"]
    except (OSError, ValueError, KeyError):
        pass
    return b
