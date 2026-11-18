"""DB lock safety (person 11/12 ~15:15, after the 15:05 "database is locked"), through the operating paths on a temp
market.db: market_db.record_cash and ceo.tick (strategy.tick, the restock hook) while another connection holds the
write lock for ~1.3 s: with db_safe.json on (busy 0.2 s here) they retry and succeed; dbutil.retry gives up after 3
retries and never retries other errors; WAL: a long open read no longer blocks a writer's commit (the 15:05 case:
an analyst read of ~57 s), and a read-only URI connection still reads after every connection closed;
market_db.board_snapshot makes its HTTP call outside a write transaction while on. Live files untouched.
    python tests/test_db_safe.py"""
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy, tmp_json  # noqa: E402

policy()
import dbutil  # noqa: E402
import market_db  # noqa: E402
from depts import ceo  # noqa: E402

ON = tmp_json({"enabled": True}, "db_safe")
OFF = tmp_json({}, "db_safe")


def temp_db():
    p = os.path.join(tempfile.mkdtemp(), "market.db")
    with mock.patch.object(market_db, "DB_PATH", p), mock.patch.object(dbutil, "RULES", OFF):
        market_db.connect().close()                       # the schema
    return p


def hold_write(p, secs):
    """Another process's write transaction for `secs` (BEGIN IMMEDIATE), in a thread; returns once it holds it."""
    ready = threading.Event()

    def run():
        d = sqlite3.connect(p, timeout=5)
        d.execute("BEGIN IMMEDIATE")
        d.execute("INSERT OR REPLACE INTO meta VALUES ('holder', '1')")
        ready.set()
        time.sleep(secs)
        d.commit()
        d.close()
    t = threading.Thread(target=run)
    t.start()
    ready.wait()
    return t


def inv():
    return {"balance": 55_000_000, "storage_per_day": 0.01,
            "units": [{"unit": "u1", "sku": "S", "cost": 100_000, "grade": "A"}]}


def test_retry_unit():
    calls = []

    def f():
        calls.append(1)
        if len(calls) < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"
    assert dbutil.retry(f, sleep=lambda s: None) == "ok" and len(calls) == 3
    calls.clear()
    try:
        dbutil.retry(lambda: calls.append(1) or (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")),
                     sleep=lambda s: None)
        raise AssertionError("no error")
    except sqlite3.OperationalError:
        assert len(calls) == 4                              # 1 + 3 retries
    calls.clear()
    try:
        dbutil.retry(lambda: calls.append(1) or (_ for _ in ()).throw(sqlite3.OperationalError("no such table: x")),
                     sleep=lambda s: None)
    except sqlite3.OperationalError:
        assert len(calls) == 1                              # not a lock error: no retry
    with mock.patch.object(dbutil, "RULES", OFF):
        assert not isinstance(dbutil.connect(":memory:"), dbutil.RetryConnection)


def test_record_cash_and_ceo_tick_under_lock():
    p = temp_db()
    with mock.patch.object(market_db, "DB_PATH", p), mock.patch.object(dbutil, "RULES", ON), \
            mock.patch.object(dbutil, "BUSY_S", 0.2), mock.patch.object(dbutil, "_cache", {"mtime": None, "on": False}):
        t = hold_write(p, 1.3)                               # 0.2 s busy + 0.5 + 1 s waits: the 3rd try gets in
        row = market_db.record_cash(inv(), log=lambda m: None)
        t.join()
        assert row and row[1] == 55_000_000
        t = hold_write(p, 1.3)
        with mock.patch.object(ceo, "decide", lambda inv, now, st: (("normal", "ok"), ("normal", "ok"))), \
                mock.patch.object(ceo, "adjust", lambda log: None), \
                mock.patch.object(ceo, "connect", market_db.connect):
            ceo.tick(inv(), log=lambda m: None)
        t.join()
        d = sqlite3.connect(p)
        assert d.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 1
        d.close()
    q = temp_db()                                            # off: the old 30 s connect, no retry class
    with mock.patch.object(market_db, "DB_PATH", q), mock.patch.object(dbutil, "RULES", OFF), \
            mock.patch.object(dbutil, "_cache", {"mtime": None, "on": False}):
        assert not isinstance(market_db.connect(), dbutil.RetryConnection)


def test_wal_reader_does_not_block_writer():
    p = temp_db()
    assert dbutil.wal(p, sleep=lambda s: None) == "wal"
    r = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    r.execute("BEGIN")
    r.execute("SELECT COUNT(*) FROM settles").fetchone()     # a long read stays open
    with mock.patch.object(market_db, "DB_PATH", p), mock.patch.object(dbutil, "RULES", ON), \
            mock.patch.object(dbutil, "BUSY_S", 0.2), mock.patch.object(dbutil, "_cache", {"mtime": None, "on": False}):
        t0 = time.time()
        assert market_db.record_cash(inv(), log=lambda m: None)
        assert time.time() - t0 < 1.0                        # no wait for the reader
    r.rollback()
    r.close()
    ro = sqlite3.connect(f"file:{p}?mode=ro", uri=True)       # all closed: a read-only connection still reads
    assert ro.execute("SELECT COUNT(*) FROM cash").fetchone()[0] == 1
    assert ro.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    ro.close()


def test_board_snapshot_outside_write():
    p = temp_db()
    seen = []

    class R:
        def json(self):
            return {"shops": [{"name": "choo-chu", "all": 0.5}], "buyers": [{"name": "choo-chu", "all": 0.8, "saved": 1}]}

    def get(*a, **k):
        seen.append(d.in_transaction)
        return R()
    with mock.patch.object(market_db, "DB_PATH", p), mock.patch.object(dbutil, "_cache", {"mtime": None, "on": False}), \
            mock.patch.object(market_db.httpx, "get", get):
        for rules, want in ((ON, False), (OFF, True)):
            with mock.patch.object(dbutil, "RULES", rules):
                d = market_db.connect()
                market_db.board_snapshot(d, time.time() + (0 if want is False else 4000))
                d.commit()
                d.close()
        assert seen == [False, True], seen                  # on: no write open during the call; off: as before
    d = sqlite3.connect(p)
    assert d.execute("SELECT COUNT(*) FROM board_hist").fetchone()[0] == 2
    d.close()


if __name__ == "__main__":
    test_retry_unit()
    test_record_cash_and_ceo_tick_under_lock()
    test_wal_reader_does_not_block_writer()
    test_board_snapshot_outside_write()
    print("ok")
