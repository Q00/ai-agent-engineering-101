"""Rebuildable SQLite audit index over append-only buyer evidence.

This observer cannot place orders or alter trading policy. Payments and signed
receipts remain separate. Cursors and inserts commit together; unfinished lines
are retried, and rotated/truncated files receive a new generation.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
import buyer_forum_memory

SOURCES = {'logs/buyer-events.jsonl': None, '.market/payments.jsonl': 'checkout_payment',
           '.market/broken.jsonl': 'breakage_notice', 'logs/buyer-order-observations.jsonl': None}


def connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS sources (
            source TEXT PRIMARY KEY, device INTEGER, inode INTEGER, generation TEXT, offset INTEGER);
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY, source TEXT NOT NULL, generation TEXT NOT NULL, byte_offset INTEGER NOT NULL,
            at TEXT, event TEXT NOT NULL, order_id TEXT, selection_id TEXT, task_id TEXT,
            shop_name TEXT, shop_url TEXT, sku TEXT, price INTEGER, paid INTEGER, ledger INTEGER,
            grade TEXT, value INTEGER, payload TEXT NOT NULL,
            indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source, generation, byte_offset));
        CREATE INDEX IF NOT EXISTS buyer_order_events ON events(order_id, id);
        CREATE INDEX IF NOT EXISTS buyer_shop_sku_events ON events(shop_name, sku, id);
        CREATE INDEX IF NOT EXISTS buyer_ledger_events ON events(ledger, event);
        CREATE VIEW IF NOT EXISTS confirmed_purchases AS
            SELECT p.order_id, p.shop_url, p.sku, p.price, p.ledger,
                (SELECT s.shop_name FROM events s WHERE s.order_id=p.order_id
                    AND s.shop_url=p.shop_url AND s.shop_name IS NOT NULL ORDER BY s.id DESC LIMIT 1) AS shop_name,
                (SELECT s.grade FROM events s WHERE s.event='settled' AND s.ledger=p.ledger
                    ORDER BY s.id DESC LIMIT 1) AS grade,
                (SELECT s.value FROM events s WHERE s.event='settled' AND s.ledger=p.ledger
                    ORDER BY s.id DESC LIMIT 1) AS value
            FROM events p WHERE p.event='checkout_payment' AND p.paid=1 AND p.ledger IS NOT NULL
                AND p.id=(SELECT MIN(q.id) FROM events q WHERE q.event='checkout_payment'
                    AND q.paid=1 AND q.ledger=p.ledger);
    ''')
    # Additive migration keeps old indexed evidence/cursors intact.
    columns={row[1] for row in db.execute('PRAGMA table_info(events)')}
    for name in ('episode_id','decision_id','attempt_id'):
        if name not in columns:db.execute(f'ALTER TABLE events ADD COLUMN {name} TEXT')
    db.execute('CREATE INDEX IF NOT EXISTS buyer_decision_events ON events(decision_id,id)')
    db.execute('CREATE INDEX IF NOT EXISTS buyer_episode_events ON events(episode_id,id)')
    return db


def insert(db, source, generation, offset, row, override=None):
    event = override or row.get('event')
    if not isinstance(event, str): raise ValueError('missing event name')
    paid = int(row['paid']) if type(row.get('paid')) is bool else None
    db.execute('''INSERT OR IGNORE INTO events (
        source,generation,byte_offset,at,event,order_id,selection_id,task_id,
        shop_name,shop_url,sku,price,paid,ledger,grade,value,payload,episode_id,decision_id,attempt_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
        source,generation,offset,row.get('at'),event,row.get('order'),row.get('selection_id'),row.get('task_id'),
        row.get('shop_name'),row.get('shop'),row.get('sku') or (row.get('item') if override=='checkout_payment' else None),row.get('price'),paid,
        row.get('ledger'),row.get('grade'),row.get('value'),json.dumps(row,ensure_ascii=False),
        row.get('episode_id'),row.get('decision_id'),row.get('attempt_id')))


def index_file(db, root, source, override):
    path = root/source
    if not path.exists(): return 0
    added = 0
    with path.open('rb') as stream, db:
        # fstat identifies the file actually opened, even during a concurrent rename.
        stat = os.fstat(stream.fileno())
        old = db.execute('SELECT device,inode,generation,offset FROM sources WHERE source=?', (source,)).fetchone()
        generation, offset = (old[2], old[3]) if old and old[:2] == (stat.st_dev, stat.st_ino) and stat.st_size >= old[3] else (uuid.uuid4().hex, 0)
        stream.seek(offset)
        for _ in range(10000):
            before = stream.tell(); line = stream.readline()
            if not line or not line.endswith(b'\n'): break
            try:
                row = json.loads(line)
                if not isinstance(row, dict): raise ValueError('not an object')
                insert(db, source, generation, before, row, override)
            except (ValueError, UnicodeError, TypeError) as error:
                # Retain the failed attempt too; the full original bytes stay in the source.
                insert(db, source, generation, before, {
                    'event': 'log_parse_error', 'reason': str(error), 'raw_preview': line[:1000].decode('utf-8', errors='replace')})
            offset = stream.tell(); added += 1
        db.execute('INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?)',
                   (source,stat.st_dev,stat.st_ino,generation,offset))
    return added


def capture_order(db, root):
    try: order = json.loads((root/'.market/order.json').read_text())
    except (OSError, ValueError): return
    identity = order.get('order') if isinstance(order, dict) else None
    if not identity or db.execute("SELECT 1 FROM events WHERE event='order_received' AND order_id=? LIMIT 1", (identity,)).fetchone(): return
    # Never persist private mandates/credentials even if the kit contract changes.
    fields = {k: order.get(k) for k in ('order','item','budget','kind','acceptable','use_within_hours','expires')}
    row = {'at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'event': 'order_received',
           'source_note': 'Observed current kit order, not a new order request', **fields}
    path = root/'logs/buyer-order-observations.jsonl'; path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a') as target:
        target.write(json.dumps(row,ensure_ascii=False)+'\n'); target.flush(); os.fsync(target.fileno())


def sync(root):
    root = Path(root)
    with closing(connect(root/'state/buyer.sqlite3')) as db:
        added = sum(index_file(db,root,source,override) for source,override in SOURCES.items())
        capture_order(db,root)
        added += index_file(db,root,'logs/buyer-order-observations.jsonl',None)
        result = {'indexed_lines': added, 'events': db.execute('SELECT COUNT(*) FROM events').fetchone()[0],
                'confirmed_purchases': db.execute('SELECT COUNT(*) FROM confirmed_purchases').fetchone()[0],
                'parse_errors': db.execute("SELECT COUNT(*) FROM events WHERE event='log_parse_error'").fetchone()[0]}
    result['forum']=buyer_forum_memory.ingest(root/'.market',db_path=root/'state/buyer.sqlite3')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--loop', action='store_true'); args = parser.parse_args()
    state = args.runtime/'state'; state.mkdir(parents=True,exist_ok=True)
    with (state/'buyer-logdb.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            try:
                result = sync(args.runtime)
                status = {'at':time.time(),'pid':os.getpid(),**result}
                temporary = state/'buyer-logdb.tmp'; temporary.write_text(json.dumps(status))
                os.replace(temporary,state/'buyer-logdb-status.json')
                if result['indexed_lines']: print(json.dumps(status),flush=True)
            except Exception as error:
                print(f'buyer log index error: {type(error).__name__}: {error}',flush=True)
                if not args.loop: raise
            if not args.loop: break
            time.sleep(2)
