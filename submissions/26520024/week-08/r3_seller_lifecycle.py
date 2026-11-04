"""Serialize A2A mutations before SDK task/queue creation and replay completed sends."""
import asyncio
from contextlib import asynccontextmanager, contextmanager
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
from r3_clock import clock as time

from r3_common import parse_json
from r3_storage import database_dir

MAX_BODY = 256 * 1024
SEND_METHODS = {'SendMessage', 'message/send', 'send_message', 'sendMessage'}
CANCEL_METHODS = {'CancelTask', 'tasks/cancel', 'cancel_task', 'cancelTask'}
STREAM_METHODS = {'SendStreamingMessage', 'message/stream', 'send_streaming_message', 'sendStreamingMessage'}


def packed(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False)


class RequestJournal:
    def __init__(self, directory):
        self.directory = database_dir(directory, write=True)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'r3_seller_requests.sqlite3'
        self.lock_dir = self.directory / 'seller-request-locks'
        self.lock_dir.mkdir(exist_ok=True)
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS requests '
                       '(message TEXT PRIMARY KEY, digest TEXT NOT NULL, state TEXT NOT NULL, '
                       'created REAL NOT NULL, response BLOB, status INTEGER, headers TEXT)')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @asynccontextmanager
    async def lock(self, key):
        path = self.lock_dir / (hashlib.sha256(key.encode()).hexdigest() + '.lock')
        with path.open('a') as stream:
            deadline = time.monotonic() + 120
            while True:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Another request still owns this task; retry the same messageId')
                    await asyncio.sleep(.02)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def existing(self, message_id, digest):
        with self.connection() as db:
            row = db.execute('SELECT * FROM requests WHERE message=?', (message_id,)).fetchone()
        if row is None:
            return None
        if row['digest'] != digest:
            raise ValueError('messageId was already used with different request content')
        if row['state'] != 'complete':
            raise RuntimeError('Previous attempt has an unresolved outcome; it will not be executed again')
        return dict(status=row['status'], headers=json.loads(row['headers']), body=bytes(row['response']))

    def start(self, message_id, digest):
        with self.connection() as db:
            db.execute('INSERT INTO requests(message,digest,state,created) VALUES(?,?,?,?)',
                       (message_id, digest, 'started', time.time()))

    def finish(self, message_id, response):
        with self.connection() as db:
            db.execute("UPDATE requests SET state='complete',response=?,status=?,headers=? WHERE message=?",
                       (response['body'], response['status'], json.dumps(response['headers']), message_id))


class SellerRequestBoundary:
    """Keep the lock until SDK response aggregation AND producer cleanup have finished."""
    def __init__(self, app, journal):
        self.app, self.journal = app, journal

    @staticmethod
    async def respond(send, response, request_id=None, replay=False):
        body = response['body']
        if replay:
            result = parse_json(body.decode('utf-8'))
            if isinstance(result, dict):
                result['id'] = request_id
                body = packed(result).encode('utf-8')
        headers = [(key.encode('latin1'), value.encode('latin1')) for key, value in response['headers']
                   if key.lower() != 'content-length']
        headers.append((b'content-length', str(len(body)).encode('ascii')))
        await send(dict(type='http.response.start', status=response['status'], headers=headers))
        await send(dict(type='http.response.body', body=body))

    @classmethod
    async def error(cls, send, request_id, code, message, status=200):
        await cls.respond(send, dict(status=status, headers=[('content-type', 'application/json')],
                                    body=packed(dict(jsonrpc='2.0', id=request_id,
                                                     error=dict(code=code, message=message))).encode('utf-8')))

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') != 'POST' or scope.get('path') != '/':
            await self.app(scope, receive, send)
            return
        chunks, size = [], 0
        while True:
            event = await receive()
            if event['type'] == 'http.disconnect':
                return
            chunk = event.get('body', b'')
            size += len(chunk)
            if size > MAX_BODY:
                await self.error(send, None, -32600, 'Request exceeds the seller body limit', 413)
                return
            chunks.append(chunk)
            if not event.get('more_body', False):
                break
        body = b''.join(chunks)
        try:
            request = parse_json(body.decode('utf-8'))
            if not isinstance(request, dict):
                raise ValueError('One JSON-RPC request object is required')
        except (ValueError, UnicodeError) as exc:
            await self.error(send, None, -32700, str(exc), 400)
            return
        method, request_id = request.get('method'), request.get('id')
        if not isinstance(method, str):
            await self.error(send, request_id, -32600, 'A string RPC method is required')
            return
        if method in STREAM_METHODS:
            await self.error(send, request_id, -32601, 'Streaming SendMessage is not supported')
            return
        params = request.get('params', {})
        forwarded = False

        async def replay_receive():
            nonlocal forwarded
            if not forwarded:
                forwarded = True
                return dict(type='http.request', body=body, more_body=False)
            return await receive()

        if method not in SEND_METHODS | CANCEL_METHODS:
            await self.app(scope, replay_receive, send)
            return
        try:
            if not isinstance(params, dict):
                raise ValueError('params must be an object')
            message_id = None
            if method in SEND_METHODS:
                message = params.get('message')
                if not isinstance(message, dict):
                    raise ValueError('message must be an object')
                if any(camel in message and snake in message for camel, snake in
                       (('messageId', 'message_id'), ('taskId', 'task_id'), ('contextId', 'context_id'))):
                    raise ValueError('Duplicate message field aliases are not allowed')
                message_id = message.get('messageId', message.get('message_id'))
                task_id = message.get('taskId', message.get('task_id'))
                if not isinstance(message_id, str) or not 1 <= len(message_id) <= 200:
                    raise ValueError('A bounded, nonempty messageId is required')
                config = params.get('configuration', {})
                if not isinstance(config, dict):
                    raise ValueError('configuration must be an object')
                if (config.get('returnImmediately') or config.get('return_immediately') or
                        ('blocking' in config and config['blocking'] is not True)):
                    raise ValueError('This seller supports blocking SendMessage only')
            else:
                task_id = params.get('id')
                if not task_id:
                    raise ValueError('CancelTask requires an existing task ID')
            if task_id is not None and (not isinstance(task_id, str) or not 1 <= len(task_id) <= 200):
                raise ValueError('Invalid task ID')
        except ValueError as exc:
            await self.error(send, request_id, -32602, str(exc))
            return

        digest = (hashlib.sha256(packed(dict(method='SendMessage', params=params)).encode()).hexdigest()
                  if message_id is not None else None)

        async def execute():
            if message_id is not None:
                self.journal.start(message_id, digest)
            response = dict(status=500, headers=[], body=b'')
            pieces = []

            async def capture(event):
                if event['type'] == 'http.response.start':
                    response['status'] = event['status']
                    response['headers'] = [(key.decode('latin1'), value.decode('latin1'))
                                           for key, value in event.get('headers', [])]
                elif event['type'] == 'http.response.body':
                    pieces.append(event.get('body', b''))

            await self.app(scope, replay_receive, capture)
            response['body'] = b''.join(pieces)
            return response

        async def guarded():
            if task_id:
                async with self.journal.lock('task:' + task_id):
                    return await execute()
            return await execute()

        async def transact():
            if message_id is None:
                return await guarded(), False
            async with self.journal.lock('message:' + message_id):
                previous = self.journal.existing(message_id, digest)
                if previous is not None:
                    return previous, True
                response = await guarded()
                self.journal.finish(message_id, response)
                return response, False

        # Client cancellation must not release the task lock while the SDK still owns its queue.
        work = asyncio.create_task(transact())
        try:
            response, replay = await asyncio.shield(work)
        except asyncio.CancelledError:
            await work
            raise
        except ValueError as exc:
            await self.error(send, request_id, -32602, str(exc))
            return
        except (RuntimeError, TimeoutError) as exc:
            await self.error(send, request_id, -32001, str(exc))
            return
        await self.respond(send, response, request_id, replay)
