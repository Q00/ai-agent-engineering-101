"""Week-8 pricing change: the shop maximizes profit, and the buyer avoids shops that cannot settle in Round 3.

    python -m unittest test_profit_strategy -v        (from submissions/26622007/week-08)

- objective "profit": an arm whose deals close at the reserve earns nothing, so an arm that sells less often
  but above the reserve wins; objective "revenue" (weeks 6-7) still prefers the arm that sells most;
- the profit score uses the price deals really closed at, not the opening ask;
- Round 3: the buyer chooses again when the chosen shop serves no /.well-known/acp.json; Round 2 does not check.
"""
import collections
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import market_buyer      # noqa: E402
import shop_memory       # noqa: E402

ARMS = [0.0, 0.03, 0.06, 0.1, 0.15, 0.2]


def negotiation(arm, result, price=None, reserve=100):
    return {"item": "lamp", "reserve": reserve, "open": round(reserve * (1 + arm)), "arm": arm, "task": "t",
            "result": result, "price": price, "offers": [reserve]}


class OpeningTest(unittest.TestCase):
    def pick(self, negotiations, objective, draws=300):
        with tempfile.TemporaryDirectory() as d:
            mem, pricing = os.path.join(d, "m.json"), os.path.join(d, "p.json")
            for path, data in ((mem, {"negotiations": negotiations}),
                               (pricing, {"objective": objective, "arms": ARMS, "gamma": 1.0, "lambda": 0,
                                          "lambda_low_share": 0})):
                with open(path, "w") as f:
                    json.dump(data, f)
            with mock.patch.object(shop_memory, "MEMORY", mem), mock.patch.object(shop_memory, "PRICING", pricing), \
                    mock.patch.object(shop_memory, "market_means", lambda: {}), \
                    mock.patch.object(shop_memory, "review_stars", lambda: {}):
                return collections.Counter(shop_memory.choose_opening("lamp", 100)[1] for _ in range(draws))

    def history(self):
        h = []
        for arm in ARMS:                            # every arm tried 40 times
            for i in range(40):
                if arm == 0.0:                      # 90% deals, all at the reserve
                    h.append(negotiation(arm, "deal", 100) if i < 36 else negotiation(arm, "rejected"))
                elif arm == 0.1:                    # 60% deals, at 110
                    h.append(negotiation(arm, "deal", 110) if i < 24 else negotiation(arm, "rejected"))
                elif arm == 0.2:                    # 25% deals, at 120
                    h.append(negotiation(arm, "deal", 120) if i < 10 else negotiation(arm, "rejected"))
                else:                               # 70% deals, but the buyer talked them down to the reserve
                    h.append(negotiation(arm, "deal", 100) if i < 28 else negotiation(arm, "rejected"))
        return h

    def test_profit_prefers_margin_over_volume(self):
        picks = self.pick(self.history(), "profit")
        self.assertEqual(picks.most_common(1)[0][0], 0.1, picks)     # 0.6 * 10 beats 0.25 * 20 and 0.9 * 0
        self.assertLess(picks[0.0], 10, picks)

    def test_profit_counts_the_price_deals_closed_at(self):
        picks = self.pick(self.history(), "profit")
        self.assertLess(picks[0.03] + picks[0.06] + picks[0.15], 30, picks)   # opened high, closed at the reserve

    def test_revenue_objective_keeps_the_old_behavior(self):
        picks = self.pick(self.history(), "revenue")
        self.assertNotIn(picks.most_common(1)[0][0], (0.15, 0.2), picks)

    def test_untried_arms_get_explored(self):
        picks = self.pick([negotiation(0.0, "deal", 100) for _ in range(200)], "profit")
        self.assertGreater(sum(v for k, v in picks.items() if k > 0), 250, picks)


class ConcessionTest(unittest.TestCase):
    def floors(self, concession, opened, reserve, rounds=4):
        with tempfile.TemporaryDirectory() as d:
            pricing = os.path.join(d, "p.json")
            with open(pricing, "w") as f:
                json.dump({} if concession is None else {"concession": concession}, f)
            with mock.patch.object(shop_memory, "PRICING", pricing):
                return [shop_memory.concession_floor(opened, reserve, r) for r in range(1, rounds + 1)]

    def test_steps_down_to_the_reserve(self):
        self.assertEqual(self.floors([0.6, 0.3, 0.0], 120, 100), [112, 106, 100, 100])
        self.assertEqual(self.floors([0.6, 0.3, 0.0], 216, 180), [202, 191, 180, 180])

    def test_default_is_the_weeks_6_7_schedule(self):
        self.assertEqual(self.floors(None, 120, 100), [100, 100, 100, 100])

    def test_never_below_the_reserve(self):
        self.assertEqual(self.floors([0.5], 100, 100), [100, 100, 100, 100])

    def test_food_keeps_its_own_schedule(self):
        # 메인 01:25 (가게 협상 점검 2번): round 1 gave 70% of the margin away (Gb2392fa9 18,788 -> 15,000 at once);
        # food first, its paid and walk-away rates measured against the rest
        with tempfile.TemporaryDirectory() as d:
            pricing = os.path.join(d, "p.json")
            with open(pricing, "w") as f:
                json.dump({"concession": [0.3, 0.2, 0.1], "concession_food": [0.7, 0.4, 0.15]}, f)
            with mock.patch.object(shop_memory, "PRICING", pricing):
                food = [shop_memory.concession_floor(200, 100, r, "food") for r in range(1, 5)]
                goods = [shop_memory.concession_floor(200, 100, r, "goods") for r in range(1, 5)]
                self.assertEqual(food, [170, 140, 115, 115])
                self.assertEqual(goods, [130, 120, 110, 110])
                self.assertEqual(shop_memory.concession_floor(200, 100, 1), 130)
            for bad in ("x", [], [2.0], [True], [-0.1]):                        # a bad food schedule: the usual one
                with open(pricing, "w") as f:
                    json.dump({"concession": [0.3, 0.2, 0.1], "concession_food": bad}, f)
                with mock.patch.object(shop_memory, "PRICING", pricing):
                    self.assertEqual(shop_memory.concession_floor(200, 100, 1, "food"), 130, bad)
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["concession_food"], [0.7, 0.4, 0.15])

    def test_the_seller_uses_it(self):
        src = open(os.path.join(HERE, "seller.py"), encoding="utf-8").read()
        self.assertIn("shop_memory.concession_floor(deal.get(\"open\", deal[\"ask\"]), deal[\"reserve\"], deal[\"rounds\"], deal.get(\"kind\"))", src)
        self.assertIn("price_guards(deal, action, price, offer, turn_floor(deal))", src)
        self.assertNotIn("cap = reserve   # first counter goes straight to the reserve", src)
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["concession"], [0.3, 0.2, 0.1])      # never down to F; GPT Pro 11/06 16:45 §3.2


class AcpChoiceTest(unittest.TestCase):
    SHOPS = [{"name": "no-acp", "url": "https://a.example"}, {"name": "has-acp", "url": "https://b.example"}]

    def run_main(self, rnd):
        chosen = []

        def choose(shops, memory, item, budget):
            s = shops[0]
            chosen.append(s["name"])
            return s, "test"

        with tempfile.TemporaryDirectory() as d:
            shops, mem = os.path.join(d, "shops.json"), os.path.join(d, "mem.json")
            with open(shops, "w") as f:
                json.dump(self.SHOPS, f)
            out = {"state": "TASK_STATE_REJECTED", "task": None, "receipt": None, "history": [], "agreed": None,
                   "holds": 0}
            with mock.patch.object(market_buyer, "MEMORY", mem), \
                    mock.patch.object(market_buyer, "market_round", lambda: rnd), \
                    mock.patch.object(market_buyer, "serves_acp", lambda url: url == "https://b.example"), \
                    mock.patch.object(market_buyer, "choose", choose), \
                    mock.patch.object(market_buyer, "negotiate", lambda *a: out) as neg, \
                    mock.patch.object(sys, "argv", ["market_buyer.py", "--shops", shops, "--item", "lamp",
                                                    "--budget", "120"]), \
                    mock.patch("builtins.print"):
                market_buyer.main()
        return chosen

    def test_round_3_skips_a_shop_without_acp(self):
        self.assertEqual(self.run_main(3), ["no-acp", "has-acp"])

    def test_round_2_does_not_check(self):
        self.assertEqual(self.run_main(2), ["no-acp"])


if __name__ == "__main__":
    unittest.main()
