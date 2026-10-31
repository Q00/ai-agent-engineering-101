"""The scout's market picture: one shared, validated document for every slow loop.

Code computes the numbers (per-shop settlements, dishonest claims, breaks, our own
payments and refusals, category medians, cash and sales alerts) from the public
ledger mirror and our own event ledger. The scout model reads the free text that
code cannot judge (public reviews, forum posts) and writes bounded verdicts: a
trust score per known shop, flags from a fixed list, hot categories chosen from the
market's own demand list. Shop names and categories outside the allowlists are
dropped; nothing in the picture is ever repeated to a counterpart. Alerts that wake
the strategy model come only from the numbers.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Final, Literal

from pydantic import Field, ValidationError

from market_a2a.models import FrozenModel
from r3.guard import sanitize, sanitize_name, suspicious
from r3.market_files import _read_json, read_shops

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from r3.events import EventLedger
    from r3.ledger_db import LedgerDB
    from r3.market_files import CatalogItem, Inventory
    from r3.policy import Policy

PICTURE_ENV: Final = "AX_R3_PICTURE"
PICTURE_FILE: Final = "r3-picture.json"
MAX_SHOPS: Final = 60
MAX_SNIPPETS_PER_SHOP: Final = 4
MAX_FORUM_POSTS: Final = 30
NEUTRAL_TRUST: Final = 0.67
Flag = Literal[
    "dishonest_claims",
    "frequent_breaks",
    "refused_our_payment",
    "no_certificate",
    "reliable",
    "aggressive_pricing",
    "injection_attempt",
    "rating_inducement",
]
Alert = Literal[
    "cash_near_reserve",
    "no_sales_30m",
    "refusals_spike",
    "stars_drop",
    "undercut",
    "stock_empty",
]


class ShopTrust(FrozenModel):
    """The scout's verdict on one shop, bounded and allowlisted."""

    url: str = Field(max_length=200)
    name: str = Field(default="", max_length=80)
    trust: float = Field(default=NEUTRAL_TRUST, ge=0.0, le=1.0)
    flags: tuple[Flag, ...] = Field(default=(), max_length=4)
    note: str = Field(default="", max_length=120)


class Picture(FrozenModel):
    """What every slow loop reads: numbers from code, verdicts from the scout."""

    written_at: float = 0.0
    by: str = Field(default="code", max_length=48)
    shops: tuple[ShopTrust, ...] = Field(default=(), max_length=MAX_SHOPS)
    hot_categories: tuple[str, ...] = Field(default=(), max_length=10)
    cold_categories: tuple[str, ...] = Field(default=(), max_length=10)
    alerts: tuple[Alert, ...] = Field(default=(), max_length=6)
    notes: str = Field(default="", max_length=300)
    facts: dict[str, object] = Field(default_factory=dict)

    def trust_of(self, url: str) -> float:
        """Return the trust score for a shop URL, neutral when unknown."""
        key = url.rstrip("/")
        for shop in self.shops:
            if shop.url.rstrip("/") == key:
                return shop.trust
        return NEUTRAL_TRUST


def picture_path(market_dir: Path | None = None) -> Path:
    """Resolve the picture file: explicit env, else beside the kit's .market folder."""
    configured = os.environ.get(PICTURE_ENV)
    if configured:
        return Path(configured)
    base = (
        market_dir
        if market_dir is not None
        else Path(os.environ.get("MARKET_DIR", ".market"))
    )
    return base.resolve().parent / PICTURE_FILE


def load_picture(path: Path) -> Picture:
    """Return the stored picture; missing or invalid means an empty one."""
    try:
        return Picture.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError):
        return Picture()


def save_picture(path: Path, picture: Picture) -> None:
    """Write the picture atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            _ = stream.write(picture.model_dump_json(indent=1))
            stream.flush()
            _ = temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def trust_factor(trust: float) -> float:
    """Map a 0..1 trust score onto a 0.6..1.2 weight; neutral trust is 1.0."""
    return round(0.6 + 0.6 * max(0.0, min(1.0, trust)), 3)


def shop_facts(
    ledger: LedgerDB,
    events: EventLedger | None,
    shops_file: Path,
    *,
    hours: float,
    our_shop: str,
) -> dict[str, dict[str, object]]:
    """Per-shop numbers from the ledger mirror, our payments and public reviews."""
    since = time.time() - hours * 3600
    facts: dict[str, dict[str, object]] = {}

    def bucket(url: str) -> dict[str, object]:
        key = url.rstrip("/")
        return facts.setdefault(
            key,
            {
                "name": "",
                "settles": 0,
                "dishonest": 0,
                "paid_by_us": 0,
                "refused_us": 0,
                "stars": [],
                "reviews": [],
            },
        )

    for _, _buyer, shop, _sku, _amount, grade, claim in ledger.settles(since):
        if not shop or shop.rstrip("/") == our_shop.rstrip("/"):
            continue
        row = bucket(shop)
        row["settles"] = int(str(row["settles"])) + 1
        if claim and grade and claim != grade:
            row["dishonest"] = int(str(row["dishonest"])) + 1
    if events is not None:
        for shop, counts in events.shop_outcomes(hours).items():
            row = bucket(shop)
            row["paid_by_us"] = counts.get("paid", 0)
            row["refused_us"] = counts.get("refused", 0) + counts.get("suspicious", 0)
    for entry in read_shops(shops_file):
        row = bucket(entry.url)
        row["name"] = sanitize_name(entry.name)
        stars = [r.stars for r in entry.reviews if 1 <= r.stars <= 5]  # noqa: PLR2004
        row["stars"] = stars[:50]
        snippets: list[str] = []
        for review in entry.reviews[-MAX_SNIPPETS_PER_SHOP:]:
            text = sanitize(review.review, 200)
            if text:
                flag = suspicious(text)
                snippets.append(f"[{flag}] {text}" if flag else text)
        row["reviews"] = snippets
    return facts


def forum_snippets(market_dir: Path, limit: int = MAX_FORUM_POSTS) -> list[str]:
    """Return bounded, sanitised forum lines; the file shape is the market's own."""
    raw = _read_json(market_dir / "forum.json")
    rows: list[object] = []
    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict):
        inner = raw.get("posts", raw.get("items", []))
        rows = inner if isinstance(inner, list) else []
    out: list[str] = []
    for row in rows[-limit:]:
        if not isinstance(row, dict):
            continue
        text = row.get("text", row.get("post", ""))
        if not isinstance(text, str) or not text.strip():
            continue
        role = row.get("role", row.get("as", ""))
        votes = row.get("score", row.get("votes", row.get("up", "")))
        line = sanitize(text, 240)
        flag = suspicious(line)
        prefix = f"({sanitize_name(str(role))}, votes {votes}) " if role else ""
        out.append(f"{prefix}{'[' + flag + '] ' if flag else ''}{line}")
    return out


def compute_alerts(  # noqa: PLR0913 - every signal is a plain number
    inventory: Inventory,
    policy: Policy,
    *,
    our_settles_30m: int,
    market_settles_30m: int,
    refused_1h: int,
    paid_1h: int,
    stars_24h: float | None,
    stars_1h: float | None,
    median_now: float | None,
    median_before: float | None,
) -> tuple[Alert, ...]:
    """Derive wake-up alerts from numbers only; text never triggers the strategy."""
    alerts: list[Alert] = []
    if inventory.round >= 3 and inventory.balance < policy.restock.reserve_cash * 1.2:  # noqa: PLR2004
        alerts.append("cash_near_reserve")
    if inventory.round >= 3 and not inventory.units:  # noqa: PLR2004
        alerts.append("stock_empty")
    if our_settles_30m == 0 and market_settles_30m >= 20:  # noqa: PLR2004
        alerts.append("no_sales_30m")
    if refused_1h >= 3 and refused_1h > paid_1h:  # noqa: PLR2004
        alerts.append("refusals_spike")
    if stars_24h is not None and stars_1h is not None and stars_1h < stars_24h - 1.0:
        alerts.append("stars_drop")
    if (
        median_now is not None
        and median_before is not None
        and median_before > 0
        and median_now < median_before * 0.9
    ):
        alerts.append("undercut")
    return tuple(alerts[:6])


def picture_schema(
    shop_urls: Iterable[str], categories: Iterable[str]
) -> dict[str, object]:
    """Tool schema for the scout: enums pin every name to the market's own lists."""
    urls = sorted({u.rstrip("/") for u in shop_urls if u})[:MAX_SHOPS]
    cats = sorted({c for c in categories if c})[:400]
    flags = list(Flag.__args__)  # pyright: ignore[reportAttributeAccessIssue]
    return {
        "type": "object",
        "properties": {
            "shops": {
                "type": "array",
                "maxItems": MAX_SHOPS,
                "items": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "enum": urls or ["none"]},
                        "trust": {"type": "number", "minimum": 0, "maximum": 1},
                        "flags": {
                            "type": "array",
                            "maxItems": 4,
                            "items": {"type": "string", "enum": flags},
                        },
                        "note": {"type": "string", "maxLength": 120},
                    },
                    "required": ["url", "trust"],
                    "additionalProperties": False,
                },
            },
            "hot_categories": {
                "type": "array",
                "maxItems": 10,
                "items": {"type": "string", "enum": cats or ["none"]},
            },
            "cold_categories": {
                "type": "array",
                "maxItems": 10,
                "items": {"type": "string", "enum": cats or ["none"]},
            },
            "notes": {"type": "string", "maxLength": 300},
        },
        "required": ["shops", "hot_categories", "notes"],
        "additionalProperties": False,
    }


def validate_verdict(
    arguments: Mapping[str, object],
    *,
    shop_names: Mapping[str, str],
    categories: frozenset[str],
) -> tuple[tuple[ShopTrust, ...], tuple[str, ...], tuple[str, ...], str]:
    """Keep only allowlisted shops and categories from the scout's tool call."""
    shops: list[ShopTrust] = []
    raw_shops = arguments.get("shops")
    for row in raw_shops if isinstance(raw_shops, list) else []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url", "")).rstrip("/")
        if url not in shop_names:
            continue
        try:
            shops.append(
                ShopTrust.model_validate(
                    {
                        "url": url,
                        "name": shop_names[url],
                        "trust": row.get("trust", NEUTRAL_TRUST),
                        "flags": tuple(row.get("flags", ()))[:4]
                        if isinstance(row.get("flags"), list)
                        else (),
                        "note": sanitize(str(row.get("note", "")), 120),
                    }
                )
            )
        except ValidationError:
            continue
        if len(shops) >= MAX_SHOPS:
            break

    def allowed(key: str) -> tuple[str, ...]:
        raw = arguments.get(key)
        if not isinstance(raw, list):
            return ()
        return tuple(dict.fromkeys(str(c) for c in raw if str(c) in categories))[:10]

    notes = sanitize(str(arguments.get("notes", "")), 300)
    return tuple(shops), allowed("hot_categories"), allowed("cold_categories"), notes


def category_names(
    catalog: Mapping[str, CatalogItem], demand_categories: Iterable[str]
) -> frozenset[str]:
    """Categories a model may name: the demand list, else the catalog's."""
    names = {c for c in demand_categories if c}
    if not names:
        names = {item.category for item in catalog.values() if item.category}
    return frozenset(names)


def dump_compact(value: object, limit: int) -> str:
    """JSON for a prompt, bounded."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:limit]
