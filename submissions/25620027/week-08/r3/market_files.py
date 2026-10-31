"""Typed readers for the files kit v4 keeps in ``.market/`` (Round 3 agent contract).

Every reader tolerates a missing or half-written file by returning an empty value,
because the kit replaces these files on its own schedule and a trade must never
crash on a stale snapshot. Unknown fields are ignored so a newer kit still parses.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

FOOD_GRADE_CUTS = (0.8, 0.45)  # the market's shares of shelf life left for A, B


class KitFile(BaseModel):
    """Immutable view of a kit-written document; extra fields are ignored."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")


class Unit(KitFile):
    """One stocked unit; cost and grade were fixed by the market when it arrived."""

    unit: str
    sku: str
    name: str = ""
    category: str = ""
    list_price: int = 0
    kind: Literal["food", "goods"] = "goods"
    cost: int = 0
    grade: str = "C"
    supplied_at: float = 0.0
    expires_at: float | None = None
    cert: dict[str, object] | None = None

    def shelf_left_ratio(self, now: float, shelf_hours: float | None) -> float | None:
        """Share of shelf life left now, or None for goods or unknown shelf life."""
        if self.kind != "food" or self.expires_at is None or not shelf_hours:
            return None
        return max(0.0, (self.expires_at - now) / 3600.0) / shelf_hours

    def current_food_grade(self, now: float, shelf_hours: float | None) -> str:
        """The grade the market would certify now; falls back to the stored grade."""
        ratio = self.shelf_left_ratio(now, shelf_hours)
        if ratio is None:
            return self.grade
        if ratio >= FOOD_GRADE_CUTS[0]:
            return "A"
        return "B" if ratio >= FOOD_GRADE_CUTS[1] else "C"


class Inventory(KitFile):
    """The shop's wallet and stock as the kit last read it (up to 10 s old)."""

    round: int = 0
    balance: int = 0
    start: int = 5_000_000
    injected: int = 0
    bankruptcies_left: int = 2
    bankrupt_line: int = 500_000
    storage_per_day: float = 0.01
    units: tuple[Unit, ...] = ()


class CatalogItem(KitFile):
    """One market SKU."""

    sku: str = ""
    name: str = ""
    category: str = ""
    list_price: int = 0
    kind: Literal["food", "goods"] = "goods"
    shelf_hours: int | None = None


class DemandRow(KitFile):
    """A category orders can name; the kit publishes no demand weights."""

    category: str
    item: str = ""
    kind: Literal["food", "goods"] = "goods"
    skus: int = 0
    median_price: int = 0
    shelf_hours: int | None = None


class Order(KitFile):
    """The buyer's current order without its private mandates."""

    order: str = ""
    item: str = ""
    category: str = ""
    budget: int = 0
    acceptable: tuple[str, ...] = ()
    kind: Literal["food", "goods"] | None = None
    use_within_hours: float | None = None
    expires: float | None = None


class ShopOffer(KitFile):
    """A SKU of the ordered category that one shop has in stock."""

    sku: str
    name: str = ""
    qty: int = 0


class PublicReview(KitFile):
    """A third-party review as the market shows it; untrusted text."""

    stars: int = 3
    review: str = ""
    round: int = 0


class ShopEntry(KitFile):
    """One registered shop as listed for this order."""

    name: str
    url: str
    reviews: tuple[PublicReview, ...] = ()
    offers: tuple[ShopOffer, ...] = ()


class Settled(KitFile):
    """The market's mandate receipt for a paid deal."""

    shop: str = ""
    unit: str = ""
    sku: str = ""
    name: str = ""
    grade: str = ""
    claim: str | None = None
    claim_honest: bool | None = None
    expires_at: float | None = None
    warranty_hours: float | None = None
    value: int | None = None
    ledger: int | None = None


class Deal(KitFile):
    """Input of the review hook: the order, our receipt and the market's receipt."""

    order: Order = Field(default_factory=Order)
    receipt: dict[str, object] = Field(default_factory=dict)
    settled: Settled = Field(default_factory=Settled)


class PaymentLine(KitFile):
    """One line of payments.jsonl: what the kit did after an agreed deal."""

    order: str = ""
    shop: str = ""
    item: str = ""
    price: int = 0
    paid: bool = False
    ledger: int | None = None
    reason: str | None = None


class BrokenLine(KitFile):
    """One line of broken.jsonl: a bought good that broke."""

    name: str = ""
    broke_at: float = 0.0


def market_dir(explicit: Path | None = None) -> Path:
    """The kit's state folder: explicit, else MARKET_DIR, else ./.market."""
    if explicit is not None:
        return explicit
    return Path(os.environ.get("MARKET_DIR", ".market"))


def _read_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_inventory(folder: Path) -> Inventory:
    """Read inventory.json; an unreadable file means no known stock."""
    raw = _read_json(folder / "inventory.json")
    try:
        return Inventory.model_validate(raw) if raw is not None else Inventory()
    except ValidationError:
        return Inventory()


def read_catalog(folder: Path) -> dict[str, CatalogItem]:
    """Read catalog.json (sku -> item). Missing fields default; bad rows are skipped."""
    raw = _read_json(folder / "catalog.json")
    if not isinstance(raw, dict):
        return {}
    catalog: dict[str, CatalogItem] = {}
    for sku, value in raw.items():
        try:
            item = CatalogItem.model_validate(value)
        except ValidationError:
            continue
        catalog[str(sku)] = item.model_copy(update={"sku": item.sku or str(sku)})
    return catalog


def read_demand(folder: Path) -> tuple[DemandRow, ...]:
    """Read demand.json: every category an order can come from."""
    raw = _read_json(folder / "demand.json")
    try:
        return TypeAdapter(tuple[DemandRow, ...]).validate_python(raw or ())
    except ValidationError:
        return ()


def read_order(path: Path) -> Order:
    """Read the current order.json."""
    raw = _read_json(path)
    try:
        return Order.model_validate(raw) if raw is not None else Order()
    except ValidationError:
        return Order()


def read_shops(path: Path) -> tuple[ShopEntry, ...]:
    """Read shops.json for the current order."""
    raw = _read_json(path)
    try:
        return TypeAdapter(tuple[ShopEntry, ...]).validate_python(raw or ())
    except ValidationError:
        return ()


def read_deal(path: Path) -> Deal:
    """Read deal.json for the review hook."""
    raw = _read_json(path)
    try:
        return Deal.model_validate(raw) if raw is not None else Deal()
    except ValidationError:
        return Deal()


def read_jsonl_tail(path: Path, max_bytes: int = 2_000_000) -> list[dict[str, object]]:
    """Parse the last ``max_bytes`` of a JSONL file; a torn first line is skipped."""
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            if size > max_bytes:
                _ = stream.seek(size - max_bytes)
                _ = stream.readline()
            raw = stream.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    rows: list[dict[str, object]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def read_payments(folder: Path) -> tuple[PaymentLine, ...]:
    """Read the tail of payments.jsonl."""
    lines: list[PaymentLine] = []
    for row in read_jsonl_tail(folder / "payments.jsonl"):
        try:
            lines.append(PaymentLine.model_validate(row))
        except ValidationError:
            continue
    return tuple(lines)


def read_broken(folder: Path) -> tuple[BrokenLine, ...]:
    """Read the tail of broken.jsonl."""
    lines: list[BrokenLine] = []
    for row in read_jsonl_tail(folder / "broken.jsonl"):
        try:
            lines.append(BrokenLine.model_validate(row))
        except ValidationError:
            continue
    return tuple(lines)


def category_leaf(category: str) -> str:
    """The last segment of ``a>b>c``; orders name categories by this leaf."""
    return category.split(">")[-1].strip()
