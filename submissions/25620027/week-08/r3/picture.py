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

import contextlib
import json
import os
import re
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Final, Literal

from pydantic import Field, ValidationError

from market_a2a.models import FrozenModel
from r3.guard import annotate, sanitize, sanitize_name
from r3.market_files import _read_json, read_shops

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping

    from r3.events import EventLedger
    from r3.ledger_db import LedgerDB
    from r3.market_files import CatalogItem, Inventory
    from r3.policy import Policy

PICTURE_ENV: Final = "AX_R3_PICTURE"
PICTURE_FILE: Final = "r3-picture.json"
MAX_SHOPS: Final = 60
MAX_SNIPPETS_PER_SHOP: Final = 2
MAX_FORUM_POSTS: Final = 20
# Buyers' request posts, counted by code over the last hour of the forum.
WANTED: Final = re.compile(
    r"구합니다|구해요|구함|찾습니다|찾아요|wanted|looking for", re.IGNORECASE
)
WANTED_MIN_POSTS: Final = 2
MAX_WANTS: Final = 10
MAX_HOT: Final = 10
NEUTRAL_TRUST: Final = 0.67
# Code-enforced trust ceilings: a shop whose reviews are mostly its own owner's, or
# that fills reviews with appeals to buying agents, cannot be rated above these.
SELF_REVIEW_CAP: Final = 0.5
SELF_REVIEW_SHARE: Final = 0.5
AI_APPEAL_CAP: Final = 0.6
AI_APPEAL_COUNT: Final = 3
# Goods from a shop that break this often (with enough observed sales) cap its
# trust: a broken unit without warranty costs our buyer the whole price.
BREAK_CAP: Final = 0.4
BREAK_SHARE: Final = 0.25
BREAK_MIN_OBSERVED: Final = 5
Flag = Literal[
    "dishonest_claims",
    "frequent_breaks",
    "refused_our_payment",
    "no_certificate",
    "reliable",
    "aggressive_pricing",
    "injection_attempt",
    "rating_inducement",
    "self_reviews",
    "ai_appeal",
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
    """Write the picture atomically and append it to ``*-history.jsonl`` beside it."""
    with (
        contextlib.suppress(OSError),
        path.with_name(path.stem + "-history.jsonl").open(
            "a", encoding="utf-8"
        ) as stream,
    ):
        _ = stream.write(picture.model_dump_json() + "\n")
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


def shop_facts(  # noqa: PLR0913, C901 - one pass over every evidence source
    ledger: LedgerDB,
    events: EventLedger | None,
    shops_file: Path,
    *,
    hours: float,
    our_shop: str,
    goods: Collection[str] = (),
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
                "goods_observed": 0,
                "broken": 0,
            },
        )

    for _, _buyer, shop, _sku, _amount, grade, claim in ledger.settles(since):
        if not shop or shop.rstrip("/") == our_shop.rstrip("/"):
            continue
        row = bucket(shop)
        row["settles"] = int(str(row["settles"])) + 1
        if claim and grade and claim != grade:
            row["dishonest"] = int(str(row["dishonest"])) + 1
    if goods:
        for shop, (broken, observed) in ledger.shop_breaks(since, goods).items():
            if shop == our_shop.rstrip("/"):
                continue
            row = bucket(shop)
            row["broken"], row["goods_observed"] = broken, observed
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
            text = sanitize(review.review, 160)
            if text:
                flag = annotate(text)
                snippets.append(f"[{flag}] {text}" if flag else text)
        row["reviews"] = snippets
    return facts


def review_facts(
    reviews: Iterable[Mapping[str, object]], shop_owner: str
) -> dict[str, object]:
    """Count self-written and agent-appeal reviews from the market's review list.

    Each review may carry ``owner.login`` (the author's GitHub owner); a review by
    the shop's own owner counts for nothing and lowers trust. The share, the
    third-party average and the appeal count go to the scout as numbers.
    """
    total = own = appeals = 0
    third: list[float] = []
    for review in reviews:
        total += 1
        owner = review.get("owner")
        login = str(owner.get("login", "")) if isinstance(owner, dict) else ""
        text = str(review.get("review", "") or "")
        if annotate(text) == "ai_appeal":
            appeals += 1
        stars = review.get("stars")
        if shop_owner and login == shop_owner:
            own += 1
        elif isinstance(stars, (int, float)) and not isinstance(stars, bool):
            third.append(float(stars))
    return {
        "reviews_total": total,
        "self_reviews": own,
        "self_review_share": round(own / total, 2) if total else 0.0,
        "third_party_stars": round(sum(third) / len(third), 2) if third else None,
        "ai_appeal_reviews": appeals,
    }


def trust_cap(review: Mapping[str, object]) -> float | None:
    """Return the code-enforced trust ceiling implied by the review facts, if any."""
    caps: list[float] = []
    share = review.get("self_review_share")
    if isinstance(share, (int, float)) and share >= SELF_REVIEW_SHARE:
        caps.append(SELF_REVIEW_CAP)
    appeals = review.get("ai_appeal_reviews")
    if isinstance(appeals, int) and appeals >= AI_APPEAL_COUNT:
        caps.append(AI_APPEAL_CAP)
    broken, observed = review.get("broken"), review.get("goods_observed")
    if (
        isinstance(broken, int)
        and isinstance(observed, int)
        and observed >= BREAK_MIN_OBSERVED
        and broken >= BREAK_SHARE * observed
    ):
        caps.append(BREAK_CAP)
    return min(caps) if caps else None


def enforce_caps(
    shops: Iterable[ShopTrust], caps: Mapping[str, float], names: Mapping[str, str]
) -> tuple[ShopTrust, ...]:
    """Apply code trust ceilings whether or not the scout model judged the shop.

    A capped shop the model skipped (or every shop, when the model is off or its
    reply failed) still gets its ceiling, so the buyer avoids it either way.
    """
    out: list[ShopTrust] = []
    seen: set[str] = set()
    for shop in shops:
        cap = caps.get(shop.url)
        seen.add(shop.url)
        out.append(
            shop
            if cap is None or shop.trust <= cap
            else shop.model_copy(update={"trust": cap})
        )
    for url, cap in sorted(caps.items()):
        if url not in seen:
            out.append(ShopTrust(url=url, name=names.get(url, "")[:80], trust=cap))
    return tuple(out)


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
        line = sanitize(text, 200)
        flag = annotate(line)
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
    caps: Mapping[str, float] | None = None,
) -> tuple[tuple[ShopTrust, ...], tuple[str, ...], tuple[str, ...], str]:
    """Keep only allowlisted shops and categories from the scout's tool call.

    ``caps`` are code-enforced trust ceilings per shop URL (self-written reviews,
    appeals to agents): the model may rate a shop lower, never higher.
    """
    shops: list[ShopTrust] = []
    raw_shops = arguments.get("shops")
    for row in raw_shops if isinstance(raw_shops, list) else []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url", "")).rstrip("/")
        if url not in shop_names:
            continue
        trust = row.get("trust", NEUTRAL_TRUST)
        cap = (caps or {}).get(url)
        if cap is not None and isinstance(trust, (int, float)):
            trust = min(float(trust), cap)
        try:
            shops.append(
                ShopTrust.model_validate(
                    {
                        "url": url,
                        "name": shop_names[url],
                        "trust": trust,
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


def forum_wants(
    rows: Iterable[object],
    categories: Iterable[str],
    *,
    since: float,
    skip_author: str = "",
) -> dict[str, int]:
    """Count categories named in buyers' wanted posts since ``since``, by code.

    The forum moves dozens of posts a minute, so the scout's 20-post window misses
    most requests ("신선육류 구합니다", "Wanted: ...; category=식품>..."). Only
    categories from the market's own list count, matched by their full path or
    their last segment; the post text itself never reaches a decision.
    """
    leaves: dict[str, str] = {}
    for category in categories:
        leaves.setdefault(category, category)
        leaves.setdefault(category.rsplit(">", 1)[-1], category)
    counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts, text = row.get("ts"), row.get("text")
        if (
            row.get("role") != "buyer"
            or row.get("author") == skip_author
            or not isinstance(ts, (int, float))
            or ts < since
            or not isinstance(text, str)
            or not WANTED.search(text)
        ):
            continue
        named = {full for key, full in leaves.items() if key and key in text}
        for full in named:
            counts[full] = counts.get(full, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1])[:MAX_WANTS])


def merge_hot(hot: Iterable[str], wants: Mapping[str, int]) -> tuple[str, ...]:
    """Put categories asked at least twice first, then the scout's own."""
    asked = [c for c, n in wants.items() if n >= WANTED_MIN_POSTS]
    merged = list(dict.fromkeys([*asked, *hot]))
    return tuple(merged[:MAX_HOT])


def dump_compact(value: object, limit: int) -> str:
    """JSON for a prompt, bounded."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:limit]
