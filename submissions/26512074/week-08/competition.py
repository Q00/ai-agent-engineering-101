"""Profit-seeking price experiments and signed, capped game alliances.

No external messages or registration here. Commitments are not treated as returns:
only a Round-3 public settlement fetched by our operator credits cooperation.
"""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from contextlib import contextmanager

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from policy import ROOT, RESERVES

SELF = "totato2203"
TERMS = {"max_extra_per_purchase": 1, "pilot_extra_per_partner": 3,
         "global_extra_cap": 30, "return_margin_multiplier": 2,
         "description": "Optional in-game reciprocal procurement; no automatic stars or platform approval."}


class Competition:
    def __init__(self, path=None):
        self.path = Path(path or ROOT / ".market/competition.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS quotes(task TEXT PRIMARY KEY,item TEXT,markup INT,
                    opened REAL,closed INT DEFAULT 0,settled INT DEFAULT 0,profit REAL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS agreements(name TEXT PRIMARY KEY,pubkey TEXT,expires REAL,terms TEXT);
                CREATE TABLE IF NOT EXISTS returns(id TEXT PRIMARY KEY,name TEXT,margin REAL);
                CREATE TABLE IF NOT EXISTS spending(id TEXT PRIMARY KEY,name TEXT,extra REAL,state TEXT);
                CREATE TABLE IF NOT EXISTS loyalty_claims(source TEXT PRIMARY KEY,task TEXT UNIQUE,discount INT);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def quote(self, task, item, base):
        reserve = RESERVES[item]
        arms = sorted({0, 1, round(reserve * .03), round(reserve * .05),
                       base["opening_markup"], round(reserve * .15), round(reserve * .25), round(reserve * .40)})
        with self.connect() as db:
            rows = db.execute('''SELECT markup,COUNT(*) n,SUM(settled) wins,SUM(profit) profit
                FROM quotes WHERE item=? AND (settled=1 OR closed=1 OR opened<?) GROUP BY markup''',
                (item, time.time() - 300)).fetchall()
            stats = {r["markup"]: dict(r) for r in rows}
            won = db.execute("SELECT COUNT(*) FROM quotes WHERE settled=1").fetchone()[0]
            total = sum(r["n"] for r in rows)
            if won < 20:
                # Build a customer base without betting every opening on a high anchor.
                eligible = [a for a in arms if a <= max(base["opening_markup"], math.ceil(reserve * .1))]
            else:
                eligible = arms
            def score(arm):
                observed = stats.get(arm, {"n": 0, "wins": 0, "profit": 0})
                n = observed["n"]
                volume_bonus = .15 if won < 20 else .025
                prior = .05 - abs(arm - base["opening_markup"]) / reserve * .05
                mean = (observed["profit"] / reserve + volume_bonus * observed["wins"] + prior * 2) / (n + 2)
                exploration = .12 * math.sqrt(math.log(total + 3) / (n + 2))
                return mean + exploration
            markup = max(eligible, key=lambda a: (score(a), -a))
            db.execute("INSERT OR IGNORE INTO quotes(task,item,markup,opened) VALUES(?,?,?,?)", (task, item, markup, time.time()))
        policy = dict(base)
        policy["opening_markup"] = markup
        # Do not immediately accept a first floor offer while quoting a premium.
        policy["minimum_markup"] = min(markup, max(base["minimum_markup"], max(1, markup // 3))) if markup else 0
        return policy

    def close_quote(self, task):
        with self.connect() as db:
            db.execute("UPDATE quotes SET closed=1 WHERE task=? AND settled=0", (task,))

    def settle_quote(self, task, price):
        with self.connect() as db:
            row = db.execute("SELECT item FROM quotes WHERE task=?", (task,)).fetchone()
            if row:
                db.execute("UPDATE quotes SET settled=1,closed=1,profit=? WHERE task=?", (price - RESERVES[row["item"]], task))

    def redeem_loyalty(self, source, task, requested):
        if source == task or type(requested) is not int or not 0 < requested <= 5:
            return False
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM quotes WHERE task=? AND settled=1", (source,)).fetchone():
                return False
            if db.execute("SELECT 1 FROM loyalty_claims WHERE source=? OR task=?", (source, task)).fetchone():
                return False
            db.execute("INSERT INTO loyalty_claims VALUES(?,?,?)", (source, task, requested))
        return True

    def accept(self, agreement, signature, registered_key):
        required = {"partner", "beneficiary", "pubkey", "expires", "terms", "nonce"}
        if set(agreement) != required or agreement["beneficiary"] != SELF or agreement["partner"] == SELF:
            raise ValueError("agreement identity invalid")
        if agreement["pubkey"] != registered_key or agreement["terms"] != TERMS:
            raise ValueError("agreement key or risk limits changed")
        if not time.time() < agreement["expires"] <= time.time() + 86400:
            raise ValueError("agreement must expire within one day")
        raw = json.dumps(agreement, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        Ed25519PublicKey.from_public_bytes(base64.b64decode(registered_key, validate=True)).verify(base64.b64decode(signature, validate=True), raw)
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO agreements VALUES(?,?,?,?)", (agreement["partner"], registered_key, agreement["expires"], json.dumps(agreement)))

    def record_public_return(self, entry):
        # Called ONLY on raw entries retrieved from the official market ledger by
        # our own operator, never from a partner's advertisement or message.
        data = entry.get("data", {})
        if entry.get("kind") != "settle" or data.get("round") != 3:
            return False
        if data.get("shop") != SELF or data.get("item") not in RESERVES:
            return False  # Unknown schemas fail closed; no guessed counterparty.
        name, amount, identity = data.get("buyer"), data.get("amount"), entry.get("id", entry.get("seq"))
        if identity is None or isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < RESERVES[data["item"]]:
            return False
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM agreements WHERE name=? AND expires>?", (name, time.time())).fetchone():
                return False
            db.execute("INSERT OR IGNORE INTO returns VALUES(?,?,?)", (str(identity), name, amount - RESERVES[data["item"]]))
        return True

    def allowance(self, name):
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM agreements WHERE name=? AND expires>?", (name, time.time())).fetchone():
                return 0
            returned = db.execute("SELECT COALESCE(SUM(margin),0) FROM returns WHERE name=?", (name,)).fetchone()[0]
            spent = db.execute("SELECT COALESCE(SUM(extra),0) FROM spending WHERE name=? AND state!='released'", (name,)).fetchone()[0]
            global_ = db.execute("SELECT COALESCE(SUM(extra),0) FROM spending WHERE state!='released'").fetchone()[0]
        credit = TERMS["pilot_extra_per_partner"] + returned / TERMS["return_margin_multiplier"]
        return max(0, min(TERMS["max_extra_per_purchase"], credit - spent, TERMS["global_extra_cap"] - global_))

    def reserve_extra(self, identity, name, extra):
        if not 0 < extra <= 1:
            return False
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM spending WHERE id=?", (identity,)).fetchone():
                return False
            agreement = db.execute("SELECT 1 FROM agreements WHERE name=? AND expires>?", (name, time.time())).fetchone()
            returned = db.execute("SELECT COALESCE(SUM(margin),0) FROM returns WHERE name=?", (name,)).fetchone()[0]
            spent = db.execute("SELECT COALESCE(SUM(extra),0) FROM spending WHERE name=? AND state!='released'", (name,)).fetchone()[0]
            total = db.execute("SELECT COALESCE(SUM(extra),0) FROM spending WHERE state!='released'").fetchone()[0]
            if not agreement or spent + extra > 3 + returned / 2 or total + extra > 30:
                return False
            db.execute("INSERT INTO spending VALUES(?,?,?,'reserved')", (identity, name, extra))
        return True

    def resolve_extra(self, identity, paid):
        with self.connect() as db:
            db.execute("UPDATE spending SET state=? WHERE id=?", ("paid" if paid else "released", identity))


def live_competition():
    # Offline protocol tests must not teach the live market learner fake outcomes.
    return Competition() if os.environ.get("COMPETITION_LIVE") == "1" else None
