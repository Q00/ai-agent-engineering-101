"""Versioned, bounded trading policy shared by the seller, buyer, restock and manager.

The fast trade loop never calls a model: every price, acceptance threshold, stock
rule and buyer acceptance condition is looked up here. Two slow loops rewrite this
file inside the bounds declared below. The **strategy** layer (Sonnet, hourly) owns
the structural fields and sets a band for each tactical field; the **tactical**
layer (Haiku, every ten minutes) may move only tactical fields, only inside their
band, and only for a limited time, after which the strategy values apply again. One
owner per field: a change to a field by the other layer is refused. Budgets, keys,
settlement and the market contract are not policy and cannot be changed here.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Final, Literal

from pydantic import Field, JsonValue, ValidationError

from market_a2a.models import FrozenModel

if TYPE_CHECKING:
    from collections.abc import Mapping

Grade = Literal["A", "B", "C"]
GRADES: Final[tuple[Grade, ...]] = ("A", "B", "C")
POLICY_ENV: Final = "AX_R3_POLICY"
POLICY_FILE: Final = "r3-policy.json"
MAX_HISTORY: Final = 2
MAX_TACTICAL_CHANGES: Final = 3
TACTICAL_TTL_SECONDS: Final = 3600.0
# Fields the tactical layer may move inside a band; everything else is strategy.
TACTICAL_FIELDS: Final = frozenset(
    {
        "seller.accept_margin",
        "seller.concession",
        "seller.clearance_shelf_ratio",
        "seller.clearance_floor_of_cost",
        "seller.stale_markup_cut",
        "buyer.first_offer_ratio",
        "buyer.quick_accept_ratio",
        "buyer.late_accept_share",
        "restock.replenish_qty",
    }
)


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
    # slow seller costs no throughput; beyond ~30 s every extra second drops orders,
    # so the bound stays near the cadence whatever a model proposes.
    order_seconds: float = Field(default=18.0, ge=3.0, le=30.0)
    # After this share of the budget is spent, accept any ask within the cap.
    late_accept_share: float = Field(default=0.45, ge=0.1, le=1.0)
    # e-greedy: this share of orders goes to a random eligible shop instead of the
    # best-ranked one, so cheaper or newer shops get found (2026-11-03 user ask).
    explore_rate: float = Field(default=0.10, ge=0.0, le=0.5)
    # Exploration skips shops with a failed deal this recent; refusals and tricks
    # still count over refusal_memory_hours.
    explore_memory_hours: float = Field(default=6.0, ge=0.5, le=48.0)
    # A shop whose last attempts all ended without a deal sits out this long.
    fail_streak: int = Field(default=3, ge=1, le=20)
    fail_cooldown_minutes: float = Field(default=30.0, ge=0.0, le=720.0)


class RestockPolicy(FrozenModel):
    """What the shop buys; the kit executes the plan, the market draws the costs."""

    reserve_cash: int = Field(default=1_500_000, ge=0, le=5_000_000)
    seed_budget: int = Field(default=2_500_000, ge=0, le=20_000_000)
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
    # Share of the kit's ten buy lines allowed for food, including replenishment.
    # Demand signals may rank those lines, never override the allowance.
    food_share: float = Field(default=0.6, ge=0.0, le=1.0)


class Policy(FrozenModel):
    """One complete, validated policy version."""

    version: int = Field(default=1, ge=1)
    updated_at: str = ""
    reason: str = Field(default="defaults", max_length=400)
    seller: SellerPolicy = Field(default_factory=SellerPolicy)
    buyer: BuyerPolicy = Field(default_factory=BuyerPolicy)
    restock: RestockPolicy = Field(default_factory=RestockPolicy)


class Band(FrozenModel):
    """The strategy layer's allowed range for one tactical field."""

    low: float
    high: float


class TacticalOverlay(FrozenModel):
    """Bounded, time-limited changes on top of the strategy values."""

    changes: dict[str, JsonValue] = Field(
        default_factory=dict, max_length=MAX_TACTICAL_CHANGES
    )
    reason: str = Field(default="", max_length=300)
    applied_at: float = 0.0
    expires_at: float = 0.0
    version: int = Field(default=1, ge=1)


class PolicyHistory(FrozenModel):
    """The effective policy, the strategy base beneath it, bands and two old versions.

    ``current`` is what the trade loop reads: the strategy values with the tactical
    overlay applied. ``strategy`` is None in files written before the two layers
    existed; ``current`` is then the base.
    """

    current: Policy
    previous: tuple[Policy, ...] = Field(default=(), max_length=MAX_HISTORY)
    strategy: Policy | None = None
    tactical: TacticalOverlay | None = None
    bands: dict[str, Band] = Field(default_factory=dict)

    def base(self) -> Policy:
        """Return the strategy layer's policy without any tactical overlay."""
        return self.strategy if self.strategy is not None else self.current


SECTIONS: Final = {
    "seller": SellerPolicy,
    "buyer": BuyerPolicy,
    "restock": RestockPolicy,
}


def field_bounds(key: str) -> tuple[float | None, float | None]:
    """Return the declared (ge, le) bounds of a numeric policy field."""
    section, _, field = key.partition(".")
    model = SECTIONS.get(section)
    if model is None or field not in model.model_fields:
        message = f"Unknown policy field: {key}"
        raise ValueError(message)
    low: float | None = None
    high: float | None = None
    for item in model.model_fields[field].metadata:
        value = getattr(item, "ge", None)
        if value is not None:
            low = float(value)
        value = getattr(item, "le", None)
        if value is not None:
            high = float(value)
    return low, high


def field_value(policy: Policy, key: str) -> JsonValue:
    """Read ``section.field`` from a policy."""
    section, _, field = key.partition(".")
    if section not in SECTIONS or field not in SECTIONS[section].model_fields:
        message = f"Unknown policy field: {key}"
        raise ValueError(message)
    value = getattr(getattr(policy, section), field)
    if isinstance(value, tuple):
        return list(value)
    return value


def default_band(key: str, base: Policy, share: float = 0.10) -> Band:
    """Return a +-10% band around the strategy value, clipped to the field bounds."""
    value = field_value(base, key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        message = f"Field has no numeric band: {key}"
        raise TypeError(message)
    low, high = field_bounds(key)
    step = max(abs(float(value)) * share, 1.0 if isinstance(value, int) else 0.01)
    band_low = float(value) - step
    band_high = float(value) + step
    if low is not None:
        band_low = max(band_low, low)
    if high is not None:
        band_high = min(band_high, high)
    return Band(low=round(band_low, 6), high=round(band_high, 6))


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


def load_history(path: Path) -> PolicyHistory:
    """Return the full history or a default history when none exists."""
    try:
        return PolicyHistory.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError):
        return PolicyHistory(current=Policy())


def load_policy(path: Path, now: float | None = None) -> Policy:
    """Return the effective policy; an expired tactical overlay yields the base.

    A missing or invalid file yields the defaults, so a trade never waits on the
    manager.
    """
    history = load_history(path)
    stamp = now if now is not None else time.time()
    overlay = history.tactical
    if overlay is not None and stamp >= overlay.expires_at:
        return history.base()
    return history.current


def _write(path: Path, history: PolicyHistory) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            _ = stream.write(history.model_dump_json(indent=1))
            stream.flush()
            _ = temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _stamp(candidate: Policy, version: int, reason: str) -> Policy:
    return candidate.model_copy(
        update={
            "version": version,
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "reason": reason[:400],
        }
    )


def validate_bands(bands: Mapping[str, Band], base: Policy) -> dict[str, Band]:
    """Keep only bands on tactical fields that lie inside the field's own bounds."""
    accepted: dict[str, Band] = {}
    for key, band in bands.items():
        if key not in TACTICAL_FIELDS:
            message = f"Band on a non-tactical field: {key}"
            raise ValueError(message)
        low, high = field_bounds(key)
        if band.low > band.high:
            message = f"Band low above high: {key}"
            raise ValueError(message)
        if (low is not None and band.low < low) or (
            high is not None and band.high > high
        ):
            message = f"Band outside the field bounds: {key}"
            raise ValueError(message)
        _ = field_value(base, key)
        accepted[key] = band
    return accepted


def save_policy(
    path: Path,
    candidate: Policy,
    reason: str,
    *,
    bands: Mapping[str, Band] | None = None,
) -> Policy:
    """Publish a strategy version: it becomes the base and clears any tactical overlay.

    Keeps two previous effective versions for rollback. ``bands`` replaces the
    tactical bands when given; otherwise the stored bands stay.
    """
    history = load_history(path)
    current = history.current
    stamped = _stamp(candidate, current.version + 1, reason)
    kept = validate_bands(bands, stamped) if bands is not None else history.bands
    _write(
        path,
        PolicyHistory(
            current=stamped,
            previous=(current, *history.previous)[:MAX_HISTORY],
            strategy=stamped,
            tactical=None,
            bands=kept,
        ),
    )
    return stamped


def save_bands(path: Path, bands: Mapping[str, Band]) -> dict[str, Band]:
    """Replace the tactical bands only: no new version, the overlay stays."""
    history = load_history(path)
    kept = validate_bands(bands, history.base())
    _write(path, history.model_copy(update={"bands": kept}))
    return kept


def widen_band(key: str, band: Band, side: str, share: float = 0.5) -> Band:
    """Extend ``band`` on ``side`` ("low" or "high") by ``share`` of its span.

    Clipped to the field's own bounds; an already fully open side stays put.
    """
    span = max(band.high - band.low, 1e-6)
    low, high = field_bounds(key)
    new_low, new_high = band.low, band.high
    if side == "low":
        new_low = band.low - span * share
        if low is not None:
            new_low = max(new_low, low)
    else:
        new_high = band.high + span * share
        if high is not None:
            new_high = min(new_high, high)
    return Band(low=round(new_low, 6), high=round(new_high, 6))


def strategy_changes(changes: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Refuse strategy changes that touch tactical fields (one owner per field)."""
    for key in changes:
        if key in TACTICAL_FIELDS:
            message = f"Tactical field is not owned by the strategy layer: {key}"
            raise ValueError(message)
    return dict(changes)


def save_tactical(
    path: Path,
    changes: Mapping[str, JsonValue],
    reason: str,
    *,
    now: float | None = None,
    ttl: float = TACTICAL_TTL_SECONDS,
) -> Policy:
    """Apply at most three tactical changes inside their bands for ``ttl`` seconds."""
    if len(changes) > MAX_TACTICAL_CHANGES:
        message = f"At most {MAX_TACTICAL_CHANGES} tactical changes per step"
        raise ValueError(message)
    history = load_history(path)
    base = history.base()
    stamp = now if now is not None else time.time()
    for key, value in changes.items():
        if key not in TACTICAL_FIELDS:
            message = f"Not a tactical field: {key}"
            raise ValueError(message)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            message = f"Tactical value must be a number: {key}"
            raise TypeError(message)
        band = history.bands.get(key) or default_band(key, base)
        if not band.low <= float(value) <= band.high:
            message = f"Tactical value outside its band: {key}"
            raise ValueError(message)
    effective = apply_changes(base, dict(changes))
    stamped = _stamp(effective, history.current.version + 1, f"tactical: {reason}")
    overlay = TacticalOverlay(
        changes=dict(changes),
        reason=reason[:300],
        applied_at=stamp,
        expires_at=stamp + ttl,
        version=stamped.version,
    )
    _write(
        path,
        PolicyHistory(
            current=stamped,
            previous=(history.current, *history.previous)[:MAX_HISTORY],
            strategy=base,
            tactical=overlay,
            bands=history.bands,
        ),
    )
    return stamped


def expire_tactical(path: Path, now: float | None = None) -> Policy | None:
    """Drop an expired overlay from the file; return the base when one was dropped."""
    history = load_history(path)
    overlay = history.tactical
    stamp = now if now is not None else time.time()
    if overlay is None or stamp < overlay.expires_at:
        return None
    base = _stamp(history.base(), history.current.version + 1, "tactical expired")
    _write(
        path,
        PolicyHistory(
            current=base,
            previous=(history.current, *history.previous)[:MAX_HISTORY],
            strategy=base,
            tactical=None,
            bands=history.bands,
        ),
    )
    return base


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
