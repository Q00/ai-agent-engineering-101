"""Buyer orders vs the avoid list, for the 24 h report on the avoid rule (person's decision 11/07 ~16:10: a shop is
avoided for untrue grade claims only with >= 2 of them AND >= 5% of its settles; alerts.avoid_report).

window(t0, t1) over logs/market-kit.log ([buyer] lines, HH:MM:SS dated by walking back from the end):
  orders            orders the buyer handled
  deal_rate         buyer_deals deal / (deal + fail) in the window (market.db)
  ledger_skips      orders where a shop was skipped for a ledger flag ('[skip] X: ledger: ...')
  missed            of those, orders that ended without a deal while a skipped ledger-flagged shop had a higher shop
                    score (market_stats.json shop_score) than the shop chosen
  unblocked_deals   deals with Kiro729 / 11im / winun1127 (the shops the new rule stopped avoiding)
    python tools/avoid_stats.py HOURS     print the last HOURS (default 24)
"""
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNBLOCKED = ("Kiro729", "11im", "winun1127")


def _lines(t0, t1, tail_bytes=120_000_000):
    path = os.path.join(HERE, "logs", "market-kit.log")
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - tail_bytes))
        lines = f.read().decode("utf-8", errors="replace").splitlines()
    out, day, prev = [], datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp(), None
    for ln in reversed(lines):
        m = re.match(r"(\d\d):(\d\d):(\d\d) ", ln)
        if not m:
            continue
        t = day + int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        if prev is not None and t > prev + 3600:
            day -= 86400
            t -= 86400
        prev = t
        if t < t0:
            break
        if t < t1:
            out.append(ln)
    out.reverse()
    return out


def window(t0, t1):
    scores = json.load(open(os.path.join(HERE, "market_stats.json"), encoding="utf-8")).get("shop_score", {})
    orders, cur = [], None
    for ln in _lines(t0, t1):
        if re.search(r"\[order o-\w+\]", ln):
            cur = {"skips": [], "choose": None, "end": None}
            orders.append(cur)
            continue
        if cur is None or "[buyer]" not in ln:
            continue
        m = re.search(r"\[skip\] (\S+): (ledger: )?", ln)
        if m:
            cur["skips"].append((m[1], bool(m[2])))
        m = re.search(r"\[choose\] \S+?: (\S+?): shop score", ln)
        if m and cur["choose"] is None:
            cur["choose"] = m[1]
        m = re.search(r'"shop": "?[^",]*"?, "receipt": (null|\{)', ln)
        if m:
            cur["end"] = "deal" if m[1] == "{" else "no deal"
    done = [o for o in orders if o["end"]]
    ledger = [o for o in done if any(led for _, led in o["skips"])]
    sc = lambda s: (scores.get(s) or {}).get("score", 0)
    missed = [o for o in ledger if o["end"] != "deal" and o["choose"]
              and any(led and sc(s) > sc(o["choose"]) for s, led in o["skips"])]
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
    try:
        d = db.execute("SELECT outcome, shop FROM buyer_deals WHERE ts>=? AND ts<?", (t0, t1)).fetchall()
    finally:
        db.close()
    deals, tried = sum(1 for o, _ in d if o == "deal"), sum(1 for o, _ in d if o in ("deal", "fail"))
    return {"orders": len(done), "deal_rate": round(deals / tried, 3) if tried else None, "deals": deals,
            "ledger_skips": len(ledger), "missed": len(missed),
            "unblocked_deals": {s: sum(1 for o, sh in d if o == "deal" and sh == s) for s in UNBLOCKED}}


if __name__ == "__main__":
    h = float(sys.argv[1]) if len(sys.argv) > 1 else 24
    now = time.time()
    print(json.dumps(window(now - h * 3600, now), ensure_ascii=False))
