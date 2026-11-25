"""Default-off, bounded reader trial. No inventory or pricing policy lives here."""
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

class Trial:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else Path(os.environ.get("MARKET_DIR") or Path(__file__).parent / ".market")
        self.warned = set()

    def invalid(self, reason):
        if reason not in self.warned:
            self.warned.add(reason)
            print("TRIAL_INVALID " + reason, file=sys.stderr)

    def config(self):
        try:
            p = self.root / "seller_concession_trial.json"
            if not p.exists():
                return None
            c = json.loads(p.read_text())
            if not isinstance(c, dict) or set(c) - {"enabled", "id", "start", "end", "seed", "stop"}:
                raise ValueError()
            if c.get("stop") is True:
                self.invalid("explicit_stop")
                return {"stop": True}
            if type(c.get("enabled")) is not bool:
                raise ValueError()
            if c["enabled"] is False:
                return None
            if not isinstance(c.get("id"), str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", c["id"]):
                raise ValueError()
            for key in ("start", "end"):
                if type(c.get(key)) not in (int, float) or not math.isfinite(c[key]):
                    raise ValueError()
            if not 0 < c["end"] - c["start"] <= 1800 or type(c.get("seed")) is not int:
                raise ValueError()
            if "stop" in c and type(c["stop"]) is not bool:
                raise ValueError()
            return c
        except Exception:
            self.invalid("config")
            return None

    def assign(self, task, deal, now=None):
        """Enroll after unit selection, before the first public quote; full UUID key."""
        now = time.time() if now is None else now
        a = {"arm": "A", "id": None, "stratum": None}
        c = self.config()
        if not c or c.get("stop"):
            return a
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.root / "seller_concession_trial.sqlite3", timeout=10)) as db, db:
                db.execute("CREATE TABLE IF NOT EXISTS assignments (task TEXT PRIMARY KEY, data TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS strata (trial TEXT, stratum TEXT, n INTEGER, PRIMARY KEY(trial,stratum))")
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT data FROM assignments WHERE task=?", (task,)).fetchone()
                if row:
                    return json.loads(row[0])
                if now >= c["end"]:
                    return a
                if c["start"] <= now < c["end"]:
                    count = db.execute("SELECT COUNT(*) FROM assignments WHERE json_extract(data,'$.id')=?", (c["id"],)).fetchone()[0]
                    bcount = db.execute("SELECT COUNT(*) FROM assignments WHERE json_extract(data,'$.id')=? AND json_extract(data,'$.arm')='B'", (c["id"],)).fetchone()[0]
                    if count < 200 and bcount < 100:
                        s = f"{deal.get('kind')}|{'high' if deal['open'] >= 300000 else 'low'}|{int((now-c['start'])//300)}"
                        row = db.execute("SELECT n FROM strata WHERE trial=? AND stratum=?", (c["id"], s)).fetchone()
                        n = row[0] if row else 0
                        first = hashlib.sha256(f"{c['seed']}|{c['id']}|{s}|{n//2}".encode()).digest()[0] % 2
                        arm = "B" if (first + n % 2) % 2 else "A"
                        a = {"arm": arm, "id": c["id"], "stratum": s,
                             "enrolled_at": now, "unit": deal.get("unit"), "sku": deal.get("id"),
                             "kind": deal.get("kind"), "grade": deal.get("reader_trial_grade"),
                             "cost": deal.get("cost"), "reserve": deal["reserve"], "open": deal["open"],
                             "expires_at": deal.get("expires_at"), "policy_sha": deal.get("reader_trial_policy_sha")}
                        db.execute("INSERT OR REPLACE INTO strata VALUES (?,?,?)", (c["id"], s, n+1))
                db.execute("INSERT INTO assignments VALUES (?,?)", (task, json.dumps(a)))
            return a
        except Exception:
            self.invalid("assignment")
            return a if a["id"] is None else {"arm":"A", "id":None, "stratum":None}

    def arm(self, assignment):
        c = self.config()
        # Existing enrollment survives normal end; absence/invalidity fails closed.
        if assignment.get("contaminated") or not c or c.get("stop") or c["id"] != assignment.get("id"):
            return "A"
        return assignment.get("arm", "A")

    def quote(self, task, deal, now=None):
        """Change only the first public ask; retain the original schedule anchor."""
        a = self.assign(task, deal, now)
        if a.get('id'):
            snapshot = {"unit": deal.get("unit"), "sku": deal.get("id"),
                        "kind": deal.get("kind"), "grade": deal.get("reader_trial_grade"),
                        "cost": deal.get("cost"), "reserve": deal["reserve"], "open": deal["open"],
                        "expires_at": deal.get("expires_at"), "policy_sha": deal.get("reader_trial_policy_sha")}
            if any(a.get(k) != v for k, v in snapshot.items()):
                # Do not mutate the enrollment denominator or mix historical prices with a new unit.
                a = dict(a, contaminated=True, invalid_reason="snapshot_mismatch")
                self.invalid("snapshot_mismatch")
        deal['concession_trial'] = a
        if a.get('id'):
            original = deal['open'] if a.get('contaminated') else a['open']
            deal['ask'] = max(deal['reserve'], round(original * .97)) if self.arm(a) == 'B' else original
            deal['history'][0] = ('you asked', deal['ask'])
            self.event(task, deal, 'quote', deal['ask'], now)
        return a

    def restore(self, deal):
        """On invalid/stop return untouched opening to control; never raise a later counter."""
        a = deal.get('concession_trial') or {}
        if a.get('id') and not a.get('contaminated') and self.arm(a) != a.get('arm') and deal.get('rounds', 0) == 0:
            deal['ask'] = a['open']
            deal['history'][0] = ('you asked', a['open'])

    def event(self, task, deal, event, price=None, now=None, **fields):
        a = deal.get('concession_trial') or {}
        if not a.get('id'):
            return
        try:
            rec = dict(ts=time.time() if now is None else now, task=task, id=a['id'],
                       arm=a['arm'], effective_arm=self.arm(a), event=event, price=price,
                       original_open=a['open'], reserve=a['reserve'], cost=a.get('cost'),
                       contaminated=bool(a.get('contaminated')), invalid_reason=a.get('invalid_reason'),
                       current_open=deal.get('open'), current_reserve=deal.get('reserve'),
                       unit=deal.get('unit'), sku=deal.get('id'), assignment_unit=a.get('unit'))
            rec.update(fields)
            fd = os.open(self.root / 'seller_concession_trial.jsonl', os.O_WRONLY|os.O_CREAT|os.O_APPEND, 0o600)
            try:
                data = (json.dumps(rec, allow_nan=False)+'\n').encode()
                if os.write(fd, data) != len(data):
                    raise OSError('short write')
            finally:
                os.close(fd)
        except Exception:
            self.invalid('trace')

    def trace(self, task, deal, reader, started, raw, action, price, offer):
        """Called at exactly the legacy reader trace sites, after final price guards."""
        self.event(task, deal, 'turn', offer if action == 'accept' else price,
                   action=action, offer=offer, ask=deal['ask'], floor=deal.get('reader_trial_floor'),
                   seconds=time.monotonic()-started, source=reader.source, jev_calls=reader.jev_calls,
                   harness_used=bool(deal.get('reader_trial_harness_used')), round=deal['rounds'],
                   raw={k: raw.get(k) for k in ('action', 'price', 'buyer_offer')})

    def readout(self, trial_id, now=None):
        """Non-authoritative auxiliary event readout; not ledger settlement evidence.

        ITT denominators include no replies. Outcomes mature after 600 seconds.
        """
        now = time.time() if now is None else now
        result = {arm: dict(assigned=0, mature=0, deals=0, gross_margin=0) for arm in ('A','B')}
        path = self.root / 'seller_concession_trial.sqlite3'
        if not path.exists():
            return result
        with closing(sqlite3.connect(path)) as db:
            rows = [(task,json.loads(data)) for task,data in db.execute('SELECT task,data FROM assignments')]
        events = {}
        trace = self.root / 'seller_concession_trial.jsonl'
        if trace.exists():
            for line in trace.read_text().splitlines():
                try:
                    r = json.loads(line)
                    if r['id'] == trial_id and r['event'] == 'deal':
                        events.setdefault(r['task'],r)
                except (ValueError,KeyError):
                    continue
        for task,a in rows:
            if a.get('id') != trial_id:
                continue
            r = result[a['arm']]
            r['assigned'] += 1
            if now < a['enrolled_at'] + 600:
                continue
            r['mature'] += 1
            e = events.get(task)
            if e and a['enrolled_at'] <= e['ts'] <= a['enrolled_at'] + 600 and e['ts'] <= now:
                r['deals'] += 1
                r['gross_margin'] += e['price'] - (a.get('cost') or 0)
        return result
