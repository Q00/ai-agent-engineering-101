"""Week 08 clearance: stock bought before the demand fix goes cheaper now, where the market buys it.

Restock bought on a flat demand prior until 11-03 23:55 KST (5b66120) and filled the shelf with categories that hardly
sell. Unsold stock counted as a total loss at the end and costs 1% of its cost a day in storage, so (owner, 11-04):
  - a unit supplied before FIX_TS whose category other shops sell MIN_RATE to MAX_RATE times an hour (real demand,
    competitors to win it from, but not so much that it sells anyway at the usual price) opens at the market's median share of the list price for that category less
    ASK_UNDER, and its floor drops to FLOOR_COST x cost: the next order of that category should find us cheapest;
  - in a category with no such demand nothing changes: no order came yet, a lower price would not bring one.
Since 11-04 16:50 (owner: unsold goods only cost storage, sell them cheaper) any goods unit held STALE_H hours or
more is clearance too, whenever it was bought and however fast its category sells: it opens at the market's median
share of the list price for its SKU over the last day (its category's when the SKU has fewer than MIN_SKU_SETTLES
settles by other shops), less ASK_UNDER, or at NO_DATA_ASK x cost with no settles at all, and grade C less the
policy's c_discount on top (the SKU's median is mostly grade A sales); its floor is STALE_FLOOR x that ask, never
under FLOOR_COST x cost (with FLOOR_COST x cost alone a grade C sofa costing 21만 that the market buys near 40만
could be talked down to 19만). 13-16 h: 30 SKUs held 2 h+ had no sale of ours in 6 h; their floor (cost x 1.08 + warranty) sat at or over
the market's prices (TV G7f82f04e 23.9만 asked, market 20-26만; carbon mat floor 7.9만, buyer offered 7.5만), and
once sold restock does not buy them again (first unit ranked 624-748 of 752 by value per won).
Since 11-04 22:50 (owner: sell the ones we hold but cannot sell a little cheaper) a goods SKU other shops settled
STUCK_MKT times or more in the last STUCK_WINDOW_H hours while we settled under STUCK_SHARE of that is "stuck": a unit of it held
STUCK_HELD_H hours or more opens at the SKU's market median share of list (no ASK_UNDER) with its floor STUCK_FLOOR x
that ask, never under its cost (it sells in the market: no reason to lose on it). 20:40-22:40: 지누스 그린티 sold 379 in the market at a median 0.73 of list,
ours 0 of 68 held in 42 talks, our floor 0.78 (a buyer stopped at 0.75); 다우닝 플라나 4인 86 / 0 of 15, 우아미 윈4인
35 / 0 of 19, 헬렌스타인 구스 35 / 0 of 9.
Negotiations that offer a clearance unit are logged with arm "clearance", so the price bandit (model.bandit_stats)
leaves them out.
Since 11-04 23:50 (owner: what we bought and cannot sell, sell it all, even at a small loss; run as designed from
here) a goods unit whose usual floor (seller.floor_price) is above what the market pays for its SKU and grade
(model.market_grade_prices: other shops' median over 2 h, or the day) is "underwater": it opens UW_UNDER under that
price with its floor UW_FLOOR x the ask, never under UW_FLOOR_COST x its cost. When it is also stuck or stale, the
terms with the lower floor apply. 22-23 h: 자코모 4인 A floor 0.86-0.93 of list against the market's 0.80-0.81, 92
talks and 4 deals.

The table is rebuilt from the collector's ledger at most every REFRESH seconds and written to data/clearance.json
for the managers and the owner.
"""
import json
import threading
import time

import model
import policy

FIX_TS = 1793717700       # 11-03 23:55 KST, the demand prior fix went live
MIN_RATE = 1.0            # other shops' settles per hour in the category: below this, no clearance
MAX_RATE = 10.0           # above this the category sells anyway at the usual price: no clearance (owner, 11-04)
ASK_UNDER = 0.05          # open this far under the market's median share of the list price
FLOOR_COST = 1.00         # floor as a share of the unit's cost (0.90 until 11-05, see below)
STALE_H = 6               # a goods unit held this long is clearance (11-04 16:50)
MIN_SKU_SETTLES = 3       # other shops' settles of the SKU in the last day for its own median; else the category's
NO_DATA_ASK = 1.10        # opening ask as a share of cost when nobody sold the SKU or its category in a day
STALE_FLOOR = 0.90        # a stale unit's floor as a share of its clearance ask (never under FLOOR_COST x cost)
STUCK_WINDOW_H = 2        # a SKU is stuck when, over this window, other shops settled it STUCK_MKT times
STUCK_MKT = 20            # ... or more and we settled under STUCK_SHARE of their count (11-04 22:50; "none" let one
STUCK_SHARE = 0.05        # sale of a 6 h stale unit put the other 66 지누스 back on the usual 0.78 floor)
STUCK_HELD_H = 1          # a unit of a stuck SKU held this long goes on clearance
STUCK_FLOOR = 0.95        # its floor as a share of its ask (the SKU's market median), never under its cost
UW_UNDER = 0.01           # an underwater unit opens this far under the market's price for its SKU and grade
UW_FLOOR = 0.95           # its floor as a share of that ask
UW_FLOOR_COST = 1.00      # ... never under this share of its cost (0.85 until 11-05: a small loss then, a loss in book now)
UW_STORAGE_DAY = 0.01     # ... less the storage it would pay to the round's end: 1% of its cost a day (rules_shop.md)
# 11-06 12 h (owner): held at cost, an A of 다우닝 플라나 4인 at 0.814 of list asked 0.814 where the market paid 0.766
# and never sold. Kept to the end it pays 1% of its cost a day (11.5 days left: 11.5%); sold just under the market it
# loses 0.3-7.2% of cost (13 units held over cost, 0.57M at the ask). So its floor is cost x (1 - 0.01 x days left):
# 0.885 now, the cost on the last day. restock.UW_A_UNCOUNTED buys a new A for the SKU meanwhile.


def uw_floor_cost(now):
    """The share of its cost an underwater unit may sell at: UW_FLOOR_COST less the storage to the round's end."""
    return max(0.0, UW_FLOOR_COST - UW_STORAGE_DAY * policy.hours_left(now) / 24)


# 11-15 (owner: "조금 손해보더라도 현금을 늘려서 회전력을"): a goods A over the market is held at what keeping it is
# worth (uw_floor_cost) less what its cost earns as cash until restock stops (CASH_RETURN). The cash ran out: 455M
# (11-13 11 h) -> 87M (11-14 05 h), 80-98M since, goods stock flat at ~860M of which 566 A over the market (420M) and
# C 197M; restock buys what sold, so goods sales fell 657M -> 183M per 12 h (A 491 -> 132M, B_SEEK SKUs: G24f5f979
# -102M, G47788edd -54M) with the market flat (~/wk8-test/drop_sku.py, cash48.py). A won bought 11-13 12 h - 11-14
# 18 h earned 4.5-7.9% of its cost in 12 h and 46-55% of it came back as cash (~/wk8-test/cohort.py): r(h) =
# CASH_K x CASH_R12 x (1 - CASH_TURN^n) / (1 - CASH_TURN), n = 12 h cycles left. CASH_K 0.75: the marginal won earns
# less than the average one. With 60 h left the A is let go at up to ~10% under its cost (2.5% storage + 7.9%);
# selling every A over the market at its median would lose 23.3M of 419M, those within 10% 14.8M of 347M
# (~/wk8-test/thresh.py). At r 0.5x or 1x the average, 10% is within 1M of the best threshold either way.
CASH_RETURN = True
CASH_R12, CASH_TURN, CASH_K = 0.05, 0.55, 0.75


def cash_return(now):
    """What a won of cash earns from now until restock stops (CASH_RETURN), as a share of it."""
    h = policy.hours_left(now) - policy.LAST_HOURS_NO_RESTOCK
    if not CASH_RETURN or h <= 0:
        return 0.0
    return CASH_K * CASH_R12 * (1 - CASH_TURN ** (h / 12)) / (1 - CASH_TURN)


def hold_share(now):
    """The share of its cost a goods A over the market may sell at (seller.hold_floor, restock.kept_a)."""
    return max(0.0, uw_floor_cost(now) - cash_return(now))
UW_HELD_H = 1             # ... and only once held this long (11-05 01:40 review: 66 of 124 underwater opens were
                          # units under 3 h old, some 0.01 h; a fresh unit gets an hour at its usual floor first)
# 11-05 03:45 KST the board's ALL moved from cash to book (cash + cost of unsold units): a sale under cost now lowers
# the score by the gap, while an unsold goods unit costs only 1% of its cost a day. FLOOR_COST and UW_FLOOR_COST went
# 0.90 / 0.85 -> 1.0 (owner, 11-05): clearance still sells cheaper than the usual floor, never under the cost. Food
# close to its date keeps seller.floor_price's half cost (past its date it leaves the book at its cost).
REFRESH = 600
DAY = 86400
PATH = policy.DATA / "clearance.json"

_table, _built = {}, 0.0
_stale = {"sku": {}, "category": {}, "stuck": set(), "grade": {}}


def build(now=None):
    """{category: {"rate": other shops' settles/h, "median": median paid / list}} for categories between MIN_RATE and
    MAX_RATE, and sets _stale: the median paid / list per SKU (MIN_SKU_SETTLES or more) and per category, any rate."""
    global _stale
    now = now or time.time()
    db = model.connect()
    cat = model.catalog(db)
    ours = model.our_urls(db)
    since = max(now - DAY, model.round_start(db) or now)
    hours = max((now - since) / 3600, 0.5)
    n, pct, by_sku = {}, {}, {}
    recent_mkt, recent_ours = {}, {}
    for ts, d in model.entries(db, "settle", since):
        x = cat.get(model.sku_of(d)) or {}
        price = model.amount_of(d)
        if ts >= now - STUCK_WINDOW_H * 3600:
            if d.get("shop") in ours:
                recent_ours[model.sku_of(d)] = recent_ours.get(model.sku_of(d), 0) + 1
            elif x.get("kind", "goods") == "goods":
                recent_mkt[model.sku_of(d)] = recent_mkt.get(model.sku_of(d), 0) + 1
        if d.get("shop") in ours or not x.get("category") or not price or not x.get("list_price"):
            continue
        c = x["category"]
        n[c] = n.get(c, 0) + 1
        pct.setdefault(c, []).append(price / x["list_price"])
        by_sku.setdefault(model.sku_of(d), []).append(price / x["list_price"])
    med = lambda v: sorted(v)[len(v) // 2]
    _stale = {"sku": {k: round(med(v), 3) for k, v in by_sku.items() if len(v) >= MIN_SKU_SETTLES},
              "category": {c: round(med(v), 3) for c, v in pct.items()},
              "stuck": {k for k, v in recent_mkt.items() if v >= STUCK_MKT and recent_ours.get(k, 0) < STUCK_SHARE * v},
              "grade": model.market_grade_prices(db, now, cat)}
    out = {}
    for c, k in n.items():
        if MIN_RATE <= k / hours <= MAX_RATE:
            p = sorted(pct[c])
            out[c] = {"rate": round(k / hours, 2), "median": round(p[len(p) // 2], 3)}
    return out


_lock = threading.Lock()
BACKGROUND = False   # the seller sets it (11-11 16 h): once there is a table only table(True), its warm thread, rebuilds
                     # it. A rebuild takes 2.7 s; stale() and market_ratio() call table() on the seller's event loop,
                     # and every talk open at the 10 minute mark rebuilt it for itself.


def table(rebuild=False):
    global _table, _built
    now = time.time()
    if now - _built < REFRESH or (BACKGROUND and _built and not rebuild):
        return _table
    with _lock:
        if now - _built < REFRESH:         # rebuilt by another thread while this one waited
            return _table
        try:
            _table = build(now)
            PATH.write_text(json.dumps({"ts": now, "fix_ts": FIX_TS, "min_rate": MIN_RATE, "max_rate": MAX_RATE, "ask_under": ASK_UNDER,
                                        "floor_cost": FLOOR_COST, "categories": _table, "stale_h": STALE_H,
                                        "stale_median_sku": _stale["sku"], "stuck": sorted(_stale["stuck"]),
                                        "grade_prices": _stale["grade"]},
                                       ensure_ascii=False),
                            encoding="utf-8")
        except Exception as e:             # a failed rebuild keeps the last table; the seller must never stop on it
            print(f"[clearance] rebuild failed: {type(e).__name__}: {str(e)[:160]}", flush=True)
        _built = now
    return _table


def market_ratio(u):
    """What other shops are paid for this SKU and grade, median paid / list (model.market_grade_prices), or None."""
    table()
    return _stale["grade"].get(u.get("sku"), {}).get(u.get("grade"))


def stale(u, now=None):
    """A goods unit held STALE_H hours or more."""
    return (u.get("kind", "goods") == "goods" and u.get("grade") != "expired"
            and (now or time.time()) - (u.get("supplied_at") or now or time.time()) >= STALE_H * 3600)


def terms(u, now=None, c_discount=0.0, floor=None):
    """{"ask": price, "floor": price} for a clearance unit, None for any other. floor: the unit's usual floor
    (seller.floor_price), for the underwater test."""
    if (u.get("supplied_at") or FIX_TS) < FIX_TS:
        t = table().get(u.get("category"))
        if t:
            return {"ask": u["list_price"] * max(t["median"] - ASK_UNDER, 0.05), "floor": FLOOR_COST * u["cost"]}
    table()
    now = now or time.time()
    cost = u.get("_pool_cost") or u["cost"]
    goods = u.get("kind", "goods") == "goods" and u.get("grade") != "expired"
    out = []
    # the market's price for this SKU and grade when it has one, else the SKU's (all grades) median: 11-05 01:40, the
    # SKU median (0.75 with B and C in it) put G1515d951 A at 0.70 against the market's A 0.85, 7 sales under cost
    m = _stale["grade"].get(u.get("sku"), {}).get(u.get("grade")) if goods else None
    share = m or _stale["sku"].get(u.get("sku"))
    if (u.get("sku") in _stale["stuck"] and share and goods
            and now - (u.get("supplied_at") or now) >= STUCK_HELD_H * 3600):
        ask = u["list_price"] * share * ((1 - c_discount) if u.get("grade") == "C" else 1)
        out.append({"ask": ask, "floor": max(cost, STUCK_FLOOR * ask)})
    if (m and floor and floor > m * u["list_price"]
            and now - (u.get("supplied_at") or now) >= UW_HELD_H * 3600):
        ask = m * u["list_price"] * (1 - UW_UNDER)
        out.append({"ask": ask, "floor": max(uw_floor_cost(now) * cost, UW_FLOOR * ask)})
    if stale(u, now):
        share = _stale["sku"].get(u.get("sku")) or _stale["category"].get(u.get("category"))
        if m:     # just under the market's price for its grade, as underwater (with m - ASK_UNDER a C asked 0.61 of
            ask = m * u["list_price"] * (1 - UW_UNDER)      # list where the market paid C 0.72)
        else:
            ask = u["list_price"] * max(share - ASK_UNDER, 0.05) if share else NO_DATA_ASK * u["cost"]
        if u.get("grade") == "C":
            ask *= 1 - c_discount
        out.append({"ask": ask, "floor": max(FLOOR_COST * cost, STALE_FLOOR * ask)})
    return min(out, key=lambda x: x["floor"]) if out else None
