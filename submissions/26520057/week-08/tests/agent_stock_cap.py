"""restock: held stock cost + this run's worst-case cost stays within stock_cost_cap x stock_cap_share."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(os.environ.get("AX_TEST_SOURCE", str(Path(__file__).resolve().parent.parent)))
sys.path.insert(0, str(SOURCE))
import shop_strategy as strategy

real = strategy.params
cfg = lambda section: ({**real(section), "stock_cost_cap": 15000000, "stock_cap_share": .85, "cash_reserve": 3000000,
                        "per_run_spend": 10000000, "max_units": 150, "max_line_qty": 3, "max_sku_units": 3,
                        "sku_cap_share": .3, "food_cost_share": .1, "min_sales": 1, "share_divisor": 1,
                        "one_per_category_run": False, "worst_surge": 1.03, "max_list_price": 4500000}
                       if section == "shop" else real(section))
with tempfile.TemporaryDirectory() as tmp:
    state = Path(tmp)
    catalog = {f"G{i:08x}": {"sku": f"G{i:08x}", "category": f"c{i}", "kind": "goods", "list_price": 400000 + i * 10000}
               for i in range(20)}
    (state / "catalog.json").write_text(json.dumps(catalog))
    obs = {"sku_sales": {k: 50 for k in catalog}, "category_ratio": {}, "updated": 1}
    for held_cost in [0, 10000000, 12000000, 12750000, 14000000]:
        units = [{"sku": "Gheld", "cost": held_cost, "category": "x", "kind": "goods"}] if held_cost else []
        inv = {"balance": 18000000, "units": units, "restock": {"open": True}, "surge": []}
        (state / "inventory.json").write_text(json.dumps(inv))
        with patch.object(strategy, "params", side_effect=cfg), patch.object(strategy, "signals", return_value=obs):
            buys = strategy.restock_plan(state)["buy"]
        bound = sum(catalog[b["sku"]]["list_price"] * .75 * 1.03 * b["qty"] for b in buys)
        assert bound == 0 or held_cost + bound <= 15000000 * .85, (held_cost, bound)
        if held_cost >= 12750000:
            assert buys == []
print("PASS restock keeps held + planned stock cost within stock_cost_cap x stock_cap_share")
