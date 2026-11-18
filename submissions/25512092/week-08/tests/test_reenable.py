"""11/10 ~11:50 (person): automatic STOCK_CAP on again with unsold judged at 12 h from the switch-on, high_share 0.7
pinned 24 h, sofa warranty on again with the 48 h refunds > 30% of profit switch-off. Temp files only.
    python tests/test_reenable.py"""
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy, tmp_json, tmp_path  # noqa: E402

P = policy({"purchasing": {"high_share": 0.85, "high_max": 100}})
import alerts  # noqa: E402
import limits  # noqa: E402
import reenable_1110 as R  # noqa: E402
from depts import cap_brake, policy_notify, sofa, turnover_case  # noqa: E402

NOW = 1794282000.0


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def test_apply():
    auto = tmp_json({"enabled": False, "since": 1, "reserve": 3_000_000, "exit_ratio": 0.88, "paused": False,
                     "case": {"median_h": 4, "old_share": 0.35, "unsold_min": 5_000_000, "consecutive": 2},
                     "fired": 5, "fired_why": ["x"]}, "auto")
    brake = tmp_json({"enabled": False, "median_h": 4, "old_h": 6, "old_share": 0.35, "consecutive": 2, "warn_streak": 2,
                      "fired": 5, "fired_why": ["y"], "low": 20_000_000}, "brake")
    so = tmp_json({"enabled": True, "warranty": False, "since": 1, "c_no_warranty": True}, "sofa")
    lp = tmp_path("limits.py")
    open(lp, "w").close()
    with mock.patch.object(limits, "AUTO_CAP_PATH", auto), mock.patch.object(cap_brake, "RULES", brake), \
            mock.patch.object(sofa, "RULES", so), mock.patch.object(policy_notify, "PINS", tmp_path("pins.json")), \
            mock.patch.object(turnover_case, "STATE", tmp_path("tc.json")), mock.patch.object(R, "LIMITS_PY", lp), \
            mock.patch.object(R, "STATE", tmp_path("st.json")), mock.patch.object(R, "LOG", tmp_path("log.txt")):
        t, out = R.apply(now=NOW, held=(30_000_000, 40, 10_000_000, 100, 5_000_000))
        a, b, s = load(auto), load(brake), load(so)
        assert a["enabled"] and a["since"] == round(NOW) and a["case"]["old_h"] == 12 and a["case"]["old_share"] == 0.35
        assert a["case"]["consecutive"] == 2 and a["history"][0]["fired"] == 5 and "fired" not in a
        assert b["enabled"] and b["old_h"] == 12 and b["since"] == round(NOW) and b["warn_streak"] == 0
        assert b["median_h"] == 4 and b["old_share"] == 0.35 and b["consecutive"] == 2 and "fired" not in b
        assert s["warranty"] and s["warranty_since"] == round(NOW) and s["c_no_warranty"] and s["off_rule"]["share"] == 0.3
        assert P.get("purchasing", "high_share") == 0.7 and P.get("purchasing", "high_max") == 100
        assert policy_notify.pinned("purchasing.high_share", NOW + 23 * 3600)
        assert not policy_notify.pinned("purchasing.high_share", NOW + 25 * 3600)
        assert "42_000_000" in open(lp).read()                     # 5M + 30M + 10M - 3M
        assert limits.auto_cap_on(r=a, now=NOW)
        u = {"sku": "S", "grade": "B", "cost": 200_000}
        assert sofa.warranty_for(u) == 72 and sofa.warranty_for(dict(u, grade="C")) == 0


def test_sofa_off_rule():
    r = {"enabled": True, "warranty": True, "since": 1, "off_rule": {"window_h": 48, "share": 0.3}}
    base = {"negotiations": 10, "receipts": 3, "rate": 0.3, "sold": 3, "revenue": 3_000_000}
    for profit, refund, n, want in ((10_000_000, 2_900_000, 4, False), (10_000_000, 3_100_000, 1, True),
                                    (-1, 100, 1, True), (5_000_000, 0, 0, False)):
        o = dict(base, profit=profit, refund_amount=refund, refunds=n)
        with mock.patch.object(alerts, "sofa_numbers", return_value=o) as sn:
            trip, _, rule = alerts.sofa_off_trip(r, now=NOW)
        assert trip is want, (profit, refund, trip)
        assert sn.call_args[0][0] == NOW - 48 * 3600 and "30%" in rule
    o = dict(base, profit=10_000_000, refund_amount=100, refunds=3)  # the old rule: 3 refunds
    with mock.patch.object(alerts, "sofa_numbers", return_value=o):
        assert alerts.sofa_off_trip({"since": 1}, now=NOW)[0]
        assert not alerts.sofa_off_trip(r, now=NOW)[0]


def test_turnover_12h_since():
    import sqlite3
    db = tmp_path("m.db")
    d = sqlite3.connect(db)
    d.execute("CREATE TABLE settles (unit TEXT, shop TEXT, ts REAL)")
    d.execute("CREATE TABLE my_stock (unit TEXT, cost INTEGER, supplied_at REAL)")
    for i, age in enumerate((13, 13, 8, 30)):            # 8 h old: not judged yet at 12 h; 30 h: before the switch-on
        d.execute("INSERT INTO my_stock VALUES (?, 3000000, ?)", (f"u{i}", NOW - age * 3600))
    d.commit()
    d.close()
    m = turnover_case.measure(NOW, NOW - 20 * 3600, {"old_h": 12}, db)
    assert m["old_cost"] == 6_000_000 and m["old_share"] == 1.0, m


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
    print("ok")
