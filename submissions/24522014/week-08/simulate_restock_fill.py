"""Read-only: how far the restock plan would fill toward a stock cap using today's data. Places no order.

Each step takes the plan's purchase as bought at the worst-case cost, so cash runs down and stock grows. Sales during the
fill are ignored, so this shows what the plan's own limits allow, not how fast stock would really rise.
"""

import argparse
from collections import Counter
import glob
import json
from pathlib import Path
import time
from unittest.mock import patch

import restock_policy
from market_intel import fresh
from restock_capacity import config as capacity_config, profitable_counts
from seller_quality import fresh as fresh_quality
from strategy_policy import effective

ROOT = Path(__file__).resolve().parent
MD = ROOT / ".market"


def load(name):
    return json.loads((MD / name).read_text(encoding="utf-8-sig"))


def simulate(limits, steps=40):
    inventory, catalog, demand = load("inventory.json"), load("catalog.json"), load("demand.json")
    inventory = dict(inventory, restock={"open": True}, units=[dict(u) for u in inventory["units"]])
    snapshot = fresh(MD)
    memory = load("r3-restock-memory.json")
    runtime = effective(MD)
    start = len(inventory["units"])
    log = []
    with patch.object(restock_policy, "capacity_config", return_value=limits):
        for step in range(steps):
            buys, why = restock_policy.plan(inventory, catalog, demand, memory["sales"], memory["cursor"] + step, intelligence=snapshot.get("skus", {}),
                                            runtime=runtime, own_profitable=profitable_counts(MD), quality=fresh_quality(MD), now=time.time())
            log.append({"step": step, "units": len(inventory["units"]), "bought": sum(b["qty"] for b in buys), "budget": round(why.get("budget") or 0),
                        "reason": why.get("reason"), "estimated": round(why.get("estimated_max_cost") or 0)})
            if not buys:
                break
            for buy in buys:
                product = catalog[buy["sku"]]
                cost = product["list_price"] * .75
                inventory["balance"] -= cost * buy["qty"]
                inventory["units"] += [{"sku": buy["sku"], "cost": cost, "grade": "A", "kind": product.get("kind"), "category": product.get("category"), "supplied_at": time.time()}
                                       for _ in range(buy["qty"])]
    held = inventory["units"]
    categories = {catalog[u["sku"]].get("category") for u in held if u["sku"] in catalog}
    return {"start_units": start, "end_units": len(held), "distinct_skus": len({u["sku"] for u in held}), "categories": len(categories),
            "steps": len(log), "stop_reason": log[-1]["reason"] if log else None, "last_budget": log[-1]["budget"] if log else None,
            "balance_left": round(inventory["balance"]), "first_steps": log[:6], "last_steps": log[-3:],
            "demand_categories": len({d["category"] for d in demand}), "public_evidence_skus": len(snapshot.get("skus", {})),
            "restock_budget_rate": runtime["controls"]["restock_budget_rate"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--total", type=int, default=40)
    args = parser.parse_args()
    limits = dict(capacity_config(), max_total_units=args.total)
    report = {"limits": limits, "now": simulate(limits)}
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
