"""Supply cost / list over the last two days, from the kit log [supply] lines and the current inventory."""
import json, re, statistics as st, time, datetime as dt
W = "/srv/market/repo/submissions/26620029/week-08/"
cat = json.load(open(W + ".market/catalog.json", encoding="utf-8"))
by_name = {}
for sku, it in cat.items():
    by_name.setdefault(it.get("name"), (sku, it))
inv = json.load(open(W + ".market/inventory.json", encoding="utf-8"))

pat = re.compile(r"^(\d\d):(\d\d):\d\d \[supply\] (\d+) x (.*) for (\d+), balance")
rows = []                      # (day index, hour, sku, ratio, qty, total)
day, last_h = 0, None
for f in ("logs/market-kit.log.1", "logs/market-kit.log"):
    for line in open(W + f, encoding="utf-8", errors="replace"):
        m = re.match(r"^(\d\d):", line)
        if not m:
            continue
        h = int(m.group(1))
        if last_h is not None and h < last_h - 12:
            day += 1
        last_h = h
        m = pat.match(line)
        if not m:
            continue
        q, name, total = int(m.group(3)), m.group(4).strip(), int(m.group(5))
        hit = by_name.get(name)
        if not hit:
            continue
        sku, it = hit
        rows.append((day, h, sku, total / q / it["list_price"], q, total, it["list_price"]))

def tier(lp): return "high" if lp > 1_000_000 else "mid" if lp > 200_000 else "low"
def summ(rs):
    r = [x[3] for x in rs]
    q = sum(x[4] for x in rs); t = sum(x[5] for x in rs)
    return f"lines {len(rs):4d} units {q:4d} spend {t/1e4:8,.0f}만 ratio mean {st.mean(r):.3f} med {st.median(r):.3f} p10 {sorted(r)[len(r)//10]:.3f} p90 {sorted(r)[len(r)*9//10]:.3f}" if rs else "none"

print("== log [supply] lines, matched to catalog:", len(rows), "(day 0 = log.1 start, 11-08 17:02 KST)")
print("all     ", summ(rows))
for t in ("high", "mid", "low"):
    print(f"{t:8s}", summ([x for x in rows if tier(x[6]) == t]))
print("== by 6 h block (day, start hour)")
blocks = {}
for x in rows:
    blocks.setdefault((x[0], x[1] // 6 * 6), []).append(x)
for k in sorted(blocks):
    print(f"d{k[0]} {k[1]:02d}h ", summ(blocks[k]))
print("== same SKU bought repeatedly: first half vs second half ratio (top 12 by lines)")
per = {}
for x in rows:
    per.setdefault(x[2], []).append(x)
for sku, rs in sorted(per.items(), key=lambda kv: -len(kv[1]))[:12]:
    r = [x[3] for x in rs]; n = len(r) // 2
    print(f"{sku} {cat[sku]['list_price']:>10,} n {len(r):3d} first {st.mean(r[:n] or r):.3f} second {st.mean(r[n:]):.3f} min {min(r):.3f} max {max(r):.3f}")
print("== current inventory: cost/list by grade (goods)")
g = {}
for u in inv["units"]:
    if u.get("kind") == "goods" and u.get("list_price"):
        g.setdefault(u["grade"], []).append(u["cost"] / u["list_price"])
for k, r in sorted(g.items()):
    r.sort()
    print(k, len(r), f"mean {st.mean(r):.3f} min {r[0]:.3f} p10 {r[len(r)//10]:.3f} med {st.median(r):.3f} p90 {r[len(r)*9//10]:.3f} max {r[-1]:.3f}")
print("surge in inventory.json:", json.dumps(inv.get("surge"), ensure_ascii=False)[:300])
