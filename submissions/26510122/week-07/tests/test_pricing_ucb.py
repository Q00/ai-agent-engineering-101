"""UCB must remain offline and refuse biased or unverified arm data."""

from __future__ import annotations

import unittest

from pricing_simulator import candidate_price
from pricing_ucb import evaluate_ucb


def row(arm: str, status: str = "COUNTED", price: int = 110) -> dict:
    return {"item": "lamp", "assigned_arm": arm, "reserve": 100,
            "opening_ask_actual": candidate_price(100, arm),
            "ledger_status": status, "final_price": price if status == "COUNTED" else None,
            "receipt_issued": status in {"COUNTED", "PENDING"},
            "negotiation_status": "COMPLETED" if status in {"COUNTED", "PENDING"} else "REJECTED"}


class PricingUcbTest(unittest.TestCase):
    def test_legacy_trades_cannot_start_ucb(self):
        legacy = row("High")
        legacy["assigned_arm"] = "legacy_cap_1.12"
        result = evaluate_ucb([legacy], "lamp", minimum_exposed=1)
        self.assertFalse(result["ready"])
        self.assertIsNone(result["recommended_arm"])

    def test_pending_and_ask_mismatch_block_recommendation(self):
        rows = [row(arm) for arm in ("Low", "Mid", "High")]
        rows[0] = row("Low", "PENDING")
        self.assertFalse(evaluate_ucb(rows, "lamp", minimum_exposed=1)["ready"])
        rows[0] = row("Low")
        rows[1]["opening_ask_actual"] += 1
        self.assertFalse(evaluate_ucb(rows, "lamp", minimum_exposed=1)["ready"])

    def test_scores_only_verified_balanced_arms(self):
        rows = [row("Low", price=105), row("Mid", price=108), row("High", price=110)]
        result = evaluate_ucb(rows, "lamp", minimum_exposed=1, exploration=0)
        self.assertTrue(result["ready"])
        self.assertEqual(result["recommended_arm"], "High")
        self.assertEqual(result["arms"]["High"]["counted"], 1)


if __name__ == "__main__":
    unittest.main()
