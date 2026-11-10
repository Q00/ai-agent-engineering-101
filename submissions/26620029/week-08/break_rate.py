"""Market-wide break rate per SKU over the last HOURS of the ledger: broken entries / settles."""
import httpx, json, time, collections
HOURS = 12
c = httpx.Client(); M = "https://market.ourolabs.ai"
head = c.get(f"{M}/api/ledger", params={"after": -1}, timeout=20).json()[-1]["seq"]
after = head - int(HOURS * 3600 / 112 * 500)
st, br = collections.Counter(), collections.Counter()
t0 = time.time() - HOURS * 3600
while True:
    rows = c.get(f"{M}/api/ledger", params={"after": after}, timeout=30).json()
    if not rows: break
    after = rows[-1]["seq"]
    for e in rows:
        if e["ts"] < t0: continue
        d = e.get("data") or {}
        if e["kind"] == "settle" and d.get("sku"): st[d["sku"]] += 1
        if e["kind"] == "broken" and d.get("item"): br[d["item"]] += 1
    if len(rows) < 500: break
cat = json.load(open(".market/catalog.json", encoding="utf-8"))
print("settles", sum(st.values()), "broken", sum(br.values()))
rows = [(s, st[s], br[s], br[s] / st[s]) for s in st if st[s] >= 20 and cat.get(s, {}).get("kind") == "goods"]
by = collections.defaultdict(lambda: [0, 0])
for s in st:
    k = cat.get(s, {}).get("category", "?"); by[k][0] += st[s]; by[k][1] += br[s]
print("== goods SKUs (20+ settles) by break rate")
for s, n, b, r in sorted(rows, key=lambda x: -x[3])[:20]:
    print(f"{s} {r:.3f} {b:3d}/{n:4d} list {cat[s]['list_price']:>10,} {cat[s]['name'][:28]}")
print("overall goods rate", round(sum(br[s] for s in st if cat.get(s,{}).get('kind')=='goods')/max(1,sum(n for s,n in st.items() if cat.get(s,{}).get('kind')=='goods')),4))
print("== categories by break rate (100+ settles)")
for k, (n, b) in sorted(by.items(), key=lambda kv: -kv[1][1] / max(1, kv[1][0]))[:10]:
    if n >= 100: print(f"{b/n:.3f} {b}/{n} {k}")
json.dump({"hours": HOURS, "settles": st, "broken": br}, open("logs/break-rate-%s.json" % time.strftime("%m%d-%H%M"), "w"))
