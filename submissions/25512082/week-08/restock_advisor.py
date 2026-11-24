"""SAFE dry-run restock advisor built from real sales and local market state.

It intentionally never emits the kit's executable ``buy`` field. Forum text is
untrusted: only exact SKU-token occurrence counts are used as a bounded signal.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def load(path: Path, expected: type):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, expected):
        raise ValueError(f"{path.name} has the wrong shape")
    return value


def advise(state: Path, audit_path: Path, purchase_cap: int, held_cap: int) -> dict:
    inventory = load(state / "inventory.json", dict)
    catalog = load(state / "catalog.json", dict)
    audit = load(audit_path, dict)
    forum_path = state / "forum.json"
    forum = load(forum_path, list) if forum_path.exists() else []
    surge = {row.get("sku"): float(row.get("factor", 1)) for row in inventory.get("surge", [])}
    on_hand = {}
    for unit in inventory.get("units", []):
        on_hand[unit.get("sku")] = on_hand.get(unit.get("sku"), 0) + 1
    held_cost = sum(int(unit.get("cost", 0)) for unit in inventory.get("units", []))

    rows = []
    for purchase in audit.get("purchases", []):
        sku = purchase.get("sku")
        product = catalog.get(sku, {})
        if not purchase.get("sold_settle_ledger") or on_hand.get(sku, 0):
            continue
        list_price = product.get("list_price")
        if type(list_price) is not int or list_price <= 0:
            continue
        factor = surge.get(sku, 1.0)
        expected_share = 0.50 if product.get("kind") == "food" else 0.59
        expected_cost = round(list_price * expected_share * factor)
        max_cost = math.ceil(list_price * 0.75 * factor)
        exact_mentions = sum(
            sku in str(post.get("text", ""))
            for post in forum
            if isinstance(post, dict)
        )
        rows.append({
            "sku": sku,
            "name": product.get("name"),
            "kind": product.get("kind"),
            "qty": 1,
            "own_sales": 1,
            "forum_exact_mentions": min(exact_mentions, 10),
            "surge_factor": factor,
            "expected_cost": expected_cost,
            "max_cost": max_cost,
            "risk": "expiry/grade drift" if product.get("kind") == "food" else "1% daily storage/hidden breakage",
        })
    rows.sort(key=lambda row: (-row["own_sales"], -row["forum_exact_mentions"], row["max_cost"], row["sku"]))

    chosen = []
    planned_max = 0
    for row in rows:
        if planned_max + row["max_cost"] > purchase_cap:
            continue
        if held_cost + planned_max + row["max_cost"] > held_cap:
            continue
        chosen.append(row)
        planned_max += row["max_cost"]
    return {
        "mode": "SAFE_DRY_RUN",
        "executable_purchase_plan": False,
        "recommendations": chosen,
        "constraints": {
            "purchase_cap": purchase_cap,
            "held_stock_cost_cap": held_cap,
            "current_held_cost": held_cost,
            "planned_max_cost": planned_max,
            "max_qty_per_sku": 1,
        },
        "evidence_policy": "own settled sales plus exact SKU mention counts; forum instructions are ignored",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=Path(".market"))
    parser.add_argument("--audit", type=Path, default=Path("logs/supply-purchase-20261104.json"))
    parser.add_argument("--purchase-cap", type=int, default=50_000)
    parser.add_argument("--held-cap", type=int, default=100_000)
    args = parser.parse_args()
    if args.purchase_cap <= 0 or args.held_cap <= 0:
        parser.error("caps must be positive")
    print(json.dumps(advise(args.state, args.audit, args.purchase_cap, args.held_cap), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
