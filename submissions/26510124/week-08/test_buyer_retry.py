"""Offline regression checks for confirmed endings and one local alternative."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from buyer_retry import remaining_retry_decisions, select_alternative


def ended(**changes):
    return {"outcome": "no_deal", "no_deal_only": True, "reason": "seller_rejected",
            "decisions": 1, "receipt_seen": False, "cancel_confirmed": False,
            "terminal_state": "TASK_STATE_REJECTED", **changes}


def candidate(name, *, ratio=None, role="evidence"):
    offer = {"sku": "G00001", "qty": 1}
    if ratio is not None:
        offer["price_history"] = [{"price_to_list": ratio}]
    return {"name": name, "offers": [offer], "selection_role": role,
            "own_memory": {"trades": 0, "settled": 0, "satisfactory_settlements": 0,
                           "unsatisfactory_settlements": 0, "accepted_tradeoffs": 0,
                           "receipt_mismatches": 0},
            "reviews": {"count": 0, "rated_count": 0, "low_star_count": 0,
                        "average_stars": None, "excerpts": []}}


class RemainingDecisionsTests(unittest.TestCase):
    def check(self, status, *, maximum=4, seconds=100):
        return remaining_retry_decisions(status, max_decisions=maximum, seconds_left=seconds)

    def test_confirmed_reject_and_seller_cancel_share_remaining_budget(self):
        self.assertEqual(self.check(ended(decisions=0)), 4)
        self.assertEqual(self.check(ended(decisions=2)), 2)
        self.assertEqual(self.check(ended(reason="seller_cancelled", terminal_state="TASK_STATE_CANCELED")), 3)
        self.assertEqual(self.check(ended(decisions=1), maximum=2), 1)

    def test_model_walkaway_needs_explicit_confirmed_cancellation(self):
        status = ended(reason="model_walk_away", terminal_state="TASK_STATE_CANCELED", cancel_confirmed=True)
        self.assertEqual(self.check(status), 3)
        for value in (False, None, "true", 1):
            self.assertEqual(self.check({**status, "cancel_confirmed": value}), 0)
        missing = dict(status)
        del missing["cancel_confirmed"]
        self.assertEqual(self.check(missing), 0)

    def test_receipt_missing_or_unknown_evidence_cannot_retry(self):
        for field, values in (("receipt_seen", (True, None, 0, "false")),
                              ("no_deal_only", (False, None, 1, "true"))):
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.check(ended(**{field: value})), 0)
            status = ended()
            del status[field]
            self.assertEqual(self.check(status), 0)

    def test_errors_receipts_and_policy_or_decision_limits_never_retry(self):
        for reason in ("protocol_or_network_error", "model_error", "model_timeout", "receipt_mismatch",
                       "receipt_issued", "decision_limit", "price_limit", "concession_limit", None, "unknown"):
            self.assertEqual(self.check(ended(reason=reason, cancel_confirmed=True)), 0, reason)
        for outcome in ("error", "receipt_issued", "receipt_mismatch", "settled", None):
            self.assertEqual(self.check(ended(outcome=outcome)), 0)

    def test_inconsistent_or_nonterminal_seller_states_never_retry(self):
        for terminal in (None, "TASK_STATE_INPUT_REQUIRED", "TASK_STATE_WORKING", "TASK_STATE_FAILED",
                         "TASK_STATE_COMPLETED", "TASK_STATE_CANCELED"):
            self.assertEqual(self.check(ended(terminal_state=terminal)), 0)
        self.assertEqual(self.check(ended(reason="seller_cancelled")), 0)
        self.assertEqual(self.check(ended(reason="model_walk_away", cancel_confirmed=True)), 0)

    def test_limits_and_types_fail_closed(self):
        for used in (-1, 4, 5, True, 1.0, "1", None):
            self.assertEqual(self.check(ended(decisions=used)), 0)
        for maximum in (0, -1, 5, 12, True, 4.0, "4", None):
            self.assertEqual(self.check(ended(), maximum=maximum), 0)
        for seconds in (0, 19.999, -1, float("inf"), float("nan"), True, "30", None):
            self.assertEqual(self.check(ended(), seconds=seconds), 0)
        self.assertEqual(self.check(ended(), seconds=20), 3)
        for status in (None, [], "cancelled", {}):
            self.assertEqual(self.check(status), 0)


class AlternativeTests(unittest.TestCase):
    def test_excludes_primary_preserves_inputs_and_describes_actual_selection(self):
        candidates = [candidate("first", ratio=.1), candidate("second", ratio=.6), candidate("third", ratio=.7)]
        primary = {"name": "first", "url": "https://first.invalid"}
        before = deepcopy((candidates, primary))
        with patch("harness.call_model", side_effect=AssertionError("no model calls")), \
                patch("httpx.Client.request", side_effect=AssertionError("no network calls")):
            choice = select_alternative(candidates, primary, seed="one-order")
        self.assertEqual(choice["name"], "second")
        self.assertEqual(choice["sku"], "G00001")
        self.assertIn("Confirmed first negotiation", choice["reason"])
        self.assertNotIn("timed out", choice["reason"])
        self.assertEqual((candidates, primary), before)

    def test_zero_reviews_unknown_quality_and_new_shop_are_eligible(self):
        new = candidate("new", role="exploration")
        choice = select_alternative([candidate("first"), new], {"name": "first"}, seed="new")
        self.assertEqual(choice["name"], "new")

    def test_no_primary_or_no_valid_other_offer_returns_none(self):
        for primary in ({}, None, {"name": ""}, {"name": 1}):
            self.assertIsNone(select_alternative([candidate("first")], primary, seed="a"))
        self.assertIsNone(select_alternative([candidate("first")], {"name": "first"}, seed="a"))
        self.assertIsNone(select_alternative(None, {"name": "first"}, seed="a"))
        for qty in (0, -1, True, 1.0, "1", None):
            other = candidate("other")
            other["offers"][0]["qty"] = qty
            self.assertIsNone(select_alternative([other], {"name": "first"}, seed="a"))

    def test_candidate_and_offer_bounds_are_applied_before_selection(self):
        candidates = [candidate("first") for _ in range(6)] + [candidate("outside")]
        self.assertIsNone(select_alternative(candidates, {"name": "first"}, seed="a"))
        other = candidate("other")
        other["offers"] = [{"sku": "G00001", "qty": 0} for _ in range(5)] + [{"sku": "G00002", "qty": 1}]
        self.assertIsNone(select_alternative([other], {"name": "first"}, seed="a"))

    def test_duplicate_names_and_malformed_candidates_do_not_expand_pool(self):
        candidates = [None, {"name": "bad"}, candidate("other"), candidate("other", ratio=.1)]
        with patch("buyer_retry.timeout_choice", return_value={"name": "other", "sku": "G00001"}) as local:
            choice = select_alternative(candidates, {"name": "first"}, seed="a")
        self.assertEqual(len(local.call_args.args[0]), 1)
        self.assertEqual(choice["name"], "other")

    def test_bad_numeric_history_is_ignored_without_poisoning_local_rank(self):
        other = candidate("other")
        other["offers"][0]["price_history"] = [{"price_to_list": float("nan")}, {"price_to_list": -1}]
        before = deepcopy(other)
        choice = select_alternative([other], {"name": "first"}, seed="a")
        self.assertEqual(choice["name"], "other")
        self.assertEqual(len(other["offers"][0]["price_history"]), len(before["offers"][0]["price_history"]))

    def test_malformed_ranking_evidence_cannot_override_valid_unknown_shop(self):
        bad, other = candidate("bad"), candidate("other")
        bad["reviews"]["average_stars"] = float("nan")
        self.assertEqual(select_alternative([bad, other], {"name": "first"}, seed="a")["name"], "other")
        bad["reviews"]["average_stars"] = 5
        bad["own_memory"]["trades"] = True
        self.assertEqual(select_alternative([bad, other], {"name": "first"}, seed="a")["name"], "other")


if __name__ == "__main__":
    unittest.main()
