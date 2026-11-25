"""Exercise the real A2A path and signatures with deterministic model doubles."""
import asyncio
import base64
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import AsyncMock, Mock, patch

from a2a.helpers import new_text_part
from a2a.types import Message
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient

import harness
from seller_agent import DEFAULT, Seller, bounded_history, build

ROOT = Path(__file__).resolve().parent
CONFIG = {**DEFAULT, **json.loads((ROOT / "shop-adaptive.json").read_text())}
POLICY = {"version": "first", "target": 125, "hint": "balanced"}


def send(client, text, task_id=None):
    message = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        message["taskId"] = task_id
    response = client.post("/", headers={"A2A-Version": "1.0"}, json={
        "jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "SendMessage",
        "params": {"message": message}})
    body = response.json()
    if "error" in body:
        raise AssertionError(body)
    return body["result"].get("task", body["result"])


class SellerTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        p = patch("seller_agent.ROOT", self.root)
        p.start()
        self.addCleanup(p.stop)

    def test_policy_is_pinned_and_signed_receipt_is_not_called_settlement(self):
        key = Ed25519PrivateKey.generate()
        with patch("seller_agent.StrategyReader.for_item", side_effect=[POLICY, {**POLICY, "version": "second"}]) as reader, \
                patch("seller_agent.load_shop_key", return_value=key), \
                patch("seller_agent.opening_price", side_effect=[125, 225]) as opening, \
                patch("seller_agent.seller_decision", return_value={"action": "accept", "price": 120}) as decide, \
                TestClient(build({**CONFIG, "sign_receipts": True})) as client:
            first = send(client, "I want a lamp")
            done = send(client, "120", first["id"])
            send(client, "I want a chair")
        self.assertEqual(reader.call_count, 2)
        self.assertEqual(decide.call_args.kwargs["adaptive"]["version"], "first")
        self.assertEqual(opening.call_args.kwargs["adaptive"]["version"], "second")
        receipt = done["artifacts"][0]["parts"][0]["data"]
        payload = {k: receipt[k] for k in ("shop", "item", "price", "task")}
        payload["price"] = int(payload["price"])
        key.public_key().verify(base64.b64decode(receipt["sig_seller"]),
                                json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
        event = json.loads((self.root / "logs" / "seller-events.jsonl").read_text())
        self.assertEqual(event["outcome"], "receipt_issued")
        self.assertEqual(event["strategy"], "first")
        self.assertEqual(event["price"], 120)

    def test_missing_snapshot_uses_baseline_without_network_or_extra_model_call(self):
        with patch("seller_agent.opening_price", return_value=125) as opening, \
                TestClient(build(CONFIG)) as client:
            send(client, "I want a lamp")
        opening.assert_called_once()
        self.assertNotIn("adaptive", opening.call_args.kwargs)

    def test_below_reserve_or_wrong_acceptance_cannot_issue_receipt(self):
        for bad in ({"action": "accept", "price": 90}, {"action": "accept", "price": 121},
                    {"action": "counter", "price": 140}):
            with self.subTest(bad=bad), patch("seller_agent.opening_price", return_value=125), \
                    patch("seller_agent.seller_decision", return_value=bad), TestClient(build(CONFIG)) as client:
                first = send(client, "I want a lamp")
                done = send(client, "120", first["id"])
                self.assertEqual(done["status"]["state"], "TASK_STATE_FAILED")
                self.assertFalse(done.get("artifacts"))

    def test_max_turns_end_bargaining_without_another_model_call(self):
        config = {**CONFIG, "adaptive": {"enabled": True, "max_turns": 2}}
        with patch("seller_agent.opening_price", return_value=125), \
                patch("seller_agent.seller_decision", return_value={"action": "counter", "price": 120}) as decide, \
                TestClient(build(config)) as client:
            first = send(client, "I want a lamp")
            for _ in range(2):
                send(client, "90", first["id"])
            done = send(client, "90", first["id"])
        self.assertEqual(decide.call_count, 2)
        self.assertEqual(done["status"]["state"], "TASK_STATE_REJECTED")

    def test_exact_quote_can_be_accepted_at_turn_limit(self):
        config = {**CONFIG, "adaptive": {"enabled": True, "max_turns": 2}}
        with patch("seller_agent.opening_price", return_value=125), \
                patch("seller_agent.seller_decision", side_effect=[
                    {"action": "counter", "price": 120}, {"action": "counter", "price": 120},
                    {"action": "accept", "price": 120}]), TestClient(build(config)) as client:
            first = send(client, "I want a lamp")
            send(client, "90", first["id"])
            send(client, "90", first["id"])
            done = send(client, "120", first["id"])
        self.assertEqual(done["status"]["state"], "TASK_STATE_COMPLETED")

    def test_history_is_bounded_and_latest_price_survives(self):
        history = [{"role": "buyer", "text": "x" * 1000}] * 100
        history += [{"role": "seller", "text": "Counteroffer: 120."}]
        result = bounded_history(history)
        self.assertEqual(len(result), 7)
        self.assertLessEqual(sum(len(r["text"]) for r in result), 2800)
        self.assertEqual(result[-1]["text"], "Counteroffer: 120.")

    def test_model_output_must_stay_in_experimental_opening_band(self):
        def decide(prompt, context, validator, **_):
            return validator('{"price":200,"reason":"overcharge"}')
        with patch("harness.decide", side_effect=decide), self.assertRaises(harness.HarnessError):
            harness.opening_price("lamp", 100, "lamp", adaptive=POLICY)


class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_keeps_slot_until_worker_finishes(self):
        seller = Seller(DEFAULT)
        release = threading.Event()
        calls = []
        def slow():
            calls.append(1)
            release.wait(3)
            return 125
        jobs = [asyncio.create_task(seller.infer(slow)) for _ in range(2)]
        try:
            await asyncio.sleep(.05)
            jobs[0].cancel()
            with self.assertRaises(asyncio.CancelledError):
                await jobs[0]
            with self.assertRaises(harness.HarnessError):
                await seller.infer(slow)
            self.assertEqual(len(calls), 2)
        finally:
            release.set()
            await asyncio.gather(*jobs, return_exceptions=True)
            await asyncio.sleep(.05)

    async def test_busy_shop_never_starts_a_third_model_call(self):
        seller = Seller(DEFAULT)
        release = threading.Event()
        calls = []
        def slow_quote(*args, **kwargs):
            calls.append(args)
            release.wait(3)
            return 125
        contexts = [SimpleNamespace(current_task=SimpleNamespace(id=f"t{i}", context_id=f"c{i}"),
                                   message=Message(parts=[new_text_part("lamp")])) for i in range(3)]
        up = Mock(requires_input=AsyncMock(), failed=AsyncMock())
        with patch("seller_agent.opening_price", side_effect=slow_quote), patch("seller_agent.TaskUpdater", return_value=up):
            jobs = [asyncio.create_task(seller.execute(context, object())) for context in contexts]
            try:
                await asyncio.sleep(.25)
                self.assertEqual(len(calls), 2)
                up.failed.assert_awaited_once()
            finally:
                release.set()
                await asyncio.gather(*jobs)


if __name__ == "__main__":
    unittest.main()
