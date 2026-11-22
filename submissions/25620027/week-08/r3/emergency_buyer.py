"""Owner emergency: normalized prices and minimum useful purchase credit."""

from __future__ import annotations

import dataclasses
import json
import math
import re
import sqlite3
import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from pydantic import Field, ValidationError, field_validator

from r3.market_files import KitFile

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from pydantic import JsonValue

    from market_a2a.remote import Task

MIN_EXPECTED_CREDIT: Final = 0.34
BREAK_RISK: Final = {"A": 0.044, "B": 0.131, "?": 0.063}
# "?" = a quote that names no grade: the kit pays only A or B, so the risk is the
# A/B mix of goods supply (A 70 %, B 20 %); the receipt's certificate re-checks it.
UNKNOWN_GRADE: Final = "?"
MARKET_PAID_RATIO: Final = 0.72
FULL_WARRANTY_HOURS: Final = 6
NAKED_A_CAP: Final = 0.60
# Patch 3.6 (11/12 18:19): Saved per deal is (list - paid) / (list - cost), 1 at or
# under cost, -1 for a useless unit, 0 refunded, over all of Round 3. The cost is
# the seller's unit cost, so we price against an estimate of it: the market draws
# A at 55-75 % of list and B at 40-60 %; the notice gives food about 52 %. The
# unknown "?" grade is the A/B supply mix.
GOODS_COST_RATIO: Final = {"A": 0.65, "B": 0.50, "?": 0.62}
FOOD_COST_RATIO: Final = 0.52
# The ledger's supply rows name every unit and its lot's total cost, so a unit
# the seller names (certificate or receipt) is priced at its real cost. 11/12
# 19:38-21:20: 141 deals averaged 0.874 of the margin, yet some kept only
# 0.60-0.68 because the unit cost less than the estimate. Four days of supply
# scan in about 0.1 s; an older unit falls back to the estimate.
UNIT_COST_DAYS: Final = 4.0


@dataclass(frozen=True, slots=True)
class PurchaseValue:
    """Keep the exact proposed price and quality facts together."""

    kind: str | None
    grade: str | None
    warranty_hours: float
    list_price: int
    price: int
    # Share of covered breaks this shop really refunded (11/7: 47 of 89 broken
    # goods came back refunded); 1.0 when we have no history with it.
    refund_reliability: float = 1.0
    # The credit a purchase must add (policy buyer.min_expected_credit, owner).
    min_credit: float = MIN_EXPECTED_CREDIT
    # Patch 3.6: when set, credit is the share of the seller's margin we kept and
    # this is the line it must reach; the list-discount rules above stand aside.
    margin_line: float | None = None
    # The named unit's real cost over list, from the ledger; None = estimate.
    known_cost: float | None = None

    def protection(self) -> float:
        """Share of the break loss a warranty really removes."""
        hours = min(max(self.warranty_hours, 0.0), FULL_WARRANTY_HOURS)
        reliability = min(max(self.refund_reliability, 0.0), 1.0)
        return reliability * hours / FULL_WARRANTY_HOURS

    def cost_ratio(self) -> float | None:
        """Seller unit cost over list: the named unit's, else the estimate."""
        if self.known_cost is not None:
            return self.known_cost
        if self.kind == "food":
            return FOOD_COST_RATIO
        return GOODS_COST_RATIO.get(self.grade or "")

    def margin_share(self) -> float | None:
        """Patch 3.6 credit before breaks: the seller's margin we kept, at most 1."""
        cost = self.cost_ratio()
        if cost is None or self.list_price <= 0:
            return None
        paid = self.price / self.list_price
        if paid <= cost:
            return 1.0
        return (1 - paid) / (1 - cost)

    def line(self) -> float:
        """Return the credit a purchase must reach under the active Saved rule."""
        return self.margin_line if self.margin_line is not None else self.min_credit

    def expected_credit(self) -> float | None:
        """Return expected normalized value, preserving unknown quality."""
        if self.list_price <= 0 or self.price < 0 or self.kind not in {"food", "goods"}:
            return None
        discount = (
            self.margin_share()
            if self.margin_line is not None
            else 1 - self.price / self.list_price
        )
        if discount is None:
            return None
        if self.kind == "food":
            return discount
        risk = BREAK_RISK.get(self.grade or "")
        if risk is None:
            return None
        return (1 - risk) * discount - risk * (1 - self.protection())

    def acceptable(self) -> bool:
        """Apply minimum credit and the owner's explicit quality exclusions."""
        credit = self.expected_credit()
        return (
            credit is not None
            and credit + 1e-12 >= self.line()
            and not (
                self.kind == "goods"
                and self.grade == "B"
                and self.warranty_hours < FULL_WARRANTY_HOURS
            )
            and not (
                self.margin_line is None
                and self.kind == "goods"
                and self.grade in {"A", UNKNOWN_GRADE}
                and self.warranty_hours < FULL_WARRANTY_HOURS
                and self.price / self.list_price > NAKED_A_CAP
            )
        )

    def max_price(self) -> int | None:
        """Highest price (whole credits) this unit still passes at, or None."""
        if self.list_price <= 0 or self.kind not in {"food", "goods"}:
            return None
        if (
            self.kind == "goods"
            and self.grade == "B"
            and self.warranty_hours < FULL_WARRANTY_HOURS
        ):
            return None
        if self.margin_line is not None:
            ratio = self.margin_ratio()
            if ratio is None:
                return None
        elif self.kind == "food":
            ratio = 1 - self.min_credit
        else:
            risk = BREAK_RISK.get(self.grade or "")
            if risk is None:
                return None
            loss = risk * (1 - self.protection())
            ratio = 1 - (self.min_credit + loss) / (1 - risk)
            if (
                self.grade in {"A", UNKNOWN_GRADE}
                and self.warranty_hours < FULL_WARRANTY_HOURS
            ):
                ratio = min(ratio, NAKED_A_CAP)
        price = math.floor(self.list_price * ratio)
        while price > 0 and not dataclasses.replace(self, price=price).acceptable():
            price -= max(1, self.list_price // 10000)
        return price if price > 0 else None

    def margin_ratio(self) -> float | None:
        """Patch 3.6: the highest paid/list whose expected credit meets the line."""
        cost = self.cost_ratio()
        if cost is None or cost >= 1:
            return None
        share = self.line()
        if self.kind == "goods":
            risk = BREAK_RISK.get(self.grade or "")
            if risk is None:
                return None
            share = (share + risk * (1 - self.protection())) / (1 - risk)
        if share > 1:
            return None
        return 1 - share * (1 - cost)


FINAL_WORDS: Final = re.compile(
    r"\b(final|lowest|best (?:i|we) can|floor|bottom|cannot go lower|can't go lower|"
    r"no deal below|last offer|take it or leave it)\b|최종|최저|마지막|더는",
    re.IGNORECASE,
)


def says_final(text: str) -> bool:
    """Tell whether a seller says its ask will not move (sellers' know-how, 11/7)."""
    return bool(FINAL_WORDS.search(text or ""))


def refund_reliability(
    db: Path, buyer_name: str, now: float, hours: float = 72.0
) -> dict[str, float]:
    """Shop URL -> share of our covered broken goods it refunded (Beta(1,1) prior)."""
    since = now - hours * 3600
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        shop_of: dict[str, str] = {}
        for unit, warranty, shop in con.execute(
            "select json_extract(data,'$.unit'), json_extract(data,'$.warranty_hours'),"
            " shop from rows where kind='settle' and buyer=? and ts>?",
            (buyer_name, since - 6 * 3600),
        ):
            if unit and isinstance(warranty, (int, float)) and warranty > 0:
                shop_of[str(unit)] = (shop or "").rstrip("/")
        tally: dict[str, list[int]] = {}
        for (data,) in con.execute(
            "select data from rows where kind='broken' and buyer=? and ts>?",
            (buyer_name, since),
        ):
            row = json.loads(data or "{}")
            shop = shop_of.get(str(row.get("unit") or ""))
            if shop is None:
                continue
            count = tally.setdefault(shop, [0, 0])
            count[1] += 1
            if int(row.get("refunded") or 0) > 0:
                count[0] += 1
    finally:
        con.close()
    return {shop: (ok + 1) / (n + 2) for shop, (ok, n) in tally.items()}


def discount_factor(url: str, ratios: Mapping[str, float]) -> float:
    """Use observed shop paid/list, with the observed market median as prior."""
    finite = [v for v in ratios.values() if math.isfinite(v) and 0 < v <= 1]
    prior = statistics.median(finite) if finite else MARKET_PAID_RATIO
    ratio = ratios.get(url.rstrip("/"), prior)
    ratio = ratio if math.isfinite(ratio) and 0 < ratio <= 1 else prior
    return max(0.0, 1 - ratio)


def warranty_hours(task: Task) -> float:
    """Require an explicit unit-level warranty; unknown/partial coverage is naked."""
    sources: list[dict[str, JsonValue]] = [
        {"warranty_hours": task.status.message.metadata.warranty_hours},
        {"warranty_hours": task.metadata.warranty_hours},
    ]
    sources.extend(p.data for p in task.status.message.parts if p.data is not None)
    sources.extend(
        p.data for a in task.artifacts for p in a.parts if p.data is not None
    )
    values: list[float] = []
    for source in sources:
        value = source.get("warranty_hours")
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        ):
            values.append(float(value))
    return min(values) if values else 0.0


def stated_warranty(task: Task) -> float | None:
    """Return the quote's warranty hours, or None when the seller has not said."""
    sources: list[dict[str, JsonValue]] = [
        {"warranty_hours": task.status.message.metadata.warranty_hours},
        {"warranty_hours": task.metadata.warranty_hours},
    ]
    sources.extend(p.data for p in task.status.message.parts if p.data is not None)
    for source in sources:
        value = source.get("warranty_hours")
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        ):
            return warranty_hours(task)
    return None


def warranty_share(
    db: Path, buyer_name: str, now: float, hours: float = 72.0
) -> tuple[dict[str, float], float]:
    """Shop URL -> share of our goods receipts that carried >= 6 h, and the overall.

    11/8: most goods quotes name no warranty, so the line priced them as naked
    (0.57 of list) and no goods deal closed overnight; many of those shops do put
    a warranty on the receipt, which is checked again before we pay.
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        tally: dict[str, list[int]] = {}
        for shop, warranty, expires in con.execute(
            "select shop, json_extract(data,'$.warranty_hours'),"
            " json_extract(data,'$.expires_at') from rows"
            " where kind='settle' and buyer=? and ts>?",
            (buyer_name, now - hours * 3600),
        ):
            if expires is not None:
                continue  # food carries no warranty
            count = tally.setdefault((shop or "").rstrip("/"), [0, 0])
            count[1] += 1
            if isinstance(warranty, (int, float)) and warranty >= FULL_WARRANTY_HOURS:
                count[0] += 1
    finally:
        con.close()
    covered = sum(c[0] for c in tally.values())
    total = sum(c[1] for c in tally.values())
    overall = (covered + 1) / (total + 2)
    return {shop: (ok + 1) / (n + 2) for shop, (ok, n) in tally.items()}, overall


class _Grade(KitFile):
    grade: str | None = None
    unit: str | None = None


class _Certificate(KitFile):
    cert: _Grade | None = None


class _Receipt(KitFile):
    cert: _Certificate | None = None
    unit: str | None = None
    claim: str | None = None
    warranty_hours: float = Field(default=0, ge=0, allow_inf_nan=False)

    # 11/15 08:1x: several shops (sergiobuilds, gom31, totato2203-r3, cheapest-in-
    # town, yeonsong2, moss-and-oak ...) sign receipts with ``"cert": true`` and the
    # grade in ``claim``. The whole receipt failed to parse, read as grade unknown
    # with no warranty, and 142 agreed goods deals in 6 h were refused at the last
    # step ("claim None, warranty 0 h"). A non-object certificate is ignored so the
    # claim decides; the kit still checks the certified grade at checkout.
    @field_validator("cert", mode="before")
    @classmethod
    def _object_cert_only(cls, value: object) -> object:
        return value if value is None or isinstance(value, dict) else None


def receipt_value(receipt: dict[str, object], value: PurchaseValue) -> PurchaseValue:
    """Use the actual promised receipt warranty, never a previous quote's claim."""
    try:
        parsed = _Receipt.model_validate(receipt)
    except ValidationError:
        return dataclasses.replace(value, grade=None, warranty_hours=0)
    # Sellers' receipts carry the claimed grade; the certificate is attached by the
    # kit at checkout, where it pays only A or B. Without this, every goods receipt
    # read as grade unknown and was refused (11/7 21:43 - 11/8 10:50: 0 goods).
    grade = (
        parsed.cert.cert.grade
        if parsed.cert and parsed.cert.cert and parsed.cert.cert.grade
        else parsed.claim
    )
    if not grade and value.kind == "goods":
        # Neither certificate nor claim: the kit still checks the certified grade
        # at checkout and pays only A or B, so price it at the A/B supply mix.
        grade = UNKNOWN_GRADE
    return dataclasses.replace(value, grade=grade, warranty_hours=parsed.warranty_hours)


def receipt_unit(receipt: Mapping[str, object]) -> str | None:
    """Return the unit a receipt or its certificate names, if any."""
    try:
        parsed = _Receipt.model_validate(dict(receipt))
    except ValidationError:
        return None
    if parsed.unit:
        return parsed.unit
    if parsed.cert is not None and parsed.cert.cert is not None:
        return parsed.cert.cert.unit or None
    return None


def unit_cost(db: Path, unit: str, now: float) -> int | None:
    """Return a unit's cost (its supply lot's average) from the public ledger."""
    if not unit.startswith("u-"):
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return None
    try:
        row = con.execute(
            "select data from rows where kind='supply' and ts>=? and instr(data,?)>0 "
            "order by seq desc limit 1",
            (now - UNIT_COST_DAYS * 86400, f'"{unit}"'),
        ).fetchone()
        if row is None:
            return None
        lot = json.loads(row[0] or "{}")
        units = [str(u) for u in lot.get("units") or []]
        total = lot.get("total")
        if unit not in units or not isinstance(total, (int, float)) or total <= 0:
            return None
        return round(total / len(units))
    except (sqlite3.Error, ValueError):
        return None
    finally:
        con.close()
