"""Long-lived public A2A gateway; reload sellers without replacing the tunnel.

Control uses a private Unix socket, never a route exposed through the tunnel.
Existing tasks stay on their originating seller, including terminal replays.
The gateway never selects prices, changes receipts, or retries purchase messages.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import socket
import sys
import time
from urllib.parse import urlparse

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
import uvicorn

ROOT = Path(__file__).resolve().parent
STATE = ROOT / '.market' / 'shop-service'
CARD = '/.well-known/agent-card.json'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@dataclass
class Generation:
    number: int
    base: str
    process: object = None
    log: object = None
    in_flight: int = 0

    @property
    def alive(self):
        return self.process is None or self.process.returncode is None


class ShopService:
    def __init__(self, public_url, *, state=STATE, log_dir=None):
        self.public_url = public_url.rstrip('/')
        self.state = Path(state)
        self.log_dir = Path(log_dir or ROOT / 'logs' / 'runtime')
        self.generations = []
        self.active = None
        self.tasks = {}
        self.reload_lock = asyncio.Lock()
        self.http = None
        self.events = []

    def record(self, event, **fields):
        record = {'time': datetime.now(timezone.utc).isoformat(), 'event': event, **fields}
        print(json.dumps(record), flush=True)
        self.events.append(record)
        # No request bodies, keys, receipts or ownership claims in control state.
        self.events = self.events[-30:]

    async def spawn(self):
        port = free_port()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / ('seller-service-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.log')
        log = path.open('a')
        env = os.environ.copy()
        env.pop('MARKET_OWNER', None)
        process = await asyncio.create_subprocess_exec(sys.executable, '-u', str(ROOT / 'seller.py'),
            '--port', str(port), cwd=ROOT, env=env, stdout=log, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True)
        generation = Generation(len(self.generations) + 1, f'http://127.0.0.1:{port}', process, log)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and generation.alive:
                try:
                    response = await self.http.get(generation.base + CARD, timeout=1)
                    response.raise_for_status()
                    card = response.json()
                    if not card.get('supportedInterfaces'):
                        raise ValueError('Seller has no A2A interface')
                    return generation
                except (httpx.HTTPError, ValueError):
                    await asyncio.sleep(0.1)
            raise RuntimeError('New seller did not become healthy; current seller remains active')
        except BaseException:
            await self.stop_generation(generation)
            raise

    async def reload(self):
        async with self.reload_lock:
            generation = await self.spawn()
            # Commit the new routing target only after the new card answers.
            self.generations.append(generation)
            self.active = generation
            self.record('seller_activated', generation=generation.number, pid=generation.process.pid,
                        preserved_task_routes=len(self.tasks))
            return self.status()

    async def stop_generation(self, generation):
        process = generation.process
        if process and process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
            except ProcessLookupError:
                pass
        if generation.log:
            generation.log.close()

    def status(self):
        return {'public_url': self.public_url, 'active_generation': self.active.number if self.active else None,
                'task_routes': len(self.tasks), 'generations': [
                    {'number': g.number, 'pid': g.process.pid if g.process else None,
                     'alive': g.alive, 'in_flight': g.in_flight,
                     'tasks': sum(v is g for v in self.tasks.values())} for g in self.generations],
                'events': self.events}

    def requested_task(self, body):
        params = body.get('params')
        if not isinstance(params, dict):
            return None
        message = params.get('message')
        if isinstance(message, dict):
            return message.get('taskId') or message.get('task_id')
        return params.get('id') or params.get('taskId') or params.get('task_id')

    async def public(self, request: Request):
        if request.method == 'GET' and request.url.path == '/healthz':
            ready = bool(self.active and self.active.alive)
            return JSONResponse({'ready': ready}, status_code=200 if ready else 503)
        is_card = request.method == 'GET' and request.url.path == CARD
        is_rpc = request.method == 'POST' and request.url.path in ('/', '/a2a/0')
        if not (is_card or is_rpc):
            return JSONResponse({'error': 'Not found'}, status_code=404)
        content = await request.body()
        task_id = None
        if is_rpc:
            try:
                body = json.loads(content)
                if not isinstance(body, dict):
                    raise ValueError('Expected a JSONRPC object')
                task_id = self.requested_task(body)
                if task_id is not None and not isinstance(task_id, str):
                    raise ValueError('Expected a text task ID')
            except (ValueError, TypeError):
                return JSONResponse({'error': 'Invalid JSONRPC request'}, status_code=400)
        generation = self.tasks.get(task_id, self.active)
        if generation is None or not generation.alive:
            # Never redirect an ambiguous existing purchase to another seller.
            return JSONResponse({'error': 'Seller temporarily unavailable'}, status_code=503)
        started = time.monotonic()
        generation.in_flight += 1
        try:
            headers = {k: v for k, v in request.headers.items() if k.lower() in
                       ('content-type', 'a2a-version', 'authorization', 'accept')}
            response = await self.http.request(request.method, generation.base + (CARD if is_card else '/'),
                                                content=content, headers=headers)
            result = response.content
            if is_card and response.status_code == 200:
                card = response.json()
                for interface in card.get('supportedInterfaces', []):
                    interface['url'] = self.public_url + '/a2a/0'
                if card.get('url'):
                    card['url'] = self.public_url + '/a2a/0'
                result = json.dumps(card, ensure_ascii=False).encode()
            elif is_rpc and response.status_code == 200:
                envelope = response.json()
                task = envelope.get('result')
                if isinstance(task, dict):
                    task = task.get('task', task)
                    if isinstance(task, dict) and isinstance(task.get('id'), str) and 'status' in task:
                        self.tasks.setdefault(task['id'], generation)
            return Response(result, status_code=response.status_code,
                            media_type=response.headers.get('content-type'),
                            headers={'Cache-Control': 'no-store'} if is_card else None)
        except (httpx.HTTPError, ValueError) as exc:
            self.record('upstream_error', generation=generation.number, kind=type(exc).__name__)
            # No automatic replay: the request may already have completed upstream.
            return JSONResponse({'error': 'Seller response unavailable; check the same task'}, status_code=502)
        finally:
            generation.in_flight -= 1
            if is_rpc:
                self.record('request_finished', generation=generation.number,
                            continued=bool(task_id), seconds=round(time.monotonic() - started, 3))

    async def control(self, request):
        if request.method == 'GET':
            return JSONResponse(self.status())
        try:
            body = await request.json()
            if body.get('action') == 'reload':
                return JSONResponse(await self.reload())
            if body.get('action') == 'set_public_url':
                url = body.get('url', '')
                parsed = urlparse(url)
                if parsed.scheme != 'https' or not parsed.hostname or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
                    raise ValueError('Expected an HTTPS origin')
                self.public_url = url.rstrip('/')
                return JSONResponse(self.status())
            raise ValueError('Unknown action')
        except (ValueError, RuntimeError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)

    async def monitor(self):
        while True:
            await asyncio.sleep(2)
            if self.active and not self.active.alive:
                self.record('active_seller_exited', generation=self.active.number)
                try:
                    await self.reload()
                except RuntimeError as exc:
                    self.record('seller_restart_failed', detail=str(exc))


async def serve(args):
    service = ShopService(args.public_url, log_dir=args.log_dir)
    service.state.mkdir(parents=True, exist_ok=True, mode=0o700)
    control_socket = service.state / 'control.sock'
    # Refuse duplicate services instead of unlinking a running service's socket.
    if control_socket.exists():
        try:
            with socket.socket(socket.AF_UNIX) as probe:
                probe.connect(str(control_socket))
        except ConnectionRefusedError:
            control_socket.unlink()
        else:
            raise RuntimeError('A shop service already owns the control socket')
    async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(50, connect=2)) as client:
        service.http = client
        await service.reload()
        public_app = Starlette(routes=[Route('/{path:path}', service.public, methods=['GET', 'POST'])])
        control_app = Starlette(routes=[Route('/', service.control, methods=['GET', 'POST'])])
        public_server = uvicorn.Server(uvicorn.Config(public_app, host='127.0.0.1', port=args.port,
                                                      log_level='warning', access_log=False))
        control_server = uvicorn.Server(uvicorn.Config(control_app, uds=str(control_socket),
                                                       log_level='warning', access_log=False))
        # One signal owner stops both servers and their seller generations.
        public_server.capture_signals = async_signal_noop
        control_server.capture_signals = async_signal_noop
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: (setattr(public_server, 'should_exit', True),
                                                 setattr(control_server, 'should_exit', True)))
        monitor = asyncio.create_task(service.monitor())
        servers = [asyncio.create_task(server.serve()) for server in (public_server, control_server)]
        try:
            while not control_server.started and not servers[1].done():
                await asyncio.sleep(0.05)
            if control_socket.exists():
                control_socket.chmod(0o600)
            (service.state / 'pid').write_text(str(os.getpid()))
            await asyncio.gather(*servers)
        finally:
            public_server.should_exit = control_server.should_exit = True
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            for generation in service.generations:
                await service.stop_generation(generation)
            control_socket.unlink(missing_ok=True)


from contextlib import contextmanager


@contextmanager
def async_signal_noop():
    yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('serve')
    run.add_argument('--port', type=int, required=True)
    run.add_argument('--public-url', required=True)
    run.add_argument('--log-dir')
    sub.add_parser('status')
    sub.add_parser('reload')
    update = sub.add_parser('set-public-url')
    update.add_argument('url')
    args = parser.parse_args()
    if args.command == 'serve':
        asyncio.run(serve(args))
    else:
        with httpx.Client(transport=httpx.HTTPTransport(uds=str(STATE / 'control.sock')), timeout=30) as client:
            if args.command == 'status':
                response = client.get('http://localhost/')
            else:
                body = {'action': 'reload'} if args.command == 'reload' else {'action': 'set_public_url', 'url': args.url}
                response = client.post('http://localhost/', json=body)
            response.raise_for_status()
            print(json.dumps(response.json(), indent=2))


if __name__ == '__main__':
    main()
