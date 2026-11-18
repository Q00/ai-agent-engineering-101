"""Real buyer against the real local ASGI seller, without public requests."""
import asyncio
import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import market_buyer
import seller
from protocol import A2AClient
from signing import canon


class BuyerTests(unittest.TestCase):
    def run_case(self, budget):
        async def run():
            key = Ed25519PrivateKey.generate()
            old_sign, old_client, old_memory = seller.sign_receipt, market_buyer.A2AClient, market_buyer.MEMORY
            seller.sign_receipt = lambda rec: {**rec, "sig_seller": base64.b64encode(key.sign(canon(rec))).decode()}
            app = seller.build(9999)
            class LocalClient(A2AClient):
                def __init__(self, base_url, **kwargs):
                    self.base_url, self.endpoint, self.label = base_url.rstrip("/"), None, "local-fixture"
                    self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                        headers={"A2A-Version": "1.0"}, base_url=base_url)
            market_buyer.A2AClient = LocalClient
            try:
                with tempfile.TemporaryDirectory() as folder:
                    market_buyer.MEMORY = Path(folder) / "memory.json"
                    result = await market_buyer.run([{"name": "local-seller", "url": "http://127.0.0.1:9999", "reviews": []}], "lamp", budget)
                    memory = json.loads(market_buyer.MEMORY.read_text(encoding="utf-8"))
                    return result, memory
            finally:
                seller.sign_receipt, market_buyer.A2AClient, market_buyer.MEMORY = old_sign, old_client, old_memory
        return asyncio.run(run())

    def test_actual_buyer_receives_signed_receipt_under_budget(self):
        result, memory = self.run_case(120)
        self.assertIsNotNone(result["receipt"])
        self.assertLessEqual(result["receipt"]["price"], 120)
        record = memory["shops"]["local-seller"]["history"][-1]
        self.assertLessEqual(record["last_offer"], 120)
        self.assertEqual(record["state"], "agreed_unsettled")

    def test_unaffordable_order_is_not_a_one_star_accusation(self):
        result, memory = self.run_case(90)
        self.assertIsNone(result["receipt"])
        self.assertEqual(result["stars"], 3)
        self.assertFalse(memory["shops"]["local-seller"].get("blocked"))
        self.assertIsNone(memory["shops"]["local-seller"]["history"][-1]["last_offer"])


if __name__ == "__main__":
    unittest.main()
