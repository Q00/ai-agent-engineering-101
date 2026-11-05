"""Read-only, no network, no model: what the orders stopped by the hourly model quota looked like, against the orders that went the rule path.

A replay of saved records. It cannot say what a seller would have done with an offer that was never made.
"""
import ctypes
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[2]
ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00100000)
SINCE = sys.argv[1] if len(sys.argv) > 1 else "20261105T000000"
folder = ROOT / "logs/market-buyer"
rows = []
for name in sorted(os.listdir(folder)):
    if name < SINCE:
        continue
    try:
        rows.append(json.loads((folder / name).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
print("records", len(rows), "since", SINCE)
paid_keys = set()
for line in (ROOT / ".market/payments.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
    try:
        p = json.loads(line)
    except ValueError:
        continue
    if p.get("paid"):
        paid_keys.add(p.get("order"))
catalog = json.loads((ROOT / ".market/catalog.json").read_text(encoding="utf-8"))


def kind_of(rec):
    return "food" if "use_within" in json.dumps(rec.get("accept_conditions") or {}) and (rec.get("accept_conditions") or {}).get("use_within_hours") else "goods"


groups = {}
for row in rows:
    rec = row.get("record") or {}
    trail = [s for s in rec.get("decision_trail") or [] if isinstance(s, dict)]
    last = trail[-1] if trail else {}
    code = f"{last.get('source')}:{last.get('reason_code')}" if trail else f"no_trail:{rec.get('failure_reason') or rec.get('outcome')}"
    groups.setdefault(code, []).append((rec, last, rec.get("order") in paid_keys))
print("\n== last decision step, count, share paid")
for code, items in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:30]:
    print(f"  {len(items):5} paid {sum(i[2] for i in items):4}  {code[:90]}")

quota = groups.get("model_gate:model_quota_hour", [])
print("\n== orders stopped by model_quota_hour:", len(quota))
print("example step keys", sorted(quota[0][1].keys()) if quota else None)
print("quote_check keys", sorted((quota[0][1].get("quote_check") or {}).keys()) if quota else None)
print("hours UTC", sorted(Counter(i[0]["timestamp"][:13] for i in quota if i[0].get("timestamp")).items()))
stats = Counter()
ratios, surplus = [], []
for rec, step, _ in quota:
    q = step.get("quote_check") or {}
    stats[str(q.get("status") or q.get("reason") or q.get("result"))] += 1
    quote = q.get("quote") or q.get("price")
    cap = q.get("cap") or q.get("budget") or rec.get("budget")
    value = q.get("value") or q.get("list_price")
    if quote and cap:
        ratios.append(quote / cap)
    if quote and value:
        surplus.append(value - quote)
print("quote_check", stats.most_common(10))
if ratios:
    r = sorted(ratios)
    print("quote/cap p10 p50 p90", round(r[len(r) // 10], 3), round(r[len(r) // 2], 3), round(r[len(r) * 9 // 10], 3), "at or under cap", sum(x <= 1 for x in ratios), "of", len(ratios))
if surplus:
    print("value-quote median", median(surplus), "positive", sum(s > 0 for s in surplus), "of", len(surplus))
print("category top", Counter(str(i[0].get("category")).split(">")[-1] for i in quota).most_common(6))

print("\n== rule path with a quote: paid share by quote/cap band (saved records)")
bands = Counter()
paid_b = Counter()
for code, items in groups.items():
    if not code.startswith("deterministic:"):
        continue
    for rec, step, paid in items:
        q = step.get("quote_check") or {}
        quote, cap = q.get("quote") or q.get("price"), q.get("cap") or q.get("budget") or rec.get("budget")
        if quote and cap:
            band = "<=.8" if quote / cap <= .8 else "<=1" if quote / cap <= 1 else "<=1.25" if quote / cap <= 1.25 else ">1.25"
            bands[band] += 1
            paid_b[band] += paid
print({b: (bands[b], paid_b[b]) for b in bands})
