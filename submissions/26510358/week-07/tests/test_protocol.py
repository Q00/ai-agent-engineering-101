"""Focused protocol correlation and initialization deadline regressions."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from protocol import A2AClient, ProtocolError


class SilentTrace:
    def write(self, *args, **kwargs):
        pass


class Response:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class HTTPDouble:
    def __init__(self, card=None, transform=None):
        self.card = card if card is not None else {"supportedInterfaces": [
            {"protocolBinding": "JSONRPC", "protocolVersion": "1.0", "url": "http://shop/"}]}
        self.transform, self.closed, self.gets, self.posts = transform, False, [], []

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return Response(self.card)

    def post(self, url, *, json, **kwargs):
        self.posts.append((url, json, kwargs))
        result = {"jsonrpc": "2.0", "id": json["id"], "result": {"task": {"id": "task-1"}}}
        return Response(self.transform(result) if self.transform else result)

    def close(self):
        self.closed = True


class ProtocolTests(unittest.TestCase):
    def test_card_get_and_rpc_honor_remaining_deadline(self):
        http = HTTPDouble()
        with patch("protocol.httpx.Client", return_value=http), patch("protocol.time.monotonic", return_value=100):
            client = A2AClient("http://shop", SilentTrace(), deadline=105)
            self.assertEqual(client.send("hello")["id"], "task-1")
            self.assertEqual(http.gets[0][1]["timeout"], 5)
            self.assertEqual(http.posts[0][2]["timeout"], 5)
            client.close()
        self.assertTrue(http.closed)

    def test_configured_timeout_also_bounds_rpc(self):
        http = HTTPDouble()
        with patch("protocol.httpx.Client", return_value=http):
            client = A2AClient("http://shop", SilentTrace(), timeout=2)
            client.send("hello")
            self.assertEqual(http.posts[0][2]["timeout"], 2)
            client.close()

    def test_expired_initial_deadline_closes_client_before_network(self):
        http = HTTPDouble()
        with patch("protocol.httpx.Client", return_value=http), patch("protocol.time.monotonic", return_value=100):
            with self.assertRaises(TimeoutError):
                A2AClient("http://shop", SilentTrace(), deadline=99)
        self.assertTrue(http.closed)
        self.assertEqual(http.gets, [])

    def test_invalid_card_or_origin_closes_initialized_client(self):
        for card in ([], {}, {"supportedInterfaces": [{"protocolBinding": "JSONRPC", "protocolVersion": "1.0",
                                                       "url": "http://different-shop/"}]}):
            http = HTTPDouble(card=card)
            with self.subTest(card=card), patch("protocol.httpx.Client", return_value=http):
                with self.assertRaises(ProtocolError):
                    A2AClient("http://shop", SilentTrace())
            self.assertTrue(http.closed)

    def test_changed_response_id_version_and_shape_are_rejected(self):
        transforms = [lambda body: {**body, "id": "replayed-response"},
                      lambda body: {**body, "jsonrpc": "1.0"},
                      lambda body: {**body, "error": {"code": -1, "message": "conflict"}},
                      lambda body: {**body, "result": []}, lambda body: []]
        for transform in transforms:
            http = HTTPDouble(transform=transform)
            with self.subTest(transform=transform), patch("protocol.httpx.Client", return_value=http):
                client = A2AClient("http://shop", SilentTrace())
                try:
                    with self.assertRaises(ProtocolError):
                        client.send("hello")
                finally:
                    client.close()

    def test_received_completion_crossing_deadline_is_parsed_but_next_rpc_is_blocked(self):
        clock = {"now": 100}
        receipt = {"item": "lamp", "price": 110, "taskId": "task-1"}

        def complete(body):
            clock["now"] = 106
            return {**body, "result": {"task": {"id": "task-1", "status": {
                "state": "TASK_STATE_COMPLETED"}, "artifacts": [
                    {"parts": [{"data": receipt}]}]}}}

        http = HTTPDouble(transform=complete)
        with patch("protocol.httpx.Client", return_value=http), patch(
                "protocol.time.monotonic", side_effect=lambda: clock["now"]):
            client = A2AClient("http://shop", SilentTrace(), deadline=105)
            try:
                task = client.send("110", "task-1")
                self.assertEqual(task["status"]["state"], "TASK_STATE_COMPLETED")
                self.assertEqual(task["artifacts"][0]["parts"][0]["data"], receipt)
                with self.assertRaises(TimeoutError):
                    client.send("another request", "task-1")
                self.assertEqual(len(http.posts), 1)
            finally:
                client.close()

    def test_deadline_crossing_response_still_requires_valid_rpc_evidence(self):
        for invalid in (lambda body: {**body, "id": "wrong-request"},
                        lambda body: {**body, "result": []}, lambda body: []):
            clock = {"now": 100}

            def after_deadline(body):
                clock["now"] = 106
                return invalid(body)

            http = HTTPDouble(transform=after_deadline)
            with self.subTest(invalid=invalid), patch("protocol.httpx.Client", return_value=http), patch(
                    "protocol.time.monotonic", side_effect=lambda: clock["now"]):
                client = A2AClient("http://shop", SilentTrace(), deadline=105)
                try:
                    with self.assertRaises(ProtocolError):
                        client.send("110", "task-1")
                    self.assertEqual(len(http.posts), 1)
                finally:
                    client.close()

    def test_card_received_after_deadline_remains_rejected(self):
        clock = {"now": 100}

        class SlowCardHTTP(HTTPDouble):
            def get(self, url, **kwargs):
                response = super().get(url, **kwargs)
                clock["now"] = 106
                return response

        http = SlowCardHTTP()
        with patch("protocol.httpx.Client", return_value=http), patch(
                "protocol.time.monotonic", side_effect=lambda: clock["now"]):
            with self.assertRaises(TimeoutError):
                A2AClient("http://shop", SilentTrace(), deadline=105)
        self.assertTrue(http.closed)
        self.assertEqual(http.posts, [])

    def test_buyer_commits_received_completion_after_deadline_without_new_decision(self):
        import buyer
        from evidence import Ledger

        clock, judgments = {"now": 100}, []
        receipt = {"item": "lamp", "price": 110, "taskId": "task-1"}

        class TestPolicy:
            sha256, version = "TEST_ONLY", "TEST_ONLY"

            def choose(self, *args):
                return "balanced"

        def respond(body):
            if len(http.posts) == 1:
                task = {"id": "task-1", "status": {"state": "TASK_STATE_INPUT_REQUIRED", "message": {
                    "metadata": {"ask": 110, "itemId": "lamp"}, "parts": [{"text": "Current ask 110"}]}}}
            else:
                clock["now"] = 106
                task = {"id": "task-1", "status": {"state": "TASK_STATE_COMPLETED"},
                        "artifacts": [{"parts": [{"data": receipt}]}]}
            return {**body, "result": {"task": task}}

        def decide(_instance, context, **kwargs):
            judgments.append(context)
            return kwargs["validator"]({"action": "accept", "price": 110, "item_id": "lamp",
                                        "offer": None, "reason": "TEST ONLY judgment", "stance": None})

        http, ledger = HTTPDouble(transform=respond), Ledger(120)
        with patch("protocol.httpx.Client", return_value=http), patch(
                "protocol.time.monotonic", side_effect=lambda: clock["now"]), patch.object(
                buyer.Harness, "decide", decide):
            result = buyer.negotiate("http://shop", "lamp", 120, ledger=ledger,
                                     policy=TestPolicy(), trace=SilentTrace(), timeout=5)
        self.assertEqual((result["status"], result["receipt"], result["offer_count"], result["market_verified"]), ("completed", receipt, 1, False))
        self.assertEqual(ledger.snapshot()["committed_total"], 110)
        self.assertEqual(ledger.snapshot()["reserved_total"], 0)
        self.assertEqual(len(judgments), 1)
        self.assertEqual(len(http.posts), 2)

    def test_buyer_does_not_authorize_price_from_quote_received_after_deadline(self):
        import buyer
        from evidence import Ledger

        clock = {"now": 100}

        class TestPolicy:
            sha256, version = "TEST_ONLY", "TEST_ONLY"

        def late_quote(body):
            clock["now"] = 106
            return {**body, "result": {"task": {"id": "task-1", "status": {
                "state": "TASK_STATE_INPUT_REQUIRED", "message": {"metadata": {
                    "ask": 110, "itemId": "lamp"}, "parts": [{"text": "Current ask 110"}]}}}}}

        http, ledger = HTTPDouble(transform=late_quote), Ledger(120)
        with patch("protocol.httpx.Client", return_value=http), patch(
                "protocol.time.monotonic", side_effect=lambda: clock["now"]), patch.object(
                buyer.Harness, "decide") as model:
            with self.assertRaises(TimeoutError):
                buyer.negotiate("http://shop", "lamp", 120, ledger=ledger,
                                policy=TestPolicy(), trace=SilentTrace(), timeout=5)
        model.assert_not_called()
        self.assertEqual(len(http.posts), 1)
        self.assertEqual(ledger.snapshot()["reserved_total"], 0)


if __name__ == "__main__":
    unittest.main()
