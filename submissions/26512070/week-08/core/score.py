"""How the board scores us - one place for the rule (user 11/4 11:2x: the metric belongs in the harness).

The market's rules (market.ourolabs.ai/rules, patch 3.3 on 11/5: book replaced cash): shop ALL = 0.5 book + 0.3 revenue +
0.2 stars, book = balance - starting capital - bankruptcy money + what the unsold stock cost (it still counts at cost when
the round ends; goods storage and spoiled food leave it); the board's `profit` is now cash. Before: 0.5 profit; buyer ALL = 0.5 surplus + 0.3 forum votes
on posts written as a buyer + 0.1 deals + 0.1 posts written as a buyer (hidden posts do not count). Each part is a
percentile among the ranked rows only: buyers with a deal, shops with a deal or a purchase. Checked against the
public board on 11/4 11:1x: missing values count as 0 and ties count half, and stars use the board's stars_rank
(buyer 0.423 vs the board's 0.421, shop 0.732 vs 0.737). Before this our collector ranked over every row and read
our buyer's surplus at the 74th percentile when the board put it at the 28th.
"""

SHOP = {"book": 0.5, "revenue": 0.3, "stars_rank": 0.2}
BUYER = {"surplus": 0.5, "forum": 0.3, "deals": 0.1, "posts": 0.1}


def ranked_buyers(rows):
    return [r for r in rows or [] if (r.get("deals") or 0) > 0]


def ranked_shops(rows):
    # a shop is ranked with a deal or a purchase: a purchase moves cash (profit) or leaves stock at cost
    return [r for r in rows or [] if (r.get("deals") or 0) > 0 or (r.get("profit") or 0) != 0
            or (r.get("stock_cost") or 0) > 0]


def _val(r, f):
    v = r.get(f)
    return v if isinstance(v, (int, float)) else 0


def percentile(rows, mine, field):
    """0-1: rows below us plus half the ties, over the ranked rows."""
    if not rows or mine is None:
        return None
    v = _val(mine, field)
    vals = [_val(r, field) for r in rows]
    return (sum(1 for x in vals if x < v) + 0.5 * sum(1 for x in vals if x == v)) / len(vals)


def part(rows, mine, field, weight):
    """A part of ALL: our value, percentile, contribution, and what moving up one ranked row would take and give."""
    p = percentile(rows, mine, field)
    if p is None:
        return None
    v = _val(mine, field)
    above = sorted(x for x in (_val(r, field) for r in rows) if x > v)
    return {"value": v, "percentile": round(p, 3), "weight": weight, "contribution": round(weight * p, 4),
            "next_value": above[0] if above else None,                      # the next row we would pass
            "gain_per_row": round(weight / len(rows), 4),                    # ALL gained per row passed
            "rows_above": len(above)}


def side(rows, mine, weights):
    parts = {f: part(rows, mine, f, w) for f, w in weights.items()}
    if any(p is None for p in parts.values()):
        return None
    return {"all": round(sum(p["contribution"] for p in parts.values()), 4), "ranked": len(rows), "parts": parts,
            # the part where one row up gains most per unit of effort is the one with the most weight per row;
            # the largest remaining room is weight x (1 - percentile)
            "room": sorted(((f, round(p["weight"] * (1 - p["percentile"]), 4)) for f, p in parts.items()),
                           key=lambda x: -x[1])}


def board_score(board, name):
    """Our shop and buyer recomputed from the board as it ranks them, with the board's own ALL to compare."""
    shops, buyers = ranked_shops(board.get("shops")), ranked_buyers(board.get("buyers"))
    me_s = next((r for r in board.get("shops") or [] if r.get("name") == name), None)
    me_b = next((r for r in board.get("buyers") or [] if r.get("name") == name), None)
    out = {"shop": side(shops, me_s, SHOP), "buyer": side(buyers, me_b, BUYER)}
    out["board_all"] = {"shop": (me_s or {}).get("all"), "buyer": (me_b or {}).get("all")}
    return out


def text():
    """The rule in words, for prompts."""
    return ("Board score (market agent.md, checked against the board): shop ALL = 0.5 book + 0.3 revenue + 0.2 "
            "stars (stars_rank); buyer ALL = 0.5 surplus + 0.3 forum votes on buyer posts + 0.1 deals + 0.1 buyer "
            "posts. Each part is a percentile among buyers with a deal and shops with a deal or a purchase only "
            "(missing counts as 0, ties half), so it is our place against those players, not an amount. "
            "What each part counts (agent.md, user 11/5 02:0x): book = cash (balance minus the starting "
            "capital and bankruptcy money) + what the unsold stock cost, so buying stock does not lower it and stock left on "
            "11/17 23:59 still counts at cost; goods storage (1%/day) and food thrown away past its date leave it "
            "(patch 3.3, 11/5); revenue = settled "
            "prices minus refunds; stars are pulled toward 3 while reviews are few; surplus = list price of what "
            "served the order (food that lasted, goods that did not break) minus the price paid, refunds returned; "
            "forum = other agents' votes on our buyer posts; posts = posts written as a buyer (hidden ones do not "
            "count). The final rank is the Players total (shop ALL + buyer ALL) on 11/17 23:59; our live rank in "
            "every part is in logs/strategy/position.md.")
