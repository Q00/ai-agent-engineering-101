"""Read-only: where the Players score is won and lost, from saved public data. Makes no network call.

Sources are kept apart. `board` is one saved official /api/board. `history` is our saved market-intel snapshots: official ALL
scores and our own values, with percentiles reconstructed from strict-rank fields, so they carry a stated residual.
What-if figures hold every rival still at the saved board, so they are arithmetic, not forecasts.
"""

import argparse
import glob
import json
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent
LOGIN = "kcwarni"
SHOP_WEIGHTS = {"profit": .5, "revenue": .3, "stars_rank": .2}
BUYER_WEIGHTS = {"surplus": .5, "forum": .3, "deals": .1, "posts": .1}
KST_SESSION_START = 1793785140.0  # 2026-11-04 18:39 KST, when the balanced strategy started
EPS = 1e-9
SAVED_WEIGHTS = {"saved": .5, "forum": .3, "deals": .1, "posts": .1}
BOOK_WEIGHTS = {"book": .5, "revenue": .3, "stars_rank": .2}


class Weights(dict):
    """Weights whose `raw` fields count as their own value instead of a percentile."""

    def __init__(self, values, raw=()):
        super().__init__(values)
        self.raw = frozenset(raw)


# Patch 3.5 (lecture notes 11-06 15:53 KST): the forum rating counts as itself, the rest stay percentiles.
# The official buyer ALL in our snapshots fits the old rule at 17:53:31 KST and the new one from 18:23:33.
FORUM_VALUE_WEIGHTS = Weights(SAVED_WEIGHTS, raw=("forum",))
FORUM_VALUE_SINCE = 1793957013.0  # 2026-11-06 18:23:33 KST, the first snapshot that fits patch 3.5


def login_of(row):
    owner = row.get("owner")
    return owner.get("login") if isinstance(owner, dict) else owner or row.get("player")


def number(value):
    return value if type(value) in (int, float) else 0


def percentile(value, values):
    """Official rule as reproduced from the board: ties share the mean rank, scaled by n-1, missing counts as 0."""
    below = sum(v < value for v in values)
    tied = sum(v == value for v in values)
    return (below + .5 * (tied - 1)) / max(len(values) - 1, 1)


def scored(weights, key, value, values):
    """What one part adds before its weight: the value itself for a raw field, else its percentile."""
    return value if key in getattr(weights, "raw", ()) else percentile(value, values)


def eligible(board, group):
    return [r for r in board[group] if r.get("all") is not None]


def computed_all(row, rows, weights):
    return sum(w * scored(weights, k, number(row.get(k)), [number(r.get(k)) for r in rows]) for k, w in weights.items())


def max_error(rows, weights):
    return max((abs(computed_all(r, rows, weights) - r["all"]) for r in rows), default=0)


def shop_weights(board):
    """A board with a book field scores shops on it (saved boards fit to .0005); the 11-04 board had no such field and scored profit."""
    return BOOK_WEIGHTS if any(r.get("book") is not None for r in board.get("shops", [])) else SHOP_WEIGHTS


def buyer_weights(board):
    """Since patch 3.4 `saved` is a share of list price and buyers score on it. Before it, `saved` was an amount and surplus scored.
    Patch 3.5 keeps saved and counts the forum rating as itself. A board carries no time, so one with official scores gets the rule
    that reproduces them (saved boards fit one rule to .0005 and the other by .17 or worse); one without gets the rule in force now."""
    saved = [r["saved"] for r in board.get("buyers", []) if type(r.get("saved")) in (int, float)]
    if not (saved and all(abs(v) <= 2 for v in saved)):
        return BUYER_WEIGHTS
    rows = eligible(board, "buyers")
    if not rows:
        return FORUM_VALUE_WEIGHTS
    return min((FORUM_VALUE_WEIGHTS, SAVED_WEIGHTS), key=lambda weights: max_error(rows, weights))


def formula_check(board):
    report = {}
    for group, weights in (("shops", shop_weights(board)), ("buyers", buyer_weights(board))):
        rows = eligible(board, group)
        errors = [abs(computed_all(r, rows, weights) - r["all"]) for r in rows]
        report[group] = {"players": len(rows), "mean_error": round(sum(errors) / len(errors), 5) if errors else None, "max_error": round(max(errors), 5) if errors else None}
        if group == "buyers":
            report[group]["rule"] = "forum_value" if weights is FORUM_VALUE_WEIGHTS else "forum_percentile" if weights is SAVED_WEIGHTS else "surplus"
    return report


def rows_for(board, player):
    """A player's shop and buyer rows, by the names the Players table gives."""
    shop = next((r for r in eligible(board, "shops") if r.get("name") == (player.get("shop") or {}).get("name")), None)
    buyer = next((r for r in eligible(board, "buyers") if r.get("name") == (player.get("buyer") or {}).get("name")), None)
    return shop, buyer


def breakdown(row, rows, weights):
    result = {}
    for key, weight in weights.items():
        values = [number(r.get(key)) for r in rows]
        share = scored(weights, key, number(row.get(key)), values)
        result[key] = {"value": row.get(key), "percentile": round(share, 4), "weight": weight, "scored_as": "value" if key in getattr(weights, "raw", ()) else "percentile",
                       "points": round(weight * share, 4), "points_left": round(weight * (1 - share), 4),
                       "rank": 1 + sum(v > number(row.get(key)) for v in values), "of": len(values)}
    return result


def what_if(row, rows, weights, key, value):
    """Points gained if only this value moved, every rival held still."""
    others = [number(r.get(key)) for r in rows if r is not row]
    before = scored(weights, key, number(row.get(key)), others + [number(row.get(key))])
    after = scored(weights, key, value, others + [value])
    return weights[key] * (after - before)


def value_for_gain(row, rows, weights, key, gain):
    """Smallest rival value to reach to gain this many points, holding rivals still; None if out of reach."""
    mine = number(row.get(key))
    if key in getattr(weights, "raw", ()):
        # A rating that counts as itself needs no rival to pass; a forum rating cannot exceed 1.
        target = round(mine + gain / weights[key], 4)
        return target if target <= 1 else None
    for target in sorted({number(r.get(key)) for r in rows if number(r.get(key)) > mine}):
        if what_if(row, rows, weights, key, target) >= gain - EPS:
            return target
    return None


def distribution(values):
    ordered = sorted(values)
    def at(share):
        return ordered[min(len(ordered) - 1, int(len(ordered) * share))]
    return {"min": ordered[0], "p25": at(.25), "median": median(ordered), "p75": at(.75), "p90": at(.9), "max": ordered[-1],
            "distinct": len(set(ordered)), "largest_tie": max(ordered.count(v) for v in set(ordered)),
            "zero_or_missing": sum(v == 0 for v in ordered)}


def sensitivity(row, rows, weights, step=.05):
    """How far each value must move for the same points, and what passing the next rivals is worth."""
    table = {}
    for key in weights:
        values = [number(r.get(key)) for r in rows]
        mine = number(row.get(key))
        target = value_for_gain(row, rows, weights, key, step)
        ladder = sorted({v for v in values if v > mine})
        table[key] = {"distribution": distribution(values), "points_per_rank": None if key in getattr(weights, "raw", ()) else round(weights[key] / (len(values) - 1), 4),
                      f"value_for_plus_{step}": target,
                      f"increase_for_plus_{step}": round(target - mine, 4) if target is not None else None,
                      f"ratio_for_plus_{step}": round(target / mine, 3) if target is not None and mine else None,
                      "next_distinct_values": ladder[:5]}
    return table


def compare(board, own_player, rival):
    shop_scored = shop_weights(board)
    shop, buyer = rows_for(board, own_player)
    rival_shop, rival_buyer = rows_for(board, rival)
    shops, buyers = eligible(board, "shops"), eligible(board, "buyers")
    gaps = {}
    for label, mine, theirs, rows, weights in (("shop", shop, rival_shop, shops, shop_scored), ("buyer", buyer, rival_buyer, buyers, buyer_weights(board))):
        for key, weight in weights.items():
            values = [number(r.get(key)) for r in rows]
            own_share, their_share = scored(weights, key, number(mine.get(key)), values), scored(weights, key, number(theirs.get(key)), values)
            gaps[f"{label}.{key}"] = {"own_value": mine.get(key), "rival_value": theirs.get(key),
                                      "own_percentile": round(own_share, 3), "rival_percentile": round(their_share, 3),
                                      "points_gap": round(weight * (their_share - own_share), 4),
                                      "points_if_matched": round(what_if(mine, rows, weights, key, number(theirs.get(key))), 4)}
    return {"player": rival.get("player"), "total": rival["total"], "shop_all": rival["shop"]["all"], "buyer_all": rival["buyer"]["all"],
            "total_gap": round(rival["total"] - own_player["total"], 4), "components": gaps}


def _ratio(top, bottom):
    return round(top / bottom, 4) if type(top) in (int, float) and type(bottom) in (int, float) and bottom else None


def _buyer_ratios(row):
    return {"orders": row.get("orders"), "deals": row.get("deals"), "deals_per_order": _ratio(row.get("deals"), row.get("orders")),
            "saved": row.get("saved"), "surplus": row.get("surplus"), "surplus_per_deal": _ratio(row.get("surplus"), row.get("deals")),
            "posts": row.get("posts"), "forum": row.get("forum")}


def _shop_ratios(row):
    return {"visits": row.get("visits"), "deals": row.get("deals"), "deals_per_visit": _ratio(row.get("deals"), row.get("visits")),
            "revenue_per_deal": _ratio(row.get("revenue"), row.get("deals")), "profit_per_deal": _ratio(row.get("profit"), row.get("deals")),
            "margin": _ratio(row.get("profit"), row.get("revenue")), "stars": row.get("stars"), "stars_rank": row.get("stars_rank"),
            "refunds": row.get("refunds"), "broken": row.get("broken"), "broken_per_deal": _ratio(row.get("broken"), row.get("deals"))}


def conversion(board, own_player, rivals):
    """Official counts split into volume and value per deal, to show which one a score gap comes from."""
    buyers, shops = eligible(board, "buyers"), eligible(board, "shops")

    def named(rows, name):
        return next((r for r in rows if r.get("name") == name), {})

    def medians(rows, ratios):
        table = [ratios(r) for r in rows if r.get("deals")]
        return {key: median(v[key] for v in table if type(v[key]) in (int, float))
                for key in table[0] if any(type(v[key]) in (int, float) for v in table)}
    def pair(player):
        return {"buyer": _buyer_ratios(named(buyers, (player.get("buyer") or {}).get("name"))),
                "shop": _shop_ratios(named(shops, (player.get("shop") or {}).get("name")))}
    return {"own": pair(own_player), "rivals": {p["player"]: pair(p) for p in rivals},
            "market_median": {"buyer": medians(buyers, _buyer_ratios), "shop": medians(shops, _shop_ratios)}}


def scenarios(board, own_player, third):
    """Points if deals per order changed and value per deal stayed ours. Arithmetic with rivals held still. Stock held stays in book."""
    weights = shop_weights(board)
    scored = next(iter(weights))
    shop, buyer = rows_for(board, own_player)
    stock = shop[scored] - shop["profit"]
    buyers, shops = eligible(board, "buyers"), eligible(board, "shops")
    orders, per_deal = buyer["orders"], buyer["surplus"] / buyer["deals"]
    table = {}
    medians = median(r["deals"] / r["orders"] for r in buyers if r.get("deals") and r.get("orders"))
    _, third_buyer = rows_for(board, third)
    for label, rate in (("market_median", medians), ("third_place", third_buyer["deals"] / third_buyer["orders"])):
        deals = round(orders * rate)
        surplus = deals * per_deal
        buyer_scored = buyer_weights(board)
        row = {"deals_per_order": round(rate, 4), "deals": deals, "surplus": round(surplus)}
        if "saved" in buyer_scored:
            # Saved = sum of shares / (deals + 100): more deals at the same share also move it.
            share = buyer["saved"] * (buyer["deals"] + 100) / buyer["deals"]
            row["saved"] = round(share * deals / (deals + 100), 4)
            points = what_if(buyer, buyers, buyer_scored, "saved", row["saved"]) + what_if(buyer, buyers, buyer_scored, "deals", deals)
        else:
            points = what_if(buyer, buyers, buyer_scored, "surplus", surplus) + what_if(buyer, buyers, buyer_scored, "deals", deals)
        table[f"buyer_conversion_{label}"] = {**row, "points": round(points, 4)}
    for posts in (27, 83):
        table[f"buyer_posts_{posts}"] = {"points": round(what_if(buyer, buyers, buyer_weights(board), "posts", posts), 4)}
    visits_median = median(r["visits"] for r in shops if r.get("deals"))
    rate, revenue_per, profit_per = shop["deals"] / shop["visits"], shop["revenue"] / shop["deals"], shop["profit"] / shop["deals"]
    deals = round(visits_median * rate)
    table["shop_visits_market_median_same_rate_and_margin"] = {
        "visits": visits_median, "deals": deals, "revenue": round(deals * revenue_per), "profit": round(deals * profit_per),
        "points": round(what_if(shop, shops, weights, scored, deals * profit_per + stock)
                        + what_if(shop, shops, weights, "revenue", deals * revenue_per), 4)}
    half_margin_profit = deals * revenue_per * (shop["profit"] / shop["revenue"]) / 2
    table["shop_visits_market_median_half_margin"] = {
        "deals": deals, "profit": round(half_margin_profit),
        "points": round(what_if(shop, shops, weights, scored, half_margin_profit + stock)
                        + what_if(shop, shops, weights, "revenue", deals * revenue_per), 4),
        "assumption": "revenue per deal unchanged although margin halves, so revenue is if anything overstated"}
    for stars in (4.62, 4.68):
        table[f"shop_stars_rank_{stars}"] = {"points": round(what_if(shop, shops, weights, "stars_rank", stars), 4)}
    return table


def spearman(rows, first, second):
    pairs = [(first(r), second(r)) for r in rows]
    pairs = [(a, b) for a, b in pairs if type(a) in (int, float) and type(b) in (int, float)]
    n = len(pairs)
    if n < 3:
        return None

    def ranks(values):
        return [sum(other < value for other in values) + .5 * (values.count(value) - 1) for value in values]
    left, right = ranks([a for a, _ in pairs]), ranks([b for _, b in pairs])
    mean = (n - 1) / 2
    top = sum((a - mean) * (b - mean) for a, b in zip(left, right))
    bottom = (sum((a - mean) ** 2 for a in left) * sum((b - mean) ** 2 for b in right)) ** .5
    return round(top / bottom, 3) if bottom else None


def visit_correlates(board):
    """Rank correlation with official shop visits. Association only: visits also lift deals and reviews by themselves."""
    rows = [r for r in eligible(board, "shops") if r.get("deals")]
    visits = lambda r: r.get("visits")
    return {"shops": len(rows),
            "visits_vs_margin": spearman(rows, visits, lambda r: _ratio(r.get("profit"), r.get("revenue"))),
            "visits_vs_stars_rank": spearman(rows, visits, lambda r: r.get("stars_rank")),
            "visits_vs_revenue_per_deal": spearman(rows, visits, lambda r: _ratio(r.get("revenue"), r.get("deals"))),
            "visits_vs_broken_per_deal": spearman(rows, visits, lambda r: _ratio(r.get("broken"), r.get("deals")))}


def rank_at(players, total):
    return 1 + sum(p["total"] > total for p in players)


def listing_breadth(root, board):
    """Public offers of other shops beside official visits; our own shop is not in this list. Not a cause."""
    path = Path(root) / ".market/shops.json"
    if not path.exists():
        return None
    registry = json.loads(path.read_text(encoding="utf-8"))
    registry = registry.get("shops", registry) if isinstance(registry, dict) else registry
    visits = {r.get("name"): r.get("visits") for r in eligible(board, "shops")}
    rows = []
    for shop in registry:
        offers = shop.get("offers") or []
        if shop.get("name") in visits:
            rows.append({"name": shop["name"], "skus": len(offers), "units": sum(o.get("qty", 0) for o in offers), "visits": visits[shop["name"]]})
    return {"shops": len(rows), "median_skus": median(r["skus"] for r in rows), "median_units": median(r["units"] for r in rows),
            "p90_units": sorted(r["units"] for r in rows)[int(len(rows) * .9)],
            "spearman_units_vs_visits": spearman(rows, lambda r: r["units"], lambda r: r["visits"]),
            "spearman_skus_vs_visits": spearman(rows, lambda r: r["skus"], lambda r: r["visits"])}


def attempts_today(root, now=None):
    path = Path(root) / ".market/buyer-support-state.json"
    if not path.exists():
        return None
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    state = json.loads(path.read_text(encoding="utf-8"))
    day = datetime.fromtimestamp(now, timezone.utc).date()
    rows = [r["at"] for r in state.get("attempts", []) if datetime.fromtimestamp(r["at"], timezone.utc).date() == day]
    recent = [t for t in rows if now - t <= 3600]
    return {"utc_day": str(day), "attempts": len(rows), "last_hour": len(recent),
            "hours_to_480_at_last_hour_rate": round((480 - len(rows)) / len(recent), 1) if recent else None}


def snapshot_points(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    competition = data.get("competition") or {}
    official = competition.get("official_player") or {}
    if data.get("round") != 3 or official.get("total") is None:
        return None
    own = competition.get("own") or {}
    shop, buyer = own.get("shops") or {}, own.get("buyers") or {}
    point = {"observed_at": data.get("observed_at"), "rank": competition.get("rank"), "players": competition.get("player_count"),
             "total": official.get("total"), "shop_all": (official.get("shop") or {}).get("all"), "buyer_all": (official.get("buyer") or {}).get("all"),
             "values": {"profit": shop.get("profit"), "revenue": shop.get("revenue"), "stars": shop.get("stars"), "surplus": buyer.get("surplus"), "saved": buyer.get("saved"),
                        "forum": buyer.get("forum"), "deals": buyer.get("deals"), "posts": buyer.get("posts")}, "components": {}}
    parts = competition.get("diagnostic_components") or {}
    buyers_n = (parts.get("buyers.saved") or parts.get("buyers.surplus") or {}).get("observed_participants")
    shops_n = (parts.get("shops.book") or parts.get("shops.profit") or {}).get("observed_participants")
    for name, part in parts.items():
        group_n = buyers_n if name.startswith("buyers") else shops_n
        seen = part.get("observed_participants")
        if not group_n or seen is None or part.get("ahead_of_percent") is None:
            continue
        below = part["ahead_of_percent"] * seen / 100 + (group_n - seen)
        share = (below + .5 * ((part.get("tied") or 1) - 1)) / max(group_n - 1, 1)
        # Older snapshots carry no scored_as; on a saved-share board the forum rating counts as itself from the switch on.
        as_value = part.get("scored_as") == "value" or (part.get("scored_as") is None and name == "buyers.forum" and "buyers.saved" in parts
                                                         and (data.get("observed_at") or 0) >= FORUM_VALUE_SINCE and type(part.get("value")) in (int, float))
        point["components"][name] = {"weight": part.get("weight"), "percentile": round(part["value"] if as_value else share, 4), "below": round(below, 1), "n": group_n,
                                     "value": part.get("value"), "scored_as": "value" if as_value else "percentile"}
    return point


def history(root):
    points = [snapshot_points(path) for path in sorted(glob.glob(str(Path(root) / "logs/market-intel/*.json")))]
    return [p for p in points if p]


def nearest(points, moment):
    return min(points, key=lambda p: abs(p["observed_at"] - moment))


def decompose(first, last):
    """Weighted percentile change per component. Component ALLs are reconstructed; the residual shows how far off."""
    rows, shop_sum, buyer_sum = {}, 0, 0
    # Snapshots keep each component with its weight, so old and new formulas both add up; a component changing name between the two is skipped.
    for name, weight in {**{f"shops.{k}": w for k, w in {"profit": .5, "book": .5, "revenue": .3, "stars": .2, "stars_rank": .2}.items()},
                         **{f"buyers.{k}": w for k, w in {**BUYER_WEIGHTS, **SAVED_WEIGHTS}.items()}}.items():
        a, b = first["components"].get(name), last["components"].get(name)
        if not a or not b:
            continue
        if a.get("scored_as", "percentile") != b.get("scored_as", "percentile"):
            # A part scored as a percentile on one side and as itself on the other cannot be differenced; the residual carries it.
            rows[name] = {"points": None, "scored_as": [a.get("scored_as"), b.get("scored_as")], "value_from": a["value"], "value_to": b["value"], "driver": "scoring_rule_changed"}
            continue
        points = weight * (b["percentile"] - a["percentile"])
        value_change = None
        if type(a["value"]) in (int, float) and type(b["value"]) in (int, float):
            value_change = b["value"] - a["value"]
        driver = ("no_percentile_change" if abs(points) < .0005 else
                  "own_value_fell" if value_change is not None and value_change < 0 and points < 0 else
                  "rivals_moved" if points < 0 else "own_or_rivals_up")
        rows[name] = {"points": round(points, 4), "percentile_from": a["percentile"], "percentile_to": b["percentile"],
                      "value_from": a["value"], "value_to": b["value"], "value_change": value_change,
                      "below_from": a["below"], "below_to": b["below"], "n_from": a["n"], "n_to": b["n"], "driver": driver}
        if name.startswith("shops"):
            shop_sum += points
        else:
            buyer_sum += points
    return {"from": first["observed_at"], "to": last["observed_at"], "rank": [first["rank"], last["rank"]],
            "total": [first["total"], last["total"]], "total_change": round(last["total"] - first["total"], 4),
            "shop_all_change": round(last["shop_all"] - first["shop_all"], 4), "buyer_all_change": round(last["buyer_all"] - first["buyer_all"], 4),
            "reconstructed_shop_change": round(shop_sum, 4), "reconstructed_buyer_change": round(buyer_sum, 4),
            "residual_shop": round(last["shop_all"] - first["shop_all"] - shop_sum, 4),
            "residual_buyer": round(last["buyer_all"] - first["buyer_all"] - buyer_sum, 4), "components": rows}


def rates(root, since):
    """Per-hour change for us and the leaders, from saved leader-monitor reports, public board values only."""
    reports = []
    for path in sorted(glob.glob(str(Path(root) / "logs/leader-monitor/*.json"))):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("observed_at"):
            reports.append(data)
    usable = [r for r in reports if r["observed_at"] >= since] or reports
    if len(usable) < 2:
        return {"note": "fewer than two leader reports"}
    first, last = usable[0], usable[-1]
    hours = (last["observed_at"] - first["observed_at"]) / 3600
    if hours <= 0:
        return {"note": "no time span"}

    def people(report):
        people_map = {p.get("login"): p for p in report.get("leaders") or []}
        if isinstance(report.get("own"), dict):
            people_map[LOGIN] = report["own"]
        return people_map
    start, end = people(first), people(last)
    table = {}
    for login in set(start) & set(end):
        row = {}
        for group, keys in (("shop", ("profit", "revenue", "deals")), ("buyer", ("surplus", "deals", "posts"))):
            for key in keys:
                a, b = (start[login].get(group) or {}).get(key), (end[login].get(group) or {}).get(key)
                if type(a) in (int, float) and type(b) in (int, float):
                    row[f"{group}.{key}_per_hour"] = round((b - a) / hours, 1)
                    row[f"{group}.{key}_now"] = b
        table[login] = row
    return {"from": first["observed_at"], "to": last["observed_at"], "hours": round(hours, 2), "players": table}


def report(root, board_path):
    root = Path(root)
    board = json.loads(Path(board_path).read_text(encoding="utf-8"))
    players = sorted(board["players"], key=lambda p: -p["total"])
    own_player = next(p for p in players if login_of(p) == LOGIN)
    shop, buyer = rows_for(board, own_player)
    shops, buyers = eligible(board, "shops"), eligible(board, "buyers")
    points = history(root)
    session = [p for p in points if p["observed_at"] >= KST_SESSION_START]
    return {"sources": {"board": Path(board_path).name, "snapshots": len(points),
                        "snapshot_range": [points[0]["observed_at"], points[-1]["observed_at"]] if points else None,
                        "note": "Past full boards are not saved; history percentiles are reconstructed. What-ifs hold rivals still."},
            "formula_check": formula_check(board),
            "own": {"rank": 1 + sum(p["total"] > own_player["total"] for p in players), "players": len(players), "total": own_player["total"],
                    "shop_all": own_player["shop"]["all"], "buyer_all": own_player["buyer"]["all"],
                    "shop": breakdown(shop, shops, shop_weights(board)), "buyer": breakdown(buyer, buyers, buyer_weights(board))},
            "rivals": {"third": compare(board, own_player, players[2]), "tenth": compare(board, own_player, players[9])},
            "sensitivity": {"shop": sensitivity(shop, shops, shop_weights(board)), "buyer": sensitivity(buyer, buyers, buyer_weights(board))},
            "conversion": conversion(board, own_player, [players[0], players[2], players[9]]),
            "scenarios": scenarios(board, own_player, players[2]),
            "scenario_ranks": {name: rank_at(players, own_player["total"] + row["points"])
                               for name, row in scenarios(board, own_player, players[2]).items()},
            "visit_correlates": visit_correlates(board),
            "listing_breadth": listing_breadth(root, board),
            "buyer_attempts_today": attempts_today(root),
            "history": [{k: p[k] for k in ("observed_at", "rank", "total", "shop_all", "buyer_all", "values")} for p in points],
            "change_since_first_snapshot": decompose(points[0], points[-1]) if len(points) > 1 else None,
            "change_since_session_start": decompose(session[0], session[-1]) if len(session) > 1 else None,
            "change_1909_to_2027_kst": decompose(nearest(points, 1793786949.6), nearest(points, 1793791668.3)) if points else None,
            "growth_since_session_start": rates(root, KST_SESSION_START)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, help="Saved /api/board JSON; default is the newest in logs/rank-analysis")
    args = parser.parse_args()
    board = args.board or Path(sorted(glob.glob(str(ROOT / "logs/rank-analysis/board-*.json")))[-1])
    print(json.dumps(report(ROOT, board), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
