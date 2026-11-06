"""Read-only: what grade C does in our shop. Saved files only, no network. Prints aggregates only: no unit cost, no review text. Run from week-08."""
import collections
import datetime as d
import glob
import json
import os
import re
import statistics as st
import time

KST = d.timezone(d.timedelta(hours=9))
ts = lambda h, m, s=0: d.datetime(2026, 11, 6, h, m, s, tzinfo=KST).timestamp()
hm = lambda t: d.datetime.fromtimestamp(t, KST).strftime("%m-%d %H:%M")
APPLY, RESTART, NOW = ts(14, 27), ts(15, 53, 17), time.time()


def load(pattern):
    found = {}
    for path in glob.glob(pattern):
        try:
            with open(path, encoding="utf-8") as stream:
                found[os.path.basename(path)] = json.load(stream)
        except (OSError, ValueError):
            pass
    return found


def read(path):
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


offers, events = load(".market/signed-offers/*.json"), load(".market/checkout-events/*.json")
rows = [{**x, "status": events.get(k, {}).get("status")} for k, x in offers.items() if isinstance(x.get("issued_at"), (int, float))]
rows.sort(key=lambda r: r["issued_at"])
done = [r for r in rows if r["status"] == "completed"]
cancel = [r for r in rows if r["status"] == "canceled"]
book = lambda g: sum(r["price"]-r["cost"] for r in g)
print("offers", len(rows), "from", hm(rows[0]["issued_at"]), "status", collections.Counter(r["status"] for r in rows).most_common())

print("\n== offers by grade and period: canceled/all")
periods = [("before 11-06", 0, ts(0, 0)), ("11-06 00:00-10:24", ts(0, 0), ts(10, 24)), ("10:24-14:27", ts(10, 24), APPLY), ("14:27-restart", APPLY, RESTART), ("restart-now", RESTART, NOW)]
for name, a, b in periods:
    g = [r for r in rows if a <= r["issued_at"] < b]
    print(name, {q: f"{sum(r['status'] == 'canceled' for r in g if r.get('grade') == q)}/{sum(r.get('grade') == q for r in g)}" for q in "ABC"})

print("\n== C offers by price, since 11-06 00:00: canceled/all")
c_all = [r for r in rows if r.get("grade") == "C" and r["issued_at"] >= ts(0, 0)]
for lo, hi in ((0, 50000), (50000, 200000), (200000, 500000), (500000, 1000000), (1000000, 10**10)):
    g = [r for r in c_all if lo <= r["price"] < hi]
    print(f"{lo}+", f"{sum(r['status'] == 'canceled' for r in g)}/{len(g)}")

print("\n== C units since 11-06 00:00")
per_unit = collections.defaultdict(list)
for r in c_all:
    per_unit[r["unit"]].append(r)
shape = collections.Counter()
for unit, g in per_unit.items():
    shape[(sum(r["status"] == "canceled" for r in g), any(r["status"] == "completed" for r in g))] += 1
print("(cancels, sold): units", sorted(shape.items()))
gaps = [b["issued_at"]-a["issued_at"] for g in per_unit.values() for a, b in zip(g, g[1:])]
print("minutes between offers of one C unit: median", round(st.median(gaps)/60, 1) if gaps else None)

print("\n== other units of the SKU in stock when a C offer was canceled")
inv = read(".market/inventory.json")
units = inv["units"]
catalog = read(".market/catalog.json")
by_name = {str(p.get("name")): sku for sku, p in catalog.items()}
supplies = collections.defaultdict(list)
with open("logs/market-kit.log", "rb") as stream:
    log = stream.read().decode("utf-8", "replace").split("\n")
day = d.datetime(2026, 11, 6, tzinfo=KST)
last_midnight = max(i for i, line in enumerate(log[:-1]) if line[:2] == "23" and log[i+1][:2] == "00")
for line in log[last_midnight+1:]:
    m = re.match(r"(\d\d):(\d\d):(\d\d) \[supply\] (\d+) x (.+) for \d+, balance", line)
    if m and m[5] in by_name:
        supplies[by_name[m[5]]].append((day.replace(hour=int(m[1]), minute=int(m[2]), second=int(m[3])).timestamp(), int(m[4])))
print("supply lines parsed for 11-06:", sum(len(v) for v in supplies.values()), "units", sum(q for v in supplies.values() for _, q in v))


def depth_at(sku, t):
    now = sum(u["sku"] == sku for u in units)
    return now-sum(q for at, q in supplies[sku] if at > t)+sum(r["sku"] == sku and r["issued_at"] > t for r in done)


for name, a, b in (("10:24-14:27", ts(10, 24), APPLY), ("14:27-now", APPLY, NOW)):
    g = [r for r in cancel if r.get("grade") == "C" and a <= r["issued_at"] < b]
    blocked = [r for r in g if depth_at(r["sku"], r["issued_at"]) >= 2]
    margins = [x["price"]-x["cost"] for r in blocked for x in done if x["sku"] == r["sku"] and x.get("grade") in ("A", "B")]
    print(name, "C cancels", len(g), "with another unit of the SKU", len(blocked), "paid A/B on those SKUs: median book per sale", round(st.median(margins)) if margins else None, "n", len(margins))
    hours = (min(b, NOW)-a)/3600
    worth = []
    for r in blocked:
        same = [x["price"]-x["cost"] for x in done if x["sku"] == r["sku"] and x.get("grade") in ("A", "B")]
        if same:
            worth.append(st.mean(same))
    print("   if each blocked buyer had paid for an A or B unit instead: book/h at most", round(sum(worth)/hours), "with SKU A/B history", len(worth), "of", len(blocked), "hours", round(hours, 2))

print("\n== grade of stock and of new units")
pricing = load("logs/pricing/*.json")
first = {}
for row in pricing.values():
    try:
        at = d.datetime.fromisoformat(row["at"]).timestamp()
    except (KeyError, TypeError, ValueError):
        continue
    if row.get("unit") and (row["unit"] not in first or at < first[row["unit"]][0]):
        first[row["unit"]] = (at, row.get("grade_observed"), row.get("sku"))
print("units quoted", len(first), "from", hm(min(v[0] for v in first.values())))
for name, a, b in (("before 11-06", 0, ts(0, 0)), ("11-06 to 14:27", ts(0, 0), APPLY), ("14:27-now", APPLY, NOW)):
    c = collections.Counter(v[1] for v in first.values() if a <= v[0] < b)
    print("first quoted", name, dict(c), "C share", round(c["C"]/max(1, sum(c.values())), 3))
c = collections.Counter(u.get("grade") for u in units)
print("stock now", dict(c), "C share", round(c["C"]/max(1, len(units)), 3))
for grade in "ABC":
    ages = [(NOW-u["supplied_at"])/3600 for u in units if u.get("grade") == grade and isinstance(u.get("supplied_at"), (int, float))]
    print(grade, "in stock", len(ages), "median age h", round(st.median(ages), 1) if ages else None, "over 12h", sum(x > 12 for x in ages))
sold_at = {r["unit"]: r["issued_at"] for r in done}
in_stock = {u["unit"] for u in units}
at_apply = collections.Counter(v[1] for u, v in first.items() if v[0] < APPLY and (u in in_stock or sold_at.get(u, 0) >= APPLY))
print("stock at 14:27 rebuilt from quotes, unquoted units missing:", dict(at_apply), "C share", round(at_apply["C"]/max(1, sum(at_apply.values())), 3))

print("\n== outcome of units first quoted from 11-05 12:00 to 12 h ago")
ca, cb = ts(0, 0)-12*3600, NOW-12*3600
for grade in "ABC":
    cohort = [u for u, v in first.items() if ca <= v[0] < cb and v[1] == grade]
    sold = [u for u in cohort if u in sold_at]
    in12 = [u for u in sold if sold_at[u]-first[u][0] <= 12*3600]
    print(grade, "units", len(cohort), "sold", len(sold), "sold within 12h", len(in12), "rate", round(len(in12)/max(1, len(cohort)), 3), "median hours to sale", round(st.median([(sold_at[u]-first[u][0])/3600 for u in sold]), 2) if sold else None)

print("\n== Book by grade")
for name, a, b in (("10:47-14:27", ts(10, 47), APPLY), ("14:27-now", APPLY, NOW), ("15:02-now", ts(15, 2), NOW)):
    h = (b-a)/3600
    g = [r for r in done if a <= r["issued_at"] < b]
    out = {}
    for grade in "ABC":
        x = [r for r in g if r.get("grade") == grade]
        out[grade] = {"sales/h": round(len(x)/h, 1), "book/h": round(book(x)/h), "book/sale": round(book(x)/max(1, len(x))), "rev/h": round(sum(r["price"] for r in x)/h), "ratio": round(book(x)/max(1, sum(r["price"] for r in x)), 3)}
    print(name, f"{h:.2f}h", "total book/h", round(book(g)/h), out)
    under = [r for r in g if r["price"] < 1000000]
    print("   under 1M book/h", round(book(under)/h), "1M+ deals", len(g)-len(under))
for grade in "ABC":
    x = [u["cost"]/u["list_price"] for u in units if u.get("grade") == grade and u.get("list_price")]
    print("cost/list in stock now", grade, round(st.median(x), 3) if x else None, len(x))

print("\n== the public ledger: who pays for C (saved rows)")
intel = read(".market/market-intel.json")
settles = [r["data"] for r in intel["rows"] if r["kind"] == "settle"]
print("settles", len(settles), "span", hm(float(intel["rows"][0]["ts"])), hm(float(intel["rows"][-1]["ts"])), "grades", collections.Counter(s.get("grade") for s in settles).most_common())
per = collections.defaultdict(collections.Counter)
for s in settles:
    if s.get("grade") in ("A", "B", "C"):
        per[s["buyer"]][s["grade"]] += 1
big = {b: c for b, c in per.items() if sum(c.values()) >= 20}
never = [b for b, c in big.items() if c["C"] == 0]
house = lambda b: bool(re.fullmatch(r"buyer-\d+", b))
print("buyers with 20+ settles", len(big), "never paid C", len(never), "share of those buyers' settles", round(sum(sum(big[b].values()) for b in never)/max(1, sum(sum(c.values()) for c in big.values())), 3))
print("house buyers", sum(house(b) for b in big), "never C", sum(house(b) for b in never), "| players", sum(not house(b) for b in big), "never C", sum(not house(b) for b in never))
goods = [s for s in settles if s.get("expires_at") is None]
print("goods settles by grade, house/player", {g: collections.Counter("house" if house(s["buyer"]) else "player" for s in goods if s.get("grade") == g).most_common() for g in "ABC"})
print("goods amount median by grade", {g: round(st.median([s["amount"] for s in goods if s.get("grade") == g] or [0])) for g in "ABC"})
broken = [r["data"] for r in intel["rows"] if r["kind"] == "broken"]
grade_of = {s["order"]: s.get("grade") for s in settles}
bc = collections.Counter(grade_of.get(x["order"]) for x in broken if x["order"] in grade_of)
gc = collections.Counter(s.get("grade") for s in goods)
print("broken after sale per settled goods, by grade", {g: f"{bc[g]}/{gc[g]}={bc[g]/max(1, gc[g]):.3f}" for g in "ABC"})
