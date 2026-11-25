"""Local SQLite state for seller leases, negotiations, and protocol tasks.

Receipt leases are committed before exposing a signed artifact. Epoch deadlines
survive restarts; monotonic clocks are reconstructed only for the live process.
No keys, inferred buyer preferences, or permanent model rules belong here.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time

from a2a.server.owner_resolver import resolve_user_scope
from a2a.server.agent_execution import SimpleRequestContextBuilder
from a2a.server.tasks import InMemoryTaskStore, TaskStore
from a2a.types import Task
from inventory_observation import timestamp

DB = Path('state/seller.sqlite3')


class SellerState:
    def __init__(self, path=None):
        self.path = (path or DB).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS negotiations (
                    task TEXT PRIMARY KEY, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS leases (
                    unit TEXT PRIMARY KEY, task TEXT NOT NULL, kind TEXT NOT NULL,
                    until REAL NOT NULL, receipt TEXT
                );
                CREATE TABLE IF NOT EXISTS settled_units (
                    unit TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS protocol_tasks (
                    owner TEXT NOT NULL, task TEXT NOT NULL, body BLOB NOT NULL,
                    PRIMARY KEY(owner, task)
                );
                CREATE TABLE IF NOT EXISTS purchase_contexts (
                    task TEXT PRIMARY KEY, requirements TEXT NOT NULL, updated_at REAL NOT NULL
                );
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def mark_settled(self, units):
        """An authoritative payment permanently removes this physical unit from sale."""
        if not units:
            return
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.executemany('INSERT OR IGNORE INTO settled_units VALUES (?)', ((unit,) for unit in units))
            db.execute("""DELETE FROM negotiations WHERE task IN (
                       SELECT task FROM leases JOIN settled_units USING (unit) WHERE kind='negotiation')""")
            db.execute('DELETE FROM leases WHERE unit IN (SELECT unit FROM settled_units)')

    def settled_units(self):
        with self.connect() as db:
            return {row[0] for row in db.execute('SELECT unit FROM settled_units')}

    def restore_deals(self):
        with self.connect() as db:
            rows = db.execute('SELECT task, data FROM negotiations').fetchall()
        now, mono = time.time(), time.monotonic()
        deals = {}
        for task, raw in rows:
            deal = json.loads(raw)
            deal['opened_monotonic'] = mono - max(0, now - deal['opened_at'])
            deal['last_activity'] = mono - max(0, now - deal['last_activity_at'])
            deals[task] = deal
        return deals

    def save_deal(self, task, deal, *, idle=300, maximum=900, create=True):
        now = time.time()
        until = min(deal['opened_at'] + maximum, deal['last_activity_at'] + idle)
        data = {k: v for k, v in deal.items() if k not in ('opened_monotonic', 'last_activity')}
        # Full raw messages remain in the append-only log, not an expanding prompt.
        data['history'] = data.get('history', [])[-4:]
        unit = deal['unit']['unit']
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM settled_units WHERE unit=?', (unit,)).fetchone():
                return False
            lease = db.execute('SELECT task, kind, until FROM leases WHERE unit=?', (unit,)).fetchone()
            if not create and (not lease or lease[0] != task or lease[1] != 'negotiation' or lease[2] <= now):
                return False
            if lease and lease[2] > now and (lease[0] != task or lease[1] == 'receipt'):
                return False
            db.execute('INSERT OR REPLACE INTO leases VALUES (?, ?, ?, ?, NULL)',
                       (unit, task, 'negotiation', until))
            db.execute('INSERT OR REPLACE INTO negotiations VALUES (?, ?)',
                       (task, json.dumps(data, ensure_ascii=False)))
            if 'requirements' in data:
                db.execute('INSERT OR REPLACE INTO purchase_contexts VALUES (?, ?, ?)',
                           (task, json.dumps(data['requirements'], ensure_ascii=False), now))
        return True

    def requirements_for(self, task):
        with self.connect() as db:
            row = db.execute('SELECT requirements FROM purchase_contexts WHERE task=?', (task,)).fetchone()
        return json.loads(row[0]) if row else None

    def close_deal(self, task):
        with self.connect() as db:
            db.execute('DELETE FROM negotiations WHERE task=?', (task,))
            db.execute("DELETE FROM leases WHERE task=? AND kind='negotiation'", (task,))

    def commit_receipt(self, task, receipt, seconds=600):
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("""SELECT task, kind, until FROM leases WHERE unit=?
                             AND unit NOT IN (SELECT unit FROM settled_units)""", (receipt['unit'],)).fetchone()
            if not row or row[0] != task or row[1] != 'negotiation' or row[2] <= now:
                return False
            db.execute("UPDATE leases SET kind='receipt', until=?, receipt=? WHERE unit=?",
                       (now + seconds, json.dumps(receipt, ensure_ascii=False), receipt['unit']))
            db.execute('DELETE FROM negotiations WHERE task=?', (task,))
        return True

    def active_leases(self, history=()):
        now = time.time()
        settled = {e.get('unit') for e in history if e.get('event') == 'settled' and e.get('unit')}
        with self.connect() as db:
            db.execute('DELETE FROM leases WHERE until<=?', (now,))
            for unit in settled:
                db.execute('DELETE FROM leases WHERE unit=?', (unit,))
            return db.execute('SELECT unit, task, kind, until FROM leases').fetchall()

    def pending_receipt(self, task):
        with self.connect() as db:
            row = db.execute("""SELECT receipt FROM leases WHERE task=? AND kind='receipt' AND until>?
                             AND unit NOT IN (SELECT unit FROM settled_units)""",
                             (task, time.time())).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def receipt_reservation(self, task):
        """Original signed terms and deadline; never extend or recreate a lease."""
        with self.connect() as db:
            row=db.execute("""SELECT unit,until,receipt FROM leases WHERE task=?
                AND kind='receipt' AND until>? AND receipt IS NOT NULL
                AND unit NOT IN (SELECT unit FROM settled_units)""",(task,time.time())).fetchall()
        if len(row)!=1:return None
        row=row[0]
        receipt=json.loads(row[2])
        return {'unit':row[0],'until':row[1],'receipt':receipt}

    def import_receipt_holds(self, history, seconds=600):
        """One-time upgrade safety, also covering an old artifact/log crash gap."""
        now = time.time()
        settled = {e['unit'] for e in history
                   if e.get('event') == 'settled' and e.get('unit') and e.get('ledger') is not None}
        self.mark_settled(settled)
        with self.connect() as db:
            for event in history:
                at = timestamp(event.get('at'))
                if (event.get('event') != 'receipt_signed' or at is None or at + seconds <= now
                        or not event.get('unit') or not event.get('task_id') or event['unit'] in settled):
                    continue
                db.execute("""INSERT INTO leases
                              SELECT ?, ?, 'receipt', ?, NULL
                              WHERE NOT EXISTS (SELECT 1 FROM settled_units WHERE unit=?)
                              ON CONFLICT(unit) DO UPDATE SET task=excluded.task, kind='receipt',
                                  until=excluded.until, receipt=NULL WHERE excluded.until>leases.until""",
                           (event['unit'], event['task_id'], at + seconds, event['unit']))


class SQLiteTaskStore(TaskStore):
    """Protocol tasks use the same owner scoping as the SDK's memory store."""
    def __init__(self, state):
        self.state = state

    async def save(self, task, context):
        with self.state.connect() as db:
            db.execute('INSERT OR REPLACE INTO protocol_tasks VALUES (?, ?, ?)',
                       (resolve_user_scope(context), task.id, task.SerializeToString()))

    async def get(self, task_id, context):
        with self.state.connect() as db:
            row = db.execute('SELECT body FROM protocol_tasks WHERE owner=? AND task=?',
                             (resolve_user_scope(context), task_id)).fetchone()
        return Task.FromString(row[0]) if row else None

    async def delete(self, task_id, context):
        with self.state.connect() as db:
            db.execute('DELETE FROM protocol_tasks WHERE owner=? AND task=?',
                       (resolve_user_scope(context), task_id))

    async def list(self, params, context):
        # Delegate filtering/pagination to the SDK rather than maintaining a
        # second implementation of its protocol cursor rules.
        memory = InMemoryTaskStore()
        with self.state.connect() as db:
            rows = db.execute('SELECT body FROM protocol_tasks WHERE owner=?', (resolve_user_scope(context),)).fetchall()
        for row in rows:
            await memory.save(Task.FromString(row[0]), context)
        return await memory.list(params, context)


class ResumingContextBuilder(SimpleRequestContextBuilder):
    async def build(self, context, params=None, task_id=None, context_id=None, task=None):
        # The SDK otherwise generates a new context when a buyer supplies only
        # taskId after this server has restarted. The persisted task owns it.
        if task is None and task_id and not context_id and self._task_store is not None:
            task = await self._task_store.get(task_id, context)
        if task is not None and not context_id:
            context_id = task.context_id
        return await super().build(context, params, task_id, context_id, task)
