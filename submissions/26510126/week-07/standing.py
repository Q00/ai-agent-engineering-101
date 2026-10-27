"""Our Round-2 standing from /api/board, as the Markdown block in REPORT.md section 3.
The market ranks shops and buyers separately; the combined rank is ours: the participants that run both
with at least MIN deals each and MIN shop reviews, ranked on five measures, ranks averaged. Shops the board marks as house are left out; it marks no buyer as house.
MIN is computed at run time from the round's own spread, so an average over MIN deals is tight enough to rank."""
import datetime, json, math, statistics, sys, urllib.request

import market_intel as mi

ME = "thisjonghoon"
Z = 1.96            # 95% confidence
STAR_MARGIN = 0.1   # the board shows stars to one decimal
PAID_MARGIN = 0.01  # one percentage point of budget

board = json.load(urllib.request.urlopen("https://market.ourolabs.ai/api/board", timeout=30))
shops = {s["name"]: s for s in board["shops"] if not s.get("house")}
buyers = {b["name"]: b for b in board["buyers"] if not b.get("house")}

# the spread of one review's stars and of one deal's price/budget over every verified Round-2 deal
entries = mi.load_ledger(timeout=60)
budget = {x["data"]["order"]: x["data"]["budget"] for x in entries if x["kind"] == "order" and x.get("round") == board["round"]}
deals = [x["data"] for x in entries if x["kind"] == "result" and x.get("round") == board["round"]
         and x["data"].get("verified") and x["data"].get("receipt") and isinstance(x["data"].get("stars"), (int, float))]
star_sd = statistics.pstdev(d["stars"] for d in deals)
paid_sd = statistics.pstdev(d["receipt"]["price"] / budget[d["order"]] for d in deals if budget.get(d["order"]))
n_star = math.ceil((Z * star_sd / STAR_MARGIN) ** 2)
n_paid = math.ceil((Z * paid_sd / PAID_MARGIN) ** 2)
MIN = max(n_star, n_paid)
print(f"[standing] {len(deals):,} verified deals: star sd {star_sd:.3f} -> {n_star}, paid sd {paid_sd:.4f} -> {n_paid}; MIN {MIN}", file=sys.stderr)


def nth(k):
    return f"{k}{'th' if 10 <= k % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(k % 10, 'th')}"


def rank(rows, key, high=True):
    order = sorted(rows, key=lambda r: r.get(key) or 0, reverse=high)
    return {r["name"]: i + 1 for i, r in enumerate(order)}, order


def pool_of(t):
    return [p for p in shops if p in buyers and shops[p]["deals"] >= t and buyers[p]["deals"] >= t and (shops[p].get("reviews") or 0) >= t]


def measures_of(pool):
    return {
        "shop profit": rank([shops[p] for p in pool], "profit")[0],
        "shop deals": rank([shops[p] for p in pool], "deals")[0],
        "shop stars": rank([shops[p] for p in pool], "stars")[0],
        "buyer deals": rank([buyers[p] for p in pool], "deals")[0],
        "buyer paid": rank([buyers[p] for p in pool], "paid", high=False)[0],
    }


both = pool_of(MIN)
measures = measures_of(both)
weightings = [
    ("All five measures equal", list(measures)),
    ("Profit instead of shop deals", ["shop profit", "shop stars", "buyer deals", "buyer paid"]),
    ("Shop deals instead of profit", ["shop deals", "shop stars", "buyer deals", "buyer paid"]),
]
n = len(both)
active = len([p for p in shops if p in buyers])
now = datetime.datetime.now().strftime("%m/%d %H:%M")
out = [f"**Overall standing in Round 2 ({now}, `/api/board` and the ledger via `standing.py`, the shops the board marks as house left out; the board marks no buyer as house, so every buyer counts (the week-08 lecture mentions house buyers, which the board does not separate)).** The market ranks shops and buyers separately and gives no combined score, so this is ours. Of the {active} participants that registered both a shop and a buyer, we took the {n} with at least {MIN} deals each and {MIN} shop reviews, ranked them on five measures, and averaged the ranks.",
       "",
       f"Why {MIN}: two of the measures are averages, and an average over few deals is mostly luck. Over the round's {len(deals):,} verified deals one review's stars spread with a standard deviation of {star_sd:.3f} and one deal's price/budget with {paid_sd:.4f}. For a 95% interval of ±{STAR_MARGIN} star (the board's one decimal) a participant needs {n_star} reviews, and for ±{PAID_MARGIN:.0%} of budget {n_paid} deals; {MIN} meets both. It is recomputed from the ledger each time this block is regenerated.",
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


def combined_rank(t):
    pool = pool_of(t)
    ms = measures_of(pool).values()
    avg = {p: sum(m[p] for m in ms) / len(ms) for p in pool}
    return sorted(pool, key=lambda p: avg[p]).index(ME) + 1, len(pool)


sens = {t: combined_rank(t) for t in sorted({1, 20, 100, MIN, 500})}
ranks = {r for r, _ in sens.values()}
out += ["",
        "With equal weights, a minimum of " + ", ".join(f"{t} gives rank {r} of {k}" for t, (r, k) in sens.items())
        + (", so the result does not depend on the cut." if len(ranks) == 1 else ".")]

S = list(shops.values())
srank = {k: rank(S, k)[0][ME] for k in ("profit", "revenue", "deals", "share")}
stars_rank, _ = rank([s for s in S if (s.get("reviews") or 0) >= n_star], "stars")
top = rank(S, "profit")[1][:5]
me = shops[ME]
out += ["",
        "Shops, all " + f"{len(S)}: ours is {nth(srank['profit'])} by profit ({me['profit']:,}; " + ", ".join(f"{s['name']} {s['profit']:,}" for s in top if s['name'] != ME)
        + f"), {nth(srank['revenue'])} by revenue ({me['revenue']:,}), {nth(srank['deals'])} by deals ({me['deals']:,}) and share ({me['share']:.1%}), "
        + f"and {nth(stars_rank[ME])} of {len(stars_rank)} by stars ({me['stars']}) among shops with {n_star}+ reviews."]

B = list(buyers.values())
b = buyers[ME]
deals_rank, deals_order = rank(B, "deals")
saved_rank, saved_order = rank(B, "saved")
orders_rank, _ = rank(B, "orders")
paid_rows = [x for x in B if x["deals"] >= n_paid]
paid_rank, paid_order = rank(paid_rows, "paid", high=False)
for x in paid_rows:
    x["fill"] = x["deals"] / max(1, x["orders"] - (x.get("hopeless") or 0))
fill_rank, fill_order = rank(paid_rows, "fill")
fillable = b["orders"] - (b.get("hopeless") or 0)
out += ["",
        f"| Buyer, of {len(B)} (paid and fill rate: of {len(paid_rows)} with {n_paid}+ deals) | Ours | Rank | Best |",
        "|---|---|---|---|",
        f"| Paid / budget (lower is better) | {b['paid']:.1%} | {paid_rank[ME]} | {paid_order[0]['name']} {paid_order[0]['paid']:.1%} |",
        f"| Saved (budget minus price) | {b['saved']:,} | {saved_rank[ME]} | {saved_order[0]['name']} {saved_order[0]['saved']:,} |",
        f"| Deals | {b['deals']:,} | {deals_rank[ME]} | {deals_order[0]['name']} {deals_order[0]['deals']:,} |",
        f"| Orders received | {b['orders']:,} | {orders_rank[ME]} | |",
        f"| Deals / orders some shop could fill | {b['deals'] / max(1, fillable):.1%} ({b['deals']:,} of {fillable:,}; {b.get('hopeless') or 0:,} budgets were below every reserve) | {fill_rank[ME]} | {fill_order[0]['name']} {fill_order[0]['fill']:.1%} |"]
print("\n".join(out))
