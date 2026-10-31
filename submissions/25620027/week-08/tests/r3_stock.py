"""Shared Round 3 fixtures: a kit-shaped .market folder with stock and a catalog."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

HOUR: Final = 3600.0
KIMCHI: Final = "G00000001"
MILK: Final = "G00000002"
LAMP: Final = "G00000003"
CURTAIN: Final = "G00000004"
CATALOG: Final = {
    KIMCHI: {
        "name": "종가 포기김치 10kg",
        "category": "식품>김치/반찬>김치",
        "list_price": 52000,
        "kind": "food",
        "shelf_hours": 720,
    },
    MILK: {
        "name": "소화가 잘되는 우유 24팩",
        "category": "식품>계란/유제품>우유/두유",
        "list_price": 23000,
        "kind": "food",
        "shelf_hours": 48,
    },
    LAMP: {
        "name": "LED 스탠드 램프",
        "category": "가구/침구>조명>스탠드",
        "list_price": 40000,
        "kind": "goods",
        "shelf_hours": None,
    },
    CURTAIN: {
        "name": "이중암막커튼 특대형",
        "category": "가구/침구>침구/홈데코>커튼",
        "list_price": 236900,
        "kind": "goods",
        "shelf_hours": None,
    },
}


def unit(  # noqa: PLR0913 - test fixture
    sku: str,
    number: int,
    *,
    grade: str = "A",
    cost: int | None = None,
    now: float | None = None,
    shelf_left_ratio: float | None = None,
    supplied_hours_ago: float = 1.0,
) -> dict[str, object]:
    """One inventory unit in the kit's shape."""
    stamp = now if now is not None else time.time()
    item = CATALOG[sku]
    list_price = int(item["list_price"])  # type: ignore[arg-type]
    share = {"A": 0.65, "B": 0.5, "C": 0.35}[grade]
    row: dict[str, object] = {
        "unit": f"u-{sku}-{number}",
        "sku": sku,
        "name": item["name"],
        "category": item["category"],
        "list_price": list_price,
        "kind": item["kind"],
        "cost": cost if cost is not None else int(list_price * share),
        "grade": grade,
        "supplied_at": stamp - supplied_hours_ago * HOUR,
        "expires_at": None,
        "cert": {"cert": {"unit": f"u-{sku}-{number}", "sku": sku}, "sig": "x"},
    }
    if item["kind"] == "food":
        shelf = float(item["shelf_hours"] or 0)  # type: ignore[arg-type]
        ratio = shelf_left_ratio if shelf_left_ratio is not None else 0.9
        row["expires_at"] = stamp + shelf * ratio * HOUR
    return row


def write_market(
    folder: Path,
    units: list[dict[str, object]],
    *,
    balance: int = 5_000_000,
    market_round: int = 3,
) -> Path:
    """Write inventory.json, catalog.json and demand.json like kit v4 does."""
    folder.mkdir(parents=True, exist_ok=True)
    _ = (folder / "inventory.json").write_text(
        json.dumps(
            {
                "round": market_round,
                "balance": balance,
                "start": 5_000_000,
                "injected": 0,
                "bankruptcies_left": 2,
                "bankrupt_line": 500_000,
                "storage_per_day": 0.01,
                "units": units,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _ = (folder / "catalog.json").write_text(
        json.dumps(
            {sku: {"sku": sku, **item} for sku, item in CATALOG.items()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    demand = []
    for sku, item in CATALOG.items():
        demand.append(
            {
                "category": item["category"],
                "item": str(item["category"]).split(">")[-1],
                "kind": item["kind"],
                "skus": 1,
                "median_price": item["list_price"],
                "shelf_hours": item["shelf_hours"],
                "_sku": sku,
            }
        )
    _ = (folder / "demand.json").write_text(
        json.dumps(demand, ensure_ascii=False), encoding="utf-8"
    )
    return folder
