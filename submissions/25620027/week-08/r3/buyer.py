"""Round 3 buyer: one order in seconds, chosen from evidence, bounded by policy.

The market hands a buyer a new order about every 20 seconds, so code ranks the
shops that actually stock an acceptable SKU by the
surplus they can give (list price of the SKU minus what we expect to pay) and by what
happened with them before (our own payments and settlements), negotiates in two or
three turns, and prints the receipt with an ``accept`` policy the kit enforces before
paying. Bounded exploration samples eligible shops; a limited interpreter reads
otherwise unrecognized prose without changing the code's price or payment limits.
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import hashlib
import json
import math
import random
import re
import sqlite3
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, Self

import anyio
import httpx2
from pydantic import ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from http_client import create_client
from market_a2a.models import FrozenModel, HarnessError
from market_a2a.remote import Remote, Task
from market_a2a.security import SecurityEvents
from r3.buyer_report_db import open_readonly
from r3.emergency_buyer import (
    UNKNOWN_GRADE,
    PurchaseValue,
    discount_factor,
    receipt_unit,
    receipt_value,
    refund_reliability,
    says_final,
    stated_warranty,
    unit_cost,
    warranty_share,
)
from r3.events import EventLedger, FoodEvidence, events_path
from r3.guard import suspicious
from r3.interpreter import read_seller_message
from r3.ledger_db import ledger_path as ledger_file
from r3.market_files import (
    KitFile,
    Order,
    ShopEntry,
    market_dir,
    read_catalog,
    read_order,
    read_payments,
    read_shops,
)
from r3.model_client import ModelClient
from r3.picture import Picture, load_picture, picture_path, trust_factor
from r3.policy import BuyerPolicy, load_policy, policy_path
from r3.policy_storage import atomic_snapshot
from r3.sku_selection import (
    ENABLED_FILE,
    SelectionTerms,
    SkuEvidence,
    scale_weight,
    selection_terms,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from pathlib import Path

    from r3.market_files import CatalogItem
    from r3.policy import Policy

MAX_STARS: Final = 5
# Shops below this evidence weight (refusals, failed deals, low trust) are never
# picked by exploration; price memory moves a shop by at most this share.
EXPLORE_MIN_EVIDENCE: Final = 0.6
EXPLORE_MIN_TRUST: Final = 0.6
# Failure kinds exploration reads from the short window (explore_memory_hours).
RECENT_FAILURE_KINDS: Final = ("no_deal", "error")
PRICE_FACTOR_SPAN: Final = 0.2
NUMBER: Final = re.compile(r"(?<![0-9A-Za-z])([0-9]{2,9})(?![0-9A-Za-z])")
TRADE_GATE: Final = "AX_R3_TRADE_MODEL"
SAVED_FEEDBACK_FILE: Final = "r3-saved-feedback.json"
SKU_FEEDBACK_FILE: Final = "r3-sku-feedback.json"
SAVED_FEEDBACK_DISABLED_FILE: Final = "r3-saved-feedback.disabled"
SAVED_MATURITY_SECONDS: Final = 21780
SAVED_WINDOW_SECONDS: Final = 86400
SAVED_FEEDBACK_MAX_AGE: Final = 3600
SAVED_LEDGER_MAX_LAG: Final = 60
SAVED_MIN_KNOWN: Final = 20
SAVED_LOSS_PRIOR: Final = 20


class SavedShopLoss(FrozenModel):
    """Observed normalized loss against useful goods, before prior shrinkage."""

    known_deals: int = Field(ge=0, strict=True)
    loss_sum: float = Field(ge=0, allow_inf_nan=False, strict=True)

    @model_validator(mode="after")
    def bounded_loss(self) -> Self:
        """One known unit cannot lose more than two normalized credit units."""
        if self.loss_sum > 2 * self.known_deals:
            message = "Saved loss exceeds its known-deal bound"
            raise ValueError(message)
        return self


class SavedFeedback(FrozenModel):
    """Fresh, finite evidence; owner control is metadata, not a trading gate."""

    generated_at: float = Field(ge=0, allow_inf_nan=False, strict=True)
    ledger_latest_at: float | None = Field(ge=0, allow_inf_nan=False, strict=True)
    maturity_seconds: Literal[21780] = SAVED_MATURITY_SECONDS
    owner_control_until: float = Field(
        default=0, ge=0, allow_inf_nan=False, strict=True
    )
    shops: dict[str, SavedShopLoss]
    skus: dict[str, dict[str, SkuEvidence]] = Field(default_factory=dict)


class _SavedCatalogItem(KitFile):
    """Require explicit goods identity and positive list value at the boundary."""

    kind: Literal["goods", "food"] | None = None
    list_price: int | None = Field(default=None, ge=0, strict=True)


class _SavedUnit(KitFile):
    """A ledger settlement must identify both its order and physical unit."""

    unit: str | None = Field(default=None, strict=True)
    order: str | None = Field(default=None, strict=True)


_SAVED_CATALOG: Final = TypeAdapter(dict[str, _SavedCatalogItem])
_SAVED_ROWS: Final = TypeAdapter(list[tuple[float, str, str, int | None, str]])
_SAVED_LATEST: Final = TypeAdapter(tuple[int, float] | None)
_SAVED_CURSOR: Final = TypeAdapter(tuple[int] | None)
_SAVED_BREAK: Final = TypeAdapter(tuple[float, int | None, Literal[0, 1]] | None)


def load_saved_feedback(path: Path, now: float) -> SavedFeedback | None:
    """Ignore missing, malformed or stale evidence without blocking an order."""
    try:
        if path.with_name(SAVED_FEEDBACK_DISABLED_FILE).exists():
            return None
        result = SavedFeedback.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return None
    if (
        not math.isfinite(now)
        or not 0 <= now - result.generated_at <= SAVED_FEEDBACK_MAX_AGE
        or result.ledger_latest_at is None
        or not 0
        <= result.generated_at - result.ledger_latest_at
        <= SAVED_LEDGER_MAX_LAG
    ):
        return None
    return result


def publish_saved_feedback(  # noqa: PLR0913 - explicit independent source paths
    ledger_path: Path,
    facts_path: Path,
    catalog_path: Path,
    buyer_name: str,
    output_path: Path,
    *,
    observed_at: float,
    ledger_latest_at: float | None,
    ledger_fresh: bool,
    owner_control_until: float,
) -> None:
    """Publish mature own-goods losses from read-only indexed source snapshots.

    A current facts cursor is necessary: an absent break in an out-of-date cache
    must never count as evidence that a unit was useful. Unknown partial refunds
    and duplicate units/orders contribute neither a loss nor a known deal.
    """
    if (
        not ledger_fresh
        or not math.isfinite(observed_at)
        or ledger_latest_at is None
        or not math.isfinite(ledger_latest_at)
        or not 0 <= observed_at - ledger_latest_at <= SAVED_LEDGER_MAX_LAG
    ):
        return
    try:
        catalog = _SAVED_CATALOG.validate_json(catalog_path.read_bytes(), strict=True)
        with (
            contextlib.closing(open_readonly(ledger_path)) as ledger,
            contextlib.closing(open_readonly(facts_path)) as facts,
        ):
            _ = ledger.execute("BEGIN")
            _ = facts.execute("BEGIN")
            latest = _SAVED_LATEST.validate_python(
                ledger.execute(
                    "SELECT seq,ts FROM rows ORDER BY seq DESC LIMIT 1"
                ).fetchone(),
                strict=True,
            )
            cursor = _SAVED_CURSOR.validate_python(
                facts.execute(
                    "SELECT seq FROM report_cursor WHERE buyer=?", (buyer_name,)
                ).fetchone(),
                strict=True,
            )
            if (
                latest is None
                or cursor is None
                or cursor[0] < latest[0]
                or not math.isfinite(latest[1])
                or not 0 <= observed_at - latest[1] <= SAVED_LEDGER_MAX_LAG
            ):
                return
            end = observed_at - SAVED_MATURITY_SECONDS
            rows = _SAVED_ROWS.validate_python(
                ledger.execute(
                    """SELECT ts,shop,sku,amount,data FROM rows
WHERE kind='settle' AND ts>=? AND ts<? AND buyer=? AND round=3 ORDER BY ts""",
                    (end - SAVED_WINDOW_SECONDS, end, buyer_name),
                ).fetchall(),
                strict=True,
            )
            units = [_SavedUnit.model_validate_json(row[4]) for row in rows]
            order_counts = Counter(unit.order for unit in units)
            unit_counts = Counter(unit.unit for unit in units)
            shops: dict[str, SavedShopLoss] = {}
            skus: dict[str, dict[str, SkuEvidence]] = {}
            for row, unit in zip(rows, units, strict=True):
                at, shop, sku, paid, _ = row
                item = catalog.get(sku)
                if (
                    item is None
                    or item.kind != "goods"
                    or item.list_price is None
                    or item.list_price <= 0
                    or paid is None
                    or not 0 <= paid <= item.list_price
                    or not shop.rstrip("/")
                    or not math.isfinite(at)
                    or observed_at - at < SAVED_MATURITY_SECONDS
                    or not unit.unit
                    or not unit.order
                    or order_counts[unit.order] != 1
                    or unit_counts[unit.unit] != 1
                ):
                    continue
                broken = _SAVED_BREAK.validate_python(
                    facts.execute(
                        "SELECT ts,refund,refund_full FROM breaks WHERE unit=?",
                        (unit.unit,),
                    ).fetchone(),
                    strict=True,
                )
                loss = 0.0
                if broken is not None:
                    broken_at, refund, full = broken
                    if (
                        not math.isfinite(broken_at)
                        or not at <= broken_at <= observed_at
                    ):
                        continue
                    if full or (refund is not None and refund > 0 and refund >= paid):
                        loss = 1 - paid / item.list_price
                    elif refund == 0:
                        loss = 2 - paid / item.list_price
                    else:
                        continue
                key = shop.rstrip("/")
                previous = shops.get(key, SavedShopLoss(known_deals=0, loss_sum=0.0))
                shops[key] = SavedShopLoss(
                    known_deals=previous.known_deals + 1,
                    loss_sum=previous.loss_sum + loss,
                )
                by_sku = skus.setdefault(key, {})
                previous_sku = by_sku.get(
                    sku, SkuEvidence(known_deals=0, paid_share_sum=0.0, loss_sum=0.0)
                )
                by_sku[sku] = SkuEvidence(
                    known_deals=previous_sku.known_deals + 1,
                    paid_share_sum=previous_sku.paid_share_sum + paid / item.list_price,
                    loss_sum=previous_sku.loss_sum + loss,
                )
        result = SavedFeedback(
            generated_at=observed_at,
            ledger_latest_at=latest[1],
            owner_control_until=owner_control_until,
            shops=shops,
            skus=skus,
        )
        atomic_snapshot(
            output_path.with_name(SKU_FEEDBACK_FILE), result.model_dump_json()
        )
        # Keep the established strict cache readable by the previous runtime.
        atomic_snapshot(output_path, result.model_dump_json(exclude={"skus"}))
    except (OSError, sqlite3.Error, ValidationError):
        return


_FOOD_QUOTE_NUMBER: Final = TypeAdapter(
    float | None, config=ConfigDict(allow_inf_nan=False)
)


def flush_food_evidence(
    events: EventLedger | None,
    evidence: Sequence[FoodEvidence],
    emit: Callable[[str], None],
) -> None:
    """Flush after any buyer outcome, including an order with no candidate."""
    if events is not None and evidence:
        try:
            events.record_food_evidence_batch(evidence)
        except (OSError, sqlite3.Error):
            emit("[R3 BUYER] food evidence batch unavailable")


class _FoodUnitEvidence(KitFile):
    """Only explicit seller/certificate fields, never verified outcome claims."""

    unit: str | None = Field(default=None, strict=True)
    sku: str | None = Field(default=None, strict=True)
    expires_at: float | None = Field(
        default=None, ge=0, allow_inf_nan=False, strict=True
    )
    grade: Literal["A", "B", "C"] | None = None
    grade_now: Literal["A", "B", "C"] | None = None
    warranty_hours: float | None = Field(
        default=None, ge=0, allow_inf_nan=False, strict=True
    )


class _FoodCertificateEvidence(KitFile):
    """An attached certificate is a claim until the existing kit verifies it."""

    cert: _FoodUnitEvidence | None = None


class _FoodPartEvidence(_FoodUnitEvidence):
    """Parse the seller's structured metadata without interpreting prose."""

    cert: _FoodCertificateEvidence | None = None
    hours_left: float | None = Field(default=None, allow_inf_nan=False, strict=True)


class _FoodReceiptIdentity(KitFile):
    """Read identity independently of unrelated receipt or certificate fields."""

    unit: str | None = Field(default=None, min_length=1, strict=True)
    item: str | None = Field(default=None, min_length=1, strict=True)


@dataclass(frozen=True, slots=True)
class _FoodBoundClaims:
    """Keep an unambiguous claim source attached to one observed identity."""

    unit: str | None
    sku: str | None
    certificate: _FoodUnitEvidence | None = None
    seller: _FoodPartEvidence | None = None


def _compatible_food_parts(
    parsed: Sequence[_FoodPartEvidence], sku: str
) -> list[_FoodPartEvidence]:
    """Exclude a whole part when its own explicit identities contradict it."""
    usable: list[_FoodPartEvidence] = []
    for part in parsed:
        cert = part.cert.cert if part.cert else None
        if part.sku not in (None, sku) or (cert and cert.sku not in (None, sku)):
            continue
        if cert and part.unit and cert.unit and part.unit != cert.unit:
            continue
        usable.append(part)
    return usable


def _bind_food_claims(
    parsed: Sequence[_FoodPartEvidence], task: Task, *, receipt_stage: bool, sku: str
) -> _FoodBoundClaims:
    """Never fill a unit's missing fields from another unit or certificate."""
    usable = _compatible_food_parts(parsed, sku)
    observed_sku: str | None = sku
    if receipt_stage:
        try:
            identity = _FoodReceiptIdentity.model_validate(receipt_from(task) or {})
        except ValidationError:
            identity = _FoodReceiptIdentity()
        unit = identity.unit
        if identity.item != sku:
            observed_sku = None
    else:
        units = {
            value
            for part in usable
            for value in (
                part.unit,
                part.cert.cert.unit if part.cert and part.cert.cert else None,
            )
            if value
        }
        unit = next(iter(units)) if len(units) == 1 else None
    if unit is None or observed_sku is None:
        return _FoodBoundClaims(unit, observed_sku)
    certificates = {
        cert.model_dump_json(): cert
        for part in usable
        if part.cert
        and (cert := part.cert.cert) is not None
        and cert.unit == unit
        and cert.sku == sku
    }
    if len(certificates) > 1:
        return _FoodBoundClaims(unit, observed_sku)
    certificate = next(iter(certificates.values()), None)
    sellers: dict[str, _FoodPartEvidence] = {}
    for part in usable:
        cert = part.cert.cert if part.cert else None
        anchor = part.unit or (cert.unit if cert and cert.sku == sku else None)
        known_sku = part.sku if part.sku is not None else cert.sku if cert else None
        fields = (
            part.expires_at,
            part.hours_left,
            part.grade,
            part.grade_now,
            part.warranty_hours,
        )
        if (
            anchor == unit
            and known_sku == sku
            and any(value is not None for value in fields)
        ):
            sellers[part.model_dump_json(exclude={"cert"})] = part
    seller = next(iter(sellers.values())) if len(sellers) == 1 else None
    return _FoodBoundClaims(unit, observed_sku, certificate, seller)


def food_task_evidence(  # noqa: PLR0913 - explicit observation context
    order: Order,
    policy: Policy,
    choice: Choice,
    task: Task,
    *,
    stage: Literal["quote", "acceptance", "receipt"],
    price: int | None,
) -> FoodEvidence | None:
    """Capture food facts in memory; database writes happen after negotiation."""
    if order.kind != "food":
        return None
    parts = [*task.status.message.parts]
    if stage == "receipt":
        parts.extend(part for artifact in task.artifacts for part in artifact.parts)
    parsed: list[_FoodPartEvidence] = []
    attached: bool | None = None
    for data in (part.data for part in parts if part.data is not None):
        attached = bool(attached or data.get("cert") is not None)
        try:
            parsed.append(_FoodPartEvidence.model_validate(data))
        except ValidationError:
            # Keep the stage and attachment observation; malformed fields stay unknown.
            continue
    bound = _bind_food_claims(
        parsed, task, receipt_stage=stage == "receipt", sku=choice.sku
    )
    now = WALL_CLOCK()
    observed_price = price
    if observed_price is None:
        try:
            quote_number = _FOOD_QUOTE_NUMBER.validate_python(
                task.status.message.metadata.ask, strict=True
            )
        except ValidationError:
            quote_number = None
        if quote_number is not None and quote_number >= 0 and quote_number.is_integer():
            observed_price = int(quote_number)
    certificate_expiry = bound.certificate.expires_at if bound.certificate else None
    seller_expiry = bound.seller.expires_at if bound.seller else None
    expiry = certificate_expiry if certificate_expiry is not None else seller_expiry
    left = (
        (expiry - now) / 3600
        if expiry is not None
        else bound.seller.hours_left
        if bound.seller
        else None
    )
    facts = bound.certificate or bound.seller
    try:
        return FoodEvidence(
            order_id=order.order,
            stage=stage,
            observed_at=now,
            task_id=task.id,
            shop=choice.shop.url.rstrip("/"),
            sku=bound.sku,
            unit=bound.unit,
            use_within_hours=order.use_within_hours,
            food_margin_hours=policy.buyer.food_margin_hours,
            certificate_expires_at=certificate_expiry,
            seller_expires_at=seller_expiry,
            stated_hours_left=left,
            grade=(facts.grade or facts.grade_now) if facts else None,
            warranty_hours=facts.warranty_hours if facts else None,
            price=observed_price,
            list_price=choice.list_price
            if bound.sku == choice.sku and choice.list_price > 0
            else None,
            certificate_attached=attached,
            policy_version=policy.version,
        )
    except ValidationError:
        # Malformed observation fields must not reject an existing trade.
        return None


class SuspiciousCounterpartError(HarnessError):
    """The seller's text tripped an injection or scam screen; walk away."""


class UnfitUnitError(HarnessError):
    """The offered unit fails our accept conditions; try another shop."""


class ShortShelfLifeError(UnfitUnitError):
    """The offered food unit will not last the order; try another shop."""


class UnacceptedGradeError(UnfitUnitError):
    """The offered goods unit is certified at a grade we do not buy."""


class NoStockError(UnfitUnitError):
    """The shop listed the SKU but refused at once for lack of stock."""


def stock_refusal(task: Task) -> bool:
    """Return True for an immediate "none in stock" refusal (stale stock list).

    On 11/4 17:00-17:26, 17 of 73 orders ended this way about a second in, with
    17 of the order's 18 seconds unused; the next shop now gets the order.
    """
    if task.status.state != "TASK_STATE_REJECTED":
        return False
    text = task.observation().lower()
    return "in stock" in text or "out of stock" in text or "sold out" in text


STATED_SHELF_LEFT: Final = re.compile(
    r"""
    \bshelf\s+life\s+left\s+(-?[0-9]+(?:\.[0-9]+)?)\s*(?:hours?|h)\b
    |
    (-?[0-9]+(?:\.[0-9]+)?)\s*(?:hours?|h)\s+(?:of\s+)?
    shelf\s+life\s+left\b
    """,
    re.IGNORECASE | re.VERBOSE,
)
SHELF_LEFT: Final = re.compile(r"([0-9]+(?:\.[0-9]+)?) h of shelf life left")
# Only a statement about this unit counts: "certified grade B" or "grade B now",
# not a card's "grade A goods carry a warranty". The kit's reason: "grade C, ...".
STATED_GRADE: Final = re.compile(r"certified grade ([ABC])\b|\bgrade ([ABC]) now\b")
STATED_C_GRADE: Final = re.compile(
    r"\bgrade\s*[:=-]?\s*C\b|\brefurbished\b|C\s*급", re.IGNORECASE
)
REFUSED_GRADE: Final = re.compile(r"\bgrade ([ABC]),")
# 11/14 22:0x (task 1-B, owner-approved scope A): of 102 receipt-stage rejects in
# 2 h, 23 were grade B units whose first quote already said "grade B" (ddolcom,
# dae-yangkim: "... (grade B, three-hour full-refund warranty ...)"), yet the
# buyer negotiated as grade unknown with a 6 h warranty. When the quote text names
# exactly one grade, or exactly one warranty length, that value is used; text with
# none, or several (option lists), leaves the old behaviour unchanged.
TEXT_GRADE: Final = re.compile(r"\bgrade\s*[:=]?\s*([ABC])\b")
WARRANTY_WORDS: Final = {
    "one": 1.0,
    "two": 2.0,
    "three": 3.0,
    "six": 6.0,
    "twelve": 12.0,
    "twenty-four": 24.0,
    "forty-eight": 48.0,
    "seventy-two": 72.0,
}
TEXT_WARRANTY: Final = re.compile(
    r"(\d+(?:\.\d+)?|"
    + "|".join(WARRANTY_WORDS)
    + r")\s*[- ]?\s*(?:h|hr|hrs|hours?)\b(?:(?!shelf|left)[^.;]){0,30}?\bwarranty"
    r"|\bwarranty\b(?:(?!shelf|left)[^.;]){0,20}?\b(\d+(?:\.\d+)?)\s*[- ]?\s*"
    r"(?:h|hr|hrs|hours?)\b(?!\s*(?:of\s+)?(?:shelf|left))",
    re.IGNORECASE,
)
WARRANTY_TEXT_MAX: Final = 72.0  # the market refuses longer warranties
NO_WARRANTY: Final = re.compile(
    r"\bno warranty\b|\bwithout (?:a )?warranty\b", re.IGNORECASE
)


def text_grade(text: str) -> str | None:
    """Return the one grade a quote's text names, or None (none or several)."""
    grades = set(TEXT_GRADE.findall(text))
    return grades.pop() if len(grades) == 1 else None


def text_warranty(text: str) -> float | None:
    """Return the one warranty length a quote's text names, or None."""
    hours = {
        WARRANTY_WORDS.get(a.lower(), None)
        if a and not a[0].isdigit()
        else float(a or b)
        for a, b in TEXT_WARRANTY.findall(text)
    }
    hours.discard(None)
    hours = {h for h in hours if h is not None and h <= WARRANTY_TEXT_MAX}
    if len(hours) == 1:
        return float(hours.pop())
    if not hours and NO_WARRANTY.search(text):
        return 0.0
    return None


# A remembered unit is assumed still on that shop's shelf for this long.
UNFIT_MEMORY_HOURS: Final = 6.0
# Price walls: a shop that would not come down to our credit line for a SKU is
# asked again on the next order of that SKU (11/9 11-14h: 44 % of goods orders
# ended at a wall, 2.7 shops tried per order). Remember the wall for a while and
# rank that (shop, SKU) low; it is still tried when nothing else is listed.
WALL_MEMORY_HOURS: Final = 2.0
WALL_WEIGHT: Final = 0.2


def wall_weighter(
    walls: Mapping[tuple[str, str], int],
) -> Callable[[str, str], float]:
    """Return an offer weight: WALL_WEIGHT for a (shop, SKU) that walled us lately."""

    def weight(url: str, sku: str) -> float:
        return WALL_WEIGHT if walls.get((url.rstrip("/"), sku)) else 1.0

    return weight


# Patch 3.6 (11/12 18:19): Saved counts the share of the seller's margin we kept.
# Our Saved was 0.718 at the change, so a deal must expect at least this to lift
# it; at an A unit's 0.65 cost estimate the line moves from 0.60-0.66 of list
# to about 0.71-0.73, where bedding sellers stopped (11/12: 668 price walls in
# 1.8 h). Food moves from 0.66 to 0.64 of list.
MARGIN_LINE: Final = 0.75


# 11/12: 91 % of our goods orders are bedding (a hidden favourite) and 21 % close.
# The ranking had no term for whether a shop ever closes at our line: in 12 h
# dae-yangkim came first 167 times and closed 2 % of first tries, dragonash first
# 39 times and closed 54 % (96 of our 206 bedding deals). Our own close rate on
# the order's category, shrunk toward the category mean, now scales goods offers
# within these bounds; the line and receipt checks still decide every purchase.
# 11/12 17:03 goods kept it (5.4 -> 9.5 deals/h); 17:40 food too: in 12 h
# YAHO1212 came first 60 times at 40 % first-try closes, cheapest-in-town 58 at
# 36 %, nohaggle-hero only 29 times at 90 %.
# 11/13 19:2x (owner: "판매 지속적으로 거절되는 가게에 갈시간에 ... 점차 끊고 판매
# 잘되었던 가게로"): over 13:34-19:15 KST, 27 shops with 15+ tries closed under
# 10 % yet took 1,529 of our 3,288 tries (ddolcom 309 at 7 %, moss-and-oak 158 at
# 4 %, gom31 111 at 3 %) while nohaggle-hero closed 30 %. The floor, ceiling and
# prior now let a shop that keeps refusing fade to 0.02 and one that closes rise
# to 5x, sooner; explore_rate still sends some tries anywhere.
CLOSE_HOURS: Final = 6.0
CLOSE_PRIOR: Final = 2.0  # 4.0 until 11/13 19:2x
CLOSE_FLOOR: Final = 0.02  # 0.25 until 11/13 19:2x (owner: "그냥 확 낮춰")
CLOSE_CEILING: Final = 5.0  # 3.0 until 11/13 19:2x


# 11/14 06:5x (owner: "구매도 팍팍 늘려야돼 ... 드래곤도 90점 넘기던데 잘 매입해보는
# 전략로 분석"): dragonash closes 53 % of its orders (Saved 0.80), we 37 % (0.74).
# Saved is the mean deal share over deals + 100, and our deals already average
# 0.89-0.91, so more deals is the lever. Over 6 h our order time was 10,407 s for
# 344 deals (1.98 a minute): YAHO1212 took 1,618 s for 17 (0.63/min), winun1127
# 1,155 s for 16, yeonsong2 411 s for 1, mjforge 386 s for 1, while dragonash
# made 5.7 a minute, thisjonghoon 5.6, Kiro729 4.8, psyoung82 4.1. The close rate
# counts tries, not the seconds a try costs, so shops are also scaled by our
# deals per second spent with them (all categories, a TIME_PRIOR_SECONDS prior
# at the mean), between TIME_FLOOR and TIME_CEILING.
TIME_HOURS: Final = 6.0
TIME_PRIOR_SECONDS: Final = 120.0
TIME_FLOOR: Final = 0.1
TIME_CEILING: Final = 3.0


def time_weighter(
    base: Callable[[str, str], float],
    shops: Iterable[ShopEntry],
    spent: Mapping[str, tuple[float, int]],
) -> tuple[Callable[[str, str], float], dict[str, float]]:
    """Scale offers by our deals per second of order time with each shop."""
    seconds = sum(s for s, _ in spent.values())
    deals = sum(k for _, k in spent.values())
    if seconds <= 0 or deals <= 0:
        return base, {}
    mean = deals / seconds
    factors: dict[str, float] = {}
    for shop in shops:
        s, k = spent.get(shop.name.lower(), (0.0, 0))
        rate = (k + TIME_PRIOR_SECONDS * mean) / (s + TIME_PRIOR_SECONDS)
        factors[shop.url.rstrip("/")] = min(max(rate / mean, TIME_FLOOR), TIME_CEILING)

    def weight(url: str, sku: str) -> float:
        return base(url, sku) * factors.get(url.rstrip("/"), 1.0)

    return weight, factors


def close_scaled(order: Order) -> bool:
    """Round 3 orders, food and goods alike, rank shops by our close rate."""
    return bool(order.acceptable)


def close_weighter(
    base: Callable[[str, str], float],
    shops: Iterable[ShopEntry],
    counts: Mapping[str, tuple[int, int]],
) -> tuple[Callable[[str, str], float], dict[str, float]]:
    """Scale offers by each shop's recent close rate on this category."""
    tries = sum(n for n, _ in counts.values())
    deals = sum(k for _, k in counts.values())
    mean = (deals + 1) / (tries + 2)
    factors: dict[str, float] = {}
    for shop in shops:
        n, k = counts.get(shop.name.lower(), (0, 0))
        rate = (k + CLOSE_PRIOR * mean) / (n + CLOSE_PRIOR)
        factors[shop.url.rstrip("/")] = min(
            max(rate / mean, CLOSE_FLOOR), CLOSE_CEILING
        )

    def weight(url: str, sku: str) -> float:
        return base(url, sku) * factors.get(url.rstrip("/"), 1.0)

    return weight, factors


def cheap_first(
    base: Callable[[str, str], float],
    cheap: frozenset[tuple[str, str]],
    dear: frozenset[tuple[str, str]] = frozenset(),
) -> Callable[[str, str], float]:
    """Put ledger-cheap (shop, SKU) pairs first and ledger-dear pairs last."""

    def weight(url: str, sku: str) -> float:
        key = (url.rstrip("/"), sku)
        if key in cheap:
            return base(url, sku) * CHEAP_BOOST
        if key in dear:
            return base(url, sku) * DEAR_WEIGHT
        return base(url, sku)

    return weight


def unfit_skipper(
    unfit: list[tuple[float, str, str, str]],
    order: Order,
    policy: BuyerPolicy,
    now: float,
) -> Callable[[str, str], bool]:
    """Return a test for (shop URL, SKU) pairs whose last unit cannot serve this order.

    From our own refused payments: a goods unit certified C, or a food unit whose
    remaining life (as the kit read it at checkout, aged since) is short of the
    order's need. 154 shelf-life and 91 grade refusals a day were renegotiated
    with the same shop and the same unit.
    """
    need = (
        float(order.use_within_hours) + policy.food_margin_hours
        if order.kind == "food" and order.use_within_hours
        else None
    )
    bad: set[tuple[str, str]] = set()
    for ts, shop, item, reason in unfit:
        if order.kind == "goods" and "grade" in reason:
            grade = REFUSED_GRADE.search(reason)
            if grade is not None and grade[1] not in policy.goods_grades:
                bad.add((shop, item))
        elif need is not None:
            left = SHELF_LEFT.search(reason)
            if left is not None and float(left[1]) - (now - ts) / 3600 < need:
                bad.add((shop, item))
    return lambda shop, sku: (shop.rstrip("/"), sku) in bad


def stated_grade(task: Task) -> str | None:
    """Return stated quality; an explicit C quote is never negotiated."""
    if STATED_C_GRADE.search(task.observation()):
        return "C"
    for part in task.status.message.parts:
        data = part.data
        if not isinstance(data, dict):
            continue
        cert = data.get("cert")
        inner = cert.get("cert") if isinstance(cert, dict) else None
        for source in (inner, data):
            if isinstance(source, dict):
                for key in ("grade", "grade_now"):
                    value = source.get(key)
                    if value in {"A", "B", "C"}:
                        return str(value)
    found = STATED_GRADE.search(task.observation())
    return (found[1] or found[2]) if found is not None else None


def stated_unit(task: Task) -> str | None:
    """Return the unit id a quote's certificate or data names, if any."""
    for part in task.status.message.parts:
        data = part.data
        if not isinstance(data, dict):
            continue
        cert = data.get("cert")
        inner = cert.get("cert") if isinstance(cert, dict) else None
        for source in (inner, data):
            if isinstance(source, dict):
                unit = source.get("unit")
                if isinstance(unit, str) and unit.startswith("u-"):
                    return unit
    return None


def known_cost_ratio(
    unit: str | None, list_price: int, cost_of: Callable[[str], int | None] | None
) -> float | None:
    """Real cost over list of a named unit, or None to use the estimate."""
    if unit is None or cost_of is None or list_price <= 0:
        return None
    cost = cost_of(unit)
    if cost is None or not 0 < cost < list_price:
        return None
    return cost / list_price


# A food order the shop's unit cannot serve is found in its first reply (the
# market certificate carries expires_at) instead of at payment: 154 agreed deals
# on 11/4 were negotiated to the end and then refused for shelf life. With this
# share of the order's seconds still left, the next-best shop gets the order.
RECEIPT_REFUSAL: Final = "receipt expected credit below the line or quality"
# Goods orders skip a shop whose receipts we refused this often lately (it hides
# grade C or no warranty behind an unstated quote: 11/8 ggari-shop 34, yms979 23).
RECEIPT_SHY_COUNT: Final = 3
# 11/17 08:5x (owner, strategy 08:51): a (shop, SKU) pair whose last LOSING_STREAK
# attempts all ended without a deal rests LOSING_REST_MINUTES (price rules as before).
LOSING_STREAK: Final = 6
LOSING_REST_MINUTES: Final = 60.0
# 11/17 10:5x (owner, strategy 10:52): a whole shop rests after this many straight
# failed attempts, for this long.
LOSING_SHOP_STREAK: Final = 20
LOSING_SHOP_MINUTES: Final = 60.0
# 13:08 judged (11:06 deploy): 13-15 shops rested at a time; settles 87 -> 64.5/h
# and the Saved rise slowed (+0.0006/h -> +0.00035/h): off.
LOSING_SHOP_ON: Final = False
# 11/17 17:1x (owner, strategy 17:16): 62-100 price walls an hour repeat the same
# (shop, SKU, final price) within 60 min; in 6 h all 88 deals after a wall came at
# a different price. A (shop, SKU) whose last price wall is under REPEAT_WALL_SECONDS
# old ends at once when the seller's first ask is not below the one it gave then.
REPEAT_WALL_ON: Final = True
REPEAT_WALL_SECONDS: Final = 3600.0
_PRICE_WALLS: dict[tuple[str, str], tuple[int, float]] = {}


def repeat_wall(key: tuple[str, str], first_ask: int, now: float) -> bool:
    """Return True when this first ask repeats a recent price wall's."""
    seen = _PRICE_WALLS.get(key)
    if not REPEAT_WALL_ON or seen is None:
        return False
    if now - seen[1] > REPEAT_WALL_SECONDS:
        _ = _PRICE_WALLS.pop(key, None)
        return False
    return first_ask >= seen[0]


def remember_wall(key: tuple[str, str], first_ask: int | None, now: float) -> None:
    """Remember a price wall's first ask for this (shop, SKU)."""
    if first_ask is not None:
        _PRICE_WALLS[key] = (first_ask, now)


# 11/16 13:1x (owner): 3 h -> 1 h. Of 20 pairs held out for 3 h, 8 later sold 22
# grade A units to other buyers within about 1.3 h of our last failure.
RECEIPT_SHY_HOURS: Final = 1.0
# 11/15 10:5x (owner): a shop/SKU pair refused at the receipt for the same reason in
# two orders, or whose checkout found the agreed unit gone, rests for 30 min (all
# order kinds; the 3 h goods rule above stays).
REPEAT_REST_MINUTES: Final = 30.0
REPEAT_REST_ORDERS: Final = 2
# 11/15 22:0x (owner): in 3 h other buyers settled 17 grade A units (5.7 an hour) at
# shop/SKU pairs resting after receipt refusals, so that rest is 10 min; the
# none-in-stock and 409 stale-unit rests keep REPEAT_REST_MINUTES.
RECEIPT_REST_MINUTES: Final = 10.0
WALL_STATES: Final = frozenset({"TASK_STATE_INPUT_REQUIRED", "TASK_STATE_REJECTED"})
# Expiry is wall-clock time; negotiation timing uses an injectable monotonic clock.
WALL_CLOCK: Final = time.time


BIG_ORDER_BUDGET: Final = 200_000
# Shops one order may try. 11/10 04:15-08:15: 46 of 63 goods and 16 of 34 food
# orders lost to empty shelves stopped at the third shop after a median of only
# 3-4 s of their 26 s; the time share below still bounds every retry.
# 11/10 09:30-15:30 at five shops: 179 of 530 lost orders stopped at the fifth
# shop with a median 8 s used (p90 18 s); shops four and five still won 5.3-5.5 %
# of their tries, each at credit >= 0.32 (marginal Saved 0.41 vs end Saved 0.34).
# The time share below is the real bound (~1.6 s a try, about 10 in 70 % of 26 s);
# this count only keeps the loop finite when every shop fails at once.
BIG_ORDER_SHOPS: Final = 30
BIG_RETRY_SHARE: Final = 0.7
# 11/11 11:22-15:40: tries that started after 15 s won 0.2 deals an hour, but an
# order still running at 20-24 s pushed the next order from ~23 s to 29-36 s
# away; orders fell from 148 an hour (11/10) to 131. No new shop after 14 s.
BIG_RETRY_LATEST_SECONDS: Final = 14.0
BIG_FIRST_REPLY_SECONDS: Final = 6.0
SETTLEMENT_HANDOFF_SECONDS: Final = 1.5
MIN_RETRY_SECONDS: Final = 2.0
SLOW_SHOP_TIMEOUTS: Final = 2
# A shop that left us waiting HUNG_MIN times in the last HUNG_HOURS is
# skipped for every order, and cheap ledger evidence no longer lifts it: 11/12
# 07-09 KST two tunnel shops took 25 tries and ~5 whole orders this way while
# their cheap sales to other buyers kept lifting them.
HUNG_HOURS: Final = 1.0
# Three, not two: YAHO1212 and seochanit hang twice now and then and still sell.
HUNG_MIN: Final = 3


@dataclass(frozen=True, slots=True)
class BigOrderClock:
    """One deadline and bounded interpreter/commit state shared by all shops."""

    started: float
    deadline: float
    model_reads: set[str] = dataclasses.field(default_factory=set)
    committed: set[str] = dataclasses.field(default_factory=set)

    def retry_allowed(self, now: float, attempted: int) -> bool:
        """Leave time for another quote and the kit's final settlement."""
        return (
            attempted < BIG_ORDER_SHOPS
            and now - self.started < (self.deadline - self.started) * BIG_RETRY_SHARE
            and now - self.started < BIG_RETRY_LATEST_SECONDS
            and self.deadline - now > MIN_RETRY_SECONDS
            and not self.committed
        )


class ShopTimeoutError(UnfitUnitError):
    """Only the initial, noncommitting card/quote exceeded six seconds."""


class InvalidShopCardError(UnfitUnitError):
    """Discovery failed before any request could begin a negotiation."""


async def first_reply(
    remote: Remote, choice: Choice, clock: BigOrderClock
) -> tuple[str, Task]:
    """Bound discovery and first response; never label order expiry a shop timeout."""
    remaining = clock.deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    with anyio.move_on_after(min(BIG_FIRST_REPLY_SECONDS, remaining)) as scope:
        try:
            card = await remote.card(choice.shop.url)
        except ValidationError as exc:
            message = "invalid shop discovery card"
            raise InvalidShopCardError(message) from exc
        endpoint = card.endpoint()
        task = await remote.send(endpoint, choice.request)
        return endpoint, task
    if scope.cancelled_caught and remaining > BIG_FIRST_REPLY_SECONDS:
        message = "shop timeout"
        raise ShopTimeoutError(message)
    raise TimeoutError


def stated_hours_left(task: Task, now: float) -> float | None:
    """Hours of shelf life the seller's unit has, from its data part; None if unsaid.

    Read from the market-signed certificate when present (``cert.cert.expires_at``),
    else the seller's own ``expires_at`` or ``hours_left``. Used only to walk away
    early; the kit still checks the checkout before it pays.
    """
    for part in task.status.message.parts:
        data = part.data
        if not isinstance(data, dict):
            continue
        cert = data.get("cert")
        inner = cert.get("cert") if isinstance(cert, dict) else None
        for source in (inner, data):
            if not isinstance(source, dict):
                continue
            expires = source.get("expires_at")
            if isinstance(expires, (int, float)) and not isinstance(expires, bool):
                return (float(expires) - now) / 3600
        hours = data.get("hours_left")
        if isinstance(hours, (int, float)) and not isinstance(hours, bool):
            return float(hours)
    # Some shops state the unit's remaining life only in prose. It can reject
    # a short quote early; it never replaces the kit's certificate at checkout.
    found = STATED_SHELF_LEFT.search(task.observation())
    return float(found[1] or found[2]) if found is not None else None


@dataclass(frozen=True, slots=True)
class Choice:
    """The shop and SKU we will ask for, with the value we expect to get."""

    shop: ShopEntry
    sku: str
    name: str
    list_price: int
    request: str
    score: float
    explored: bool = False
    # the historical price term inside ``score``; a real quote replaces it
    price_term: float = 1.0


@dataclass(frozen=True, slots=True)
class Outcome:
    """The buyer's result line for the kit, plus facts for our own ledger."""

    shop: str
    receipt: dict[str, object] | None
    stars: int
    review: str
    accept: dict[str, object] | None
    agreed_price: int | None
    reason: str


def shop_factor(
    url: str, outcomes: dict[str, dict[str, int]], reasons: dict[str, str]
) -> float:
    """Weight a shop by what our own payments showed; refusals cut it hard."""
    counts = outcomes.get(url.rstrip("/"), {})
    paid = counts.get("paid", 0)
    refused = counts.get("refused", 0)
    no_deal = counts.get("no_deal", 0)
    factor = 1.0 + min(0.3, 0.03 * paid) - min(0.5, 0.15 * no_deal)
    factor *= 0.5 ** min(2, counts.get("suspicious", 0))
    reason = reasons.get(url.rstrip("/"), "").lower()
    if refused:
        if "certificate" in reason:
            factor *= 0.25
        elif "shelf life" in reason or "grade" in reason:
            factor *= 0.5
        elif "warranty" in reason:
            factor *= 0.6
        else:
            factor *= 0.7
    return max(0.05, factor)


# Prior for "a listed SKU is really in stock": Beta(4, 1), mean 0.8, so a new shop
# is not punished before it has answered, and each answer moves the estimate.
STOCK_PRIOR_HIT: Final = 4.0
STOCK_PRIOR_MISS: Final = 1.0


# The board the manager cached (kv "board", refreshed every 10 min) and our name
# there; the buyer never fetches the board itself on the 18 s order path.
OUR_SHOP_NAME: Final = "25620027"


def cached_board(events: EventLedger) -> object:
    """Return the manager's cached public board, or None."""
    raw = events.get_value("board")
    if not raw:
        return None
    try:
        return json.loads(raw).get("body")
    except (ValueError, AttributeError):
        return None


def stock_factor(url: str, misses: Mapping[str, tuple[int, int]]) -> float:
    """Posterior mean that this shop really has a SKU it lists (bandit estimate).

    On 11/4 195 of 926 asks in 6 h were answered "none in stock", and some shops
    did so 10 times out of 10; each such ask costs part of an 18 s order. The
    weight falls with every miss and recovers with every answer that had stock.
    """
    miss, asks = misses.get(url.rstrip("/"), (0, 0))
    hits = asks - miss
    return (hits + STOCK_PRIOR_HIT) / (asks + STOCK_PRIOR_HIT + STOCK_PRIOR_MISS)


# Competition (owner, 11/4): what our buyer pays becomes the seller's revenue and
# profit. Shops of players ranked above us, or just below (within RIVAL_MARGIN of
# our Players total), get half the weight unless they have sold to us cheaper than
# usual; shops of bottom-half players get a little more, to find cheap sellers.
# 11/13 19:3x (owner: "내 앞, 내뒤 경쟁자 3명까지는 피하자. 확 낮춰. 그대신 다른 가게
# 더 많이 가"): every player above us and the RIVALS_BELOW players just below us
# are rivals, at 0.02 weight whatever their price (in 24 h we paid yms979's shops
# 8.5M over 82 deals); big orders no longer skip this, and bottom-half shops get
# 1.3. The line and receipt checks still decide every purchase.
# 11/14 01:2x owner: "구매도 무조건 성적올려. 경쟁자꺼라도 3위에게 안밀리려면 잘 조율해
# 너무 보수적으로 하지마": once 01:06 made the rival weight real, 0.02 would cut most
# bedding offers; rivals now weigh 0.3 (cheap or not) and stay out of exploration.
# 11/14 12:0x guardrail C-11: inextro tied us for 2nd (1.704), back to 0.1.
RIVAL_FACTOR: Final = 0.1  # 0.5 to 11/13 19:3x, 0.02 to 11/14 01:2x, 0.3 to 12:0x
RIVAL_CHEAP_FACTOR: Final = (
    0.1  # 0.8 until 11/13 19:3x, 0.02 until 11/14 01:2x, 0.3 until 11/14 12:0x
)
# The board's Players table mixes in non-class players (sergiobuilds above us), so
# four below covers the three class players just behind us (inextro, DY-76,
# Lee-sungsoo at 19:30 KST; dragonash22 sits between them in the table).
RIVALS_BELOW: Final = 4  # was every player within 0.05 below us
UNDERDOG_FACTOR: Final = 1.3  # 1.1 until 11/13 19:3x


# 11/14 19:53-21:54 task 1-A trial (rivals from the student Class only, yms979
# weighted 1.0) was reverted: in 2 h buys went 60.5 -> 60.4 an hour and deals x
# (share - Saved) 9.00 -> 8.87; yms979 took 89 tries for 5 deals, sergiobuilds 17
# for 0, inextro fell from 47/7 to 28/1. Rivals come from the whole Players table.


def rival_view(board: object, our_shop_name: str) -> dict[str, str]:
    """Map shop name -> "rival" or "underdog" from the public board's Players table."""
    if not isinstance(board, dict):
        return {}
    players = [
        p
        for p in (board.get("players") or [])
        if isinstance(p, dict) and not p.get("house")
    ]
    ours = next(
        (
            p
            for p in players
            if isinstance(p.get("shop"), dict)
            and p["shop"].get("name") == our_shop_name
        ),
        None,
    )
    if ours is None:
        return {}
    try:
        mine = float(ours.get("total") or 0)
        totals = sorted(float(p.get("total") or 0) for p in players)
    except (TypeError, ValueError):
        return {}
    median = totals[len(totals) // 2] if totals else 0.0
    below = sorted((t for t in totals if t < mine), reverse=True)
    line = below[RIVALS_BELOW - 1] if len(below) >= RIVALS_BELOW else float("-inf")
    view: dict[str, str] = {}
    for player in players:
        shop = player.get("shop")
        if player is ours or not isinstance(shop, dict) or not shop.get("name"):
            continue
        total = float(player.get("total") or 0)
        if total >= line:
            view[str(shop["name"])] = "rival"
        elif total < median:
            view[str(shop["name"])] = "underdog"
    return view


def rival_factor(name: str, view: Mapping[str, str], *, cheap: bool) -> float:
    """Weight for a shop by its owner's standing against ours."""
    match view.get(name):
        case "rival":
            return RIVAL_CHEAP_FACTOR if cheap else RIVAL_FACTOR
        case "underdog":
            return UNDERDOG_FACTOR
        case _:
            return 1.0


# Prior for "our payment to this shop goes through": Beta(3, 1), mean 0.75.
PAY_PRIOR_HIT: Final = 3.0
PAY_PRIOR_MISS: Final = 1.0
# Thompson draws use their own generator, independent of the module's random.
THOMPSON_RNG: Final = random.Random()  # noqa: S311 - ranking draws, not security


def thompson_factor(
    url: str,
    misses: Mapping[str, tuple[int, int]],
    outcomes: Mapping[str, Mapping[str, int]],
    rng: random.Random | None = None,
) -> float:
    """One Thompson draw of P(listed stock is there) x P(our payment completes).

    A shop with little evidence draws widely, so it still gets tried; a shop
    that kept missing draws low almost every time. This replaces the posterior
    mean in the ranking, so exploration follows uncertainty, not a fixed 10 %.
    """
    draw = rng or THOMPSON_RNG
    miss, asks = misses.get(url.rstrip("/"), (0, 0))
    stock = draw.betavariate(asks - miss + STOCK_PRIOR_HIT, miss + STOCK_PRIOR_MISS)
    counts = outcomes.get(url.rstrip("/"), {})
    pay = draw.betavariate(
        counts.get("paid", 0) + PAY_PRIOR_HIT, counts.get("refused", 0) + PAY_PRIOR_MISS
    )
    return stock * pay


def review_factor(shop: ShopEntry) -> float:
    """Weigh a light prior from public stars; our own evidence dominates."""
    stars = [r.stars for r in shop.reviews if 1 <= r.stars <= MAX_STARS]
    if not stars:
        return 1.0
    return 0.9 + 0.05 * (sum(stars) / len(stars) - 3.0)


def price_factor(url: str, ratios: Mapping[str, float]) -> float:
    """Favour shops that sold to us below our usual share of list; +-20 %."""
    mine = ratios.get(url.rstrip("/"))
    if mine is None or not ratios:
        return 1.0
    usual = statistics.median(ratios.values())
    return 1.0 + max(-PRICE_FACTOR_SPAN, min(PRICE_FACTOR_SPAN, usual - mine))


# Expected-surplus price term bounds: a shop can at most double or cut to 0.3
# the weight its usual price earns it.
SURPLUS_TERM_MIN: Final = 0.3
SURPLUS_TERM_MAX: Final = 2.0
USUAL_RATIO: Final = 0.72


def surplus_factor(url: str, ratios: Mapping[str, float]) -> float:
    """Weight a shop by the surplus its usual price leaves us, against our usual.

    Surplus per unit is list x (1 - paid share). A shop that sells to us at 0.70
    of list leaves 0.30, one at 0.80 leaves 0.20: 1.5x apart, where the old price
    factor moved them only +-10 %. Unknown shops count at our usual share.
    """
    usual = statistics.median(ratios.values()) if ratios else USUAL_RATIO
    mine = ratios.get(url.rstrip("/"), usual)
    term = (1.0 - mine) / max(0.05, 1.0 - usual)
    return max(SURPLUS_TERM_MIN, min(SURPLUS_TERM_MAX, term))


def saved_loss_factor(
    url: str,
    losses: Mapping[str, SavedShopLoss],
    ratios: Mapping[str, float],
) -> float:
    """Discount expected surplus by shrunk, mature loss without banning a shop."""
    loss = losses.get(url.rstrip("/"))
    if loss is None or loss.known_deals < SAVED_MIN_KNOWN:
        return 1.0
    ratio = ratios.get(url.rstrip("/"), USUAL_RATIO)
    expected_surplus = max(0.05, 1 - ratio)
    shrunk_loss = loss.loss_sum / (loss.known_deals + SAVED_LOSS_PRIOR)
    return max(0.5, 1 - shrunk_loss / expected_surplus)


def cooling_shops(
    streaks: Mapping[str, tuple[int, float]], policy: BuyerPolicy
) -> frozenset[str]:
    """Shops whose last attempts all failed sit out a cooldown, then get one retry.

    ``streaks`` maps a shop URL to (consecutive no-deals, seconds since the last).
    """
    cooldown = policy.fail_cooldown_minutes * 60
    return frozenset(
        url
        for url, (count, age) in streaks.items()
        if count >= policy.fail_streak and age < cooldown
    )


def exploration_allowed(
    shop: ShopEntry,
    outcomes: dict[str, dict[str, int]],
    reasons: dict[str, str],
    picture: Picture | None,
) -> bool:
    """Past successes cannot cancel failure evidence for random exploration."""
    key = shop.url.rstrip("/")
    counts = outcomes.get(key, {})
    if any(
        counts.get(kind, 0) for kind in ("no_deal", "refused", "suspicious", "error")
    ):
        return False
    if reasons.get(key):
        return False
    if picture is not None and picture.trust_of(key) < EXPLORE_MIN_TRUST:
        return False
    return (
        shop_factor(key, outcomes, reasons) * review_factor(shop)
        >= EXPLORE_MIN_EVIDENCE
    )


def exploration_view(
    outcomes: dict[str, dict[str, int]],
    recent: Mapping[str, Mapping[str, int]] | None,
) -> dict[str, dict[str, int]]:
    """Build the counts exploration reads: short memory for failures, long for refusals.

    Over a whole day almost every shop shows one network error or failed deal
    (33 of 34 live shops on 2026-11-03), which left nothing to explore. Plain
    failures therefore come from the shorter ``recent`` window, while refusals,
    suspicious replies and payments keep the full memory.
    """
    if recent is None:
        return outcomes
    view: dict[str, dict[str, int]] = {}
    for url in set(outcomes) | set(recent):
        counts = dict(outcomes.get(url, {}))
        for kind in RECENT_FAILURE_KINDS:
            counts.pop(kind, None)
            if recent.get(url, {}).get(kind):
                counts[kind] = recent[url][kind]
        view[url] = counts
    return view


def preferred_saved_choice(choice: Choice, kept: Choice | None, order: Order) -> bool:
    """Break equal proxy scores by order/SKU hash, independent of list or ad order."""
    if kept is None or choice.score > kept.score:
        return True
    if choice.score != kept.score:
        return False
    keys = [
        hashlib.sha256(
            f"{order.order}\0{entry.shop.url.rstrip('/')}\0{entry.sku}".encode()
        ).digest()
        for entry in (choice, kept)
    ]
    return keys[0] < keys[1]


GOODS_REQUEST_PARTS: Final = (
    ", please state this unit's certified grade and offered warranty",
    "duration in hours. If a warranty is available, include it in your quote",
    "Please repeat this unit's grade and warranty duration in every counteroffer",
    "and final price reply; state explicitly if no warranty is offered.",
    # 11/11: dragonash sells grade B goods at 0.549 of list to others but quoted
    # us grade A at 0.69, above our line, on 4 of 5 tries; it picks the unit the
    # buyer names. 14:30: of 512 walls since 11:22, 192 were at a shop that had
    # just sold the SKU cheaper, mostly grade B with a warranty (258 sales), to
    # buyers who write "Grade B or C is fine if it is cheaper; please quote your
    # lowest-priced unit". Naming grade A first got us A. Grade C stays out.
    # 16:25 reverted: the grade-B wording ran 14:21-16:25 with goods at 27 % and
    # dragonash at 31 % (11:22-14:09 with this wording: 38 % and 57 %).
    "Grade A or B is fine: quote your cheapest grade A or B unit that carries",
    "a warranty.",
)
# Asked once when a shop quotes grade A clearly above our line (11/11).
ASK_FOR_B: Final = (
    "Grade B is fine if it is cheaper. Please quote your lowest-priced grade B "
    "unit with a warranty."
)
ASK_FOR_B_ABOVE: Final = 0.68  # share of list
ASK_FOR_B_ENABLED: Final = False  # 16:25 off with the wording above
GOODS_QUOTE_INFORMATION: Final = " ".join(GOODS_REQUEST_PARTS)


# 11/11: shops that offer to match "another shop's ledger price for this SKU"
# (dragonash) get the lowest A/B price another shop was paid in the last 24 h.
PRICE_MATCH_OFFER: Final = re.compile(
    r"\bmatch\s*<price>|\bprice[ -]match", re.IGNORECASE
)
PRICE_MATCH_HOURS: Final = 24.0


def ledger_low_price(
    path: Path, sku: str, exclude_shop: str, now: float | None = None
) -> int | None:
    """Return the lowest A/B price another shop was paid for ``sku`` lately."""
    stamp = now if now is not None else time.time()
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return None
    try:
        row = con.execute(
            "SELECT min(amount) FROM rows WHERE kind='settle' AND sku=? AND ts>=? "
            "AND grade IN ('A','B') AND rtrim(shop,'/')!=?",
            (sku, stamp - PRICE_MATCH_HOURS * 3600, exclude_shop.rstrip("/")),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return int(row[0]) if row is not None and row[0] else None


# 11/11 audit of 12 h: of 1,238 lost orders, 842 had a shop that sold the same
# SKU at grade A/B under our line within the hour before; 450 such shops sat out
# on our own penalties (slow-shop skip, failure cooldown, past-wall weight) and
# 201 orders ran out of time before reaching one. Fresh ledger evidence that a
# shop sells this SKU cheaply lifts those penalties for this order and puts the
# pair first. The line and the receipt checks still decide every purchase.
CHEAP_EVIDENCE_HOURS: Final = 1.0
CHEAP_RATIO: Final = {"food": 0.68, "goods": 0.59}
# 11/11 11:22-14:10 goods tries: cheap-evidence pairs won 23.0 % (344 tries), pairs
# whose recent A/B sales all sat over our line +0.05 won 3.4 % (148), and 92 lost
# orders ran out of time with a cheap pair still untried at x3. So cheap pairs go
# strictly first and clearly dear pairs last.
# 16:25 reverted to x3 and no dear weight: 14:09-15:43 settles fell 61.4 -> 45.3
# an hour (food 66 -> 52 %, goods 38 -> 27 %) after x100 / x0.1.
CHEAP_BOOST: Final = 3.0
DEAR_HOURS: Final = 3.0
DEAR_MARGIN: Final = 0.05
DEAR_MIN_SALES: Final = 2
DEAR_WEIGHT: Final = 1.0


def ledger_cheap_pairs(
    path: Path,
    kind: str,
    list_prices: Mapping[str, int],
    now: float | None = None,
) -> frozenset[tuple[str, str]]:
    """(shop URL, SKU) pairs another buyer got at A/B under our line lately."""
    wanted = sorted(list_prices)
    ratio = CHEAP_RATIO.get(kind)
    if not wanted or ratio is None:
        return frozenset()
    stamp = now if now is not None else time.time()
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return frozenset()
    pairs: set[tuple[str, str]] = set()
    try:
        # Only "?" placeholders are joined in; the SKUs travel as parameters.
        query = (
            "SELECT shop, sku, amount, grade, data FROM rows WHERE kind='settle' "  # noqa: S608
            "AND ts>=? AND grade IN ('A','B') AND sku IN ("
            + ",".join("?" * len(wanted))
            + ")"
        )
        for shop, sku, amount, grade, data in con.execute(
            query, (stamp - CHEAP_EVIDENCE_HOURS * 3600, *wanted)
        ):
            listed = list_prices.get(str(sku))
            if not listed or int(amount or 0) > listed * ratio:
                continue
            if kind == "goods" and grade != "A":
                warranty = json.loads(data or "{}").get("warranty_hours") or 0
                if not warranty:
                    continue
            pairs.add((str(shop).rstrip("/"), str(sku)))
    except (sqlite3.Error, ValueError):
        return frozenset()
    finally:
        con.close()
    return frozenset(pairs)


def ledger_dear_pairs(
    path: Path,
    kind: str,
    list_prices: Mapping[str, int],
    now: float | None = None,
) -> frozenset[tuple[str, str]]:
    """(shop URL, SKU) pairs whose recent A/B sales all sat clearly over our line."""
    wanted = sorted(list_prices)
    ratio = CHEAP_RATIO.get(kind)
    if not wanted or ratio is None:
        return frozenset()
    stamp = now if now is not None else time.time()
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return frozenset()
    lowest: dict[tuple[str, str], float] = {}
    sales: dict[tuple[str, str], int] = {}
    try:
        query = (
            "SELECT shop, sku, amount FROM rows WHERE kind='settle' "  # noqa: S608
            "AND ts>=? AND grade IN ('A','B') AND sku IN ("
            + ",".join("?" * len(wanted))
            + ")"
        )
        for shop, sku, amount in con.execute(
            query, (stamp - DEAR_HOURS * 3600, *wanted)
        ):
            listed = list_prices.get(str(sku))
            if not listed:
                continue
            key = (str(shop).rstrip("/"), str(sku))
            share = int(amount or 0) / listed
            lowest[key] = min(lowest.get(key, share), share)
            sales[key] = sales.get(key, 0) + 1
    except (sqlite3.Error, ValueError):
        return frozenset()
    finally:
        con.close()
    return frozenset(
        key
        for key, share in lowest.items()
        if sales[key] >= DEAR_MIN_SALES and share > ratio + DEAR_MARGIN
    )


def quote_information_request(order: Order, margin_hours: float) -> str:
    """Ask for unit facts on goods while preserving the food lifetime request."""
    if order.kind == "food" and order.use_within_hours:
        hours = math.ceil(float(order.use_within_hours) + margin_hours)
        # Saved pays the same for any unit that lasts: ask for the cheapest one
        # (near-date stock sells at 0.55-0.60 of list; 11/9 owner strategy).
        return f", need {hours} h, your cheapest unit that still lasts that long"
    return GOODS_QUOTE_INFORMATION if order.kind == "goods" and order.acceptable else ""


def choose(  # noqa: PLR0913, PLR0912, C901 - ranking needs every evidence source
    order: Order,
    shops: tuple[ShopEntry, ...],
    catalog: dict[str, CatalogItem],
    outcomes: dict[str, dict[str, int]],
    reasons: dict[str, str],
    *,
    blocked: frozenset[str] = frozenset(),
    picture: Picture | None = None,
    ratios: Mapping[str, float] | None = None,
    explore: float = 0.0,
    rng: random.Random | None = None,
    recent: Mapping[str, Mapping[str, int]] | None = None,
    skip_offer: Callable[[str, str], bool] | None = None,
    need_margin_hours: float = 1.0,
    stock_misses: Mapping[str, tuple[int, int]] | None = None,
    rivals: Mapping[str, str] | None = None,
    thompson: bool = False,
    surplus_score: bool = False,
    mature_losses: Mapping[str, SavedShopLoss] | None = None,
    sku_evidence: Mapping[str, Mapping[str, SkuEvidence]] | None = None,
    offer_weight: Callable[[str, str], float] | None = None,
) -> Choice | None:
    """Pick the shop and SKU with the best expected surplus; None when no stock fits.

    The scout's trust score (from reviews and forum text the model read) weighs a
    shop between 0.6 and 1.2; our own payment evidence still dominates, and our own
    past prices move it by up to 20 %. With probability ``explore`` the order goes
    to a random shop among the unpenalized candidates instead (e-greedy), so a
    cheaper or newer shop can be found; penalized shops are never explored.
    """
    acceptable = set(order.acceptable)
    # Food orders say the shelf life they need up front, in the "need 24 h" form
    # shops advertise, so a shop picks a unit that lasts (or says it has none)
    # instead of selling one the kit refuses at checkout: 11 of 59 agreed deals on
    # 11/4 17:26-17:56 were refused for shelf life, each a different shop and SKU.
    need = quote_information_request(order, need_margin_hours)
    explore_counts = exploration_view(outcomes, recent)
    draw = (rng or random).random() < explore
    best: Choice | None = None
    by_shop: dict[str, Choice] = {}
    for shop in shops:
        if shop.name in blocked or shop.url.rstrip("/") in blocked:
            continue
        evidence = shop_factor(shop.url, outcomes, reasons) * review_factor(shop)
        if picture is not None:
            evidence *= trust_factor(picture.trust_of(shop.url))
        priced = (
            surplus_factor(shop.url, ratios or {})
            if surplus_score
            else discount_factor(shop.url, ratios or {})
        )
        weight = (
            evidence
            * priced
            * (
                thompson_factor(shop.url, stock_misses or {}, outcomes)
                if thompson
                else stock_factor(shop.url, stock_misses or {})
            )
            # 11/14 01:0x: the rival weight applied only under surplus_score, which
            # is off, so from 22:55 to 01:00 yms979 still came first 51 times and
            # inextro, moss-and-oak (DY-76) and yms979 took 144 retries.
            * rival_factor(shop.name, rivals or {}, cheap=priced > 1.0)
        )
        shop_loss = 1.0
        if surplus_score and order.kind == "goods" and mature_losses is not None:
            shop_loss = saved_loss_factor(shop.url, mature_losses, ratios or {})
            weight *= shop_loss
        eligible = exploration_allowed(shop, explore_counts, reasons, picture) and (
            (rivals or {}).get(shop.name) != "rival"
        )
        if not acceptable:
            # R2 shops need not publish stock. A lone stock advertisement must not
            # hide all other shops from ranking or random exploration.
            choice = Choice(
                shop,
                order.item,
                order.item,
                0,
                f"I want to buy {order.item}",
                weight,
            )
            if best is None or choice.score > best.score:
                best = choice
            if eligible:
                by_shop[shop.url.rstrip("/")] = choice
            continue
        candidates = [
            o
            for o in shop.offers
            if o.qty > 0
            and (not acceptable or o.sku in acceptable)
            and not (skip_offer is not None and skip_offer(shop.url, o.sku))
        ]
        for offer in candidates:
            item = catalog.get(offer.sku)
            list_price = item.list_price if item is not None else 0
            name = (item.name if item is not None else "") or offer.name or offer.sku
            if surplus_score and list_price <= 0:
                continue
            terms = selection_terms(
                (sku_evidence or {}).get(shop.url.rstrip("/"), {}).get(offer.sku),
                ratios or {},
                shop.url,
                SelectionTerms(priced, shop_loss),
                enabled=surplus_score and order.kind == "goods",
            )
            score = scale_weight(weight, SelectionTerms(priced, shop_loss), terms)
            if offer_weight is not None:
                score *= offer_weight(shop.url, offer.sku)
            choice = Choice(
                shop,
                offer.sku,
                name,
                list_price,
                f"I want to buy {name} ({offer.sku}){need}",
                score,
                price_term=terms.price,
            )
            better = preferred_saved_choice(choice, best, order)
            if better:
                best = choice
            key = shop.url.rstrip("/")
            kept = by_shop.get(key)
            better = preferred_saved_choice(choice, kept, order)
            if eligible and better:
                by_shop[key] = choice
    if draw and by_shop:
        pick = (rng or random).choice(sorted(by_shop))
        return dataclasses.replace(by_shop[pick], explored=True)
    return best


def seller_ask(task: Task, floor: int = 0) -> int | None:
    """Read the seller's ask from metadata or text; None when it named none.

    A lone number in prose counts only when it is at least ``floor`` (a tenth of
    the list price): "10kg or 5kg?" is a question, not an ask of 10.
    """
    known = task.asking_price()
    if known is not None:
        return known
    text = task.observation()
    for pattern in (
        r"asking\s*:?\s*([0-9]{2,9})",
        r"[Cc]ounteroffer\s*:?\s*([0-9]{2,9})",
        r"price\s*(?:is|:)?\s*([0-9]{2,9})",
    ):
        matched = re.search(pattern, text)
        if matched is not None:
            return int(matched[1])
    numbers = NUMBER.findall(text.split("\n", 1)[0])
    if len(numbers) != 1 or int(numbers[0]) < floor:
        return None
    return int(numbers[0])


def receipt_from(task: Task) -> dict[str, object] | None:
    """Return the data part the seller attached; the kit verifies its signature."""
    receipts = [
        part.data
        for artifact in task.artifacts
        for part in artifact.parts
        if part.data is not None and "price" in part.data
    ]
    return dict(receipts[-1]) if receipts else None


def food_min_hours_left(order: Order, margin_hours: float) -> float | None:
    """Use the kit's two-decimal horizon at both quote and payment boundaries."""
    if order.kind != "food" or order.use_within_hours is None:
        return None
    return round(float(order.use_within_hours) + margin_hours, 2)


def accept_policy(order: Order, policy: BuyerPolicy) -> dict[str, object] | None:
    """Build what the checkout must show before the kit pays; None before Round 3."""
    if not order.acceptable:
        return None
    accept: dict[str, object] = {}
    if policy.require_cert:
        accept["cert"] = True
    need = food_min_hours_left(order, policy.food_margin_hours)
    if need is not None:
        accept["min_hours_left"] = need
    if order.kind == "goods":
        accept["grades"] = list(policy.goods_grades)
        if policy.goods_warranty_hours > 0:
            accept["warranty_hours"] = policy.goods_warranty_hours
    return accept


# A first quote that does not arrive within this many seconds is ignored.
QUOTE_SECONDS: Final = 6.0
OVER_CAP_SHARE: Final = 0.1


def quote_value(
    choice: Choice, task: Task, budget: int, order: Order, policy: BuyerPolicy
) -> float | None:
    """Return the expected surplus of continuing with a first quote, or None.

    Surplus is list minus what we would pay: the ask when it is within our cap,
    else half of what the cap leaves (a counter may or may not close the gap).
    """
    if task.status.state != "TASK_STATE_INPUT_REQUIRED":
        return None
    try:
        check_shelf_life(task, order, policy)
    except UnfitUnitError:
        return None
    if suspicious(task.observation()) is not None:
        return None
    cap = price_cap(choice, budget, policy)
    ask = seller_ask(task, floor=int(choice.list_price * 0.1))
    scale = 1 if policy.surplus_score else max(1, choice.list_price)
    if ask is None:
        return max(0.0, (choice.list_price - cap) * 0.25) / scale
    if ask <= cap:
        return float(choice.list_price - ask) / scale
    # Above our cap the deal may not close at all: rank it below every in-cap
    # quote of a comparable item (a reviewer reproduced an over-cap quote winning).
    return max(0.0, (choice.list_price - cap) * OVER_CAP_SHARE) / scale


async def first_quotes(
    remote: Remote, choices: list[Choice], clock: BigOrderClock | None = None
) -> list[tuple[Choice, str, Task]]:
    """Send each choice's opening request at once; keep the replies that came back."""
    got: list[tuple[Choice, str, Task]] = []

    async def one(choice: Choice) -> None:
        with anyio.move_on_after(QUOTE_SECONDS):
            try:
                if clock is not None:
                    endpoint, task = await first_reply(remote, choice, clock)
                else:
                    card = await remote.card(choice.shop.url)
                    endpoint = card.endpoint()
                    task = await remote.send(endpoint, choice.request)
            except (HarnessError, httpx2.HTTPError, ValidationError, TimeoutError):
                return
            got.append((choice, endpoint, task))

    async with anyio.create_task_group() as group:
        for choice in choices:
            group.start_soon(one, choice)
    return got


def check_shelf_life(task: Task, order: Order | None, policy: BuyerPolicy) -> None:
    """Raise when the offered unit fails the order: grade we do not buy, or too old."""
    if order is not None and order.kind == "goods" and order.acceptable:
        grade = stated_grade(task)
        if grade is not None and grade not in policy.goods_grades:
            message = f"grade {grade} offered, we buy {list(policy.goods_grades)}"
            raise UnacceptedGradeError(message)
    if order is None:
        return
    need = food_min_hours_left(order, policy.food_margin_hours)
    if need is None:
        return
    left = stated_hours_left(task, WALL_CLOCK())
    if left is not None and left < need:
        message = f"shelf life {left:.1f} h left, the order needs {need:.1f}"
        raise ShortShelfLifeError(message)


def judged_list_price(
    receipt: Mapping[str, object],
    order: Order,
    choice: Choice,
    catalog: Mapping[str, CatalogItem],
) -> int | None:
    """List price Saved will divide by: the shipped SKU's, or the worst case.

    A receipt may name the order's category instead of a SKU, and then the
    seller ships any acceptable SKU it holds: 11/8 15:38 a juice order agreed at
    0.59 of the asked SKU's list shipped a cheaper SKU, 0.752 of its own list.
    Without a SKU we judge on the cheapest acceptable SKU this shop listed.
    """
    entry = catalog.get(str(receipt.get("item") or ""))
    if entry is not None and entry.list_price > 0:
        return entry.list_price
    acceptable = set(order.acceptable)
    lists = [
        catalog[o.sku].list_price
        for o in choice.shop.offers
        if o.sku in acceptable and o.sku in catalog and catalog[o.sku].list_price > 0
    ]
    return min(lists) if lists else None


def check_receipt_value(  # noqa: PLR0913, PLR0917 - one receipt, every owner line
    receipt: Mapping[str, object],
    order: Order,
    choice: Choice,
    catalog: Mapping[str, CatalogItem],
    price: int | None,
    policy: BuyerPolicy,
    reliability: Mapping[str, float],
    cost_of: Callable[[str], int | None] | None = None,
) -> tuple[PurchaseValue | None, str | None]:
    """Return the receipt's value and why we refuse it, or None when it passes."""
    list_price = judged_list_price(receipt, order, choice, catalog)
    if list_price is None:
        item = str(receipt.get("item") or "")[:40]
        return None, f"{RECEIPT_REFUSAL} (shipped SKU unknown: {item})"
    paid = price or 0
    value = receipt_value(
        dict(receipt),
        PurchaseValue(
            order.kind,
            None,
            0,
            list_price,
            paid,
            reliability.get(choice.shop.url.rstrip("/"), 1.0),
            policy.min_expected_credit,
            MARGIN_LINE,
        ),
    )
    known = known_cost_ratio(receipt_unit(receipt), list_price, cost_of)
    if known is not None:
        value = dataclasses.replace(value, known_cost=known)
    ceiling = math.floor(list_price * policy.max_price_ratio)
    if order.budget:
        ceiling = min(ceiling, order.budget)
    if paid > ceiling:
        return value, (
            f"{RECEIPT_REFUSAL} (paid {paid} above {policy.max_price_ratio:.2f} "
            f"of the shipped list {list_price})"
        )
    if not value.acceptable():
        return value, (
            f"{RECEIPT_REFUSAL} (claim {value.grade}, "
            f"warranty {value.warranty_hours:g} h)"
        )
    return value, None


async def negotiate(  # noqa: PLR0913, PLR0912, PLR0915, C901 - one bounded negotiation
    remote: Remote,
    endpoint: str,
    choice: Choice,
    budget: int,
    *,
    policy: BuyerPolicy,
    emit: Callable[[str], None],
    clock: Callable[[], float] = time.monotonic,
    reader: ModelClient | None = None,
    order: Order | None = None,
    opened: Task | None = None,
    order_clock: BigOrderClock | None = None,
    committed_shops: set[str] | None = None,
    observe_food: Callable[
        [Task, Literal["quote", "acceptance", "receipt"], int | None], None
    ]
    | None = None,
    reliability: Mapping[str, float] | None = None,
    warranty_odds: tuple[Mapping[str, float], float] | None = None,
    match_price: Callable[[], int | None] | None = None,
    cost_of: Callable[[str], int | None] | None = None,
) -> tuple[Task, int | None]:
    """Two to four turns: open, read the ask, accept or counter, never above cap.

    Slow sellers (a model per turn) can spend most of the order budget on one
    reply, so once ``late_accept_share`` of the budget is gone any ask within the
    cap is taken rather than countered: the next order arrives in ~20 s anyway.
    When a seller writes prose instead of a number, one model read (Haiku, 2.5 s)
    extracts the ask; a message that trips the injection screen ends the talk.
    """
    cap = price_cap(choice, budget, policy)
    if cap < 1:
        message = "No affordable price under this order's cap"
        raise HarnessError(message)
    started = clock() if order_clock is None else order_clock.started
    task = opened if opened is not None else await remote.send(endpoint, choice.request)
    if observe_food is not None:
        observe_food(task, "quote", None)
    my_offer: int | None = None
    reads = 0
    reply = ""
    matched = False
    asked_b = False
    wall_key = (choice.shop.url.rstrip("/"), choice.sku)
    first_ask: int | None = None
    for round_number in range(policy.max_rounds + 1):
        state = task.status.state
        if state == "TASK_STATE_COMPLETED":
            _ = _PRICE_WALLS.pop(wall_key, None)
            return task, my_offer
        if state != "TASK_STATE_INPUT_REQUIRED":
            if my_offer is None and stock_refusal(task):
                raise NoStockError(task.observation()[:120])
            return task, None
        # Screen before parsing: a number inside an injection is not an ask.
        flag = suspicious(task.observation())
        if flag is not None:
            raise SuspiciousCounterpartError(flag)
        check_shelf_life(task, order, policy)
        ask = seller_ask(task, floor=int(choice.list_price * 0.1))
        if (
            ask is None
            and reads < 1
            and (order_clock is None or not order_clock.model_reads)
        ):
            reads += 1
            if order_clock is not None:
                order_clock.model_reads.add(choice.shop.url)
            ask, stop, reply = await prose_ask(reader, task, choice, emit, order)
            if stop:
                return task, None
        if first_ask is None and ask is not None:
            first_ask = ask
            if repeat_wall(wall_key, ask, WALL_CLOCK()):
                emit(f"[R3 BUYER] repeat_wall_stop sku={choice.sku} ask={ask}")
                message = (
                    f"repeat price wall: first ask {ask} not below the last wall's"
                )
                raise UnfitUnitError(message)
        if (
            ASK_FOR_B_ENABLED
            and not asked_b
            and order is not None
            and order.kind == "goods"
            and ask is not None
            and choice.list_price > 0
            and ask > choice.list_price * ASK_FOR_B_ABOVE
            and stated_grade(task) == "A"
        ):
            # Rides in front of this round's number offer ("... My offer: N") so
            # sellers that read only numbers keep negotiating.
            asked_b = True
            reply = f"{reply} {ASK_FOR_B}".strip()
            emit(f"[R3 BUYER] ask_for_b ask={ask} list={choice.list_price}")
        if (
            not matched
            and match_price is not None
            and ask is not None
            and PRICE_MATCH_OFFER.search(task.observation()) is not None
        ):
            matched = True
            low = match_price()
            if low is not None and low < ask and low <= cap:
                emit(f"[R3 BUYER] price_match ask={ask} ledger={low}")
                task = await remote.send(endpoint, f"match {low}", task)
                continue
        reference = choice.list_price if choice.list_price > 0 else budget
        quick = int(reference * policy.quick_accept_ratio)
        last_round = round_number >= policy.max_rounds - 1
        duration = (
            policy.order_seconds
            if order_clock is None
            else order_clock.deadline - started
        )
        prior_offer = my_offer
        late = clock() - started >= duration * policy.late_accept_share
        if ask is not None and ask <= cap and (ask <= quick or last_round or late):
            my_offer = ask
        else:
            target = (
                reference * policy.first_offer_ratio
                if my_offer is None
                else (my_offer + (ask if ask is not None else cap)) / 2
            )
            if ask is not None:
                target = min(target, ask)
            offer = min(cap, max(1, int(target)))
            if my_offer is not None and offer <= my_offer:
                offer = min(cap, my_offer + max(1, int(reference * 0.03)))
            if (
                my_offer is not None
                and offer == my_offer
                and (ask is None or offer < ask)
            ):
                return task, None
            # Progress must never turn a lower seller ask into an overbid.
            my_offer = min(offer, ask) if ask is not None else offer
        if order is not None and order.acceptable:
            shop_url = choice.shop.url.rstrip("/")
            refund = (reliability or {}).get(shop_url, 1.0)
            # 11/14 23:5x: reading grade/warranty from quote text (1-B, 22:36)
            # was turned off again: buys 58.0 -> 49.3 an hour and deals x (share -
            # Saved) 8.40 -> about 6.7 in 1.2 h, receipt rejects barely fell (47.5
            # -> about 44 an hour). text_grade/text_warranty stay for analysis.
            stated = stated_warranty(task)
            hours = stated if stated is not None else 0.0
            if stated is None and order.kind == "goods" and warranty_odds:
                # Square the historical share for an unstated warranty;
                # explicit promises and the actual receipt stay separate.
                shares, overall = warranty_odds
                hours = 6.0
                refund *= shares.get(shop_url, overall) ** 2
            value = PurchaseValue(
                order.kind,
                stated_grade(task) or UNKNOWN_GRADE,
                hours,
                choice.list_price,
                my_offer,
                refund,
                policy.min_expected_credit,
                MARGIN_LINE,
                known_cost_ratio(stated_unit(task), choice.list_price, cost_of),
            )
            line = value.max_price()
            line = min(line, cap) if line is not None else None
            # A stated final price is not moving: take it at or under the line,
            # otherwise leave now and spend the time on the next shop (11/7).
            if line is not None and ask is not None and says_final(task.observation()):
                if ask > line:
                    remember_wall(wall_key, first_ask, WALL_CLOCK())
                    message = f"final price {ask} above the emergency line {line}"
                    raise UnfitUnitError(message)
                my_offer = ask
            elif line is not None and my_offer > line:
                # Offer the line itself once rather than dropping a shop whose
                # unit passes at a slightly lower price.
                if prior_offer is not None and line <= prior_offer:
                    remember_wall(wall_key, first_ask, WALL_CLOCK())
                    message = f"seller stays above the emergency line {line}"
                    raise UnfitUnitError(message)
                my_offer = line
            value = dataclasses.replace(value, price=my_offer)
            if not value.acceptable():
                message = (
                    f"expected credit below {value.line():.2f} or excluded "
                    "quality: "
                    f"credit={value.expected_credit()} grade={value.grade}"
                )
                raise UnfitUnitError(message)
            emit(
                f"[R3 BUYER] expected_credit={value.expected_credit():.6f} "
                f"grade={value.grade} warranty_hours={value.warranty_hours}"
                + (
                    f" cost={value.known_cost:.3f}"
                    if value.known_cost is not None
                    else ""
                )
            )
        emit(
            f"[R3 BUYER] round={round_number + 1} ask={ask} offer={my_offer} cap={cap}"
        )
        # A screened, digit-free answer to the seller's question rides in front of
        # the offer; the offer keyword keeps every seller's number parser working.
        message = f"{reply} My offer: {my_offer}" if reply else str(my_offer)
        reply = ""
        # A sent acceptance may be binding even if the reply is not final.
        if observe_food is not None and ask is not None and my_offer >= ask:
            observe_food(task, "acceptance", my_offer)
        if committed_shops is not None and ask is not None and my_offer >= ask:
            committed_shops.add(choice.shop.url)
        if order_clock is not None and ask is not None and my_offer >= ask:
            order_clock.committed.add(choice.shop.url)
        task = await remote.send(endpoint, message, task)
    return task, my_offer if task.status.state == "TASK_STATE_COMPLETED" else None


def order_facts(choice: Choice, order: Order | None) -> dict[str, object]:
    """Return the only facts a model may answer a seller from; never budget or cap."""
    facts: dict[str, object] = {
        "item": choice.name,
        "sku": choice.sku,
        "quantity": 1,
        "needs": "the market certificate attached to the unit",
    }
    if order is not None:
        if order.kind == "food" and order.use_within_hours is not None:
            facts["needs_shelf_life"] = (
                f"at least {order.use_within_hours:g} hours left at settlement"
            )
        if order.kind == "goods":
            facts["accepts_grades"] = "A or B"
    return facts


async def prose_ask(
    reader: ModelClient | None,
    task: Task,
    choice: Choice,
    emit: Callable[[str], None],
    order: Order | None = None,
) -> tuple[int | None, bool, str]:
    """Read a seller's prose once; return (ask, stop, reply). Screened text raises."""
    read = await read_seller_message(
        reader,
        task.observation(),
        item=choice.name,
        list_price=choice.list_price,
        facts=order_facts(choice, order),
    )
    if read is None:
        return None, False, ""
    emit(
        f"[R3 BUYER] read intent={read.intent} ask={read.ask} reply={bool(read.reply)}"
    )
    if read.intent == "suspicious":
        raise SuspiciousCounterpartError(read.screened)
    return read.ask, read.intent == "reject", read.reply


def price_cap(choice: Choice, budget: int, policy: BuyerPolicy) -> int:
    """Return the most we pay: the budget, or the policy share of the list price."""
    if choice.list_price <= 0:
        return budget
    return min(budget, int(choice.list_price * policy.max_price_ratio))


def valid_receipt(  # noqa: PLR0911 - each check names its reason
    receipt: dict[str, object],
    order: Order,
    choice: Choice,
    price: int | None,
    cap: int | None = None,
) -> str | None:
    """Return why a receipt cannot be used, or None when it matches the deal.

    ``price`` is the amount the buyer actually offered or accepted; a receipt
    completed without one is refused, whatever it says.
    """
    if price is None:
        return "completed without an offer the buyer made or accepted"
    item = receipt.get("item")
    raw_price = receipt.get("price")
    if not isinstance(raw_price, (int, float)) or float(raw_price) != int(raw_price):
        return "receipt price is not a whole number"
    paid = int(raw_price)
    if order.acceptable and item not in order.acceptable and item != order.item:
        return f"receipt item {item!r} is not acceptable for this order"
    if not order.acceptable and item not in (choice.sku, order.item):
        return f"receipt item {item!r} differs from the request"
    if price is not None and paid != price:
        return f"receipt price {paid} differs from the agreed {price}"
    if paid > order.budget:
        return "receipt price exceeds the budget"
    if cap is not None and paid > cap:
        return f"receipt price {paid} exceeds the buyer's cap {cap}"
    if not receipt.get("sig_seller"):
        return "receipt is not signed"
    return None


async def buy(  # noqa: PLR0913, C901, PLR0912, PLR0915 - one order, explicit inputs
    shops_path: Path,
    item: str,
    budget: int,
    order_path: Path | None,
    emit: Callable[[str], None],
    *,
    folder: Path | None = None,
    policy_file: Path | None = None,
    events_file: Path | None = None,
    blocked: frozenset[str] = frozenset(),
) -> Outcome:
    """Run one order end to end within the policy's time budget."""
    started = time.monotonic()
    wall_started = WALL_CLOCK()
    state = market_dir(
        folder
        if folder is not None
        else (order_path.parent if order_path is not None else None)
    )
    policy = load_policy(policy_file if policy_file is not None else policy_path(state))
    order = read_order(order_path) if order_path is not None else Order()
    order = order.model_copy(
        update={
            "item": order.item or item,
            "budget": order.budget or budget,
        }
    )
    big = order.budget >= BIG_ORDER_BUDGET
    deadline = started + policy.buyer.order_seconds
    if order.expires is not None:
        deadline = min(deadline, started + order.expires - wall_started)
    order_clock = BigOrderClock(started, deadline - SETTLEMENT_HANDOFF_SECONDS)
    if big:
        emit(f"[R3 BUYER] big order budget={order.budget}")
    shops = read_shops(shops_path)
    catalog = read_catalog(state)
    events: EventLedger | None = None
    food_evidence: list[FoodEvidence] = []
    outcomes: dict[str, dict[str, int]] = {}
    reasons: dict[str, str] = {}
    ratios: dict[str, float] = {}
    misses: dict[str, tuple[int, int]] = {}
    rivals: dict[str, str] = {}
    recent: dict[str, dict[str, int]] | None = None
    cooling: frozenset[str] = frozenset()
    refused_skus: frozenset[tuple[str, str]] = frozenset()
    slow: frozenset[str] = frozenset()
    unfit: list[tuple[float, str, str, str]] = []
    walls: dict[tuple[str, str], int] = {}
    try:
        events = EventLedger(
            events_file if events_file is not None else events_path(state)
        )
        events.record_order_metadata(
            order, observed_at=wall_started, policy_version=policy.version
        )
        if order.kind == "food" and order.order:
            try:
                food_evidence.append(
                    FoodEvidence(
                        order_id=order.order,
                        stage="order",
                        observed_at=wall_started,
                        use_within_hours=order.use_within_hours,
                        food_margin_hours=policy.buyer.food_margin_hours,
                        policy_version=policy.version,
                    )
                )
            except (OSError, sqlite3.Error, ValidationError):
                emit("[R3 BUYER] food order evidence unavailable")
        _ = events.sync_payments(read_payments(state))
        fruitless = events.slow_fruitless_shops(6.0)
        timeouts = events.shop_timeout_counts(6.0) if big else {}
        slow = frozenset(
            shop.name
            for shop in shops
            if shop.url.rstrip("/") in fruitless
            or timeouts.get(shop.url.rstrip("/"), 0) >= SLOW_SHOP_TIMEOUTS
        )
        outcomes = events.shop_outcomes(policy.buyer.refusal_memory_hours, order.kind)
        reasons = events.refusal_reasons(policy.buyer.refusal_memory_hours)
        recent = events.shop_outcomes(policy.buyer.explore_memory_hours, order.kind)
        ratios = events.price_ratios(policy.buyer.refusal_memory_hours, order.kind)
        misses = events.stock_misses(policy.buyer.explore_memory_hours, order.kind)
        rivals = rival_view(cached_board(events), OUR_SHOP_NAME)
        unfit = events.unfit_offers(UNFIT_MEMORY_HOURS)
        if order.kind == "goods":
            walls = events.wall_offers(WALL_MEMORY_HOURS)
        cooling = cooling_shops(
            events.failure_streaks(policy.buyer.refusal_memory_hours),
            policy.buyer,
        )
        if order.kind == "goods":
            refused_skus = events.receipt_refused_skus(
                RECEIPT_SHY_HOURS, RECEIPT_SHY_COUNT
            )
        resting = (
            events.repeated_receipt_refusals(RECEIPT_REST_MINUTES, REPEAT_REST_ORDERS)
            | events.stale_unit_pairs(REPEAT_REST_MINUTES)
            | events.repeated_stock_misses(REPEAT_REST_MINUTES, REPEAT_REST_ORDERS)
            | events.losing_streak_pairs(LOSING_STREAK, LOSING_REST_MINUTES)
        )
        if resting:
            refused_skus = refused_skus | resting
            emit(f"[R3 BUYER] resting_pairs={len(resting)}")
    except (OSError, sqlite3.Error):
        emit("[R3 BUYER] events ledger unavailable; choosing without own history")
    offered = {
        offer.sku
        for shop in shops
        for offer in shop.offers
        if offer.qty > 0 and (not order.acceptable or offer.sku in order.acceptable)
    }
    offered_prices = {
        sku: catalog[sku].list_price
        for sku in offered
        if sku in catalog and catalog[sku].list_price > 0
    }
    cheap = ledger_cheap_pairs(
        ledger_file(state), order.kind, offered_prices, WALL_CLOCK()
    )
    dear = (
        ledger_dear_pairs(ledger_file(state), order.kind, offered_prices, WALL_CLOCK())
        - cheap
    )
    if cheap:
        cheap_urls = {url for url, _ in cheap}
        lifted = sorted(
            shop.name
            for shop in shops
            if shop.url.rstrip("/") in cheap_urls
            and (shop.name in slow or shop.url.rstrip("/") in cooling)
        )
        slow = frozenset(
            name
            for name in slow
            if not any(
                s.name == name and s.url.rstrip("/") in cheap_urls for s in shops
            )
        )
        cooling = frozenset(url for url in cooling if url not in cheap_urls)
        walls = {key: n for key, n in walls.items() if key not in cheap}
        emit(
            f"[R3 BUYER] cheap_evidence pairs={len(cheap)} dear={len(dear)} "
            f"lifted={','.join(lifted)}"
        )
    hung = events.hung_shop_names(HUNG_HOURS, HUNG_MIN)
    held = frozenset(shop.name for shop in shops if shop.name.lower() in hung)
    if held:
        slow = slow | held
        emit(f"[R3 BUYER] hung_shops skipped={','.join(sorted(held))}")
    offer_weight = cheap_first(wall_weighter(walls), cheap, dear)
    if close_scaled(order):
        with contextlib.suppress(OSError, sqlite3.Error, ValueError):
            offer_weight, factors = close_weighter(
                offer_weight,
                shops,
                events.close_counts(CLOSE_HOURS, order.acceptable),
            )
            ranked = sorted(factors.items(), key=lambda item: -item[1])
            names = {shop.url.rstrip("/"): shop.name for shop in shops}
            emit(
                "[R3 BUYER] close_rate "
                + " ".join(f"{names[url]}={f:.2f}" for url, f in ranked[:3])
            )
        with contextlib.suppress(OSError, sqlite3.Error, ValueError):
            offer_weight, paced = time_weighter(
                offer_weight, shops, events.time_yield(TIME_HOURS, order.kind)
            )
            slowest = sorted(paced.items(), key=lambda item: item[1])
            names = {shop.url.rstrip("/"): shop.name for shop in shops}
            emit(
                "[R3 BUYER] time_yield "
                + " ".join(f"{names[url]}={f:.2f}" for url, f in slowest[:3])
            )
    picture = load_picture(picture_path(state))
    reliability: dict[str, float] = {}
    with contextlib.suppress(OSError, sqlite3.Error, ValueError):
        reliability = refund_reliability(
            ledger_file(state), OUR_SHOP_NAME, WALL_CLOCK()
        )
    warranty_odds: tuple[dict[str, float], float] | None = None
    with contextlib.suppress(OSError, sqlite3.Error, ValueError):
        warranty_odds = warranty_share(ledger_file(state), OUR_SHOP_NAME, WALL_CLOCK())
    feedback = (
        load_saved_feedback(state.parent / SAVED_FEEDBACK_FILE, WALL_CLOCK())
        if policy.buyer.surplus_score and order.kind == "goods"
        else None
    )
    sku_feedback = (
        load_saved_feedback(state.parent / SKU_FEEDBACK_FILE, WALL_CLOCK())
        if feedback is not None and (state.parent / ENABLED_FILE).exists()
        else None
    )
    skip_unfit = unfit_skipper(unfit, order, policy.buyer, WALL_CLOCK())
    cost_of = functools.lru_cache(maxsize=64)(
        functools.partial(unit_cost, ledger_file(state), now=WALL_CLOCK())
    )

    # 11/17 10:5x (owner): a shop whose last LOSING_SHOP_STREAK attempts all failed
    # sits out LOSING_SHOP_MINUTES (price rules as before; cheap evidence too).
    if LOSING_SHOP_ON and events is not None:
        with contextlib.suppress(OSError, sqlite3.Error):
            losing = events.losing_shops(LOSING_SHOP_STREAK, LOSING_SHOP_MINUTES)
            if losing:
                cooling = cooling | losing
                emit(f"[R3 BUYER] losing_shops={len(losing)}")

    def skip_offer(shop: str, sku: str) -> bool:
        return skip_unfit(shop, sku) or (shop.rstrip("/"), sku) in refused_skus

    def pick(skip: frozenset[str] = frozenset()) -> Choice | None:
        skipped_urls = {shop.url.rstrip("/") for shop in shops if shop.name in skip}
        aliases = frozenset(
            shop.name for shop in shops if shop.url.rstrip("/") in skipped_urls
        )
        return choose(
            order,
            shops,
            catalog,
            outcomes,
            reasons,
            blocked=blocked | cooling | slow | skip | aliases,
            picture=picture,
            ratios=ratios,
            explore=0.0 if big else policy.buyer.explore_rate,
            recent=recent,
            skip_offer=skip_offer,
            offer_weight=offer_weight,
            need_margin_hours=policy.buyer.food_margin_hours,
            stock_misses=misses,
            rivals=rivals,
            thompson=policy.buyer.thompson,
            surplus_score=policy.buyer.surplus_score,
            mature_losses=feedback.shops if feedback is not None else None,
            sku_evidence=(
                sku_feedback.skus
                if feedback is not None
                and sku_feedback is not None
                and sku_feedback.generated_at == feedback.generated_at
                and sku_feedback.ledger_latest_at == feedback.ledger_latest_at
                and sku_feedback.owner_control_until == feedback.owner_control_until
                and feedback.owner_control_until > WALL_CLOCK()
                and (state.parent / ENABLED_FILE).exists()
                else None
            ),
        )

    choice = pick()
    if choice is None:
        listed_stock = any(
            offer.qty > 0 and (not order.acceptable or offer.sku in order.acceptable)
            for shop in shops
            for offer in shop.offers
        )
        missing_reason = "no eligible candidate" if listed_stock else "out of stock"
        if events is not None:
            with contextlib.suppress(OSError, sqlite3.Error):
                events.record_attempt(
                    order=order.order,
                    shop="",
                    shop_name="",
                    sku="",
                    list_price=0,
                    price=None,
                    outcome="no_deal",
                    reason=missing_reason,
                    elapsed=time.monotonic() - started,
                    market_round=3 if order.acceptable else 2,
                )
        flush_food_evidence(events, food_evidence, emit)
        fallback = shops[0].url if shops else ""
        return Outcome(
            fallback,
            None,
            3,
            "No shop stocks an acceptable SKU.",
            None,
            None,
            "no_stock",
        )
    emit(
        f"[R3 BUYER] choose shop={choice.shop.name} sku={choice.sku} "
        f"list={choice.list_price} score={choice.score:.0f}"
        + (" explore=1" if choice.explored else "")
        + (f" cooling={len(cooling)}" if cooling else "")
    )
    security = SecurityEvents()
    reader = ModelClient.from_env(events, gate=TRADE_GATE)
    reason, price, receipt, task_state = "", None, None, "none"
    suspicious = False
    try:
        time_left = order_clock.deadline - time.monotonic()
        with anyio.fail_after(max(0.0, time_left)):
            await anyio.lowlevel.checkpoint()
            async with create_client() as client:
                remote = Remote(client, emit, security)
                skipped: set[str] = set()
                attempted: set[str] = set()
                opened: dict[str, tuple[str, Task]] = {}
                # The second quote comes only from a shop with no refused payment in
                # memory: a cheap quote is worth nothing if the checkout fails again.
                rival = (
                    pick(frozenset({choice.shop.name, *reasons}))
                    if policy.buyer.dual_quote and not big
                    else None
                )
                if rival is not None and rival.shop.url != choice.shop.url:
                    attempted.update(c.shop.url.rstrip("/") for c in (choice, rival))
                    quotes = await first_quotes(remote, [choice, rival], order_clock)
                    opened.update((c.shop.url, (e, t)) for c, e, t in quotes)
                    skipped.update(
                        c.shop.name for c in (choice, rival) if c.shop.url not in opened
                    )
                    # The quote's surplus times the shop's evidence weight (refusals,
                    # stock odds, rivalry), so a cheap quote from a shop that failed
                    # our payment before does not win on price alone.
                    valued = [
                        (
                            v
                            * c.score
                            / (
                                (
                                    max(1, c.list_price)
                                    if policy.buyer.surplus_score
                                    else 1
                                )
                                * max(1e-9, c.price_term)
                            ),
                            c,
                            e,
                            t,
                        )
                        for c, e, t in quotes
                        if (v := quote_value(c, t, order.budget, order, policy.buyer))
                        is not None
                    ]
                    if valued:
                        best = max(valued, key=lambda q: q[0])[1]
                        emit(
                            "[R3 BUYER] dual quote "
                            + " vs ".join(
                                f"{c.shop.name}={seller_ask(t)}" for c, _, t in quotes
                            )
                            + f" -> {best.shop.name}"
                        )
                        choice = best
                    elif choice.shop.name in skipped:
                        retry = (
                            pick(frozenset(skipped))
                            if order_clock.retry_allowed(
                                time.monotonic(), len(attempted)
                            )
                            else None
                        )
                        if retry is None:
                            message = "shop timeout"
                            raise ShopTimeoutError(message)  # noqa: TRY301 - close exhausted preflight in the order's handler
                        choice = retry

                def observe_food_task(
                    observed_task: Task,
                    stage: Literal["quote", "acceptance", "receipt"],
                    offered_price: int | None,
                ) -> None:
                    if events is not None and choice is not None:
                        observation = food_task_evidence(
                            order,
                            policy,
                            choice,
                            observed_task,
                            stage=stage,
                            price=offered_price,
                        )
                        if observation is not None:
                            food_evidence.append(observation)

                committed_shops: set[str] = set()
                while True:
                    attempted.add(choice.shop.url.rstrip("/"))
                    try:
                        quoted = opened.pop(choice.shop.url, None)
                        if quoted is not None:
                            endpoint_now, first_task = quoted
                        else:
                            endpoint_now, first_task = await first_reply(
                                remote, choice, order_clock
                            )
                        task, price = await negotiate(
                            remote,
                            endpoint_now,
                            choice,
                            order.budget,
                            policy=policy.buyer,
                            emit=emit,
                            clock=time.monotonic,
                            reader=reader,
                            order=order,
                            opened=first_task,
                            order_clock=order_clock,
                            committed_shops=committed_shops,
                            observe_food=observe_food_task
                            if order.kind == "food"
                            else None,
                            reliability=reliability,
                            warranty_odds=warranty_odds,
                            match_price=functools.partial(
                                ledger_low_price,
                                ledger_file(state),
                                choice.sku,
                                choice.shop.url,
                            ),
                            cost_of=cost_of,
                        )
                    except UnfitUnitError as short:
                        spent = time.monotonic() - started
                        skipped.add(choice.shop.name)
                        retry = (
                            pick(frozenset(skipped))
                            if not committed_shops
                            and order_clock.retry_allowed(
                                time.monotonic(), len(attempted)
                            )
                            else None
                        )
                        if retry is None:
                            raise
                        emit(
                            f"[R3 BUYER] skip shop={choice.shop.name} ({short}); "
                            f"retry shop={retry.shop.name} sku={retry.sku}"
                        )
                        if events is not None:
                            with contextlib.suppress(OSError, sqlite3.Error):
                                events.record_attempt(
                                    order=order.order,
                                    shop=choice.shop.url.rstrip("/"),
                                    shop_name=choice.shop.name,
                                    sku=choice.sku,
                                    list_price=choice.list_price,
                                    price=None,
                                    outcome="no_deal",
                                    reason="shop timeout"
                                    if isinstance(short, ShopTimeoutError)
                                    else f"{type(short).__name__}: {short}",
                                    elapsed=spent,
                                    market_round=3,
                                )
                        choice = retry
                        continue
                    # A price wall (the shop's lowest ask stays above our cap) ends
                    # in a couple of seconds, yet the market hands out one order per
                    # ~20 s: on 11/5 05:00-06:58 a third of the lost orders were
                    # walls or empty shelves with that time unused. Try the next
                    # shop instead of closing the order empty-handed.
                    state_now = task.status.state
                    spent = time.monotonic() - started
                    walled = state_now in WALL_STATES
                    retry = (
                        pick(frozenset({*skipped, choice.shop.name}))
                        if walled
                        and not committed_shops
                        and order_clock.retry_allowed(time.monotonic(), len(attempted))
                        else None
                    )
                    if retry is not None:
                        skipped.add(choice.shop.name)
                        emit(
                            f"[R3 BUYER] price wall at shop={choice.shop.name} "
                            f"({state_now}); retry shop={retry.shop.name} "
                            f"sku={retry.sku}"
                        )
                        if events is not None:
                            with contextlib.suppress(OSError, sqlite3.Error):
                                events.record_attempt(
                                    order=order.order,
                                    shop=choice.shop.url.rstrip("/"),
                                    shop_name=choice.shop.name,
                                    sku=choice.sku,
                                    list_price=choice.list_price,
                                    price=None,
                                    outcome="no_deal",
                                    reason=(f"{state_now}: {task.observation()[:120]}"),
                                    elapsed=spent,
                                    market_round=3,
                                )
                        choice = retry
                        continue
                    # A signed receipt we will refuse (grade C, no warranty, or
                    # under the credit line) is not paid, so the order can still
                    # go to the next shop: 11/8 11:20-12:36, 89 of 136 goods
                    # orders ended at the receipt with most of their time left.
                    early = (
                        receipt_from(task)
                        if task.status.state == "TASK_STATE_COMPLETED"
                        and order.acceptable
                        else None
                    )
                    if early is not None:
                        _, early_problem = check_receipt_value(
                            early,
                            order,
                            choice,
                            catalog,
                            price,
                            policy.buyer,
                            reliability,
                            cost_of,
                        )
                        spent = time.monotonic() - started
                        if early_problem is not None:
                            # This completed receipt is rejected locally before
                            # settlement; an uncertain acceptance never gets here.
                            order_clock.committed.discard(choice.shop.url)
                            committed_shops.discard(choice.shop.url)
                        retry = (
                            pick(frozenset({*skipped, choice.shop.name}))
                            if early_problem is not None
                            and order_clock.retry_allowed(
                                time.monotonic(), len(attempted)
                            )
                            else None
                        )
                        if retry is not None:
                            skipped.add(choice.shop.name)
                            note = early_problem or RECEIPT_REFUSAL
                            emit(
                                f"[R3 BUYER] {note} at shop={choice.shop.name}; "
                                f"retry shop={retry.shop.name} sku={retry.sku}"
                            )
                            if events is not None:
                                with contextlib.suppress(OSError, sqlite3.Error):
                                    events.record_attempt(
                                        order=order.order,
                                        shop=choice.shop.url.rstrip("/"),
                                        shop_name=choice.shop.name,
                                        sku=choice.sku,
                                        list_price=choice.list_price,
                                        price=None,
                                        outcome="no_deal",
                                        reason=note,
                                        elapsed=spent,
                                        market_round=3,
                                    )
                            choice, price = retry, None
                            continue
                    break
                task_state = task.status.state
                if task_state == "TASK_STATE_COMPLETED":
                    receipt = receipt_from(task)
                    if receipt is None:
                        reason = "completed without a receipt"
                    else:
                        problem = valid_receipt(
                            receipt,
                            order,
                            choice,
                            price,
                            price_cap(choice, order.budget, policy.buyer),
                        )
                        if problem is None and order.acceptable:
                            _, problem = check_receipt_value(
                                receipt,
                                order,
                                choice,
                                catalog,
                                price,
                                policy.buyer,
                                reliability,
                                cost_of,
                            )
                        if problem is not None:
                            reason, receipt, price = problem, None, None
                        else:
                            observe_food_task(task, "receipt", price)
                else:
                    reason = f"{task_state}: {task.observation()[:120]}"
    except ShopTimeoutError:
        reason = "shop timeout"
    except TimeoutError:
        reason = "timed out"
    except SuspiciousCounterpartError as exc:
        reason, suspicious = f"suspicious seller text: {exc}"[:200], True
    except (HarnessError, httpx2.HTTPError, ValidationError) as exc:
        reason = f"{type(exc).__name__}: {exc}"[:200]
    if security.snapshot().operator_directive_observed:
        reason, receipt = "operator directive observed in seller text", None
        suspicious = True
    elapsed = time.monotonic() - started
    if receipt is not None and price is not None:
        outcome = "agreed"
        pct = (100 * price // choice.list_price) if choice.list_price else 0
        review = (
            f"Agreed {choice.name} ({choice.sku}) at {price}"
            + (f", {pct}% of list price" if choice.list_price else "")
            + ". Settlement and certified quality pending."
        )
        stars = 4
    else:
        outcome = "suspicious" if suspicious else "no_deal"
        review = f"No deal with this shop: {reason or 'no agreement'}."
        stars = 3
    emit(
        f"[R3 BUYER] outcome={outcome} price={price} elapsed={elapsed:.1f}s "
        f"reason={reason!r}"
    )
    if events is not None:
        try:
            events.record_attempt(
                order=order.order,
                shop=choice.shop.url.rstrip("/"),
                shop_name=choice.shop.name,
                sku=choice.sku,
                list_price=choice.list_price,
                price=price,
                outcome=outcome,
                reason=reason,
                elapsed=elapsed,
                market_round=3 if order.acceptable else 2,
            )
        except OSError:
            emit("[R3 BUYER] events write failed")
    flush_food_evidence(events, food_evidence, emit)
    return Outcome(
        choice.shop.url,
        receipt,
        stars,
        review,
        accept_policy(order, policy.buyer) if receipt is not None else None,
        price,
        reason,
    )


def result_line(outcome: Outcome) -> str:
    """Render the one JSON line the kit reads from the buyer's output."""
    body: dict[str, object] = {
        "shop": outcome.shop,
        "receipt": outcome.receipt,
        "stars": outcome.stars,
        "review": outcome.review,
    }
    if outcome.accept:
        body["accept"] = outcome.accept
    return json.dumps(body, ensure_ascii=False)
