"""STEP 6: protocol fixtures only. No harness or model calls."""

import json
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

import buyer


def task(state="INPUT_REQUIRED", tid="task-a", price=None):
    value = {"id": tid, "contextId": "context-a", "status": {"state": "TASK_STATE_" + state,
             "message": {"parts": [{"text": "lamp: asking 130"}]}}}
    if price is not None:
        value["artifacts"] = [{"name": "receipt", "parts": [{"data": {"item": "lamp", "price": price}}]}]
    return value


class BuyerTransportChecks(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.root_patch = patch.object(buyer, "ROOT", buyer.ROOT / "logs/step6-tests" / uuid4().hex)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def transport(self, replies=None, card=None):
        replies = iter(replies or [task()])
        default_card = {"supportedInterfaces": [{"protocolBinding": "JSONRPC", "protocolVersion": "1.0",
                                                  "url": "http://fixture/rpc"}]}

        def respond(request):
            self.requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json=default_card if card is None else card)
            data = json.loads(request.content)
            result = next(replies)
            if isinstance(result, Exception):
                raise result
            if isinstance(result, httpx.Response):
                return result
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": data["id"], "result": result})

        return httpx.MockTransport(respond)

    def client(self, **kwargs):
        return buyer.BuyerTransport("http://fixture", "lamp", 120, **kwargs)

    def test_advertised_endpoint_headers_and_task_ids(self):
        with self.client(transport=self.transport([task(), task(), task(), task("CANCELED")])) as client:
            client.open()
            client.offer(80)
            client.get_task()
            client.cancel()
        self.assertEqual(self.requests[0].url.path, "/.well-known/agent-card.json")
        for request in self.requests:
            self.assertEqual(request.headers["A2A-Version"], "1.0")
        for request in self.requests[1:]:
            self.assertEqual(request.url.path, "/rpc")
        initial, followup = [json.loads(r.content)["params"]["message"] for r in self.requests[1:3]]
        self.assertNotIn("taskId", initial)
        self.assertEqual(followup["taskId"], "task-a")
        self.assertEqual(followup["contextId"], "context-a")
        self.assertNotEqual(initial["messageId"], followup["messageId"])

    def test_budget_guard_sends_no_extra_request(self):
        with self.client(transport=self.transport()) as client:
            client.open()
            for value in (121, 0, -1, True, 110.5):
                with self.subTest(value=value), self.assertRaises(buyer.BuyerError):
                    client.offer(value)
            self.assertEqual(len(self.requests), 2)

    def test_terminal_states_cannot_offer_or_cancel(self):
        for state in ("COMPLETED", "REJECTED", "CANCELED", "FAILED"):
            with self.subTest(state=state), self.client(transport=self.transport(
                    [task(state, price=110 if state == "COMPLETED" else None)])) as client:
                client.open()
                count = len(self.requests)
                with self.assertRaises(buyer.BuyerError):
                    client.offer(110)
                with self.assertRaises(buyer.BuyerError):
                    client.cancel()
                self.assertEqual(len(self.requests), count)

    def test_receipt_price_must_be_numeric_and_within_budget(self):
        for price in (121, "110", True, -1):
            with self.subTest(price=price), self.client(transport=self.transport([task("COMPLETED", price=price)])) as client:
                with self.assertRaises(buyer.BuyerError):
                    client.open()
                self.assertIsNone(client.receipt)
        with self.client(transport=self.transport([task(), task("COMPLETED", price=110)])) as client:
            client.open()
            client.offer(110)
            self.assertEqual(client.receipt["price"], 110)

    def test_missing_or_incompatible_interface(self):
        for card in ({}, {"supportedInterfaces": None}, {"supportedInterfaces": 3},
                     {"supportedInterfaces": [{"protocolBinding": "HTTP+JSON", "protocolVersion": "1.0"}]}):
            with self.subTest(card=card), self.client(transport=self.transport(card=card)) as client:
                with self.assertRaises(buyer.BuyerError):
                    client.open()

    def test_changed_task_id_is_rejected(self):
        with self.client(transport=self.transport([task(), task(tid="other-task")])) as client:
            client.open()
            with self.assertRaisesRegex(buyer.BuyerError, "changed taskId"):
                client.offer(80)

    def test_http_and_json_failures_keep_raw_output(self):
        for response in (httpx.Response(503, text="fixture unavailable"),
                         httpx.Response(200, text="not JSON")):
            with self.subTest(status=response.status_code), self.client(transport=self.transport([response])) as client:
                with self.assertRaises(buyer.BuyerError):
                    client.open()
                self.assertEqual((client.log_dir / "02.response.txt").read_bytes(), response.content)

    def test_deadline_stops_network_request(self):
        with self.client(transport=self.transport()) as client:
            client.deadline = time.monotonic() - 1
            with self.assertRaisesRegex(buyer.BuyerError, "time limit"):
                client.open()
            self.assertEqual(self.requests, [])

    def test_jsonrpc_errors_and_mismatched_response_ids(self):
        for error_mode in (True, False):
            def reply(request):
                if request.method == "GET":
                    return httpx.Response(200, json={"supportedInterfaces": [{"protocolBinding": "JSONRPC",
                        "protocolVersion": "1.0", "url": "http://fixture/rpc"}]})
                data = json.loads(request.content)
                if error_mode:
                    return httpx.Response(200, json={"jsonrpc": "2.0", "id": data["id"],
                                                    "error": {"code": -32001, "message": "fixture error"}})
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": "wrong-id", "result": task()})
            with self.subTest(error_mode=error_mode), self.client(transport=httpx.MockTransport(reply)) as client:
                with self.assertRaises(buyer.BuyerError):
                    client.open()

    def test_changed_context_id_is_rejected(self):
        changed = task()
        changed["contextId"] = "other-context"
        with self.client(transport=self.transport([task(), changed])) as client:
            client.open()
            with self.assertRaisesRegex(buyer.BuyerError, "changed contextId"):
                client.offer(80)


if __name__ == "__main__":
    unittest.main(verbosity=2)
