"""The benchmark (user 11/4 03:3x): our shop and buyer next to the top players, from the public ledger and board.

    py -m outer.benchmark            # writes memory/facts/benchmark.json and logs/strategy/benchmark.md

The outer loop runs it every BENCH_EVERY; the strategy brief reads its summary. Shops: the top three by board
profit - deals per hour, median paid share of list, goods share of revenue, stock bought (supply), their best
sellers and which of them we do not hold, and our SKUs nobody in the market sold in the window. Buyers: the top
three by buyer ALL - deals per hour, median paid share of list for goods and for food, goods share, surplus per
deal, the shops they buy from most.
"""
import collections
import json
import statistics as st
import sys
import time

from core import atomic, paths

HOURS = 6
TOP = 3
MARKET = "https://market.ourolabs.ai"
ME = "gom31-certified-goods"
BANDS = [(0, 30000, "<30k"), (30000, 100000, "30-100k"), (100000, 300000, "100-300k"), (300000, 10 ** 9, "300k+")]


def _rows(since):
    for f in sorted((paths.memory() / "market").glob("ledger-*.jsonl"))[-2:]:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("ts", 0) >= since:
                    yield d


def _board():
    try:
        import httpx
        return httpx.get(MARKET + "/api/board", timeout=20).json()
    except Exception:                       # the benchmark works from the ledger alone
        return {}


def _med(xs):
    return round(st.median(xs), 2) if xs else None


def build(now=None, board=None):
    now = now or time.time()
    since = now - HOURS * 3600
    root = paths.root()
    cat = json.loads((root / ".market" / "catalog.json").read_text(encoding="utf-8"))
    names = {}
    try:
        shops_now = json.loads((root / ".market" / "shops.json").read_text(encoding="utf-8"))
        for s in (shops_now if isinstance(shops_now, list) else shops_now.get("shops", [])):
            names[s.get("url", "").rstrip("/")] = s["name"]
    except (OSError, ValueError, KeyError):
        pass
    settles, supply = [], []
    orders_by = collections.Counter()
    stars_by = collections.defaultdict(list)
    for d in _rows(0):                       # joins from the start, so old tunnel URLs keep their names
        if d["kind"] == "join" and d["data"].get("role") == "shop":
            names[d["data"]["url"].rstrip("/")] = d["data"]["name"]
        elif d["ts"] >= since and d["kind"] == "settle":
            settles.append(d)
        elif d["ts"] >= since and d["kind"] == "supply":
            supply.append(d)
        elif d["ts"] >= since and d["kind"] == "order":
            orders_by[d["data"].get("buyer")] += 1
        elif d["ts"] >= since and d["kind"] == "result" and d["data"].get("stars") is not None:
            stars_by[d["data"].get("shop") or ""].append(d["data"]["stars"])

    def shop_of(x):
        u = x["shop"].rstrip("/")
        return names.get(u) or (u.split("/shop/")[-1] if "/shop/" in u else u)

    shop_sales, buyer_deals = collections.defaultdict(list), collections.defaultdict(list)
    for d in settles:
        x = d["data"]
        c = cat.get(x.get("sku") or x.get("item"))
        if not c or not c.get("list_price"):
            continue
        row = {"ts": d["ts"], "sku": c["sku"], "kind": c["kind"], "list": c["list_price"], "paid": x["amount"],
               "shop": shop_of(x), "buyer": x.get("buyer"), "warranty": x.get("warranty_hours") or 0,
               "grade": x.get("grade"), "url": x["shop"].rstrip("/")}
        shop_sales[row["shop"]].append(row)
        buyer_deals[row["buyer"]].append(row)
    bought = collections.defaultdict(lambda: {"units": 0, "spend": 0})
    for d in supply:
        b = bought[d["data"]["shop"]]
        b["units"] += d["data"].get("qty", 0)
        b["spend"] += d["data"].get("total", 0)
    market_sold = collections.Counter(r["sku"] for rows in shop_sales.values() for r in rows)

    def shop_stats(name):
        rows = shop_sales.get(name, [])
        rev = sum(r["paid"] for r in rows) or 1
        return {"name": name, "deals": len(rows), "deals_per_h": round(len(rows) / HOURS, 1),
                "paid_share": _med([r["paid"] / r["list"] for r in rows]),
                "goods_revenue_share": round(sum(r["paid"] for r in rows if r["kind"] == "goods") / rev, 2),
                "avg_paid": round(rev / len(rows)) if rows else 0,
                "stock_bought": bought.get(name, {"units": 0, "spend": 0}),
                "warranty_hours": collections.Counter(r["warranty"] for r in rows).most_common(3),
                "grades": dict(collections.Counter(r["grade"] for r in rows)),
                "review_stars": (lambda xs: round(sum(xs) / len(xs), 2) if xs else None)(
                    sum((stars_by.get(u, []) for u in {r["url"] for r in rows}), [])),
                "top_skus": [(s, n) for s, n in collections.Counter(r["sku"] for r in rows).most_common(8)]}

    def buyer_stats(name):
        rows = buyer_deals.get(name, [])
        g = [r for r in rows if r["kind"] == "goods"]
        f = [r for r in rows if r["kind"] == "food"]
        sur = [r["list"] - r["paid"] for r in rows]
        bands = {}
        for lo, hi, lab in BANDS:                    # 11/4 11:3x: where the surplus comes from, by list price
            b = [r for r in rows if lo <= r["list"] < hi]
            if b:
                bands[lab] = {"deals": len(b), "goods": sum(1 for r in b if r["kind"] == "goods"),
                              "paid_share": _med([r["paid"] / r["list"] for r in b]),
                              "surplus": sum(r["list"] - r["paid"] for r in b)}
        n_orders = orders_by.get(name, 0)
        return {"name": name, "deals": len(rows), "deals_per_h": round(len(rows) / HOURS, 1), "bands": bands,
                "orders_per_h": round(n_orders / HOURS, 1), "close_rate": round(len(rows) / n_orders, 2) if n_orders else None,
                "goods_warranty_share": round(sum(1 for r in g if r["warranty"]) / len(g), 2) if g else None,
                "goods_share": round(len(g) / len(rows), 2) if rows else None,
                "paid_share_goods": _med([r["paid"] / r["list"] for r in g]),
                "paid_share_food": _med([r["paid"] / r["list"] for r in f]),
                "surplus_per_deal": round(sum(sur) / len(sur)) if sur else 0, "surplus": sum(sur),
                "top_shops": collections.Counter(r["shop"] for r in rows).most_common(4)}

    board = board if board is not None else _board()
    top_shops = [s["name"] for s in sorted((s for s in board.get("shops", []) if not s.get("house")),
                                           key=lambda s: -(s.get("profit") or 0))[:TOP]]
    top_buyers = [b["name"] for b in sorted((b for b in board.get("buyers", []) if not b.get("house")),
                                            key=lambda b: -(b.get("all") or 0))[:TOP]]
    if not top_shops:                        # no board: the busiest by ledger
        top_shops = [n for n, _ in sorted(shop_sales.items(), key=lambda kv: -len(kv[1]))[:TOP]]
    if not top_buyers:
        top_buyers = [n for n, _ in sorted(buyer_deals.items(), key=lambda kv: -len(kv[1]))[:TOP]]

    inv = json.loads((root / ".market" / "inventory.json").read_text(encoding="utf-8"))
    held = collections.Counter(u["sku"] for u in inv.get("units", []) if u.get("grade") != "expired")
    their_best = collections.Counter()
    for n in top_shops:
        for r in shop_sales.get(n, []):
            their_best[r["sku"]] += 1
    missing = [(s, k, cat[s]["list_price"]) for s, k in their_best.most_common(25) if s not in held][:12]
    dead = sorted(((s, n, cat.get(s, {}).get("list_price")) for s, n in held.items() if market_sold.get(s, 0) == 0),
                  key=lambda x: -(x[2] or 0))[:12]
    doc = {"ts": now, "hours": HOURS,
           "shops": {"ours": shop_stats(ME), "top": [shop_stats(n) for n in top_shops]},
           "buyers": {"ours": buyer_stats(ME), "top": [buyer_stats(n) for n in top_buyers]},
           "skus": {"their_best_we_lack": missing, "ours_nobody_sold": dead,
                    "held_skus": len(held), "held_units": sum(held.values())}}
    atomic.write_json(paths.memory() / "facts" / "benchmark.json", doc)
    (root / "logs" / "strategy" / "benchmark.md").write_text(render(doc, cat), encoding="utf-8")
    return doc


def render(doc, cat):
    def nm(s):
        return (cat.get(s, {}).get("name") or s)[:30]
    L = [f"# Benchmark {time.strftime('%Y-%m-%d %H:%M', time.localtime(doc['ts']))} (last {doc['hours']} h, "
         "public ledger and board)", "", "## Shops (top by board profit)", "",
         "| shop | deals/h | paid/list | goods rev | avg paid | stock bought (units, won) |", "|---|---|---|---|---|---|"]
    for s in [doc["shops"]["ours"]] + doc["shops"]["top"]:
        b = s["stock_bought"]
        L.append(f"| {s['name']} | {s['deals_per_h']} | {s['paid_share']} | {s['goods_revenue_share']} | "
                 f"{s['avg_paid']:,} | {b['units']}, {b['spend']:,} |")
    L += ["", "Their best sellers we do not hold: " + "; ".join(f"{nm(s)} ({s}, sold {k}, list {lp:,})"
                                                              for s, k, lp in doc["skus"]["their_best_we_lack"]),
          "", "Ours nobody in the market sold in the window: " + "; ".join(
              f"{nm(s)} x{n} (list {lp:,})" for s, n, lp in doc["skus"]["ours_nobody_sold"] if lp),
          "", "## Buyers (top by buyer ALL)", "",
          "| buyer | deals/h | goods share | paid/list goods | paid/list food | surplus/deal | surplus |",
          "|---|---|---|---|---|---|---|"]
    for b in [doc["buyers"]["ours"]] + doc["buyers"]["top"]:
        L.append(f"| {b['name']} | {b['deals_per_h']} | {b['goods_share']} | {b['paid_share_goods']} | "
                 f"{b['paid_share_food']} | {b['surplus_per_deal']:,} | {b['surplus']:,} |")
    L += ["", "### Behaviour", "", "| player | orders/h | close rate | goods under warranty | shop warranty h | grades | review stars |",
          "|---|---|---|---|---|---|---|"]
    for b in [doc["buyers"]["ours"]] + doc["buyers"]["top"]:
        L.append(f"| buyer {b['name']} | {b.get('orders_per_h')} | {b.get('close_rate')} | {b.get('goods_warranty_share')} | | | |")
    for x in [doc["shops"]["ours"]] + doc["shops"]["top"]:
        L.append(f"| shop {x['name']} | | | | {x.get('warranty_hours')} | {x.get('grades')} | {x.get('review_stars')} |")
    L += ["", "### Buyer surplus by list price band: deals (goods) paid/list surplus", "",
          "| buyer | " + " | ".join(lab for _, _, lab in BANDS) + " |", "|---|" + "---|" * len(BANDS)]
    for b in [doc["buyers"]["ours"]] + doc["buyers"]["top"]:
        cells = []
        for _, _, lab in BANDS:
            x = (b.get("bands") or {}).get(lab)
            cells.append(f"{x['deals']} ({x['goods']}g) {x['paid_share']} {x['surplus']:,}" if x else "-")
        L.append(f"| {b['name']} | " + " | ".join(cells) + " |")
    return "\n".join(L) + "\n"


def summary(doc):
    """A short text for the strategy brief."""
    if not doc:
        return "no benchmark yet"
    o, t = doc["shops"]["ours"], doc["shops"]["top"]
    ob, tb = doc["buyers"]["ours"], doc["buyers"]["top"]
    return json.dumps({"shop_ours": {k: o[k] for k in ("deals_per_h", "paid_share", "avg_paid")},
                       "shop_top": [{k: s[k] for k in ("name", "deals_per_h", "paid_share", "avg_paid", "stock_bought")}
                                    for s in t],
                       "their_best_we_lack": doc["skus"]["their_best_we_lack"][:8],
                       "ours_nobody_sold": doc["skus"]["ours_nobody_sold"][:8],
                       "buyer_ours": {k: ob[k] for k in ("deals_per_h", "goods_share", "paid_share_goods",
                                                         "paid_share_food", "surplus_per_deal")},
                       "buyer_top": [{k: b[k] for k in ("name", "deals_per_h", "goods_share", "paid_share_goods",
                                                        "paid_share_food", "surplus_per_deal")} for b in tb]},
                      ensure_ascii=False)


if __name__ == "__main__":
    d = build()
    print(summary(d) if "--json" in sys.argv else (paths.root() / "logs" / "strategy" / "benchmark.md").read_text(encoding="utf-8"))
