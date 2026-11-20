"""Read-only look at the buyers the first pass raised: each buyer's stars with shop-side facts held alike, and the receipt item format."""
import json, re, sys, collections as C
root = "logs/star-audit/20261105/"
seen = {}
for name in ("rows.jsonl", "rows-tail.jsonl"):
    for line in open(root + name, encoding="utf-8"):
        r = json.loads(line); seen[r["seq"]] = r
rows = [seen[k] for k in sorted(seen)]
cat = json.load(open(".market/catalog.json", encoding="utf-8"))
loc = json.load(open(".market/local-settlements.json", encoding="utf-8"))["records"]
units = {r.get("unit") for r in (loc.values() if isinstance(loc, dict) else loc)}
settle = {r["data"].get("order"): r for r in rows if r["kind"] == "settle"}
own = {r["data"]["shop"] for r in rows if r["kind"] == "settle" and r["data"].get("unit") in units}
broken = {r["data"].get("order") for r in rows if r["kind"] == "broken"}
res = [r for r in rows if r["kind"] == "result" and r["data"].get("verified") and type(r["data"].get("stars")) in (int, float)]
print("own urls", len(own), "results", len(res), "ours", sum(r["data"]["shop"] in own for r in res))
print("seq range", rows[0]["seq"], rows[-1]["seq"])
sku = re.compile(r"^G[0-9a-f]{8}$")
def fmt(r):
    item = ((r["data"].get("receipt") or {}).get("item"))
    return "none" if item is None else "sku" if sku.match(str(item)) else "name"
def share(r):
    s = settle.get(r["data"].get("order"))
    if not s: return None
    l = (cat.get(s["data"].get("item")) or {}).get("list_price")
    return s["data"].get("amount", 0) / l if l else None
def band(x):
    return "?" if x is None else "<.6" if x < .6 else "<.7" if x < .7 else "<.8" if x < .8 else "<.9" if x < .9 else ">=.9"
def tmpl(t):
    return re.sub(r"[0-9a-fA-F]{6,}|\d[\d,.]*", "#", str(t or ""))[:70]
print("\n== receipt item format, ours vs others")
for name, group in (("ours", [r for r in res if r["data"]["shop"] in own]), ("others", [r for r in res if r["data"]["shop"] not in own])):
    print(name, C.Counter(fmt(r) for r in group))
for b in sys.argv[1:]:
    mine = [r for r in res if r["data"].get("buyer") == b]
    print("\n====", b, len(mine))
    for name, group in (("US", [r for r in mine if r["data"]["shop"] in own]), ("OTHER", [r for r in mine if r["data"]["shop"] not in own])):
        print(name, len(group), sorted(C.Counter(r["data"]["stars"] for r in group).items()))
        t = C.defaultdict(list)
        for r in group:
            s = settle.get(r["data"].get("order"))
            d = s["data"] if s else {}
            t[(d.get("grade"), band(share(r)), bool(d.get("warranty_hours")), (cat.get(d.get("item")) or {}).get("kind"))].append(r["data"]["stars"])
        for k in sorted(t, key=str):
            v = t[k]
            if len(v) >= (1 if name == "US" else 8):
                print("  ", k, len(v), round(sum(v) / len(v), 2), sorted(C.Counter(v).items()))
        print("  fmt", {f: (len(x), round(sum(x) / len(x), 2), sum(y <= 2 for y in x)) for f, x in ((f, [r["data"]["stars"] for r in group if fmt(r) == f]) for f in ("sku", "name", "none")) if x})
        w = C.Counter((r["data"]["stars"], tmpl(r["data"].get("review"))) for r in group)
        for (st, tx), n in w.most_common(7):
            print("  ", n, st, tx.encode("ascii", "backslashreplace").decode()[:90] if False else tx)
        print("  broke among", sum(r["data"].get("order") in broken for r in group), "low&broke", sum(r["data"].get("order") in broken and r["data"]["stars"] <= 2 for r in group))
print("\n== low reviews anywhere whose text blames a receipt name, by buyer")
pat = re.compile(r"receipt names|forge|names G|SCAM", re.I)
hits = C.Counter()
for r in res:
    if r["data"]["stars"] <= 2 and pat.search(str(r["data"].get("review"))):
        hits[(r["data"]["buyer"], r["data"]["shop"] in own, fmt(r))] += 1
print(hits.most_common(20))
print("\n== low share by item format, buyers with both formats")
tab = C.defaultdict(lambda: C.defaultdict(list))
for r in res:
    tab[r["data"]["buyer"]][fmt(r)].append(r["data"]["stars"])
for b, f in sorted(tab.items(), key=lambda p: str(p[0])):
    if len(f.get("sku", [])) >= 5 and len(f.get("name", [])) >= 5:
        a, c = f["sku"], f["name"]
        if abs(sum(a) / len(a) - sum(c) / len(c)) >= .3:
            print(b, "sku", len(a), round(sum(a) / len(a), 2), sum(x <= 2 for x in a), "name", len(c), round(sum(c) / len(c), 2), sum(x <= 2 for x in c))
