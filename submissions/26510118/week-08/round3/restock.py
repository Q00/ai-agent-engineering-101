"""Round 3 restock hook (S1): the kit runs it every 20 minutes
(RUN_EVERY; every 60 s until the 11/3 change) and buys the first 10 lines it prints.

    python restock.py --state .market
    {"buy": [{"sku": "...", "qty": 1}, ...]}            (or "bankrupt": true)

L4L: each run buys only the gap between a target count per product and what is on the
shelf, so nothing is bought again until something sold (food with under a day left
does not count as on the shelf; S5 sells it off). Exploration phase:
  stage 1   one representative product in every category it can stock (list price up to
            60,000, food that keeps a week or more): the product the whole market sells
            most in that category (ledger summary), else the one nearest its median price
  stage 2   once our own sales reach 10: a cheaper second product (near 0.7 of the
            median) in the 40 largest categories, and the categories whose products all
            list above 60,000 (up to 400,000)
  targets   1 per listed product; 3 once it has sold, and 2 from the start in a category the
            whole market sold 3 or more of in the last day (a refill waits for the next run, up
            to 20 minutes); +1 if a buyer was turned away for lack of it in the last day; at most 4.
            A product selling fast is stocked for its pace instead: what our last 3 hours of its
            sales say will sell in the next COVER windows, up to 20 (one line's quantity)
  explore   the lines a window allows are few (about 18), and refilling what sells in small lots
            would take them all, so in the exploration phase 40% of a run's lines (10% in the
            focus phase) go to products never stocked yet, a category we never had first (a second
            product of a category we know waits), and the deep refills free the rest
  order     only 10 lines a run, so what comes first matters while the first day's shelf fills
            (about 12 runs): refills of what we sold, then the products with the most evidence
            of demand (the market's sales of the category in the last day, our buyer's orders for
            it, a wanted post), then the stage order. With no evidence yet it is the stage order,
            which brings one product of every group (the catalog's top level: 식품, 가구/침구,
            의류, ...) in the first runs and then mixes the groups in proportion to their size, so
            each run samples the whole catalog; the market's list is in alphabetical order, which
            would bring furniture first and kitchenware hours later.
  limits    stock at cost up to CAP (40M) or CAP_SHARE (90%) of the book (cash + stock at cost),
            whichever is more, and the balance kept at RESERVE (2.5M) or more, planning as if every
            unit came in at the dearest cost (75% of list)
  wanted    a category the analysis found wanted (another agent's "wanted" post on the
            forum, or one our buyer found no shop for), or one our shop turned buyers away
            for 3 times in 6 hours because it had nothing of it (missed), gets its
            representative product
Supply windows (the market's rule from the evening of 11/3): supply opens for everyone for 3 minutes
every 20 (the first window 10 minutes); outside one the market refuses (429), and inventory.json
carries restock {open, opens_at, closes_at}. A run with the window shut and not opening within
SUPPLY_LEAD buys nothing; otherwise it plans only the lines the window leaves time for (one supply
call per SUPPLY_GAP seconds), the most needed first.
Rules in force (analysis/active_rules.json, which the integrated analysis writes only in
the focus phase): focus categories +1, drop categories 0. Wind-down, by the clock: from 3
days before the end only products selling 2 a day or more are refilled and nothing new is
listed; nothing is bought in the last 6 hours.
The premium line (focus phase, 11/4: sell where supply is short): dear products listed in
analysis/line.json (linewatch.py rewrites it every 20 minutes and adds products; a person may write
it, then it is left alone; LINE when there is none) with the most units each may hold. 76% of the
market's money was in beds and sofas, and the dear ones had few sellers (2-11 a product) while half
of our buyer's orders for beds over 1.2M found no shop. A line product is kept at 1 + our sales of
it in the last hour (LINE_PACE) up to its cap; one held LINE_STALL without a sale gets no more; none
is bought in the last 30 hours. Its lines come first: up to LINE_TRY_FIRST products not yet tried,
then the best sellers, at most LINE_LINES a run and LINE_SHARE of the money above RESERVE, leaving
the shelf SHELF_LINES lines, SHELF_MONEY and SHELF_ROOM of the cap; the shelf's other rules
(stages, drops, the wind-down) do not touch it. Anything listed over PRICEY_MAX that is not in
the line is never refilled (a sofa sold once is not stocked 3 deep by the ordinary rule).

Each run also checks which agreed deals settled (their unit left the inventory) or stayed
unpaid (still there 10 minutes later), and records what reached the shelf and what left it
without a sale (expired food, or gone) in records/stock_flow.jsonl. The ledger is collected by the analysis runner
(analysis.py); this hook only reads its market_summary.json.
"""
import argparse
import json
import math
import time
from collections import Counter, defaultdict
from pathlib import Path

import common
import pricing

CAP = 40_000_000         # stock at cost, planned at DEAREST: 2.5M until 11/4 14:00, then 4M, 15M at 20:48, 25M at 21:45 (the
                         # premium line sold 12 units in its first 30 minutes) and 40M at 23:40 (25M bound at the 23:20 window
                         # and the line sold out within half an hour): the balance kept binds first, the user's call to invest
CAP_SHARE = 0.9          # 11/5 01:00, the person's call: the cap grows with the book (cash + stock at cost): CAP or this
                         # share of the book, whichever is more (top shops' books were 80-280M against our 57M)
SHELF_LINES = 2          # ...and the shelf keeps a share of every run: the line leaves it this many lines,
SHELF_MONEY = 2_000_000  # this much of the money above RESERVE,
SHELF_ROOM = 3_000_000   # and this much of the stock cap (from 23:40 the line took 9 of 10 lines, 90% of the money and
                         # 36M of the 40M cap: our regular goods fell from about 300 units to 151 and their sales by half)
RESERVE = 2_500_000      # balance kept: refunds of broken goods under warranty come out of it
LINE = {                 # the premium line: SKU -> the most units held (11/4 evening; analysis/line.json overrides it,
    "G8ec51935": 5,      # 자코모 4인 소파 2,320,000: 40 sold an hour by 11 shops, 0.80 of list   which the line watch rewrites)
    "G7a341425": 4,      # 자코모 3.5인 소파 2,192,000: 3.8 an hour, 2 shops (ours: 3 sold in the first 30 minutes)
    "G00327e80": 4,      # 다우닝 4인 소파 2,246,800: 15.8 an hour, 9 shops
    "G03d0199f": 4,      # 라클라우드 침대 Q 1,611,000: 12.8 an hour, 6 shops; our buyer's orders for such beds unmet
    "G8adbc159": 3,      # 에비뉴엘 4인 소파 1,872,000: 7.9 an hour, 3 shops
    "Gb5d0dde7": 3,      # 시몬스 매트리스 Q 1,094,400: 9.4 an hour, 7 shops
    "G04ee8423": 3,      # 자코모 3인 소파 1,624,000: 9 an hour, 9 shops, 0.80 (added 21:45)
    "G8095ddd6": 2,      # 침대 851,400: 4.9 an hour, 4 shops
    "G05277ffe": 2,      # 침구 600,000: 6.7 an hour, 4 shops
    "G0e4deffc": 2,      # 에비뉴엘 4인 소파+스툴 2,112,000: 2 an hour, 3 shops, 0.845 (added 21:45)
    "G19a70f63": 2,      # 소파 1,672,800: 1.9 an hour, 4 shops (added 21:45)
    "G10693924": 2,      # 소파 707,000: 2.9 an hour, 5 shops (added 21:45)
}
LINE_LINES = 8           # line products bought in one run at most (4 until 21:45, 7 until 23:40, 9 until 11/5 01:00, when
                         # the shelf's SHELF_LINES made 8 of the kit's 10 the most anyway), and never more than
LINE_SHARE = 0.9         # LINE_SHARE of the run's lines nor of the balance above RESERVE (0.5 until 21:45, 0.75 until 23:40)
LINE_PACE = 3600         # a line product is kept at 1 + what we sold of it in this long (a unit sells out within
                         # minutes, so our sales count supply as much as demand: one more than they asks for more)
LINE_MAX_UNITS = 10      # 5 until 11/4 23:40 (the best seller sold 20 in 1.5 h at a cap of 5; others sold it 90 an hour)
LINE_STALL = 2 * 3600    # held this long with no sale of the product: no more units
LINE_TRY_FIRST = 5       # line products never sold nor held (just added) go first, this many a run (3 until 11/5 02:20):
                         # ordered by sales alone, one with none came after the best sellers and no run reached it
                         # (11/5 01:20: the three beds added at 01:09 were not bought; ranked as 3 sold an hour, at 01:40
                         # they tied with the best sellers, came after them on the tie, and the money ran out first)
LINE_STOP = 0            # no line product is bought this close to the end: off (30 h until 11/5 15:40; the book
                         # counts unsold units at their cost, so buying to the end loses nothing but storage)
LINES = 10               # the kit buys at most 10 lines a run
RUN_EVERY = 20 * 60      # seconds between the kit's runs of this hook (60 until 11/3); set it to the kit's
SLOW = RUN_EVERY >= 600  # refills wait 10 minutes or more: keep one more unit of what sells
SOLD_TARGET = 2 + SLOW   # a product we have sold
HOT, HOT_TARGET = 3, 1 + SLOW   # a category the market sold this many of in a day: its products start here
WANTED_WEIGHT = 2        # a wanted category counts as this many sales when ordering what to buy first
SUPPLY_LEAD = 90         # seconds: the kit waits up to this long for a window before it skips a line
SUPPLY_GAP = 10          # seconds between the kit's supply calls
MAX_TARGET = 4
PACE_HOURS = 3           # our recent sales of a product, over this many hours, set its pace
COVER = 2                # runs' worth of that pace kept on the shelf (the next window may not have lines to spare)
MAX_DEPTH = 20           # one line's quantity at most
EXPLORE_SHARE = {"explore": 0.4, "focus": 0.1}   # share of a run's lines for products never stocked yet
GATE = 10                # our settled sales before stage 2
MAX_PRICE = 60_000       # stage 1 list prices
PRICEY_MAX = 400_000     # stage 2, categories with nothing cheaper
MIN_SHELF = 168          # hours: food that keeps less than a week is not listed
SECOND, SECOND_TOP = 0.7, 40
DEAREST = 0.75           # grade A costs at most 75% of the list price
WIND_DOWN, STOP = 0, 0    # off (3 days, 6 h until 11/5 15:40): only fast sellers refilled in the last 3 days, nothing
                          # bought in the last 6 h; with the book counting stock at cost, those were sales given up
FAST = 2                 # sales in the last day for a product to be refilled in the wind-down
UNPAID_AFTER = 600       # seconds an agreed unit may stay in stock before the deal counts as unpaid
WARN_AFTER, WARN_BELOW = 86400, 3


def check_deals(inv: dict, state: dict, now: float) -> set:
    """Deals the seller agreed: settled once the unit left the inventory, unpaid if it is still
    there UNPAID_AFTER seconds later (the buyer's kit refused to pay, or the buyer left). A unit a
    buyer agreed to and did not pay for is offered again, so several deals can name one unit: when
    it leaves, only the latest of them is the sale (the earlier buyers went without paying).
    Returns the units recorded as sold."""
    pending = state.setdefault("pending", {})
    sold = set()
    for d in new_deals(state):
        pending[d["task"]] = d
    present = {u["unit"] for u in inv.get("units") or []}
    latest = {}
    for d in pending.values():
        if d["unit"] not in present and (d.get("ts") or 0) >= (latest.get(d["unit"], {}).get("ts") or 0):
            latest[d["unit"]] = d
    for task, d in list(pending.items()):
        fields = {k: d.get(k) for k in ("task", "unit", "sku", "price", "cost", "list_price", "grade", "warranty")}
        if d["unit"] not in present:
            if latest[d["unit"]] is d:
                common.record("sales", deal_ts=d.get("ts"), **fields)
                sold.add(d["unit"])
            else:
                common.record("unpaid", deal_ts=d.get("ts"), superseded=True, **fields)
            del pending[task]
        elif now - (d.get("ts") or now) > UNPAID_AFTER:
            common.record("unpaid", deal_ts=d.get("ts"), **fields)
            del pending[task]
    return sold


def stock_flow(inv: dict, state: dict, now: float, sold: set) -> dict:
    """What reached the shelf and what left it since the last run, unit by unit, into records/stock_flow.jsonl:
    received (the supply that arrived: records/restock.jsonl holds the lines asked for, and a line outside a
    supply window is refused), expired (food past its date, which the market takes off the shelf with nothing
    recorded) and gone (left with no deal we know of). The expired units' cost is what a category wastes;
    before 11/4 neither was known (2,850 units asked for, 2,502 sold, 168 on the shelf: 180 unexplained)."""
    units = {u["unit"]: u for u in inv.get("units") or [] if u.get("unit")}
    prev = state.get("shelf")
    state["shelf"] = {k: [u.get("sku"), u.get("cost"), u.get("expires_at")] for k, u in units.items()}
    if prev is None:                                     # the first run only takes the snapshot
        return {}
    out = Counter()
    for k, (sku, cost, expires) in prev.items():
        if k in units or k in sold:
            continue
        what = "expired" if expires and expires <= now + 120 else "gone"
        common.record("stock_flow", what=what, unit=k, sku=sku, cost=cost)
        out[what] += 1
    for k, u in units.items():
        if k not in prev:
            common.record("stock_flow", what="received", unit=k, sku=u.get("sku"), cost=u.get("cost"), grade=u.get("grade"))
            out["received"] += 1
    return dict(out)


def new_deals(state: dict) -> list:
    """The deals agreed since the last run: records/deals.jsonl read from the byte offset where the last
    run stopped (state["deals_offset"]), so a file that grows all round is not read whole every minute.
    A line still being written waits for the next run."""
    path = common.MARKET_DIR / "records" / "deals.jsonl"
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    if "deals_offset" not in state:                      # before 11/4 the state counted lines
        seen = state.pop("deals_seen", 0)
        state["deals_offset"] = sum(len(x) for x in raw.splitlines(keepends=True)[:seen])
    tail = raw[state["deals_offset"]:]
    done = tail[:tail.rfind(b"\n") + 1]
    state["deals_offset"] += len(done)
    out = []
    for line in done.decode("utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def stages(catalog: dict, demand: list, summary: dict) -> tuple[list, list]:
    """(stage 1 SKUs, stage 2 SKUs) from the categories, the catalog and the market's sales."""
    by_cat = defaultdict(list)
    for s in catalog.values():
        by_cat[s["category"]].append(s)
    sold = {k: v.get("sold_24h", 0) for k, v in (summary.get("by_sku") or {}).items()}
    near = lambda skus, price: min(skus, key=lambda s: abs(s["list_price"] - price))
    ok = [d for d in demand if not (d.get("kind") == "food" and (d.get("shelf_hours") or 0) < MIN_SHELF)]
    one, two = [], []
    for d in ok:
        fit = [s for s in by_cat[d["category"]] if s["list_price"] <= MAX_PRICE]
        if fit:
            best = max(fit, key=lambda s: sold.get(s["sku"], 0))
            one.append(best["sku"] if sold.get(best["sku"]) else near(fit, d["median_price"])["sku"])
        else:
            dear = [s for s in by_cat[d["category"]] if s["list_price"] <= PRICEY_MAX]
            if dear:
                two.append(near(dear, d["median_price"])["sku"])
    for d in sorted(ok, key=lambda d: -d.get("skus", 0))[:SECOND_TOP]:
        fit = [s for s in by_cat[d["category"]] if s["list_price"] <= MAX_PRICE and s["sku"] not in one]
        if fit:
            two.append(near(fit, SECOND * d["median_price"])["sku"])
    return spread(one, catalog), spread(two, catalog)


def spread(skus: list, catalog: dict) -> list:
    """The same SKUs, each top-level group spread over the list: the i-th of a group of n sits at i / n
    of the way, so every group's first product comes before any group's second (the largest groups
    first), and after that each group comes in proportion to its size (a group of 38 every third place)."""
    group = lambda s: catalog[s]["category"].split(">")[0]
    sizes, seen, keys = Counter(group(s) for s in skus), Counter(), {}
    for s in skus:
        g = group(s)
        keys[s] = (seen[g] / sizes[g], -sizes[g])
        seen[g] += 1
    return sorted(skus, key=lambda s: keys[s])


MISSED_HOURS, MISSED_MIN = 6, 3   # turned away for a whole category we do not stock: this many times in this many hours


def missed(refusals: list, now: float) -> dict:
    """{category leaf: times} our shop turned a buyer away because it had nothing of that category (a request by
    category, not for one SKU), in the last MISSED_HOURS, when MISSED_MIN or more: our own visitors' demand,
    which the public ledger cannot show. 11/4, 6 hours: 곡물 80, 가공/조리 육류 27, 디저트 24, 한과/떡 17."""
    c = Counter(r.get("category") for r in refusals if r.get("reason") == "out" and not r.get("sku") and r.get("category")
                and (r.get("ts") or 0) >= now - MISSED_HOURS * 3600)
    return {k: n for k, n in c.items() if n >= MISSED_MIN}


def wanted_skus(catalog: dict, demand: list, wanted: dict, listed: set) -> list:
    """The representative product of each wanted category we do not list yet."""
    out = []
    for d in demand:
        leaf = d["category"].split(">")[-1]
        if d["category"] not in wanted and leaf not in wanted:
            continue
        fit = [s for s in catalog.values() if s["category"] == d["category"] and s["list_price"] <= PRICEY_MAX]
        if fit and not any(s["sku"] in listed for s in fit):
            out.append(min(fit, key=lambda s: abs(s["list_price"] - d["median_price"]))["sku"])
    return out


def evidence(catalog: dict, summary: dict, orders: list, wanted: dict, now: float):
    """sku -> how much says its category sells: the market's sales in the last day, our buyer's orders
    for it in the last day, and WANTED_WEIGHT for a wanted category. 0 when nothing says so yet."""
    market = {c: v.get("sold_24h", 0) for c, v in (summary.get("by_category") or {}).items()}
    ordered = Counter(o.get("category") for o in orders if (o.get("ts") or 0) >= now - 86400)

    def score(sku: str) -> float:
        cat = catalog[sku]["category"]
        hot = cat in wanted or cat.split(">")[-1] in wanted
        return market.get(cat, 0) + ordered.get(cat, 0) + (WANTED_WEIGHT if hot else 0)
    return score


def load_line(data) -> dict:
    """analysis/line.json {"skus": {SKU: most units}} when it is there and well formed ({"skus": {}} turns the
    line off), else LINE. A malformed file must not stop the run: it buys the whole shelf, food included."""
    if isinstance(data, dict) and isinstance(data.get("skus"), dict):
        return data["skus"]
    return LINE


def line_gaps(line: dict | None, catalog: dict, units: list, sales: list, now: float) -> list:
    """[(sku, units to buy)] for the premium line: 1 + our sales of it in the last LINE_PACE seconds (one unit to
    start), up to the product's cap; nothing more for one held LINE_STALL seconds with no sale, nor near the end.
    (Until 21:45 on 11/4 the pace over 3 hours covered two cycles: 3 sold in half an hour still meant one unit.)"""
    line = {s: n for s, n in (line or {}).items()
            if s in catalog and isinstance(n, int) and not isinstance(n, bool) and 0 < n <= LINE_MAX_UNITS}
    if not line or common.ROUND_END - now <= LINE_STOP:
        return []
    held = Counter(u["sku"] for u in units if u.get("sku") in line)
    recent = Counter(s["sku"] for s in sales if s.get("sku") in line and s.get("ts", 0) >= now - LINE_PACE)
    alive = {}                                           # the last sale of it, or the arrival of the oldest unit held
    for s in sales:
        if s.get("sku") in line:
            alive[s["sku"]] = max(alive.get(s["sku"], 0), s.get("ts", 0))
    for u in units:
        if u.get("sku") in line:
            first = min((x.get("supplied_at") or now) for x in units if x.get("sku") == u["sku"])
            alive[u["sku"]] = max(alive.get(u["sku"], 0), first)
    out = []
    for sku, most in line.items():
        if held[sku] and now - alive.get(sku, now) > LINE_STALL:
            continue
        target = min(most, 1 + recent[sku])
        if target > held[sku]:
            out.append((sku, target - held[sku]))
    new = [g for g in out if g[0] not in alive]
    tried = sorted((g for g in out if g[0] in alive), key=lambda g: -recent[g[0]])
    return new[:LINE_TRY_FIRST] + tried + new[LINE_TRY_FIRST:]    # a few new products, then what sells most


def plan(inv: dict, catalog: dict, demand: list, summary: dict, sales: list, refusals: list,
         rules: dict, now: float, wanted: dict | None = None, orders: list | None = None,
         stocked: set | None = None, phase: str = "explore", line: dict | None = None) -> tuple[dict, dict]:
    """({"buy": [...]} or bankruptcy, a few numbers for the log). rules: the seller's rules in force;
    orders: our buyer's order records (what buyers ask for); stocked: the SKUs bought before;
    line: the premium line, SKU -> the most units held."""
    units = inv.get("units") or []
    if inv.get("round", 0) < 3:
        return {"buy": []}, {"note": "before Round 3"}
    if inv.get("balance", 0) < inv.get("bankrupt_line", 0) and not units and inv.get("bankruptcies_left", 0) > 0:
        return {"buy": [], "bankrupt": True}, {"note": "below the line with nothing to sell"}
    to_end = common.ROUND_END - now
    if to_end < STOP:
        return {"buy": []}, {"note": "last 6 h: no buying"}
    window = inv.get("restock") or {}
    if window and not window.get("open") and (window.get("opens_at") or 0) - now > SUPPLY_LEAD:
        return {"buy": []}, {"note": f"supply closed; the next window opens in {int(window['opens_at'] - now)} s"}
    lines = LINES
    if window.get("closes_at"):
        left = window["closes_at"] - max(now, window.get("opens_at") or now)
        lines = max(1, min(LINES, int(left // SUPPLY_GAP)))
    one, two = stages(catalog, demand, summary)
    sold = Counter(s["sku"] for s in sales)
    sold_day = Counter(s["sku"] for s in sales if s.get("ts", 0) >= now - 86400)
    refused = Counter(r["sku"] for r in refusals if r.get("sku") in catalog and r.get("ts", 0) >= now - 86400)
    gate = len(sales) >= GATE
    targets = {s: 1 for s in one}
    if gate:
        for s in two:
            targets.setdefault(s, 1)
    market = {c: v.get("sold_24h", 0) for c, v in (summary.get("by_category") or {}).items()}
    for s in targets:
        if market.get(catalog[s]["category"], 0) >= HOT:
            targets[s] = max(targets[s], HOT_TARGET)
    pace = Counter(s["sku"] for s in sales if s.get("ts", 0) >= now - PACE_HOURS * 3600)
    depth = {s: min(MAX_DEPTH, math.ceil(n / (PACE_HOURS * 3600) * COVER * RUN_EVERY)) for s, n in pace.items()}
    for s in sold:
        if s in catalog:
            targets[s] = max(targets.get(s, 0), SOLD_TARGET, depth.get(s, 0))
    for s, n in refused.items():
        targets[s] = targets.get(s, 1) + 1
    gone = missed(refusals, now)                         # whole categories our visitors asked for when we had none
    for s in targets:
        if catalog[s]["category"].split(">")[-1] in gone:
            targets[s] += 1
    wanted = dict(wanted or {}, **{c: n for c, n in gone.items() if c not in (wanted or {})})
    for s in wanted_skus(catalog, demand, wanted, set(targets)):
        targets.setdefault(s, 1)
    drop, focus = set(rules.get("drop_categories") or []), set(rules.get("focus_categories") or [])
    for s in list(targets):
        cat = catalog[s]["category"]
        if cat in drop or cat.split(">")[-1] in drop:
            targets[s] = 0
        elif cat in focus or cat.split(">")[-1] in focus:
            targets[s] += 1
    targets = {s: min(MAX_DEPTH if depth.get(s, 0) > MAX_TARGET else MAX_TARGET, t) for s, t in targets.items()
               if s not in (line or {}) and catalog[s]["list_price"] <= PRICEY_MAX}    # dear goods only through the line
    wind_down = to_end < WIND_DOWN
    if wind_down:
        targets = {s: t for s, t in targets.items() if sold_day.get(s, 0) >= FAST}
    usable = Counter(u["sku"] for u in units if u.get("grade") != "expired"
                     and (u.get("expires_at") or float("inf")) > now and not pricing.near_expiry(u, now))
    rank = lambda s: 0 if sold.get(s) else 1 if s in one else 2
    score = evidence(catalog, summary, orders or [], wanted or {}, now)
    gaps = sorted(((rank(s), s, t - usable[s]) for s, t in targets.items() if t > usable[s]),
                  key=lambda g: (g[0] > 0, -score(g[1]), g[0], -g[2]))       # refills, then evidence, then stage
    value = sum(u.get("cost", 0) for u in units)
    balance = inv.get("balance", 0)
    seen = (stocked or set()) | set(usable) | set(sold)
    seen_cats = {catalog[s]["category"] for s in seen if s in catalog}
    fresh = sorted((g for g in gaps if g[1] not in seen),          # never stocked: what exploration is for,
                   key=lambda g: catalog[g[1]]["category"] in seen_cats)   # a category new to us before a second product
    known = [g for g in gaps if g[1] in seen]
    explore = min(len(fresh), max(1, round(lines * EXPLORE_SHARE.get(phase, 0.0)))) if fresh and not wind_down else 0
    buy, spend = [], 0.0

    cap = max(CAP, CAP_SHARE * (balance + value))

    def take(rows, room, limit=float("inf"), most=None):
        nonlocal spend
        most = cap if most is None else most
        for _, sku, need in rows:
            if room <= 0 or len(buy) >= lines:
                return
            each = catalog[sku]["list_price"] * DEAREST
            qty = need
            while qty and (value + spend + each * qty > most or balance - spend - each * qty < RESERVE
                           or spend + each * qty > limit):
                qty -= 1
            if qty:
                buy.append({"sku": sku, "qty": qty})
                spend += each * qty
                room -= 1
    premium = line_gaps(line, catalog, units, sales, now)
    # the premium line first, but leaving the shelf its lines, money and room under the cap, so the shelf's refills
    # always have a share of the run (review 11/4: with 40 s left it took all 4 lines; 11/5: the shelf had halved)
    take([(-1, sku, need) for sku, need in premium], min(LINE_LINES, int(lines * LINE_SHARE), max(1, lines - SHELF_LINES)),
         LINE_SHARE * max(0, balance - RESERVE - SHELF_MONEY), cap - SHELF_ROOM)
    bought_line = len(buy)
    take(known, max(min(SHELF_LINES, lines - bought_line), lines - explore - bought_line))   # refills, the fast sellers stocked deep
    take(fresh, explore)
    take([g for g in known + fresh if g[1] not in {b["sku"] for b in buy}], lines)   # lines left over
    info = {"stage1": len(one), "gate": f"{len(sales)}/{GATE}" + (" open" if gate else ""),
            "targets": len(targets), "gaps": len(gaps), "stock": int(value), "wind_down": wind_down, "lines": lines,
            "new": sum(1 for b in buy if b["sku"] not in seen), "deep": sum(1 for b in buy if b["qty"] > MAX_TARGET),
            "line": bought_line, "line_gaps": len(premium), "cap": int(cap)}
    return {"buy": buy}, info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=str(common.MARKET_DIR))
    state_dir = Path(ap.parse_args().state)
    now = time.time()
    inv = common.read_json(state_dir / "inventory.json", {}) or {}
    catalog = common.catalog(state_dir)
    path = common.MARKET_DIR / "records" / "restock_state.json"
    state = common.read_json(path, {}) or {}
    if inv.get("round", 0) >= 3:
        state.setdefault("round_start", now)
        flow = stock_flow(inv, state, now, check_deals(inv, state, now))
        if flow:
            print(f"[restock] shelf since the last run: {flow}", flush=True)
    sales = common.records("sales")
    wanted = (common.read_json(common.MARKET_DIR / "analysis" / "wanted.json", {}) or {}).get("categories") or {}
    refusals = common.records("refusals")
    if "stocked" not in state:                           # every SKU bought so far; records/restock.jsonl is archived after 48 h
        state["stocked"] = sorted({b.get("sku") for r in common.records("restock") for b in r.get("buy") or [] if b.get("sku")})
    line = load_line(common.read_json(common.MARKET_DIR / "analysis" / "line.json", None))
    out, info = plan(inv, catalog, common.read_json(state_dir / "demand.json", []) or [],
                     common.read_json(common.MARKET_DIR / "market_summary.json", {}) or {},
                     sales, refusals, common.rules()["seller"], now, wanted, common.records("orders"),
                     set(state["stocked"]), common.strategy()["phase"], line)
    state["stocked"] = sorted(set(state["stocked"]) | {b["sku"] for b in out["buy"]})
    print(f"[restock] {info}, buying {len(out['buy'])} line(s)", flush=True)
    if state.get("round_start") and now - state["round_start"] > WARN_AFTER and len(sales) < WARN_BELOW \
            and now - state.get("warned", 0) > 3600:
        print(f"[restock] WARNING: {len(sales)} sale(s) in the first day; look at it at the second enhancement",
              flush=True)
        state["warned"] = now
    if out["buy"] or out.get("bankrupt"):
        common.record("restock", balance=inv.get("balance"), units=len(inv.get("units") or []), **out)
    common.write_json(path, state)
    common.last_line(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
