import json
import os
import unittest
from unittest.mock import patch

import harness


class FakeResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


class HarnessTest(unittest.TestCase):
    def test_payload_bounds_cost_and_reasoning(self):
        payload = harness._payload("rules", "state")
        self.assertEqual("gpt-6-luna", payload["model"])
        self.assertEqual({"effort": "none"}, payload["reasoning"])
        self.assertEqual(48, payload["max_output_tokens"])
        self.assertEqual("rules", payload["instructions"])
        self.assertEqual("state", payload["input"])

    def test_collects_output_text_without_assuming_first_item(self):
        response = {
            "output": [
                {"type": "reasoning", "content": []},
                {"type": "message", "content": [
                    {"type": "output_text", "text": '{"action":"offer","price":12}'}
                ]},
            ]
        }
        self.assertEqual('{"action":"offer","price":12}', harness._output_text(response))

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-or-v1-test"}, clear=True)
    def test_routes_openrouter_key_to_openai_model(self):
        provider, endpoint, model, _ = harness._route()
        self.assertEqual("openrouter", provider)
        self.assertEqual(harness.OPENROUTER_API_URL, endpoint)
        self.assertEqual("openai/gpt-6-luna", model)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-openai-test"}, clear=True)
    def test_routes_openai_key_directly(self):
        provider, endpoint, model, _ = harness._route()
        self.assertEqual("openai", provider)
        self.assertEqual(harness.OPENAI_API_URL, endpoint)
        self.assertEqual("gpt-6-luna", model)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-openai-test"})
    @patch("harness.urllib.request.urlopen")
    def test_decide_parses_api_response(self, urlopen):
        urlopen.return_value = FakeResponse({
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": '{"action":"offer","price":12.4}'}
            ]}],
            "usage": {"input_tokens": 10, "output_tokens": 8},
        })
        self.assertEqual({"action": "offer", "price": 12}, harness.decide("rules", "state"))
        request = urlopen.call_args.args[0]
        self.assertNotIn(b"sk-openai-test", request.data)
        self.assertEqual("Bearer sk-openai-test", request.headers["Authorization"])

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_key_fails_closed(self):
        self.assertEqual("", harness.call("rules", "state"))


if __name__ == "__main__":
    unittest.main()
