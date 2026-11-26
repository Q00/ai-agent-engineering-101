"""TEST ONLY: simulated upstreams exercise real gateway routing, without market trades."""
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from starlette.applications import Starlette
from starlette.routing import Route

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shop_service import CARD, Generation, ShopService


class ShopServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.opening_started, self.release_opening = asyncio.Event(), asyncio.Event()
        self.block_opening = False
        self.fail_upstream = False
        self.service = ShopService('https://stable.example')
        self.one = Generation(1, 'http://127.0.0.1:1111', SimpleNamespace(pid=1111, returncode=None))
        self.two = Generation(2, 'http://127.0.0.1:2222', SimpleNamespace(pid=2222, returncode=None))
        self.service.generations = [self.one]
        self.service.active = self.one

        async def upstream(request):
            if self.fail_upstream:
                raise httpx.ReadTimeout('TEST ONLY lost reply')
            self.calls.append(request)
            if request.method == 'GET':
                return httpx.Response(200, json={'name': 'TEST ONLY shop', 'description': 'unchanged',
                    'supportedInterfaces': [{'protocolBinding': 'JSONRPC', 'url': 'http://127.0.0.1/'}]})
            body = json.loads(request.content)
            params = body['params']
            task_id = self.service.requested_task(body)
            if not task_id:
                task_id = 'test-task-on-' + str(request.url.port)
                if self.block_opening:
                    self.opening_started.set()
                    await self.release_opening.wait()
            task = {'id': task_id, 'status': {'state': 'TASK_STATE_INPUT_REQUIRED'},
                    'metadata': {'test_only_upstream': request.url.port}}
            return httpx.Response(200, json={'jsonrpc': '2.0', 'id': body['id'], 'result': {'task': task}})
        self.service.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = Starlette(routes=[Route('/{path:path}', self.service.public, methods=['GET', 'POST'])])
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://stable.example')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.service.http.aclose()

    async def send(self, task=None, method='SendMessage'):
        params = {'message': {'messageId': 'test-only', 'role': 'ROLE_USER', 'parts': [{'text': 'TEST ONLY quote'}]}}
        if task:
            params['message']['taskId'] = task
        if method != 'SendMessage':
            params = {'id': task}
        response = await self.client.post('/a2a/0', headers={'A2A-Version': '1.0', 'Authorization': 'test-only'},
            json={'jsonrpc': '2.0', 'id': 'test-only', 'method': method, 'params': params})
        response.raise_for_status()
        return response.json()['result']['task']

    async def activate_two(self):
        with patch.object(self.service, 'spawn', AsyncMock(return_value=self.two)):
            await self.service.reload()

    async def test_reload_preserves_old_task_continuations_get_cancel_and_terminal_replays(self):
        first = await self.send()
        await self.activate_two()
        new = await self.send()
        self.assertEqual(new['metadata']['test_only_upstream'], 2222)
        for method in ('SendMessage', 'GetTask', 'CancelTask', 'SendMessage'):
            continuation = await self.send(first['id'], method)
            self.assertEqual(continuation['metadata']['test_only_upstream'], 1111)
            self.assertEqual(continuation['id'], first['id'])
        self.assertEqual(self.calls[-1].headers['A2A-Version'], '1.0')
        self.assertEqual(self.calls[-1].headers['Authorization'], 'test-only')

    async def test_opening_in_flight_during_reload_retains_original_owner(self):
        self.block_opening = True
        opening = asyncio.create_task(self.send())
        await asyncio.wait_for(self.opening_started.wait(), 1)
        await self.activate_two()
        self.release_opening.set()
        first = await opening
        self.assertIs(self.service.tasks[first['id']], self.one)
        followup = await self.send(first['id'])
        self.assertEqual(followup['metadata']['test_only_upstream'], 1111)

    async def test_card_keeps_public_origin_across_reload_and_disables_stale_cache(self):
        for _ in range(2):
            response = await self.client.get(CARD)
            self.assertEqual(response.json()['description'], 'unchanged')
            self.assertEqual(response.json()['supportedInterfaces'][0]['url'], 'https://stable.example/a2a/0')
            self.assertEqual(response.headers['cache-control'], 'no-store')
            await self.activate_two()

    async def test_bad_new_generation_never_replaces_current_seller(self):
        with patch.object(self.service, 'spawn', AsyncMock(side_effect=RuntimeError('TEST ONLY unhealthy'))):
            with self.assertRaises(RuntimeError):
                await self.service.reload()
        self.assertIs(self.service.active, self.one)
        self.assertEqual((await self.send())['metadata']['test_only_upstream'], 1111)

    async def test_control_is_not_public_and_ambiguous_post_is_never_retried(self):
        response = await self.client.post('/reload', json={'action': 'reload'})
        self.assertEqual(response.status_code, 404)
        self.fail_upstream = True
        with patch.object(self.service.http, 'request', wraps=self.service.http.request) as request:
            response = await self.client.post('/a2a/0', json={'params': {}})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(request.call_count, 1)

    async def test_dead_original_generation_cannot_route_purchase_to_new_seller(self):
        first = await self.send()
        await self.activate_two()
        self.one.process = type('ExitedTestProcess', (), {'returncode': 1})()
        response = await self.client.post('/a2a/0', json={'params': {'message': {'taskId': first['id']}}})
        self.assertEqual(response.status_code, 503)
        self.assertEqual((await self.send())['metadata']['test_only_upstream'], 2222)


if __name__ == '__main__':
    unittest.main()
