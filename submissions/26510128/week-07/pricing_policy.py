"""Auditable Week 07 seller price adaptation using real market performance.

The public market board supplies counted deals, review count, average stars
and share where available. Read-only GET requests; never submit orders,
forge reviews, create accounts or alter market results.

Opening bids are genuine seller offers. The floor is NEVER lowered. Signed
local deals are retained for diagnostics but are NOT treated as market-
verified deals (tests also generate local receipts).
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parent
SALES = ROOT / ".market" / "seller-sales.jsonl"
REVIEWS = ROOT / ".market" / "seller-reviews.json"
MARKET_BASE = os.getenv("MARKET_URL", "https://market.ourolabs.ai").rstrip("/")

# One cached result per running seller; avoids querying the market on each A2A turn.
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
CACHE_SECONDS = 90


def record_sale(item: str, price: int, reserve: int, task: str) -> None:
    """Local signed receipts, including tests; NOT market-confirmed deals."""
    SALES.parent.mkdir(exist_ok=True, parents=True)
    entry = {"at": datetime.now(timezone.utc).isoformat(), "item": item,
             "price": int(price), "reserve": int(reserve), "task": str(task),
             "verification": "local signed receipt only"}
    with SALES.open("a", encoding="utf-8") as out:
        out.write(json.dumps(entry, ensure_ascii=False) + "\n")


def local_sales(item: str) -> list[dict[str, Any]]:
    if not SALES.exists():
        return []
    records = []
    for line in SALES.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            if record.get("item") == item and isinstance(record.get("price"), int):
                records.append(record)
        except json.JSONDecodeError:
            continue
    return records


def market_reviews(shop: str) -> list[dict[str, Any]]:
    """Optional genuine reviews exported from dashboard by the shop owner."""
    try:
        data = json.loads(REVIEWS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if isinstance(data, dict) and data.get("shop") == shop:
        return [x for x in data.get("reviews", []) if isinstance(x, dict)
                and isinstance(x.get("stars"), int) and 1 <= x["stars"] <= 5]
    return []


def _number(value: Any) -> float | None:
    try:
        return float(str(value).strip().replace("%", "")) if value is not None else None
    except (TypeError, ValueError):
        return None


def _board_row(shop: str, round_id: int) -> dict[str, Any] | None:
    """Only read the official public market board (no authentication tokens)."""
    url = f"{MARKET_BASE}/api/board?round={round_id}"
    with urllib.request.urlopen(url, timeout=3) as r:
        data = json.load(r)
    shops = data.get("shops", [])
    if not isinstance(shops, list):
        return None
    return next((s for s in shops if isinstance(s, dict)
                 and str(s.get("name", "")).casefold() == shop.casefold()), None)


def get_market_feedback(shop: str) -> dict[str, Any]:
    now = time.monotonic()
    cached = _CACHE.get(shop)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]

    record: dict[str, Any] = {"source": "unavailable", "round2": None, "round1": None}
    for rid in (2, 1):
        try:
            row = _board_row(shop, rid)
        except Exception as exc:
            record[f"round{rid}_error"] = type(exc).__name__
            continue
        if row is None:
            continue
        deals = _number(row.get("deals"))
        count = _number(row.get("reviews"))
        stars = _number(row.get("stars"))
        share = row.get("share")
        record[f"round{rid}"] = {
            "counted_deals": int(deals) if deals is not None else None,
            "review_count": int(count) if count is not None else None,
            "mean_stars": round(stars, 2) if stars is not None and 0 <= stars <= 5 else None,
            "share": share if isinstance(share, (int, float, str)) else None,
        }
        record["source"] = "official-public-market-board"
    _CACHE[shop] = (now, record)
    return record


def opening_quote(shop: str, item: str, reserve: int, model_ask: int) -> tuple[int, dict[str, Any]]:
    """Use real trade/review feedback to adapt opening quotes above the reserve.

    Cold-start marketing policy: small honest margins while Round 2 has
    zero counted sales. When real positive reviews accumulate, cautiously
    test a slightly higher quote. Negative reviews pull prices back.
    """
    stats = get_market_feedback(shop)
    r2 = stats.get("round2") or {}
    r1 = stats.get("round1") or {}
    real_reviews = market_reviews(shop)
    # Prefer current-round public reviews; fall back to previous round's
    # public rating or genuine review export. Never invent review counts.
    review_count = r2.get("review_count")
    rating = r2.get("mean_stars")
    if not review_count and r1.get("review_count"):
        review_count = r1["review_count"]
        rating = r1.get("mean_stars")
    if not review_count and real_reviews:
        review_count = len(real_reviews)
        rating = mean(x["stars"] for x in real_reviews)

    counted = r2.get("counted_deals")
    if counted is None:
        # If market is unreachable, cautious introductory price;
        # local/test receipts do not justify a price rise.
        margin, cause = 5, "market-unavailable: introductory margin"
    elif counted == 0:
        margin, cause = 0, "round-2 cold start: no counted deals"
    elif review_count is not None and review_count >= 2 and rating is not None and rating < 3:
        margin, cause = 0, "poor real ratings: reduce opening margin"
    elif counted >= 3 and review_count is not None and review_count >= 2 and rating is not None and rating >= 4.3:
        margin, cause = 10, "repeat sales and positive real ratings"
    else:
        margin, cause = 5, "market sales exist: modest opening margin"

    if any("overcharg" in str(x.get("review", "")).lower() for x in real_reviews[-10:]):
        margin, cause = 0, "actual feedback reports overcharging"

    quoted = max(int(reserve), min(int(model_ask), int(reserve) + margin))
    sales = local_sales(item)
    evidence = {
        "item": item,
        "reserve_enforced": int(reserve),
        "llm_ask_before_policy": int(model_ask),
        "final_ask": quoted,
        "margin": quoted - int(reserve),
        "cause": cause,
        "market": stats,
        "local_signed_receipts_including_tests": len(sales),
        "local_receipts_are_not_market_counted_deals": True,
    }
    return quoted, evidence
