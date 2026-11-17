"""11/04 changes (approved): forum posts, restock quantity, stock-miss retry, shelf early walk, no-cert shops,
ledger catch-up, end-of-round floor."""
from __future__ import annotations

import json
import random
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import forum_post
import r3_buyer as rb
import restock
import seller_r3 as s
from test_round3 import CATALOG, DEMAND

NOW = 1_794_000_000


class Restock1104(unittest.TestCase):
    def inv(self, units=()):
        return {"round": 3, "balance": 5_000_000, "start": 5_000_000, "units": list(units)}

    def test_reorder_tops_up_to_recent_sales(self):
        units = [{"unit": f"a{i}", "sku": "S001", "category": "cat1", "cost": 7000} for i in range(4)]
        _, mem = restock.plan(self.inv(units), DEMAND, CATALOG, {"own": {}}, now=NOW)
        buy, mem = restock.plan(self.inv(units[:1]), DEMAND, CATALOG, mem, now=NOW + 60)   # 3 sold
        self.assertAlmostEqual(mem["own"]["S001"], 3, places=2)
        self.assertEqual(buy[0], {"sku": "S001", "qty": 4}, "target ceil(1.5 x 3) = 5, holding 1")

    def test_food_capped_and_hot_qty(self):
        buy, _ = restock.plan(self.inv(), DEMAND, CATALOG, {"own": {"S003": 10}}, now=NOW, rng=random.Random(1),
                              settles=["S071"] * 3)
        d = {b["sku"]: b["qty"] for b in buy}
        self.assertEqual(d["S003"], restock.FOOD_MAX, "food never above FOOD_MAX")
        self.assertEqual(d["S071"], restock.HOT_QTY)

    def test_seed_from_cumulative_sales(self):
        _, mem = restock.plan(self.inv(), DEMAND, CATALOG, {"sold": {"S001": 8}}, now=NOW)
        self.assertEqual(mem["own"]["S001"], 2.0)


class BigTicket1104(unittest.TestCase):
    CAT = {"BED1": {"list_price": 900_000, "category": "가구>침대", "kind": "goods"},
           "BED2": {"list_price": 300_000, "category": "가구>침대", "kind": "goods"},
           "PEN": {"list_price": 2_000, "category": "문구>펜", "kind": "goods"}}
    DEM = [{"category": "가구>침대", "kind": "goods", "median_price": 600_000},
           {"category": "문구>펜", "kind": "goods", "median_price": 2_000}]

    def test_caps_follow_balance_and_big_ticket_first(self):
        inv = {"round": 3, "balance": 15_000_000, "start": 5_000_000, "units": []}
        old = restock.PER_SKU_CAP
        try:
            restock.PER_SKU_CAP = 0.10
            buy, _ = restock.plan(inv, self.DEM, self.CAT, {"own": {}}, now=NOW, rng=random.Random(0),
                                  settles=["BED1"])
        finally:
            restock.PER_SKU_CAP = old
        self.assertEqual(buy[0]["sku"], "BED1", "hot x price first; 0.10 x 15 M = 1.5 M room covers a 900 k bed")
        self.assertLessEqual(buy[0]["qty"], 2, "big ticket: at most 2 units at a time")

    def test_seller_warranty_on_ab_goods_over_min_list(self):
        g = {"unit": "u", "sku": "BED1", "kind": "goods", "grade": "A", "list_price": 900_000}
        self.assertEqual(s.warranty_for(g), 6)
        self.assertIsNone(s.warranty_for(dict(g, grade="C")), "C goods: no warranty")
        self.assertIsNone(s.warranty_for(dict(g, list_price=50_000)), "cheap goods: no warranty")
        self.assertIsNone(s.warranty_for(dict(g, kind="food")))
        self.assertIn("6 h warranty", s.unit_line(dict(g, name="bed")))


class StockCap1105(unittest.TestCase):
    def test_total_stock_follows_selling_rate(self):
        units = [{"unit": f"u{i}", "sku": "S001", "category": "cat1", "cost": 100_000} for i in range(10)]
        inv = lambda us: {"round": 3, "balance": 20_000_000, "start": 5_000_000, "units": us}
        _, mem = restock.plan(inv(units), DEMAND, CATALOG, {"own": {}}, now=NOW)
        # one hour later 2 units (cost 200 k) sold: COGS 200 k/h -> cap 600 k, holding 800 k -> no buying
        buy, mem = restock.plan(inv(units[:8]), DEMAND, CATALOG, mem, now=NOW + 3600, settles=["S071"] * 3)
        self.assertEqual(buy, [])
        self.assertEqual(mem["stock_cap"], 600_000)
        # sales pick up: 6 more sold in the next hour -> COGS 400 k/h over 2 h -> cap 1.2 M > 200 k held -> buys
        buy, mem = restock.plan(inv(units[:2]), DEMAND, CATALOG, mem, now=NOW + 7200, settles=["S071"] * 3)
        self.assertTrue(buy)
        self.assertEqual(mem["stock_cap"], 1_200_000)

    def test_no_cap_without_history(self):
        buy, mem = restock.plan({"round": 3, "balance": 5_000_000, "start": 5_000_000, "units": []}, DEMAND, CATALOG,
                                {"own": {}}, now=NOW, rng=random.Random(1))
        self.assertTrue(buy)
        self.assertNotIn("stock_cap", mem)


class ValueFloor1105(unittest.TestCase):
    def test_floor_is_max_of_cost_and_value(self):
        old = s.PRICING.get("value_floor")
        try:
            s.PRICING["value_floor"] = 0.65
            c = {"unit": "c", "sku": "X", "kind": "goods", "grade": "C", "list_price": 100_000, "cost": 35_000,
                 "supplied_at": NOW}
            self.assertEqual(s.floor_price(c, NOW), 65_000, "C unit: value floor, not cost x 1.05")
            a = dict(c, grade="A", cost=70_000)
            self.assertEqual(s.floor_price(a, NOW), 73_500, "A unit: cost floor is higher")
            self.assertEqual(s.floor_price(c, s.ROUND_END - 3600), 28_000, "endgame still sells at cost x 0.8")
        finally:
            s.PRICING["value_floor"] = old


class Patch35_1106(unittest.TestCase):
    def test_cheapest_shops_first_unknown_in_the_middle(self):
        mem = [{"round": 3, "outcome": "deal", "shop": "a", "price": 80, "value": 100}] * 3 + \
              [{"round": 3, "outcome": "deal", "shop": "b", "price": 60, "value": 100}] * 3
        cands = [{"name": n} for n in ("a", "b", "c", "d")]
        self.assertEqual([c["name"] for c in rb.cheapest_shops(cands, mem, 3)], ["b", "c", "d"])

    def test_surge_skip_and_last_buy(self):
        inv = {"round": 3, "balance": 5_000_000, "start": 5_000_000, "units": [],
               "surge": [{"sku": "S071", "factor": 1.2}]}
        buy, _ = restock.plan(inv, DEMAND, CATALOG, {"own": {}}, now=NOW, rng=random.Random(1), settles=["S071"] * 3)
        self.assertNotIn("S071", [b["sku"] for b in buy], "surged SKU skipped")
        self.assertEqual(restock.plan(inv, DEMAND, CATALOG, {}, now=restock.ROUND_END - 11 * 3600)[0], [])
        self.assertTrue(restock.plan(dict(inv, surge=[]), DEMAND, CATALOG, {}, now=restock.ROUND_END - 20 * 3600,
                                     rng=random.Random(1))[0], "13-20 h before the end still buys (was 36 h)")

    def test_forum_never_votes(self):
        self.assertFalse(forum_post.ALLOW_VOTES)


class Seller1104(unittest.TestCase):
    GOODS = {"unit": "g", "sku": "B", "name": "b", "list_price": 100_000, "kind": "goods", "cost": 50_000,
             "supplied_at": s.ROUND_END - 5 * 86400}

    def test_endgame_floor(self):
        self.assertEqual(s.floor_price(self.GOODS, s.ROUND_END - 30 * 3600), 54_375, "normal: 1.05 + 3.75 days storage")
        self.assertEqual(s.floor_price(self.GOODS, s.ROUND_END - 20 * 3600), 50_000, "last 24 h: cost")
        self.assertEqual(s.floor_price(self.GOODS, s.ROUND_END - 3 * 3600), 40_000, "last 6 h: cost x 0.8")


class Claim1104(unittest.TestCase):
    def test_goods_claim_is_the_fixed_grade(self):
        u = {"unit": "g1", "sku": "B", "kind": "goods", "grade": "B"}
        self.assertEqual(s.claim_for(u, NOW), "B")
        self.assertIsNone(s.claim_for(dict(u, grade="expired"), NOW))
        self.assertIsNone(s.claim_for({"sku": "chair", "r2": True}, NOW), "no claim before Round 3")

    def test_food_claim_only_when_it_holds_through_the_margin(self):
        shelf = 100.0                                         # hours
        food = lambda left_h: {"unit": "f", "sku": "F", "kind": "food", "expires_at": NOW + left_h * 3600}
        self.assertEqual(s.claim_for(food(90), NOW, shelf), "A")
        self.assertEqual(s.claim_for(food(60), NOW, shelf), "B")
        self.assertEqual(s.claim_for(food(20), NOW, shelf), "C")
        self.assertIsNone(s.claim_for(food(80.1), NOW, shelf), "crosses A->B within 15 min: no claim")
        self.assertIsNone(s.claim_for(food(45.2), NOW, shelf), "crosses B->C within 15 min: no claim")
        self.assertIsNone(s.claim_for(food(0.2), NOW, shelf), "about to expire: no claim")
        self.assertIsNone(s.claim_for(food(60), NOW, None) if not s.shelf_hours("F") else None, "shelf unknown: no claim")


class Buyer1104(unittest.TestCase):
    def test_shelf_early_walk(self):
        p = rb.ValueLadderPolicy(20_000, min_hours=48.5)
        p.last_text = "김치 (G1), grade B, 18.9 h shelf life left, market certificate included. Asking 15000."
        self.assertEqual(p.respond("김치", 30_000, 15_000, 0).action, "walk")
        p.last_text = "김치 (G1), grade A, 120.0 h shelf life left. Asking 15000."
        self.assertEqual(p.respond("김치", 30_000, 15_000, 0).action, "offer")
        self.assertEqual(rb.ValueLadderPolicy(20_000).respond("x", 30_000, 15_000, 0).action, "offer", "goods: no check")

    def test_stock_miss_and_no_cert(self):
        for t in ("Sorry, we have none of that in stock.", "No matching unreserved stock", "No matching stock available."):
            self.assertTrue(rb.STOCK_MISS.search(t), t)
        self.assertFalse(rb.STOCK_MISS.search("Our lowest price: 5000"))
        with TemporaryDirectory() as d:
            p = Path(d) / "payments.jsonl"
            p.write_text("\n".join(json.dumps(x) for x in [
                {"shop": "https://a/", "paid": False, "reason": "not paying: no certificate, and the policy asks for one"},
                {"shop": "https://b", "paid": False, "reason": "not paying: 3.2 h of shelf life left"},
                {"shop": "https://c", "paid": True}]))
            self.assertEqual(rb.no_cert_shops(p), {"https://a"})


class ShelfShops1104(unittest.TestCase):
    def test_short_shelf_shops_need_strikes(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "payments.jsonl"
            r = lambda shop, reason: json.dumps({"shop": shop, "paid": False, "reason": reason})
            p.write_text("\n".join([r("https://a", "not paying: 3.2 h of shelf life left, the policy asks for 12.5")] * 2
                                   + [r("https://b", "not paying: 70.0 h of shelf life left, the policy asks for 84.5")]
                                   + [r("https://c", "not paying: no certificate, and the policy asks for one")] * 3))
            self.assertEqual(rb.short_shelf_shops(p, 2), {"https://a"})
            self.assertEqual(rb.short_shelf_shops(p, 1), {"https://a", "https://b"})


class Tune1104(unittest.TestCase):
    def metrics(self, paid, refused):
        return {"window_hours": 3, "shop_now": {"categories": 0, "balance": 1e7, "stock_units": 100},
                "window": {"shop": {"openings": 0}, "buyer": {"talks": 100, "outcomes": {},
                           "payments": {"paid": paid, "refused": refused, "mean_price_share": 0.8}}}}

    def test_rung_rule_retired_and_refusal_rule(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent / "improve"))
        import tune
        pol = {k: dict(v) for k, v in tune.DEFAULTS.items()}
        ch = tune.propose(self.metrics(60, 40), pol)
        self.assertEqual([(c["key"], c["new"]) for c in ch if c["section"] == "buyer"], [("shelf_strikes", 1)])
        ch = tune.propose(self.metrics(95, 5), pol)
        self.assertEqual([(c["key"], c["new"]) for c in ch if c["section"] == "buyer"], [("shelf_strikes", 3)])
        ch = tune.propose(self.metrics(85, 15), pol)
        self.assertFalse([c for c in ch if c["section"] == "buyer"], "no rung_1 cut any more")


class Forum1104(unittest.TestCase):
    def setup_dir(self, d):
        md, log = Path(d) / ".market", Path(d) / "market-kit.log"
        md.mkdir()
        (md / "catalog.json").write_text(json.dumps({f"G{i}": {"list_price": 10_000, "category": "식품>김치"} for i in range(6)}))
        (md / "payments.jsonl").write_text("\n".join(json.dumps({"item": f"G{i}", "paid": True, "ledger": 100 + i}) for i in range(6)))
        (md / "buyer-memory.jsonl").write_text(json.dumps({"url": "https://shop-a", "shop": "shop-a"}))
        log.write_text("\n".join(f"10:00:0{i} [acp] paid {6000 + 100 * i} through https://shop-a/acp, settled at ledger "
                                 f"#{100 + i}: 김치 {i}, grade A, 80.0 h of shelf life left" for i in range(6)))
        return md, log

    def test_report_then_rate_limit_then_stats(self):
        with TemporaryDirectory() as d:
            md, log = self.setup_dir(d)
            p = forum_post.maybe_post(md, log, now=NOW)
            self.assertIn("[구매 기록]", p["post"]); self.assertEqual(p["refs"], [103, 104, 105]); self.assertEqual(p["as"], "buyer")
            self.assertIn("정가 10,000원의 65%", p["post"])
            self.assertIsNone(forum_post.maybe_post(md, log, now=NOW + 60), "outbox not sent yet / 5 min gap")
            (md / "outbox.jsonl").write_text("")
            self.assertIsNone(forum_post.maybe_post(md, log, now=NOW + 100), "5 min gap")
            p2 = forum_post.maybe_post(md, log, now=NOW + 400)
            self.assertEqual(p2["refs"], [100, 101, 102], "only deals not posted yet")
            (md / "outbox.jsonl").write_text("")
            p3 = forum_post.maybe_post(md, log, now=NOW + 800)
            self.assertIn("[시세]", p3["post"]); self.assertIn("김치: 중앙값", p3["post"])

    def test_post_taken_by_shop_kit_is_retried_as_buyer(self):
        with TemporaryDirectory() as d:
            md, log = self.setup_dir(d)
            p = forum_post.maybe_post(md, log, now=NOW)
            (md / "outbox.jsonl").write_text("")             # the kit took the line ...
            with log.open("a") as f:
                f.write("\n10:01:00 [forum] post as shop: 200\n")   # ... but it was our shop's kit
            p2 = forum_post.maybe_post(md, log, now=NOW + 30)
            self.assertEqual(p2["refs"], p["refs"], "same deals again, right away")
            (md / "outbox.jsonl").write_text("")
            with log.open("a") as f:
                f.write("10:02:00 [forum] post as buyer: 200\n")
            self.assertIsNone(forum_post.maybe_post(md, log, now=NOW + 60), "buyer posted: wait 5 min")
            st = json.loads((md / "memory" / "forum.json").read_text())
            self.assertEqual((st["as_shop"], st["as_buyer"]), (1, 1))

    def test_own_shop_deals_are_not_posted(self):
        with TemporaryDirectory() as d:
            md, log = self.setup_dir(d)
            (md / "buyer-memory.jsonl").write_text(json.dumps({"url": "https://shop-a", "shop": "ggari-shop"}))
            self.assertIsNone(forum_post.maybe_post(md, log, now=NOW))


if __name__ == "__main__":
    unittest.main()
