"""STEP 4 protocol and validation checks with synthetic harness answers."""

import json
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

import seller


def decision(action, price):
    return {"action": action, "price": price, "reason": "local STEP 4 fixture"}


class FollowupValidation(unittest.TestCase):
    def test_accept_must_match_offer_and_reserve(self):
        for response in (decision("accept", 99), decision("accept", 130),
                         decision("ask", 99), decision("offer", 120)):
            with self.subTest(response=response), self.assertRaises(ValueError):
                seller.validate_followup(response, 100, 120)
        seller.validate_followup(decision("accept", 120), 100, 120)
        seller.validate_followup(decision("ask", 135), 100, 80)
        seller.validate_followup(decision("reject", None), 100, 80)


class NegotiationProtocol(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        root = seller.ROOT / "logs/step4-tests" / uuid4().hex
        (root / "prompts").mkdir(parents=True)
        (root / "prompts/seller.txt").write_text(
            (seller.ROOT / "prompts/seller.txt").read_text(encoding="utf-8"), encoding="utf-8")
        self.root_patch = patch.object(seller, "ROOT", root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=seller.build_app("claude", 12345)),
                                       base_url="http://test", headers={"A2A-Version": "1.0"})
        self.addAsyncCleanup(self.client.aclose)

    async def rpc(self, method, params):
        response = await self.client.post("/", json={"jsonrpc": "2.0", "id": str(uuid4()),
                                                    "method": method, "params": params})
        return response.json()

    async def send(self, text, task_id=None):
        msg = {"role": "ROLE_USER", "messageId": str(uuid4()), "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        return await self.rpc("SendMessage", {"message": msg})

    def task(self, payload):
        self.assertNotIn("error", payload)
        return payload["result"].get("task", payload["result"])

    async def test_counteroffer_receipt_and_terminal_task(self):
        with patch.object(seller, "run_harness", side_effect=[decision("ask", 150), decision("ask", 130),
                                                             decision("accept", 120)]) as harness:
            first = self.task(await self.send("I want to buy a lamp"))
            tid = first["id"]
            counter = self.task(await self.send("80", tid))
            self.assertEqual(counter["id"], tid)
            self.assertEqual(counter["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
            self.assertIn('"current_offer": 80', harness.call_args.args[1])
            done = self.task(await self.send("120", tid))
            self.assertEqual(done["status"]["state"], "TASK_STATE_COMPLETED")
            self.assertEqual(done["id"], tid)
            receipt = done["artifacts"][0]["parts"][0]["data"]
            self.assertEqual(receipt["price"], 120)
            self.assertEqual(receipt["item"], "lamp")
            self.assertIn("error", await self.send("125", tid))
            self.assertEqual(harness.call_count, 3)

    async def test_reject_and_cancel_have_no_receipts(self):
        with patch.object(seller, "run_harness", side_effect=[decision("ask", 350), decision("reject", None),
                                                             decision("ask", 250)]):
            first = self.task(await self.send("bookshelf"))
            rejected = self.task(await self.send("1", first["id"]))
            self.assertEqual(rejected["status"]["state"], "TASK_STATE_REJECTED")
            self.assertFalse(rejected.get("artifacts"))
            chair = self.task(await self.send("chair"))
            canceled = self.task(await self.rpc("CancelTask", {"id": chair["id"]}))
            self.assertEqual(canceled["status"]["state"], "TASK_STATE_CANCELED")
            self.assertFalse(canceled.get("artifacts"))

    async def test_invalid_offer_does_not_call_harness_or_lose_task(self):
        with patch.object(seller, "run_harness", side_effect=[decision("ask", 150), decision("ask", 125)]) as harness:
            first = self.task(await self.send("lamp"))
            tid = first["id"]
            for text in ("-80", "80.5", "I offer 80"):
                self.assertIn("error", await self.send(text, tid))
            self.assertEqual(harness.call_count, 1)
            counter = self.task(await self.send("80", tid))
            self.assertEqual(counter["status"]["state"], "TASK_STATE_INPUT_REQUIRED")

    async def test_invalid_acceptance_is_corrected_not_clamped(self):
        with patch.object(seller, "run_harness", side_effect=[decision("ask", 150), decision("accept", 90),
                                                             decision("ask", 135)]) as harness:
            first = self.task(await self.send("lamp"))
            response = self.task(await self.send("90", first["id"]))
            self.assertEqual(response["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
            self.assertFalse(response.get("artifacts"))
            self.assertIn("asking 135", response["status"]["message"]["parts"][0]["text"])
            self.assertEqual(harness.call_count, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
