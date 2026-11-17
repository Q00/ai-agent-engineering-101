"""
Read a Round 3 inventory snapshot produced by Market Kit v4.


Run locally after the kit has synchronized the shop inventory::

    python market_inventory.py --market-dir .market

This module is read-only. A snapshot is not a reservation: the market must
check availability again at checkout. The kit's local files are not shared
with an independently deployed seller.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

class InventoryError(ValueError):
    """
    if snapshot is missing, malformed, or not a round 3 snapshot.
    """

@dataclass
class InventoryUnit:
    unit: str
    sku: str
    name: str
    category: str | None
    kind: str
    cost: int | float
    list_price: int | float
    grade: str | None
    expires_at: int | float | None
    supplied_at: int | float | None

def _required_text(row: dict[str, Any], key: str, idx: int) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InventoryError(f"units[{idx}] {key} must be a non-empty string")
    return value


def _required_amount(row: dict[str, Any], key: str, index: int) -> int | float:
    value = row.get(key)
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            (isinstance(value, float) and not math.isfinite(value)) or value < 0):
        raise InventoryError(f"units[{index}].{key} must be a finite non-negative number")
    return value


def _optional_time(row: dict[str, Any], key: str, index: int) -> int | float | None:
    value = row.get(key)
    if value is None:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            (isinstance(value, float) and not math.isfinite(value))):
        raise InventoryError(f"units[{index}].{key} must be a finite timestamp or null")
    return value


def parse_inventory(data: Any) -> list[InventoryUnit]:
    """Validate the fields used by the official starter and return stock units.

    The full market API schema is not assumed. Unknown fields are ignored.
    """
    if not isinstance(data, dict):
        raise InventoryError("inventory must be a JSON object")
    round_number = data.get("round")
    if round_number is not None and (isinstance(round_number, bool) or
                                     not isinstance(round_number, int) or round_number < 3):
        raise InventoryError(f"expected a Round 3 inventory; got round={round_number!r}")
    rows = data.get("units")
    if not isinstance(rows, list):
        raise InventoryError("inventory.units must be a JSON array")

    units: list[InventoryUnit] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise InventoryError(f"units[{index}] must be an object")
        category = row.get("category")
        if category is not None and not isinstance(category, str):
            raise InventoryError(f"units[{index}].category must be a string or null")
        kind = _required_text(row, "kind", index)
        if kind not in ("food", "goods"):
            raise InventoryError(f"units[{index}].kind must be 'food' or 'goods'")
        if kind == "food" and row.get("expires_at") is None:
            raise InventoryError(f"units[{index}].expires_at is required for food")
        unit = InventoryUnit(
            unit=_required_text(row, "unit", index),
            sku=_required_text(row, "sku", index),
            name=_required_text(row, "name", index),
            category=category,
            kind=kind,
            cost=_required_amount(row, "cost", index),
            list_price=_required_amount(row, "list_price", index),
            grade=_required_text(row, "grade", index),
            expires_at=_optional_time(row, "expires_at", index),
            supplied_at=_optional_time(row, "supplied_at", index),
        )
        if unit.unit in seen:
            raise InventoryError(f"duplicate unit id: {unit.unit}")
        seen.add(unit.unit)
        units.append(unit)
    return units


def read_inventory(market_dir: Path) -> dict[str, Any]:
    """Read inventory.json without fabricating fallback stock."""
    path = market_dir / "inventory.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InventoryError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise InventoryError("inventory must be a JSON object")
    return data


def load_inventory(market_dir: Path) -> list[InventoryUnit]:
    return parse_inventory(read_inventory(market_dir))


def load_catalog(market_dir: Path) -> dict[str, dict[str, Any]]:
    """Read the kit's SKU-keyed catalog without treating list price as cost."""
    path = market_dir / "catalog.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InventoryError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise InventoryError("catalog must be an object indexed by SKU")
    if any(not isinstance(sku, str) or not isinstance(product, dict)
           for sku, product in data.items()):
        raise InventoryError("catalog must map SKU strings to product objects")
    return data


def available_units(units: list[InventoryUnit], *, now: float | None = None) -> list[InventoryUnit]:
    """Exclude visibly expired units; this does not guarantee checkout stock."""
    now = time.time() if now is None else now
    return [u for u in units if u.grade.lower() != "expired" and
            (u.expires_at is None or u.expires_at > now)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-dir", type=Path, default=Path(os.getenv("MARKET_DIR", ".market")))
    parser.add_argument("--sku", help="show one SKU from the catalog and its stock")
    args = parser.parse_args()
    try:
        snapshot = read_inventory(args.market_dir)
        stock = parse_inventory(snapshot)
        catalog = load_catalog(args.market_dir)
        if args.sku and args.sku not in catalog:
            raise InventoryError(f"SKU {args.sku!r} not found in catalog")
        age = max(0.0, time.time() - (args.market_dir / "inventory.json").stat().st_mtime)
    except (InventoryError, OSError) as exc:
        print(f"inventory error: {exc}", file=sys.stderr)
        return 1
    available = available_units(stock)
    selected = [u for u in available if not args.sku or u.sku == args.sku]
    result = {"round": snapshot.get("round"), "catalog_skus": len(catalog), "total_units": len(stock),
              "available_units": len(available), "inventory_file_age_seconds": round(age, 1),
              "units": [asdict(u) for u in selected]}
    if args.sku:
        result["catalog_product"] = catalog[args.sku]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())   