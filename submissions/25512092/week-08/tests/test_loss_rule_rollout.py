"""tools/loss_rule_rollout.py (person 11/12 ~14:40): targets() through the seller's floor chain with the rules on vs
off (tagged 3/4, 1, 2), apply() writes both switches (temp files), and the 3 h report's lines from a temp db (board
reads faked). Live files untouched.
    python tests/test_loss_rule_rollout.py"""
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy, tmp_json  # noqa: E402

policy()
import loss_rule_rollout as R  # noqa: E402
from depts import high_ask, overcost, sales, sofa  # noqa: E402

NOW = time.time()
HA = {"enabled": True, "cap": True, "floor_rule": {"enabled": True, "cost": 1.05, "median": 0.8},
      "c_step": {"enabled": True, "h1": 12, "h2": 24, "cost": 1.1, "cost2": 1.0, "median": 0.6}}


def u(unit, cost, age_h, grade="A", sku=None):
    return {"unit": unit, "sku": sku or unit, "cost": cost, "grade": grade, "kind": "goods", "category": "가구>침대",
            "supplied_at": NOW - age_h * 3600, "expires_at": None, "list_price": cost * 2, "name": unit}


def test_targets_apply_report():
    units = [u("loss", 1_040_000, 2), u("far", 1_100_000, 2), u("ab", 600_000, 30), u("same", 500_000, 2),
             u("old", 90_000, 60)]
    avg = {"loss": 1_000_000, "far": 1_000_000, "ab": 1_000_000, "same": 700_000, "old": 100_000}
    with mock.patch.object(high_ask, "RULES", tmp_json(HA, "high_ask")), \
            mock.patch.object(overcost, "RULES", tmp_json({"enabled": True}, "overcost")), \
            mock.patch.object(sales, "LOSS_RULE", tmp_json({}, "loss_rule")), \
            mock.patch.object(sales, "ROUND_END", NOW + 5 * 86400), \
            mock.patch.object(sofa, "market", lambda sku, now=None: (1_000_000, 50, None, None)), \
            mock.patch.object(sofa, "applies", lambda u: False), \
            mock.patch("depts.volume.floor", lambda u, now=None: None):
        ts = R.targets(NOW, units, avg_of=avg.get, case="normal")
    got = {t["unit"]: (t["rule"], t["cur"], t["new"]) for t in ts}
    assert got == {"loss": ("3/4", 1_040_000, 1_000_000), "ab": ("1", 800_000, 700_000), "old": ("2", 81_000, 90_000)}, got
    txt = R.preview_text(ts, NOW)
    assert "3·4번 손절 -> 시장 평균] 1개" in txt and "원가/평균 1.040" in txt, txt
    d = tempfile.mkdtemp()
    with mock.patch.object(R, "HIGH_ASK", tmp_json(HA, "high_ask")) as ha, mock.patch.object(R, "LOSS", os.path.join(d, "l.json")):
        R.apply(NOW)
        assert json.load(open(R.HIGH_ASK, encoding="utf-8"))["age_ab"]["enabled"]
        assert json.load(open(R.HIGH_ASK, encoding="utf-8"))["floor_rule"]["cost"] == 1.05      # other keys kept
        assert json.load(open(R.LOSS, encoding="utf-8"))["per_day"] == 0.01
    db = os.path.join(d, "m.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE my_stock (unit, sku, cost, supplied_at)")
    c.execute("CREATE TABLE settles (ts, shop, unit, amount)")
    c.executemany("INSERT INTO my_stock VALUES (?,?,?,?)", [("loss", "loss", 1_040_000, NOW - 7200),
                                                          ("ab", "ab", 600_000, NOW - 30 * 3600)])
    c.executemany("INSERT INTO settles VALUES (?,?,?,?)", [(NOW + 600, "choo-chu", "loss", 1_000_000),
                                                         (NOW + 900, "choo-chu", "ab", 720_000)])
    c.commit()
    c.close()
    import board_read as BR
    with mock.patch.object(BR, "now", lambda *a, **k: None), mock.patch.object(BR, "at", lambda *a, **k: None), \
            mock.patch.object(BR, "per_h", lambda a, b, s, k: 1234.0), mock.patch.object(BR, "failed", lambda **k: []):
        txt = R.report({"t0": round(NOW), "units": {t["unit"]: t["rule"] for t in ts}}, now=NOW + 3 * 3600, db_path=db,
                       inv_path=tmp_json({"units": []}, "inv"))
    assert "3·4번 (손절 -> 평균): 1개 중 판매 1개, 매출 1,000,000, 원가 1,040,000, 손익 -40,000, 실제 손실 합 -40,000 "            "(원가 아래로 판 건만), 남은 0개" in txt, txt
    assert "1번 (A·B 24시간+): 1개 중 판매 1개, 매출 720,000, 원가 600,000, 손익 +120,000" in txt, txt
    assert "12시간+ 미판매 고가: " in txt and "시간당 Book 1,234" in txt, txt


if __name__ == "__main__":
    test_targets_apply_report()
    print("ok")
