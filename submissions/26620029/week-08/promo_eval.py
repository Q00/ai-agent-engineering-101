"""Did the shop ads (promo.py) bring asks? A before/after comparison of the advertisable half against the control half.

promo.py advertises only SKUs whose fixed hash puts them in the test half, and picks busy ones, so advertised against
not advertised would be biased. Here both halves are cut the same way (goods in stock now; hot = HOT_MIN+ market deals in
3 h), and opens and deals an hour are compared for the BEFORE_H hours before START and the time since.
Usage (inside market-kit, from week-08): python -X utf8 promo_eval.py HH:MM:SS   (START, today, KST in the kit log)
"""
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from promo import HOT_MIN, in_test

BEFORE_H = 3


def secs(hms):
    h, m, s = map(int, hms.split(":"))
    return h * 3600 + m * 60 + s


def main():
    start = secs(sys.argv[1])
    now = datetime.now()
    now_s = None
    inv = json.loads(Path(".market/inventory.json").read_text(encoding="utf-8"))
    skus = {u["sku"] for u in inv["units"] if u.get("kind") == "goods" and u.get("unit")}
    recent = (json.loads(Path("restock_memory.json").read_text(encoding="utf-8")).get("customers") or {}).get("recent") or {}
    market = defaultdict(int)
    for bucket in recent.values():
        for s, n in bucket.items():
            market[s] += n
    count = {"before": (Counter(), Counter()), "after": (Counter(), Counter())}
    rows, day, prev = [], 0, None
    for f in ("logs/market-kit.log.1", "logs/market-kit.log"):
        p = Path(f)
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"(\d\d:\d\d:\d\d) \[seller\] \[price\] (open|deal) (G[0-9a-f]+)", line)
            if not m:
                continue
            t = secs(m.group(1))
            if prev is not None and t < prev - 6 * 3600:
                day += 1                      # the log went past midnight
            prev = t
            rows.append((t + day * 86400, m.group(2), m.group(3)))
    now_s = rows[-1][0] if rows else start
    start += (now_s // 86400) * 86400
    if start > now_s:
        start -= 86400                        # START was yesterday
    for t, kind, sku in rows:
        w = "after" if t >= start else "before" if t >= start - BEFORE_H * 3600 else None
        if w:
            count[w][0 if kind == "open" else 1][sku] += 1
    after_h = max((now_s - start) / 3600, 0.01)
    print(f"start {sys.argv[1]}, before {BEFORE_H} h, after {after_h:.2f} h, goods SKUs in stock {len(skus)}")
    for hot in (True, False):
        for test in (True, False):
            ss = {s for s in skus if in_test(s) == test and (market[s] >= HOT_MIN) == hot}
            row = []
            for w, h in (("before", BEFORE_H), ("after", after_h)):
                o, d = count[w]
                row.append(f"{w} opens {sum(o[s] for s in ss) / h:.1f}/h, deals {sum(d[s] for s in ss) / h:.1f}/h")
            print(f"  {'hot' if hot else 'other'} {'test' if test else 'control'}: {len(ss)} SKUs; " + "; ".join(row))


if __name__ == "__main__":
    main()
