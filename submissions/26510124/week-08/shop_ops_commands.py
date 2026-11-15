"""Authenticated fixed operations commands. Free text is never executed.

Key file is operator-owned 0600, generated only by explicit init-key. Signed
requests bind identity, time, nonce, command, CAS version and request ID. A
request file is durable before its nonce is consumed; replays are rejected.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time

from shop_ops_policy import atomic_write, guard_state_path, immutable_record, read_json, state_dir, writer_lock

COMMANDS = {'status', 'analyse', 'apply', 'rollback', 'evaluate'}
STATES = {'received', 'analysing', 'proposed', 'applied', 'verifying', 'completed', 'rolled_back'}
TRANSITIONS = {
    'received': {'analysing'},
    'analysing': {'analysing', 'proposed', 'applied', 'verifying', 'completed', 'rolled_back'},
    'proposed': {'applied', 'verifying', 'completed'},
    'applied': {'applied', 'verifying', 'completed', 'rolled_back'},
    'verifying': {'verifying', 'completed', 'rolled_back'},
}


def key_bytes(path):
    path = guard_state_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != 64):
            raise ValueError('operator key must be owned by this user with mode 0600')
        raw = handle.read(65)
    try:
        if not re.fullmatch(rb'[0-9a-f]{64}', raw):
            raise ValueError('invalid key encoding')
        return bytes.fromhex(raw.decode())
    except ValueError as exc:
        raise ValueError('invalid operator key') from exc


def init_key(path):
    path = guard_state_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(secrets.token_hex(32).encode())
        f.flush()
        os.fsync(f.fileno())


def signature(body, key):
    return hmac.new(key, json.dumps(body, sort_keys=True, separators=(',', ':'),
                                   allow_nan=False).encode(), hashlib.sha256).hexdigest()


def signed_request(command, *, key_path, request_id, args=None, now=None):
    body = {'schema': 1, 'request_id': request_id, 'operator_uid': os.getuid(),
            'time': time.time() if now is None else now, 'nonce': secrets.token_hex(16),
            'command': command, 'args': args or {}}
    return {**body, 'signature': signature(body, key_bytes(key_path))}


def accept(root, envelope, *, key_path, now=None):
    now = time.time() if now is None else now
    required = {'schema', 'request_id', 'operator_uid', 'time', 'nonce', 'command', 'args', 'signature'}
    if not isinstance(envelope, dict) or set(envelope) != required:
        raise ValueError('invalid command envelope')
    body = {k: v for k, v in envelope.items() if k != 'signature'}
    if (not isinstance(envelope['signature'], str)
            or not hmac.compare_digest(envelope['signature'], signature(body, key_bytes(key_path)))):
        raise ValueError('operator authentication failed')
    if (type(body['schema']) is not int or body['schema'] != 1 or type(body['operator_uid']) is not int
            or body['operator_uid'] != os.getuid() or type(body['time']) not in (int, float)
            or not 0 <= now - body['time'] <= 300):
        raise ValueError('wrong operator or stale command')
    for key in ('request_id', 'nonce'):
        if not isinstance(body[key], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}', body[key]):
            raise ValueError('invalid request identity')
    cmd, args = body['command'], body['args']
    if not isinstance(cmd, str) or cmd not in COMMANDS or not isinstance(args, dict):
        raise ValueError('command not allowed')
    allowed = {'apply': {'decision_id', 'expected_version'}, 'rollback': {'expected_version'},
               'evaluate': {'decision_id'}, 'analyse': set(), 'status': set()}[cmd]
    if set(args) != allowed or any(not isinstance(v, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', v) for v in args.values()):
        raise ValueError('invalid fixed command arguments')
    with writer_lock(root):
        nonce = state_dir(root) / 'nonces' / (body['nonce'] + '.json')
        if nonce.exists():
            raise ValueError('replayed command')
        # Recover replay protection if a process stopped after persisting the
        # request but before writing the nonce index. The request is authoritative.
        for path in (state_dir(root) / 'requests').glob('*.json'):
            if read_json(path).get('nonce') == body['nonce']:
                raise ValueError('replayed durable request')
        record = {'request_id': body['request_id'], 'command': cmd, 'args': args,
                  'operator_uid': body['operator_uid'], 'received_at': now, 'state': 'received',
                  'nonce': body['nonce'], 'history': [{'state': 'received', 'time': now}],
                  'decision_id': None, 'result': None}
        immutable_record(root, 'requests', body['request_id'], record)
        immutable_record(root, 'nonces', body['nonce'], {'request_id': body['request_id']})
    return record


def transition(root, request_id, state, *, now, result=None, decision_id=None):
    if (not isinstance(state, str) or state not in STATES or not isinstance(request_id, str)
            or not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}', request_id)
            or decision_id is not None and (not isinstance(decision_id, str) or not re.fullmatch(r'[0-9a-f]{32}', decision_id))):
        raise ValueError('invalid request state')
    with writer_lock(root):
        path = state_dir(root) / 'requests' / (request_id + '.json')
        row = read_json(path)
        if row['state'] in ('completed', 'rolled_back'):
            return row  # final results cannot be overwritten by stale evaluators
        if state not in TRANSITIONS.get(row['state'], set()):
            raise ValueError('request lifecycle regression')
        if decision_id is not None and row.get('decision_id') not in (None, decision_id):
            raise ValueError('request already belongs to another decision')
        row.update(state=state, result=result)
        if decision_id is not None:
            row['decision_id'] = decision_id
        row['history'].append({'state': state, 'time': now, 'result': result})
        atomic_write(path, row)
        return row
