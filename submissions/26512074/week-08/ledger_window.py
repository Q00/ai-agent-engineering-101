"""Incremental, replay-safe index of the append-only official market journal."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def window(path, now, tags):
    path = Path(path)
    if not path.exists():
        return []
    tags = set(tags)
    identity = 'v2:' + json.dumps(sorted(tags))
    with path.open('rb') as source, closing(sqlite3.connect(path.with_name('ledger-window.sqlite3'), timeout=60)) as db, db:
        db.execute('create table if not exists events(seq integer primary key, ts real, own integer, payload text)')
        db.execute('create table if not exists cursor(id integer primary key, offset integer, inode text, tags text)')
        db.execute('begin immediate')
        stat = path.stat()
        inode = str((stat.st_dev, stat.st_ino))
        previous = db.execute('select offset,inode,tags from cursor where id=1').fetchone()
        offset = previous[0] if previous and previous[1:] == (inode, identity) and previous[0] <= stat.st_size else 0
        if not offset:
            db.execute('delete from events')
        source.seek(offset)
        pending = []
        while True:
            start = source.tell()
            raw = source.readline()
            if not raw:
                break
            if not raw.endswith(b'\n'):
                source.seek(start)
                break  # The writer will finish this record on a later read.
            try:
                event = json.loads(raw)
                ts = event.get('ts', 0)
                own = event.get('data', {}).get('shop') in tags
                seq = event.get('seq')
                # A scan can overlap append operations. Retain newer records
                # now and expose them once their timestamp enters the window.
                if seq is not None and now-ts < (86400 if own else 7200):
                    pending.append((seq, ts, int(own), raw.decode('utf-8')))
            except (ValueError, TypeError):
                continue
            if len(pending) >= 1000:
                db.executemany('insert or replace into events values(?,?,?,?)', pending)
                pending.clear()
        db.executemany('insert or replace into events values(?,?,?,?)', pending)
        db.execute('delete from events where ts<=? or (own=0 and ts<=?)', (now-86400, now-7200))
        db.execute('insert or replace into cursor values(1,?,?,?)', (source.tell(), inode, identity))
        rows = db.execute('select payload from events where ts<=? order by ts,seq', (now,)).fetchall()
    return [json.loads(row[0]) for row in rows]
