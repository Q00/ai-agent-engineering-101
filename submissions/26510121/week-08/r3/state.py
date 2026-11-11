"""Atomic snapshots and an append-only, process-safe evidence journal."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import uuid
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]


def warranty_hours(value):
    """Missing/null warranty means no stated coverage; never fabricate a refund."""
    return value if type(value) in (int, float) and math.isfinite(value) and value > 0 else 0


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows readers/antivirus can briefly deny replacing an existing file.
    # Bound retries; a real permission failure must still reach the caller.
    for attempt in range(4):
        try:
            os.replace(tmp, path)
            break
        except PermissionError as exc:
            if getattr(exc, 'winerror', None) not in {5, 32, 33} or attempt == 3:
                raise
            time.sleep(.01 * 2 ** attempt)


def policy(root=ROOT):
    result = read_json(Path(root) / "policy.json", {})
    for role in ("actor", "critic"):
        result[role + "_model"] = os.environ.get("R3_" + role.upper() + "_MODEL", result[role + "_model"])
    return result


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Journal:
    def __init__(self, md=None):
        self.md = Path(md or os.environ.get("MARKET_DIR", ROOT / ".market"))
        self.md.mkdir(parents=True, exist_ok=True)
        self.db = self.md / "r3.sqlite3"
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,kind TEXT,ts REAL,role TEXT,data TEXT);
            CREATE INDEX IF NOT EXISTS event_kind ON events(kind,ts);
            CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE IF NOT EXISTS reservations(unit TEXT PRIMARY KEY,task TEXT,until REAL);
            CREATE TABLE IF NOT EXISTS spending(id TEXT PRIMARY KEY,day TEXT,amount REAL,status TEXT);
            CREATE TABLE IF NOT EXISTS forum(id TEXT PRIMARY KEY,payload TEXT,status TEXT);
            """)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=15)
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=15000")
            with c:
                yield c
        finally:
            c.close()

    def add(self, kind, data, *, eid=None, role="system", ts=None):
        eid = eid or uuid.uuid4().hex
        with self.connect() as c:
            cur = c.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)",
                            (eid, kind, time.time() if ts is None else ts, role,
                             json.dumps(data, ensure_ascii=False)))
            return cur.rowcount == 1

    def events(self, kind, limit=500):
        with self.connect() as c:
            rows = c.execute("SELECT id,ts,role,data FROM events WHERE kind=? ORDER BY ts DESC,rowid DESC LIMIT ?",
                             (kind, limit)).fetchall()
        return [dict(json.loads(d), _event_id=i, _ts=t, _role=r) for i, t, r, d in rows]

    def add_batch(self, rows, *, ts=None):
        """Append idempotent evidence in one transaction, preserving event IDs."""
        if not rows:return 0
        at=time.time() if ts is None else ts
        payload=[(eid,kind,at,role,json.dumps(data,ensure_ascii=False))
                 for kind,data,eid,role in rows]
        with self.connect() as c:
            before=c.total_changes
            c.executemany('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)',payload)
            return c.total_changes-before

    def get(self, key, default=None):
        with self.connect() as c:
            row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.connect() as c:
            c.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def reserve(self, unit, task, ttl=180, now=None):
        now = time.time() if now is None else now
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("DELETE FROM reservations WHERE until<=?", (now,))
            row = c.execute("SELECT task FROM reservations WHERE unit=?", (unit,)).fetchone()
            if row and row[0] != task:
                return False
            c.execute("INSERT OR REPLACE INTO reservations VALUES(?,?,?)", (unit, task, now + ttl))
        return True

    def release(self, task):
        with self.connect() as c:
            c.execute("DELETE FROM reservations WHERE task=?", (task,))

    def hold_budget(self, amount, cap):
        """Reserve and track estimated spend. cap=None disables the daily ceiling."""
        day = time.strftime("%Y-%m-%d", time.gmtime())
        request_id = uuid.uuid4().hex
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            total = c.execute("SELECT COALESCE(SUM(amount),0) FROM spending WHERE day=?", (day,)).fetchone()[0]
            if cap is not None and total + amount > cap:
                return None
            c.execute("INSERT INTO spending VALUES(?,?,?,?)", (request_id, day, amount, "reserved"))
        return request_id

    def close_budget(self, request_id, amount):
        with self.connect() as c:
            c.execute("UPDATE spending SET amount=?,status='actual' WHERE id=?", (amount, request_id))

    def queue_post(self, payload, key):
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO forum VALUES(?,?,?)", (key, json.dumps(payload, ensure_ascii=False), "pending"))

    def pending_posts(self):
        with self.connect() as c:
            return [(i, json.loads(p)) for i, p in c.execute("SELECT id,payload FROM forum WHERE status='pending' ORDER BY rowid")]

    def sent_post(self, key):
        with self.connect() as c:
            c.execute("UPDATE forum SET status='sent' WHERE id=?", (key,))

    def snapshot(self, name, default=None):
        return read_json(self.md / name, default)

    def reconcile(self, now=None):
        """Re-read replaced files and de-duplicate append-only streams; never edit kit logs."""
        now = time.time() if now is None else now
        for name, kind in [("payments.jsonl", "payment"), ("broken.jsonl", "broken_news")]:
            path = self.md / name
            if not path.exists():
                continue
            offset = self.get("offset:" + name, 0)
            if path.stat().st_size < offset:
                offset = 0
            with path.open("rb") as f:
                f.seek(offset)
                while True:
                    start = f.tell()
                    line = f.readline()
                    if not line or not line.endswith(b"\n"):
                        f.seek(start)
                        break
                    try:
                        data = json.loads(line)
                    except ValueError:
                        continue
                    self.add(kind, data, eid=kind + ":" + identity(data), role="buyer")
                self.put("offset:" + name, f.tell())
        # Read purchases once. Re-reading and decoding the same 10k rows for
        # every old break added >20 seconds before each live Buyer decision.
        purchases=self.events('purchase',10000)
        by_name={}
        for item in purchases:by_name.setdefault(item.get('name'),[]).append(item)
        ambiguous_orders = set();pending=[]
        broken_orders = {e['order'] for e in self.events('outcome',10000) if e.get('broken')}
        for news in self.events("broken_news"):
            possible = [e for e in by_name.get(news.get('name'),[])
                        if 0 <= news.get("broke_at", 0) - e["paid_at"] <= 21600]
            if len(possible) == 1:
                item = possible[0]
                hours = warranty_hours(item.get('warranty_hours'))
                covered = hours > 0 and news["broke_at"] - item["paid_at"] <= hours * 3600
                pending.append(("outcome", {"order": item["order"], "broken": True, "covered": covered,
                                    "surplus": 0 if covered else -item["price"], "source": news["_event_id"],
                                    "refund_status": "expected_by_warranty_rule" if covered else "not_covered"},
                         "broken-outcome:" + item["order"], "buyer"))
                broken_orders.add(item['order'])
            elif possible:
                ambiguous_orders.update(x["order"] for x in possible)
                pending.append(("ambiguous_break", {"source": news["_event_id"], "orders": [x["order"] for x in possible]},
                         "ambiguous:" + news["_event_id"], "buyer"))
        for item in purchases:
            mature = item.get("kind") == "food" or now >= item["paid_at"] + 21600
            if mature and item["order"] not in broken_orders and item["order"] not in ambiguous_orders:
                pending.append(("outcome", {"order": item["order"], "broken": False, "surplus": item["value"] - item["price"],
                                     "matured": True}, "mature:" + item["order"], "buyer"))
        self.add_batch(pending,ts=now)
        from .outcomes import reconcile as reconcile_units
        reconcile_units(self,purchases,now)
