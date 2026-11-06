"""Round 3 restock hook: explore broadly, follow my own depletion, and follow the market's settled deals.

Usage: python restock.py --state .market
The last line follows the kit contract: {"buy": [{"sku": ..., "qty": ...}]}.
The ledger (ledger.py) says what the whole market bought in the last 24 h and at what price: categories that
sell get a bonus, SKUs that sell are preferred, and a SKU the market pays less than its expected cost for is
skipped (logged as skipped_below_cost). This runs without an order: the pro-active half of the agent.
"""
import argparse
import json
import math
import time
from collections import Counter
from pathlib import Path

import forum_post
import ledger
import round_clock


MAX_LINES = 10               # the kit buys at most 10 lines per run
FOOD_TARGET = 4
GOODS_TARGET = 6
EXPECTED_COST_RATIO = 0.60   # supply cost as a share of list price, averaged over the grade mix (agent.md): about 0.59
# Round 3 minute 10 (11-03 21:05): 10 runs had put 1.10M of the 5M into 56 units over 31 of 140 categories, 0.2M sold.
# Unsold stock is a loss and food spoils, so the capital tied up in stock is capped, and exploring categories with
# no evidence (no ledger sales, no own depletion, no forum demand) is allowed only while that stock is small.
# Day 2 (11-04 17:10): with a 1.25M cap and "cheapest SKU first" the shop sold condiments at 10k a unit (106 lots,
# median lot 10k) while the market's revenue was sofas at 1.7M and mattresses at 300k (the top shop: lots of 1M, 180M
# bought in 2.3 h). The caps now scale with capital, categories are weighed by settled revenue, and the SKU is the one
# nearest the price the category actually settles at, as long as one unit stays a small share of the balance.
STOCK_SHARE = 0.60           # stock at cost may not exceed this share of capital (balance + stock at cost);
                             # 11-06 18:30: 80% of what was bought in 5 h had sold, the top shops hold 400M+ in stock
EXPLORE_STOCK_SHARE = 0.10   # of capital: below this, categories with no evidence may still be tried (one lot each)
RUN_SPEND_SHARE = 0.40       # of the balance, per run
UNIT_COST_SHARE = 0.20       # one unit's expected cost may not exceed this share of capital (balance + stock at cost);
                             # 11-04 17:40: at 15% of the balance a 1.63M sofa blocked the next one until it sold (2 min,
                             # 3 buyers); a refund on a break (~3% of settles) is a third of the margin in expectation
CATEGORY_SHARE = 0.35        # one category's stock at cost may not exceed this share of the stock cap
TOP_CATEGORY_SHARE = 0.55    # ... except a top-3 revenue category whose units I close 50%+ of the time (11-05 22:40:
                             # four sofas in stock answered 44 opens with 17 deals; the top shop keeps dozens)
TOP_CATEGORIES = 3
MIN_UNIT_MARGIN = 5_000      # a category whose typical unit cannot clear this much is explored once, never topped up:
                             # 330 units of 2-5k items cost storage, spoilage and negotiations for nothing


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def unit_snapshot(unit):
    return {key: unit.get(key) for key in ("unit", "sku", "category", "kind", "expires_at")}


def update_memory(previous, units, now=None):
    """Infer demand only from an unexpired disappearance; expiry is waste."""
    now = now or time.time()
    memory = {
        "units": previous.get("units") or [],
        "category_depletion": dict(previous.get("category_depletion") or {}),
        "sku_depletion": dict(previous.get("sku_depletion") or {}),
        "category_waste": dict(previous.get("category_waste") or {}),
    }
    current_ids = {unit.get("unit") for unit in units}
    for old in memory["units"]:
        if old.get("unit") in current_ids:
            continue
        expired = old.get("kind") == "food" and old.get("expires_at") and old["expires_at"] <= now
        target = memory["category_waste"] if expired else memory["category_depletion"]
        category = old.get("category") or "unknown"
        target[category] = target.get(category, 0) + 1
        if not expired and old.get("sku"):
            memory["sku_depletion"][old["sku"]] = memory["sku_depletion"].get(old["sku"], 0) + 1
    memory["units"] = [unit_snapshot(unit) for unit in units]
    memory["updated_at"] = int(now)
    return memory


def forum_scores(forum, categories):
    scores = Counter()
    for post in forum if isinstance(forum, list) else []:
        text = str(post.get("text", "")).casefold()
        if not post.get("refs"):  # unreferenced promotion is not demand evidence
            continue
        for category in categories:
            leaf = category.split(">")[-1].strip().casefold()
            if leaf and leaf in text:
                scores[category] += 1
    return scores


def sellable_choice(choices, category, stats, skipped):
    """The first SKU the market does not pay less than its expected cost for. Supply draws A 70% at 55-75% of list,
    B 20% at 40-60%, C 10% at 25-45%: about 0.59 on average. A SKU whose settled price sits under that is a loss
    before storage; it is recorded in `skipped` so the log shows why it was not bought."""
    for sku, row in choices:
        price, n, _ = ledger.market_price(stats, sku, category, row.get("list_price"))
        expected_cost = (row.get("list_price") or 0) * EXPECTED_COST_RATIO
        if price is not None and price < expected_cost:
            skipped.append({"sku": sku, "market": round(price), "settles": n, "expected_cost": round(expected_cost)})
            continue
        return sku, row
    return None


def plan(inv, catalog, demand, forum, memory, stats=None, report=None, pressure=None, hours_to_end=None):
    units = inv.get("units") or []
    balance = max(0, int(inv.get("balance") or 0))
    if not catalog or not demand or balance <= 0:
        return []
    stats = stats or {}
    report = report if report is not None else {}
    skipped = report.setdefault("skipped_below_cost", [])
    pressure = pressure or {}
    if hours_to_end is not None and hours_to_end < round_clock.STOP_ALL_HOURS:
        report["liquidation"] = f"{hours_to_end:.1f} h to the close: no more stock"   # worth nothing after the close
        return []
    stock_cost = sum(unit.get("cost") or 0 for unit in units)
    capital = balance + stock_cost
    stock_cap = STOCK_SHARE * capital
    room = stock_cap - stock_cost
    report["stock_cost"], report["stock_room"] = stock_cost, round(room)
    if room <= 0:
        return []
    explore = stock_cost < EXPLORE_STOCK_SHARE * capital
    report["explore"] = explore
    stock_category = Counter(unit.get("category") for unit in units)
    cost_category = Counter()
    for unit in units:
        cost_category[unit.get("category")] += unit.get("cost") or 0
    stock_sku = Counter(unit.get("sku") for unit in units)
    categories = [row.get("category") for row in demand if row.get("category")]
    typical = {row.get("category"): row.get("median_price") for row in demand}   # the category's price level
    posts = forum_scores(forum, categories)
    # the ledger's settled deals are the one public sign of what sells; weighed by revenue (sofas at 1.7M are worth
    # more than kimchi at 23k), scaled by the richest category. Older stats without revenue fall back to the count.
    cat_rows = stats.get("categories") or {}
    cat_rev = {cat: (row.get("revenue") or row.get("n", 0)) for cat, row in cat_rows.items()}
    richest = max(cat_rev.values(), default=0)
    top_categories = {c for c, _ in sorted(cat_rev.items(), key=lambda kv: -kv[1])[:TOP_CATEGORIES]}

    def sells_at_of(category):   # the list price the category actually settles around, else its demand median
        row_ = cat_rows.get(category) or {}
        if row_.get("median_amount") and row_.get("median_ratio"):
            return row_["median_amount"] / row_["median_ratio"]
        return typical.get(category) or None

    def comparable_of(category, sells_at):   # stock that can answer the orders this category gets: cheap leftovers
        return sum(1 for unit in units if unit.get("category") == category          # (six 40k stools) do not count
                   and (not sells_at or (unit.get("list_price") or 0) >= 0.5 * sells_at))

    # my own sales weighed by the category's price level, like the market's: 140 sauces at 7k are not more evidence
    # than one sofa at 1.7M (17:45 dry run: a raw count of 140 put sauces ahead of sofas)
    depletions = memory.get("category_depletion") or {}
    own_rev = {category: depletions.get(category, 0) * (sells_at_of(category) or 1) for category in categories}
    richest_own = max(own_rev.values(), default=0)

    scored = []
    for category in categories:
        depletion = depletions.get(category, 0)
        waste = (memory.get("category_waste") or {}).get(category, 0)
        stock = comparable_of(category, sells_at_of(category))
        evidence = depletion or posts[category] or cat_rev.get(category, 0)
        if not evidence and not explore:
            continue
        score = 1.0 + 0.5 * posts[category] - 0.75 * stock - 2.0 * waste
        if richest_own:
            score += 3.0 * own_rev[category] / richest_own
        if richest:
            score += 2.0 * cat_rev.get(category, 0) / richest
        if stock == 0:
            score += 1.5
        scored.append((score, category))
    scored.sort(key=lambda row: (-row[0], row[1]))

    spend_cap = min(balance * RUN_SPEND_SHARE, room)
    unit_cap = capital * UNIT_COST_SHARE
    estimated, buys, too_dear = 0.0, [], 0
    sku_depletion = memory.get("sku_depletion") or {}
    sku_sales = stats.get("skus") or {}
    for _, category in scored:
        press = pressure.get(category) or {}
        if press.get("opens", 0) >= 10 and press.get("close_rate", 1.0) < 0.4 and stock_category[category]:
            report.setdefault("skipped_slow", []).append(category.split(">")[-1])   # my units there are not moving
            continue
        share = TOP_CATEGORY_SHARE if category in top_categories and press.get("close_rate", 0) >= 0.5 else CATEGORY_SHARE
        if cost_category[category] >= share * stock_cap:
            continue
        choices = [(sku, row) for sku, row in catalog.items() if row.get("category") == category
                   and (row.get("list_price") or 0) * EXPECTED_COST_RATIO <= unit_cap]
        too_dear += sum(1 for sku, row in catalog.items() if row.get("category") == category) - len(choices)
        if not choices:
            continue
        kind = choices[0][1].get("kind")
        if kind == "food" and hours_to_end is not None and hours_to_end < round_clock.STOP_FOOD_HOURS:
            continue   # it would not sell before it spoils or the close
        target = FOOD_TARGET if kind == "food" else GOODS_TARGET
        sells_at = sells_at_of(category)
        comparable = comparable_of(category, sells_at)
        if comparable >= target:
            continue
        ratio = (cat_rows.get(category) or {}).get("median_ratio")
        if comparable and sells_at and ratio and sells_at * (ratio - EXPECTED_COST_RATIO) < MIN_UNIT_MARGIN:
            report.setdefault("skipped_small_margin", []).append(category.split(">")[-1])
            continue
        if sells_at:
            # an order accepts SKUs priced 0.5x-1.5x its reference (agent.md); outside that band around the price the
            # category settles at, a unit is cheap to buy and never asked for (17:40 dry run: a 3,860 spray paint in
            # the sofa category because I had sold one before). Nothing affordable in the band: skip the category.
            choices = [pair for pair in choices if 0.5 * sells_at <= (pair[1].get("list_price") or 0) <= 1.5 * sells_at]
            if not choices:
                continue

        def rank(pair):   # my own sales first (I know I can sell it), then the market's, then not in stock yet,
            sku, row = pair                                  # then the dearest inside the band: margin scales with price
            return (-sku_depletion.get(sku, 0), -sku_sales.get(sku, {}).get("n", 0), stock_sku[sku],
                    -(row.get("list_price") or 0), sku)      # (11-06: my mattresses listed at 424k, the market's at 546k)

        choices.sort(key=rank)
        choice = sellable_choice(choices, category, stats, skipped)
        if choice is None:
            continue
        sku, row = choice
        est_cost = max(1, row.get("list_price", 1)) * EXPECTED_COST_RATIO
        per_lot = 1 if (kind == "food" or est_cost > 0.05 * balance) else 2
        qty = min(per_lot, target - comparable)
        line_cost = est_cost * qty
        if estimated + line_cost > spend_cap:
            continue
        buys.append({"sku": sku, "qty": qty})
        estimated += line_cost
        if len(buys) >= MAX_LINES:
            break
    report["too_dear"], report["planned_cost"] = too_dear, round(estimated)
    return buys


PRESSURE_FILE = "category_pressure.json"
PRESSURE_HOURS = 6


def category_pressure(events, catalog, since):
    """Per category, how many buyers each of my units draws (opens per deal) and the close rate over recent hours.
    The seller reads this file: a category where units draw several buyers can carry a markup, one whose units do
    not move needs a cut. Written every minute by this hook; the seller never reads the event log itself."""
    opens, deals = Counter(), Counter()
    for e in events:
        if e.get("at", "") < since:
            continue
        category = (catalog.get(e.get("sku")) or {}).get("category") or "?"
        if e.get("event") == "seller_open":
            opens[category] += 1
        elif e.get("event") == "seller_deal":
            deals[category] += 1
    return {c: {"opens": opens[c], "deals": deals[c], "opens_per_deal": round(opens[c] / max(1, deals[c]), 2),
                "close_rate": round(deals[c] / opens[c], 3)}
            for c in opens if opens[c] >= 5}


def write_pressure(state, catalog, now=None):
    try:
        lines = (state / "seller_events.jsonl").read_text(encoding="utf-8").splitlines()[-30000:]
    except OSError:
        return {}
    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime((now or time.time()) - PRESSURE_HOURS * 3600))
    pressure = category_pressure(events, catalog, since)
    (state / PRESSURE_FILE).write_text(json.dumps(pressure, ensure_ascii=False), encoding="utf-8")
    return pressure


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True)
    args = parser.parse_args()
    state = Path(args.state)
    inv = read_json(state / "inventory.json", {})
    catalog = read_json(state / "catalog.json", {})
    demand = read_json(state / "demand.json", [])
    forum = read_json(state / "forum.json", [])
    memory_path = state / "restock_memory.json"
    memory = update_memory(read_json(memory_path, {}), inv.get("units") or [])
    stats = ledger.refresh(state, catalog)   # one ledger call per run; the seller reads the file this writes
    pressure = write_pressure(state, catalog)   # and this one: buyers per unit by category, for the seller's markup
    try:
        posted = forum_post.maybe_market_post(state, catalog)   # the pro-active half of the forum: a price note
    except Exception as e:                                       # never let a forum problem stop the restock
        print(f"[forum] market post failed: {type(e).__name__}: {str(e)[:100]}", flush=True)
        posted = None
    if posted:
        print("[strategy] " + json.dumps({"event": "market_post", "text": posted[:120]}, ensure_ascii=False), flush=True)
    hours_to_end = round_clock.hours_left()
    report = {"pressure_categories": len(pressure), "hours_to_end": round(hours_to_end, 1)}
    buys = plan(inv, catalog, demand, forum, memory, stats, report, pressure, hours_to_end)
    memory_path.write_text(json.dumps(memory, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[strategy] " + json.dumps({"event": "restock", "stock": len(inv.get("units") or []),
                                      "balance": inv.get("balance"), "ledger_settles": stats.get("settles"),
                                      "stock_cost": report.get("stock_cost"), "stock_room": report.get("stock_room"),
                                      "explore": report.get("explore"), "planned_cost": report.get("planned_cost"),
                                      "skus_too_dear": report.get("too_dear"), "hours_to_end": report.get("hours_to_end"),
                                      "liquidation": report.get("liquidation"),
                                      "skipped_small_margin": report.get("skipped_small_margin", []),
                                      "skipped_slow": report.get("skipped_slow", []),
                                      "skipped_below_cost": report.get("skipped_below_cost", []), "buy": buys},
                                     ensure_ascii=False), flush=True)
    bankrupt = ((inv.get("balance") or 0) < (inv.get("bankrupt_line") or 0)
                and not (inv.get("units") or []) and (inv.get("bankruptcies_left") or 0) > 0)
    print(json.dumps({"buy": buys, **({"bankrupt": True} if bankrupt else {})}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
