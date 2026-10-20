"""Model-free ASGI and state tests for the Week 06 seller."""
import itertools
import unittest
from unittest.mock import patch

import httpx

import harness
import seller


class SellerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.executor = seller.Seller()
        self.app = seller.build_app(18765, self.executor)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.ids = itertools.count(1)

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)

    async def rpc(self, method, params):
        response = await self.client.post(
            "/", headers={"A2A-Version": "1.0"},
            json={"jsonrpc": "2.0", "id": str(next(self.ids)), "method": method, "params": params},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    async def send(self, text, task_id=None):
        message = {"role": "ROLE_USER", "messageId": f"m-{next(self.ids)}", "parts": [{"text": text}]}
        if task_id:
            message["taskId"] = task_id
        return await self.rpc("SendMessage", {"message": message})

    @staticmethod
    def task(response):
        result = response["result"]
        return result.get("task", result)

    async def open_item(self, item, ask):
        decision = {"action": "ask", "price": ask, "reason": "opening"}
        with patch("seller.decide_seller", return_value=decision) as decide:
            response = await self.send(f"I want to buy a {item}")
        return self.task(response), decide

    async def test_port_parser(self):
        self.assertEqual(seller.parse_args(["--port", "9999"]).port, 9999)
        with self.assertRaises(SystemExit):
            seller.parse_args(["--port", "0"])

    async def test_agent_card_path_fields_and_port(self):
        response = await self.client.get("/.well-known/agent-card.json")
        self.assertEqual(response.status_code, 200)
        card = response.json()
        self.assertEqual(card["name"], seller.SELLER_NAME)
        self.assertTrue(card["skills"])
        interface = card["supportedInterfaces"][0]
        self.assertEqual(interface["protocolBinding"], "JSONRPC")
        self.assertEqual(interface["protocolVersion"], "1.0")
        self.assertEqual(interface["url"], "http://127.0.0.1:18765/")

    async def test_each_item_opens_input_required_with_last_number_as_ask(self):
        for item, ask in (("lamp", 130), ("chair", 220), ("bookshelf", 300)):
            with self.subTest(item=item):
                task, decide = await self.open_item(item, ask)
                self.assertEqual(task["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
                text = task["status"]["message"]["parts"][0]["text"]
                self.assertTrue(text.endswith(str(ask)))
                context = decide.call_args.args[0]
                self.assertEqual((context.item, context.reserve), (item, seller.RESERVES[item]))

    async def test_follow_up_uses_same_task_and_counteroffers(self):
        task, _ = await self.open_item("lamp", 130)
        task_id = task["id"]
        with patch("seller.decide_seller", return_value={"action": "counter", "price": 115, "reason": "continue"}) as decide:
            next_task = self.task(await self.send("I offer 80", task_id))
        self.assertEqual(next_task["id"], task_id)
        self.assertEqual(next_task["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
        self.assertTrue(next_task["status"]["message"]["parts"][0]["text"].endswith("115"))
        context = decide.call_args.args[0]
        self.assertEqual(context.last_buyer_offer, 80)
        self.assertEqual(len(context.history), 3)

    async def test_accept_completes_with_integer_receipt_and_terminal_reuse_errors(self):
        task, _ = await self.open_item("lamp", 110)
        task_id = task["id"]
        with patch("seller.decide_seller", return_value={"action": "accept", "price": 110, "reason": "deal"}):
            completed = self.task(await self.send("110", task_id))
        self.assertEqual(completed["status"]["state"], "TASK_STATE_COMPLETED")
        price = completed["artifacts"][0]["parts"][0]["data"]["price"]
        # A2A data Parts use protobuf Struct numbers, represented as JSON floats.
        self.assertIsInstance(price, (int, float))
        self.assertEqual(int(price), 110)
        self.assertEqual(price, int(price))
        self.assertIn("error", await self.send("110", task_id))

    async def test_reject_is_terminal(self):
        task, _ = await self.open_item("chair", 220)
        with patch("seller.decide_seller", return_value={"action": "reject", "price": None, "reason": "stop"}):
            rejected = self.task(await self.send("100", task["id"]))
        self.assertEqual(rejected["status"]["state"], "TASK_STATE_REJECTED")
        self.assertEqual(self.executor.negotiations[task["id"]].state, "rejected")

    async def test_low_opening_and_low_accept_are_safely_rejected(self):
        with patch("seller.decide_seller", return_value={"action": "ask", "price": 99, "reason": "bad"}):
            low_open = self.task(await self.send("lamp"))
        self.assertEqual(low_open["status"]["state"], "TASK_STATE_REJECTED")
        task, _ = await self.open_item("chair", 220)
        with patch("seller.decide_seller", return_value={"action": "accept", "price": 100, "reason": "bad"}):
            low_sale = self.task(await self.send("100", task["id"]))
        self.assertEqual(low_sale["status"]["state"], "TASK_STATE_REJECTED")

    async def test_harness_failure_is_safely_rejected(self):
        with patch("seller.decide_seller", side_effect=harness.HarnessError("mock timeout")) as decide:
            task = self.task(await self.send("bookshelf"))
        self.assertEqual(task["status"]["state"], "TASK_STATE_REJECTED")
        decide.assert_called_once()

    async def test_negotiations_are_isolated(self):
        lamp, _ = await self.open_item("lamp", 130)
        chair, _ = await self.open_item("chair", 220)
        self.assertNotEqual(lamp["id"], chair["id"])
        self.assertEqual((self.executor.negotiations[lamp["id"]].item,
                          self.executor.negotiations[lamp["id"]].ask), ("lamp", 130))
        self.assertEqual((self.executor.negotiations[chair["id"]].item,
                          self.executor.negotiations[chair["id"]].ask), ("chair", 220))

    async def test_cancel_marks_negotiation_canceled(self):
        task, _ = await self.open_item("lamp", 130)
        canceled = self.task(await self.rpc("CancelTask", {"id": task["id"]}))
        self.assertEqual(canceled["status"]["state"], "TASK_STATE_CANCELED")
        self.assertEqual(self.executor.negotiations[task["id"]].state, "canceled")


if __name__ == "__main__":
    unittest.main()
