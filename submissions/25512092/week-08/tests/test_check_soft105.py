"""tools/check_soft105.py board readings (person 11/11 ~16:40): the 16:35 report showed deals(board) / saved / ALL as
None (the nearest board_hist snapshot was 21 min old, the limit was 20 min). Now: "now" from the live board, the past
points from the nearest snapshot within 45 min, a failed reading shown as "읽기 실패", and the report sent again with
the next snapshot. Through send_with_retry (what main() calls), a temp db, a fake board and a fake send.
    python tests/test_check_soft105.py"""
import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
import check_soft105 as C  # noqa: E402
import board_read as BR  # noqa: E402

T0 = 1_800_000_000


def db_with(rows):
    p = os.path.join(tempfile.mkdtemp(), "m.db")
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE board_hist (ts REAL PRIMARY KEY, shop TEXT, buyer TEXT)")
    for ts, deals, saved, al, rank in rows:
        db.execute("INSERT INTO board_hist VALUES (?,?,?)", (ts, "{}", json.dumps(
            {"name": "choo-chu", "deals": deals, "saved": saved, "all": al, "rank": rank})))
    db.commit()
    db.close()
    return p


def board_ok():
    return {"buyers": [{"name": "x", "all": 0.9, "saved": 0.5, "deals": 1},
                       {"name": "choo-chu", "all": 0.866, "saved": 0.4135, "deals": 4180},
                       {"name": "y", "all": 0.8, "saved": 0.3, "deals": 2}]}


def board_down():
    raise OSError("board down")


def sn_log():
    p = os.path.join(tempfile.mkdtemp(), "sn.jsonl")
    open(p, "w").close()
    return p


S = {"t0": T0, "all_line": "buyer ALL 4위/65"}
NOW = T0 + 3 * 3600
# snapshots 25 min off the 3 points (missed by the old 20 min limit)
ROWS = [(T0 - 3 * 3600 + 1500, 3900, 0.415, 0.86, 5), (T0 - 1500, 4000, 0.4146, 0.865, 4)]


def test_live_board_now():
    sent = []
    out = C.send_with_retry(S, lambda lv, k, t: sent.append((k, t)), now=NOW, sleep=lambda s: None,
                            db_path=db_with(ROWS), board=board_ok, path=sn_log(), clock=lambda: NOW)
    t = out[0]
    assert len(sent) == 1 and "None" not in t and BR.FAIL not in t, t
    assert "-> 0.4135" in t and "(4위) -> 0.866 (2위)" in t
    # deals per hour over the real snapshot / reading times: (4180-4000)/(3h+25min) and (4000-3900)/(3h-50min)
    assert f"{180 / ((3 * 3600 + 1500) / 3600):.1f} vs {100 / ((3 * 3600 - 3000) / 3600):.1f}" in t


def test_failed_then_next_snapshot():
    sent = []
    p = db_with(ROWS)
    slept = []

    def sleep(s):                                    # the kit writes the next snapshot while we wait
        slept.append(s)
        db = sqlite3.connect(p)
        db.execute("INSERT INTO board_hist VALUES (?,?,?)", (NOW + 600, "{}", json.dumps(
            {"name": "choo-chu", "deals": 4190, "saved": 0.4134, "all": 0.866, "rank": 3})))
        db.commit()
        db.close()

    calls = {"n": 0}

    def board():                                     # down at the report, up again at the retry
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("down")
        return board_ok()

    out = C.send_with_retry(S, lambda lv, k, t: sent.append((k, t)), now=NOW, sleep=sleep, db_path=p,
                            board=board, path=sn_log())
    first, second = sent[0][1], sent[1][1]
    assert "None" not in first and f"saved 0.4146 -> {BR.FAIL}" in first and "다음 스냅샷 값으로 다시 시도" in first
    assert "시간당 deals(보드) " + BR.FAIL in first
    assert sent[1][0].endswith(":retry1") and "다시 읽음 1" in second and BR.FAIL not in second, second
    assert "-> 0.4134" in second and "(3위)" in second and slept and len(out) == 2


def test_gives_up_without_snapshot():
    sent = []
    C_WAIT = BR.WAIT_S
    BR.WAIT_S = 0
    try:
        C.send_with_retry(S, lambda lv, k, t: sent.append((k, t)), now=NOW, sleep=lambda s: None,
                          db_path=db_with(ROWS), board=board_down, path=sn_log())
    finally:
        BR.WAIT_S = C_WAIT
    assert len(sent) == 2 and "다시 읽기 중단" in sent[1][1]


def test_old_limit_missed():
    p = db_with(ROWS)
    assert BR.at(T0, db_path=p).get("b", "deals") == 4000         # 25 min off: found now
    assert not BR.at(T0, tol=1200, db_path=p).ok   # the old 20 min limit: None


if __name__ == "__main__":
    test_live_board_now()
    test_failed_then_next_snapshot()
    test_gives_up_without_snapshot()
    test_old_limit_missed()
    print("ok")
