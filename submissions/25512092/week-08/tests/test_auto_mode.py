"""11/10 ~15:00 (person): auto mode - Telegram filter, warn-only brakes, the watchdog's restart rules, harness failure
streak. Temp files only.
    python tests/test_auto_mode.py"""
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import tmp_json  # noqa: E402
import alerts  # noqa: E402

NOW = 1_000_000.0


def test_held():
    p = tmp_json({"enabled": True, "until": NOW + 10, "pass_keys": ["morning_summary"], "brakes_warn_only": True}, "am")
    with mock.patch.object(alerts, "AUTO_MODE", p):
        assert alerts.held("info", "x", NOW) and alerts.held("daily", "deep_high_report:1", NOW)
        assert not alerts.held("urgent", "x", NOW) and not alerts.held("daily", "morning_summary:20261110", NOW)
        assert not alerts.held("info", "x", NOW + 20)                  # after "until"
        assert alerts.brakes_warn_only(NOW) and not alerts.brakes_warn_only(NOW + 20)
        sent = []
        with mock.patch.object(alerts, "telegram", lambda t: sent.append(t) or "sent"), \
                mock.patch.object(alerts, "PATH", p + ".jsonl"), mock.patch("time.time", return_value=NOW):
            assert alerts.send("info", "k1", "t", log=lambda m: None) == "held"
            assert alerts.send("urgent", "k2", "t", log=lambda m: None) == "sent" and len(sent) == 1
    with mock.patch.object(alerts, "AUTO_MODE", p + ".missing"):
        assert not alerts.held("info", "x", NOW) and not alerts.brakes_warn_only(NOW)


def test_watchdog():
    import watchdog as W
    rows = [(1, 0, "python keepalive.py"), (2, 1, "python market_kit.py join"), (3, 2, "python seller_r3.py"),
            (4, 0, "python analyst_main.py")]
    calls = []
    with mock.patch.object(W, "urgent", lambda k, t: calls.append(k)), \
            mock.patch.object(W, "start_analyst", lambda: calls.append("start")), \
            mock.patch.object(W, "restart_kit", lambda r: calls.append("kit") or True):
        s = {}
        assert W.check(s, rows, NOW, 10) == []
        no_an = [r for r in rows if r[0] != 4]
        for i in range(3):
            assert "analyst restarted" in W.check(s, no_an, NOW + i * 60, 10)
        acts = W.check(s, no_an, NOW + 200, 10)                         # 4th within the hour: stopped
        assert "analyst restarted" not in acts and "watchdog_stop:analyst" in calls and calls.count("start") == 3
        no_seller = [r for r in rows if r[0] != 3]
        s = {}
        assert W.check(s, no_seller, NOW, 10) == ["seller down: x"] or "kit" not in calls[-1:]   # 1st miss: wait
        acts = W.check(s, no_seller, NOW + 60, 10)
        assert any("kit restarted (seller)" in a for a in acts), acts
        acts = W.check(s, rows, NOW + 120, 9999)                        # buyer silent, but a restart 2 min ago
        assert "kit restart pending" in acts, acts
    with mock.patch.object(W, "urgent", lambda k, t: calls.append(k)),             mock.patch.object(W, "start_keepalive", lambda: calls.append("ka") or True):
        s = {}
        assert "keepalive restarted" in W.check(s, rows[1:], NOW, 10) and calls[-1].startswith("watchdog_restart:keep")
        assert "keepalive start pending" in W.check(s, rows[1:], NOW + 60, 10)


def test_restart_kit_spares_keepalive():
    import watchdog as W
    rows = [(10, 1, "venv python keepalive.py"), (11, 10, "python312 keepalive.py"),
            (20, 11, "venv python market_kit.py join"), (21, 20, "python312 market_kit.py join"),
            (30, 21, "python seller_r3.py")]
    killed = []
    with mock.patch("subprocess.run", lambda args, **k: killed.append(args[-1])):
        assert W.restart_kit(rows)
    assert killed == ["20"], killed                  # the top kit only: not 11 (keepalive) nor 21 (its child)


def test_harness_streak():
    import harness
    sent = []
    with mock.patch.object(alerts, "send", lambda lvl, key, *a, **k: sent.append((lvl, key))), \
            mock.patch.object(harness, "FAIL_LOG", tmp_json({}, "hf") + ".jsonl"):
        harness._ok()
        for _ in range(10):
            harness._failed("limit", 1, "", "usage limit")
        assert all(l == "info" for l, _ in sent)
        harness._failed("limit", 1, "", "usage limit")                 # the 11th in a row
        assert sent[-1][0] == "urgent" and sent[-1][1].startswith("harness_fail_streak")
        harness._ok()
        harness._failed("limit", 1, "", "x")
        assert sent[-1][0] == "info"


def test_kit_flapping():
    import watchdog as W
    calls = []
    base = [(1, 0, "keepalive.py"), (4, 0, "analyst_main.py")]
    with mock.patch.object(W, "urgent", lambda k, t: calls.append(k)):
        s = {}
        for i in range(5):                           # kit pid changes 4 times after the first sight
            rows = base + [(100 + i, 1, "market_kit.py join"), (200 + i, 100 + i, "market_kit.py join"),
                           (300 + i, 200 + i, "seller_r3.py")]
            W.check(s, rows, NOW + i * 600, 10)
        assert len(s["kit_starts"]) == 4 and calls.count("watchdog_kit_flapping") == 1, (s, calls)


if __name__ == "__main__":
    test_kit_flapping()
    test_held()
    test_watchdog()
    test_harness_streak()
    test_restart_kit_spares_keepalive()
    print("ok")
