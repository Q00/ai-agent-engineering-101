"""Market digital twin (CI-1): replays the buyers who came to our shop in a recent window and asks what a
different policy would have earned from the same buyers. Read-only against live state.

Data (all from memory.sqlite, opened read-only or from a temporary copy, plus the kit's inventory/demand files):
  negotiations (role='shop')   every buyer visit: sku, list price, our first ask, the buyer's offers, outcome, style
  settles + shops(mine=1)      our settled sales (units sold per SKU) and the market's prices (median price / list)
  ledger kind='supply'         units the market delivered to our shop (qty, total cost)
  restocks                     what our restock hook asked for (fill rate = received / requested)
  stockouts                    buyers we turned away for lack of stock (demand addressed to us)
  ledger kind='result'         stars our paid deals received
  inventory.json               stock at the end of the window, so the start is reconstructed backwards

One visit, replayed under a policy (pricing.py's pure rules with the policy's constants patched into config):
  ask    = unit_ask(market ratio at the visit, ask_adjustment(outcomes so far)) * m, where m = observed first ask /
           the same formula under live constants and the observed history. m carries what the twin does not
           rebuild (grade factor, warranty price-in, scout premium), so the live replay reproduces the first ask.
  floor  = unit_floor at the visit time, cost of the unit the twin ships.
  hold   = from the buyer style's profile (memory._profile, recomputed in memory over the history so far).
  buyer  = the observed offer path (in list-price ratio): the buyer repeats what it offered live, accepts any
           ask at or below its next observed offer when that is within its reservation price R, and walks when
           the path ends. Deals: R = final ratio + U(0, spread) with spread = the style's close_ratio -
           min_close_ratio (clamped to [0, MAX_SPREAD], DEFAULT_SPREAD without a profile), and the buyer may go
           EXTRA_ROUNDS past its observed path, closing half the gap to the ask up to R. Non-deals: R = the highest
           offer seen, no extra rounds. A buyer that never sent an offer stays a no-deal under any policy.
Stock: the twin ships a unit of the visit's SKU, else a unit of the same category with list price 0.5-1.5x (the
order's acceptable band), negotiation replayed in ratio space. No unit: a stockout (turned away). Turned-away
requests from the log become extra demand when the twin has stock: they replay a seeded random visit of the same
category. Restock: 'actual' replays our real supply deliveries (same for every policy); 'plan' calls
restock_r3.plan(dry_run=True, adjust=...) every SUPPLY_EVERY_S (the market opens supply every 20 min) on the
twin's own stock and balance, with the observed fill rate.
Determinism: every random draw comes from random.Random(f"{seed}:{key}") per visit, so two policies see the same
buyers with the same draws (common random numbers). Reruns with the same --end give the same numbers (QA
2026-11-05): restock_r3.plan runs under frozen_clock(tick ts) (memory.py windows such as price_ratio,
premium_skus and forum_demand_signals use time.time()); the inventory can be a snapshot taken at inventory_ts and
is moved to the window with the rows between; set iteration is sorted; DB rows are read with a full ORDER BY.
"""
import bisect
import contextlib
import json
import math
import random
import shutil
import sqlite3
import statistics
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config, market, pricing

SUPPLY_EVERY_S = 20 * 60
EXTRA_ROUNDS = 2              # a buyer that closed live may make this many offers past its observed path
DEFAULT_SPREAD = 0.02         # reservation headroom (share of list) without a style profile
MAX_SPREAD = 0.05
MAX_SELLER_OFFERS = 8         # shop_r3 rejects after 8 offers
HOLD_FACTOR = 0.98            # shop_r3: hold = list * close_ratio * 0.98 once a style has 3 deals
HOLD_MIN_DEALS = 3
COST_LOOKBACK_H = 24                       # start-stock unit cost: our supplies in the 24 h before the window
PRICE_HOURS = 72              # memory.price_ratio window
OUTCOME_HOURS = 72            # memory.seller_outcomes window
PROFILE_HALF_LIFE_H = 24

PRICING_KEYS = ("ASK_ROOM", "MIN_MARGIN", "START_ASK_RATIO", "CLEARANCE_HOURS", "GRADE_FACTOR")
RESTOCK_KEYS = ("STOCK_BUDGET_SHARE", "STOCK_BUDGET_MAX", "MAX_STOCK_COST", "CASH_RESERVE", "CATEGORY_MAX_SHARE",
                "DEEP_CATEGORY_SHARE", "DEEP_CATEGORY_RATE", "HORIZON_H", "PREMIUM_BUDGET", "PREMIUM_MAX_UNITS",
                "EV_COST_RATIO", "EV_DEFAULT_PRICE_RATIO", "GOODS_END_RISK", "FOOD_LEFTOVER_RISK")
TWIN_KEYS = ("HOLD_FACTOR", "HOLD_MIN_DEALS")


# ------------------------------------------------------------------------------------------- policy
def live_policy():
    """The policy the shop runs now: r3lib/config.py values (read at call time, never written)."""
    p = {"name": "live", "HOLD_FACTOR": HOLD_FACTOR, "HOLD_MIN_DEALS": HOLD_MIN_DEALS,
         "restock_adjust": {}, "category_budget_share": {}, "sku_targets": {}, "cut_skus": {},
         "forum": {}}
    for k in PRICING_KEYS + RESTOCK_KEYS:
        p[k] = getattr(config, k)
    return p


def make_policy(overrides=None, name=None):
    """live_policy() with overrides (a dict, e.g. loaded from a candidate JSON). Unknown keys are refused so a
    typo cannot silently test the live policy."""
    p = live_policy()
    o = dict(overrides or {})
    p["name"] = name or o.pop("name", "candidate")
    o.pop("description", None)
    o.pop("notes", None)
    for k, v in o.items():
        if k not in p:
            raise KeyError(f"unknown policy key {k!r}")
        p[k] = v
    return p


def restock_differs(a, b):
    keys = RESTOCK_KEYS + ("restock_adjust", "category_budget_share", "sku_targets", "cut_skus")
    return any(a.get(k) != b.get(k) for k in keys)


@contextlib.contextmanager
def patched(policy):
    """Policy constants in r3lib.config for the duration of one replay (in-process only, restored after)."""
    saved = {}
    try:
        for k in PRICING_KEYS + RESTOCK_KEYS:
            if k in policy:
                saved[k] = getattr(config, k)
                setattr(config, k, policy[k])
        yield
    finally:
        for k, v in saved.items():
            setattr(config, k, v)


def match_category(key, category):
    """A policy key names a category: the full path, or a part of it below the top level ('매트리스', '침구',
    '김치'), so '침구' matches '가구/침구>침구>이불' but not '가구/침구>가구>침대/매트리스'."""
    if not key or not category:
        return False
    if key == category:
        return True
    return key in category.split(">", 1)[-1]


def lookup_category(mapping, category, default=None):
    for k, v in (mapping or {}).items():
        if match_category(k, category):
            return v
    return default


# ------------------------------------------------------------------------------------------- data
def open_readonly(db_path):
    """Read-only connection: sqlite URI mode=ro (never creates or writes the file)."""
    c = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=30)
    c.row_factory = sqlite3.Row
    return c


SNAPSHOT_STATE_TABLES = ("shops", "shop_stock")     # latest-state upserts: kept in the plan snapshot


def snapshot_db(db_path, dest=None, end=None):
    """Consistent copy of the live DB (sqlite backup API from a read-only connection) for code that needs a
    Memory object (restock_r3.plan). With `end`, every event row with ts > end is deleted from the copy (no
    look-ahead past the window). shops and shop_stock (SNAPSHOT_STATE_TABLES) are latest-state upserts whose ts is
    the last sync, not an event time: deleting them would drop every shop's stock, so they are kept as they are
    (a known, small look-ahead: the latest competitor stock). Returns the copy's path; the caller deletes it (DefaultPlan does). If the copy fails, a temp
    dir made here is removed before the error is raised."""
    made = dest is None
    dest_dir = Path(dest or tempfile.mkdtemp(prefix="twin-"))
    path = dest_dir / "memory.sqlite"
    try:
        src = open_readonly(db_path)
        dst = sqlite3.connect(path)
        try:
            src.backup(dst)
            if end is not None:
                tables = [r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                for t in tables:
                    if t in SNAPSHOT_STATE_TABLES:
                        continue
                    if any(c[1] == "ts" for c in dst.execute(f'PRAGMA table_info("{t}")')):
                        dst.execute(f'DELETE FROM "{t}" WHERE ts > ?', (end,))
                dst.commit()
        finally:
            dst.close()
            src.close()
    except BaseException:
        if made:
            shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    return path


@dataclass
class Visit:
    id: object
    ts: float
    sku: str
    category: str
    list_price: int
    first_ask: int
    final_price: object
    offers: list
    outcome: str
    style: object
    kind: str = "goods"
    synthetic: bool = False


@dataclass
class Window:
    start: float
    end: float
    visits: list
    history: list                          # negotiations before the window (for adjust and style profiles)
    stockouts: list                        # (ts, category, sku)
    supplies: list                         # (ts, sku, qty, unit_cost)
    start_stock: dict                      # sku -> [unit costs]
    unit_cost: dict                        # sku -> average cost of our supplied units
    price_index: dict                      # ('sku'|'category', key) -> (sorted ts list, ratios list)
    catalog: dict
    balance_end: float = 0.0
    fill_rate: float = 0.6
    stars: object = None
    star_count: int = 0
    actual: dict = field(default_factory=dict)
    demand_rows: list = field(default_factory=list)
    db_path: object = None
    notes: list = field(default_factory=list)
    end_source: str = "argument"           # "argument" (--end / end=) or "last negotiation" (resolved default)

    def market_ratio(self, sku, category, ts, hours=PRICE_HOURS, min_n=3):
        """memory.price_ratio at time ts: median settled price / list for the SKU, else the category."""
        for key in (("sku", sku), ("category", category)):
            row = self.price_index.get(key)
            if not row:
                continue
            tss, ratios = row
            lo, hi = bisect.bisect_right(tss, ts - hours * 3600), bisect.bisect_right(tss, ts)
            if hi - lo >= min_n:
                return statistics.median(ratios[lo:hi])
        return None


def _json(text, default):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


@contextlib.contextmanager
def frozen_clock(ts):
    """time.time() returns ts inside the block (this process only). restock_r3.plan and memory.py read the wall
    clock for their look-back windows; the twin's clock is the tick time, so a rerun later gives the same plan."""
    real = time.time
    time.time = lambda: float(ts)
    try:
        yield
    finally:
        time.time = real


def _moves(c, mine, names, a, b):
    """Our sold units, settle revenue, supplied units and supply cost in (a, b], per SKU."""
    sold, supplied, revenue, cost = {}, {}, 0, 0.0
    if mine and b > a:
        q = f"SELECT sku, amount FROM settles WHERE ts>? AND ts<=? AND shop IN ({','.join('?' * len(mine))})"
        for r in c.execute(q + " ORDER BY ts, rowid", (a, b, *sorted(mine))):
            sold[r["sku"]] = sold.get(r["sku"], 0) + 1
            revenue += r["amount"] or 0
    if b > a:
        for r in c.execute("SELECT data FROM ledger WHERE kind='supply' AND ts>? AND ts<=? ORDER BY ts, rowid", (a, b)):
            d = _json(r["data"], {})
            if d.get("shop") in names and d.get("qty"):
                supplied[d.get("sku")] = supplied.get(d.get("sku"), 0) + int(d["qty"])
                cost += d.get("total") or 0
    return sold, revenue, supplied, cost


def load_window(db_path=None, hours=6, end=None, inventory=None, catalog=None, demand_rows=None, inventory_ts=None):
    """Everything the twin replays for [end - hours, end], read-only. inventory: the kit's inventory.json dict;
    None reads $MARKET_DIR/inventory.json. inventory_ts: when that inventory was taken. None = at `end` (old
    behaviour). Else the stock and balance are moved from inventory_ts to `end` with our settles and supplies in
    between, so a pinned snapshot gives the same window on every rerun."""
    db_path = Path(db_path or config.DB_PATH)
    cat = catalog if catalog is not None else market.catalog()
    inv = inventory if inventory is not None else (market.read_json("inventory.json", {}) or {})
    if demand_rows is None:
        demand_rows = market.read_json("demand.json", []) or []
        if isinstance(demand_rows, dict):
            demand_rows = demand_rows.get("categories") or demand_rows.get("demand") or []
    c = open_readonly(db_path)
    try:
        end_source = "argument" if end else "last negotiation"
        end = end or c.execute("SELECT MAX(ts) t FROM negotiations").fetchone()["t"] or time.time()
        start = end - hours * 3600
        mine = {r["shop_url"] for r in c.execute("SELECT shop_url FROM shops WHERE mine=1")}
        names = set(config.MY_NAMES) | {r["name"] for r in c.execute("SELECT name FROM shops WHERE mine=1")}

        visits, history = [], []
        for r in c.execute("SELECT * FROM negotiations WHERE role='shop' AND ts>? AND ts<=? ORDER BY ts, id",
                           (start - OUTCOME_HOURS * 3600, end)):
            info = cat.get(r["sku"]) or {}
            v = Visit(id=r["id"], ts=r["ts"], sku=r["sku"], category=r["category"] or info.get("category"),
                      list_price=r["list_price"] or info.get("list_price") or 0, first_ask=r["first_ask"],
                      final_price=r["final_price"], offers=[int(x) for x in _json(r["offers"], []) if x],
                      outcome=r["outcome"], style=r["style"], kind=info.get("kind") or "goods")
            if not v.list_price:
                continue
            (visits if v.ts > start else history).append(v)

        price_index = {}
        for r in c.execute("SELECT ts, sku, category, amount, list_price FROM settles WHERE ts>? AND ts<=? "
                           "AND list_price>0 AND amount>0 ORDER BY ts, rowid", (start - PRICE_HOURS * 3600, end)):
            ratio = r["amount"] / r["list_price"]
            for key in (("sku", r["sku"]), ("category", r["category"])):
                if key[1]:
                    row = price_index.setdefault(key, ([], []))
                    row[0].append(r["ts"])
                    row[1].append(ratio)

        sold = {}
        revenue = 0
        if mine:
            q = f"SELECT sku, amount FROM settles WHERE ts>? AND ts<=? AND shop IN ({','.join('?' * len(mine))})"
            for r in c.execute(q + " ORDER BY ts, rowid", (start, end, *sorted(mine))):
                sold[r["sku"]] = sold.get(r["sku"], 0) + 1
                revenue += r["amount"] or 0

        # supply prices drift a lot over the round (G0be9ed07 went from ~10k to ~35k a unit), so costs come
        # from recent supplies: the window itself, else the COST_LOOKBACK_H before it, else anything older
        supplies, cost_win, cost_pre, cost_old = [], {}, {}, {}
        for r in c.execute("SELECT ts, data FROM ledger WHERE kind='supply' AND ts<=? ORDER BY ts, rowid", (end,)):
            d = _json(r["data"], {})
            if d.get("shop") not in names or not d.get("qty"):
                continue
            uc = (d.get("total") or 0) / d["qty"]
            bucket = cost_win if r["ts"] > start else cost_pre if r["ts"] > start - COST_LOOKBACK_H * 3600 \
                else cost_old
            s = bucket.setdefault(d.get("sku"), [0.0, 0])
            s[0] += uc * d["qty"]
            s[1] += d["qty"]
            if start < r["ts"] <= end:
                supplies.append((r["ts"], d.get("sku"), int(d["qty"]), uc))
        avg = lambda b: {k: v[0] / v[1] for k, v in b.items() if v[1]}
        win, pre, old = avg(cost_win), avg(cost_pre), avg(cost_old)
        unit_cost = {**old, **pre, **win}                    # restock / ask model: the latest price we paid
        start_cost = {**old, **win, **pre}                   # units already on the shelf at the window start
        requested = c.execute("SELECT SUM(qty) q FROM restocks WHERE ts>? AND ts<=?", (start, end)).fetchone()["q"]
        received = sum(q for _, _, q, _ in supplies)

        stockouts = [(r["ts"], r["category"], r["sku"]) for r in c.execute(
            "SELECT ts, category, sku FROM stockouts WHERE ts>? AND ts<=? ORDER BY ts, rowid", (start, end))]

        stars = []
        for r in c.execute("SELECT json_extract(data, '$.shop') shop, json_extract(data, '$.stars') stars "
                           "FROM ledger WHERE kind='result' AND ts>? AND ts<=? ORDER BY ts, rowid", (start, end)):
            if r["stars"] is not None and (r["shop"] or "").rstrip("/") in mine:
                stars.append(r["stars"])
        # inventory taken at inventory_ts, not at end: units_end = units_inv - sold(inv, end] + supplied(inv, end]
        shift_sold, shift_sup, shift_cash = {}, {}, 0.0
        if inventory_ts is not None and inventory_ts != end:
            a, b, sign = (inventory_ts, end, 1) if inventory_ts < end else (end, inventory_ts, -1)
            s_sold, s_rev, s_sup, s_cost = _moves(c, mine, names, a, b)
            shift_sold = {k: sign * v for k, v in s_sold.items()}
            shift_sup = {k: sign * v for k, v in s_sup.items()}
            shift_cash = sign * (s_rev - s_cost)
    finally:
        c.close()

    # stock at the start = stock at the end + units sold in the window - units supplied in the window
    end_units = {}
    for u in inv.get("units") or []:
        if u.get("grade") != "expired" and u.get("sku"):
            end_units.setdefault(u["sku"], []).append(u.get("cost") or 0)
    supplied = {}
    for _, sku, q, _ in supplies:
        supplied[sku] = supplied.get(sku, 0) + q
    start_stock = {}
    for sku in sorted(set(end_units) | set(sold) | set(supplied) | set(shift_sold) | set(shift_sup)):
        n = len(end_units.get(sku, [])) - shift_sold.get(sku, 0) + shift_sup.get(sku, 0) \
            + sold.get(sku, 0) - supplied.get(sku, 0)
        if n <= 0:
            continue
        info = cat.get(sku) or {}
        base = start_cost.get(sku) or (statistics.mean(end_units[sku]) if end_units.get(sku) else None) or \
            (info.get("list_price") or 0) * config.EV_COST_RATIO
        known = sorted(end_units.get(sku, []))[:n]
        start_stock[sku] = known + [base] * (n - len(known))

    deals = [v for v in visits if v.outcome == "deal" and v.final_price]
    negotiated = sum(1 for v in visits if v.offers)
    # one close-rate definition for observed and twin (QA 2026-11-05): close_rate = deals / negotiations, a
    # negotiation = a session where the buyer made at least one offer; served_close_rate = deals / sessions
    # (observed: every negotiations row; twin: every visit that got a unit)
    actual = {"visits": len(visits), "deals": len(deals), "stockouts": len(stockouts), "negotiations": negotiated,
              "close_rate": round(len(deals) / negotiated, 4) if negotiated else None,
              "served_close_rate": round(len(deals) / len(visits), 4) if visits else None,
              "final_price_ratio": round(statistics.mean(v.final_price / v.list_price for v in deals), 4)
              if deals else None,
              "first_ask_ratio": round(statistics.mean(v.first_ask / v.list_price for v in visits if v.first_ask), 4)
              if visits else None,
              "deal_revenue": sum(v.final_price for v in deals), "settled_revenue": revenue,
              "revenue_basis": "deal_revenue = final prices of negotiations rows with outcome deal; "
                               "settled_revenue = settles rows of our shop in the window",
              "settled_units": sum(sold.values()), "supplied_units": received, "requested_units": requested or 0,
              "silent_visits": sum(1 for v in visits if not v.offers)}
    w = Window(start=start, end=end, visits=visits, history=history, stockouts=stockouts, supplies=supplies,
               start_stock=start_stock, unit_cost=unit_cost, price_index=price_index, catalog=cat,
               balance_end=float(inv.get("balance") or 0) + shift_cash,
               fill_rate=min(1.0, max(0.1, received / requested)) if requested else 0.6,
               stars=round(statistics.mean(stars), 3) if stars else None, star_count=len(stars), actual=actual,
               demand_rows=list(demand_rows), db_path=db_path, end_source=end_source)
    if not inv.get("units"):
        w.notes.append("no inventory snapshot: start stock rebuilt from sales and supplies only")
    if inventory_ts is not None and inventory_ts != end:
        w.notes.append(f"inventory taken at {inventory_ts:.0f}, moved {end - inventory_ts:+.0f} s to the window end "
                       f"with our settles and supplies in between")
    return w


# ------------------------------------------------------------------------------------------- buyer model
def style_profile(records, now, half_life_h=PROFILE_HALF_LIFE_H):
    """memory.Memory._profile over in-memory records [(ts, final_price, list_price, n_offers, outcome)]."""
    w_all = w_deal = 0.0
    ratios, rounds = [], []
    for ts, final, lp, n_off, outcome in records:
        w = 0.5 ** ((now - ts) / 3600 / half_life_h)
        w_all += w
        if outcome == "deal" and final and lp:
            w_deal += w
            ratios.append((final / lp, w))
            rounds.append(n_off)
    return {"n": len(records), "deals": len(ratios), "deal_rate": round(w_deal / w_all, 3) if w_all else None,
            "close_ratio": round(_weighted_median(ratios), 3) if ratios else None,
            "min_close_ratio": round(min(r for r, _ in ratios), 3) if ratios else None,
            "rounds": statistics.median(rounds) if rounds else None}


def _weighted_median(pairs):
    pairs = sorted(pairs)
    half, acc = sum(w for _, w in pairs) / 2, 0.0
    for v, w in pairs:
        acc += w
        if acc >= half:
            return v
    return pairs[-1][0]


def reservation(visit, profile, rng):
    """Most the buyer would pay, as a share of list. Deals: the price it paid plus seeded headroom from its
    style's spread; non-deals: its highest offer; silent buyers: None."""
    if not visit.offers:
        return None
    if visit.outcome == "deal" and visit.final_price:
        spread = DEFAULT_SPREAD
        if profile and profile.get("close_ratio") and profile.get("min_close_ratio") is not None:
            spread = min(MAX_SPREAD, max(0.0, profile["close_ratio"] - profile["min_close_ratio"]))
        return visit.final_price / visit.list_price + rng.uniform(0, spread)
    return max(visit.offers) / visit.list_price


def negotiate(ask, floor, path, r_max, extra_rounds, hold=None):
    """Replay one buyer against the shop's concession rule (pricing.seller_reply), all in credits.
    path: the buyer's observed offers (already scaled); r_max: reservation in credits or None.
    Returns (outcome, price, n_offers)."""
    if r_max is None or not path:
        return "walk", None, 0
    offers, current, last = [], ask, None
    limit = min(MAX_SELLER_OFFERS, len(path) + extra_rounds)
    for k in range(limit):
        if current <= r_max and (k >= len(path) or current <= path[k]):
            offer = current                                        # the ask is within reach: the buyer takes it
        elif k < len(path):
            offer = path[k]
        else:
            nxt = int(min(r_max, last + (current - last) * 0.5))
            if nxt <= last:
                return "walk", None, len(offers)
            offer = nxt
        offers.append(offer)
        last = offer
        action, price = pricing.seller_reply(current, floor, offer, len(offers), hold=hold)
        if action == "accept":
            return "deal", int(price), len(offers)
        current = price
    if len(offers) < MAX_SELLER_OFFERS and current <= r_max:
        return "deal", int(current), len(offers) + 1                # the last counter is within reach: take it
    return ("reject" if len(offers) >= MAX_SELLER_OFFERS else "walk"), None, len(offers)


# ------------------------------------------------------------------------------------------- replay
class _Stock:
    def __init__(self, start_stock, catalog):
        self.units = {sku: sorted(costs) for sku, costs in start_stock.items() if costs}
        self.cat = catalog

    def take(self, sku, category, list_price):
        """A unit for the order: the SKU, else the same category within 0.5-1.5x the list price (the order's
        acceptable band). Returns (sku, cost) or None."""
        if self.units.get(sku):
            return sku, self.units[sku].pop(0)
        best = None
        for s, costs in self.units.items():
            info = self.cat.get(s) or {}
            if costs and info.get("category") == category and \
                    0.5 * list_price <= (info.get("list_price") or 0) <= 1.5 * list_price:
                d = abs(info["list_price"] - list_price)
                if best is None or d < best[0]:
                    best = (d, s)
        if best:
            return best[1], self.units[best[1]].pop(0)
        return None

    def put_back(self, sku, cost):
        self.units.setdefault(sku, []).append(cost)
        self.units[sku].sort()

    def add(self, sku, qty, cost):
        self.units.setdefault(sku, []).extend([cost] * qty)
        self.units[sku].sort()

    def cost(self):
        return sum(sum(v) for v in self.units.values())

    def count(self):
        return sum(len(v) for v in self.units.values())

    def goods_cost(self):
        return sum(sum(v) for s, v in self.units.items() if (self.cat.get(s) or {}).get("kind") != "food")

    def as_inventory(self, balance):
        units = []
        for s, costs in self.units.items():
            info = self.cat.get(s) or {}
            for i, cst in enumerate(costs):
                units.append({"unit": f"twin-{s}-{i}", "sku": s, "category": info.get("category"),
                              "list_price": info.get("list_price"), "kind": info.get("kind"), "cost": cst,
                              "grade": "A"})
        return {"balance": balance, "units": units}


def _unit(list_price, cost, kind):
    return {"list_price": list_price, "cost": cost, "kind": kind, "grade": "A"}


def _adjust(outcomes, category):
    """pricing.ask_adjustment over our outcomes in the category (else all), as memory.seller_outcomes feeds it."""
    rows = outcomes.get(category) or outcomes.get(None) or []
    return pricing.ask_adjustment([{"outcome": o} for _, o in rows])


def _trim(rows, now, hours=OUTCOME_HOURS):
    cut = now - hours * 3600
    while rows and rows[0][0] <= cut:
        rows.pop(0)


def _ask_multiplier(w, v, live, base_outcomes):
    """Observed first ask / live formula's ask, both on the observed history: what the twin does not rebuild."""
    if not v.first_ask:
        return 1.0
    ratio = w.market_ratio(v.sku, v.category, v.ts)
    cost = w.unit_cost.get(v.sku) or v.list_price * config.EV_COST_RATIO
    with patched(live):
        model = pricing.unit_ask(_unit(v.list_price, cost, v.kind), ratio, _adjust(base_outcomes, v.category))
    return min(1.5, max(0.5, v.first_ask / model)) if model else 1.0


def _demand_events(w, seed):
    """Visits plus turned-away requests (as seeded copies of a visit in the same category), sorted by time."""
    events = [("visit", v.ts, v) for v in w.visits]
    by_cat = {}
    for v in w.visits:
        if v.offers:
            by_cat.setdefault(v.category, []).append(v)
    every = [v for v in w.visits if v.offers]
    for i, (ts, category, sku) in enumerate(w.stockouts):
        pool = by_cat.get(category) or every
        if not pool:
            continue
        t = random.Random(f"{seed}:stockout:{i}").choice(pool)
        info = w.catalog.get(sku) or {}
        lp = info.get("list_price") or t.list_price
        scale = lp / t.list_price
        events.append(("visit", ts, Visit(
            id=f"so{i}", ts=ts, sku=sku or t.sku, category=category, list_price=lp,
            first_ask=int(t.first_ask * scale) if t.first_ask else None,
            final_price=int(t.final_price * scale) if t.final_price else None,
            offers=[int(o * scale) for o in t.offers], outcome=t.outcome, style=t.style,
            kind=info.get("kind") or t.kind, synthetic=True)))
    return events


def replay(w, policy, seed=0, restock="actual", live=None, plan_fn=None, keep_visits=False):
    """Run one policy over the window. Returns the metrics dict (see metrics keys below). In plan mode without a
    plan_fn, one DefaultPlan (one DB copy) serves every tick and is removed when the replay ends, also on error."""
    if restock != "actual" and plan_fn is None:
        with DefaultPlan(w) as own:
            return _replay(w, policy, seed, restock, live, own, keep_visits)
    return _replay(w, policy, seed, restock, live, plan_fn, keep_visits)


def _replay(w, policy, seed, restock, live, plan_fn, keep_visits):
    live = live or live_policy()
    base_outcomes, sim_outcomes, base_styles = {}, {}, {}
    for h in w.history:
        for k in (h.category, None):
            base_outcomes.setdefault(k, []).append((h.ts, h.outcome))
        base_styles.setdefault(h.style, []).append((h.ts, h.final_price, h.list_price, len(h.offers), h.outcome))
    sim_outcomes = {k: list(v) for k, v in base_outcomes.items()}
    sim_styles = {k: list(v) for k, v in base_styles.items()}

    stock = _Stock(w.start_stock, w.catalog)
    balance0 = w.balance_end - (w.actual.get("settled_revenue") or 0) + sum(q * c for _, _, q, c in w.supplies)
    m = {"visits": 0, "served": 0, "negotiated": 0, "stockouts": 0, "stockout_requests_served": 0, "deals": 0,
         "revenue": 0, "cogs": 0.0, "supply_spend": 0.0, "storage": 0.0, "price_ratio_sum": 0.0,
         "margin_ratio_sum": 0.0, "first_ask_ratio_sum": 0.0, "silent": 0, "restock_units": 0}
    log = []
    events = _demand_events(w, seed)
    if restock == "actual":
        events += [("supply", ts, (sku, q, c)) for ts, sku, q, c in w.supplies]
    else:
        t = w.start
        while t < w.end:
            events.append(("tick", t, None))
            t += SUPPLY_EVERY_S
    order = {"supply": 0, "tick": 0, "visit": 1}
    events.sort(key=lambda e: (e[1], order[e[0]], str(getattr(e[2], "id", ""))))
    last_ts = w.start
    cash = balance0
    cut_state = {}                                   # cut_skus: units skipped so far in this replay

    for kind, ts, ev in events:
        m["storage"] += stock.goods_cost() * 0.01 * (ts - last_ts) / 86400       # goods pay 1% of cost per day
        last_ts = ts
        if kind == "supply":
            sku, q, c = ev
            stock.add(sku, q, c)
            m["supply_spend"] += q * c
            m["restock_units"] += q
            cash -= q * c
            continue
        if kind == "tick":
            got = _restock_tick(w, policy, stock, cash, ts, seed, plan_fn, cut_state)
            for sku, q, c in got:
                stock.add(sku, q, c)
                m["supply_spend"] += q * c
                m["restock_units"] += q
                cash -= q * c
            continue
        v = ev
        m["visits"] += 1
        unit = stock.take(v.sku, v.category, v.list_price)
        if unit is None:
            m["stockouts"] += 1
            if keep_visits:
                log.append({"id": v.id, "outcome": "stockout"})
            continue
        sku, cost = unit
        m["served"] += 1
        if v.synthetic:
            m["stockout_requests_served"] += 1
        lp = (w.catalog.get(sku) or {}).get("list_price") or v.list_price
        scale = lp / v.list_price
        rng = random.Random(f"{seed}:visit:{v.id}")
        with patched(policy):
            ratio = w.market_ratio(sku, v.category, v.ts)
            mult = _ask_multiplier(w, v, live, base_outcomes)
            u = _unit(lp, cost, v.kind)
            ask = int(min(lp, pricing.unit_ask(u, ratio, _adjust(sim_outcomes, v.category)) * mult))
            floor = pricing.unit_floor(u, now=v.ts, hours_left_round=config.hours_left_in_round(v.ts))
            ask = max(ask, int(floor * 1.05))
        prof = style_profile(sim_styles.get(v.style, []), v.ts) if v.style else None
        hold = None
        if prof and prof["deals"] >= policy.get("HOLD_MIN_DEALS", HOLD_MIN_DEALS) and prof["close_ratio"]:
            hold = max(floor, int(lp * prof["close_ratio"] * policy.get("HOLD_FACTOR", HOLD_FACTOR)))
        r = reservation(v, style_profile(base_styles.get(v.style, []), v.ts) if v.style else None, rng)
        path = [int(o * scale) for o in v.offers]
        extra = EXTRA_ROUNDS if v.outcome == "deal" else 0
        outcome, price, n_off = negotiate(ask, floor, path, None if r is None else r * lp, extra, hold)
        if not v.offers:
            m["silent"] += 1
        else:
            m["negotiated"] += 1
        m["first_ask_ratio_sum"] += ask / lp
        if outcome == "deal":
            m["deals"] += 1
            m["revenue"] += price
            m["cogs"] += cost
            cash += price
            m["price_ratio_sum"] += price / lp
            m["margin_ratio_sum"] += (price - cost) / lp
        else:
            stock.put_back(sku, cost)
        rec = "deal" if outcome == "deal" else "reject"
        for k in (v.category, None):
            sim_outcomes.setdefault(k, []).append((v.ts, rec))
            _trim(sim_outcomes[k], v.ts)
        if v.style:
            sim_styles.setdefault(v.style, []).append((v.ts, price, lp, n_off, outcome))
        if keep_visits:
            log.append({"id": v.id, "sku": sku, "ask": ask, "floor": floor, "hold": hold, "outcome": outcome,
                        "price": price, "cost": round(cost)})
    m["storage"] += stock.goods_cost() * 0.01 * (w.end - last_ts) / 86400
    return _metrics(m, stock, cash, balance0, w, policy, restock, log if keep_visits else None)


def _restock_tick(w, policy, stock, cash, ts, seed, plan_fn, cut_state=None):
    """One supply window: restock_r3.plan on the twin's stock and cash (policy constants patched), the lines
    filled at the observed fill rate, each unit at our average cost for that SKU."""
    adjust = expand_adjust(policy.get("restock_adjust"), w.catalog)
    with contextlib.ExitStack() as stack:
        if plan_fn is None:                        # direct callers only; replay always passes one
            plan_fn = stack.enter_context(DefaultPlan(w))
        with patched(effective_plan_policy(policy)):
            lines = plan_fn(stock.as_inventory(cash), ts, adjust, policy)
    lines = _apply_cuts(lines, policy, cut_state)
    lines = _apply_sku_targets(lines, stock, cash, policy, w)
    lines = _apply_category_shares(lines, stock, cash, policy, w.catalog)
    rng = random.Random(f"{seed}:tick:{int(ts)}")
    got = []
    for ln in lines:
        info = w.catalog.get(ln["sku"]) or {}
        c = w.unit_cost.get(ln["sku"]) or (info.get("list_price") or 0) * policy.get("EV_COST_RATIO", 0.62)
        q = sum(1 for _ in range(int(ln["qty"])) if rng.random() < w.fill_rate)
        if q and q * c <= cash - policy.get("CASH_RESERVE", 0):
            got.append((ln["sku"], q, c))
            cash -= q * c
    return got


def _apply_cuts(lines, policy, cut_state):
    """cut_skus {sku: n}: the first n units plan asks for of that SKU over the window are not bought."""
    cuts = policy.get("cut_skus") or {}
    if not cuts:
        return lines
    cut_state = {} if cut_state is None else cut_state
    out = []
    for ln in lines:
        left = int(cuts.get(ln["sku"], 0)) - cut_state.get(ln["sku"], 0)
        q = int(ln["qty"])
        if left > 0:
            drop = min(left, q)
            cut_state[ln["sku"]] = cut_state.get(ln["sku"], 0) + drop
            q -= drop
        if q > 0:
            out.append({**ln, "qty": q})
    return out


def _apply_sku_targets(lines, stock, cash, policy, w):
    """sku_targets {sku: units}: top each SKU up to its target (held + planned), while the stock cost at the
    restock_r3 estimate (list x 0.75) stays under the stock budget cap (STOCK_BUDGET_MAX)."""
    targets = policy.get("sku_targets") or {}
    if not targets:
        return lines
    stock_max = min(policy["STOCK_BUDGET_MAX"], max(policy["MAX_STOCK_COST"], policy["STOCK_BUDGET_SHARE"] * cash))
    est = lambda sku: ((w.catalog.get(sku) or {}).get("list_price") or 0) * 0.75
    total = stock.cost() + sum(est(ln["sku"]) * int(ln["qty"]) for ln in lines)
    planned = {}
    for ln in lines:
        planned[ln["sku"]] = planned.get(ln["sku"], 0) + int(ln["qty"])
    out = list(lines)
    for sku, target in sorted(targets.items()):
        e = est(sku)
        need = int(target) - len(stock.units.get(sku) or []) - planned.get(sku, 0)
        if need <= 0 or not e or sku not in w.catalog:
            continue
        q = min(need, int(max(0, stock_max - total) // e))
        if q > 0:
            out.append({"sku": sku, "qty": q})
            total += q * e
    return out


def _apply_category_shares(lines, stock, cash, policy, catalog):
    """category_budget_share: a category's stock cost (held + bought this tick) stays under share x stock budget.
    Lets a candidate cap some categories below plan's global CATEGORY_MAX_SHARE (raising others is done by the
    global share, see effective_plan_policy)."""
    shares = policy.get("category_budget_share") or {}
    if not shares:
        return lines
    stock_max = min(policy["STOCK_BUDGET_MAX"], max(policy["MAX_STOCK_COST"], policy["STOCK_BUDGET_SHARE"] * cash))
    held = {}
    for s, costs in stock.units.items():
        k = (catalog.get(s) or {}).get("category")
        held[k] = held.get(k, 0) + sum(costs)
    out = []
    for ln in lines:
        info = catalog.get(ln["sku"]) or {}
        k = info.get("category")
        share = lookup_category(shares, k)
        if share is None:
            out.append(ln)
            continue
        est = (info.get("list_price") or 0) * 0.75                     # restock_r3.unit_cost_estimate
        room = int(max(0, share * stock_max - held.get(k, 0)) // est) if est else ln["qty"]
        q = min(ln["qty"], room)
        if q > 0:
            out.append({"sku": ln["sku"], "qty": q})
            held[k] = held.get(k, 0) + q * est
    return out


def expand_adjust(adjust, catalog):
    """restock_adjust keys (full category or a part of it) -> {full category: {"factor": f}}, the shape
    restock_r3.plan reads (active_adjustments' items)."""
    out = {}
    for category in sorted({(i or {}).get("category") for i in catalog.values()} - {None}):
        f = lookup_category(adjust, category)
        if f is not None:
            out[category] = {"factor": f}
    return out


def effective_plan_policy(policy):
    """plan() has one global category cap; a candidate that gives some category a larger share raises it (both
    CATEGORY_MAX_SHARE and DEEP_CATEGORY_SHARE) and _apply_category_shares keeps every other category at the
    live share."""
    p = dict(policy)
    shares = dict(p.get("category_budget_share") or {})
    top = max(shares.values()) if shares else 0
    if top > p["CATEGORY_MAX_SHARE"] or top > p["DEEP_CATEGORY_SHARE"]:
        p["CATEGORY_MAX_SHARE"] = max(p["CATEGORY_MAX_SHARE"], top)
        p["DEEP_CATEGORY_SHARE"] = max(p["DEEP_CATEGORY_SHARE"], top)
        p["_default_share"] = (policy["CATEGORY_MAX_SHARE"], policy["DEEP_CATEGORY_SHARE"])
    return p


class DefaultPlan:
    """restock_r3.plan over ONE private copy of the DB (Memory needs a writable file; the live DB is never opened
    for writing). The copy is made on the first tick, holds no row after w.end (no look-ahead), and is shared by
    every later tick and replay that uses this object. plan(dry_run=True) does not write to it. close() (or the
    with-block) removes the temp dir, also on an exception or KeyboardInterrupt."""

    def __init__(self, w):
        self.w = w
        self.mem = None
        self.dir = None
        self.snapshots = 0

    def __call__(self, inv, ts, adjust, policy):
        import restock_r3
        from . import scout
        from .memory import Memory
        if self.mem is None:
            self.dir = Path(tempfile.mkdtemp(prefix="twin-"))
            try:
                self.mem = Memory(snapshot_db(self.w.db_path, self.dir, end=self.w.end))
            except BaseException:
                self.close()
                raise
            self.snapshots += 1
        scout._CACHE.clear()                       # its cache is keyed on wall time; the twin's clock jumps
        with frozen_clock(ts):                     # memory.py look-backs use time.time(), not `now`
            lines, _, _ = restock_r3.plan(inv, self.w.demand_rows, self.mem, now=ts, dry_run=True, adjust=adjust)
        return lines

    def close(self):
        d, self.dir, self.mem = self.dir, None, None
        if d is not None:
            shutil.rmtree(d, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _default_plan(w):
    return DefaultPlan(w)


def _metrics(m, stock, cash, balance0, w, policy, restock, log):
    """book = cash + unsold stock at cost (the board's ALL uses book). Window effect: book_delta =
    revenue - cost of units sold - storage (buying stock moves cash into stock and leaves book unchanged)."""
    deals = m["deals"]
    book_end = cash - m["storage"] + stock.cost()
    out = {
        "policy": policy.get("name"), "restock_mode": restock,
        "visits": m["visits"], "served": m["served"], "negotiated": m["negotiated"], "silent": m["silent"],
        "stockouts": m["stockouts"], "stockout_requests_served": m["stockout_requests_served"],
        "deals": deals, "negotiations": m["negotiated"],
        "close_rate": round(deals / m["negotiated"], 4) if m["negotiated"] else None,      # deals / negotiations
        "served_close_rate": round(deals / m["served"], 4) if m["served"] else None,       # deals / served
        "revenue": int(m["revenue"]), "deal_revenue": int(m["revenue"]), "revenue_basis": "deal",
        "cogs": int(m["cogs"]), "storage": int(m["storage"]),
        "profit": int(m["revenue"] - m["cogs"] - m["storage"]),
        "book_delta": int(m["revenue"] - m["cogs"] - m["storage"]),
        "book_end": int(book_end), "cash_end": int(cash - m["storage"]), "stock_cost_end": int(stock.cost()),
        "stock_units_end": stock.count(), "supply_spend": int(m["supply_spend"]), "restock_units": m["restock_units"],
        "profit_per_visit": round((m["revenue"] - m["cogs"]) / m["visits"], 1) if m["visits"] else None,
        "margin_per_visit_ratio": round(m["margin_ratio_sum"] / m["visits"], 4) if m["visits"] else None,
        "final_price_ratio": round(m["price_ratio_sum"] / deals, 4) if deals else None,
        "first_ask_ratio": round(m["first_ask_ratio_sum"] / m["served"], 4) if m["served"] else None,
        "stars": w.stars,
    }
    if log is not None:
        out["visit_log"] = log
    return out
