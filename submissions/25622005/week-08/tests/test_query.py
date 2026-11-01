"""memory/query.py: the read API other roles call. Neutral defaults, never exceptions."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

W = Path(__file__).resolve().parents[1]
os.environ.setdefault("R3_HOME", tempfile.mkdtemp(prefix="r3-query-"))
sys.path.insert(0, str(W))

from memory import query  # noqa: E402

SUMMARY = {
    "buyer": {"shops": {"https://a.example": {"attempts": 5, "paid": 3, "refused": {"no certificate": 2},
                                              "bind_mismatch": 1, "broken_confirmed": 1, "broken_inferred": 2}},
              "category": {"김치": {"uwh": {"n": 4, "median": 12.5},
                                   "budget_ratio": {"n": 4, "p25": 0.9, "p50": 1.1}, "q": {"n": 40, "upper": 0.08}}}},
    "seller": {"skus": {"G1": {"settled": 7, "last_sale_ts": 1794000000.0, "sales_per_h": 0.5}},
               "regular_cats": ["김치", "우유/두유"]},
    "market": {"categories": {"김치": {"demand": 3.0, "supply": None}, "침대": {"demand": 1, "supply": 4}}},
}


class QueryTest(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(query.shop_stats(SUMMARY, "https://a.example/"),
                         {"attempts": 5, "paid": 3, "refused": {"no certificate": 2}, "bind_mismatch": 1,
                          "broken_confirmed": 1, "broken_inferred": 2})
        c = query.category_stats(SUMMARY, "식품>김치")
        self.assertEqual(c["uwh"], {"n": 4, "median": 12.5})
        self.assertEqual(c["budget_ratio"]["p50"], 1.1)
        self.assertEqual(c["q"], {"n": 40, "upper": 0.08})
        self.assertEqual(query.sku_stats(SUMMARY, "G1"), {"settled": 7, "last_sale_ts": 1794000000.0,
                                                          "sales_per_h": 0.5})
        self.assertEqual(query.regular_categories(SUMMARY), ["김치", "우유/두유"])
        self.assertEqual(query.supply_demand(SUMMARY), {"김치": {"demand": 3.0, "supply": None},
                                                        "침대": {"demand": 1.0, "supply": 4}})

    def test_unknown_and_broken_inputs_give_neutral_defaults(self):
        for s in ({}, None, {"buyer": [1]}, {"seller": "x", "market": {"categories": [1]}}):
            self.assertEqual(query.shop_stats(s, "https://b"), {"attempts": 0, "paid": 0, "refused": {},
                                                                "bind_mismatch": 0, "broken_confirmed": 0,
                                                                "broken_inferred": 0})
            self.assertEqual(query.category_stats(s, "x"), {"uwh": {"n": 0, "median": None},
                                                            "budget_ratio": {"n": 0, "p25": None, "p50": None},
                                                            "q": {"n": 0, "upper": None}})
            self.assertEqual(query.sku_stats(s, "G9"), {"settled": 0, "last_sale_ts": None, "sales_per_h": 0.0})
            self.assertEqual(query.regular_categories(s), [])
            self.assertEqual(query.supply_demand(s), {})

    def test_load_summary_missing_or_broken(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "summary.json"
            with mock.patch.object(query, "SUMMARY", p):
                self.assertEqual(query.load_summary(), {})
                p.write_text("{not json", encoding="utf-8")
                self.assertEqual(query.load_summary(), {})
                p.write_text("[1, 2]", encoding="utf-8")
                self.assertEqual(query.load_summary(), {})
                p.write_text(json.dumps(SUMMARY), encoding="utf-8")
                self.assertEqual(query.load_summary()["seller"]["regular_cats"], ["김치", "우유/두유"])

    def test_summary_ref(self):
        self.assertEqual(query.summary_ref("shop", "https://a.example/"), "shop:https://a.example")
        self.assertEqual(query.summary_ref("cat", "식품>김치"), "cat:김치")
        self.assertEqual(query.summary_ref("category", "김치"), "cat:김치")
        self.assertEqual(query.summary_ref("sku", "G1"), "sku:G1")


if __name__ == "__main__":
    unittest.main()
