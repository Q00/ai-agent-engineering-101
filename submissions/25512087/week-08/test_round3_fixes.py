"""Week 08 fixes 1, 3, 4 (11/02): Round 3 buyer value ladder, restock from the ledger, floor with holding cost."""
from __future__ import annotations

import json
import random
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import r3_buyer as rb
import restock
import seller_r3 as s
from buyer import check_receipt
from negotiation import parse_price
from test_round3 import CATALOG, DEMAND

NOW = 1_794_000_000
ORDER = {"order": "o-1", "item": "침대", "category": "가구>침대", "budget": 700_000, "kind": "goods",
         "acceptable": ["B1", "B2", "B3"]}
CAT = {"B1": {"list_price": 600_000, "category": "가구>침대"}, "B2": {"list_price": 900_000, "category": "가구>침대"},
       "B3": {"list_price": 1_200_000, "category": "가구>침대"}}


def shop(name, *skus):
    return {"name": name, "url": f"https://{name}", "offers": [{"sku": k, "name": k, "qty": 2} for k in skus]}


class BuyerFix1(unittest.TestCase):
    def test_pick_most_surplus_within_budget(self):
        self.assertEqual(rb.best_offer(shop("a", "B1", "B2", "B3"), ORDER, CAT)["sku"], "B2",
                         "B3 is worth most but 70 % of it is over the budget")
        self.assertIsNone(rb.best_offer(shop("a", "X9"), ORDER, CAT), "no acceptable SKU")
        self.assertEqual(rb.best_offer(shop("a", "B3"), ORDER, CAT)["sku"], "B3", "nothing fits: long shot")

    def test_shortlist_keeps_shops_near_the_best(self):
        names = [p["name"] for p in rb.shortlist([shop("a", "B1"), shop("b", "B2"), shop("c")], ORDER, CAT)]
        self.assertEqual(names, ["b"], "600k is under 80 % of the best 900k; c has nothing")

    def test_ladder_anchored_on_value_never_above_cap(self):
        p = rb.ValueLadderPolicy(900_000)
        self.assertEqual(p.ladder(700_000), [450_000, 450_000, 540_000, 630_000, 700_000])
        self.assertEqual(rb.ValueLadderPolicy(100_000).ladder(10**9)[-1], 92_000, "92 % of value")
        p = rb.ValueLadderPolicy(100_000)
        self.assertEqual(p.respond("x", 10**9, 90_000, 0).price, 50_000)
        self.assertEqual(p.respond("x", 10**9, 64_000, 1).price, 64_000, "within 10 % of the next rung: take it")
        q = rb.ValueLadderPolicy(100_000)
        self.assertEqual(q.respond("x", 10**9, 95_000, 6).action, "walk", "above value cap after the ladder")

    def test_accept_terms_and_opening(self):
        self.assertEqual(rb.accept_terms(ORDER), {"cert": True})
        self.assertEqual(rb.accept_terms({"kind": "food", "use_within_hours": 6}), {"cert": True, "min_hours_left": 6.5})
        self.assertEqual(rb.ask_text({"name": "포기김치", "sku": "G1"}), "I want to buy 포기김치 (G1)")

    def test_receipt_sku_and_big_prices(self):
        rec = {"item": "B2", "price": 600_000, "sig_seller": "x"}
        self.assertIsNone(check_receipt(rec, "침대", 700_000, 600_000, allowed={"B1", "B2"}))
        self.assertIn("names", check_receipt(rec, "침대", 700_000, 600_000))
        self.assertEqual(parse_price("Our price: 1250000."), (1_250_000, None))

    def test_r3_order_only_with_acceptable(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "order.json"
            p.write_text(json.dumps({"item": "lamp", "budget": 120}))
            self.assertIsNone(rb.r3_order(p))
            p.write_text(json.dumps(ORDER))
            self.assertEqual(rb.r3_order(p)["item"], "침대")


class RestockFix3(unittest.TestCase):
    def inv(self, units=()):
        return {"round": 3, "balance": 5_000_000, "start": 5_000_000, "units": list(units)}

    def test_hot_counts_decay(self):
        mem = {"hot": restock.update_hot({}, ["S010", "S010", "S040"], CATALOG, NOW)}
        self.assertEqual(mem["hot"]["sku"]["S010"], 2)
        self.assertEqual(mem["hot"]["cat"]["cat10"], 3, "S040 is cat10 too")
        later = restock.update_hot(mem, [], CATALOG, NOW + restock.HOT_HALF_LIFE_H * 3600)
        self.assertAlmostEqual(later["sku"]["S010"], 1.0)

    def test_market_hot_skus_first_then_hot_categories(self):
        buy, mem = restock.plan(self.inv(), DEMAND, CATALOG, {}, now=NOW, rng=random.Random(1),
                                settles=["S071"] * 3 + ["S052"] * 2 + ["S022"])
        self.assertEqual([b["sku"] for b in buy[:3]], ["S071", "S052", "S022"], "momentum, hottest first")
        self.assertEqual(buy[0]["qty"], 2, "goods: 2 units")
        cats = [CATALOG[b["sku"]]["category"] for b in buy[3:]]
        self.assertNotIn("cat11", cats, "cat11 (S071) is covered by the hot line")
        self.assertIn("hot", mem)

    def test_main_reads_no_network_before_round3(self):
        self.assertEqual(restock.plan(dict(self.inv(), round=2), DEMAND, CATALOG, {}, settles=["S001"])[0], [])


class SellerFix4(unittest.TestCase):
    GOODS = {"unit": "g1", "sku": "B2", "name": "bed", "list_price": 900_000, "kind": "goods", "cost": 500_000,
             "grade": "A", "supplied_at": NOW}

    def test_floor_adds_storage_held(self):
        self.assertEqual(s.floor_price(self.GOODS, NOW), 525_000)
        self.assertEqual(s.floor_price(self.GOODS, NOW + 10 * 86400), 575_000, "10 days: +10 %")
        self.assertEqual(s.opening_ask(self.GOODS, NOW), 828_000)

    def test_food_markdown_in_last_hours(self):
        food = {"unit": "f", "sku": "F", "name": "f", "list_price": 20_000, "kind": "food", "cost": 10_000,
                "supplied_at": NOW - 86400, "expires_at": NOW + 3600}
        self.assertEqual(s.floor_price(food, NOW), 5_000, "1 h left: half the cost beats throwing it away")
        self.assertEqual(s.floor_price(food, NOW - 7200), 10_500, "3 h left: cost x 1.05, no storage for food")


if __name__ == "__main__":
    unittest.main()
