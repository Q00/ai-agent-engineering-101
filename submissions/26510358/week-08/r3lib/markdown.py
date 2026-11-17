"""The shop's unprompted action (RL_PROACTIVE.md 3, 2.1): mark down an overstocked goods SKU before anyone asks.

Every MARKDOWN_EVERY_S the CEO tick looks at each goods SKU with more than OVERSTOCK_SKU_UNITS on hand that is slow
at our own sales (pricing.is_slow_sku). The clearance bandit proposes an arm for it:
    keep   the first ask the pricing rules give
    cost   first ask = cost + MIN_MARGIN (and at least floor / 0.97: the shop takes an offer 3% under its ask)
    dump   first ask = the unit's floor (L0: pricing.unit_floor, unchanged)
and initiative.judge decides whether to act on a markdown arm, with everything in board score:
    p  share of the market's deals for this SKU in 24 h that paid more than the marked-down price
       (a buyer comparing shops would have taken ours)
    q  share of the SKU's units our own sales pace would sell by the end of the round at the regular price
    B  (1 - q) x (score_model value of one sale at that price + the storage the unit stops paying until the round
       ends): the sale of a unit that would otherwise still be on the shelf when the round ends
    C  q x (value at the regular price - value at the marked-down price): the margin given up on a unit that
       would have sold anyway. A SKU that sells out in time has q = 1, B = 0 and is never marked down.
A markdown lives MARKDOWN_TTL_H and is then judged again: a price is reversible, which is why it may act at p > p*.
The cut never exceeds MARKDOWN_MAX_CUT of the regular price and the shop still clamps to its floor.

The shop reads Memory meta `markdown` ({sku: {arm, grade, until, ...}}) when it quotes (apply()); the markdown holds
for units of the grade it was judged on. When a markdown window
ends, the SKU's sales inside it become the bandit's reward.
"""
import time

from . import bandit, config, initiative, pricing, score_model

ARMS = ("keep", "cost", "dump")
METRIC = "sku days of cover, revenue pct"
MIN_MARKET_DEALS = 5
_LOAD = object()


def overstocked(units, mem):
    """{sku: [units]} of goods SKUs that are overstocked and slow."""
    by = {}
    for u in units:
        if u.get("kind") == "goods" and u.get("grade") != "expired" and not u.get("legacy"):
            by.setdefault(u["sku"], []).append(u)
    return {s: us for s, us in by.items()
            if len(us) > config.OVERSTOCK_SKU_UNITS and pricing.is_slow_sku(len(us), mem.my_sku_sales(s))}


def arm_price(arm, u, floor, regular):
    """First ask of a markdown arm for one unit: never under the floor (with the 3% the shop accepts under an ask),
    never more than MARKDOWN_MAX_CUT under the regular first ask, never above it."""
    target = u["cost"] * (1 + config.MIN_MARGIN) if arm == "cost" else floor
    low = max(int(floor / 0.97) + 1, int(regular * (1 - config.MARKDOWN_MAX_CUT)))
    return int(min(regular, max(target, low)))


def apply(mem, u, ask, floor, now=None):
    """(ask, arm or None) for the shop: the marked-down first ask when the unit's SKU has a live markdown."""
    m = (mem.get("markdown") or {}).get(u["sku"])
    now = now or time.time()
    if not m or m.get("arm") in (None, "keep") or m.get("until", 0) <= now or u.get("kind") != "goods":
        return ask, None
    if m.get("grade") and u.get("grade") != m["grade"]:
        # The markdown was judged on one grade's price, margin and market (the unit shown first then). A unit of
        # another grade was never judged: 2026-11-11 our C goods closed at 0.82 of the market's C median, the cut
        # of a markdown decided for the SKU's A or B units.
        return ask, None
    return arm_price(m["arm"], u, floor, ask), m["arm"]


def context(u, n, sold_24h, days_left):
    premium = (u.get("list_price") or 0) >= config.PREMIUM_MIN_LIST
    return ("premium" if premium else "goods", "over_round" if n > sold_24h * days_left else "slow")


def _market_ratios(mem, sku, now, hours=24):
    with mem.db() as c:
        return [r[0] for r in c.execute("SELECT amount * 1.0 / list_price FROM settles WHERE sku=? AND ts>? AND "
                                        "list_price>0", (sku, now - hours * 3600))]


def _window_reward(mem, board, sku, m, now):
    """Board score of our sales of the SKU inside a finished markdown window (cost = the SKU's mean cost then)."""
    mine = sorted(mem.my_shop_urls() | set(config.MY_NAMES))
    with mem.db() as c:
        rows = c.execute(f"SELECT amount FROM settles WHERE sku=? AND ts>? AND ts<=? AND shop IN "
                         f"({','.join('?' * len(mine))})", (sku, m["ts"], min(now, m["until"]), *mine)).fetchall()
    return sum(score_model.sale_value(board, r[0], m.get("avg_cost") or 0) for r in rows), len(rows)


def tick(mem, units, now=None, board=_LOAD, policy=None, log=True):
    """One unprompted pass. Returns the decision rows (also written to logs/r3/initiative.jsonl). board=None values
    sales with score_model's fixed slopes."""
    now = now or time.time()
    board = score_model.load() if board is _LOAD else board
    policy = policy or bandit.Bandit("clearance", ARMS, "cost", mem=mem, log=log)
    days_left = max(0.0, config.hours_left_in_round(now)) / 24
    live = mem.get("markdown") or {}
    rows = []
    for sku, m in list(live.items()):                        # finished windows first: they teach the bandit
        if m.get("until", 0) <= now:
            reward, sold = _window_reward(mem, board, sku, m, now)
            policy.update(m["context"], m["arm"], reward, now=now, sku=sku, sold=sold)
            del live[sku]
    for sku, us in sorted(overstocked(units, mem).items()):
        if sku in live:
            continue
        n, sold = len(us), mem.my_sku_sales(sku)
        u = pricing.overstock_order(us, mem.grade_price_ratio)          # the unit the shop would offer first
        grade_ratio, _ = mem.grade_price_ratio(u["sku"], u.get("grade"))
        if not grade_ratio:                                  # too few deals at this grade: the SKU's own median
            grade_ratio, _ = mem.price_ratio(sku=sku, category=None)
        ratios = _market_ratios(mem, sku, now)
        ctx = context(u, n, sold, days_left)
        base = {"sku": sku, "units": n, "sold_24h": sold, "days_of_cover": round(n / sold, 1) if sold else None,
                "context": "|".join(ctx), "grade": u.get("grade"), "market_deals_24h": len(ratios)}
        if not grade_ratio or len(ratios) < MIN_MARKET_DEALS:
            rows.append(initiative.judge("shop_markdown", 0.0, 0.0, 0.0, METRIC, log=log, now=now, arm="keep",
                                         reason="no market price for this SKU yet", **base))
            continue
        slow_floor = pricing.needs_slow_floor(u, True, grade_ratio)
        floor = pricing.unit_floor(u, now=now, slow=slow_floor)
        regular = int(u["list_price"] * (grade_ratio - config.SLOW_UNDERCUT))       # what cap_ask gives a slow SKU
        regular = max(regular, int(floor / 0.97) + 1)
        arm, prop = policy.choose(ctx, now=now, sku=sku)
        price = arm_price(arm, u, floor, regular) if arm != "keep" else regular
        row = None
        if arm != "keep" and price < regular:
            p = sum(r > price / u["list_price"] for r in ratios) / len(ratios)
            storage = u["cost"] * config.STORAGE_PER_DAY * days_left
            at = lambda x: score_model.sale_value(board, x, u["cost"])
            q = min(1.0, sold * days_left / n) if n else 1.0
            B = (1 - q) * (at(price) + score_model.sale_value(board, storage, 0, revenue=False))
            C = q * max(0.0, at(regular) - at(price))
            row = initiative.judge("shop_markdown", p, B, C, METRIC, reversible=True, log=log, now=now, arm=arm,
                                   propensity=round(prop, 4), price=price, regular=regular, floor=floor,
                                   cost=u["cost"], sell_through=round(q, 3), **base)
        executed = arm if row and row["decision"] == "act" else "keep"
        if row is None:
            rows.append(initiative.judge("shop_markdown", 0.0, 0.0, 0.0, METRIC, log=log, now=now, arm="keep",
                                         propensity=round(prop, 4), regular=regular, floor=floor,
                                         reason="the bandit keeps the price" if arm == "keep"
                                         else "the arm's price is not under the regular ask", **base))
        else:
            rows.append(row)
        if arm == "keep" or executed != "keep":               # a vetoed arm is not what ran: it teaches nothing
            live[sku] = {"arm": executed, "context": "|".join(ctx), "ts": round(now), "units": n,
                         "grade": u.get("grade"),
                         "until": round(now + config.MARKDOWN_TTL_H * 3600),
                         "avg_cost": round(sum(x["cost"] for x in us) / n)}
    mem.put("markdown", live)
    return rows
