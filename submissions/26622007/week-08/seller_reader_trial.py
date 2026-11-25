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

import market_ref_read

N = r"([1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]*)"
OFFER_TEMPLATES = [
    re.compile(r"That is more than I want to pay\.(?: [\w.-]{1,40} quoted me " + N + r" for this\.)? I can do " + N + r"\."),
    re.compile(r"I can offer " + N + r"\."),
    re.compile(r"I offer " + N + r" for this certified item\."),
    re.compile(r"I can pay " + N + r" KRW\."),
    re.compile(r"Another shop quoted me a lower price\. My offer: " + N + r"\.?"),
    re.compile(N + r"\. On the public ledger a quarter of today's sales of this item went for about [a-z]+(?:-[a-z]+)? "
               r"percent of its list price or less\. I pay the moment the receipt arrives\."),
]
AGREED = re.compile(r"OK, " + N + r" it is\. Deal at " + N + r"\.")


def template_offer(text):
    t = (text or "").strip()
    m = AGREED.fullmatch(t)
    if m:
        a, b = (int(x.replace(",", "")) for x in m.groups())
        return a if a == b else None
    for pattern in OFFER_TEMPLATES:
        m = pattern.fullmatch(t)
        if m:
            return int([g for g in m.groups() if g][-1].replace(",", ""))
    return None


class Reader:
    """Lazy, once-per-turn reader; preserve original A returns and exceptions."""
    def __init__(self, jev, arm="A", market_ref=None):
        self.jev, self.arm = jev, arm
        self.market_ref = market_ref_read.SWITCH if market_ref is None else market_ref
        self.source, self.offer, self.confidence = "none", None, None
        self.called, self.error, self.result = False, None, None
        self.jev_calls = 0

    def __call__(self, text):
        if self.called:
            if self.error is not None:
                raise self.error
            return self.result
        self.called = True
        offer = template_offer(text) if self.arm == "B" else None
        ref = market_ref_read.offer(text) if offer is None else None
        if offer is not None:
            self.source, self.result = "rule", (offer, 1.0)
        elif ref is not None and self.jev is not None and self._market_ref_on():   # where Jev would read (market_ref_read.py)
            self.source, self.result = "rule_market_ref", (ref, 1.0)
        elif self.jev is not None:
            self.source = "jev"
            try:
                self.jev_calls += 1
                self.result = self.jev(text)
            except Exception:
                # functools.lru_cache in the original path does not cache exceptions.
                self.called = False
                raise
        else:
            self.result = (None, 0)
        self.offer, self.confidence = self.result
        return self.result

    def _market_ref_on(self):
        try:
            return self.market_ref.on() is True
        except Exception:  # noqa: BLE001 - a broken switch is off: Jev reads as before
            return False


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
            p = self.root / "seller_reader_trial.json"
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
            if not 0 < c["end"] - c["start"] <= 3600 or type(c.get("seed")) is not int:
                raise ValueError()
            if "stop" in c and type(c["stop"]) is not bool:
                raise ValueError()
            return c
        except Exception:
            self.invalid("config")
            return None

    def assign(self, task, deal, now=None):
        """Called once only after eligible unit selection and quote; full UUID is key."""
        now = time.time() if now is None else now
        a = {"arm": "A", "id": None, "stratum": None}
        c = self.config()
        if not c or c.get("stop"):
            return a
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.root / "seller_reader_trial.sqlite3", timeout=10)) as db, db:
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
        if not c or c.get("stop") or c["id"] != assignment.get("id"):
            return "A"
        return assignment.get("arm", "A")

    def trace(self, task, deal, reader, started, raw, action, price, offer):
        try:
            self._trace(task, deal, reader, started, raw, action, price, offer)
        except Exception:
            self.invalid("trace")

    def _trace(self, task, deal, reader, started, raw, action, price, offer):
        a = deal.get("reader_trial") or {}
        if not a.get("id"):
            return
        record = {"ts":time.time(), "id":a["id"], "task":task,
                  "input_sha":deal.get("reader_trial_input_sha"), "policy_sha":deal.get("reader_trial_policy_sha"),
                  "template_offer":deal.get("reader_trial_template_offer"),
                  "harness_used":bool(deal.get("reader_trial_harness_used")), "jev_calls":reader.jev_calls,
                  "history":deal.get("history") or [], "unit":deal.get("unit"), "sku":deal.get("id"),
                  "kind":deal.get("kind"), "grade":deal.get("reader_trial_grade"), "cost":deal.get("cost"),
                  "reserve":deal["reserve"], "open":deal.get("open"), "round":deal["rounds"],
                  "ask":deal["ask"], "floor":deal.get("reader_trial_floor"), "arm":reader.arm,
                  "assigned_arm":a["arm"], "stratum":a["stratum"], "source":reader.source,
                  "jev_offer":reader.offer if reader.source == "jev" else None,
                  "jev_confidence":reader.confidence if reader.source == "jev" else None,
                  "seconds":time.monotonic()-started,
                  "raw":{k:raw.get(k) for k in ("action","price","buyer_offer")},
                  "action":action, "price":offer if action == "accept" else price, "offer":offer, "no_offer":not bool(offer)}
        try:
            data = (json.dumps(record, allow_nan=False, separators=(",",":"))+"\n").encode()
            fd = os.open(self.root / "seller_reader_trial.jsonl", os.O_WRONLY|os.O_CREAT|os.O_APPEND, 0o600)
            try:
                if os.write(fd, data) != len(data):
                    raise OSError("short write")
            finally:
                os.close(fd)
        except Exception:
            self.invalid("trace")
