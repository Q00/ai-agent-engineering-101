"""Read-only: what holds stock back, list-price 1,000,000+ goods before and after restock step 1, and the current plan replayed under wider limits. Saved files only, no network, nothing written. Run from week-08. Stock cost totals are printed, so keep the output out of the repository.

Periods end at the second restart, 18:32:47, so the price cap does not enter them.
"""
import collections
import datetime as d
import glob
import json
import os
import re
import statistics as st
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.getcwd())
import restock_policy as rp
from market_intel import fresh
from restock_capacity import profitable_counts
from strategy_policy import effective

KST = d.timezone(d.timedelta(hours=9))
ts = lambda h, m, s=0: d.datetime(2026, 11, 6, h, m, s, tzinfo=KST).timestamp()
hm = lambda t: d.datetime.fromtimestamp(t, KST).strftime("%H:%M")
TOP = 1000000
BEFORE, AFTER = (ts(10, 47), ts(14, 27)), (ts(14, 46), ts(18, 32, 47))
md = Path(".market")
read = lambda name: json.loads((md / name).read_text(encoding="utf-8"))
catalog, inv, demand = read("catalog.json"), read("inventory.json"), read("demand.json")
listed = lambda sku: (catalog.get(sku) or {}).get("list_price") or 0
by_name = {str(p.get("name")): sku for sku, p in catalog.items()}

events = {os.path.basename(p): p for p in glob.glob(".market/checkout-events/*.json")}
done = []
for path in glob.glob(".market/signed-offers/*.json"):
    if os.path.getmtime(path) < ts(0, 0) or os.path.basename(path) not in events:
        continue
    o = json.load(open(path, encoding="utf-8"))
    try:
        if json.load(open(events[os.path.basename(path)], encoding="utf-8")).get("status") == "completed" and o.get("issued_at", 0) >= ts(0, 0):
            done.append(o)
    except ValueError:
        pass
done.sort(key=lambda o: o["issued_at"])

with open("logs/market-kit.log", "rb") as stream:
    log = stream.read().decode("utf-8", "replace").split("\n")
midnight = max(i for i, line in enumerate(log[:-1]) if line[:2] == "23" and log[i+1][:2] == "00")
supply = []
for line in log[midnight+1:]:
    m = re.match(r"(\d\d):(\d\d):(\d\d) \[supply\] (\d+) x (.+) for (\d+), balance", line)
    if m and m[5] in by_name:
        supply.append((ts(int(m[1]), int(m[2]), int(m[3])), by_name[m[5]], int(m[4]), int(m[6])))


def in_period(t, p):
    return p[0] <= t < p[1]


print("== 1. list price 1,000,000+: supply and sales")
for name, p in (("before 10:47-14:27", BEFORE), ("after 14:46-18:32", AFTER)):
    h = (p[1]-p[0])/3600
    s = [x for x in supply if in_period(x[0], p)]
    top = [x for x in s if listed(x[1]) >= TOP]
    sold = [o for o in done if in_period(o["issued_at"], p)]
    high = [o for o in sold if listed(o["sku"]) >= TOP]
    pricey = [o for o in sold if o["price"] >= TOP]
    print(name, f"{h:.2f}h", "supply units/h", round(sum(x[2] for x in s)/h, 1), "top units/h", round(sum(x[2] for x in top)/h, 1), "top share of units", round(sum(x[2] for x in top)/max(1, sum(x[2] for x in s)), 3), "top share of spend", round(sum(x[3] for x in top)/max(1, sum(x[3] for x in s)), 3), "top SKUs", len({x[1] for x in top}))
    print("   sales list>=1M /h", round(len(high)/h, 1), "price>=1M /h", round(len(pricey)/h, 1), "price/list median on top", round(st.median([o["price"]/listed(o["sku"]) for o in high]), 3) if high else None, "book per top sale", round(st.mean([o["price"]-o["cost"] for o in high])) if high else None, "top SKUs sold", len({o["sku"] for o in high}))

units = inv["units"]
def stock_at(t, top=None):
    """Now's stock wound back: supplies after t removed, paid sales after t added. Other exits are ignored, so a rough count."""
    pick = (lambda sku: True) if top is None else (lambda sku: (listed(sku) >= TOP) == top)
    return (sum(pick(u["sku"]) for u in units)-sum(x[2] for x in supply if x[0] > t and pick(x[1]))+sum(o["issued_at"] > t and pick(o["sku"]) for o in done))


for name, p in (("before", BEFORE), ("after", AFTER)):
    points = [p[0]+i*300 for i in range(int((p[1]-p[0])/300))]
    top, all_units = [stock_at(t, True) for t in points], [stock_at(t) for t in points]
    print("stock rebuilt", name, "top units median", st.median(top), "min", min(top), "max", max(top), "all median", st.median(all_units), "top share", round(st.mean(top)/max(1, st.mean(all_units)), 3), "share of time with 0 top", round(sum(v <= 0 for v in top)/len(top), 3))
    sales = [o for o in done if in_period(o["issued_at"], p) and listed(o["sku"]) >= TOP]
    print("   top sales per top unit on hand per hour", round(len(sales)/((p[1]-p[0])/3600)/max(.1, st.mean(top)), 3))
print("stock now: top units", sum(listed(u["sku"]) >= TOP for u in units), "of", len(units))
print("whole market, 1M+ goods settles (saved rows):")
intel = read("market-intel.json")
rows = [(float(r["ts"]), r["data"]) for r in intel["rows"] if r["kind"] == "settle" and r["data"].get("expires_at") is None]
for name, p in (("before", BEFORE), ("after", AFTER)):
    g = [x for t, x in rows if in_period(t, p)]
    span = min(p[1], rows[-1][0])-max(p[0], rows[0][0])
    if g and span > 0:
        print(name, "market list>=1M /h", round(sum(listed(x.get("item")) >= TOP for x in g)/(span/3600), 1), "price>=1M /h", round(sum(x["amount"] >= TOP for x in g)/(span/3600), 1), "of all goods /h", round(len(g)/(span/3600)), "span h", round(span/3600, 2))

print("\n== runs of the plan since 14:46")
runs = []
for path in glob.glob("logs/restock/*.json"):
    if os.path.getmtime(path) < AFTER[0]:
        continue
    try:
        runs.append(json.load(open(path, encoding="utf-8")))
    except ValueError:
        pass
print("runs", len(runs), "reasons", collections.Counter((r.get("explanation") or {}).get("reason") for r in runs).most_common())
active = [r for r in runs if r.get("buy")]
print("runs that bought", len(active), "lines median", st.median(len(r["buy"]) for r in active), "units median", st.median(r["explanation"]["planned_units"] for r in active), "budget median", round(st.median(r["explanation"]["budget"] for r in active)), "estimated/budget median", round(st.median((r["explanation"]["estimated_max_cost"]+r["explanation"]["one_day_storage_reserve"]*7)/r["explanation"]["budget"] for r in active), 3))
print("units per run counter", sorted(collections.Counter(r["explanation"]["planned_units"] for r in active).items()), "lines per run", sorted(collections.Counter(len(r["buy"]) for r in active).items()))
print("planned top units", sum(b["qty"] for r in active for b in r["buy"] if listed(b["sku"]) >= TOP), "of", sum(b["qty"] for r in active for b in r["buy"]))

print("\n== candidates now")
snapshot = fresh(md)
signals = snapshot.get("skus", {})
memory = read("r3-restock-memory.json")
own = profitable_counts(md)
categories = {x["category"] for x in demand}
limits = json.loads(Path("restock_capacity.json").read_text(encoding="utf-8"))
rate = inv.get("storage_per_day", .01)
def proven(sku):
    s = signals.get(sku, {})
    return bool(memory["sales"].get(sku, 0) or own.get(sku, 0) or (s.get("distinct_buyers", 0) >= 3 and s.get("settles_6h", 0) >= 3))
def bulk(sku):
    s = signals.get(sku, {})
    return own.get(sku, 0) >= limits["min_profitable_own_sales_6h"] or (s.get("distinct_buyers_6h", 0) >= limits["min_public_buyers"] and s.get("settles_6h", 0) >= limits["min_public_sales_6h"])
surge = {r["sku"]: r["factor"] for r in inv.get("surge") or []}
goods = {k: v for k, v in catalog.items() if v.get("kind") != "food" and v.get("category") in categories and v.get("list_price")}
def blocked(sku):
    reference = signals.get(sku, {}).get("median_price")
    return reference is not None and reference <= listed(sku)*.75*surge.get(sku, 1)*(1+rate)
owned = collections.Counter(u["sku"] for u in units)
for name, test in (("list>=1M", lambda k: listed(k) >= TOP), ("list<1M", lambda k: listed(k) < TOP)):
    keys = [k for k in goods if test(k)]
    ok = [k for k in keys if proven(k) and not blocked(k)]
    print(name, "catalog goods", len(keys), "proven", sum(proven(k) for k in keys), "proven but market median under our worst cost", sum(proven(k) and blocked(k) for k in keys), "buyable", len(ok), "bulk (3 allowed)", sum(bulk(k) for k in ok), "in stock", sum(owned[k] > 0 for k in keys), "surged", sum(k in surge for k in keys))
    print("   ceiling at 3 per bulk, 1 otherwise", sum(3 if bulk(k) else 1 for k in ok), "at 6 per bulk", sum(6 if bulk(k) else 1 for k in ok))
    print("   market settles_6h on these: median", st.median([signals.get(k, {}).get("settles_6h", 0) for k in ok] or [0]), "sum", sum(signals.get(k, {}).get("settles_6h", 0) for k in ok))

print("\n== replay: the plan's picks from current state, window forced open")
window = {**inv, "restock": {"open": True}, "local_refund_reserve": 0}
base = dict(limits)
base.pop("revision", None)
def replay(state, **change):
    with patch.object(rp, "capacity_config", return_value={**limits, **change}), patch.object(rp, "fresh_quality", create=True):
        buys, why = rp.plan(state, catalog, demand, memory["sales"], memory["cursor"], intelligence=signals, runtime=effective(md), own_profitable=own)
    return buys, why
mean_cost = st.mean(u["cost"] for u in units)
print("stock", len(units), "mean unit cost", round(mean_cost), "stock cost", round(sum(u["cost"] for u in units)), "balance", round(inv["balance"]), "budget per run rate", effective(md)["controls"]["restock_budget_rate"], "budget", round(inv["balance"]*effective(md)["controls"]["restock_budget_rate"]))
for name, change in (("now", {}), ("total 160", {"max_total_units": 160}), ("160, run 24, lines 10", {"max_total_units": 160, "max_units_per_run": 24, "max_lines_per_run": 10}), ("160, run 30, lines 10", {"max_total_units": 160, "max_units_per_run": 30, "max_lines_per_run": 10}), ("160, run 30, lines 10, sku 6", {"max_total_units": 160, "max_units_per_run": 30, "max_lines_per_run": 10, "max_proven_units_per_sku": 6})):
    buys, why = replay(window, **change)
    qty = sum(b["qty"] for b in buys)
    print(f"{name}: lines {len(buys)} units {qty} top units {sum(b['qty'] for b in buys if listed(b['sku']) >= TOP)} new SKUs {sum(owned[b['sku']] == 0 for b in buys)} estimated {round(why.get('estimated_max_cost', 0))} of budget {round(why.get('budget', 0))} reason {why.get('reason')}")

print("\n== replay: stock raised by repeating the plan's own picks until it stops (no sales)")
for name, change in (("now", {}), ("160 lines 10 run 30", {"max_total_units": 160, "max_units_per_run": 30, "max_lines_per_run": 10}), ("160 lines 10 run 30 sku 6", {"max_total_units": 160, "max_units_per_run": 30, "max_lines_per_run": 10, "max_proven_units_per_sku": 6})):
    state = json.loads(json.dumps(window))
    runs_used = 0
    stopped = None
    while runs_used < 60:
        buys, why = replay(state, **change)
        if not buys:
            stopped = why.get("reason")
            break
        runs_used += 1
        for b in buys:
            product = catalog[b["sku"]]
            cost = product["list_price"]*.65
            state["balance"] -= cost*b["qty"]
            state["units"] += [{"unit": f"sim{runs_used}{b['sku']}{i}", "sku": b["sku"], "kind": "goods", "grade": "A", "cost": cost, "category": product.get("category"), "list_price": product["list_price"], "supplied_at": time.time(), "expires_at": None} for i in range(b["qty"])]
    u = state["units"]
    skus = collections.Counter(x["sku"] for x in u)
    print(f"{name}: runs {runs_used} stopped {stopped or 'run limit'} units {len(u)} skus {len(skus)} depth {len(u)/len(skus):.2f} top units {sum(listed(x['sku']) >= TOP for x in u)} stock cost {round(sum(x['cost'] for x in u))} storage/day {round(sum(x['cost'] for x in u)*rate)} balance left {round(state['balance'])}")
