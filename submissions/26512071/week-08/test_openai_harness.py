"""Offline transport fixtures, not market evidence. Run with unittest."""
import importlib.util
import json
import unittest
from unittest.mock import patch

import httpx


class HarnessTests(unittest.TestCase):
    def test_direct_api_contract(self):
        self.assertIsNotNone(importlib.util.find_spec('openai_harness'), 'shared direct API harness is missing')
        import openai_harness as h
        seen = []
        def respond(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={'model': 'gpt-5-nano-2025-08-07', 'choices': [{'finish_reason': 'stop', 'message': {'content': '{"action":"offer","price":100}'}}], 'usage': {'prompt_tokens': 20, 'completion_tokens': 10}})
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            text = h.complete('Return one JSON object.', client=client, api_key='offline-test-key')
        self.assertEqual(json.loads(text)['price'], 100)
        self.assertEqual(seen[0]['model'], 'gpt-5-nano-2025-08-07')
        self.assertEqual(seen[0]['reasoning_effort'], 'minimal')
        self.assertLessEqual(seen[0]['max_completion_tokens'], 256)
        self.assertFalse(seen[0]['store'])
        self.assertNotIn('tools', seen[0])

    def test_buyer_and_seller_use_shared_provider(self):
        import asyncio
        import buyer
        import seller
        self.assertIn('openai_harness', buyer.harness.__code__.co_names, 'buyer still uses the old CLI')
        reply = '{"action":"offer","price":100,"reason":"low"}'
        with patch('openai_harness.complete', return_value=reply) as call:
            self.assertEqual(buyer.harness('buyer prompt'), reply)
            self.assertEqual(asyncio.run(seller.harness('seller prompt')), reply)
        self.assertEqual(call.call_count, 2)

    def test_failures_never_become_decisions_or_leak_keys(self):
        import contextlib
        import io
        import openai_harness as h
        fixtures = [
            (429, {'error': {'message': 'offline-test-key'}}),
            (200, {'choices': [{'finish_reason': 'length', 'message': {'content': '{"price":999}'}}]}),
            (200, {'choices': [{'finish_reason': 'stop', 'message': {'content': 'not json'}}]}),
        ]
        for status, data in fixtures:
            with self.subTest(status=status, data=data):
                stream = io.StringIO()
                transport = httpx.MockTransport(lambda request: httpx.Response(status, json=data))
                with httpx.Client(transport=transport) as client, contextlib.redirect_stderr(stream):
                    self.assertEqual(h.complete('test', client=client, api_key='offline-test-key'), '')
                self.assertNotIn('offline-test-key', stream.getvalue())

    def test_credentials_read_only_and_environment_priority(self):
        import os
        import pathlib
        import tempfile
        import openai_harness as h
        with tempfile.TemporaryDirectory() as directory:
            p = pathlib.Path(directory) / 'auth.json'
            p.write_text('{"OPENAI_API_KEY":"offline-file-key"}', encoding='utf-8')
            before = p.read_bytes()
            with patch.dict(os.environ, {'CODEX_HOME': directory, 'OPENAI_API_KEY': ''}):
                self.assertEqual(h.api_key(), 'offline-file-key')
            with patch.dict(os.environ, {'CODEX_HOME': directory, 'OPENAI_API_KEY': 'offline-env-key'}):
                self.assertEqual(h.api_key(), 'offline-env-key')
            self.assertEqual(p.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
