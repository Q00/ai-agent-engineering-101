"""STEP 5 seller boundary checks. All model answers here are synthetic fixtures."""

import json
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

import seller


def decision(action, price):
    return {"action": action, "price": price, "reason": "local STEP 5 fixture"}


class SellerLimits(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        root = seller.ROOT / "logs/step5-tests" / uuid4().hex
        (root / "prompts").mkdir(parents=True)
        (root / "prompts/seller.txt").write_text(
            (seller.ROOT / "prompts/seller.txt").read_text(encoding="utf-8"), encoding="utf-8")
        root_patch = patch.object(seller, "ROOT", root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=seller.build_app("claude", 12345)),
                                       base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def rpc(self, method, params, version=True):
        response = await self.client.post("/", headers={"A2A-Version": "1.0"} if version else {},
                                          json={"jsonrpc": "2.0", "id": str(uuid4()),
                                                "method": method, "params": params})
        return response.json()

    async def send(self, text, tid=None):
        message = {"role": "ROLE_USER", "messageId": str(uuid4()), "parts": [{"text": text}]}
        if tid:
            message["taskId"] = tid
        return await self.rpc("SendMessage", {"message": message})

    def task(self, payload):
        self.assertNotIn("error", payload)
        return payload["result"].get("task", payload["result"])

    async def test_all_item_openings_fail_closed_below_reserve(self):
        for item, reserve in seller.RESERVES.items():
            with self.subTest(item=item), patch.object(seller, "run_harness", return_value=decision("ask", reserve - 1)) as model:
                task = self.task(await self.send(f"I want a {item}"))
                self.assertEqual(task["status"]["state"], "TASK_STATE_FAILED")
                self.assertFalse(task.get("artifacts"))
                self.assertEqual(model.call_count, 2)
                self.assertIn("error", await self.send(str(reserve), task["id"]))
                self.assertEqual(model.call_count, 2)

    async def test_all_item_counteroffers_and_deals_fail_closed_below_reserve(self):
        for item, reserve in seller.RESERVES.items():
            for action in ("ask", "accept"):
                with self.subTest(item=item, action=action), patch.object(
                        seller, "run_harness", side_effect=[decision("ask", reserve + 50),
                                                             decision(action, reserve - 1),
                                                             decision(action, reserve - 1)]) as model:
                    first = self.task(await self.send(item))
                    last = self.task(await self.send(str(reserve - 1), first["id"]))
                    self.assertEqual(last["status"]["state"], "TASK_STATE_FAILED")
                    self.assertFalse(last.get("artifacts"))
                    self.assertEqual(model.call_count, 3)

    async def test_accepted_price_must_equal_current_offer(self):
        with patch.object(seller, "run_harness", side_effect=[decision("ask", 150),
                                                             decision("accept", 130), decision("accept", 130)]):
            first = self.task(await self.send("lamp"))
            last = self.task(await self.send("120", first["id"]))
            self.assertEqual(last["status"]["state"], "TASK_STATE_FAILED")
            self.assertFalse(last.get("artifacts"))

    async def test_finished_tasks_cannot_resume_or_cancel(self):
        cases = [("lamp", [decision("ask", 150), decision("accept", 120)], "120", "COMPLETED"),
                 ("bookshelf", [decision("ask", 350), decision("reject", None)], "230", "REJECTED"),
                 ("chair", [decision("ask", 250)], None, "CANCELED")]
        for item, responses, offer, terminal in cases:
            with self.subTest(state=terminal), patch.object(seller, "run_harness", side_effect=responses) as model:
                first = self.task(await self.send(item))
                tid = first["id"]
                ended = self.task(await self.rpc("CancelTask", {"id": tid}) if offer is None
                                  else await self.send(offer, tid))
                self.assertEqual(ended["status"]["state"], "TASK_STATE_" + terminal)
                calls = model.call_count
                self.assertIn("error", await self.send("120", tid))
                self.assertIn("error", await self.rpc("CancelTask", {"id": tid}))
                stored = self.task(await self.rpc("GetTask", {"id": tid}))
                self.assertEqual(stored["status"]["state"], "TASK_STATE_" + terminal)
                self.assertEqual(len(stored.get("artifacts", [])), 1 if terminal == "COMPLETED" else 0)
                self.assertEqual(model.call_count, calls)

    async def test_interleaved_tasks_keep_their_own_reserves_and_history(self):
        with patch.object(seller, "run_harness", side_effect=[decision("ask", 150), decision("ask", 350),
                                                             decision("ask", 125), decision("ask", 300)]) as model:
            lamp = self.task(await self.send("lamp"))
            book = self.task(await self.send("bookshelf"))
            self.assertNotEqual(lamp["id"], book["id"])
            await self.send("80", lamp["id"])
            lamp_input = json.loads(model.call_args.args[1].split("Input data:\n", 1)[1])
            await self.send("230", book["id"])
            book_input = json.loads(model.call_args.args[1].split("Input data:\n", 1)[1])
            for context, item, reserve in ((lamp_input, "lamp", 100), (book_input, "bookshelf", 260)):
                self.assertEqual(context["item"], item)
                self.assertEqual(context["reserve"], reserve)
                self.assertEqual(context["history"][0]["text"], item)
                self.assertEqual(len(context["history"]), 2)

    async def test_invalid_protocol_or_item_does_not_call_model(self):
        with patch.object(seller, "run_harness") as model:
            no_version = await self.rpc("SendMessage", {"message": {
                "role": "ROLE_USER", "messageId": str(uuid4()), "parts": [{"text": "lamp"}]}}, version=False)
            self.assertEqual(no_version["error"]["code"], -32009)
            for text in ("sofa", "lamp and chair", "lampoon"):
                self.assertIn("error", await self.send(text))
            self.assertIn("error", await self.send("80", str(uuid4())))
            model.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
