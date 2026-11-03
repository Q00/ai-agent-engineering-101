"""Round 3 restock hook (S1, and the hourly ledger collection): the kit runs it every 20 minutes
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
            to 20 minutes); +1 if a buyer was turned away for lack of it in the last day; at most 4
  order     only 10 lines a run, so what comes first matters while the first day's shelf fills
            (about 12 runs): refills of what we sold, then the products with the most evidence
            of demand (the market's sales of the category in the last day, our buyer's orders for
            it, a wanted post), then the stage order. With no evidence yet it is the stage order,
            which brings one product of every group (the catalog's top level: 식품, 가구/침구,
            의류, ...) in the first runs and then mixes the groups in proportion to their size, so
            each run samples the whole catalog; the market's list is in alphabetical order, which
            would bring furniture first and kitchenware hours later.
  limits    stock at cost up to 2,500,000 and the balance kept at 2,500,000 or more,
            planning as if every unit came in at the dearest cost (75% of list)
  wanted    a category the analysis found wanted (another agent's "wanted" post on the
            forum, or one our buyer found no shop for) gets its representative product
Supply windows (the market's rule from the evening of 11/3): supply opens for everyone for 3 minutes
every 20 (the first window 10 minutes); outside one the market refuses (429), and inventory.json
carries restock {open, opens_at, closes_at}. A run with the window shut and not opening within
SUPPLY_LEAD buys nothing; otherwise it plans only the lines the window leaves time for (one supply
call per SUPPLY_GAP seconds), the most needed first.
Rules in force (analysis/active_rules.json, which the integrated analysis writes only in
the focus phase): focus categories +1, drop categories 0. Wind-down, by the clock: from 3
days before the end only products selling 2 a day or more are refilled and nothing new is
listed; nothing is bought in the last 6 hours.

Each run also checks which agreed deals settled (their unit left the inventory) or stayed
unpaid (still there 10 minutes later). The ledger is collected by the analysis runner
(analysis.py); this hook only reads its market_summary.json.
"""
import argparse
import time
from collections import Counter, defaultdict
from pathlib import Path

import common
import pricing

CAP = 2_500_000          # stock at cost
RESERVE = 2_500_000      # balance kept
LINES = 10               # the kit buys at most 10 lines a run
RUN_EVERY = 20 * 60      # seconds between the kit's runs of this hook (60 until 11/3); set it to the kit's
SLOW = RUN_EVERY >= 600  # refills wait 10 minutes or more: keep one more unit of what sells
SOLD_TARGET = 2 + SLOW   # a product we have sold
HOT, HOT_TARGET = 3, 1 + SLOW   # a category the market sold this many of in a day: its products start here
WANTED_WEIGHT = 2        # a wanted category counts as this many sales when ordering what to buy first
SUPPLY_LEAD = 90         # seconds: the kit waits up to this long for a window before it skips a line
SUPPLY_GAP = 10          # seconds between the kit's supply calls
MAX_TARGET = 4
GATE = 10                # our settled sales before stage 2
MAX_PRICE = 60_000       # stage 1 list prices
PRICEY_MAX = 400_000     # stage 2, categories with nothing cheaper
MIN_SHELF = 168          # hours: food that keeps less than a week is not listed
SECOND, SECOND_TOP = 0.7, 40
DEAREST = 0.75           # grade A costs at most 75% of the list price
WIND_DOWN, STOP = 3 * 86400, 6 * 3600
FAST = 2                 # sales in the last day for a product to be refilled in the wind-down
UNPAID_AFTER = 600       # seconds an agreed unit may stay in stock before the deal counts as unpaid
WARN_AFTER, WARN_BELOW = 86400, 3


def check_deals(inv: dict, state: dict, now: float) -> None:
    """Deals the seller agreed: settled once the unit left the inventory, unpaid if it is still
    there UNPAID_AFTER seconds later (the buyer's kit refused to pay, or the buyer left). A unit a
    buyer agreed to and did not pay for is offered again, so several deals can name one unit: when
    it leaves, only the latest of them is the sale (the earlier buyers went without paying)."""
    deals = common.records("deals")
    pending = state.setdefault("pending", {})
    for d in deals[state.get("deals_seen", 0):]:
        pending[d["task"]] = d
    state["deals_seen"] = len(deals)
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
            else:
                common.record("unpaid", deal_ts=d.get("ts"), superseded=True, **fields)
            del pending[task]
        elif now - (d.get("ts") or now) > UNPAID_AFTER:
            common.record("unpaid", deal_ts=d.get("ts"), **fields)
            del pending[task]


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


def plan(inv: dict, catalog: dict, demand: list, summary: dict, sales: list, refusals: list,
         rules: dict, now: float, wanted: dict | None = None, orders: list | None = None) -> tuple[dict, dict]:
    """({"buy": [...]} or bankruptcy, a few numbers for the log). rules: the seller's rules in force;
    orders: our buyer's order records (what buyers ask for)."""
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
    for s in sold:
        if s in catalog:
            targets[s] = max(targets.get(s, 0), SOLD_TARGET)
    for s, n in refused.items():
        targets[s] = targets.get(s, 1) + 1
    for s in wanted_skus(catalog, demand, wanted or {}, set(targets)):
        targets.setdefault(s, 1)
    drop, focus = set(rules.get("drop_categories") or []), set(rules.get("focus_categories") or [])
    for s in list(targets):
        cat = catalog[s]["category"]
        if cat in drop or cat.split(">")[-1] in drop:
            targets[s] = 0
        elif cat in focus or cat.split(">")[-1] in focus:
            targets[s] += 1
    targets = {s: min(MAX_TARGET, t) for s, t in targets.items()}
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
    buy, spend = [], 0.0
    for _, sku, need in gaps:
        each = catalog[sku]["list_price"] * DEAREST
        qty = need
        while qty and (value + spend + each * qty > CAP or balance - spend - each * qty < RESERVE):
            qty -= 1
        if qty:
            buy.append({"sku": sku, "qty": qty})
            spend += each * qty
        if len(buy) >= lines:
            break
    info = {"stage1": len(one), "gate": f"{len(sales)}/{GATE}" + (" open" if gate else ""),
            "targets": len(targets), "gaps": len(gaps), "stock": int(value), "wind_down": wind_down, "lines": lines}
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
        check_deals(inv, state, now)
    sales = common.records("sales")
    wanted = (common.read_json(common.MARKET_DIR / "analysis" / "wanted.json", {}) or {}).get("categories") or {}
    out, info = plan(inv, catalog, common.read_json(state_dir / "demand.json", []) or [],
                     common.read_json(common.MARKET_DIR / "market_summary.json", {}) or {},
                     sales, common.records("refusals"), common.rules()["seller"], now, wanted, common.records("orders"))
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
