"""Approval-gated, fail-closed Round 3 restock hook.

No purchase is emitted unless a short-lived ``restock-approval.json`` exists
next to ``.market`` and every line passes live inventory, surge, budget and
margin gates. The supply API has no price-limit parameter, so approval must
cover the official worst-case cost or the line is rejected.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path


HARD_RUN_CAP = 100_000
HARD_HELD_COST_CAP = 1_000_000
MAX_QTY_PER_SKU = 1
MIN_EXPECTED_MARGIN_SHARE = 0.08
MAX_WORST_FLOOR_SHARE = 0.90


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Return an approved safe restock plan")
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--approval", type=Path)
    return parser.parse_args(argv)


def _load(path: Path, expected: type):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, expected):
        raise ValueError(f"{path.name} has the wrong shape")
    return value


def _empty() -> dict:
    return {"buy": []}


def build_plan(state: Path, approval_path: Path, now: float | None = None) -> dict:
    """Return only explicitly approved lines safe at their worst-case cost."""
    try:
        inventory = _load(state / "inventory.json", dict)
        catalog = _load(state / "catalog.json", dict)
        approval = _load(approval_path, dict)
        current_time = time.time() if now is None else now
        restock = inventory.get("restock")
        if not isinstance(restock, dict) or restock.get("open") is not True:
            return _empty()
        opens_at, closes_at = restock.get("opens_at"), restock.get("closes_at")
        if not isinstance(opens_at, (int, float)) or not isinstance(closes_at, (int, float)):
            return _empty()
        if not opens_at <= current_time < closes_at:
            return _empty()
        if approval.get("enabled") is not True:
            return _empty()
        if approval.get("round") != inventory.get("round") or inventory.get("round") != 3:
            return _empty()
        expires_at = approval.get("expires_at")
        if not isinstance(expires_at, (int, float)) or expires_at <= current_time:
            return _empty()
        approved_cap = approval.get("purchase_cap")
        if type(approved_cap) is not int or not 0 < approved_cap <= HARD_RUN_CAP:
            return _empty()
        lines = approval.get("lines")
        if not isinstance(lines, list) or not lines or len(lines) > 10:
            return _empty()

        held_cost = sum(int(unit["cost"]) for unit in inventory.get("units", []) if type(unit.get("cost")) is int)
        if held_cost >= HARD_HELD_COST_CAP:
            return _empty()
        factors = {row.get("sku"): float(row.get("factor", 1.0)) for row in inventory.get("surge", [])}
        held_skus = {unit.get("sku") for unit in inventory.get("units", [])}
        allow_food = approval.get("allow_food") is True
        chosen: list[dict] = []
        planned_worst = 0
        seen: set[str] = set()

        for line in lines:
            if not isinstance(line, dict):
                return _empty()
            sku, qty = line.get("sku"), line.get("qty")
            max_unit_cost = line.get("max_unit_cost")
            if not isinstance(sku, str) or sku in seen or sku in held_skus:
                return _empty()
            if type(qty) is not int or not 1 <= qty <= MAX_QTY_PER_SKU:
                return _empty()
            if type(max_unit_cost) is not int or max_unit_cost <= 0:
                return _empty()
            product = catalog.get(sku)
            if not isinstance(product, dict):
                return _empty()
            list_price, kind = product.get("list_price"), product.get("kind")
            if type(list_price) is not int or list_price <= 0 or kind not in {"food", "goods"}:
                return _empty()
            if kind == "food" and not allow_food:
                return _empty()

            factor = factors.get(sku, 1.0)
            worst_cost = math.ceil(list_price * 0.75 * factor)
            expected_cost_share = 0.50 if kind == "food" else 0.59
            expected_cost = list_price * expected_cost_share * factor
            expected_ask_share = 0.7175 if kind == "food" else 0.755
            expected_margin = list_price * expected_ask_share - expected_cost
            worst_floor = math.ceil(worst_cost * 1.05)
            if worst_cost > max_unit_cost:
                return _empty()
            if expected_margin < list_price * MIN_EXPECTED_MARGIN_SHARE:
                return _empty()
            if worst_floor > list_price * MAX_WORST_FLOOR_SHARE:
                return _empty()
            line_worst = worst_cost * qty
            if planned_worst + line_worst > approved_cap:
                return _empty()
            if held_cost + planned_worst + line_worst > HARD_HELD_COST_CAP:
                return _empty()
            seen.add(sku)
            chosen.append({"sku": sku, "qty": qty})
            planned_worst += line_worst
        # Claim the approval atomically before exposing the executable plan.
        # A hard link is O_EXCL-like: concurrent/repeated hooks cannot replace
        # an existing claim and therefore fail closed without another POST.
        claimed_path = approval_path.with_name("restock-approval.claimed.json")
        os.link(approval_path, claimed_path)
        approval_path.unlink()
        return {"buy": chosen}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return _empty()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    approval_path = args.approval or args.state.parent / "restock-approval.json"
    print(json.dumps(build_plan(args.state, approval_path), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
