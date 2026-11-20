"""Read-only: what the price cap did since the restart of 18:32:47 KST. Prints aggregates only: no unit cost, no review text. Run from week-08.

Usage: cap_check_20261106.py BOARD_NOW BOARD_BEFORE [--pages N --out PATH_OUTSIDE_THE_REPO] [--before PATH ...]
With --pages it reads at most N public ledger pages (no key) from our own sales since the restart, spaced evenly, and writes reviews to --out.
--before names earlier samples (review_sample_20261106.py) that give the accounts known before the restart.
Our sales are recognised by ledger sequence from .market/local-settlements.json, because the shop URL changes on a restart.
"""
import argparse
import collections
import datetime as d
import glob
import json
import os
import re
import ssl
import statistics as st
import time

KST = d.timezone(d.timedelta(hours=9))
ts = lambda h, m, s=0: d.datetime(2026, 11, 6, h, m, s, tzinfo=KST).timestamp()
RESTART, NOW = ts(18, 32, 47), time.time()
parser = argparse.ArgumentParser()
parser.add_argument("board_now")
parser.add_argument("board_before")
parser.add_argument("--pages", type=int, default=0)
parser.add_argument("--out")
parser.add_argument("--before", nargs="*", default=[])
args = parser.parse_args()


def read(path):
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def load(pattern):
    found = {}
    for path in glob.glob(pattern):
        try:
            found[os.path.basename(path)] = read(path)
        except (OSError, ValueError):
            pass
    return found


offers, events = load(".market/signed-offers/*.json"), load(".market/checkout-events/*.json")
rows = sorted(({**x, "status": events.get(k, {}).get("status")} for k, x in offers.items() if isinstance(x.get("issued_at"), (int, float))), key=lambda r: r["issued_at"])
done = [r for r in rows if r["status"] == "completed"]
catalog = read(".market/catalog.json")
list_of = lambda r: r.get("list_price") or (catalog.get(r["sku"]) or {}).get("list_price")

print("now", d.datetime.fromtimestamp(NOW, KST).strftime("%H:%M:%S"), "hours since restart", round((NOW-RESTART)/3600, 2))
print("\n== board")
for name, path in (("before", args.board_before), ("now", args.board_now)):
    board = read(path)
    me = next(r for r in board["shops"] if r["name"] == "kcwarni")
    open_shops = sum(bool(r.get("alive")) for r in board["shops"])
    print(name, {k: me.get(k) for k in ("deals", "stars", "reviews", "stars_rank", "book", "revenue", "all", "refunds", "broken", "stock_units")}, "alive shops", open_shops)
    student = sorted((r["stars_rank"] for r in board["shops"] if r.get("stars_rank") is not None), reverse=True)
    print("   stars_rank place", student.index(me["stars_rank"])+1, "of", len(student))

print("\n== sales per hour")
for name, a, b in (("10:47-14:27", ts(10, 47), ts(14, 27)), ("14:46-18:32", ts(14, 46), RESTART), ("since restart", RESTART, NOW)):
    h = (b-a)/3600
    g = [r for r in done if a <= r["issued_at"] < b]
    all_offers = [r for r in rows if a <= r["issued_at"] < b]
    big = [r for r in g if r["price"] >= 1000000]
    big_list = [r for r in g if (list_of(r) or 0) >= 1000000]
    print(name, f"{h:.2f}h", "deals/h", round(len(g)/h, 1), "book/h", round(sum(r["price"]-r["cost"] for r in g)/h), "revenue/h", round(sum(r["price"] for r in g)/h), "| price>=1M", len(big), "per h", round(len(big)/h, 2), "list>=1M", len(big_list), "per h", round(len(big_list)/h, 2),
          "| book/h under 1M", round(sum(r["price"]-r["cost"] for r in g if r["price"] < 1000000)/h), "| offers", len(all_offers), "paid share", round(len(g)/max(1, len(all_offers)), 3))

after = [r for r in rows if r["issued_at"] >= RESTART]
print("\n== cap state since restart, offers (paid/all)")
states = collections.defaultdict(lambda: [0, 0])
for r in after:
    s = (r.get("price_cap") or {}).get("state")
    states[s][1] += 1
    states[s][0] += r["status"] == "completed"
print({k: f"{v[0]}/{v[1]}" for k, v in states.items()})
pricing = [read(p) for p in glob.glob("logs/pricing/*.json") if os.path.getmtime(p) >= RESTART]
print("quotes", len(pricing), collections.Counter((p.get("price_cap") or {}).get("state") for p in pricing).most_common())

print("\n== grade C since restart")
print("offers by grade: canceled/all", {q: f"{sum(r['status'] == 'canceled' for r in after if r.get('grade') == q)}/{sum(r.get('grade') == q for r in after)}" for q in "ABC"})
inv = read(".market/inventory.json")
units = inv["units"]
ages = [(NOW-u["supplied_at"])/3600 for u in units if u.get("grade") == "C" and isinstance(u.get("supplied_at"), (int, float))]
print("stock", len(units), "grades", collections.Counter(u.get("grade") for u in units).most_common(), "C share", round(sum(u.get("grade") == "C" for u in units)/max(1, len(units)), 3), "C median age h", round(st.median(ages), 1) if ages else None, "C over 12h", sum(a > 12 for a in ages))
by_name = {str(p.get("name")): sku for sku, p in catalog.items()}
supplies = collections.defaultdict(list)
with open("logs/market-kit.log", "rb") as stream:
    log = stream.read().decode("utf-8", "replace").split("\n")
midnights = [i for i, line in enumerate(log[:-1]) if line[:2] == "23" and log[i+1][:2] == "00"]
today = d.datetime.fromtimestamp(NOW, KST).replace(hour=0, minute=0, second=0, microsecond=0)
for back, (start, end) in enumerate(((midnights[-1]+1, len(log)), (midnights[-2]+1, midnights[-1]+1))):
    day = today-d.timedelta(days=back)
    for line in log[start:end]:
        m = re.match(r"(\d\d):(\d\d):(\d\d) \[supply\] (\d+) x (.+) for \d+, balance", line)
        if m and m[5] in by_name:
            supplies[by_name[m[5]]].append((day.replace(hour=int(m[1]), minute=int(m[2]), second=int(m[3])).timestamp(), int(m[4])))


def depth_at(sku, at):
    now = sum(u["sku"] == sku for u in units)
    return now-sum(q for t, q in supplies[sku] if t > at)+sum(r["sku"] == sku and r["issued_at"] > at for r in done)


for name, a, b in (("14:46-18:32", ts(14, 46), RESTART), ("since restart", RESTART, NOW)):
    c = [r for r in rows if r.get("grade") == "C" and a <= r["issued_at"] < b]
    gone = [r for r in c if r["status"] == "canceled"]
    print(name, "C offers", len(c), "canceled", len(gone), "canceled with another unit of the SKU in stock", sum(depth_at(r["sku"], r["issued_at"]) >= 2 for r in gone))

if not args.pages:
    raise SystemExit
print("\n== public ledger reviews")
import httpx

local = read(".market/local-settlements.json")["records"]
own_all = {int(v["ledger"]) for v in local.values() if v.get("round") == 3 and v.get("ledger")}
own_after = sorted(int(v["ledger"]) for v in local.values() if v.get("round") == 3 and v.get("ledger") and (v.get("ts") or 0) >= RESTART)
print("own sales since restart", len(own_after))
picks = [own_after[int(i*len(own_after)/args.pages)] for i in range(args.pages)] if len(own_after) > args.pages else own_after
sales, seen, calls, covered, rows_seen = {}, set(), 0, 0, 0
started = time.time()
with httpx.Client(verify=ssl.create_default_context(), timeout=20, headers={"User-Agent": "market-review-sample"}) as client, open(args.out, "w", encoding="utf-8") as out:
    for seq in picks:
        if seq <= covered:
            continue
        response = client.get("https://market.ourolabs.ai/api/ledger", params={"after": seq-1})
        calls += 1
        if response.status_code != 200:
            print("status", response.status_code)
            break
        body = response.json()
        page = body if isinstance(body, list) else body.get("rows") or body.get("ledger") or []
        rows_seen += len(page)
        for row in page:
            data = row.get("data") or {}
            if row.get("kind") == "settle" and data.get("order"):
                sales[data["order"]] = {"seq": row["seq"], "ts": row.get("ts"), "buyer": data.get("buyer"), "shop": data.get("shop"), "amount": data.get("amount"), "item": data.get("item"), "grade": data.get("grade"), "warranty_hours": data.get("warranty_hours") or 0, "expires_at": data.get("expires_at")}
            elif row.get("kind") == "result" and data.get("verified") and data.get("order") in sales and type(data.get("stars")) in (int, float) and data["order"] not in seen:
                seen.add(data["order"])
                sale = sales[data["order"]]
                listed = (catalog.get(sale["item"]) or {}).get("list_price")
                out.write(json.dumps({**sale, "stars": data["stars"], "rater": data.get("buyer"), "list_price": listed, "ratio": round(sale["amount"]/listed, 5) if listed else None})+"\n")
        covered = max((row.get("seq", 0) for row in page), default=seq)
        time.sleep(.4)
print("calls", calls, "rows", rows_seen, "seconds", round(time.time()-started), "own sales on pages", sum(s["seq"] in own_all for s in sales.values()))

reviews = [json.loads(line) for line in open(args.out, encoding="utf-8")]
own = [r for r in reviews if r["seq"] in own_all]
others = [r for r in reviews if r["seq"] not in own_all]
earlier = {}
for path in args.before:
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        earlier[r["seq"]] = r
base = [r for r in earlier.values() if r["seq"] in own_all and float(r.get("ts") or 0) < RESTART]
mean = lambda g: round(st.mean(r["stars"] for r in g), 3) if g else None
dist = lambda g: dict(sorted(collections.Counter(r["stars"] for r in g).items()))


def bands(g):
    out = {}
    for label, test in (("list>=1M", lambda r: r["list_price"] >= 1000000), ("list<1M ratio<.79", lambda r: r["list_price"] < 1000000 and r["ratio"] < .7901), ("list<1M ratio>=.79", lambda r: r["list_price"] < 1000000 and r["ratio"] >= .7901)):
        x = [r for r in g if r.get("list_price") and test(r)]
        out[label] = {"n": len(x), "mean": mean(x), "stars": dist(x)}
    return out


print("own reviews since restart", len(own), "mean", mean(own), dist(own))
print("  ", bands(own))
print("own reviews before restart in the earlier samples", len(base), "mean", mean(base), dist(base))
print("  ", bands(base))
print("other shops on the same pages", len(others), "mean", mean(others), dist(others))


def by_account(g):
    per = collections.defaultdict(list)
    for r in g:
        per[r["rater"]].append(r["stars"])
    total = sum(st.mean(v) for v in per.values())
    return per, round((total+15)/(len(per)+5), 3), round(total/max(1, len(per)), 3)


per_now, rule_now, plain_now = by_account(own)
per_old, rule_old, plain_old = by_account(base)
print("accounts in sample: before", len(per_old), "rule", rule_old, "plain mean", plain_old, "| since restart", len(per_now), "rule", rule_now, "plain mean", plain_now)
new = sorted(set(per_now)-set(per_old))
print("accounts not seen on our shop in the earlier samples", [(n, len(per_now[n]), round(st.mean(per_now[n]), 2)) for n in new])
print("since restart, stars per account (old mean -> new mean, n)")
for name in sorted(per_now, key=lambda n: -len(per_now[n]))[:25]:
    print("  ", name[:18], round(st.mean(per_old[name]), 2) if name in per_old else None, "->", round(st.mean(per_now[name]), 2), len(per_now[name]))
for name in ("YAHO1212", "psyoung82", "mjforge", "bjeon01"):
    mine = [r for r in own if r["rater"] == name]
    all_ = [r for r in reviews if r["rater"] == name]
    print(name, "on ours since restart", [(round(r["ratio"], 3), r["stars"]) for r in mine][:12], "| all shops on pages", len(all_), "mean", mean(all_))
