import concurrent.futures
from pathlib import Path
import tempfile
import time
import unittest

from starlette.testclient import TestClient
from local_support import ROOT
from preview_app import create_app


class AppTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".local"
        scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch, prefix="app-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.web = create_app(self.root)
        self.client = TestClient(self.web)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {"x-page-token": self.web.state.page_token}

    def test_assets_and_mobile_manifest(self):
        for path in ['/', '/styles.css', '/app.js', '/brief.js', '/miniapp.js', '/manifest.webmanifest']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.get('/manifest.webmanifest').json()['display'], 'standalone')
        state = self.client.get('/api/preparation').json()
        self.assertFalse(state['market_connected'])
        self.assertFalse(state['wallets_loaded'])
        self.assertFalse(state['forging_enabled'])
        self.assertFalse(list(self.root.rglob('*.key')))

    def test_page_tool_round_trip_through_official_sdk_protocol(self):
        body = {"tools": [{"name": "shopping_brief", "description": "user-saved purchase conditions"}]}
        self.assertEqual(self.client.post('/api/offer', json=body).status_code, 403)
        result = self.client.post('/api/offer', json=body, headers=self.headers)
        self.assertEqual(result.json()['offered'], ['app_shopping_brief'])
        conditions = {"status": "saved", "budget_credits": 32000, "interests": ["우유"],
                      "is_payment_authorization": False}
        # No browser or model is impersonated as production evidence: simulate the
        # SDK's page answer against the actual Bridge and chat endpoints.
        with concurrent.futures.ThreadPoolExecutor() as pool:
            waiting = pool.submit(self.client.post, '/api/chat', json={"text": "구매 조건 알려줘", "user": "fixture"})
            deadline = time.monotonic() + 3
            call = None
            while time.monotonic() < deadline:
                calls = self.client.get('/api/state').json()['calls']
                if calls:
                    call = calls[0]
                    break
                time.sleep(.01)
            self.assertIsNotNone(call)
            self.assertEqual(call['name'], 'app_shopping_brief')
            self.assertEqual(self.client.post('/api/answer', json={"id": call['id'], "result": conditions}).status_code, 403)
            answer = self.client.post('/api/answer', headers=self.headers, json={"id": call['id'], "result": conditions})
            self.assertTrue(answer.json()['ok'])
            response = waiting.result(timeout=3).json()
        self.assertIn('32000', response['reply'])
        self.assertIn('미리보기', response['reply'])
        self.assertEqual(self.client.get('/api/state').json()['forged'], {})

    def test_blank_chat_rejected_and_unknown_call_not_accepted(self):
        self.assertEqual(self.client.post('/api/chat', json={"text": " "}).status_code, 400)
        result = self.client.post('/api/answer', headers=self.headers, json={"id": "missing", "result": {"approved": True}})
        self.assertFalse(result.json()['ok'])

    def test_real_chat_path_uses_model_interface_without_market(self):
        from unittest.mock import Mock
        mind = self.web.state.preview_mind
        mind.model = Mock()
        mind.model.complete.return_value = '{"thought":"fixture","action":"reply","args":{"text":"한국어 응답 연결 확인"}}'
        response = self.client.post('/api/chat', json={"text": "안녕"}).json()
        self.assertEqual(response['reply'], '한국어 응답 연결 확인')
        mind.model.complete.assert_called_once()
        self.assertFalse(list(self.root.rglob('*.key')))

    def test_real_chat_rejects_market_and_forge_actions(self):
        from unittest.mock import Mock
        mind = self.web.state.preview_mind
        mind.model = Mock()
        mind.model.complete.return_value = '{"thought":"fixture","action":"forge_tool","args":{}}'
        response = self.client.post('/api/chat', json={"text": "도구 만들어"}).json()
        self.assertEqual(response['state'], 'rejected')
        self.assertFalse((self.root / 'tools').exists())


if __name__ == '__main__':
    unittest.main()
