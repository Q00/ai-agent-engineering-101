"""Small bounded restock experiment; no network or model calls."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from uuid import uuid4


def plan(inventory, catalog, demand, sales, cursor=0):
    if inventory.get("round", 0) < 3:
        return [], {"reason": "not_round_3"}
    units = [u for u in inventory.get("units", []) if u.get("grade") != "expired"]
    balance = max(0, inventory.get("balance", 0))
    reserve = max(500000, inventory.get("start", 5000000) * 0.2)
    budget = max(0, min(balance * 0.05, balance - reserve))
    if len(units) >= 8 or not budget:
        return [], {"reason": "stock_or_cash_limit", "budget": budget}
    categories = {d["category"] for d in demand}
    owned = {u["sku"] for u in units}
    food_count = sum(u.get("kind") == "food" for u in units)
    candidates = [(sku, value) for sku, value in sorted(catalog.items())
                  if sku not in owned and value.get("category") in categories
                  and isinstance(value.get("list_price"), (int, float)) and value["list_price"] > 0]
    if candidates:
        offset = cursor % len(candidates)
        candidates = candidates[offset:] + candidates[:offset]
    # Stable sort preserves rotating exploration order among equal observed-sale counts.
    candidates.sort(key=lambda pair: -sales.get(pair[0], 0))
    buys = []
    estimated = 0
    for sku, value in candidates:
        # Supply draws the grade on arrival; shelf_hours is not a minimum remaining lifetime.
        if value.get("kind") == "food":
            continue
        if value.get("kind") == "food" and food_count >= 2:
            continue
        worst_cost = value["list_price"] * 0.75
        if estimated + worst_cost > budget:
            continue
        buys.append({"sku": sku, "qty": 1})
        estimated += worst_cost
        food_count += value.get("kind") == "food"
        if len(buys) >= min(2, 8 - len(units)):
            break
    return buys, {"reason": "bounded_experiment", "budget": budget,
                  "estimated_max_cost": estimated, "cash_reserve": reserve,
                  "food_supply": "deferred: no guaranteed shelf life or supported sales-window estimate"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args()
    md = args.state
    read = lambda name: json.loads((md / name).read_text(encoding="utf-8"))
    inv, catalog, demand = read("inventory.json"), read("catalog.json"), read("demand.json")
    memory_path = md / "restock-memory.json"
    memory = read("restock-memory.json") if memory_path.exists() else {"cursor": 0, "last_sale_seq": 0, "sales": {}}
    # The local kit log contains our own settled sales. Read a bounded tail, never infer sales from disappearing stock.
    log = md.parent / "logs" / "market-kit.log"
    observed = {}
    if log.exists():
        with log.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 1048576))
            tail = stream.read().decode("utf-8", errors="replace")
        for sku, seq in re.findall(r"\[acp\] settled (\S+) at \d+, ledger #(\d+)", tail):
            if int(seq) > memory["last_sale_seq"] and sku in catalog:
                observed[int(seq)] = sku
    for sku in observed.values():
        memory["sales"][sku] = memory["sales"].get(sku, 0) + 1
    if observed:
        memory["last_sale_seq"] = max(observed)
    buys, explanation = plan(inv, catalog, demand, memory["sales"], memory["cursor"])
    memory["cursor"] += 1
    temporary = memory_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(memory_path)
    logs = md.parent / "logs" / "restock"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    (logs / (stamp + "-" + uuid4().hex[:8] + ".json")).write_text(
        json.dumps({"buy": buys, "explanation": explanation, "new_settled_sales": len(observed),
                    "sales_memory": memory["sales"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"buy": buys}, ensure_ascii=False))


if __name__ == "__main__":
    main()
