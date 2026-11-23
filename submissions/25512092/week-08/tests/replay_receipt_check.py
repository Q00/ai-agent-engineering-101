"""Replay depts/receipt_check.agreed over every past receipt (market.db): how many it would change, high (cost over
qc.HIGH_MIN) / normal, paid / unpaid, and how many it would turn into a counter (agreed price under the floor)."""
import os
import re
import sqlite3
import sys
from collections import Counter

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from depts.qc import HIGH_MIN  # noqa: E402
from depts.receipt_check import agreed  # noqa: E402

d = sqlite3.connect(os.path.join(HERE, "market.db"))
c, ex = Counter(), []
rows = d.execute("SELECT task, cost, price, first_ask, floor, unit, ts FROM shop_deals WHERE outcome='receipt' AND price IS NOT NULL").fetchall()
for task, cost, price, ask0, floor, unit, ts in rows:
    msgs = d.execute("SELECT who, text FROM shop_msgs WHERE task=? ORDER BY ts", (task,)).fetchall()
    asks = [int(m.group(1)) for w, t in msgs if w == "shop" for m in [re.match(r"(?:ask|counter) (\d+)", t or "")] if m]
    acc = [i for i, (w, t) in enumerate(msgs) if w == "shop" and (t or "").startswith("accept")]
    before = msgs[:acc[-1]] if acc else msgs
    text = next((t for w, t in reversed(before) if w == "buyer"), "") or ""
    last_ask = asks[-1] if asks else ask0
    a, why = agreed(text, price, last_ask)
    paid = d.execute("SELECT 1 FROM settles WHERE unit=? AND shop='choo-chu' AND ts BETWEEN ? AND ?",
                     (unit, ts - 60, ts + 1800)).fetchone() is not None
    tier = "high" if (cost or 0) > HIGH_MIN else "normal"
    c[(tier, "all", "paid" if paid else "unpaid")] += 1
    if why:
        act = "counter (agreed under floor)" if floor and a < floor else "receipt at agreed"
        c[(tier, act, "paid" if paid else "unpaid")] += 1
        if len(ex) < 12:
            ex.append((tier, price, a, last_ask, floor, paid, text[:90]))
print(f"receipts {len(rows)}")
for k, v in sorted(c.items()):
    print(v, k)
for e in ex:
    print("  ", e)
