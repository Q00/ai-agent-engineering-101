"""unserved_boost: ledger settles of the last 6 h (catalog SKUs only) are recorded; boost 1.0 keeps the plan."""
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(os.environ.get("AX_TEST_SOURCE", str(Path(__file__).resolve().parent.parent)))
sys.path.insert(0, str(SOURCE))
import shop_strategy as strategy


class Resp(io.BytesIO):
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


with tempfile.TemporaryDirectory() as tmp:
    state = Path(tmp)
    catalog = {f"G{i:08x}": {"sku": f"G{i:08x}", "category": f"c{i}", "kind": "goods", "list_price": 100000}
               for i in range(4)}
    (state / "catalog.json").write_text(json.dumps(catalog))
    (state / "shop-signals.json").write_text(json.dumps(
        {"sku_sales": {}, "category_ratio": {}, "market_offset": 0, "updated": 0, "cursor": 0}))
    now = time.time()
    rows = [{"seq": 1, "round": 3, "kind": "settle", "ts": now - 100, "data": {"sku": "G00000001", "amount": 70000}},
            {"seq": 2, "round": 3, "kind": "settle", "ts": now - 7 * 3600, "data": {"sku": "G00000002", "amount": 70000}},
            {"seq": 3, "round": 3, "kind": "settle", "ts": now - 50, "data": {"sku": "소파/의자/스툴", "amount": 70000}}]

    def fake(url, timeout=None):
        if "/api/ledger" in url:
            return Resp(json.dumps(rows).encode())
        raise OSError("no network in tests")

    with patch.object(strategy.urllib.request, "urlopen", side_effect=fake):
        obs = strategy.signals(state, refresh=True)
    assert set(obs["recent_settles"]) == {"G00000001"}, obs["recent_settles"]
    print("PASS recent settles keep only catalog SKUs inside the 6 h window")

    real = strategy.params
    obs = {"sku_sales": {"G00000000": 40, "G00000001": 30, "G00000002": 20, "G00000003": 10},
           "category_ratio": {}, "market_offset": 0, "recent_settles": {"G00000001": [now - 100]}}
    inv = {"balance": 50000000, "units": [], "restock": {"open": True}, "surge": []}
    (state / "inventory.json").write_text(json.dumps(inv))

    def plan(boost=None):
        cfg = lambda s: ({**real(s), "max_line_qty": 1, "per_run_spend": 120000, "stock_cost_cap": 50000000,
                          "cash_reserve": 0, "min_sales": 1, "min_list_price": 1000,
                          **({"unserved_boost": boost} if boost is not None else {})} if s == "shop" else real(s))
        with patch.object(strategy, "params", side_effect=cfg), patch.object(strategy, "signals", return_value=obs):
            return strategy.restock_plan(state)["buy"]

    base = plan()
    assert strategy.DEFAULTS["shop"]["unserved_boost"] == 1.0
    assert plan(1.0) == base and base[0]["sku"] == "G00000000", base
    assert plan(2.0)[0]["sku"] == "G00000001", plan(2.0)
    print("PASS unserved_boost 1.0 keeps today's plan; a higher boost moves the unserved SKU up")
