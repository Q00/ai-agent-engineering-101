"""Supply window 11 s a line (person 11/11 ~18:25), through the real restock.main() path (test_restock_window.run:
everything touching the market, the db or live files faked): the second plan fitted with 11 s (one line more than
12 s at 64 s to the close), the close - 5 s rule unchanged; more than 3 lines refused as "supply is closed" in 3 h ->
back to 12 s on the next run, an urgent Telegram, and the same run's fit already uses 12 s.
    python tests/test_window_line11.py"""
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_restock_window as T  # noqa: E402  (its import isolates policy / supply_window files)
from depts import supply_window as W  # noqa: E402


def rules(d, **kw):
    p = os.path.join(d, "supply_window.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(dict({"sec_per_line": 11, "since": time.time() - 3600, "guard_h": 3, "max_closed": 3,
                        "fallback": 12}, **kw), f)
    return p


def kit_log(d, n_closed, ago_s=600):
    p = os.path.join(d, "market-kit.log")
    t = time.time() - ago_s
    with open(p, "w", encoding="utf-8") as f:
        for i in range(n_closed):
            f.write(f"{datetime.fromtimestamp(t + i):%H:%M:%S} [supply] G{i} refused: 429 supply is closed "
                    f"{{'qty': 2}}\n")
        f.write(f"{datetime.fromtimestamp(time.time() - 5):%H:%M:%S} [supply] 1 x ok for 1, balance 1\n")
    return p


def second_plan_lines(d, left_s):
    """First run (plans 2 x 10, saves 7 for the 2nd), then the 2nd run with left_s to the close."""
    now = time.time()
    T.run(d, {"open": False, "opens_at": now + 60, "closes_at": now + 240}, [])
    p2 = os.path.join(d, "plan2.json")
    with open(p2, encoding="utf-8") as f:
        s = json.load(f)
    s["opens_at"], s["closes_at"] = time.time() - 100, time.time() + left_s
    with open(p2, "w", encoding="utf-8") as f:
        json.dump(s, f)
    return T.run(d, {"open": True, "opens_at": s["opens_at"], "closes_at": s["closes_at"]}, [])


def test_11s_fits_one_more():
    d = tempfile.mkdtemp()
    with mock.patch.object(W, "RULES", rules(d)), mock.patch.object(W, "KIT_LOGS", [kit_log(d, 0)]):
        assert len(second_plan_lines(d, 64)) == 6                        # 3 + 5 x 11 = 58 <= 64 - 5
    d = tempfile.mkdtemp()
    with mock.patch.object(W, "RULES", os.path.join(d, "none.json")):
        assert len(second_plan_lines(d, 64)) == 5                        # 12 s: 3 + 4 x 12 = 51; close - 5 s unchanged


def test_guard_reverts_on_4_closed():
    d = tempfile.mkdtemp()
    sent = []
    with mock.patch.object(W, "RULES", rules(d)), mock.patch.object(W, "KIT_LOGS", [kit_log(d, 4)]), \
            mock.patch("alerts.send", lambda lv, k, t, **kw: sent.append((lv, k, t))):
        got = second_plan_lines(d, 64)
        r = W.rules()
    assert r["sec_per_line"] == 12 and r["reverted_from"] == 11 and r["closed_now"] == 4
    assert len(got) == 5                                                  # the same run already fits at 12 s
    assert sent and sent[0][0] == "urgent" and sent[0][1] == "window_line_revert" and "11초 -> 12초" in sent[0][2]


def test_guard_keeps_11_at_3_closed():
    d = tempfile.mkdtemp()
    sent = []
    with mock.patch.object(W, "RULES", rules(d)), mock.patch.object(W, "KIT_LOGS", [kit_log(d, 3)]), \
            mock.patch("alerts.send", lambda *a, **kw: sent.append(a)):
        assert len(second_plan_lines(d, 64)) == 6
        r = W.rules()
    assert r["sec_per_line"] == 11 and r["closed_now"] == 3 and not sent and not r.get("reverted")


def test_closed_before_the_change_not_counted():
    d = tempfile.mkdtemp()
    with mock.patch.object(W, "RULES", rules(d, since=time.time() - 300)), \
            mock.patch.object(W, "KIT_LOGS", [kit_log(d, 5, ago_s=1200)]), mock.patch("alerts.send", lambda *a, **k: 0):
        W.guard()
        assert W.rules()["sec_per_line"] == 11 and W.rules()["closed_now"] == 0


def test_115s_and_6_closed():
    """11/12 ~09:10 (person): 11.5 s a line, back to 12 s only past 6 lines refused as closed in 3 h."""
    d = tempfile.mkdtemp()
    with mock.patch.object(W, "RULES", rules(d, sec_per_line=11.5, max_closed=6)),             mock.patch.object(W, "KIT_LOGS", [kit_log(d, 0)]):
        assert len(second_plan_lines(d, 55)) == 5                        # ~47 s left: 47 / 11.5 -> 4 + 1
    d = tempfile.mkdtemp()
    with mock.patch.object(W, "RULES", os.path.join(d, "none.json")):
        assert len(second_plan_lines(d, 55)) == 4                        # 12 s: 47 / 12 -> 3 + 1
    d = tempfile.mkdtemp()
    sent = []
    with mock.patch.object(W, "RULES", rules(d, sec_per_line=11.5, max_closed=6)),             mock.patch.object(W, "KIT_LOGS", [kit_log(d, 6)]), mock.patch("alerts.send", lambda *a, **k: sent.append(a)):
        second_plan_lines(d, 55)
        r = W.rules()
    assert r["sec_per_line"] == 11.5 and r["closed_now"] == 6 and not sent and not r.get("reverted")
    d = tempfile.mkdtemp()
    with mock.patch.object(W, "RULES", rules(d, sec_per_line=11.5, max_closed=6)),             mock.patch.object(W, "KIT_LOGS", [kit_log(d, 7)]), mock.patch("alerts.send", lambda *a, **k: sent.append(a)):
        assert len(second_plan_lines(d, 55)) == 4                        # reverted in the same run
        r = W.rules()
    assert r["sec_per_line"] == 12 and r["reverted_from"] == 11.5 and sent and "11.5초 -> 12초" in sent[0][2]         and "6줄 초과" in sent[0][2], sent


if __name__ == "__main__":
    test_11s_fits_one_more()
    test_guard_reverts_on_4_closed()
    test_guard_keeps_11_at_3_closed()
    test_closed_before_the_change_not_counted()
    test_115s_and_6_closed()
    print("ok")
