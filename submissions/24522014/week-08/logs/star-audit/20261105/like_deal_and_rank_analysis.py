"""Read-only: each buyer's stars on us against what that same buyer gave elsewhere for like deals, the time-matched check, owner links and rank effect."""
import collections as C
import json
import math
import sys

sys.path.insert(0, ".")
from rank_analysis import what_if, SHOP_WEIGHTS, eligible, percentile

root = "logs/star-audit/20261105/"
seen = {}
for name in ("rows.jsonl", "rows-tail.jsonl"):
    for line in open(root + name, encoding="utf-8"):
        r = json.loads(line)
        seen[r["seq"]] = r
rows = [seen[k] for k in sorted(seen)]
cat = json.load(open(".market/catalog.json", encoding="utf-8"))
loc = json.load(open(".market/local-settlements.json", encoding="utf-8"))["records"]
units = {r.get("unit") for r in (loc.values() if isinstance(loc, dict) else loc)}
settle = {r["data"].get("order"): r for r in rows if r["kind"] == "settle"}
own = {r["data"]["shop"] for r in rows if r["kind"] == "settle" and r["data"].get("unit") in units}
broken = {r["data"].get("order") for r in rows if r["kind"] == "broken"}
res = [r for r in rows if r["kind"] == "result" and r["data"].get("verified") and type(r["data"].get("stars")) in (int, float) and r["data"].get("shop")]


def stratum(r):
    s = settle.get(r["data"].get("order"))
    if not s:
        return None
    d = s["data"]
    l = (cat.get(d.get("item")) or {}).get("list_price")
    if not l:
        return None
    x = d.get("amount", 0) / l
    band = "<.6" if x < .6 else "<.7" if x < .7 else "<.8" if x < .8 else "<.9" if x < .9 else ">=.9"
    return ((cat.get(d.get("item")) or {}).get("kind"), bool(d.get("warranty_hours")), band)


by = C.defaultdict(lambda: {"u": [], "o": []})
for r in res:
    by[r["data"]["buyer"]]["u" if r["data"]["shop"] in own else "o"].append(r)
print("== like-for-like: (buyer, n_us, mean_us, n_other, mean_other, matched, unmatched, mean_diff, se)")
out = []
for b, v in by.items():
    if len(v["u"]) < 5:
        continue
    table = C.defaultdict(list)
    for r in v["o"]:
        table[stratum(r)].append(r["data"]["stars"])
    diffs, unmatched = [], 0
    for r in v["u"]:
        ref = table.get(stratum(r)) or []
        if len(ref) >= 5:
            diffs.append(r["data"]["stars"] - sum(ref) / len(ref))
        else:
            unmatched += 1
    raw = sum(r["data"]["stars"] for r in v["u"]) / len(v["u"])
    other = sum(r["data"]["stars"] for r in v["o"]) / len(v["o"]) if v["o"] else None
    mean = sum(diffs) / len(diffs) if diffs else None
    se = (math.sqrt(sum((d - mean) ** 2 for d in diffs) / (len(diffs) - 1)) / math.sqrt(len(diffs))) if len(diffs) > 1 else None
    out.append((b, len(v["u"]), round(raw, 2), len(v["o"]), other and round(other, 2), len(diffs), unmatched, None if mean is None else round(mean, 3), None if se is None else round(se, 3)))
for row in sorted(out, key=lambda x: (x[7] if x[7] is not None else 9)):
    print(row)
print("\n== market stars by kind and warranty, us vs others")
m = C.defaultdict(list)
for r in res:
    s = stratum(r)
    if s:
        m[("us" if r["data"]["shop"] in own else "others", s[0], s[1])].append(r["data"]["stars"])
for k in sorted(m, key=str):
    print(k, len(m[k]), round(sum(m[k]) / len(m[k]), 3))
print("\n== totato time-matched: 1-star share per 6h window, us vs others")
tt = by["totato2203-r3"]
win = C.defaultdict(lambda: [0, 0, 0, 0])
for tag, group in (("u", tt["u"]), ("o", tt["o"])):
    for r in group:
        w = int(r["ts"] // 21600)
        i = 0 if tag == "u" else 2
        win[w][i] += 1
        win[w][i + 1] += r["data"]["stars"] == 1
exp = obs = var = 0
for w in sorted(win):
    a, b1, c, d1 = win[w]
    if a:
        p = d1 / c if c else None
        print(w * 21600, "us", a, b1, "others", c, d1, None if p is None else round(p, 2))
        if c >= 10:
            exp += a * p
            obs += b1
            var += a * p * (1 - p)
print("expected", round(exp, 1), "observed", obs, "z", round((obs - exp) / math.sqrt(var), 2) if var else None)
shops = C.defaultdict(lambda: [0, 0])
for r in tt["o"]:
    shops[r["data"]["shop"]][0] += 1
    shops[r["data"]["shop"]][1] += r["data"]["stars"] == 1
big = [(v[0], v[1], round(v[1] / v[0], 2)) for v in shops.values() if v[0] >= 20]
print("totato other shops n>=20 (n, ones, share):", sorted(big, key=lambda x: -x[2]))
print("broke among ours", sum(r["data"].get("order") in broken for r in tt["u"]))
print("\n== owners")
reg = json.load(open(root + "shops.json", encoding="utf-8"))
sh = reg.get("shops", reg) if isinstance(reg, dict) else reg
print("shop keys", sorted(sh[0].keys()))
b = json.load(open(root + "board.json", encoding="utf-8"))
print("board keys", list(b), "shop row keys", sorted(b["shops"][0].keys()))
print("player sample", json.dumps(b["players"][0], ensure_ascii=True)[:300])
print("buyer row keys", sorted(b["buyers"][0].keys()))
for name in ("totato2203-r3", "buzllightyear", "move_76", "q00"):
    stem = name.split("-")[0].lower()
    srow = [{k: x.get(k) for k in ("name", "owner", "player", "deals", "stars") if k in x} for x in b["shops"] if stem in str(x.get("name", "")).lower() or stem in str(x.get("owner", "")).lower()]
    brow = [{k: x.get(k) for k in ("name", "owner", "player", "deals") if k in x} for x in b["buyers"] if x.get("name") == name]
    print(name, "buyer", brow, "shops", srow[:3])
print("\n== stars_rank fit")
rows2 = [x for x in b["shops"] if x.get("reviews") and x.get("stars_rank") is not None and x.get("stars") is not None]
best = None
for k in [i / 2 for i in range(0, 80)]:
    for m0 in [3 + i * .02 for i in range(0, 110)]:
        e = sum((((x["reviews"] * x["stars"] + k * m0) / (x["reviews"] + k)) - x["stars_rank"]) ** 2 for x in rows2)
        if best is None or e < best[0]:
            best = (e, k, m0)
print("sse,k,m", best, len(rows2), "max err", max(abs(((x["reviews"] * x["stars"] + best[1] * best[2]) / (x["reviews"] + best[1])) - x["stars_rank"]) for x in rows2))
me = next(x for x in b["shops"] if x["name"] == "kcwarni")
el = eligible(b, "shops")
print({k: me.get(k) for k in ("reviews", "stars", "stars_rank", "all", "deals", "broken")}, "pct", percentile(me["stars_rank"], [x.get("stars_rank") or 0 for x in el]), len(el))
ours = [r for r in res if r["data"]["shop"] in own]
total_stars = me["reviews"] * me["stars"]
k, m0 = best[1], best[2]


def scen(name, removed=None, changed=None):
    n, s = me["reviews"], total_stars
    for r in removed or ():
        n -= 1
        s -= r["data"]["stars"]
    for r, new in changed or ():
        s += new - r["data"]["stars"]
    mean = s / n
    ranked = (s + k * m0) / (n + k)
    pts = what_if(me, el, SHOP_WEIGHTS, "stars_rank", round(ranked, 2))
    print(name, "removed", len(removed or ()), "mean", round(mean, 3), "rank value", round(ranked, 3), "pct", round(percentile(round(ranked, 2), [x.get("stars_rank") or 0 for x in el if x is not me] + [round(ranked, 2)]), 3), "points", round(pts, 4))


q = [r for r in ours if r["data"]["buyer"] == "q00"]
low = [r for r in ours if r["data"]["stars"] <= 2]
tot1 = [r for r in ours if r["data"]["buyer"] == "totato2203-r3" and r["data"]["stars"] == 1]
scen("q00 removed", q)
scen("q00 + totato 1-star removed", q + tot1)
scen("all <=2 removed", low)
scen("all <=3 removed", [r for r in ours if r["data"]["stars"] <= 3])
print("low counts by buyer", C.Counter(r["data"]["buyer"] for r in low).most_common(), "threes:", C.Counter(r["data"]["buyer"] for r in ours if r["data"]["stars"] == 3).most_common(8))
print("low by day", sorted(C.Counter(int(r["ts"] // 86400) for r in low).items()))
all_star = C.Counter(r["data"]["stars"] for r in ours)
print("ours", len(ours), sorted(all_star.items()))
