"""UCB must refuse biased or unverified data and honor the live freshness gate."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx

from pricing import PricingRecorder
from pricing_simulator import candidate_price
from pricing_ucb import evaluate_ucb
from seller import build


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

    def test_live_selection_uses_verified_data_only_while_reconciliation_is_fresh(self):
        async def opening(app):
            message = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()),
                       "parts": [{"text": "I want a lamp"}]}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://testserver") as client:
                response = await client.post("/", json={"jsonrpc": "2.0", "id": "1",
                                                         "method": "SendMessage",
                                                         "params": {"message": message}},
                                             headers={"A2A-Version": "1.0"})
            response.raise_for_status()
            result = response.json()
            self.assertNotIn("error", result)
            task = result["result"].get("task", result["result"])
            return task["id"], task["status"]["message"]["parts"][0]["text"]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pricing.sqlite"
            recorder = PricingRecorder(path)
            for arm, price in (("Low", 103), ("Mid", 106), ("High", 110)):
                for number in range(30):
                    task_id = f"fixture-{arm}-{number}"
                    recorder.start(task_id, "lamp", 100, arm)
                    recorder.opening_sent(task_id, candidate_price(100, arm))
                    recorder.receipt(task_id, price)
                    recorder.market_result(task_id, True, "test fixture")
            recorder.mark_reconciliation_complete()
            with patch.dict("seller.os.environ", {
                "PRICING_STATE_PATH": str(path), "WEEK07_PRICE_EXPERIMENT_PERCENT": "100",
                "WEEK07_PRICE_UCB_AUTO": "1",
            }), patch("seller.assign_price", return_value=("Low", 106)), \
                 patch("seller.decide", return_value={"action": "ask", "price": 113}), \
                 patch("seller.trace"):
                app = build(9999, profile={"name": "Luna Shop", "reserve": {"lamp": 100}})
                task_id, text = asyncio.run(opening(app))
                self.assertIn("asking 112", text)
                self.assertEqual(recorder.get(task_id)["assigned_arm"], "High")
                with sqlite3.connect(path) as db:
                    db.execute("UPDATE metadata SET value='2020-01-01T00:00:00+00:00' "
                               "WHERE key='ledger_reconciled_at'")
                task_id, text = asyncio.run(opening(app))
                self.assertIn("asking 106", text)
                self.assertEqual(recorder.get(task_id)["assigned_arm"], "Low")


if __name__ == "__main__":
    unittest.main()
