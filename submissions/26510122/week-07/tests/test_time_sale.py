"""The dated sale uses a real timezone and keeps one price per task."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx

from pricing import PricingRecorder
from seller import build
from time_sale import choose_campaign_price


class TimeSaleTest(unittest.TestCase):
    async def exchange(self, app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://testserver") as client:
            card_response = await client.get("/.well-known/agent-card.json")
            card_response.raise_for_status()
            message = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()),
                       "parts": [{"text": "I want a lamp"}]}
            response = await client.post("/", json={"jsonrpc": "2.0", "id": "1",
                                                   "method": "SendMessage", "params": {"message": message}},
                                         headers={"A2A-Version": "1.0"})
            response.raise_for_status()
            result = response.json()
            self.assertNotIn("error", result)
            task = result["result"].get("task", result["result"])
            return card_response.json(), task

    def test_campaign_boundaries_and_exact_prices(self):
        kst = ZoneInfo("Asia/Seoul")
        cases = [
            ("2026-10-21T23:59:59+09:00", "campaign_regular_1.12", 112),
            ("2026-10-22T00:00:00+09:00", "campaign_sale_1.09", 109),
            ("2026-10-28T23:59:59+09:00", "campaign_sale_1.09", 109),
            ("2026-10-29T00:00:00+09:00", "campaign_regular_1.12", 112),
        ]
        for clock, arm, ask in cases:
            at = datetime.fromisoformat(clock)
            with self.subTest(clock=clock):
                self.assertEqual(choose_campaign_price(100, at)[:2], (arm, ask))
                self.assertEqual(choose_campaign_price(100, at.astimezone(timezone.utc))[:2], (arm, ask))
        self.assertEqual(choose_campaign_price(180, datetime(2026, 10, 22, 17, 0, tzinfo=kst))[1], 197)
        self.assertEqual(choose_campaign_price(260, datetime(2026, 10, 29, 17, 20, tzinfo=kst))[1], 292)
        with self.assertRaises(ValueError):
            choose_campaign_price(100, datetime(2026, 10, 22, 17))

    def test_card_and_recorded_opening_agree(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pricing.sqlite"
            with patch.dict("seller.os.environ", {
                "PRICING_STATE_PATH": str(path), "WEEK07_TIME_SALE": "1",
                "WEEK07_CARD_PROMO": "0",
                "WEEK07_PRICE_EXPERIMENT_PERCENT": "100", "WEEK07_PRICE_UCB_AUTO": "1",
            }), patch("seller.choose_campaign_price", return_value=(
                "campaign_sale_1.09", 109, "2026-10-22T17:00:00+09:00",
            )) as choose, patch("seller.decide", return_value={"action": "ask", "price": 112}), \
                 patch("seller.trace"):
                app = build(9999, profile={"name": "Luna Shop", "reserve": {"lamp": 100}})
                card, task = asyncio.run(self.exchange(app))
                self.assertNotIn("Time Sale Now!", card["description"])
                self.assertIn("asking 109", task["status"]["message"]["parts"][0]["text"])
                row = PricingRecorder(path).get(task["id"])
                self.assertEqual(row["assigned_arm"], "campaign_sale_1.09")
                self.assertEqual(row["opening_ask_actual"], 109)
                self.assertEqual(choose.call_count, 1)

    def test_card_promo_does_not_change_legacy_opening(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pricing.sqlite"
            with patch.dict("seller.os.environ", {
                "PRICING_STATE_PATH": str(path), "WEEK07_CARD_PROMO": "1",
                "WEEK07_TIME_SALE": "0", "WEEK07_PRICE_EXPERIMENT_PERCENT": "0",
                "WEEK07_PRICE_UCB_AUTO": "0",
            }), patch("seller.decide", return_value={"action": "ask", "price": 112}), \
                 patch("seller.trace"):
                app = build(9999, profile={"name": "Luna Shop", "reserve": {"lamp": 100}})
                card, task = asyncio.run(self.exchange(app))
                self.assertIn("Time Sale Now!", card["description"])
                self.assertIn("Oct 28, 2026 23:59 KST", card["skills"][0]["description"])
                self.assertNotIn("reserve", card["description"].lower())
                self.assertNotIn("1.09", card["description"])
                self.assertNotIn("1.12", card["description"])
                self.assertIn("asking 112", task["status"]["message"]["parts"][0]["text"])
                row = PricingRecorder(path).get(task["id"])
                self.assertEqual(row["assigned_arm"], "legacy_cap_1.12")
                self.assertEqual(row["opening_ask_actual"], 112)


if __name__ == "__main__":
    unittest.main()
