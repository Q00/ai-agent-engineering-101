"""tools/stall_watch.check (person 11/11 ~22:00), the function the watcher runs every 5 min, with fake kit log lines,
inventory, sales count, restart and send: no sale 30 min / cash >= 5M and 2 windows with 0 supply lines / no order 10
min -> one kit restart + urgent; still stalled 15 min later -> one more urgent only (and a decision-needed row); cleared
-> recovered; the summary line counts it. Temp files only.
    python tests/test_stall_watch.py"""
import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import stall_watch as W  # noqa: E402

NOW = datetime(2026, 11, 12, 2, 0).timestamp()


def line(t, text):
    return (t, f"{datetime.fromtimestamp(t):%H:%M:%S} {text}")


def run(s, now, lines, inv, n_sales, log, needs):
    calls = {"restart": 0, "send": []}

    def restart():
        calls["restart"] += 1
        return True
    why = W.check(s, now, lines, inv, n_sales, restart, lambda lv, k, t: calls["send"].append((lv, k, t)),
                  log_path=log, needs_path=needs)
    return why, calls


def paths():
    d = tempfile.mkdtemp()
    return os.path.join(d, "log.jsonl"), os.path.join(d, "needs.jsonl")


def test_fine():
    log, needs = paths()
    lines = [line(NOW - 60, "[order o-1] 과자")]
    why, c = run({}, NOW, lines, {}, 12, log, needs)
    assert why == [] and c["restart"] == 0 and not c["send"]


def test_no_orders_then_still_then_recovered():
    log, needs = paths()
    s = {}
    old = [line(NOW - 1200, "[order o-1] 과자")]
    why, c = run(s, NOW, old, {}, 5, log, needs)
    assert why == ["buyer 주문 10분 동안 0건"] and c["restart"] == 1 and c["send"][0][0] == "urgent"
    why, c = run(s, NOW + 300, old, {}, 5, log, needs)                    # 5 min later: no second restart
    assert why and c["restart"] == 0 and not c["send"]
    why, c = run(s, NOW + 900, old, {}, 5, log, needs)                    # 15 min after the restart
    assert c["restart"] == 0 and c["send"][0][1] == "stall_still"
    why, c = run(s, NOW + 1200, old, {}, 5, log, needs)                   # nothing more
    assert not c["send"] and c["restart"] == 0
    why, c = run(s, NOW + 1500, [line(NOW + 1450, "[order o-2] 곡물")], {}, 5, log, needs)
    assert why == [] and "incident" not in s
    assert open(needs, encoding="utf-8").read().count("멈춤 계속") == 1
    txt = W.summary_line(NOW - 60, NOW + 3600, path=log)
    assert txt.startswith("멈춤 감지 1번") and "kit 재시작 시도" in txt and "회복" in txt, txt


def test_no_sales():
    log, needs = paths()
    why, c = run({}, NOW, [line(NOW - 60, "[order o-1] x")], {}, 0, log, needs)
    assert why == ["shop 판매 30분 동안 0건"] and c["restart"] == 1


def test_two_empty_windows_with_cash():
    log, needs = paths()
    s = {}
    o = [line(NOW - 30, "[order o-1] x")]
    w1 = {"opens_at": NOW - 1500, "closes_at": NOW - 1320}
    w2 = {"opens_at": NOW - 700, "closes_at": NOW - 520}
    run(s, NOW - 1600, o, {"balance": 9_000_000, "restock": w1}, 3, log, needs)
    run(s, NOW - 800, o, {"balance": 9_000_000, "restock": w2}, 3, log, needs)
    why, c = run(s, NOW, o, {"balance": 9_000_000, "restock": w2}, 3, log, needs)
    assert any("공급 창 2번 연속 매입 0줄" in w for w in why) and c["restart"] == 1
    s2 = {}                                                               # a supply line in the 2nd window: fine
    run(s2, NOW - 1600, o, {"balance": 9_000_000, "restock": w1}, 3, log, needs)
    run(s2, NOW - 800, o, {"balance": 9_000_000, "restock": w2}, 3, log, needs)
    why, c = run(s2, NOW, o + [line(NOW - 650, "[supply] 2 x 김치 for 1, balance 1")],
                 {"balance": 9_000_000, "restock": w2}, 3, log, needs)
    assert why == [] and c["restart"] == 0
    s3 = {}                                                               # cash under 5M: not a stall
    run(s3, NOW - 1600, o, {"balance": 4_000_000, "restock": w1}, 3, log, needs)
    run(s3, NOW - 800, o, {"balance": 4_000_000, "restock": w2}, 3, log, needs)
    why, _ = run(s3, NOW, o, {"balance": 4_000_000, "restock": w2}, 3, log, needs)
    assert why == []


def test_board_offline():
    """11/12 ~09:30 (person): our shop offline on 2 board reads in a row -> one urgent only, never a restart; a failed
    read neither counts nor resets; back online -> logged; the summary line shows both. The reader picks the shop
    row (not the buyer row) from a board shaped like the live one."""
    board = {"shops": [{"name": "x", "alive": True, "deals": 1}, {"name": "choo-chu", "alive": False, "deals": 5}],
             "buyers": [{"name": "choo-chu", "alive": True, "orders": 3, "deals": 2}]}
    assert W.board_alive(board) is False and W.board_alive({"shops": []}) is None
    log, _ = paths()
    s, sent = {}, []
    send = lambda lv, k, t: sent.append((lv, k, t))
    W.check_board(s, NOW, False, send, log_path=log)
    assert not sent
    W.check_board(s, NOW + 300, None, send, log_path=log)                    # read failed: no change
    assert not sent and s["off_n"] == 1
    W.check_board(s, NOW + 600, False, send, log_path=log)
    assert len(sent) == 1 and sent[0][:2] == ("urgent", "board_offline") and "재시작은 하지 않음" in sent[0][2]
    W.check_board(s, NOW + 900, False, send, log_path=log)                   # still offline: no second alert
    assert len(sent) == 1
    W.check_board(s, NOW + 1200, True, send, log_path=log)
    assert "off_n" not in s and len(sent) == 1
    txt = W.summary_line(NOW - 60, NOW + 3600, path=log)
    assert "보드 offline 2번 연속" in txt and "보드 online 복귀" in txt, txt
    s2 = {}                                                                  # offline once, then online: nothing
    W.check_board(s2, NOW, False, send, log_path=log)
    W.check_board(s2, NOW + 300, True, send, log_path=log)
    assert len(sent) == 1


if __name__ == "__main__":
    test_fine()
    test_no_orders_then_still_then_recovered()
    test_no_sales()
    test_two_empty_windows_with_cash()
    test_board_offline()
    print("ok")
