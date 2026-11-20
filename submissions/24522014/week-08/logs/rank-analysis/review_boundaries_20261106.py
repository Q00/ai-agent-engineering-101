"""Read-only: from a review sample (review_sample_20261106.py), each rater's stars against price per list price, warranty and grade, and what a price cap could do to our per-account mean. Aggregates only.

Raters are public market accounts, reported only as statistics to choose our own price; no rater is treated differently. Usage: review_boundaries_20261106.py SAMPLE [own_url ...]
"""
import collections
import json
import statistics as st
import sys

rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]
own_urls = set(sys.argv[2:])
rows = [r for r in rows if r.get("ratio") and r["ratio"] <= 1.5]
goods = [r for r in rows if r.get("expires_at") is None]
print("reviews", len(rows), "goods", len(goods), "raters", len({r["rater"] for r in rows}), "own in sample", sum(r["shop"] in own_urls for r in rows))
mean = lambda g: round(st.mean(r["stars"] for r in g), 3) if g else None


def band_table(g):
    bands = [(0, .7), (.7, .75), (.75, .79), (.79, .8), (.8, .81), (.81, .85), (.85, .9), (.9, 1.0), (1.0, 2)]
    return {f"{a}-{b}": (len(x), mean(x)) for a, b in bands for x in [[r for r in g if a <= r["ratio"] < b]]}


print("\nall goods without warranty", band_table([r for r in goods if not r["warranty_hours"]]))
print("all goods with warranty", band_table([r for r in goods if r["warranty_hours"]]))
print("food", band_table([r for r in rows if r.get("expires_at") is not None]))
print("by grade, goods no warranty", {g: (sum(r["grade"] == g for r in goods if not r["warranty_hours"]), mean([r for r in goods if r["grade"] == g and not r["warranty_hours"]])) for g in "ABC"})
print("warranty share of goods", round(sum(bool(r["warranty_hours"]) for r in goods)/len(goods), 3), "hours", collections.Counter(r["warranty_hours"] for r in goods).most_common(6))

print("\n== raters with 12+ goods reviews without warranty: best single split")
by = collections.defaultdict(list)
for r in goods:
    if not r["warranty_hours"]:
        by[r["rater"]].append(r)
found = []
for rater, g in by.items():
    if len(g) < 12:
        continue
    g = sorted(g, key=lambda r: r["ratio"])
    best = None
    for i in range(1, len(g)):
        if g[i]["ratio"] == g[i-1]["ratio"]:
            continue
        low, high = g[:i], g[i:]
        if len(low) < 2 or len(high) < 2:
            continue
        score = (st.mean(r["stars"] for r in low)-st.mean(r["stars"] for r in high))*min(len(low), len(high))**.5
        if best is None or score > best[0]:
            best = (score, g[i-1]["ratio"], g[i]["ratio"], mean(low), mean(high), len(low), len(high))
    wrong = None
    if best:
        wrong = sum(r["stars"] < best[3]-.5 for r in g if r["ratio"] <= best[1])+sum(r["stars"] > best[4]+.5 for r in g if r["ratio"] >= best[2])
    found.append((rater, len(g), round(st.mean(r["ratio"] for r in g), 3), mean(g), best, wrong))
found.sort(key=lambda x: -(x[4][0] if x[4] else 0))
for rater, n, ratio, stars, best, wrong in found[:30]:
    print(f"{rater[:18]:18} n={n:3} mean ratio {ratio} stars {stars}", "split" if best else "", (f"last-low {best[1]:.4f} first-high {best[2]:.4f} low {best[3]} ({best[5]}) high {best[4]} ({best[6]}) off {wrong}" if best else ""))
print("raters checked", len(found), "flat (<.3 gap)", sum(1 for f in found if not f[4] or f[4][3]-f[4][4] < .3))

print("\n== same rater, warranty against none")
for rater in {r["rater"] for r in goods}:
    a = [r for r in goods if r["rater"] == rater and r["warranty_hours"]]
    b = [r for r in goods if r["rater"] == rater and not r["warranty_hours"]]
    if len(a) >= 5 and len(b) >= 5 and abs(st.mean(r["stars"] for r in a)-st.mean(r["stars"] for r in b)) >= .4:
        print(f"{rater[:18]:18} warranty {mean(a)} ({len(a)}) none {mean(b)} ({len(b)})")

print("\n== cap simulation on this sample's raters: stars by rater and band, then a per-account mean")
own = [r for r in rows if r["shop"] in own_urls]
print("own reviews", len(own), "stars", mean(own), band_table([r for r in own if r.get("expires_at") is None]))
accounts = collections.defaultdict(list)
for r in own:
    accounts[r["rater"]].append(r)
rule = lambda total, n: (total+15)/(n+5)


def expected(rater, ratio, warranted=False):
    """A rater's mean near this ratio on all shops in the sample; None when the sample has too little."""
    near = [r for r in rows if r["rater"] == rater and abs(r["ratio"]-ratio) <= .04 and bool(r["warranty_hours"]) == warranted and (r.get("expires_at") is None)]
    return st.mean(r["stars"] for r in near) if len(near) >= 3 else None


for cap in (None, .85, .8, .79, .75):
    total = 0
    for rater, g in accounts.items():
        values = []
        for r in g:
            if cap and r["ratio"] >= cap and r.get("expires_at") is None:
                value = expected(rater, cap-.01)
                values.append(r["stars"] if value is None else value)
            else:
                values.append(r["stars"])
        total += st.mean(values)
    print("cap", cap, "accounts", len(accounts), "mean by account", round(total/max(1, len(accounts)), 3), "(sum+15)/(accounts+5)", round(rule(total, len(accounts)), 3))
