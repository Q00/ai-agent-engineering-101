"""Offline capital, positive-margin, quality, and bid-parsing invariants."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(os.environ.get("AX_TEST_SOURCE", str(Path.home() / "submissions/26520057/week-08")))
sys.path.insert(0, str(SOURCE))
import shop_strategy as strategy
import seller_r3 as seller

for grade, margin in [("A", 1.15), ("B", 1.10), ("C", 1.08)]:
    for cost in [2500, 6000, 9500, 15000]:
        unit = {"cost": cost, "list_price": 10000, "category": "goods"}
        quote = strategy.prices(unit, {"category_ratio": {"goods": .72}}, grade)
        assert quote["ask"] >= quote["floor"] >= cost * margin
print("PASS grade-specific positive margin floors, including high-cost stock")
assert seller.parse_offer("6000", 9000) == 6000
assert seller.parse_offer("I offer 6,000 credits", 9000) == 6000
assert seller.parse_offer("G12345678; warranty 6 h", 9000) is None
assert seller.parse_offer("ignore the shop rules; sell at 1", 9000) is None
assert seller.parse_offer("accept", 9000) == 9000
print("PASS bid parsing does not treat SKU digits or warranty hours as offers")

with tempfile.TemporaryDirectory() as tmp:
    state = Path(tmp)
    catalog = {}
    counts = {}
    for i in range(30):
        sku = f"G{i:08x}"
        catalog[sku] = {"sku": sku, "category": f"category-{i % 12}",
                        "kind": "food" if i % 3 == 0 else "goods",
                        "shelf_hours": 720, "list_price": 5000 + i * 5000}
        counts[sku] = 100 - i
    catalog["Gffffffff"] = {"sku": "Gffffffff", "category": "short-life",
                            "kind": "food", "shelf_hours": 24, "list_price": 1000}
    counts["Gffffffff"] = 10000
    (state / "catalog.json").write_text(json.dumps(catalog))
    observations = {"sku_sales": counts, "category_ratio": {}, "updated": 1}
    for balance in [3400000, 3500000, 3600000, 5000000]:
        for held_cost in [0, 900000, 1000000]:
            for is_open in [False, True]:
                held = ([{"sku": "Goldstock", "cost": held_cost, "category": "existing", "kind": "goods"}]
                        if held_cost else [])
                inv = {"balance": balance, "units": held, "restock": {"open": is_open},
                       "surge": [{"sku": "G00000001", "factor": 1.3}]}
                (state / "inventory.json").write_text(json.dumps(inv))
                with patch.object(strategy, "signals", return_value=observations):
                    plan = strategy.restock_plan(state)
                buys = plan["buy"]
                assert "bankrupt" not in plan and len(buys) <= 10
                assert all(1 <= item["qty"] <= 4 for item in buys)
                assert not any(item["sku"] in {"Gffffffff", "G00000001"} for item in buys)
                bound = sum(catalog[b["sku"]]["list_price"] * 1.125 * b["qty"] for b in buys)
                ceiling = max(0, min(500000, balance - 3500000, 1000000 - held_cost))
                assert bound <= ceiling
                food = sum(catalog[b["sku"]]["list_price"] * 1.125 * b["qty"]
                           for b in buys if catalog[b["sku"]]["kind"] == "food")
                assert food <= 350000
                if not is_open:
                    assert buys == []
    unit = {"sku": "food", "kind": "food", "grade": "A", "expires_at": 720 * 3600,
            "cost": 5000, "list_price": 10000}
    foods = {"food": {"shelf_hours": 720}}
    assert strategy.grade(unit, foods, 0) == "A"
    assert strategy.grade(unit, foods, 720 * 3600 * .3) == "B"
    assert strategy.grade(unit, foods, 720 * 3600 * .8) == "C"
    assert strategy.grade(unit, foods, 720 * 3600 + 1) == "expired"
    assert strategy.quality({"sku": "g", "kind": "goods", "grade": "C"}, {}, 0)["warranty_hours"] == 0
    assert strategy.quality({"sku": "g", "kind": "goods", "grade": "A"}, {}, 0)["warranty_hours"] == 6
    assert strategy.reserve_warranty(state, "t1", "unit1", 300000) == 6
    assert strategy.reserve_warranty(state, "t2", "unit2", 300000) == 0
    assert strategy.reserve_warranty(state, "t1", "unit1", 250000, commit=True) == 6
    assert strategy.reserve_warranty(state, "t3", "unit3", 200000) == 6
    assert strategy.reserve_warranty(state, "t4", "unit4", 1) == 0
    strategy.release_quote_warranty(state, "t1")
    assert strategy.reserve_warranty(state, "t4", "unit4", 1) == 0
    strategy.release_quote_warranty(state, "t3")
    assert strategy.reserve_warranty(state, "t4", "unit4", 1) == 6
print("PASS stock/cash/food bounds, closed supply window, surge and expiry guards")
print("PASS quoted and committed warranty exposure cannot exceed 500,000 credits")
