"""Synthetic horizon evidence: selection preferences never authorize payment."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest

from buyer_food_horizon import filter_recent_short_life, prioritize_history
from buyer_policy import load_policy
from buyer_selection import shortlist
from buyer_fast_selection import value_choice, exploration_turn


class FoodHorizonTests(unittest.TestCase):
    def setUp(self):
        self.config = load_policy()
        self.order = {"round": 3, "kind": "food", "use_within_hours": 360}
        self.shops = [{"name": "ours", "url": "https://ours.invalid", "offers": [{"sku": "G1", "qty": 2}]}]
        self.row = {"time": datetime.fromtimestamp(990, timezone.utc).isoformat(), "round": 3,
                    "name": "ours", "url": "https://ours.invalid", "item": "G1", "result": "no_deal",
                    "observed_stock_qty": 2, "negotiation_diagnostics": {"cancel_confirmed": True},
                    "food_lifetime_observation": {"source": "verified_market_certificate", "unit": "u1",
                        "observed_at": 989, "checked_market_time": 900, "expires_at": 900+138*3600,
                        "required_hours": 361, "insufficient": True}}

    def filtered(self, rows=None, shops=None, order=None, now=1000):
        return filter_recent_short_life(shops or self.shops, [self.row] if rows is None else rows,
                                       order or self.order, self.config, now=now)

    def test_confirmed_short_life_excludes_until_changed_stock_horizon_or_cooldown(self):
        self.assertEqual(self.filtered(), ([], 1))
        self.assertEqual(self.filtered(now=1900)[1], 0)
        self.assertEqual(self.filtered(order={**self.order, "use_within_hours": 12})[1], 0)
        changed = deepcopy(self.shops); changed[0]["offers"][0]["qty"] = 3
        self.assertEqual(self.filtered(shops=changed)[1], 0)
        changed[0]["url"] = "https://new.invalid"
        self.assertEqual(self.filtered(shops=changed)[1], 0)

    def test_unknown_prose_pending_receipt_and_uncertain_cancel_never_blacklist(self):
        for field, value in (("food_lifetime_observation", None), ("result", "receipt_issued"),
                             ("negotiation_diagnostics", {"cancel_confirmed": False}),
                             ("observed_receipt", {"task": "pending"}), ("observed_stock_qty", None)):
            row = deepcopy(self.row); row[field] = value
            row["detail"] = "Certified shelf life is insufficient"
            self.assertEqual(self.filtered(rows=[row])[1], 0)
        for change in ({"source": "seller"}, {"observed_at": 1001}, {"insufficient": False},
                       {"expires_at": float("nan")}, {"expires_at": 900+400*3600}):
            row = deepcopy(self.row); row["food_lifetime_observation"].update(change)
            self.assertEqual(self.filtered(rows=[row])[1], 0)
        newer = {**self.row, "time": datetime.fromtimestamp(999, timezone.utc).isoformat(),
                 "food_lifetime_observation": None}
        self.assertEqual(self.filtered(rows=[newer, self.row])[1], 0)

    def test_long_horizon_prefers_supported_history_short_horizon_keeps_bargain_scan(self):
        seed = next(str(n) for n in range(100) if not exploration_turn(str(n)))
        shops = [{"name": name, "url": "https://"+name+".invalid", "offers": [{"sku": "G1", "qty": 2}]}
                 for name in ("known", "cheap", "unknown")]
        common = {"kind": "food", "grade": "C", "sample_count": 3, "age_seconds": 10, "buyer_count": 2}
        values = {("known", "G1"): {"groups": [{**common, "median_price": 550, "price_to_list": .55,
                                                 "minimum_hours_left": 400}], "best_price_to_list": .55},
                  ("cheap", "G1"): {"groups": [{**common, "median_price": 300, "price_to_list": .3,
                      "source": "public_food_discovery", "freshness_verified": False}], "best_price_to_list": .3}}
        original = deepcopy(values)
        for hours, expected in ((360, "known"), (12, "cheap")):
            order = {**self.order, "use_within_hours": hours}
            ranked = prioritize_history(values, order, self.config)
            candidates = shortlist(shops, [], seed=seed, value_evidence=ranked, round_number=3)
            choice = value_choice(candidates, values=ranked, budget=1000, config=self.config,
                                  order=order, catalog={"G1": {"list_price": 1000}}, seed=seed)
            self.assertEqual(choice["name"], expected)
            self.assertEqual({c["name"] for c in candidates}, {"known", "cheap", "unknown"})
        self.assertEqual(values, original)

    def test_horizon_candidate_survives_offer_and_shop_limits(self):
        shops = [{"name": str(n), "offers": [{"sku": "G"+str(i), "qty": 1} for i in range(9)]}
                 for n in range(9)]
        values = {(str(n), "G"+str(i)): {"best_price_to_list": .2, "groups": [{"price_to_list": .2}]}
                  for n in range(9) for i in range(8)}
        values[("8", "G8")] = {"food_horizon_supported": True, "best_price_to_list": .55,
                                 "groups": [{"price_to_list": .55}]}
        candidates = shortlist(shops, [], seed="test", value_evidence=values, round_number=3)
        selected = next(c for c in candidates if c["name"] == "8")
        self.assertEqual(selected["offers"][0]["sku"], "G8")
        self.assertTrue(selected["offers"][0]["food_horizon_supported"])
        self.assertLessEqual(len(candidates), 6)
