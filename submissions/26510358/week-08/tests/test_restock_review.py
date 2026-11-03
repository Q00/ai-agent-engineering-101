"""Restock review: factors from a scripted ReAct run are clamped, stored, applied, and expire."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import restock_r3
import restock_review_r3
from r3lib import config, market
from r3lib.memory import Memory
from test_r3_core import CAT, settle

DEMAND = [{"category": CAT["F1"]["category"], "item": "생수", "kind": "food", "skus": 1, "median_price": 10000},
          {"category": CAT["G1"]["category"], "item": "의자", "kind": "goods", "skus": 1, "median_price": 100000}]


def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "_CATALOG", CAT)
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "MARKET_DIR", tmp_path)
    monkeypatch.setattr(config, "EXPLORE_MAX_LIST", 200000)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "m.sqlite")
    m = Memory()
    now = time.time()
    m._ingest([settle(i, "F1", 8500, now - i * 300) for i in range(1, 13)])
    (tmp_path / "inventory.json").write_text(json.dumps({"round": 3, "balance": 5_000_000, "units": []}))
    (tmp_path / "demand.json").write_text(json.dumps(DEMAND, ensure_ascii=False))
    return m, now


def test_clamp_keeps_known_categories_only():
    cats = {d["category"] for d in DEMAND}
    final = {"adjustments": [{"category": DEMAND[0]["category"], "factor": 9, "reason": "r"},
                             {"category": "없는>카테고리", "factor": 0},
                             {"category": DEMAND[1]["category"], "factor": "x"},
                             {"category": DEMAND[1]["category"], "factor": 1.0}]}
    assert restock_review_r3.clamp(final, cats) == {DEMAND[0]["category"]: {"factor": config.ADJUST_MAX, "reason": "r"}}


def test_review_applies_then_expires(tmp_path, monkeypatch):
    m, now = setup(tmp_path, monkeypatch)
    base, _, _ = restock_r3.plan({"balance": 5_000_000, "units": []}, DEMAND, m, now=now)
    assert any(ln["sku"] == "F1" for ln in base) and {"sku": "G1", "qty": 1} in base

    final = {"adjustments": [{"category": DEMAND[0]["category"], "factor": 0, "reason": "sell-through 0.1"},
                             {"category": DEMAND[1]["category"], "factor": 0, "reason": "goods held 3 days"}]}
    seen = {}

    def run(goal, tools, shape, **k):
        seen["tools"] = {t.name: t for t in tools}
        return final, [{}]
    restock_review_r3.main(run=run)
    assert seen["tools"]["plan_preview"].fn()["buy"]                  # the agent sees the unreviewed plan
    lines, why, _ = restock_r3.plan({"balance": 5_000_000, "units": []}, DEMAND, m, now=now)
    assert lines == [] and any("review_factor" in w for w in why)
    later, _, _ = restock_r3.plan({"balance": 5_000_000, "units": []}, DEMAND, m,
                                  now=now + (config.ADJUST_TTL_H + 1) * 3600)
    assert later                                                      # the rules take over when the review stops


def test_failed_review_keeps_old_factors(tmp_path, monkeypatch):
    m, now = setup(tmp_path, monkeypatch)
    m.put("restock_adjust", {"ts": time.time(), "items": {DEMAND[0]["category"]: {"factor": 0.5, "reason": ""}}})
    restock_review_r3.main(run=lambda *a, **k: (None, []))
    assert restock_r3.active_adjustments(m)[DEMAND[0]["category"]]["factor"] == 0.5


def test_sell_through(tmp_path, monkeypatch):
    m, now = setup(tmp_path, monkeypatch)
    m.record_restock([{"sku": "F1", "qty": 10}], [{"sku": "F1", "reason": "demand"}])
    with m.db() as c:
        c.execute("INSERT INTO shops VALUES ('https://s1', 'me', 'k', 'o', 1, ?)", (now,))
    st = m.sell_through()[CAT["F1"]["category"]]
    assert st["requested"] == 10 and st["sold"] == 12 and st["rate"] == 1.2
