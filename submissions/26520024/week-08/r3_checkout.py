"""Durable gate before kit checkout issuance. No market calls and no key access here."""
import json
import sqlite3
import time
from pathlib import Path

from starlette.responses import JSONResponse


class CheckoutGate:
    def __init__(self, md):
        self.path = Path(md) / 'r3_seller.sqlite3'

    def database(self):
        if not self.path.is_file():
            raise ValueError('Seller reservation database is not ready')
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('BEGIN IMMEDIATE')
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
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT data FROM deals WHERE json_extract(data,'$.checkout_session') IS NOT NULL "
                              "ORDER BY expires DESC LIMIT 500").fetchall()
        return [json.loads(row[0])['checkout_session'] for row in rows]


def guarded_shop(base):
    """Extend the installed kit without editing shared course files."""
    class GuardedShop(base):
        def __init__(self, key, market, md=None):
            super().__init__(key, market, md)
            self.gate = CheckoutGate(md)
            for session in self.gate.sessions():
                self.sessions[session['id']] = session

        async def create(self, body, public):
            receipt = body.get('receipt', {}) if isinstance(body, dict) else {}
            if not isinstance(receipt, dict):
                return JSONResponse({'error': 'receipt must be an object'}, status_code=400)
            try:
                stored, replay = self.gate.begin(receipt)
            except (ValueError, TypeError, sqlite3.Error):
                return JSONResponse({'error': 'receipt not active or checkout unresolved'}, status_code=409)
            if replay is not None:
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
            # Exceptions/crashes leave checkout_started set: uncertainty is not cancellation.
            return response

        async def handle(self, req, path, body, public):
            response = await super().handle(req, path, body, public)
            if response.status_code == 200 and path.endswith(('/complete', '/cancel')):
                payload = json.loads(response.body)
                if payload.get('task') and payload.get('checkout_jwt'):
                    self.gate.finish(payload['task'], payload)
            return response
    return GuardedShop
