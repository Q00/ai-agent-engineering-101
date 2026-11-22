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

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

from pydantic import Field, JsonValue, ValidationError

from market_a2a.models import FrozenModel
from r3.policy_storage import (
    SnapshotConflictError,
    coherent_snapshot,
    locked,
    read_expiries,
    read_previous_snapshot,
    reader_lock,
    write_snapshot,
)

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
        "restock.depth_skus",
    }
)


# Goods break only within 6 h of a sale (market rule), so a longer warranty costs
# nothing more. The market refuses a settle whose warranty is above 72 h
# (claim_and_warranty_well_formed, seen 11/5 13:44 with 8760 h), so 72 is the cap.
WARRANTY_MAX_HOURS: Final = 72


class SellerPolicy(FrozenModel):
    """How the shop quotes, concedes and accepts; all prices derive from stock facts."""

    # Opening ask as a share of the catalog list price, per certified grade.
    markup_a: float = Field(default=0.95, ge=0.30, le=1.00)
    markup_b: float = Field(default=0.85, ge=0.30, le=1.00)
    markup_c: float = Field(default=0.70, ge=0.25, le=1.00)
    # Owner rule (11/4): stars are 20% of the shop's ALL and ours ranked 32nd of 47.
    # Deals at 0.8+ of list drew 4.3-4.4 stars, 0.6-0.79 drew 4.7-4.8, so no ask
    # opens above this share of list unless the accept line itself is higher.
    # Live policy holds 0.76 since the 11/4 20:33 trial (market 0.755): dear sales
    # 69 -> 102/h, margin +37%; the default stays 0.79 for a fresh policy file.
    max_ask_ratio: float = Field(default=0.79, ge=0.50, le=1.00)
    # Owner rule (11/4): every deal brings one review of equal weight, and goods
    # under 50k list made 22% of our deals but 1% of margin (4.2k a deal), so they
    # open lower still and buy stars almost for free; dear goods stay at 0.79
    # (after the cap their margin per hour rose 18% on more volume).
    cheap_list_price: int = Field(default=50_000, ge=0, le=1_000_000)
    cheap_max_ask_ratio: float = Field(default=0.70, ge=0.50, le=1.00)
    # Never quote below cost * (1 + min_margin) unless the unit is in clearance.
    min_margin: float = Field(default=0.05, ge=0.0, le=0.50)
    # Accept any offer at or above cost * (1 + accept_margin) without more rounds.
    accept_margin: float = Field(default=0.15, ge=0.0, le=1.00)
    # Grade C's own accept margin (None = accept_margin): C breaks about 17% within
    # the warranty, so its accept line can carry that risk (owner trial, 11/4).
    accept_margin_c: float | None = Field(default=None, ge=0.0, le=1.00)
    # High-ticket goods (list >= high_list_price) accept at cost * (1 + this): a
    # 2.3M unit bought at 70-80 % of list sat at 0.90-1.03 of list on the 29 %
    # margin while the market paid ~0.79 (11/5); 8 % of cost is still ~130k each.
    high_list_price: int = Field(default=1_000_000, ge=0, le=100_000_000)
    accept_margin_high: float | None = Field(default=None, ge=0.0, le=1.00)
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
    warranty_a: int = Field(default=6, ge=0, le=WARRANTY_MAX_HOURS)
    warranty_b: int = Field(default=0, ge=0, le=WARRANTY_MAX_HOURS)
    warranty_c: int = Field(default=0, ge=0, le=WARRANTY_MAX_HOURS)
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
    # Owner emergency 11/7-11/8: a purchase must add at least this expected Saved
    # credit; 0.34 first, 0.30 once the final average (~0.28) set the right line.
    min_expected_credit: float = Field(default=0.34, ge=0.25, le=0.50)
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
    # Stage 1 of the owner's bandit rollout (11/4): rank shops on a Thompson draw
    # of "has the listed stock" x "our payment goes through" instead of the means.
    # A policy switch, so a bad hour is undone without a deploy.
    thompson: bool = False
    # The ranking prices a shop by the surplus it leaves us, (1 - its usual paid
    # share of list) / (1 - ours), instead of a +-20 % nudge: Thompson's failure
    # (11/4) showed the cheap-shop signal was too weak to steer the choice.
    surplus_score: bool = True
    # Ask the two best-ranked shops for a first quote at once and negotiate with
    # the one leaving more surplus (the market's top buyer asks three; 11/4).
    dual_quote: bool = True


class RestockPolicy(FrozenModel):
    """What the shop buys; the kit executes the plan, the market draws the costs."""

    reserve_cash: int = Field(default=1_500_000, ge=0, le=5_000_000)
    # Stock follows the money (owner, 11/4): stock cost may reach stock_share of
    # equity (cash + stock at cost); seed_budget is only a floor for a small shop.
    seed_budget: int = Field(default=2_500_000, ge=0)
    stock_share: float = Field(default=0.7, ge=0.0, le=0.9)
    units_per_sku: int = Field(default=1, ge=1, le=20)
    # Broaden coverage independently of the per-SKU twenty-unit holding limit.
    # Owner 11/5: the first expansion adds five market-ranked replenishment SKUs.
    depth_skus: int = Field(default=6, ge=1, le=25)
    # Growth is bounded by existing cash, stock, food and endgame limits.
    growth_enabled: bool = True
    cover_hours: float = Field(default=0.67, ge=0.34, le=2.0)
    probe_qty: int = Field(default=1, ge=1, le=3)
    # Owner-listed trial SKUs keep a small total position across all buy paths.
    # Empty preserves existing policies; clear a SKU only after reviewing it.
    probe_skus: tuple[str, ...] = ()
    price_bands: int = Field(default=3, ge=1, le=3)
    min_mature_sales: int = Field(default=20, ge=5, le=100)
    max_units: int = Field(
        default=400, ge=1, le=8000
    )  # 5000 -> 6500 09:4x -> 8000 13:2x
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
        default_factory=dict, max_length=len(TACTICAL_FIELDS)
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
    field_expiries: dict[str, float] = Field(default_factory=dict, exclude=True)

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


class PolicyConflictError(ValueError):
    """A policy changed after the caller read its expected revision."""


def _check_version(history: PolicyHistory, expected_version: int | None) -> None:
    if expected_version is not None and history.current.version != expected_version:
        message = (
            f"Policy conflict: expected {expected_version}, "
            f"found {history.current.version}"
        )
        raise PolicyConflictError(message)


def load_history(path: Path) -> PolicyHistory:
    """Read coherently using a shared lock or verified immutable-snapshot reads."""
    with reader_lock(path) as acquired:
        if acquired:
            return _load_history(path)
        try:
            snapshot = coherent_snapshot(path)
        except FileNotFoundError:
            return PolicyHistory(current=Policy())
        except (SnapshotConflictError, ValidationError) as error:
            message = "Policy snapshot metadata is inconsistent"
            raise PolicyConflictError(message) from error
        history = PolicyHistory.model_validate_json(snapshot.raw)
        return _with_expiries(history, snapshot.expiries)


def _load_history(path: Path) -> PolicyHistory:
    """Read one consistent snapshot while the caller owns the process lock."""
    try:
        raw = path.read_bytes()
    except OSError:
        return PolicyHistory(current=Policy())
    try:
        history = PolicyHistory.model_validate_json(raw)
    except (ValidationError, ValueError):
        salvaged = _salvage_history(raw)
        if salvaged is None:
            return PolicyHistory(current=Policy())
        history = salvaged
    expiries = read_expiries(path, hashlib.sha256(raw).hexdigest())
    return _with_expiries(history, expiries)


def _salvage_history(raw: bytes) -> PolicyHistory | None:
    """Keep the live policy when only an old version or a band fails validation.

    11/5 14:05: a tighter warranty bound (72 h) made the previous version (8760 h)
    invalid, the whole file read as defaults, and the next save wrote over the
    real policy. Old versions, bands and an overlay that no longer validate are
    dropped; the file still reads as defaults only when ``current`` itself fails.
    """
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    kept: list[object] = []
    for old in data.get("previous") or []:
        try:
            _ = Policy.model_validate(old)
        except ValidationError:
            continue
        kept.append(old)
    data["previous"] = kept[:MAX_HISTORY]
    bands = data.get("bands") or {}
    good: dict[str, object] = {}
    for key, band in bands.items() if isinstance(bands, dict) else []:
        try:
            _ = Band.model_validate(band)
        except ValidationError:
            continue
        good[key] = band
    data["bands"] = good
    for optional in ("strategy", "tactical"):
        try:
            _ = PolicyHistory.model_validate({**data, optional: data.get(optional)})
        except ValidationError:
            data[optional] = None
    try:
        return PolicyHistory.model_validate(data)
    except ValidationError:
        return None


def _with_expiries(history: PolicyHistory, expiries: dict[str, float]) -> PolicyHistory:
    overlay = history.tactical
    if overlay is not None:
        expiries = {
            key: expiries.get(key, overlay.expires_at) for key in overlay.changes
        }
    return history.model_copy(update={"field_expiries": expiries})


def load_policy(path: Path, now: float | None = None) -> Policy:
    """Return only tactical fields still live at the observation time."""
    history = load_history(path)
    if history.tactical is None:
        return history.current
    effective = apply_changes(
        history.base(),
        carried_changes(history, now if now is not None else time.time()),
    )
    return effective.model_copy(
        update={
            "version": history.current.version,
            "updated_at": history.current.updated_at,
            "reason": history.current.reason,
        }
    )


def _write(path: Path, history: PolicyHistory) -> None:
    write_snapshot(path, history.model_dump_json(indent=1), history.field_expiries)


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


@locked
def save_policy(
    path: Path,
    candidate: Policy,
    reason: str,
    *,
    bands: Mapping[str, Band] | None = None,
    expected_version: int | None = None,
) -> Policy:
    """Publish changed strategy fields while preserving unrelated live overlays."""
    history = _load_history(path)
    _check_version(history, expected_version)
    changes = {
        f"{section}.{field}": field_value(candidate, f"{section}.{field}")
        for section, model in SECTIONS.items()
        for field in model.model_fields
        if field_value(candidate, f"{section}.{field}")
        != field_value(history.base(), f"{section}.{field}")
    }
    base = apply_changes(history.base(), changes)
    kept = validate_bands(bands, base) if bands is not None else history.bands
    overlay_changes = dict(history.tactical.changes) if history.tactical else {}
    for key in changes:
        _ = overlay_changes.pop(key, None)
    return _publish(
        path,
        history,
        _Publication(base, overlay_changes, history.field_expiries, kept),
        reason,
    )


@dataclass(frozen=True, slots=True)
class _Publication:
    base: Policy
    changes: dict[str, JsonValue]
    expiries: dict[str, float]
    bands: dict[str, Band]
    applied_at: float | None = None


def _publish(
    path: Path, history: PolicyHistory, update: _Publication, reason: str
) -> Policy:
    base, changes = update.base, update.changes
    expiries, bands = update.expiries, update.bands
    stamped = _stamp(apply_changes(base, changes), history.current.version + 1, reason)
    expiries = {key: expiries[key] for key in changes}
    overlay = (
        TacticalOverlay(
            changes=changes,
            reason=reason[:300],
            applied_at=(
                update.applied_at
                if update.applied_at is not None
                else history.tactical.applied_at
                if history.tactical
                else time.time()
            ),
            expires_at=min(expiries.values()),
            version=stamped.version,
        )
        if changes
        else None
    )
    _write(
        path,
        PolicyHistory(
            current=stamped,
            previous=(history.current, *history.previous)[:MAX_HISTORY],
            strategy=base,
            tactical=overlay,
            bands=bands,
            field_expiries=expiries,
        ),
    )
    return stamped


@locked
def save_bands(
    path: Path, bands: Mapping[str, Band], *, expected_version: int | None = None
) -> dict[str, Band]:
    """Replace bands under the same revision lock as policy values."""
    history = _load_history(path)
    _check_version(history, expected_version)
    kept = validate_bands(bands, history.base())
    _ = _publish(
        path,
        history,
        _Publication(
            history.base(),
            dict(history.tactical.changes) if history.tactical else {},
            history.field_expiries,
            kept,
        ),
        "bands updated",
    )
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


@locked
def save_tactical(  # noqa: PLR0913 - preserves existing API plus optimistic revision
    path: Path,
    changes: Mapping[str, JsonValue],
    reason: str,
    *,
    now: float | None = None,
    ttl: float = TACTICAL_TTL_SECONDS,
    expected_version: int | None = None,
) -> Policy:
    """Apply at most three tactical changes inside their bands for ``ttl`` seconds."""
    if len(changes) > MAX_TACTICAL_CHANGES:
        message = f"At most {MAX_TACTICAL_CHANGES} tactical changes per step"
        raise ValueError(message)
    history = _load_history(path)
    _check_version(history, expected_version)
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
    # A live overlay's other fields stay: a new step adds to it rather than
    # wiping it (11/5 05:38 a buyer step erased the 05:23 idle-cash depth rise).
    merged = carried_changes(history, stamp)
    expiries = dict(history.field_expiries)
    for key, value in changes.items():
        if merged.get(key) != value:
            expiries[key] = stamp + ttl
        merged[key] = value
    return _publish(
        path,
        history,
        _Publication(base, merged, expiries, history.bands, stamp),
        f"tactical: {reason}",
    )


def carried_changes(history: PolicyHistory, now: float) -> dict[str, JsonValue]:
    """Return the live overlay's changes that still sit inside their bands."""
    overlay = history.tactical
    if overlay is None:
        return {}
    base = history.base()
    kept: dict[str, JsonValue] = {}
    for key, value in overlay.changes.items():
        if now >= history.field_expiries.get(key, overlay.expires_at):
            continue
        if key not in TACTICAL_FIELDS or isinstance(value, bool):
            continue
        if not isinstance(value, (int, float)):
            continue
        band = history.bands.get(key) or default_band(key, base)
        if band.low <= float(value) <= band.high:
            kept[key] = value
    return kept


@locked
def expire_tactical(
    path: Path, now: float | None = None, *, expected_version: int | None = None
) -> Policy | None:
    """Remove only expired tactical fields, retaining other deadlines."""
    history = _load_history(path)
    _check_version(history, expected_version)
    overlay = history.tactical
    if overlay is None:
        return None
    live = carried_changes(history, now if now is not None else time.time())
    if live == overlay.changes:
        return None
    return _publish(
        path,
        history,
        _Publication(history.base(), live, history.field_expiries, history.bands),
        "tactical expired",
    )


@locked
def rollback_policy(
    path: Path,
    reason: str,
    *,
    fields: tuple[str, ...] = (),
    expected_values: Mapping[str, JsonValue] | None = None,
    expected_version: int | None = None,
) -> Policy | None:
    """Restore explicitly scoped fields only if their expected values still match."""
    history = _load_history(path)
    _check_version(history, expected_version)
    if not history.previous or not fields:
        return None
    if expected_values is None or any(
        key not in expected_values
        or field_value(history.current, key) != expected_values[key]
        for key in fields
    ):
        message = "Rollback requires matching expected values for every scoped field"
        raise PolicyConflictError(message)
    prior_raw = read_previous_snapshot(path)
    if prior_raw is None:
        message = "Scoped rollback requires preserved layer metadata"
        raise PolicyConflictError(message)
    prior = PolicyHistory.model_validate_json(prior_raw)
    if prior.current.version != history.previous[0].version:
        message = "Rollback checkpoint no longer matches previous revision"
        raise PolicyConflictError(message)
    prior_expiries = read_expiries(path, hashlib.sha256(prior_raw.encode()).hexdigest())
    changes = {key: field_value(prior.base(), key) for key in fields}
    base = apply_changes(history.base(), changes)
    overlay = dict(history.tactical.changes) if history.tactical else {}
    expiries = dict(history.field_expiries)
    for key in fields:
        _ = overlay.pop(key, None)
        _ = expiries.pop(key, None)
        if prior.tactical is not None and key in prior.tactical.changes:
            overlay[key] = prior.tactical.changes[key]
            expiries[key] = prior_expiries.get(key, prior.tactical.expires_at)
    return _publish(
        path,
        history,
        _Publication(base, overlay, expiries, history.bands),
        f"rollback: {reason}",
    )


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
