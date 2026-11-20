"""Read-only: why our buyer's payments per hour moved on 11-06. Saved files only, no network. Counts and ratios only. Run from week-08."""
import collections
import datetime as d
import json
import re
import statistics as st
import time

KST = d.timezone(d.timedelta(hours=9))
ts = lambda h, m: d.datetime(2026, 11, 6, h, m, tzinfo=KST).timestamp()
NOW = time.time()
PERIODS = [("06:00-10:09", ts(6, 0), ts(10, 9)), ("10:43-14:27", ts(10, 43), ts(14, 27)), ("14:27-16:26", ts(14, 27), ts(16, 26)), ("16:26-now", ts(16, 26), NOW)]


def stamp(value):
    if isinstance(value, (int, float)):
        return value
    try:
        return d.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


rows = []
with open(".market/r3-buyer-memory.jsonl", encoding="utf-8") as stream:
    for line in stream:
        if '"timestamp"' not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        t = stamp(row.get("timestamp"))
        if t >= ts(6, 0):
            row["_t"] = t
            rows.append(row)
print("rows", len(rows), "keys", sorted(rows[-1])[:30])
words = re.compile(r"\d[\d,.]*")
for name, a, b in PERIODS:
    g = [r for r in rows if a <= r["_t"] < b]
    h = (b-a)/3600
    print(f"\n== {name} ({h:.2f} h) rows {len(g)} /h {len(g)/h:.1f}")
    print("outcome /h", {k: round(v/h, 1) for k, v in collections.Counter(r.get("outcome") for r in g).most_common()})
    print("negotiation", collections.Counter(str(r.get("negotiation_outcome"))[:40] for r in g).most_common(8))
    print("review shapes", collections.Counter(words.sub("#", str(r.get("review")))[:42] for r in g).most_common(6))
    print("error shapes", collections.Counter(words.sub("#", str(r.get("error")))[:50] for r in g if r.get("error")).most_common(5))
    paid = [r for r in g if r.get("outcome") == "purchased"]
    print("paid", len(paid), "mean discount off list unknown here; shops touched", len({r.get("shop_name") for r in g}), "budget median", round(st.median([r["budget"] for r in g if isinstance(r.get("budget"), (int, float))] or [0])))
    print("agreed price present", sum(r.get("agreed_price") is not None for r in g), "of those paid", sum(r.get("agreed_price") is not None and r.get("outcome") == "purchased" for r in g))

with open("logs/market-kit.log", "rb") as stream:
    log = stream.read().decode("utf-8", "replace").split("\n")
midnight = max(i for i, line in enumerate(log[:-1]) if line[:2] == "23" and log[i+1][:2] == "00")
orders = collections.Counter()
why = collections.defaultdict(collections.Counter)
widths = collections.defaultdict(list)
for line in log[midnight+1:]:
    m = re.match(r"(\d\d):(\d\d):\d\d ", line)
    if not m:
        continue
    t = ts(int(m[1]), int(m[2]))
    name = next((n for n, a, b in PERIODS if a <= t < b), None)
    if not name:
        continue
    if "[order o-" in line:
        orders[name] += 1
    w = re.search(r"\[kit\] (\d+) shops", line)
    if w:
        widths[name].append(int(w[1]))
    c = re.search(r"\[buyer\] \[choose\] (.{0,40})", line)
    if c:
        why[name][re.sub(r"[\w-]+/G[0-9a-f]+", "shop/sku", c[1])[:38]] += 1
    if "[acp] not paying" in line:
        why[name]["ACP not paying: "+re.sub(r"[\d.]+", "#", line.split("not paying:")[1])[:30]] += 1
for name, a, b in PERIODS:
    h = (b-a)/3600
    print("\n", name, "orders/h", round(orders[name]/h, 1), "shops median", st.median(widths[name] or [0]), "min", min(widths[name] or [0]))
    print(" ", why[name].most_common(9))
