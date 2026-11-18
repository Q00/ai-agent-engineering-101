"""The board's top 5 shops and top 5 buyers (students and house agents alike, by ALL), measured on the ledger
copy in market.db, next to my own shop and buyer. Differences only, no advice.

    python tools/competitor.py          -> reports/competitor_YYYYMMDD.md

Shop:  average price as % of the SKU's market average at the time, main categories, grade mix,
       share of honest grade claims, deals per 6-hour block of the day.
Buyer: average price paid as % of the SKU's market average, main categories, grade mix, deals per hour.
The ledger copy starts when market_db.py first ran (it read the last 20,000 entries then), so every number covers
the same window, stated at the top of the report.
"""
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime

import httpx

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ME = "choo-chu"
BOARD = "https://market.ourolabs.ai/api/board?round=3"


def leaf(catalog, sku):
    return ((catalog.get(sku) or {}).get("category") or "?").split(">")[-1]


def measure(db, catalog, col, name):
    rows = db.execute(f"SELECT ts, sku, grade, amount, market_avg_at, claim_honest FROM settles WHERE {col}=?",
                      (name,)).fetchall()
    if not rows:
        return {"n": 0}
    ratios = [a / m for _, _, _, a, m, _ in rows if m]
    grades = Counter(g for _, _, g, _, _, _ in rows if g)
    cats = Counter(leaf(catalog, s) for _, s, _, _, _, _ in rows)
    honest = [h for *_, h in rows if h is not None]
    blocks = Counter(datetime.fromtimestamp(ts).hour // 6 for ts, *_ in rows)
    span_h = max((rows[-1][0] - rows[0][0]) / 3600, 1)
    n = len(rows)
    return {"n": n, "ratio": sum(ratios) / len(ratios) if ratios else None,
            "cats": cats.most_common(3), "grades": {g: grades[g] / n for g in "ABC"},
            "honest": sum(honest) / len(honest) if honest else None,
            "blocks": {b: blocks[b] for b in range(4)}, "per_h": n / span_h}


def pct(x):
    return "-" if x is None else f"{100 * x:.0f}%"


def row_shop(name, all_, m):
    if not m["n"]:
        return f"| {name} | {all_} | 0 | - | - | - | - | - |"
    g = m["grades"]
    return (f"| {name} | {all_} | {m['n']} | {pct(m['ratio'])} | {', '.join(f'{c} {k}' for c, k in m['cats'])} | "
            f"A {pct(g['A'])} / B {pct(g['B'])} / C {pct(g['C'])} | {pct(m['honest'])} | "
            f"{' / '.join(str(m['blocks'][b]) for b in range(4))} |")


def row_buyer(name, all_, m):
    if not m["n"]:
        return f"| {name} | {all_} | 0 | - | - | - | - |"
    g = m["grades"]
    return (f"| {name} | {all_} | {m['n']} | {pct(m['ratio'])} | {', '.join(f'{c} {k}' for c, k in m['cats'])} | "
            f"A {pct(g['A'])} / B {pct(g['B'])} / C {pct(g['C'])} | {m['per_h']:.1f} |")


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def main():
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
    with open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8") as f:
        catalog = json.load(f)
    j = httpx.get(BOARD, timeout=30).json()
    shops = sorted(j["shops"], key=lambda x: -(x.get("all") or 0))[:5]
    buyers = sorted(j["buyers"], key=lambda x: -(x.get("all") or 0))[:5]
    my_shop = next((x for x in j["shops"] if x["name"] == ME), {})
    my_buyer = next((x for x in j["buyers"] if x["name"] == ME), {})
    t0, t1 = db.execute("SELECT MIN(ts), MAX(ts) FROM settles").fetchone()

    S = {x["name"]: (x.get("all"), measure(db, catalog, "shop", x["name"])) for x in shops}
    B = {x["name"]: (x.get("all"), measure(db, catalog, "buyer", x["name"])) for x in buyers}
    ms, mb = measure(db, catalog, "shop", ME), measure(db, catalog, "buyer", ME)

    def gap(label, mine, theirs, fmt):
        avg = mean(theirs)
        if mine is None or avg is None or avg == 0:
            return None
        return (abs(mine - avg) / abs(avg), f"{label}: ours {fmt(mine)}, top-5 average {fmt(avg)}")

    shop_ms = [m for _, m in S.values() if m["n"]]
    buy_ms = [m for _, m in B.values() if m["n"]]
    num = lambda x: f"{x:.1f}"
    gaps = [g for g in (
        gap("shop price vs market average", ms.get("ratio"), [m["ratio"] for m in shop_ms], pct),
        gap("shop deals per hour", ms.get("per_h"), [m["per_h"] for m in shop_ms], num),
        gap("shop grade A share", (ms.get("grades") or {}).get("A"), [m["grades"]["A"] for m in shop_ms], pct),
        gap("shop grade C share", (ms.get("grades") or {}).get("C"), [m["grades"]["C"] for m in shop_ms], pct),
        gap("shop honest grade claims", ms.get("honest"), [m["honest"] for m in shop_ms], pct),
        gap("buyer price vs market average", mb.get("ratio"), [m["ratio"] for m in buy_ms], pct),
        gap("buyer deals per hour", mb.get("per_h"), [m["per_h"] for m in buy_ms], num),
        gap("buyer grade C share", (mb.get("grades") or {}).get("C"), [m["grades"]["C"] for m in buy_ms], pct),
        gap("buyer grade A share", (mb.get("grades") or {}).get("A"), [m["grades"]["A"] for m in buy_ms], pct),
    ) if g]
    gaps.sort(key=lambda g: -g[0])

    day = datetime.now().strftime("%Y%m%d")
    lines = [f"# Competitor comparison {datetime.now():%Y-%m-%d %H:%M}", "",
             f"Ledger window in market.db: {datetime.fromtimestamp(t0):%m-%d %H:%M} to "
             f"{datetime.fromtimestamp(t1):%m-%d %H:%M}. Board: https://market.ourolabs.ai/board (round 3).",
             "Price % = settled price / the SKU's market average (last 2 h) at that time. Deals per 6-hour block: "
             "00-06 / 06-12 / 12-18 / 18-24 (local time).", "",
             "## Shops (board top 5 by ALL, then mine)", "",
             "| shop | ALL | deals | price % | main categories | grades | honest claims | deals per block |",
             "|---|---|---|---|---|---|---|---|"]
    lines += [row_shop(n, a, m) for n, (a, m) in S.items()]
    lines += [row_shop(f"{ME} (mine)", my_shop.get("all"), ms), "",
              "## Buyers (board top 5 by ALL, then mine)", "",
              "| buyer | ALL | deals | price % | main categories | grades | deals per hour |",
              "|---|---|---|---|---|---|---|"]
    lines += [row_buyer(n, a, m) for n, (a, m) in B.items()]
    lines += [row_buyer(f"{ME} (mine)", my_buyer.get("all"), mb), "",
              "## The three largest differences from us", "",
              "Relative gap |ours - top-5 average| / top-5 average, largest first. Differences only.", ""]
    lines += [f"{i}. {text} (gap {100 * g:.0f}%)" for i, (g, text) in enumerate(gaps[:3], 1)] + [""]
    os.makedirs(os.path.join(HERE, "reports"), exist_ok=True)
    path = os.path.join(HERE, "reports", f"competitor_{day}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print(f"-> {path}")


if __name__ == "__main__":
    main()
