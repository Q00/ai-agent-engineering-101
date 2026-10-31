"""Versioned, bounded trading policy shared by the seller, buyer, restock and manager.

The fast trade loop never calls a model: every price, acceptance threshold, stock
rule and buyer acceptance condition is looked up here. The slow policy loop (the
manager) rewrites this file inside the bounds declared below, keeps the previous
two versions and records why it changed. Budgets, keys, settlement and the market
contract are not policy and cannot be changed through this file.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Final, Literal

from pydantic import Field, ValidationError

from market_a2a.models import FrozenModel

if TYPE_CHECKING:
    from pydantic import JsonValue

Grade = Literal["A", "B", "C"]
GRADES: Final[tuple[Grade, ...]] = ("A", "B", "C")
POLICY_ENV: Final = "AX_R3_POLICY"
POLICY_FILE: Final = "r3-policy.json"
MAX_HISTORY: Final = 2


class SellerPolicy(FrozenModel):
    """How the shop quotes, concedes and accepts; all prices derive from stock facts."""

    # Opening ask as a share of the catalog list price, per certified grade.
    markup_a: float = Field(default=0.95, ge=0.30, le=1.00)
    markup_b: float = Field(default=0.85, ge=0.30, le=1.00)
    markup_c: float = Field(default=0.70, ge=0.25, le=1.00)
    # Never quote below cost * (1 + min_margin) unless the unit is in clearance.
    min_margin: float = Field(default=0.05, ge=0.0, le=0.50)
    # Accept any offer at or above cost * (1 + accept_margin) without more rounds.
    accept_margin: float = Field(default=0.15, ge=0.0, le=1.00)
    # Each counter moves this share of the way from the ask toward the accept line.
    concession: float = Field(default=0.30, ge=0.05, le=1.00)
    max_rounds: int = Field(default=5, ge=1, le=12)
    # Food below this share of shelf life left is cleared, even below cost.
    clearance_shelf_ratio: float = Field(default=0.20, ge=0.0, le=0.60)
    clearance_floor_of_cost: float = Field(default=0.60, ge=0.10, le=1.00)
    # Goods unsold this long are marked down toward cost (storage costs 1%/day).
    stale_goods_hours: float = Field(default=168.0, ge=1.0, le=720.0)
    stale_markup_cut: float = Field(default=0.10, ge=0.0, le=0.50)
    # Warranty hours promised per grade of goods; 0 means no warranty.
    warranty_a: int = Field(default=6, ge=0, le=24)
    warranty_b: int = Field(default=0, ge=0, le=24)
    warranty_c: int = Field(default=0, ge=0, le=24)
    claim_grade: bool = True
    # A food grade claim must match the grade at checkout or at settlement. When the
    # grade would change within this horizon, the claim is omitted instead of guessed.
    claim_horizon_seconds: int = Field(default=180, ge=0, le=900)

    def markup(self, grade: str) -> float:
        """Return the opening markup for a grade; unknown grades price as C."""
        match grade:
            case "A":
                return self.markup_a
            case "B":
                return self.markup_b
            case _:
                return self.markup_c

    def warranty(self, grade: str) -> int:
        """Return promised warranty hours for goods of this grade."""
        match grade:
            case "A":
                return self.warranty_a
            case "B":
                return self.warranty_b
            case _:
                return self.warranty_c


class BuyerPolicy(FrozenModel):
    """How the buyer values offers; surplus is list price minus what we pay."""

    first_offer_ratio: float = Field(default=0.60, ge=0.20, le=1.00)
    # Accept the seller's ask at once when it is at or below this share of list.
    quick_accept_ratio: float = Field(default=0.72, ge=0.20, le=1.00)
    # Never pay more than this share of the shipped SKU's list price.
    max_price_ratio: float = Field(default=0.95, ge=0.20, le=1.00)
    max_rounds: int = Field(default=4, ge=1, le=10)
    # Extra shelf life demanded beyond the order's use_within_hours.
    food_margin_hours: float = Field(default=1.0, ge=0.0, le=48.0)
    require_cert: bool = True
    goods_grades: tuple[Grade, ...] = Field(default=("A", "B"), min_length=1)
    # Warranty hours demanded for goods; 0 does not demand one.
    goods_warranty_hours: int = Field(default=0, ge=0, le=24)
    # Shops whose checkout refused payment are avoided for this long.
    refusal_memory_hours: float = Field(default=24.0, ge=0.0, le=336.0)
    # The market hands out one order about every 20 s, so waiting up to 18 s for a
    # slow seller costs no throughput.
    order_seconds: float = Field(default=18.0, ge=3.0, le=120.0)
    # After this share of the budget is spent, accept any ask within the cap.
    late_accept_share: float = Field(default=0.45, ge=0.1, le=1.0)


class RestockPolicy(FrozenModel):
    """What the shop buys; the kit executes the plan, the market draws the costs."""

    reserve_cash: int = Field(default=1_500_000, ge=0, le=5_000_000)
    seed_budget: int = Field(default=2_500_000, ge=0, le=5_000_000)
    units_per_sku: int = Field(default=1, ge=1, le=20)
    max_units: int = Field(default=400, ge=1, le=5000)
    min_food_shelf_hours: int = Field(default=720, ge=24, le=720)
    food_min_list_price: int = Field(default=5_000, ge=0)
    food_max_list_price: int = Field(default=60_000, ge=0)
    goods_min_list_price: int = Field(default=8_000, ge=0)
    goods_max_list_price: int = Field(default=150_000, ge=0)
    replenish_qty: int = Field(default=1, ge=1, le=20)
    stale_hours: float = Field(default=48.0, ge=1.0, le=336.0)
    categories_per_run: int = Field(default=10, ge=1, le=10)
    food_share: float = Field(default=0.6, ge=0.0, le=1.0)


class Policy(FrozenModel):
    """One complete, validated policy version."""

    version: int = Field(default=1, ge=1)
    updated_at: str = ""
    reason: str = Field(default="defaults", max_length=400)
    seller: SellerPolicy = Field(default_factory=SellerPolicy)
    buyer: BuyerPolicy = Field(default_factory=BuyerPolicy)
    restock: RestockPolicy = Field(default_factory=RestockPolicy)


class PolicyHistory(FrozenModel):
    """The active policy and at most two earlier versions for rollback."""

    current: Policy
    previous: tuple[Policy, ...] = Field(default=(), max_length=MAX_HISTORY)


def policy_path(market_dir: Path | None = None) -> Path:
    """Resolve the policy file: explicit env, else beside the kit's .market folder."""
    configured = os.environ.get(POLICY_ENV)
    if configured:
        return Path(configured)
    base = (
        market_dir
        if market_dir is not None
        else Path(os.environ.get("MARKET_DIR", ".market"))
    )
    return base.resolve().parent / POLICY_FILE


def load_policy(path: Path) -> Policy:
    """Return the active policy; a missing or invalid file yields the defaults."""
    try:
        return PolicyHistory.model_validate_json(path.read_bytes()).current
    except (OSError, ValidationError, ValueError):
        return Policy()


def load_history(path: Path) -> PolicyHistory:
    """Return the full history or a default history when none exists."""
    try:
        return PolicyHistory.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError):
        return PolicyHistory(current=Policy())


def save_policy(path: Path, candidate: Policy, reason: str) -> Policy:
    """Validate, version and atomically publish a policy; keep two previous versions."""
    history = load_history(path)
    current = history.current
    stamped = candidate.model_copy(
        update={
            "version": current.version + 1,
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "reason": reason[:400],
        }
    )
    published = PolicyHistory(
        current=stamped, previous=(current, *history.previous)[:MAX_HISTORY]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            _ = stream.write(published.model_dump_json(indent=1))
            stream.flush()
            _ = temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return stamped


def rollback_policy(path: Path, reason: str) -> Policy | None:
    """Make the previous version active again; the ledger of past trades stays."""
    history = load_history(path)
    if not history.previous:
        return None
    restored = history.previous[0]
    return save_policy(path, restored, f"rollback: {reason}")


def apply_changes(policy: Policy, changes: dict[str, JsonValue]) -> Policy:
    """Merge a flat ``section.field`` change set into a policy, validating bounds."""
    sections: dict[str, dict[str, JsonValue]] = {
        "seller": {},
        "buyer": {},
        "restock": {},
    }
    for key, value in changes.items():
        section, _, field = key.partition(".")
        if section not in sections or not field:
            message = f"Unknown policy field: {key}"
            raise ValueError(message)
        sections[section][field] = value
    return policy.model_copy(
        update={
            "seller": SellerPolicy.model_validate(
                {**policy.seller.model_dump(), **sections["seller"]}
            ),
            "buyer": BuyerPolicy.model_validate(
                {**policy.buyer.model_dump(), **sections["buyer"]}
            ),
            "restock": RestockPolicy.model_validate(
                {**policy.restock.model_dump(), **sections["restock"]}
            ),
        }
    )
