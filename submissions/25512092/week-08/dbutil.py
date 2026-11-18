"""market.db connections that wait and retry on "database is locked" (person's decision 11/12 ~15:15, after the
15:05 lock errors). Switched by db_safe.json (read on every connect; off -> exactly the old connect, timeout 30 s):
  on   busy_timeout BUSY_S (10 s), and a statement or commit that still fails with "database is locked" / "busy" is
       tried again up to len(WAITS) (3) times after WAITS seconds (0.5 / 1 / 2); the transaction stays as it was
       (SQLite keeps it on SQLITE_BUSY)
The journal mode (WAL, set once by tools/db_safe_rollout.py) is stored in the database file itself.
    connect(path, old_timeout=30, **kw)
"""
import json
import os
import sqlite3
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = os.path.join(HERE, "db_safe.json")
BUSY_S = 10
WAITS = (0.5, 1.0, 2.0)
_cache = {"mtime": None, "on": False}


def on(path=None):
    p = path or RULES
    try:
        m = os.path.getmtime(p)
    except OSError:
        return False
    if path is None and (p, m) == _cache["mtime"]:
        return _cache["on"]
    try:
        with open(p, encoding="utf-8") as f:
            v = bool(json.load(f).get("enabled"))
    except (OSError, ValueError):
        v = False
    if path is None:
        _cache.update(mtime=(p, m), on=v)
    return v


def _locked(e):
    s = str(e).lower()
    return "locked" in s or "busy" in s


def retry(fn, waits=WAITS, sleep=time.sleep):
    """fn() again after each wait while it fails with a lock error; the last error is raised."""
    for w in tuple(waits) + (None,):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if w is None or not _locked(e):
                raise
            sleep(w)


class RetryConnection(sqlite3.Connection):
    def execute(self, *a, **k):
        return retry(lambda: sqlite3.Connection.execute(self, *a, **k))

    def executemany(self, *a, **k):
        return retry(lambda: sqlite3.Connection.executemany(self, *a, **k))

    def executescript(self, *a, **k):
        return retry(lambda: sqlite3.Connection.executescript(self, *a, **k))

    def commit(self):
        return retry(lambda: sqlite3.Connection.commit(self))


def connect(path, old_timeout=30, rules=None, **kw):
    """sqlite3.connect with BUSY_S and the retries while db_safe.json is on, else the old timeout."""
    if not on(rules):
        return sqlite3.connect(path, timeout=old_timeout, **kw)
    return sqlite3.connect(path, timeout=BUSY_S, factory=RetryConnection, **kw)


def wal(path, tries=60, wait=2.0, sleep=time.sleep, log=print):
    """Switch the database to WAL (needs a moment with no other writer): the journal mode after the tries."""
    mode = None
    for i in range(tries):
        d = sqlite3.connect(path, timeout=BUSY_S)
        try:
            mode = d.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if mode == "wal":
                return mode
        except sqlite3.OperationalError as e:
            if not _locked(e):
                raise
            log(f"[db_safe] WAL switch try {i + 1}: {e}")
        finally:
            d.close()
        sleep(wait)
    return mode
