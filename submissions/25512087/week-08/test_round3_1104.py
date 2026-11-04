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


class Seller1104(unittest.TestCase):
    GOODS = {"unit": "g", "sku": "B", "name": "b", "list_price": 100_000, "kind": "goods", "cost": 50_000,
             "supplied_at": s.ROUND_END - 5 * 86400}

    def test_endgame_floor(self):
        self.assertEqual(s.floor_price(self.GOODS, s.ROUND_END - 30 * 3600), 54_375, "normal: 1.05 + 3.75 days storage")
        self.assertEqual(s.floor_price(self.GOODS, s.ROUND_END - 20 * 3600), 50_000, "last 24 h: cost")
        self.assertEqual(s.floor_price(self.GOODS, s.ROUND_END - 3 * 3600), 40_000, "last 6 h: cost x 0.8")


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
