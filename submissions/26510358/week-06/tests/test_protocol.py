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


if __name__ == "__main__":
    unittest.main()
