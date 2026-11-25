"""The shop choice by rule (BUYER_CHOOSE_ENGINE=rule, 메인 11/07 09:30): no agent call; a shop the prefetch has not asked
goes first, then the one that sold me this item cheapest, then the most reviewed."""
import os
import copy
import unittest
from unittest import mock

import market_buyer as mb


def shop(name, reviews=0):
    return {"name": name, "url": f"http://{name}", "reviews": [{"stars": 5, "review": "ok"}] * reviews}


def deal(name, price, item="김치"):
    return {"shop": name, "item": item, "result": "deal", "price": price, "signed": True, "problems": []}


class ChooseRule(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"BUYER_CHOOSE_ENGINE": "rule"})
        p.start()
        self.addCleanup(p.stop)
        a = mock.patch.object(mb, "ask", side_effect=AssertionError("no agent call"))
        a.start()
        self.addCleanup(a.stop)

    def test_a_shop_not_yet_asked_goes_first_then_the_cheapest_before(self):
        shops = [shop("a", 9), shop("b", 2), shop("c", 1)]
        memory = {"trades": [deal("a", 9000), deal("b", 9500), deal("c", 9900)]}
        s, why = mb.choose_racing(shops, memory, "김치", 12000, {"enough": lambda: False, "asked": {"a"}})
        self.assertEqual(s["name"], "b")
        self.assertIn(" rule: ", why)
        self.assertEqual(mb.choose_path(why), "rule")
        s, _ = mb.choose_racing(shops, memory, "김치", 12000, {"enough": lambda: False, "asked": set()})
        self.assertEqual(s["name"], "a")

    def test_no_history_the_most_reviewed_and_the_filters_still_apply(self):
        shops = [shop("a", 5), shop("b", 7), shop("cheat", 30)]
        memory = {"trades": [], "carried_bans": {"cheat": "receipt did not match"}}
        s, _ = mb.choose_racing(shops, memory, "김치", 12000, {"enough": lambda: False, "asked": set()})
        self.assertEqual(s["name"], "b")

    def test_category_caller_uses_common_prospective_sku_receipt_history(self):
        shops = [dict(shop("a", 9), usable=[{"sku": "G1", "list_price": 100}]),
                 dict(shop("b", 2), usable=[{"sku": "G1", "list_price": 100}])]
        memory = {"trades": [deal("a", 90, "G1"), deal("b", 50, "G1")]}
        chosen, _ = mb.choose_racing(shops, memory, "goods", 100, {"asked": []})
        self.assertEqual(chosen["name"], "b")

    def test_sku_preserves_pool_memory_asked_and_review_priority(self):
        shops = [dict(shop("a", 9), usable=[{"sku": "G1", "list_price": 100}]),
                 dict(shop("b", 2), usable=[{"sku": "G1", "list_price": 100}]),
                 dict(shop("excluded", 99), usable=[{"sku": "G1", "list_price": 100}])]
        memory = {"trades": [deal("a", 90, "G1"), deal("b", 50, "G1"),
                             deal("excluded", 1, "G1")],
                  "carried_bans": {"excluded": "bad receipt"}}
        before = copy.deepcopy((shops, memory))
        original_pool = mb.choose_pool
        pools = []
        def observe(*args):
            result = original_pool(*args)
            pools.append([s["name"] for s in result[0]])
            return result
        with mock.patch.object(mb, "choose_pool", side_effect=observe):
            chosen, why = mb.rule_choose(shops, memory, "goods", budget=100)
            self.assertEqual(chosen["name"], "b")
            self.assertIn("G1", why)
            chosen, _ = mb.rule_choose(shops, memory, "goods", asked={"b"}, budget=100)
            self.assertEqual(chosen["name"], "a")
        self.assertEqual(pools, [["a", "b"], ["a", "b"]])
        self.assertEqual((shops, memory), before)
        invalid = [deal("b", None, "G1"), deal("b", "1", "G1"),
                   deal("b", 1, "G2"), dict(deal("b", 1, "G1"), problems=["bad"]),
                   dict(deal("b", 1, "G1"), result="error")]
        for record in invalid:
            with self.subTest(record=record):
                chosen, _ = mb.rule_choose(shops[:2], {"trades": [deal("a", 100, "other"),
                                                               deal("b", 100, "other"), record]},
                                           "goods", budget=100)
                self.assertEqual(chosen["name"], "a")

    def test_budget_offer_and_ambiguous_or_legacy_fallback(self):
        shops = [dict(shop("a", 9), usable=[{"sku": "G1", "list_price": 100},
                                                    {"sku": "G2", "list_price": 200}]),
                 dict(shop("b", 2), usable=[{"sku": "G1", "list_price": 100},
                                                    {"sku": "G2", "list_price": 200}])]
        memory = {"trades": [deal("a", 90, "G1"), deal("b", 50, "G1"),
                             deal("a", 40, "G2"), deal("b", 80, "G2")]}
        self.assertEqual(mb.choose_racing(shops, memory, "goods", 100, {"asked": []})[0]["name"], "b")
        self.assertEqual(mb.choose_racing(shops, memory, "goods", 200, {"asked": []})[0]["name"], "a")
        self.assertEqual(mb.rule_choose(shops, memory, "goods")[0]["name"], "a")
        shops[0]["usable"] = [{"sku": "G2", "list_price": 100}]
        self.assertEqual(mb.rule_choose(shops, memory, "goods", budget=100)[0]["name"], "a")
        del shops[0]["usable"]
        self.assertEqual(mb.rule_choose(shops, memory, "goods", budget=100)[0]["name"], "a")
        legacy = {"trades": [deal("a", 90), deal("b", 50)]}
        self.assertEqual(mb.rule_choose([shop("a", 9), shop("b", 2)], legacy, "김치")[0]["name"], "b")

    def test_switch_off_keeps_the_agent(self):
        with mock.patch.dict(os.environ, {"BUYER_CHOOSE_ENGINE": "agent"}):
            self.assertFalse(mb.choose_by_rule())
        with mock.patch.dict(os.environ, {"BUYER_CHOOSE_ENGINE": ""}), \
                mock.patch.object(mb, "load_json", return_value={"buyer_choose": {"engine": "rule"}}):
            self.assertTrue(mb.choose_by_rule())
        with mock.patch.dict(os.environ, {"BUYER_CHOOSE_ENGINE": ""}), mock.patch.object(mb, "load_json", return_value={}):
            self.assertFalse(mb.choose_by_rule())


if __name__ == "__main__":
    unittest.main()
