"""Keep the draft Card separate from the live baseline and A2A contract."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from seller import build


def card(variant: str, db_path: Path) -> dict:
    async def read() -> dict:
        with patch.dict("seller.os.environ", {
            "WEEK07_CARD_VARIANT": variant,
            "PRICING_STATE_PATH": str(db_path),
        }):
            app = build(9999, profile={"name": "Existing Shop"})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://testserver") as client:
            response = await client.get("/.well-known/agent-card.json")
        response.raise_for_status()
        return response.json()
    return asyncio.run(read())


class SellerCardTest(unittest.TestCase):
    def test_draft_changes_only_descriptive_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "pricing.sqlite"
            baseline = card("baseline", db_path)
            improved = card("transparent", db_path)
        self.assertEqual(baseline["name"], improved["name"])
        self.assertEqual(baseline["supportedInterfaces"], improved["supportedInterfaces"])
        self.assertEqual(baseline["capabilities"], improved["capabilities"])
        self.assertEqual(baseline["skills"][0]["id"], improved["skills"][0]["id"])
        self.assertNotEqual(baseline["description"], improved["description"])
        self.assertIn("signed receipts", improved["description"])
        self.assertIn("whole-number", improved["skills"][0]["description"])
        self.assertNotIn("guaranteed", improved["description"].lower())

    def test_reject_unknown_variant(self):
        with patch.dict("seller.os.environ", {"WEEK07_CARD_VARIANT": "unknown"}):
            with self.assertRaises(ValueError):
                build(9999)


if __name__ == "__main__":
    unittest.main()
