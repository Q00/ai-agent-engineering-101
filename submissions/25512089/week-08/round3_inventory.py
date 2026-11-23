"""Round 3 inventory helpers. Read-only; no keys, network calls or model calls.

The market kit refreshes .market/inventory.json. Always treat it as a snapshot;
settlement is authoritative and may reject an item sold in the meantime.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any, Iterable


def market_dir() -> Path:
    return Path(os.environ.get("MARKET_DIR", ".market"))


def read_inventory() -> dict[str, Any]:
    try:
        data = json.loads((market_dir() / "inventory.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def is_round3_inventory(inventory: dict[str, Any]) -> bool:
    return int(inventory.get("round") or 0) >= 3


def _has_sku(text: str, sku: str) -> bool:
    # SKU must match a complete token. G001 must not match G0012.
    return re.search(r"(?<![\w])" + re.escape(sku) + r"(?![\w])", text, flags=re.I) is not None


def find_stock_unit(text: str, occupied: Iterable[str] = ()) -> dict[str, Any] | None:
    """Choose a still-stocked unit for a request identifying SKU, name or category.

    An exact SKU is preferred, then matching product names, then exact category.
    Does not promise a unit that another open negotiation already selected.
    """
    inv = read_inventory()
    if not is_round3_inventory(inv):
        return None
    lower = text.casefold()
    busy = set(occupied)
    choices: list[tuple[int, dict[str, Any]]] = []
    for unit in inv.get("units", []):
        if not isinstance(unit, dict):
            continue
        sku = str(unit.get("sku") or "")
        name = str(unit.get("name") or "")
        category = str(unit.get("category") or "")
        unit_id = str(unit.get("unit") or "")
        if not (sku and unit_id) or unit_id in busy or unit.get("grade") == "expired":
            continue
        if unit.get("kind") == "food" and unit.get("expires_at") is not None:
            try:
                if float(unit["expires_at"]) <= __import__("time").time():
                    continue
            except (TypeError, ValueError):
                continue
        if _has_sku(text, sku):
            rank = 0
        elif name and name.casefold() in lower:
            rank = 1
        elif category and category.split(">")[-1].strip().casefold() in lower:
            rank = 2
        else:
            continue
        choices.append((rank, unit))
    if not choices:
        return None
    def key(entry: tuple[int, dict[str, Any]]):
        rank, u = entry
        expiry = float(u.get("expires_at") or 1e20)
        return rank, expiry, float(u.get("supplied_at") or 0)
    return min(choices, key=key)[1]


def selection_from_stock(text: str, occupied: Iterable[str] = ()) -> dict[str, Any] | None:
    unit = find_stock_unit(text, occupied)
    if unit is None:
        return None
    cost = int(math.ceil(float(unit["cost"])))
    list_price = int(math.ceil(float(unit.get("list_price") or cost)))
    # An explicit margin protects against selling below cost (storage risk remains).
    reserve = max(cost + 1, math.ceil(cost * 1.05))
    price_ceiling = max(reserve, round(list_price * 1.08))
    return {
        "item": str(unit["sku"]),
        "name": str(unit.get("name") or unit["sku"]),
        "unit": str(unit["unit"]),
        "reserve": reserve,
        "list_price": list_price,
        "price_ceiling": price_ceiling,
        "kind": str(unit.get("kind") or ""),
        "round3": True,
    }


def unit_still_available(unit_id: str) -> bool:
    if not unit_id:
        return False
    inventory = read_inventory()
    return any(isinstance(u, dict) and u.get("unit") == unit_id and u.get("grade") != "expired"
               for u in inventory.get("units", []))
