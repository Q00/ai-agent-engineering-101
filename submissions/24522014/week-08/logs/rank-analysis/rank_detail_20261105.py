"""Read-only, no network: why the shop formula misses, 10:54 to 12:55 KST, and gaps to ranks 20, 15, 10 and 3 from the saved board.

What-if figures hold rivals still and are arithmetic, not forecasts.
"""
import glob
import itertools
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import rank_analysis as ra

KST = timezone(timedelta(hours=9))


def kst(t):
    return datetime.fromtimestamp(t, KST).strftime("%m-%d %H:%M:%S")


board_path = sorted(glob.glob(str(ROOT / "logs/rank-analysis/board-2026*.json")))[-1]
board = json.loads(Path(board_path).read_text(encoding="utf-8"))
old = json.loads((ROOT / "logs/star-audit/20261105/board.json").read_text(encoding="utf-8"))
print("board", Path(board_path).name)
players = sorted(board["players"], key=lambda p: -p["total"])
own = next(p for p in players if ra.login_of(p) == ra.LOGIN)
shops, buyers = ra.eligible(board, "shops"), ra.eligible(board, "buyers")
shop, buyer = ra.rows_for(board, own)

print("\n== shop formula: which fields and pool fit the official ALL")
all_shops = board["shops"]
print("rows", len(all_shops), "with all", len(shops), "house", sum(bool(r.get("house")) for r in all_shops), "keys", sorted(shops[0].keys()))
pools = {"eligible": shops, "not_house": [r for r in shops if not r.get("house")], "with_deals": [r for r in shops if r.get("deals")], "all_rows": all_shops}
for label, pool in pools.items():
    for profit_key, star_key in itertools.product(("profit", "book"), ("stars_rank", "stars")):
        weights = {profit_key: .5, "revenue": .3, star_key: .2}
        errors = [abs(ra.computed_all(r, pool, weights) - r["all"]) for r in pool if r.get("all") is not None]
        print(f"  {label:10} {profit_key:7} {star_key:10} mean {sum(errors)/len(errors):.5f} max {max(errors):.5f}  own {ra.computed_all(shop, pool, weights):.4f}")
print("  official own", shop["all"])
worst = sorted(shops, key=lambda r: -abs(ra.computed_all(r, shops, ra.SHOP_WEIGHTS) - r["all"]))[:6]
for r in worst:
    print("  worst", round(ra.computed_all(r, shops, ra.SHOP_WEIGHTS), 3), r["all"], {k: r.get(k) for k in ("deals", "profit", "stock_cost", "reviews", "stars", "stars_rank", "bankruptcies", "alive", "house")})

ra.SHOP_WEIGHTS = {"book": .5, "revenue": .3, "stars_rank": .2}  # The fields that fit the official ALL above.
print("\n== board at 10:39 (saved) against now: our row and what moved, full boards")
old_players = {ra.login_of(p): p for p in old["players"]}
old_own = old_players[ra.LOGIN]
print("old total", old_own["total"], old_own["shop"]["all"], old_own["buyer"]["all"], "rank", 1 + sum(p["total"] > old_own["total"] for p in old["players"]))
print("new total", own["total"], own["shop"]["all"], own["buyer"]["all"], "rank", 1 + sum(p["total"] > own["total"] for p in players))
old_shops, old_buyers = ra.eligible(old, "shops"), ra.eligible(old, "buyers")
old_shop, old_buyer = ra.rows_for(old, old_own)
for label, a_row, a_rows, b_row, b_rows, weights in (("buyer", old_buyer, old_buyers, buyer, buyers, ra.BUYER_WEIGHTS), ("shop", old_shop, old_shops, shop, shops, ra.SHOP_WEIGHTS)):
    print(label, "players", len(a_rows), "->", len(b_rows))
    for key, weight in weights.items():
        a_values = [ra.number(r.get(key)) for r in a_rows]
        b_values = [ra.number(r.get(key)) for r in b_rows]
        a_share = ra.percentile(ra.number(a_row.get(key)), a_values)
        b_share = ra.percentile(ra.number(b_row.get(key)), b_values)
        # Our new value against the old field shows our own move; the rest is the field's move.
        own_move = ra.percentile(ra.number(b_row.get(key)), [v for v in a_values if v != ra.number(a_row.get(key))] + [ra.number(b_row.get(key))]) - a_share
        print(f"  {key:10} {a_row.get(key)} -> {b_row.get(key)}  pct {a_share:.4f} -> {b_share:.4f}  points {weight*(b_share-a_share):+.4f}  own move {weight*own_move:+.4f}  field move {weight*(b_share-a_share-own_move):+.4f}")

print("\n== 10:54 to 12:55 KST from saved snapshots (percentiles reconstructed)")
points = ra.history(ROOT)
day = datetime(2026, 11, 5, tzinfo=KST)
a = ra.nearest(points, (day + timedelta(hours=10, minutes=54)).timestamp())
b = ra.nearest(points, (day + timedelta(hours=12, minutes=55)).timestamp())
print("snapshots", kst(a["observed_at"]), a["total"], kst(b["observed_at"]), b["total"])
window = [p for p in points if a["observed_at"] - 3600 <= p["observed_at"] <= b["observed_at"] + 3600]
for p in window:
    print("  ", kst(p["observed_at"]), "rank", p["rank"], "total", p["total"], "shop", p["shop_all"], "buyer", p["buyer_all"], p["values"])
print(json.dumps(ra.decompose(a, b), ensure_ascii=False, indent=1))

print("\n== leader-monitor reports in the same window: whole field")
reports = []
for path in sorted(glob.glob(str(ROOT / "logs/leader-monitor/*.json"))):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("observed_at") and a["observed_at"] - 1800 <= data["observed_at"] <= b["observed_at"] + 1800:
        reports.append(data)
for data in reports:
    me = data.get("own") or {}
    print("  ", kst(data["observed_at"]), {k: (me.get(k) if not isinstance(me.get(k), dict) else None) for k in ("total", "rank")}, "shop", {k: (me.get("shop") or {}).get(k) for k in ("profit", "revenue", "stars", "deals", "all")}, "buyer", {k: (me.get("buyer") or {}).get(k) for k in ("surplus", "deals", "posts", "forum", "all")})

print("\n== gaps to ranks")
for target in (25, 20, 15, 10, 5, 3):
    rival = players[target - 1]
    gap = rival["total"] - own["total"]
    print(f"rank {target}: total {rival['total']} shop {rival['shop']['all']} buyer {rival['buyer']['all']} gap {gap:.3f}")
own_total = own["total"]
print("\n== buyer surplus and deals needed, with rivals still, other values ours; deals move at our surplus per deal")
per_deal = buyer["surplus"] / buyer["deals"]
for factor in (1.25, 1.5, 2, 2.5, 3, 4, 5, 7):
    surplus, deals = buyer["surplus"] * factor, round(buyer["deals"] * factor)
    gain = ra.what_if(buyer, buyers, ra.BUYER_WEIGHTS, "surplus", surplus) + ra.what_if(buyer, buyers, ra.BUYER_WEIGHTS, "deals", deals)
    print(f"  x{factor}: deals {deals} surplus {surplus:,.0f} points {gain:+.4f} rank {ra.rank_at(players, own_total + gain)}")
print("== shop book and revenue together")
for factor in (1.25, 1.5, 2, 3, 5):
    gain = ra.what_if(shop, shops, ra.SHOP_WEIGHTS, "book", shop["book"] * factor) + ra.what_if(shop, shops, ra.SHOP_WEIGHTS, "revenue", shop["revenue"] * factor)
    print(f"  x{factor}: book {shop['book']*factor:,.0f} revenue {shop['revenue']*factor:,.0f} points {gain:+.4f} rank {ra.rank_at(players, own_total + gain)}")
print("== buyer forum if it were the plain mean of post scores and new posts keep scoring .487, rivals still (a hypothesis, tested below)")
for added in (0, 5, 10, 20, 30, 60, 100):
    value = (buyer["forum"] * buyer["posts"] + .487 * added) / (buyer["posts"] + added)
    print(f"  {added} more posts: forum {value:.3f} points {ra.what_if(buyer, buyers, ra.BUYER_WEIGHTS, 'forum', value):+.4f}")
print("== test of that mean: predicted next value from the snapshots, posts 15 to 21")
for posts, before, after in ((15, .636, .624), (16, .627, .621), (17, .621, .616), (18, .616, .609), (19, .609, .603), (20, .603, .597)):
    print(f"  {posts} posts at {before}: predicted {(before * posts + .487) / (posts + 1):.3f} observed {after}")
print("== stars_rank alone")
for stars in (4.4, 4.45, 4.5, 4.55, 4.6, 4.65, 4.7):
    gain = ra.what_if(shop, shops, ra.SHOP_WEIGHTS, "stars_rank", stars)
    print(f"  {stars}: points {gain:+.4f} rank {ra.rank_at(players, own_total + gain)}")
print("== pace per hour, from 10:39 board to now, field medians and ours")
span = (board.get("generated_at") or 0)
hours = None
own_hours = (datetime.strptime(Path(board_path).stem[6:], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc) - datetime(2026, 11, 5, 1, 39, tzinfo=timezone.utc)).total_seconds() / 3600
print("  hours", round(own_hours, 2))
from statistics import median
for group, keys, cur, past in (("buyers", ("surplus", "deals", "orders", "posts"), buyers, old_buyers), ("shops", ("book", "profit", "revenue", "deals", "visits"), shops, old_shops)):
    past_by_name = {r["name"]: r for r in past}
    mine = buyer if group == "buyers" else shop
    for key in keys:
        rates = [(ra.number(r.get(key)) - ra.number(past_by_name[r["name"]].get(key))) / own_hours for r in cur if r["name"] in past_by_name]
        my_rate = (ra.number(mine.get(key)) - ra.number(past_by_name[mine["name"]].get(key))) / own_hours
        better = sum(v > my_rate for v in rates)
        print(f"  {group}.{key}: ours {my_rate:,.1f} median {median(rates):,.1f} p90 {sorted(rates)[int(len(rates)*.9)]:,.1f} rank {better+1}/{len(rates)}")
print("own refunds", shop.get("refunds"), "broken", shop.get("broken"), "claims", shop.get("claims"), "honest", shop.get("honest_claims"))
