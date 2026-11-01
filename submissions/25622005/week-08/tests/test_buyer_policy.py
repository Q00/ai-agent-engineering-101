"""Buyer rules as pure functions: caps, surplus, chance of closing, candidates, accept, rating."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

W = Path(__file__).resolve().parents[1]
os.environ.setdefault("R3_TEST_HOME", tempfile.mkdtemp(prefix="r3-test-"))
os.environ["R3_HOME"] = os.environ["R3_TEST_HOME"]
sys.path.insert(0, str(W))

import policy  # noqa: E402
from tools import buyer_policy as bp  # noqa: E402

POL = json.loads(json.dumps(policy.DEFAULTS))
CATALOG = {
    "F1": {"name": "김치 A", "category": "식품>김치", "list_price": 30000, "kind": "food", "shelf_hours": 720},
    "F2": {"name": "김치 B", "category": "식품>김치", "list_price": 40000, "kind": "food", "shelf_hours": 720},
    "F3": {"name": "김치 C", "category": "식품>김치", "list_price": 20000, "kind": "food", "shelf_hours": 720},
    "G1": {"name": "침대", "category": "가구>침대", "list_price": 100000, "kind": "goods"},
}
FOOD = {"order": "o-f", "item": "김치", "category": "식품>김치", "budget": 50000, "acceptable": ["F1", "F2", "F3"],
        "kind": "food", "use_within_hours": 10, "round": 3}
GOODS = {"order": "o-g", "item": "침대", "category": "가구>침대", "budget": 120000, "acceptable": ["G1"],
         "kind": "goods", "round": 3}


def shop(url, *offers, name=None):
    return {"name": name or url, "url": url, "reviews": [],
            "offers": [{"sku": s, "name": CATALOG.get(s, {}).get("name", s), "qty": q} for s, q in offers]}


class CapTests(unittest.TestCase):
    def test_d6_shares(self):
        self.assertEqual(bp.cap(30000, "food", 0, None, 99999, POL), 28500)
        self.assertEqual(bp.cap(100000, "goods", 0, None, 999999, POL), 70000)
        self.assertEqual(bp.cap(100000, "goods", 6, None, 999999, POL), 94000)
        self.assertEqual(bp.cap(100000, "goods", 0, 0.10, 999999, POL), 85000)

    def test_budget_bounds_the_cap(self):
        self.assertEqual(bp.cap(100000, "food", 0, None, 40000, POL), 40000)

    def test_expected_surplus_matches_strategy_table(self):
        self.assertAlmostEqual(bp.expected_surplus(100000, 50000, "goods", 0.30, 0), 20000)
        self.assertAlmostEqual(bp.expected_surplus(100000, 60000, "goods", 0.05, 6), 38000)
        self.assertAlmostEqual(bp.expected_surplus(100000, 50000, "goods", 0.30, 6), 35000)
        self.assertEqual(bp.expected_surplus(30000, 25000, "food", 0.9, 0), 5000)

    def test_p_success(self):
        fresh = bp.p_success({"attempts": 0, "paid": 0}, None, 70000, 100000)
        self.assertEqual((fresh["p_price"], fresh["p_checkout"], fresh["p"]), (1.0, 0.9, 0.9))
        bad = bp.p_success({"attempts": 10, "paid": 1}, 77500, 70000, 100000)
        self.assertEqual((bad["p_price"], bad["p_checkout"], bad["p"]), (0.5, 0.5, 0.25))
        self.assertEqual(bp.p_success({}, 90000, 70000, 100000)["p"], 0.0)


class CandidateTests(unittest.TestCase):
    def test_offers_intersect_acceptable_one_per_shop(self):
        shops = [shop("http://a", ("F1", 2), ("F2", 1), ("X9", 3)), shop("http://b", ("F3", 1)),
                 shop("http://c"), shop("http://d", ("F2", 0))]
        cs = bp.candidates(FOOD, shops, CATALOG, {}, POL)
        self.assertEqual([(c["shop"], c["sku"]) for c in cs], [("http://a", "F2"), ("http://b", "F3")])
        self.assertEqual(cs[0]["cap"], 38000)
        self.assertIn("shop:http://a", cs[0]["memory_refs"])
        self.assertIn("cat:김치", cs[0]["memory_refs"])

    def test_max_shops_and_shop_record_ranks(self):
        shops = [shop(f"http://s{i}", ("F1", 1)) for i in range(5)]

        def stats(summary, url):
            bad = url == "http://s0"
            return {"attempts": 10 if bad else 0, "paid": 0, "refused": {}, "bind_mismatch": 0,
                    "broken_confirmed": 0, "broken_inferred": 0}
        with patch.object(bp.query, "shop_stats", side_effect=stats):
            cs = bp.candidates(FOOD, shops, CATALOG, {}, POL)
        self.assertEqual(len(cs), 3)
        self.assertNotIn("http://s0", [c["shop"] for c in cs])

    def test_goods_use_measured_break_rate_from_enough_observations(self):
        cat = {"uwh": {"n": 0, "median": None}, "budget_ratio": {"n": 0, "p25": None, "p50": None},
               "q": {"n": 40, "upper": 0.10}}
        with patch.object(bp.query, "category_stats", return_value=cat):
            c = bp.candidates(GOODS, [shop("http://a", ("G1", 1))], CATALOG, {}, POL)[0]
        self.assertEqual(c["cap"], 85000)
        c = bp.candidates(GOODS, [shop("http://a", ("G1", 1))], CATALOG, {}, POL)[0]
        self.assertEqual((c["cap"], c["q"]), (70000, bp.q_prior(POL)))

    def test_q_prior_follows_the_policy_cap(self):
        pol = json.loads(json.dumps(POL))
        pol["buyer"]["cap_goods_no_warranty"] = 0.75     # a raised D6 cap must not drop every goods offer
        c = bp.candidates(GOODS, [shop("http://a", ("G1", 1))], CATALOG, {}, pol)
        self.assertEqual([x["cap"] for x in c], [75000])

    def test_reference_list(self):
        self.assertEqual(bp.reference_list(FOOD, CATALOG), 30000)


class AcceptAndReceiptTests(unittest.TestCase):
    def setUp(self):
        self.food = bp.candidates(FOOD, [shop("http://a", ("F1", 1))], CATALOG, {}, POL)[0]
        self.goods = bp.candidates(GOODS, [shop("http://a", ("G1", 1))], CATALOG, {}, POL)[0]

    def test_food_accept(self):
        rec = {"item": "F1", "price": 25000, "unit": "u7", "sig_seller": "x"}
        a = bp.build_accept(FOOD, self.food, rec, "u7", 0, POL)
        self.assertEqual(a, {"cert": True, "min_hours_left": 11.0,
                             "bind": {"max_price_list": 0.95, "sku": "F1", "unit": "u7"}})

    def test_goods_accept_with_warranty_and_no_binding(self):
        p = json.loads(json.dumps(POL))
        p["buyer"]["binding"] = False
        a = bp.build_accept(GOODS, self.goods, {"item": "G1", "price": 90000}, None, 6, p)
        self.assertEqual(a, {"cert": True, "warranty_hours": 6})

    def test_category_receipt_binds_by_list_share_only(self):
        a = bp.build_accept(GOODS, self.goods, {"item": "침대", "price": 60000}, None, 0, POL)
        self.assertEqual(a["bind"], {"max_price_list": 0.70})

    def test_verify_receipt(self):
        ok = {"item": "F1", "price": 25000, "unit": "u7", "sig_seller": "x"}
        self.assertIsNone(bp.verify_receipt(FOOD, ok, self.food, CATALOG, POL, "u7"))
        cases = [({}, "no signed receipt"), (dict(ok, item="Z1"), "neither"), (dict(ok, price=25000.5), "whole"),
                 (dict(ok, price=60000), "limit"), (dict(ok, price=29000), "cap"),
                 ({k: v for k, v in ok.items() if k != "unit"}, "promised unit")]
        for rec, why in cases:
            with self.subTest(why=why):
                self.assertIn(why, bp.verify_receipt(FOOD, rec, self.food, CATALOG, POL, "u7"))

    def test_cap_of_the_shipped_sku_not_the_asked_one(self):
        rec = {"item": "F3", "price": 25000, "sig_seller": "x"}     # F3 lists at 20000
        self.assertIn("above the cap 19000", bp.verify_receipt(FOOD, rec, self.food, CATALOG, POL))

    def test_goods_warranty_cap_only_with_warranty(self):
        rec = {"item": "G1", "price": 90000, "sig_seller": "x"}
        self.assertIsNotNone(bp.verify_receipt(GOODS, rec, self.goods, CATALOG, POL))
        self.assertIsNone(bp.verify_receipt(GOODS, rec, self.goods, CATALOG, POL, warranty_h=6))


class RateDealTests(unittest.TestCase):
    ROW = {"id": "d-buyer-1", "choice": {"sku": "F1", "unit": "u7", "V": 30000, "name": "김치 A"}}
    RECEIPT = {"shop": "s", "item": "F1", "price": 22000, "task": "t", "unit": "u7", "sig_seller": "x"}

    def deal(self, **settled):
        s = {"shop": "http://a", "unit": "u7", "sku": "F1", "name": "김치 A", "grade": "A", "claim": None,
             "claim_honest": None, "value": 30000, "ledger": 5}
        s.update(settled)
        return {"order": dict(FOOD), "receipt": self.RECEIPT, "settled": s}

    def test_settled_as_agreed(self):
        stars, review = bp.rate_deal(self.deal(), self.ROW)
        self.assertEqual(stars, 5)
        self.assertIn("paid 22000 for list 30000", review)
        self.assertIn("unit u7 as named", review)
        self.assertNotIn(str(FOOD["budget"]), review)

    def test_price_bands(self):
        for price, stars in [(28000, 4), (29500, 3)]:
            with self.subTest(price=price):
                d = self.deal()
                d["receipt"] = dict(self.RECEIPT, price=price)
                self.assertEqual(bp.rate_deal(d, self.ROW)[0], stars)

    def test_problems(self):
        self.assertEqual(bp.rate_deal(self.deal(unit="u8"), self.ROW)[0], 1)
        self.assertEqual(bp.rate_deal(self.deal(sku="F2"), self.ROW)[0], 1)
        stars, review = bp.rate_deal(self.deal(claim="A", claim_honest=False, grade="B"), self.ROW)
        self.assertEqual(stars, 2)
        self.assertIn("claimed grade A", review)
        self.assertEqual(bp.rate_deal(self.deal(value=0), self.ROW)[0], 2)

    def test_without_decision_row_uses_settled_value(self):
        self.assertEqual(bp.rate_deal(self.deal(), None)[0], 5)

    def test_provisional(self):
        stars, review = bp.rate_deal(self.RECEIPT, self.ROW, provisional=True)
        self.assertEqual(stars, 5)
        self.assertIn("pending settlement", review)


if __name__ == "__main__":
    unittest.main()
