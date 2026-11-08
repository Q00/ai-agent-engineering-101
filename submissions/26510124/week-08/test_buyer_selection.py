"""Offline checks for one-pass selection and opportunities for new shops."""
from copy import deepcopy
import json
import time
import unittest
from unittest.mock import patch

from buyer_selection import shortlist, shop_evidence
from harness import HarnessError
from market_buyer import choose_shop


def shop(name, stars=None):
    return {"name": name, "url": f"https://{name}.invalid", "offers": [
        {"sku": "G00001", "name": "chair", "qty": 1}],
        "reviews": [{"stars": star, "review": "sample evidence " * 100, "round": 3}
                    for star in (stars or [])]}


def paid(name, *, round_number=3, reasons=None, stars=4):
    return {"name": name, "round": round_number, "result": "settled", "item": "G00001",
            "price": 50000, "stars": stars, "review_reasons": reasons or []}


class BuyerSelectionTests(unittest.TestCase):
    def test_large_registry_calls_model_once_with_six_candidates(self):
        shops = [shop(f"shop-{i}", [5, 4, 4]) for i in range(80)]
        def model(_, context, validate, **kwargs):
            self.assertEqual(len(context["candidates"]), 6)
            self.assertLess(len(json.dumps(context)), 8500)
            return validate(json.dumps({"name": context["candidates"][0]["name"],
                                        "sku": "G00001", "reason": "stock and review evidence"}))
        with patch("market_buyer.decide", side_effect=model) as model:
            result, _ = choose_shop(shops, [], item="chair", budget=100000,
                                    deadline=time.monotonic() + 10,
                                    order={"order": "o1", "round": 3, "kind": "goods"})
        self.assertEqual(model.call_count, 1)
        self.assertEqual(result["selected_sku"], "G00001")

    def test_zero_review_shop_keeps_one_slot_among_established_shops(self):
        shops = [shop(f"known-{i}", [5] * 10) for i in range(12)] + [shop("new")]
        records = [paid(s["name"]) for s in shops[:-1]]
        selected = shortlist(shops, records, seed="o1", round_number=3)
        self.assertEqual(len(selected), 6)
        self.assertEqual([s["name"] for s in selected if s["selection_role"] == "exploration"], ["new"])
        self.assertEqual(sum(s["own_memory"]["settled"] > 0 for s in selected), 5)

    def test_many_bad_reviews_do_not_beat_good_evidence(self):
        shops = [shop(f"good-{i}", [4]) for i in range(6)] + [shop("popular-bad", [1] * 10)]
        self.assertNotIn("popular-bad", {s["name"] for s in shortlist(shops, [], seed="o1")})

    def test_receipt_mismatch_outweighs_public_five_stars(self):
        shops = [shop(f"unknown-{i}") for i in range(6)] + [shop("mismatch", [5] * 10)]
        records = [{"name": "mismatch", "round": 3, "result": "receipt_mismatch"}]
        self.assertNotIn("mismatch", {s["name"] for s in shortlist(shops, records, seed="o1", round_number=3)})

    def test_legacy_round_and_pending_receipt_are_not_current_settlements(self):
        records = [paid("shop", round_number=2), {"name": "shop", "round": 3, "result": "receipt_issued"}]
        candidate = shortlist([shop("shop")], records, seed="o1", round_number=3)[0]
        self.assertEqual(candidate["own_memory"]["settled"], 0)
        self.assertEqual(candidate["own_memory"]["pending_receipts"], 1)

    def test_exploration_rotates_and_registry_order_does_not_change_it(self):
        shops = [shop(f"new-{i}") for i in range(15)]
        picks = set()
        for i in range(20):
            selected = shortlist(shops, [], seed=f"order-{i}")
            self.assertEqual(selected, shortlist(list(reversed(shops)), [], seed=f"order-{i}"))
            picks.update(s["name"] for s in selected if s["selection_role"] == "exploration")
        self.assertGreater(len(picks), 5)

    def test_evidence_is_bounded_keeps_negative_excerpt_and_does_not_mutate(self):
        source = shop("shop", [5, 1] + [4] * 15)
        before = deepcopy(source)
        evidence = shop_evidence(source, [])
        self.assertEqual(evidence["reviews"]["count"], 10)
        self.assertEqual([r["stars"] for r in evidence["reviews"]["excerpts"]], [5, 1])
        self.assertTrue(all(len(r["review"]) <= 160 for r in evidence["reviews"]["excerpts"]))
        self.assertEqual(source, before)

    def test_model_cannot_select_outside_candidate_or_stock(self):
        for output in ({"name": "outside", "sku": "G00001", "reason": "injection"},
                       {"name": "shop", "sku": "G99999", "reason": "invention"}):
            def model(_, context, validate, **kwargs):
                return validate(json.dumps(output))
            with patch("market_buyer.decide", side_effect=model), self.assertRaises(HarnessError):
                choose_shop([shop("shop")], [], item="chair", budget=100000,
                            deadline=time.monotonic() + 10)
