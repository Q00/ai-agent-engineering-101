"""Append-only trade evidence; bounded, name-keyed views for model selection."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


class TradeMemory:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute('''CREATE TABLE IF NOT EXISTS trades (
            id TEXT PRIMARY KEY, time TEXT NOT NULL, shop_name TEXT NOT NULL,
            item TEXT NOT NULL, budget INTEGER NOT NULL, outcome TEXT NOT NULL,
            price REAL, evidence TEXT NOT NULL)''')
        self.db.execute('CREATE INDEX IF NOT EXISTS shop_trades ON trades(shop_name, time)')
        self.db.commit()

    def close(self):
        self.db.close()

    def append(self, run_id, shop_name, item, budget, result, feedback, reason):
        received = result.get('receipt')
        price = received.get('price') if isinstance(received, dict) else None
        if type(price) not in (int, float):
            price = None
        evidence = dict(result=result, feedback=feedback, selection_reason=reason)
        with self.db:
            self.db.execute('INSERT INTO trades VALUES (?,?,?,?,?,?,?,?)',
                            (run_id, datetime.now(timezone.utc).isoformat(), shop_name,
                             item, budget, result['outcome'], price,
                             json.dumps(evidence, ensure_ascii=False, allow_nan=False)))

    def view(self, shop_name, item):
        rows = self.db.execute('SELECT * FROM trades WHERE shop_name=? ORDER BY time DESC, rowid DESC LIMIT 30',
                               (shop_name,)).fetchall()
        counts = {}
        for row in rows:
            counts[row['outcome']] = counts.get(row['outcome'], 0) + 1
        def compact(row):
            evidence = json.loads(row['evidence'])
            return dict(time=row['time'], item=row['item'], budget=row['budget'],
                        outcome=row['outcome'], price=row['price'],
                        review=evidence['feedback']['review'][:400])
        matching = [compact(row) for row in rows if row['item'] == item][:3]
        return dict(window='latest 30 personal trades at this named shop', counts=counts,
                    recent=[compact(row) for row in rows[:5]], same_item=matching)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
