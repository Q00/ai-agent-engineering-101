"""core.market_state: what each shop holds and what each SKU settles at, from ledger lines (11/13)."""
import unittest

from core import market_state as ms

CAT = {"G1": {"sku": "G1", "kind": "goods", "list_price": 1000}, "F1": {"sku": "F1", "kind": "food", "list_price": 100}}


def line(kind, ts, **data):
    return {"kind": kind, "ts": ts, "data": data}


class MarketState(unittest.TestCase):
    def test_held_is_supply_minus_settled_and_expired(self):
        cur = {"files": {}, "held": {}, "recent": []}
        ms._apply(cur, line("supply", 1, shop="a", sku="G1", qty=3, total=1800, units=["u1", "u2", "u3"]), CAT)
        ms._apply(cur, line("supply", 2, shop=ms.ME, sku="F1", qty=1, total=50, units=["u4"]), CAT)
        ms._apply(cur, line("settle", 3, shop="https://a", sku="G1", unit="u1", amount=700, buyer="b"), CAT)
        ms._apply(cur, line("expire", 4, shop="a", units=["u2"]), CAT)
        doc = ms.build(cur, CAT, now=5)
        self.assertEqual(doc["holders"]["G1"], {"a": 1})
        self.assertEqual(doc["skus"]["F1"]["ours"], 1)
        g = doc["skus"]["G1"]
        self.assertEqual((g["n6"], g["held"], g["p50"], g["scarcity"]), (1, 1, 0.7, 1.0))

    def test_scarcity_counts_sales_per_unit_held(self):
        cur = {"files": {}, "held": {"u9": ["a", "G1"]}, "recent": []}
        for i in range(6):
            ms._apply(cur, line("settle", 10 + i, shop="https://b", sku="G1", unit=f"x{i}", amount=600 + 10 * i), CAT)
        g = ms.build(cur, CAT, now=20)["skus"]["G1"]
        self.assertEqual(g["scarcity"], 6.0)
        self.assertLessEqual(g["p25"], g["p50"])
        self.assertLessEqual(g["p50"], g["p75"])


if __name__ == "__main__":
    unittest.main()
