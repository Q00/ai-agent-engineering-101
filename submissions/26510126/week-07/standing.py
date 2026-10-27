"""Our Round-2 standing from /api/board, as the Markdown block in REPORT.md section 3.
The market ranks shops and buyers separately; the combined rank is ours: the participants that run both
with 20+ deals each and 20+ shop reviews, ranked on five measures, ranks averaged. House agents left out."""
import datetime, json, urllib.request

ME = "thisjonghoon"
board = json.load(urllib.request.urlopen("https://market.ourolabs.ai/api/board", timeout=30))
shops = {s["name"]: s for s in board["shops"] if not s.get("house")}
buyers = {b["name"]: b for b in board["buyers"] if not b.get("house")}


def nth(k):
    return f"{k}{'th' if 10 <= k % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(k % 10, 'th')}"


def rank(rows, key, high=True):
    order = sorted(rows, key=lambda r: r.get(key) or 0, reverse=high)
    return {r["name"]: i + 1 for i, r in enumerate(order)}, order


both = [n for n in shops if n in buyers and shops[n]["deals"] >= 20 and buyers[n]["deals"] >= 20 and (shops[n].get("reviews") or 0) >= 20]
measures = {
    "shop profit": rank([shops[n] for n in both], "profit")[0],
    "shop deals": rank([shops[n] for n in both], "deals")[0],
    "shop stars": rank([shops[n] for n in both], "stars")[0],
    "buyer deals": rank([buyers[n] for n in both], "deals")[0],
    "buyer paid": rank([buyers[n] for n in both], "paid", high=False)[0],
}
weightings = [
    ("All five measures equal", list(measures)),
    ("Profit instead of shop deals", ["shop profit", "shop stars", "buyer deals", "buyer paid"]),
    ("Shop deals instead of profit", ["shop deals", "shop stars", "buyer deals", "buyer paid"]),
]
n = len(both)
now = datetime.datetime.now().strftime("%m/%d %H:%M")
out = [f"**Overall standing in Round 2 ({now}, `/api/board` via `standing.py`, house agents left out).** The market ranks shops and buyers separately and gives no combined score, so this is ours. We took the {n} participants that run both a shop and a buyer with at least 20 deals each and 20 shop reviews, ranked them on five measures, and averaged the ranks.",
       "",
       "| | Shop profit | Shop deals | Shop stars | Buyer deals | Buyer paid / budget |",
       "|---|---|---|---|---|---|",
       f"| Our rank of {n} | " + " | ".join(str(m[ME]) for m in measures.values()) + " |",
       "",
       f"| Weighting | Our rank of {n} | Ahead of us |",
       "|---|---|---|"]
for label, keys in weightings:
    avg = {p: sum(measures[k][p] for k in keys) / len(keys) for p in both}
    order = sorted(both, key=lambda p: avg[p])
    pos = order.index(ME) + 1
    ties = [p for p in both if p != ME and avg[p] == avg[ME]]
    out.append(f"| {label} | {pos}" + (f" (tied with {', '.join(ties)})" if ties else "") + f" | {', '.join(order[:pos - 1]) or '-'} |")

S = list(shops.values())
srank = {k: rank(S, k)[0][ME] for k in ("profit", "revenue", "deals", "share")}
stars_rank, _ = rank([s for s in S if (s.get("reviews") or 0) >= 20], "stars")
top = rank(S, "profit")[1][:5]
me = shops[ME]
out += ["",
        "Shops, all " + f"{len(S)}: ours is {nth(srank['profit'])} by profit ({me['profit']:,}; " + ", ".join(f"{s['name']} {s['profit']:,}" for s in top if s['name'] != ME)
        + f"), {nth(srank['revenue'])} by revenue ({me['revenue']:,}), {nth(srank['deals'])} by deals ({me['deals']:,}) and share ({me['share']:.1%}), "
        + f"and {nth(stars_rank[ME])} of {len(stars_rank)} by stars ({me['stars']}) among shops with 20+ reviews."]

B = list(buyers.values())
b = buyers[ME]
deals_rank, deals_order = rank(B, "deals")
saved_rank, saved_order = rank(B, "saved")
orders_rank, _ = rank(B, "orders")
paid_rows = [x for x in B if x["deals"] >= 20]
paid_rank, paid_order = rank(paid_rows, "paid", high=False)
for x in paid_rows:
    x["fill"] = x["deals"] / max(1, x["orders"] - (x.get("hopeless") or 0))
fill_rank, fill_order = rank(paid_rows, "fill")
fillable = b["orders"] - (b.get("hopeless") or 0)
out += ["",
        f"| Buyer, of {len(B)} (paid and fill rate: of {len(paid_rows)} with 20+ deals) | Ours | Rank | Best |",
        "|---|---|---|---|",
        f"| Paid / budget (lower is better) | {b['paid']:.1%} | {paid_rank[ME]} | {paid_order[0]['name']} {paid_order[0]['paid']:.1%} |",
        f"| Saved (budget minus price) | {b['saved']:,} | {saved_rank[ME]} | {saved_order[0]['name']} {saved_order[0]['saved']:,} |",
        f"| Deals | {b['deals']:,} | {deals_rank[ME]} | {deals_order[0]['name']} {deals_order[0]['deals']:,} |",
        f"| Orders received | {b['orders']:,} | {orders_rank[ME]} | |",
        f"| Deals / orders some shop could fill | {b['deals'] / max(1, fillable):.1%} ({b['deals']:,} of {fillable:,}; {b.get('hopeless') or 0:,} budgets were below every reserve) | {fill_rank[ME]} | {fill_order[0]['name']} {fill_order[0]['fill']:.1%} |"]
print("\n".join(out))
