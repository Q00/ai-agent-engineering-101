"""Adopted opening policy, separate from historical randomized assignments.

Rollback: create seller_opening_default.disabled under MARKET_DIR. Only new
tasks revert; existing quotes and the original negotiation anchor never change.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

class OpeningDefault:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else Path(os.environ.get("MARKET_DIR") or Path(__file__).parent / ".market")

    def quote(self, task, deal, trial, now=None):
        if "opening_default" in deal or deal.get("rounds", 0):
            return
        now = time.time() if now is None else now
        try:
            # Historical trial membership (including capped control rows) is immutable.
            legacy = self.root / "seller_concession_trial.sqlite3"
            if legacy.exists():
                with closing(sqlite3.connect(legacy.resolve().as_uri()+"?mode=ro", uri=True)) as db:
                    historic = db.execute("SELECT 1 FROM assignments WHERE task=?", (task,)).fetchone()
                if historic:
                    trial.quote(task, deal, now)
                    return
            self.root.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.root / "seller_opening_default.sqlite3", timeout=10)) as db, db:
                db.execute("CREATE TABLE IF NOT EXISTS openings (task TEXT PRIMARY KEY, data TEXT NOT NULL)")
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT data FROM openings WHERE task=?", (task,)).fetchone()
                if row:
                    rec = json.loads(row[0])
                    if any(rec.get(k) != deal.get(k) for k in ("unit", "reserve", "open", "cost")):
                        print("OPENING_DEFAULT snapshot_mismatch", file=sys.stderr)
                        return
                else:
                    enabled = not (self.root / "seller_opening_default.disabled").exists()
                    price = max(deal["reserve"], round(deal["open"] * .97)) if enabled else deal["open"]
                    rec = dict(policy="opening-3pct-v1" if enabled else "original-opening",
                               task=task, ts=now, unit=deal.get("unit"), sku=deal.get("id"),
                               kind=deal.get("kind"), cost=deal.get("cost"), reserve=deal["reserve"],
                               open=deal["open"], price=price)
                    db.execute("INSERT INTO openings VALUES (?,?)", (task, json.dumps(rec, allow_nan=False)))
            deal["ask"] = rec["price"]
            deal["history"][0] = ("you asked", rec["price"])
            deal["opening_default"] = rec
            # Separate observational trace; SQLite above is the durable quote identity.
            try:
                fd = os.open(self.root / "seller_opening_default.jsonl", os.O_WRONLY|os.O_CREAT|os.O_APPEND, 0o600)
                try:
                    data = (json.dumps(rec, allow_nan=False)+"\n").encode()
                    if os.write(fd, data) != len(data):
                        raise OSError("short write")
                finally:
                    os.close(fd)
            except Exception:
                print("OPENING_DEFAULT trace_unavailable", file=sys.stderr)
        except Exception:
            # No price change if durable assignment cannot be committed.
            print("OPENING_DEFAULT assignment_unavailable", file=sys.stderr)
