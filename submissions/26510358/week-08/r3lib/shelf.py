"""The shop's second unprompted action (RL_PROACTIVE.md 3, 2.1): show first a grade the market buys and we do not sell.

2026-11-11: 34 grade C couches (6.9M at cost, 61 h old) had not had one quote, because the shop shows A or B of a
SKU while it has them and a hot SKU always has them; the market sold 268 C of that SKU in 48 h at 0.59 of list
against our cost of 0.37. The proactive loop could not see it: its only action was a lower price (markdown.py).

Every SHELF_EVERY_S the CEO tick looks at each goods SKU with two or more grades on hand. A grade is a candidate
when SHELF_MIN_HELD units of it have waited SHELF_MIN_AGE_H, it is not the grade the shop shows first now, and the
market's median price for it covers our floor. For the candidate, in board score:
    p  share of the market's deals for this SKU at this grade (SHELF_MARKET_H) that paid our floor or more
    q  share of the held units of the grade our own sales of it would clear by the end of the round
    B  (1 - q) x (value of one sale at the market's median for the grade + the storage the unit stops paying)
    C  our close rate on the SKU x value of a sale of the unit shown first now: the quote that unit loses
initiative.judge acts at p > C / (B + C); the order of a shelf is reversible, so it may act there. A grade that
sells out in time at our own pace (q = 1) is not a candidate and writes no row.

Stars: reviews are written at the deal, so a later break does not move them (4 days: our C goods 4.58 over 342
reviews, A 4.52). The guard reads that number each time: SHELF_MIN_REVIEWS reviews of the grade under
SHELF_MIN_STARS and the grade is not pushed.

The shop reads Memory meta `shelf` ({sku: {grade, until}}) in find_unit (live()). An entry lives SHELF_TTL_H and is
then judged again on what it sold.
"""
import statistics
import time

from . import config, initiative, pricing, score_model

GOAL = "shop_grade_first"
METRIC = "units of the grade sold, stars of its deals"
_LOAD = object()


def live(mem, now=None):
    """{sku: grade to show first} for the entries still alive. {} on any memory trouble: the shop's own order stands."""
    now = now or time.time()
    try:
        return {s: m["grade"] for s, m in (mem.get("shelf") or {}).items()
                if m.get("grade") and m.get("until", 0) > now}
    except Exception:
        return {}


def shown_first(us, mem):
    """The unit shop_r3.find_unit offers for a SKU now: best grade first, or the widest margin with C last when the
    SKU is overstocked."""
    if len(us) > config.OVERSTOCK_SKU_UNITS:
        return pricing.overstock_order(us, mem.grade_price_ratio)
    return min(us, key=lambda u: ("ABC".find(u.get("grade") or "C"), u.get("supplied_at") or 0))


def _facts(mem, sku, now):
    """(market price ratios by grade over SHELF_MARKET_H, our sales by grade and our close rate on the SKU over
    24 h)."""
    mine = sorted(mem.my_shop_urls() | set(config.MY_NAMES))
    since = now - 24 * 3600
    market = {}
    with mem.db() as c:
        for r in c.execute("SELECT grade, amount * 1.0 / list_price FROM settles WHERE sku=? AND ts>? AND "
                           "list_price>0 AND amount>0", (sku, now - config.SHELF_MARKET_H * 3600)):
            market.setdefault(r[0], []).append(r[1])
        ours = {r[0]: r[1] for r in c.execute(
            f"SELECT grade, COUNT(*) FROM settles WHERE sku=? AND ts>? AND shop IN ({','.join('?' * len(mine))}) "
            "GROUP BY grade", (sku, since, *mine))}
        n, deals = c.execute("SELECT COUNT(*), SUM(outcome='deal') FROM negotiations WHERE role='shop' AND sku=? "
                             "AND ts>?", (sku, since)).fetchone()
    return market, ours, ((deals or 0) / n if n else config.SHELF_DEFAULT_CLOSE)


def tick(mem, units, now=None, board=_LOAD, log=True):
    """One unprompted pass over the shelf. Returns the decision rows (also written to logs/r3/initiative.jsonl).
    board=None values sales with score_model's fixed slopes."""
    now = now or time.time()
    board = score_model.load() if board is _LOAD else board
    days_left = max(0.0, config.hours_left_in_round(now)) / 24
    state = {s: m for s, m in (mem.get("shelf") or {}).items() if m.get("until", 0) > now}
    by = {}
    for u in units:
        if u.get("kind") == "goods" and u.get("grade") in ("A", "B", "C") and not u.get("legacy"):
            by.setdefault(u["sku"], []).append(u)
    at = lambda price, cost: score_model.sale_value(board, price, cost)
    rows = []
    for sku, us in sorted(by.items()):
        grades = {}
        for u in us:
            grades.setdefault(u["grade"], []).append(u)
        if sku in state or len(grades) < 2:
            continue
        first = shown_first(us, mem)
        facts = best = None
        for g, gs in sorted(grades.items()):
            old = [u for u in gs if now - (u.get("supplied_at") or now) >= config.SHELF_MIN_AGE_H * 3600]
            if g == first.get("grade") or len(old) < config.SHELF_MIN_HELD:
                continue
            facts = facts or _facts(mem, sku, now)
            market, ours, close = facts
            ratios = market.get(g) or []
            u = min(old, key=lambda x: x.get("supplied_at") or 0)
            floor = pricing.unit_floor(u, now=now)
            if len(ratios) < config.SHELF_MIN_MARKET or statistics.median(ratios) * u["list_price"] < floor:
                continue                              # no market for the grade, or it pays under our floor
            price = int(statistics.median(ratios) * u["list_price"])
            p = sum(r * u["list_price"] >= floor for r in ratios) / len(ratios)
            sold = ours.get(g, 0)
            q = min(1.0, sold * days_left / len(gs))
            if q >= 1:
                continue                              # it sells out in time where it is: nothing to judge
            storage = u["cost"] * config.STORAGE_PER_DAY * days_left
            B = (1 - q) * (at(price, u["cost"]) + score_model.sale_value(board, storage, 0, revenue=False))
            shown = market.get(first.get("grade")) or []
            shown_price = statistics.median(shown) * first["list_price"] if shown else first["cost"]
            C = close * max(0.0, at(shown_price, first["cost"]))
            cand = {"grade": g, "p": p, "B": B, "C": C, "held": len(gs), "waiting": len(old), "sold_24h": sold,
                    "market_deals": len(ratios), "price": price, "floor": floor, "cost": u["cost"],
                    "sell_through": round(q, 3), "shown_first": first.get("grade"), "close_rate": round(close, 3)}
            if best is None or p * B - (1 - p) * C > best["p"] * best["B"] - (1 - best["p"]) * best["C"]:
                best = cand
        if best is None:
            continue
        p, B, C = best.pop("p"), best.pop("B"), best.pop("C")
        stars, n = mem.grade_stars(best["grade"], now=now)
        if n >= config.SHELF_MIN_REVIEWS and stars < config.SHELF_MIN_STARS:
            rows.append(initiative.refuse(GOAL, f"our grade {best['grade']} goods deals average {stars:.2f} stars over "
                                          f"{n} reviews, under {config.SHELF_MIN_STARS}", METRIC, B=B, C=C, log=log,
                                          now=now, sku=sku, stars=round(stars, 2), reviews=n, **best))
            continue
        row = initiative.judge(GOAL, p, B, C, METRIC, reversible=True, log=log, now=now, sku=sku,
                               stars=round(stars, 2) if n else None, reviews=n, **best)
        rows.append(row)
        if row["decision"] == "act":
            state[sku] = {"grade": best["grade"], "ts": round(now), "held": best["held"],
                          "until": round(now + config.SHELF_TTL_H * 3600)}
    mem.put("shelf", state)
    return rows
