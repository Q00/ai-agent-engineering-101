"""Below-cost rules first built for Welcome Food (strategy v4 draft, dropped by the user on 11/3 for the goods shop,
v5; the rules stay in check.price for any strategy that sets them): below cost only on a buyer's first visit (no sign it dealt with us counts
as first), only food at grade A now up to a list price, at 60% of cost at the lowest; a buyer the fingerprints name
as a confirmed customer gets "no first-visit discount". Hidden demand: buyer favourites from settled deals.

    py -m unittest discover -s tests      # from submissions/26512070/week-08
"""
import os
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

import restock  # noqa: E402
from agents.seller_core import SellerCore  # noqa: E402
from core import atomic, paths  # noqa: E402
from outer import collect  # noqa: E402
from tests.test_calc_check import NOW, STRATEGY, UNITS, Base  # noqa: E402

V4 = dict(STRATEGY, welcome_food={"each_supply_window": True, "share_of_cost": 0.6},
          limits=dict(STRATEGY["limits"], below_cost={
              "allowed": True, "only_in_phase": "exposure", "max_loss_share_of_cost": 0.4,
              "first_deal_only": True, "kinds": ["food"], "max_list_price": 30000, "grades": ["A"]}))


class WelcomeFoodTest(Base):
    def setUp(self):
        super().setUp()
        atomic.write_json(paths.memory() / "strategy" / "active.json", V4)
        shutil.copytree(HERE / "card", paths.root() / "card")

    def test_price_rule(self):
        # u-food-a: food, grade A now, cost 14000, list 23230; u-goods: goods; u-food-c: food, list 36900, grade C now
        self.ctx.situation = {"first_time": True}
        self.assertEqual(self.ok("check.price", unit="u-food-a", price=8400)["lowest_allowed"], 8400)   # 60% of cost
        self.assertIn("lowest allowed price is 8400", self.refused("check.price", unit="u-food-a", price=8399)["hint"])
        self.assertIn("only for ['food']", self.refused("check.price", unit="u-goods", price=2800)["reason"])
        self.assertIn("up to list price 30000", self.refused("check.price", unit="u-food-c", price=9500)["reason"])
        self.ctx.situation = {"first_time": False}
        self.assertIn("no first-visit discount", self.refused("check.price", unit="u-food-a", price=13000)["reason"])
        self.ctx.situation = {}                                           # no sign either way: a first visit
        self.ok("check.price", unit="u-food-a", price=8400)
        self.ok("check.price", unit="u-food-a", price=14000)              # at cost: always fine

    def test_grade_a_only(self):
        units = [dict(UNITS[0], unit="u-food-b", expires_at=NOW + 60 * 3600)]  # 60 of 96 h left: grade B now
        self.write_inventory(units, balance=4_000_000)
        self.ctx.situation = {"first_time": True}
        self.assertIn("grade ['A'] now", self.refused("check.price", unit="u-food-b", price=9000)["reason"])

    def test_seller_marks_known_customers(self):
        os.environ["MARKET_DIR"] = str(self.market)
        try:
            atomic.write_json(paths.memory() / "facts" / "fingerprints.json", {
                "participants": {"kim": {"phrases": ["i want to buy <sku> for <n>"], "deals": 3}}, "accuracy": 0.8})
            atomic.write_json(paths.memory() / "facts" / "live.json", {"customer": {"kim": {"deals_with_us": 3}}})
            seen = []
            core = SellerCore("Test Shop", Ed25519PrivateKey.generate(), clock=lambda: NOW,
                              model_call=lambda p, s, t: seen.append(p) or
                              '{"tool": "act.counter", "args": {"unit": "u-food-a", "price": 14000, "say": "14000 KRW"}}')
            core.handle("t1", "I want to buy G0011e806 for 12000")         # kim's opening: has dealt with us
            self.assertFalse(core.open["t1"]["first_time"])
            self.assertIn("No first-visit offer for this buyer", seen[-1])
            core.handle("t2", "hello, G0026722f please")                    # nobody we know (t1 holds u-food-a)
            self.assertTrue(core.open["t2"]["first_time"])
            self.assertIn("First visit (strategy)", seen[-1])
            core.events.close()
            core.tool_events.close()
        finally:
            os.environ.pop("MARKET_DIR", None)

    def test_supply_window(self):
        self.assertEqual(restock.window({}, NOW), (None, None))                              # no window info
        self.assertEqual(restock.window({"restock": {"open": True, "closes_at": NOW + 120}}, NOW)[0], "open")
        self.assertEqual(restock.window({"restock": {"open": False, "opens_at": NOW + 60}}, NOW)[0], "soon")
        self.assertEqual(restock.window({"restock": {"open": False, "opens_at": NOW + 900}}, NOW)[0], "closed")
        # just before it opens and while it is open is the same window
        soon = restock.window({"restock": {"open": False, "opens_at": NOW + 60}}, NOW)[1]
        open_ = restock.window({"restock": {"open": True, "closes_at": NOW + 60 + 180}}, NOW + 60)[1]
        self.assertLessEqual(abs(soon - open_), 1)

    def test_hidden_demand(self):
        cat = {"G1": {"category": "식품>김치"}, "G2": {"category": "식품>음료"}}
        st, me = collect.empty_state(), {"card_name": "ours", "join_name": "ours"}
        for i, (buyer, sku) in enumerate([("kim", "G1"), ("kim", "G1"), ("kim", "G2"), ("lee", "G2")]):
            collect.fold(st, {"seq": i + 1, "ts": NOW, "kind": "result", "data": {"buyer": buyer, "shop": "x",
                              "receipt": {"shop": "elm", "item": sku, "price": 1, "task": f"t{i}"}}}, me, cat)
        atomic.write_json(paths.memory() / "facts" / "live.json", collect.live(st, cat, NOW))
        kim = self.ok("market.buyer_prefs", buyer="kim")
        self.assertEqual([(f["category"], f["deals"]) for f in kim["favourites"]], [("식품>김치", 2), ("식품>음료", 1)])
        self.assertEqual(self.ok("market.buyer_prefs")["categories_by_buyers"][0], {"category": "식품>음료", "buyers": 2})
        self.assertEqual([r["buyer"] for r in self.ok("market.buyer_prefs", category="김치")["buyers"]], ["kim"])


if __name__ == "__main__":
    unittest.main()
