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

import ledger


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
STOCK_SHARE = 0.50           # stock at cost may not exceed this share of capital (balance + stock at cost)
EXPLORE_STOCK_SHARE = 0.10   # of capital: below this, categories with no evidence may still be tried (one lot each)
RUN_SPEND_SHARE = 0.30       # of the balance, per run
UNIT_COST_SHARE = 0.15       # one unit's expected cost may not exceed this share of the balance
CATEGORY_SHARE = 0.35        # one category's stock at cost may not exceed this share of the stock cap


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


def plan(inv, catalog, demand, forum, memory, stats=None, report=None):
    units = inv.get("units") or []
    balance = max(0, int(inv.get("balance") or 0))
    if not catalog or not demand or balance <= 0:
        return []
    stats = stats or {}
    report = report if report is not None else {}
    skipped = report.setdefault("skipped_below_cost", [])
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

    scored = []
    for category in categories:
        depletion = (memory.get("category_depletion") or {}).get(category, 0)
        waste = (memory.get("category_waste") or {}).get(category, 0)
        stock = stock_category[category]
        evidence = depletion or posts[category] or cat_rev.get(category, 0)
        if not evidence and not explore:
            continue
        score = 1.0 + 3.0 * depletion + 0.5 * posts[category] - 0.75 * stock - 2.0 * waste
        if richest:
            score += 2.0 * cat_rev.get(category, 0) / richest
        if stock == 0:
            score += 1.5
        scored.append((score, category))
    scored.sort(key=lambda row: (-row[0], row[1]))

    spend_cap = min(balance * RUN_SPEND_SHARE, room)
    unit_cap = balance * UNIT_COST_SHARE
    estimated, buys, too_dear = 0.0, [], 0
    sku_depletion = memory.get("sku_depletion") or {}
    sku_sales = stats.get("skus") or {}
    for _, category in scored:
        if cost_category[category] >= CATEGORY_SHARE * stock_cap:
            continue
        choices = [(sku, row) for sku, row in catalog.items() if row.get("category") == category
                   and (row.get("list_price") or 0) * EXPECTED_COST_RATIO <= unit_cap]
        too_dear += sum(1 for sku, row in catalog.items() if row.get("category") == category) - len(choices)
        if not choices:
            continue
        kind = choices[0][1].get("kind")
        target = FOOD_TARGET if kind == "food" else GOODS_TARGET
        if stock_category[category] >= target:
            continue
        row_ = cat_rows.get(category) or {}
        sells_at = (row_["median_amount"] / row_["median_ratio"] if row_.get("median_amount") and row_.get("median_ratio")
                    else typical.get(category)) or None     # the list price the category actually settles around

        def rank(pair):   # my own sales first (I know I can sell it), then the market's, then not in stock yet,
            sku, row = pair                                  # then nearest the price that sells (dearest without data)
            list_price = row.get("list_price") or 1
            nearness = abs(math.log(list_price / sells_at)) if sells_at else -list_price
            return (-sku_depletion.get(sku, 0), -sku_sales.get(sku, {}).get("n", 0), stock_sku[sku], nearness, sku)

        choices.sort(key=rank)
        choice = sellable_choice(choices, category, stats, skipped)
        if choice is None:
            continue
        sku, row = choice
        est_cost = max(1, row.get("list_price", 1)) * EXPECTED_COST_RATIO
        per_lot = 1 if (kind == "food" or est_cost > 0.05 * balance) else 2
        qty = min(per_lot, target - stock_category[category])
        line_cost = est_cost * qty
        if estimated + line_cost > spend_cap:
            continue
        buys.append({"sku": sku, "qty": qty})
        estimated += line_cost
        if len(buys) >= MAX_LINES:
            break
    report["too_dear"], report["planned_cost"] = too_dear, round(estimated)
    return buys


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
    report = {}
    buys = plan(inv, catalog, demand, forum, memory, stats, report)
    memory_path.write_text(json.dumps(memory, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[strategy] " + json.dumps({"event": "restock", "stock": len(inv.get("units") or []),
                                      "balance": inv.get("balance"), "ledger_settles": stats.get("settles"),
                                      "stock_cost": report.get("stock_cost"), "stock_room": report.get("stock_room"),
                                      "explore": report.get("explore"), "planned_cost": report.get("planned_cost"),
                                      "skus_too_dear": report.get("too_dear"),
                                      "skipped_below_cost": report.get("skipped_below_cost", []), "buy": buys},
                                     ensure_ascii=False), flush=True)
    bankrupt = ((inv.get("balance") or 0) < (inv.get("bankrupt_line") or 0)
                and not (inv.get("units") or []) and (inv.get("bankruptcies_left") or 0) > 0)
    print(json.dumps({"buy": buys, **({"bankrupt": True} if bankrupt else {})}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
