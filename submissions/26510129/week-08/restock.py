"""Round 3 restock hook: explore broadly, then follow observed depletion.

Usage: python restock.py --state .market
The last line follows the kit contract: {"buy": [{"sku": ..., "qty": ...}]}.
"""
import argparse
import json
import time
from collections import Counter
from pathlib import Path


MAX_LINES = 6
MAX_SPEND_SHARE = 0.12
FOOD_TARGET = 4
GOODS_TARGET = 6


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


def plan(inv, catalog, demand, forum, memory):
    units = inv.get("units") or []
    balance = max(0, int(inv.get("balance") or 0))
    if not catalog or not demand or balance <= 0:
        return []
    stock_category = Counter(unit.get("category") for unit in units)
    stock_sku = Counter(unit.get("sku") for unit in units)
    categories = [row.get("category") for row in demand if row.get("category")]
    posts = forum_scores(forum, categories)

    scored = []
    for category in categories:
        depletion = (memory.get("category_depletion") or {}).get(category, 0)
        waste = (memory.get("category_waste") or {}).get(category, 0)
        stock = stock_category[category]
        score = 1.0 + 3.0 * depletion + 0.5 * posts[category] - 0.75 * stock - 2.0 * waste
        if stock == 0:
            score += 1.5
        scored.append((score, category))
    scored.sort(key=lambda row: (-row[0], row[1]))

    spend_cap = min(balance * MAX_SPEND_SHARE, 600_000)
    estimated, buys = 0.0, []
    sku_depletion = memory.get("sku_depletion") or {}
    for _, category in scored:
        choices = [(sku, row) for sku, row in catalog.items() if row.get("category") == category]
        if not choices:
            continue
        kind = choices[0][1].get("kind")
        target = FOOD_TARGET if kind == "food" else GOODS_TARGET
        if stock_category[category] >= target:
            continue
        choices.sort(key=lambda pair: (-sku_depletion.get(pair[0], 0), stock_sku[pair[0]], pair[1].get("list_price", 0), pair[0]))
        sku, row = choices[0]
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
    buys = plan(inv, catalog, demand, forum, memory)
    memory_path.write_text(json.dumps(memory, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[strategy] " + json.dumps({"event": "restock", "stock": len(inv.get("units") or []),
                                      "balance": inv.get("balance"), "buy": buys}, ensure_ascii=False), flush=True)
    bankrupt = ((inv.get("balance") or 0) < (inv.get("bankrupt_line") or 0)
                and not (inv.get("units") or []) and (inv.get("bankruptcies_left") or 0) > 0)
    print(json.dumps({"buy": buys, **({"bankrupt": True} if bankrupt else {})}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
