"""Choose a reviewed shop, negotiate through A2A, and remember the result."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from buyer import BuyerClient, BuyerError, BuyerOutcome, validate_url


class MarketBuyerError(RuntimeError):
    """Raised for invalid market input or local state."""


@dataclass(frozen=True)
class Shop:
    name: str
    url: str
    reviews: tuple[dict, ...]


@dataclass(frozen=True)
class ShopChoice:
    shop: Shop
    score: float
    reason: str


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Choose and trade with a reviewed A2A shop")
    parser.add_argument("--shops", type=Path, required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", type=int, required=True)
    args = parser.parse_args(argv)
    if args.budget <= 0:
        parser.error("--budget must be a positive integer")
    return args


def load_shops(path: Path) -> tuple[Shop, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MarketBuyerError("shop list is unavailable or invalid") from exc
    if not isinstance(raw, list) or not raw:
        raise MarketBuyerError("shop list must be a non-empty JSON array")
    shops = []
    names = set()
    for row in raw:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise MarketBuyerError("shop list contains an invalid entry")
        name = row["name"].strip()
        if not name or name in names:
            raise MarketBuyerError("shop names must be non-empty and unique")
        reviews = row.get("reviews", [])
        if not isinstance(reviews, list) or any(not isinstance(review, dict) for review in reviews):
            raise MarketBuyerError("shop reviews must be JSON objects")
        shops.append(Shop(name, validate_url(str(row.get("url", ""))), tuple(reviews)))
        names.add(name)
    return tuple(shops)


def state_dir_for(shops_path: Path) -> Path:
    override = os.environ.get("AX_STATE_DIR")
    if override:
        return Path(override)
    if shops_path.name.startswith("test-shops-"):
        return Path(".market/test-state")
    return Path(".market/runtime-state")


def load_memory(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "shops": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MarketBuyerError("buyer memory is unavailable or invalid") from exc
    if not isinstance(value, dict) or not isinstance(value.get("shops"), dict):
        raise MarketBuyerError("buyer memory has an invalid format")
    return value


def save_memory(path: Path, memory: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(memory, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _review_summary(reviews: tuple[dict, ...]) -> tuple[float, int, str]:
    stars = [review.get("stars") for review in reviews]
    valid_stars = [value for value in stars if isinstance(value, int) and 1 <= value <= 5]
    average = sum(valid_stars) / len(valid_stars) if valid_stars else 3.0
    evidence_terms = ("overcharg", "receipt", "did not match", "higher than", "avoid")
    suspicious = sum(
        any(term in str(review.get("review", "")).lower() for term in evidence_terms)
        and isinstance(review.get("stars"), int) and review["stars"] <= 2
        for review in reviews
    )
    summary = f"{len(valid_stars)} review(s), average {average:.2f}, integrity warnings {suspicious}"
    return average, suspicious, summary


def choose_shop(shops: tuple[Shop, ...], memory: dict) -> ShopChoice:
    rows = []
    remembered = memory.get("shops", {})
    for index, shop in enumerate(shops):
        average, suspicious, review_summary = _review_summary(shop.reviews)
        history = remembered.get(shop.name, {}) if isinstance(remembered, dict) else {}
        deals = int(history.get("deals", 0)) if isinstance(history, dict) else 0
        no_deals = int(history.get("no_deals", 0)) if isinstance(history, dict) else 0
        mismatches = int(history.get("receipt_mismatches", 0)) if isinstance(history, dict) else 0
        score = average * 10 + min(deals, 5) * 2 - min(no_deals, 5) - suspicious * 12 - mismatches * 25
        reason = (
            f"reviews: {review_summary}; memory: deals {deals}, no-deals {no_deals}, "
            f"receipt mismatches {mismatches}; score {score:.2f}"
        )
        rows.append((score, -index, shop.name, shop, reason))
    score, _, _, shop, reason = max(rows)
    return ShopChoice(shop, score, reason)


def _review_for(outcome: BuyerOutcome, final_offer: int | None) -> tuple[int, str, bool]:
    if outcome.outcome == "deal" and outcome.receipt is not None:
        mismatch = final_offer is not None and outcome.price != final_offer
        if mismatch:
            return 1, "Receipt price did not match the final offer.", True
        return 5, "Deal completed within budget and the receipt matched the final offer.", False
    return 2, f"No deal: {outcome.outcome}.", False


def remember(memory: dict, shop: Shop, outcome: BuyerOutcome, mismatch: bool) -> None:
    shops = memory.setdefault("shops", {})
    row = shops.setdefault(shop.name, {"deals": 0, "no_deals": 0, "receipt_mismatches": 0})
    if outcome.outcome == "deal":
        row["deals"] = int(row.get("deals", 0)) + 1
    else:
        row["no_deals"] = int(row.get("no_deals", 0)) + 1
    if mismatch:
        row["receipt_mismatches"] = int(row.get("receipt_mismatches", 0)) + 1
    row["last_outcome"] = outcome.outcome
    row["last_price"] = outcome.price


def run(
    shops_path: Path,
    item: str,
    budget: int,
    buyer_factory: Callable[[str], BuyerClient] = BuyerClient,
) -> dict:
    shops = load_shops(shops_path)
    state_dir = state_dir_for(shops_path)
    memory_path = state_dir / "buyer-memory.json"
    memory = load_memory(memory_path)
    choice = choose_shop(shops, memory)
    print(f"[choose] {choice.shop.name} ({choice.shop.url}): {choice.reason}")
    try:
        with buyer_factory(choice.shop.url) as client:
            outcome = client.buy(item, budget)
            final_offer = client.last_offer
        stars, review, mismatch = _review_for(outcome, final_offer)
    except (BuyerError, httpx.HTTPError):
        outcome = BuyerOutcome("failed")
        stars, review, mismatch = 1, "Trade failed safely before a receipt was received.", False
    remember(memory, choice.shop, outcome, mismatch)
    save_memory(memory_path, memory)
    return {
        "shop": choice.shop.url,
        "receipt": outcome.receipt,
        "stars": stars,
        "review": review,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run(args.shops, args.item, args.budget)
    except (MarketBuyerError, BuyerError, httpx.HTTPError) as exc:
        print(f"market buyer failed safely: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
