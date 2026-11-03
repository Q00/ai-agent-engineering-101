"""Round 3 restock hook: explore broadly, follow my own depletion, and follow the market's settled deals.

Usage: python restock.py --state .market
The last line follows the kit contract: {"buy": [{"sku": ..., "qty": ...}]}.
The ledger (ledger.py) says what the whole market bought in the last 24 h and at what price: categories that
sell get a bonus, SKUs that sell are preferred, and a SKU the market pays less than its expected cost for is
skipped (logged as skipped_below_cost). This runs without an order: the pro-active half of the agent.
"""
import argparse
import json
import time
from collections import Counter
from pathlib import Path

import ledger


MAX_LINES = 6
MAX_SPEND_SHARE = 0.12
FOOD_TARGET = 4
GOODS_TARGET = 6
EXPECTED_COST_RATIO = 0.60   # supply cost as a share of list price, averaged over the grade mix (agent.md): about 0.59


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
    stock_category = Counter(unit.get("category") for unit in units)
    stock_sku = Counter(unit.get("sku") for unit in units)
    categories = [row.get("category") for row in demand if row.get("category")]
    posts = forum_scores(forum, categories)
    # the ledger's settled deals are the one public sign of what sells; scaled by the busiest category
    cat_sales = {cat: row.get("n", 0) for cat, row in (stats.get("categories") or {}).items()}
    busiest = max(cat_sales.values(), default=0)

    scored = []
    for category in categories:
        depletion = (memory.get("category_depletion") or {}).get(category, 0)
        waste = (memory.get("category_waste") or {}).get(category, 0)
        stock = stock_category[category]
        score = 1.0 + 3.0 * depletion + 0.5 * posts[category] - 0.75 * stock - 2.0 * waste
        if busiest:
            score += 2.0 * cat_sales.get(category, 0) / busiest
        if stock == 0:
            score += 1.5
        scored.append((score, category))
    scored.sort(key=lambda row: (-row[0], row[1]))

    spend_cap = min(balance * MAX_SPEND_SHARE, 600_000)
    estimated, buys = 0.0, []
    sku_depletion = memory.get("sku_depletion") or {}
    sku_sales = stats.get("skus") or {}
    for _, category in scored:
        choices = [(sku, row) for sku, row in catalog.items() if row.get("category") == category]
        if not choices:
            continue
        kind = choices[0][1].get("kind")
        target = FOOD_TARGET if kind == "food" else GOODS_TARGET
        if stock_category[category] >= target:
            continue
        # my own sales first (I know I can sell it), then the market's, then what is not in stock yet
        choices.sort(key=lambda pair: (-sku_depletion.get(pair[0], 0), -sku_sales.get(pair[0], {}).get("n", 0),
                                       stock_sku[pair[0]], pair[1].get("list_price", 0), pair[0]))
        choice = sellable_choice(choices, category, stats, skipped)
        if choice is None:
            continue
        sku, row = choice
        qty = min(1 if kind == "food" else 2, target - stock_category[category])
        line_cost = max(1, row.get("list_price", 1)) * 0.75 * qty
        if estimated + line_cost > spend_cap:
            continue
        buys.append({"sku": sku, "qty": qty})
        estimated += line_cost
        if len(buys) >= MAX_LINES:
            break
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
                                      "skipped_below_cost": report.get("skipped_below_cost", []), "buy": buys},
                                     ensure_ascii=False), flush=True)
    bankrupt = ((inv.get("balance") or 0) < (inv.get("bankrupt_line") or 0)
                and not (inv.get("units") or []) and (inv.get("bankruptcies_left") or 0) > 0)
    print(json.dumps({"buy": buys, **({"bankrupt": True} if bankrupt else {})}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
