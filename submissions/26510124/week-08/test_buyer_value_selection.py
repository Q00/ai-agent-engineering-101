"""Price evidence reaches selection and negotiation without new purchase authority."""
from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import time
import unittest
from unittest.mock import Mock, patch

from buyer_fast_selection import exploration_turn, timeout_choice
from buyer_context import compact_dialogue
from buyer_policy import buying_terms, load_policy, negotiation_policy
from buyer_selection import offer_fingerprint, shortlist
from market_buyer import ROOT, Buyer, PolicyRefusal, choose_shop, last_asking_price, main, stocked_shops
from test_buyer_selection import paid, shop


def history(price=60000, samples=3):
    return {"best_price_to_list": price / 100000, "groups": [
        {"kind": "goods", "grade": "A", "warranty_hours": 6, "sample_count": samples,
         "median_price": price, "price_to_list": price / 100000, "age_seconds": 1800}]}


class BuyerValueSelectionTests(unittest.TestCase):
    def setUp(self):
        self.seed = next(f"o{n}" for n in range(100) if not exploration_turn(f"o{n}"))
        self.config = {**load_policy(), "selection_cache_seconds": 0}
        self.order = {"order": self.seed, "round": 3, "kind": "goods", "item": "chair",
                      "budget": 100000, "acceptable": ["G00001"]}
        self.catalog = {"G00001": {"list_price": 100000, "kind": "goods"}}

    def test_bargain_outside_popularity_shortlist_and_new_shop_both_survive(self):
        shops = [shop(f"frequent-{i}", [5]) for i in range(10)] + [shop("value", [4]), shop("new")]
        memory = [paid(s["name"]) for s in shops[:-2] for _ in range(20)] + [paid("value")]
        self.assertNotIn("value", {s["name"] for s in shortlist(shops, memory, seed=self.seed)})
        values = {("value", "G00001"): history()}
        result = shortlist(shops, memory, seed=self.seed, value_evidence=values)
        self.assertEqual(len(result), 6)
        self.assertEqual([s["name"] for s in result if s["selection_role"] == "value"], ["value"])
        self.assertEqual([s["name"] for s in result if s["selection_role"] == "exploration"], ["new"])
        self.assertEqual(result, shortlist(list(reversed(shops)), memory, seed=self.seed, value_evidence=values))

    def test_all_allowed_stock_is_compared_before_five_offer_prompt_limit(self):
        source = shop("value")
        source["offers"] = [{"sku": f"G{i:05}", "qty": 1, "name": "chair"} for i in range(1, 8)]
        allowed = {**self.order, "acceptable": [o["sku"] for o in source["offers"]]}
        before = deepcopy(source)
        shops = stocked_shops([source], allowed)
        self.assertEqual(len(shops[0]["offers"]), 7)
        values = {("value", "G00007"): history(), ("value", "G00006"): history(65000)}
        candidate = shortlist(shops, [paid("value")], seed=self.seed, value_evidence=values)[0]
        self.assertEqual(len(candidate["offers"]), 5)
        self.assertEqual(candidate["offers"][0]["sku"], "G00007")
        self.assertEqual(sum("price_history" in o for o in candidate["offers"]), 1)
        self.assertEqual(source, before)
        source["offers"].reverse()
        self.assertEqual(candidate, shortlist(stocked_shops([source], allowed), [paid("value")],
                                              seed=self.seed, value_evidence=values)[0])

    def test_known_fault_cannot_earn_a_value_slot(self):
        shops = [shop(f"safe-{i}", [4]) for i in range(8)] + [shop("unsafe", [5])]
        memory = [{"name": "unsafe", "round": 3, "result": "receipt_mismatch"}]
        values = {("unsafe", "G00001"): history(20000)}
        chosen = shortlist(shops, memory, seed=self.seed, value_evidence=values)
        self.assertNotIn("unsafe", {s["name"] for s in chosen})

    def test_public_low_stars_remain_visible_without_erasing_verified_price_evidence(self):
        memory = [{**paid("value", stars=2), "review_issues": [],
                   "review_reasons": ["above_preferred_price"],
                   "review_preference_shortfalls": ["above_preferred_price"]}]
        result = shortlist([shop("value", [5, 2])], memory, seed=self.seed,
                           value_evidence={("value", "G00001"): history()})[0]
        self.assertEqual(result["selection_role"], "value")
        self.assertEqual(result["reviews"]["low_star_count"], 1)
        self.assertEqual(result["reviews"]["excerpts"][-1]["stars"], 2)

    def test_timeout_compares_historical_ratio_and_keeps_exploration_turn(self):
        shops = [shop("expensive"), shop("value"), shop("new")]
        memory = [paid("expensive"), paid("value")]
        values = {("expensive", "G00001"): history(80000), ("value", "G00001"): history()}
        candidates = shortlist(shops, memory, seed=self.seed, value_evidence=values)
        self.assertEqual(timeout_choice(candidates, seed=self.seed)["name"], "value")
        explore = next(f"o{n}" for n in range(100) if exploration_turn(f"o{n}"))
        self.assertEqual(timeout_choice(candidates, seed=explore)["name"], "new")

    def test_single_model_call_sees_compact_price_context_and_returns_local_reference(self):
        shops = [shop("value"), shop("new")]
        values = {("value", "G00001"): history()}
        def model(_, context, validate, **options):
            self.assertEqual(context["catalog_prices"], {"G00001": 100000})
            found = next(c for c in context["candidates"] if c["name"] == "value")
            self.assertEqual(found["offers"][0]["price_history"], values[("value", "G00001")]["groups"])
            self.assertLess(len(json.dumps(context)), 4500)
            return validate(json.dumps({"name": "value", "sku": "G00001", "reason": "compare verified terms"}))
        with patch("market_buyer.build_value_evidence", return_value=values), \
                patch("market_buyer.decide", side_effect=model) as call, redirect_stdout(StringIO()):
            chosen, _ = choose_shop(shops, [paid("value")], item="chair", budget=100000,
                                   deadline=time.monotonic() + 30, policy=self.config,
                                   order=self.order, catalog=self.catalog)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(chosen["price_reference"], values[("value", "G00001")]["groups"])

    def test_registry_cannot_inject_price_reference(self):
        source = {**shop("value"), "price_reference": [{"median_price": 1, "grade": "A"}]}
        def model(_, context, validate, **options):
            self.assertNotIn("price_history", context["candidates"][0]["offers"][0])
            return validate(json.dumps({"name": "value", "sku": "G00001", "reason": "unknown terms"}))
        with patch("market_buyer.decide", side_effect=model), redirect_stdout(StringIO()):
            chosen, _ = choose_shop([source], [], item="chair", budget=100000,
                                   deadline=time.monotonic() + 30, policy=self.config,
                                   order=self.order, catalog=self.catalog)
        self.assertEqual(chosen["price_reference"], [])

    def test_reference_reaches_trade_without_lowering_cap_or_raising_authority(self):
        reference = history()["groups"]
        chosen = {**shop("value"), "selected_sku": "G00001", "price_reference": reference}
        def read(path):
            return self.order if Path(path).name == "order.json" else self.catalog
        with patch("market_buyer.read_json", side_effect=read), \
                patch("market_buyer.read_shops", return_value=[shop("value")]), \
                patch("market_buyer.read_memory", return_value=[]), \
                patch("market_buyer.load_policy", return_value=self.config), \
                patch("market_buyer.choose_shop", return_value=(chosen, "historical evidence")), \
                patch("market_buyer.trade_and_record", return_value={"receipt": None}) as trade, \
                redirect_stdout(StringIO()):
            code = main(["--shops", "/fixture/shops.json", "--order", "/fixture/order.json",
                         "--item", "chair", "--budget", "100000"])
        self.assertEqual(code, 0)
        terms = trade.call_args.kwargs["policy"]
        expected = buying_terms(100000, product=self.catalog["G00001"], order=self.order, config=self.config)
        self.assertEqual(terms, {**expected, "price_reference": reference})
        buyer = Buyer(Mock(), url="https://value.invalid", item="G00001", budget=100000, policy=terms)
        buyer.validate_decision({"action": "accept", "price": 80000, "reason": "valid current quote"}, "asking 80000")
        with self.assertRaises(PolicyRefusal):
            buyer.validate_decision({"action": "accept", "price": 85001, "reason": "past deal"}, "asking 85001")

    def test_reference_age_does_not_reset_failed_offer_cooldown(self):
        terms = buying_terms(100000, product=self.catalog["G00001"], order=self.order, config=self.config)
        before = offer_fingerprint(shop("value"), "G00001", terms, 3)
        for age in (1, 100, 2000):
            reference = history()["groups"]
            reference[0]["age_seconds"] = age
            self.assertEqual(before, offer_fingerprint(shop("value"), "G00001",
                                                      {**terms, "price_reference": reference}, 3))

    def test_two_price_groups_keep_negotiation_input_bounded(self):
        terms = buying_terms(100000, product=self.catalog["G00001"], order=self.order, config=self.config)
        terms["price_reference"] = history()["groups"] + [
            {**history(65000)["groups"][0], "grade": "B", "warranty_hours": 3}]
        trail = [{"role": "buyer", "text": "I want G00001"}]
        for ask, bid in ((90000, 55000), (80000, 57000), (70000, 58000), (60000, 58000)):
            message = f"Grade A. Warranty 6 hours. Asking price: {ask}"
            trail.append({"role": "seller", "text": message})
            context = {"item": "G00001", "budget": 100000, "purchase_policy": negotiation_policy(terms),
                       "current_ask": ask, **compact_dialogue(trail, message, last_asking_price)}
            prompt = (ROOT / "prompts/buyer.txt").read_text() + "\n" + json.dumps(context)
            self.assertLess(len(prompt), 2600)  # Application characters, not CLI/token accounting.
            trail.append({"role": "buyer", "text": str(bid)})


if __name__ == "__main__":
    unittest.main()
