"""Durable gate before kit checkout issuance. No market calls and no key access here."""
import asyncio
import base64
from collections import defaultdict
import json
import sqlite3
from r3_clock import clock as time

from starlette.responses import JSONResponse
from cryptography.exceptions import InvalidSignature
from r3_storage import database_dir


def verified_checkout(session, public_key):
    """Read identity only from our persisted, cryptographically verified checkout."""
    if public_key is None or not isinstance(session, dict):
        return None
    try:
        head, body, signature = session['checkout_jwt'].split('.')
        decode = lambda value: base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
        if json.loads(decode(head)).get('alg') != 'EdDSA':
            return None
        public_key.verify(decode(signature), (head + '.' + body).encode('ascii'))
        claims = json.loads(decode(body))
        if (not isinstance(claims, dict) or not claims.get('id') or
                any(claims.get(k) != session.get(k) for k in ('id', 'task', 'unit'))):
            return None
        return claims
    except (InvalidSignature, ValueError, TypeError, KeyError, AttributeError):
        return None


class CheckoutGate:
    def __init__(self, md):
        self.md = md
        self.path = database_dir(md, write=True) / 'r3_seller.sqlite3'

    def audit(self, action, task, status, session=None):
        # Telemetry failure cannot change checkout/settlement correctness.
        if not isinstance(task, str) or not task:
            return
        try:
            from r3_memory import Memory
            session = session or {}
            with Memory(self.md) as memory:
                memory.record('seller_checkout', f'{task}:{action}:{status}',
                    dict(task=task, action=action, http_status=status,
                         unit=session.get('unit'), checkout_id=session.get('id')))
        except (OSError, ValueError, sqlite3.Error):
            pass

    def database(self):
        if not self.path.is_file():
            raise ValueError('Seller reservation database is not ready')
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
        except BaseException:
            db.close()
            raise
        return db

    def begin(self, receipt, now=None):
        now = time.time() if now is None else now
        db = self.database()
        try:
            row = db.execute('SELECT * FROM deals WHERE task=?', (receipt.get('task'),)).fetchone()
            if row is None:
                raise ValueError('Unknown seller receipt')
            data = json.loads(row['data'])
            stored = data.get('receipt', {})
            fields = ('shop', 'item', 'price', 'task', 'sig_seller')
            if (not data.get('checkout_guarded') or type(receipt.get('price')) is not int or
                    any(receipt.get(k) != stored.get(k) for k in fields)):
                raise ValueError('Receipt is not the original guarded promise')
            # A successful checkout is replayed, never reissued, even after restart.
            if data.get('checkout_session'):
                if data['checkout_session'].get('status') == 'canceled' or row['status'] == 'revoked':
                    raise ValueError('Canceled checkout cannot be reopened')
                return stored, data['checkout_session']
            if (data.get('checkout_started') or row['status'] != 'signed_pending' or
                    row['unit'] is None or row['expires'] <= now):
                raise ValueError('Receipt expired, revoked or checkout outcome is unresolved')
            data['checkout_started'] = True
            db.execute('UPDATE deals SET data=? WHERE task=?', (json.dumps(data), row['task']))
            db.commit()
            return stored, None
        finally:
            db.close()

    def begin_completion(self, task, session_id):
        """Persist a possible payment before contacting the market, including across crashes."""
        db = self.database()
        try:
            row = db.execute('SELECT status,unit,data FROM deals WHERE task=?', (task,)).fetchone()
            if row is None:
                raise ValueError('Unknown checkout')
            data = json.loads(row['data'])
            session = data.get('checkout_session', {})
            if (session.get('id') != session_id or session.get('status') != 'ready_for_payment'
                    or row['status'] not in ('signed_pending', 'quarantined')
                    or row['unit'] != session.get('unit')
                    or data.get('completion_state') in ('in_flight', 'uncertain', 'confirmed')):
                raise ValueError('Checkout closed or payment outcome unresolved')
            data['completion_state'] = 'in_flight'
            db.execute('UPDATE deals SET data=? WHERE task=?', (json.dumps(data), task))
            db.commit()
        finally:
            db.close()

    def finish(self, task, session=None, *, no_checkout_issued=False):
        db = self.database()
        try:
            row = db.execute('SELECT data FROM deals WHERE task=?', (task,)).fetchone()
            if row is None:
                raise ValueError('Checkout promise disappeared')
            data = json.loads(row['data'])
            if session is not None:
                if session.get('unit') != data['receipt']['unit'] or session.get('task') != task:
                    raise ValueError('Kit checkout changed the promised unit or task')
                previous = data.get('checkout_session', {})
                if (previous.get('status') in ('canceled', 'completed') and
                        previous.get('status') != session.get('status')):
                    raise ValueError('Terminal checkout state cannot be changed')
                if session.get('status') == 'completed':
                    data['completion_state'] = 'confirmed'
                elif session.get('status') == 'canceled' and data.get('completion_state') in ('in_flight', 'uncertain'):
                    raise ValueError('Cannot cancel an unresolved payment')
                data['checkout_session'] = session
            elif no_checkout_issued and not data.get('checkout_session'):
                data['checkout_started'] = False
            db.execute('UPDATE deals SET data=? WHERE task=?', (json.dumps(data), task))
            db.commit()
        finally:
            db.close()

    def sessions(self):
        if not self.path.is_file():
            return []
        db = sqlite3.connect(self.path)
        try:
            rows = db.execute("SELECT data FROM deals WHERE json_extract(data,'$.checkout_session') IS NOT NULL "
                              "ORDER BY expires DESC LIMIT 500").fetchall()
        finally:
            db.close()
        return [json.loads(row[0])['checkout_session'] for row in rows]


def guarded_shop(base):
    """Extend the installed kit without editing shared course files."""
    class GuardedShop(base):
        def __init__(self, key, market, md=None):
            super().__init__(key, market, md)
            self.gate = CheckoutGate(md)
            self.session_locks = defaultdict(asyncio.Lock)
            for session in self.gate.sessions():
                self.sessions[session['id']] = session

        async def create(self, body, public):
            receipt = body.get('receipt', {}) if isinstance(body, dict) else {}
            items = body.get('line_items') if isinstance(body, dict) else None
            acceptable = body.get('acceptable') if isinstance(body, dict) else None
            if (not isinstance(receipt, dict) or not isinstance(items, list) or len(items) != 1 or
                    not isinstance(items[0], dict) or items[0].get('id') != receipt.get('item') or
                    type(items[0].get('quantity')) is not int or items[0]['quantity'] != 1 or
                    (acceptable is not None and (not isinstance(acceptable, list) or
                        not acceptable or any(not isinstance(sku, str) or not sku for sku in acceptable)))):
                return JSONResponse({'error': 'invalid receipt, line_items or acceptable list'}, status_code=400)
            try:
                stored, replay = self.gate.begin(receipt)
            except (ValueError, TypeError, sqlite3.Error):
                self.gate.audit('create_rejected', receipt.get('task'), 409)
                return JSONResponse({'error': 'receipt not active or checkout unresolved'}, status_code=409)
            if replay is not None:
                if acceptable is not None and replay['line_items'][0]['id'] not in acceptable:
                    return JSONResponse({'error': 'existing checkout does not fit this order'}, status_code=409)
                self.sessions[replay['id']] = replay
                return JSONResponse(replay, status_code=201)
            # Restore authoritative terms after restart, never the buyer's unsigned extras.
            self.terms[stored['task']] = stored
            response = await super().create(body, public)
            payload = json.loads(response.body)
            if response.status_code == 201:
                self.gate.finish(stored['task'], payload)
            elif response.status_code in (400, 409) and 'checkout_jwt' not in payload:
                self.gate.finish(stored['task'], no_checkout_issued=True)
            self.gate.audit('create', stored['task'], response.status_code, payload)
            # Exceptions/crashes leave checkout_started set: uncertainty is not cancellation.
            return response

        async def handle(self, req, path, body, public):
            parts = path.strip('/').split('/')
            if parts[:2] == ['acp', 'checkout_sessions'] and (
                    path != path.strip('/') or len(parts) > 4 or
                    (len(parts) == 4 and parts[3] not in ('complete', 'cancel'))):
                return JSONResponse({'error': 'unknown ACP path'}, status_code=404)
            if (len(parts) == 4 and parts[:2] == ['acp', 'checkout_sessions'] and
                    parts[3] in ('complete', 'cancel')):
                if req.method != 'POST':
                    return JSONResponse({'error': 'POST required'}, status_code=405)
                if parts[2] not in self.sessions:
                    return JSONResponse({'error': 'no such checkout session'}, status_code=404)
                # A cancel must not release/change a session while settle is in flight.
                async with self.session_locks[parts[2]]:
                    return await self._handle(req, path, body, public)
            return await self._handle(req, path, body, public)

        async def _handle(self, req, path, body, public):
            parts = path.strip('/').split('/')
            action = parts[3] if len(parts) == 4 and parts[:2] == ['acp', 'checkout_sessions'] else None
            session = self.sessions.get(parts[2], {}) if action in ('complete', 'cancel') else {}
            if action in ('complete', 'cancel'):
                try:
                    parsed = json.loads(body or b'{}')
                    if not isinstance(parsed, dict):
                        raise ValueError('Expected JSON object')
                except (ValueError, TypeError):
                    return JSONResponse({'error': 'body is not JSON'}, status_code=400)
            if action == 'complete' and session.get('status') == 'ready_for_payment':
                try:
                    self.gate.begin_completion(session['task'], session['id'])
                except (ValueError, KeyError, sqlite3.Error):
                    return JSONResponse({'error': 'checkout closed or payment unresolved'}, status_code=409)
            if action == 'cancel' and session.get('status') == 'ready_for_payment':
                try:
                    # Persist the confirmed local cancellation before publishing it in memory.
                    self.gate.finish(session['task'], dict(session, status='canceled'))
                except (ValueError, KeyError, sqlite3.Error):
                    return JSONResponse({'error': 'payment unresolved; cancellation not confirmed'}, status_code=409)
            response = await super().handle(req, path, body, public)
            if response.status_code == 200 and path.endswith(('/complete', '/cancel')):
                payload = json.loads(response.body)
                if payload.get('task') and payload.get('checkout_jwt'):
                    self.gate.finish(payload['task'], payload)
            if path.endswith(('/complete', '/cancel')):
                session = self.sessions.get(path.strip('/').split('/')[-2], {})
                self.gate.audit(path.rsplit('/', 1)[-1], session.get('task'), response.status_code, session)
            return response
    return GuardedShop
