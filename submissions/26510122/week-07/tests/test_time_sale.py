"""The hourly sale uses a real timezone and keeps one price per task."""

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
from time_sale import choose_hourly_price


class TimeSaleTest(unittest.TestCase):
    def test_kst_boundaries_and_exact_prices(self):
        kst = ZoneInfo("Asia/Seoul")
        cases = [
            ("16:59:59", "hourly_regular_1.09", 109),
            ("17:00:00", "hourly_sale_1.06", 106),
            ("17:19:59", "hourly_sale_1.06", 106),
            ("17:20:00", "hourly_regular_1.09", 109),
        ]
        for clock, arm, ask in cases:
            at = datetime.fromisoformat(f"2026-10-22T{clock}+09:00")
            with self.subTest(clock=clock):
                self.assertEqual(choose_hourly_price(100, at)[:2], (arm, ask))
                self.assertEqual(choose_hourly_price(100, at.astimezone(timezone.utc))[:2], (arm, ask))
        self.assertEqual(choose_hourly_price(180, datetime(2026, 10, 22, 17, 0, tzinfo=kst))[1], 191)
        self.assertEqual(choose_hourly_price(260, datetime(2026, 10, 22, 17, 20, tzinfo=kst))[1], 284)
        with self.assertRaises(ValueError):
            choose_hourly_price(100, datetime(2026, 10, 22, 17))

    def test_card_and_recorded_opening_agree(self):
        async def exchange(app):
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

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pricing.sqlite"
            with patch.dict("seller.os.environ", {
                "PRICING_STATE_PATH": str(path), "WEEK07_HOURLY_SALE": "1",
                "WEEK07_PRICE_EXPERIMENT_PERCENT": "100", "WEEK07_PRICE_UCB_AUTO": "1",
            }), patch("seller.choose_hourly_price", return_value=(
                "hourly_sale_1.06", 106, "2026-10-22T17:00:00+09:00",
            )) as choose, patch("seller.decide", return_value={"action": "ask", "price": 112}), \
                 patch("seller.trace"):
                app = build(9999, profile={"name": "Luna Shop", "reserve": {"lamp": 100}})
                card, task = asyncio.run(exchange(app))
                self.assertIn("every hour from :00 through :19 KST", card["description"])
                self.assertIn("every hour from :00 through :19 KST", card["skills"][0]["description"])
                self.assertIn("asking 106", task["status"]["message"]["parts"][0]["text"])
                row = PricingRecorder(path).get(task["id"])
                self.assertEqual(row["assigned_arm"], "hourly_sale_1.06")
                self.assertEqual(row["opening_ask_actual"], 106)
                self.assertEqual(choose.call_count, 1)


if __name__ == "__main__":
    unittest.main()
