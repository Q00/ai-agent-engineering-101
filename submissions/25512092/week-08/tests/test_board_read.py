"""tools/board_read.py, the one board reading for every report (person 11/11 ~17:00): "now" = the live board, else the
nearest board_hist snapshot, else "읽기 실패" and the report sent again with the next snapshot. Run through each
report's real sending path (check_window / check_soft105 / check_1111 send, morning_0900 build, board_speed.mine_at,
shop_order_check._mine) with a temp db, a fake board and a fake send; the report parts that read live logs are faked.
    python tests/test_board_read.py"""
import contextlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
import board_read as BR  # noqa: E402

NOW = time.time()
T0 = NOW - 3 * 3600


def shop(book, rev, rank=7):
    return {"name": "choo-chu", "book": book, "revenue": rev, "all": 0.7, "rank": rank}


def buyer(deals, saved, al, rank):
    return {"name": "choo-chu", "deals": deals, "saved": saved, "all": al, "rank": rank, "saved_rank": 1}


def db_with(rows):
    p = os.path.join(tempfile.mkdtemp(), "m.db")
    d = sqlite3.connect(p)
    d.execute("CREATE TABLE board_hist (ts REAL PRIMARY KEY, shop TEXT, buyer TEXT)")
    for ts, s, b in rows:
        d.execute("INSERT INTO board_hist VALUES (?,?,?)", (ts, json.dumps(s), json.dumps(b)))
    d.commit()
    d.close()
    return p


def add(p, ts, s, b):
    d = sqlite3.connect(p)
    d.execute("INSERT OR REPLACE INTO board_hist VALUES (?,?,?)", (ts, json.dumps(s), json.dumps(b)))
    d.commit()
    d.close()


# snapshots 25 min off the past points (the old 20 min limit missed them); none near now
ROWS = [(T0 - 3 * 3600 + 1500, shop(100_000_000, 50_000_000), buyer(3900, 0.415, 0.86, 5)),
        (T0 - 1500, shop(110_000_000, 55_000_000), buyer(4000, 0.4146, 0.865, 4))]


def live():
    return {"shops": [{"name": "o", "all": 0.9, "book": 1, "revenue": 1, "stars_rank": 1},
                      dict(shop(120_000_000, 60_000_000), stars_rank=3)],
            "buyers": [{"name": "x", "all": 0.9, "saved": 0.5, "deals": 1},
                       buyer(4180, 0.4135, 0.866, 0), {"name": "y", "all": 0.8, "saved": 0.3, "deals": 2}]}


def down():
    raise OSError("board down")


def flaky():
    """Down at the first reading, up after."""
    flaky.n = getattr(flaky, "n", 0) + 1
    if flaky.n == 1:
        raise OSError("down")
    return live()


# ---- board_read itself
def test_now_live_then_snapshot_then_fail():
    p = db_with(ROWS)
    a = BR.now(live, clock=lambda: NOW, db_path=p)
    assert a.src == "보드" and a.get("b", "rank") == 2 and a.get("s", "rank") == 2 and a.get("b", "deals") == 4180
    add(p, NOW - 1300, shop(119_000_000, 59_000_000), buyer(4170, 0.4136, 0.866, 3))
    b = BR.now(down, clock=lambda: NOW, db_path=p)
    assert b.src == "스냅샷" and b.get("b", "deals") == 4170
    c = BR.now(down, clock=lambda: NOW, db_path=db_with(ROWS))
    assert not c.ok and BR.show(c.get("b", "saved")) == BR.FAIL
    assert BR.at(T0, db_path=p).get("b", "deals") == 4000          # 25 min off: found (the old 20 min limit: None)
    assert abs(BR.per_h(BR.at(T0, db_path=p), a, "b", "deals") - 180 / ((NOW - (T0 - 1500)) / 3600)) < 1e-9


# ---- check_soft105 (the 16:35 case)
def test_check_soft105_path():
    import check_soft105 as C
    sn = os.path.join(tempfile.mkdtemp(), "sn.jsonl")
    open(sn, "w").close()
    p = db_with(ROWS)
    sent = []
    flaky.n = 0
    C.send_with_retry({"t0": T0, "all_line": "-"}, lambda lv, k, t: sent.append((k, t)), now=NOW,
                      sleep=lambda s: add(p, NOW + 600, shop(1, 1), buyer(4190, 0.4134, 0.866, 3)),
                      db_path=p, board=flaky, path=sn)
    assert "None" not in sent[0][1] and f"saved 0.4146 -> {BR.FAIL}" in sent[0][1] and "다시 시도" in sent[0][1]
    assert sent[1][0] == f"check_soft105:{T0}:retry1" and BR.FAIL not in sent[1][1] and "-> 0.4134" in sent[1][1]


# ---- check_window (18:02)
def fake_window_parts(es):
    import check_window as W
    import morning_summary as ms
    from depts import deep_high
    k = {"closed": 0, "closed_units": 0, "dropped": 0, "ok_lines": 0, "high_lines": 0, "runs": 0}
    hd = {"n": 0, "cost": 0, "sold": 0, "profit": 0}
    ds = {"skus": 0, "buy_n": 0, "sell_n": 0, "profit": 0, "median_h": None, "out_h": 0.0, "buy_cost": 0}
    for obj, name, val in ((W, "kit_lines", lambda now=None: []), (W, "kit_stats", lambda *a: dict(k)),
                           (W, "high_db", lambda *a, **kw: dict(hd)), (W, "band", lambda *a: set()),
                           (deep_high, "window_stats", lambda *a, **kw: dict(ds)),
                           (deep_high, "skus_between", lambda *a: []), (ms, "stock_line", lambda *a, **kw: "재고")):
        es.enter_context(mock.patch.object(obj, name, val))
    return W


def test_check_window_path():
    p = db_with(ROWS)
    with contextlib.ExitStack() as es:
        W = fake_window_parts(es)
        sent = []
        W.send_with_retry({"t0": T0, "stock": "-"}, lambda lv, k, t: sent.append((k, t)), now=NOW,
                          sleep=lambda s: None, db_path=p, board=live, clock=lambda: NOW)
        t = sent[0][1]
        assert len(sent) == 1 and BR.FAIL not in t and "None" not in t, t
        assert f"Book {10_000_000 / ((NOW - (T0 - 1500)) / 3600):,.0f}" in t and "순위 7 -> 2" in t, t
        sent = []                                   # board down, no snapshot near now: 읽기 실패, then the next one
        W.send_with_retry({"t0": T0, "stock": "-"}, lambda lv, k, t: sent.append((k, t)), now=NOW,
                          sleep=lambda s: add(p, NOW + 900, shop(121_000_000, 61_000_000, 6), buyer(1, 1, 1, 1)),
                          db_path=p, board=down, clock=lambda: NOW)
        assert f"시간당 Book {BR.FAIL}" in sent[0][1] and "지금 -> 다음 스냅샷 값으로 다시 시도" in sent[0][1], sent[0][1]
        assert sent[1][0] == f"check_window:{T0}:retry1" and BR.FAIL not in sent[1][1] and "순위 7 -> 6" in sent[1][1]
        sent = []                                   # no next snapshot at all: stops, says so
        with mock.patch.object(BR, "WAIT_S", 0):
            W.send_with_retry({"t0": T0, "stock": "-"}, lambda lv, k, t: sent.append((k, t)), now=NOW + 1000,
                              sleep=lambda s: None, db_path=db_with(ROWS), board=down, clock=lambda: NOW + 1000)
        assert len(sent) == 2 and "다시 읽기 중단" in sent[1][1]


# ---- check_1111
def test_check_1111_report():
    import check_1111 as C
    p = db_with(ROWS)
    with contextlib.ExitStack() as es:
        fake_window_parts(es)
        es.enter_context(mock.patch.object(C, "features", lambda: "-"))
        t, bad = C.report({"t0": T0, "stock": "-"}, now=NOW, board=down, db_path=p, clock=lambda: NOW)
        assert bad == ["지금"] and f"saved 0.4146 -> {BR.FAIL}" in t and "None" not in t, t
        t, bad = C.report({"t0": T0, "stock": "-"}, now=NOW, board=live, db_path=p, clock=lambda: NOW)
        assert not bad and "-> 0.4135" in t and "(2위)" in t, t


# ---- morning_0900 board lines
def test_morning_0900_board_lines():
    import morning_0900 as M
    p = db_with(ROWS)
    with mock.patch.object(M, "T0", T0 - 1500):
        L = M.board_lines(NOW, board=live, db_path=p)
        assert BR.FAIL not in "\n".join(L) and "2위 차이 -0.0865" in L[0] and "[보드]" in L[0], L
        bad = []
        L = M.board_lines(NOW, board=down, db_path=p, bad=bad)
        assert "지금" in bad and f"Book/h {BR.FAIL}" in "\n".join(L), L


# ---- the older reports' helpers
def test_helpers_use_board_read():
    import board_speed as bs
    import shop_order_check as so
    p = db_with(ROWS)
    with mock.patch.object(BR, "DB", p):
        assert bs.mine_at(T0)[1]["deals"] == 4000                           # 25 min off: found
        assert so._mine(T0 - 3 * 3600)["deals"] == 3900
        with mock.patch.object(BR, "board", live):
            assert bs.mine_at(time.time())[1]["deals"] == 4180              # now = the live board
        with mock.patch.object(BR, "board", down):
            assert bs.mine_at(time.time()) == (None, None)                  # no snapshot near now: failed


if __name__ == "__main__":
    test_now_live_then_snapshot_then_fail()
    test_check_soft105_path()
    test_check_window_path()
    test_check_1111_report()
    test_morning_0900_board_lines()
    test_helpers_use_board_read()
    print("ok")
