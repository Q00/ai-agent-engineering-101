import json
import tempfile
import unittest
from pathlib import Path

import ledger

NOW = 1_000_000
CATALOG = {
    "S1": {"category": "home>bed", "list_price": 1000},
    "S2": {"category": "home>bed", "list_price": 2000},
    "F1": {"category": "food>kimchi", "list_price": 100},
}
ENTRIES = [
    {"seq": 1, "ts": NOW - 100, "kind": "settle", "data": {"sku": "S1", "item": "S1", "amount": 800, "shop": "u1", "buyer": "b",
                                                            "unit": "u-1", "grade": "B", "claim": None, "claim_honest": None}},   # the real Round 3 shape
    {"seq": 2, "ts": NOW - 200, "kind": "settle", "data": {"item": "S1", "price": 900, "shop": "u2"}},
    {"seq": 3, "ts": NOW - 300, "kind": "settle", "data": {"sku": "S1", "price": 1000}},
    {"seq": 4, "ts": NOW - 400, "kind": "result", "data": {"receipt": {"item": "S2", "price": 1500}}},   # Round 2 shape
    {"seq": 5, "ts": NOW - 500, "kind": "order", "data": {"item": "S1", "budget": 900}},                 # not a sale
    {"seq": 6, "ts": NOW - 48 * 3600, "kind": "settle", "data": {"sku": "F1", "price": 90}},             # outside the window
    {"seq": 7, "ts": NOW - 600, "kind": "settle", "data": {"sku": "F1", "price": 0}},                    # no price
]


class LedgerTest(unittest.TestCase):
    def test_settles_normalizes_round3_and_round2_shapes_inside_the_window(self):
        rows = ledger.settles(ENTRIES, now=NOW)
        self.assertEqual(["S1", "S1", "S1", "S2"], [r["sku"] for r in rows])
        self.assertEqual(800.0, rows[0]["price"])

    def test_stats_has_sku_medians_and_category_ratios(self):
        st = ledger.stats(ledger.settles(ENTRIES, now=NOW), CATALOG, now=NOW)
        self.assertEqual(4, st["settles"])
        self.assertEqual({"n": 3, "median": 900}, st["skus"]["S1"])
        self.assertEqual(4, st["categories"]["home>bed"]["n"])
        self.assertAlmostEqual(0.85, st["categories"]["home>bed"]["median_ratio"], places=3)   # median of .8 .9 1.0 .75

    def test_market_price_falls_back_from_sku_to_category_to_nothing(self):
        st = ledger.stats(ledger.settles(ENTRIES, now=NOW), CATALOG, now=NOW)
        self.assertEqual((900.0, 3, "sku"), ledger.market_price(st, "S1", "home>bed", 1000))
        price, n, src = ledger.market_price(st, "S9", "home>bed", 2000)
        self.assertEqual(("category", 4, 1700.0), (src, n, price))
        self.assertEqual((None, 0, "none"), ledger.market_price(st, "F1", "food>kimchi", 100))
        self.assertEqual((None, 0, "none"), ledger.market_price({}, "S1", "home>bed", 1000))

    def test_stats_file_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual({}, ledger.load_stats(d))
            Path(d, ledger.STATS_FILE).write_text(json.dumps({"skus": {"S1": {"n": 5, "median": 10}}}))
            self.assertEqual(5, ledger.load_stats(d)["skus"]["S1"]["n"])


if __name__ == "__main__":
    unittest.main()
