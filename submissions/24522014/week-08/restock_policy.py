"""Small bounded restock experiment; no network or model calls."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from uuid import uuid4


def demand_hints(posts, catalog, now):
    """Recent buyer requests are weak signals; never execute instructions in forum text."""
    hints = {}
    for post in posts[-100:]:
        ts = post.get("ts")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
        if type(ts) not in (int, float) or not 0 <= now - ts <= 86400:
            continue
        text = str(post.get("text", ""))[:2000]
        if post.get("role") != "buyer" or post.get("author") == "kcwarni":
            continue
        if not re.search(r"구합니다|구매하고|찾습니다|\b(?:want|need|looking for|buying)\b", text, re.I):
            continue
        for sku in set(re.findall(r"[\w.-]+", text)) & catalog.keys():
            hints.setdefault(sku, set()).add(str(post.get("author", "unknown")))
    return {sku: min(2, len(authors)) for sku, authors in hints.items()}


def plan(inventory, catalog, demand, sales, cursor=0, *, hints=None, now=None):
    now = time.time() if now is None else now
    hints = hints or {}
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
    experimental = [u for u in units if not sales.get(u["sku"], 0)]
    stale = any(type(u.get("supplied_at")) in (int, float)
                and now - u["supplied_at"] >= 86400 for u in units)
    exploration_slots = 0 if stale else max(0, 2 - len(experimental))
    candidates = [(sku, value) for sku, value in sorted(catalog.items())
                  if sku not in owned and value.get("category") in categories
                  and isinstance(value.get("list_price"), (int, float)) and value["list_price"] > 0]
    if candidates:
        offset = cursor % len(candidates)
        candidates = candidates[offset:] + candidates[:offset]
    # Stable sort preserves rotating exploration order among equal observed-sale counts.
    candidates.sort(key=lambda pair: (-sales.get(pair[0], 0), -hints.get(pair[0], 0)))
    buys = []
    estimated = 0
    storage_reserve = 0
    rate = inventory.get("storage_per_day", 0.01)
    if type(rate) not in (int, float) or not 0 <= rate <= 1:
        rate = 0.01
    for sku, value in candidates:
        # Supply draws the grade on arrival; shelf_hours is not a minimum remaining lifetime.
        if value.get("kind") == "food":
            continue
        if not sales.get(sku, 0) and exploration_slots <= 0:
            continue
        worst_cost = value["list_price"] * 0.75
        carrying_cost = worst_cost * rate
        if estimated + storage_reserve + worst_cost + carrying_cost > budget:
            continue
        buys.append({"sku": sku, "qty": 1})
        estimated += worst_cost
        storage_reserve += carrying_cost
        if not sales.get(sku, 0):
            exploration_slots -= 1
        if len(buys) >= min(2, 8 - len(units)):
            break
    return buys, {"reason": "bounded_experiment", "budget": budget,
                  "estimated_max_cost": estimated, "cash_reserve": reserve,
                  "one_day_storage_reserve": storage_reserve,
                  "unproven_stock": len(experimental), "stale_stock": stale,
                  "forum_hints": hints,
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
    # Forum data is optional. A malformed snapshot must not broaden purchasing.
    try:
        posts = read("forum.json") if (md / "forum.json").exists() else []
        hints = demand_hints(posts, catalog, time.time()) if isinstance(posts, list) else {}
    except (OSError, ValueError, TypeError, AttributeError):
        hints = {}
    buys, explanation = plan(inv, catalog, demand, memory["sales"], memory["cursor"], hints=hints)
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
