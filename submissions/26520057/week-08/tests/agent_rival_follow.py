"""rival_follow: rival price / our first ask for quotes lost within 30 min; 0 keeps today's prices; floor holds."""
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
    root, state = Path(tmp), Path(tmp) / "state"
    (root / "logs").mkdir()
    state.mkdir()
    catalog = {s: {"sku": s, "category": "c", "kind": "goods", "list_price": 100000}
               for s in ["G00000001", "G00000002", "G00000003", "G00000004"]}
    (state / "catalog.json").write_text(json.dumps(catalog))
    (state / "shop-signals.json").write_text(json.dumps(
        {"sku_sales": {}, "category_ratio": {}, "market_offset": 0, "updated": 0, "cursor": 0}))
    now = time.time()
    log = [{"ts": now - 3000, "event": "quote", "task": "t1", "sku": "G00000001", "ask": 100000},
           {"ts": now - 2990, "event": "quote", "task": "t1", "sku": "G00000001", "ask": 90000},   # not the first ask
           {"ts": now - 3000, "event": "quote", "task": "t2", "sku": "G00000002", "ask": 100000},
           {"ts": now - 3000, "event": "quote", "task": "t3", "sku": "G00000003", "ask": 100000},
           {"ts": now - 2990, "event": "agreed", "task": "t3", "sku": "G00000003", "price": 100000},  # we sold: not lost
           {"ts": now - 3000, "event": "quote", "task": "t4", "sku": "G00000004", "ask": 100000}]
    (root / "logs/shop-decisions.jsonl").write_text("\n".join(json.dumps(e) for e in log) + "\n")
    settle = lambda seq, sku, ts, amount, shop: {"seq": seq, "round": 3, "kind": "settle", "ts": ts,
                                                 "data": {"sku": sku, "amount": amount, "shop": shop}}
    rows = [settle(1, "G00000001", now - 2000, 130000, "rival"),
            settle(2, "G00000002", now - 2000, 90000, "rival"),
            settle(3, "G00000003", now - 2000, 120000, "rival"),
            settle(4, "G00000004", now - 2000, 150000, "cheapest-in-town"),   # our own sale is no loss
            settle(5, "G00000004", now - 100, 150000, "rival")]               # 48 min after the quote: too late

    def fake(url, timeout=None):
        if "/api/ledger" in url:
            return Resp(json.dumps(rows).encode())
        raise OSError("no network in tests")

    with patch.object(strategy, "ROOT", root), patch.object(strategy.urllib.request, "urlopen", side_effect=fake):
        obs = strategy.signals(state, refresh=True)
    assert obs["rival_ratio"] == {"G00000001": 1.3, "G00000002": .9}, obs["rival_ratio"]
    print("PASS lost quotes: rival price over our first ask, other shops within 30 min, sold quotes skipped")

    real = strategy.params
    unit = {"sku": "G00000001", "cost": 50000, "list_price": 100000, "category": "c"}
    cheap = dict(unit, sku="G00000002")
    view = {"category_ratio": {"c": .72}, "rival_ratio": {"G00000001": 1.3, "G00000002": .5}}

    def price(u, follow=None, extra=None):
        cfg = lambda s: ({**real(s), **(extra or {}), **({"rival_follow": follow} if follow is not None else {})}
                         if s == "shop" else real(s))
        with patch.object(strategy, "params", side_effect=cfg):
            return strategy.prices(u, view, "B")

    assert strategy.DEFAULTS["shop"]["rival_follow"] == 0
    base = price(unit, extra={"rival_follow": 0})
    with patch.object(strategy, "params", side_effect=lambda s: dict(strategy.DEFAULTS[s])):
        assert strategy.prices(unit, view, "B") == strategy.prices(unit, {"category_ratio": {"c": .72}}, "B")
    up = price(unit, 0.5)
    assert up["floor"] == base["floor"] and up["ask"] > base["ask"], (base, up)
    assert abs(up["ask"] - base["ask"] * 1.15) <= 1, (base, up)
    down = price(cheap, 1.0)
    assert down["ask"] == down["floor"] >= cheap["cost"] * real("shop")["margin"]["B"], down
    print("PASS rival_follow 0 keeps today's ask; >0 moves it toward the rivals' price, never below the floor")
