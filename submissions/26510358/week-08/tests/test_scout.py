"""Competitor scout over synthetic ledger supply and settle entries."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from r3lib import config, scout
from test_r3_core import CAT, mem, settle


def supply(seq, shop, sku, qty, ts):
    return {"seq": seq, "ts": ts, "kind": "supply", "round": 3, "data": {"shop": shop, "sku": sku, "qty": qty}}


def test_pressure_gap_and_premium(tmp_path, monkeypatch):
    m = mem(tmp_path, monkeypatch)
    scout._CACHE.clear()
    now = time.time()
    m._ingest([settle(i, "G1", 75000, now - i * 30) for i in range(1, 41)])        # 40 chairs sold
    m._ingest([supply(100, "rival", "G1", 10, now - 100), supply(101, "myeonggi", "G1", 50, now - 100)])
    m._ingest([settle(200 + i, "F1", 7000, now - i * 30) for i in range(1, 31)])   # 30 water sold
    m._ingest([supply(300, "rival", "F1", 60, now - 100)])
    view = scout.category_view(m, now=now)
    chairs, water = view[CAT["G1"]["category"]], view[CAT["F1"]["category"]]
    assert chairs["supplied"] == 10 and chairs["pressure"] == 0.25                 # our own supply is not a rival's
    assert scout.gap_factor(chairs) == config.GAP_MAX and scout.gap_factor(water) == config.GAP_MIN
    assert scout.ask_premium(m, CAT["G1"]["category"], now=now) == config.SCARCITY_PREMIUM
    assert scout.ask_premium(m, CAT["F1"]["category"], now=now) == 0.0
    assert scout.gaps(m, now=now)[0][0] == "소파/의자/스툴"


def test_gap_needs_evidence_and_share_needs_own_sales():
    assert scout.gap_factor({"sold": 3, "supplied": 0, "pressure": 0.0}) == 1.0
    assert scout.gap_factor({"sold": 50, "supplied": 50, "pressure": 1.0}) == 1.0
    assert scout.share_estimate(0.05, {"our_sold": 2, "our_share": 0.4}) == 0.05
    assert scout.share_estimate(0.05, {"our_sold": 8, "our_share": 0.2}) == 0.2
