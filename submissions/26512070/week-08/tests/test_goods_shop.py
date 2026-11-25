"""The goods shop (strategy v5, user 11/3): the tools keep the card's promises - every goods sale carries the 6 h
warranty, a first visit is offered at our cost, only certified units are sold, no food is bought, stock up to 50%.

    py -m unittest discover -s tests      # from submissions/26512070/week-08
"""
import json
import os
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from agents.seller_core import SellerCore  # noqa: E402
from core import atomic, paths  # noqa: E402
from tests.test_calc_check import NOW, UNITS, Base  # noqa: E402

V5 = json.loads((HERE / "card" / "strategy_v5.json").read_text(encoding="utf-8"))
V5["approved"] = "test"
CERT = {"cert": {"unit": "u-goods"}, "sig": "x"}


class GoodsShopTest(Base):
    def setUp(self):
        super().setUp()
        atomic.write_json(paths.memory() / "strategy" / "active.json", V5)
        shutil.copytree(HERE / "card", paths.root() / "card")

    def test_warranty_always_on_goods(self):
        self.ctx.situation = {"unit": "u-goods", "offer": 3300, "ask": 3500}
        self.assertEqual(self.ok("act.accept", say="Deal.")["warranty_hours"], 6)
        self.assertEqual(self.ok("act.accept", say="Deal.", warranty_hours=12)["warranty_hours"], 12)
        self.ctx.situation = {"unit": "u-food-a", "offer": 15000, "ask": 15000}
        self.assertIsNone(self.ok("act.accept", say="Deal.")["warranty_hours"])         # food: not promised

    def test_first_visit_at_cost(self):
        self.ctx.situation = {"unit": "u-goods", "first_time": True}
        self.assertIn("at our cost (3000)", self.refused("act.counter", price=3500, say="3500 KRW")["reason"])
        self.ok("act.counter", price=3000, say="3000 KRW, warranty included")
        self.assertIn("below cost", self.refused("act.counter", price=2900, say="2900 KRW")["reason"])
        self.ctx.situation = {"unit": "u-goods", "first_time": False}
        self.ok("act.counter", price=3800, say="3800 KRW")                               # a returning buyer

    def test_returning_buyer_at_list_and_warranty_reserve(self):
        v9 = json.loads((HERE / "card" / "strategy_v11.json").read_text(encoding="utf-8"))
        atomic.write_json(paths.memory() / "strategy" / "active.json", dict(v9, approved="test"))
        self.ctx.situation = {"unit": "u-goods", "first_time": False, "offer": 4000, "ask": 4800}
        self.assertIn("below 4050 (90% of the list price 4500", self.refused("act.counter", price=4000, say="4400 KRW")["reason"])
        self.ok("act.counter", price=4800, say="4800 KRW")
        self.assertIn("90% of the list price", self.refused("act.accept", say="Deal.")["reason"])   # offer 4000
        self.ok("act.accept", say="Deal.", price=4800)
        self.ctx.situation = {"unit": "u-goods", "first_time": True}
        self.ok("act.counter", price=3000, say="3000 KRW")                    # a first visit stays at cost
        from tools import check
        sold = [{"ts": NOW - 3600, "data": {"price": 1_000_000, "sku": "G0026722f"}},     # goods, in warranty
                {"ts": NOW - 7 * 3600, "data": {"price": 9_000_000, "sku": "G0026722f"}},  # past 6 h
                {"ts": NOW - 60, "data": {"price": 9_000_000, "sku": "G0011e806"}}]        # food: no warranty
        orig = check.index.events
        check.index.events = lambda kinds, since=None, **kw: sold
        try:
            self.assertEqual(check.warranty_reserve(self.ctx), (500_000, 1_000_000, 1))
            self.write_inventory(UNITS, balance=1_600_000)
            r = self.refused("check.buy", plan=[{"sku": "G0026722f", "qty": 1}])
            self.assertIn("warranty reserve 500000", r["reason"])
        finally:
            check.index.events = orig

    def test_without_first_visit_at_cost_every_buyer_starts_from_list(self):
        v13 = json.loads((HERE / "card" / "strategy_v13.json").read_text(encoding="utf-8"))
        atomic.write_json(paths.memory() / "strategy" / "active.json", dict(v13, approved="test"))
        self.ctx.situation = {"unit": "u-goods", "first_time": True}
        self.assertIn("90% of the list price 4500", self.refused("act.counter", price=3000, say="3000 KRW")["reason"])
        self.ok("act.counter", price=4050, say="4050 KRW")

    def test_limits(self):
        self.assertIn("food would be", self.refused("check.buy", plan=[{"sku": "G0011e806", "qty": 1}])["reason"])
        self.ok("check.buy", plan=[{"sku": "G0026722f", "qty": 2}])

    def test_seller_offers_only_certified_units(self):
        units = [dict(UNITS[2]), dict(UNITS[2], unit="u-goods-cert", cert=CERT)]
        self.write_inventory(units, balance=4_000_000)
        os.environ["MARKET_DIR"] = str(self.market)
        try:
            seen = []
            core = SellerCore("Test Shop", Ed25519PrivateKey.generate(), clock=lambda: NOW,
                              model_call=lambda p, s, t: seen.append(p) or
                              '{"tool": "act.counter", "args": {"unit": "u-goods-cert", "price": 3000, "say": "3000 KRW"}}')
            out = core.handle("t1", "I want to buy G0026722f")
            self.assertEqual(out["state"], "input_required")
            self.assertIn("u-goods-cert", seen[0])
            self.assertNotIn("unit u-goods:", seen[0])
            self.assertIn("6 h warranty", seen[0])
            core.events.close()
            core.tool_events.close()
        finally:
            os.environ.pop("MARKET_DIR", None)


if __name__ == "__main__":
    unittest.main()
