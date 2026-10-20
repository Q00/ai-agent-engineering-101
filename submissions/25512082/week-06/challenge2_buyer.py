"""Challenge 2 buyer: discover many shops and buy a list within one budget."""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import httpx

from buyer import BuyerClient, BuyerError, BuyerOutcome, validate_url
from harness import HarnessError, SCHEMA_DIR, run_decision


MAX_SHOP_CANDIDATES = 6


class Challenge2Error(RuntimeError):
    pass


@dataclass(frozen=True)
class ShopCard:
    url: str
    name: str
    skills_text: str


@dataclass(frozen=True)
class PurchasePlan:
    item: str
    shop_url: str
    budget: int


@dataclass(frozen=True)
class NegotiationSummary:
    item: str
    shop_url: str
    budget: int
    outcome: str
    task_id: str | None
    price: int | None


@dataclass(frozen=True)
class ShoppingResult:
    success: bool
    total_budget: int
    total_spent: int
    purchases: tuple[NegotiationSummary, ...]


def read_directory(path: Path) -> tuple[str, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    shops = raw.get("shops") if isinstance(raw, dict) else None
    if not isinstance(shops, list) or not shops:
        raise Challenge2Error("directory must contain a non-empty shops list")
    urls = tuple(validate_url(value) for value in shops if isinstance(value, str))
    if len(urls) != len(shops) or len(set(urls)) != len(urls):
        raise Challenge2Error("shop URLs must be unique valid strings")
    return urls


def _skills_text(card: dict) -> str:
    skills = card.get("skills")
    if not isinstance(skills, list):
        raise Challenge2Error("Agent Card has no skills list")
    pieces = []
    for skill in skills:
        if isinstance(skill, dict):
            pieces.extend(str(skill.get(key, "")) for key in ("name", "description", "tags", "examples"))
    return " ".join(pieces).lower()


def fetch_cards(urls: tuple[str, ...], client: httpx.Client) -> tuple[ShopCard, ...]:
    cards = []
    for url in urls:
        try:
            response = client.get(f"{url}/.well-known/agent-card.json")
            response.raise_for_status()
            card = response.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            raise Challenge2Error("failed to read an Agent Card") from exc
        if not isinstance(card, dict) or not isinstance(card.get("name"), str):
            raise Challenge2Error("invalid Agent Card")
        cards.append(ShopCard(url, card["name"], _skills_text(card)))
    return tuple(cards)


def candidates_for(item: str, cards: tuple[ShopCard, ...]) -> tuple[ShopCard, ...]:
    terms = {term for term in item.lower().split() if term}
    scored = [
        (sum(term in card.skills_text for term in terms), index, card)
        for index, card in enumerate(cards)
    ]
    matches = [card for score, _, card in sorted(scored, key=lambda row: (-row[0], row[1])) if score]
    return tuple(matches[:MAX_SHOP_CANDIDATES])


def decide_shop(item: str, budget: int, candidates: tuple[ShopCard, ...]) -> str:
    if not candidates:
        raise Challenge2Error(f"no shop candidates for {item}")
    payload = {
        "item": item,
        "allocated_budget": budget,
        "shops": [asdict(card) for card in candidates],
    }
    prompt = (
        "Choose one shop for this shopping-list item from the supplied Agent Card summaries. "
        "Do not use tools. Return only the required JSON and copy selected_url exactly.\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    decision = run_decision(prompt, SCHEMA_DIR / "shop_selection.json")
    allowed = {card.url for card in candidates}
    if decision["selected_url"] not in allowed:
        raise Challenge2Error("model selected a shop outside the candidates")
    return decision["selected_url"]


def allocate_budget(items: tuple[str, ...], total_budget: int) -> tuple[int, ...]:
    if total_budget < len(items):
        raise Challenge2Error("total budget cannot allocate at least one unit per item")
    base, remainder = divmod(total_budget, len(items))
    return tuple(base + (1 if index < remainder else 0) for index in range(len(items)))


class MultiShopBuyer:
    def __init__(
        self,
        cards: tuple[ShopCard, ...],
        buyer_factory: Callable[[str], BuyerClient] = BuyerClient,
        shop_decider: Callable[[str, int, tuple[ShopCard, ...]], str] = decide_shop,
    ) -> None:
        self.cards = cards
        self.buyer_factory = buyer_factory
        self.shop_decider = shop_decider

    def _negotiate(self, plan: PurchasePlan) -> NegotiationSummary:
        with self.buyer_factory(plan.shop_url) as client:
            outcome: BuyerOutcome = client.buy(plan.item, plan.budget)
        return NegotiationSummary(
            plan.item, plan.shop_url, plan.budget,
            outcome.outcome, outcome.task_id, outcome.price,
        )

    def shop(self, items: tuple[str, ...], total_budget: int) -> ShoppingResult:
        normalized = tuple(item.strip().lower() for item in items if item.strip())
        if not normalized or len(set(normalized)) != len(normalized):
            raise Challenge2Error("shopping-list items must be non-empty and unique")
        budgets = allocate_budget(normalized, total_budget)
        plans = []
        for item, budget in zip(normalized, budgets):
            candidates = candidates_for(item, self.cards)
            plans.append(PurchasePlan(item, self.shop_decider(item, budget, candidates), budget))

        summaries = []
        with ThreadPoolExecutor(max_workers=min(len(plans), 8)) as pool:
            futures = {pool.submit(self._negotiate, plan): plan.item for plan in plans}
            for future in as_completed(futures):
                summaries.append(future.result())
        summaries.sort(key=lambda summary: normalized.index(summary.item))
        deals = [summary for summary in summaries if summary.outcome == "deal"]
        purchased_items = [summary.item for summary in deals]
        total_spent = sum(summary.price or 0 for summary in deals)
        valid_prices = all(
            summary.price is not None and 0 <= summary.price <= summary.budget
            for summary in deals
        )
        success = (
            len(deals) == len(normalized)
            and len(set(purchased_items)) == len(normalized)
            and total_spent <= total_budget
            and valid_prices
        )
        return ShoppingResult(success, total_budget, total_spent, tuple(summaries))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Challenge 2 multi-shop buyer")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--items", nargs="+", required=True)
    parser.add_argument("--budget", type=int, required=True)
    args = parser.parse_args(argv)
    if args.budget <= 0:
        parser.error("--budget must be positive")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        urls = read_directory(args.directory)
        with httpx.Client(timeout=20) as http:
            cards = fetch_cards(urls, http)
        result = MultiShopBuyer(cards).shop(tuple(args.items), args.budget)
        print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
        return 0 if result.success else 2
    except (Challenge2Error, BuyerError, HarnessError, httpx.HTTPError):
        print(json.dumps({"success": False, "error": "challenge2_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
