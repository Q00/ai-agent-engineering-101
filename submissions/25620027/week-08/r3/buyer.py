"""Round 3 buyer: one order in seconds, chosen from evidence, bounded by policy.

The market hands a buyer a new order about every 20 seconds, so the per-order path
is deterministic: code ranks the shops that actually stock an acceptable SKU by the
surplus they can give (list price of the SKU minus what we expect to pay) and by what
happened with them before (our own payments and settlements), negotiates in two or
three turns, and prints the receipt with an ``accept`` policy the kit enforces before
paying. The model shapes this behaviour only through the policy table.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import anyio
import httpx2
from pydantic import ValidationError

from http_client import create_client
from market_a2a.models import HarnessError
from market_a2a.remote import Remote, Task
from market_a2a.security import SecurityEvents
from r3.events import EventLedger, events_path
from r3.market_files import (
    Order,
    ShopEntry,
    market_dir,
    read_catalog,
    read_order,
    read_payments,
    read_shops,
)
from r3.policy import BuyerPolicy, load_policy, policy_path

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from r3.market_files import CatalogItem

MAX_STARS: Final = 5
NUMBER: Final = re.compile(r"(?<![0-9A-Za-z])([0-9]{2,9})(?![0-9A-Za-z])")


@dataclass(frozen=True, slots=True)
class Choice:
    """The shop and SKU we will ask for, with the value we expect to get."""

    shop: ShopEntry
    sku: str
    name: str
    list_price: int
    request: str
    score: float


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


def review_factor(shop: ShopEntry) -> float:
    """Weigh a light prior from public stars; our own evidence dominates."""
    stars = [r.stars for r in shop.reviews if 1 <= r.stars <= MAX_STARS]
    if not stars:
        return 1.0
    return 0.9 + 0.05 * (sum(stars) / len(stars) - 3.0)


def choose(  # noqa: PLR0913 - ranking needs every evidence source
    order: Order,
    shops: tuple[ShopEntry, ...],
    catalog: dict[str, CatalogItem],
    outcomes: dict[str, dict[str, int]],
    reasons: dict[str, str],
    *,
    blocked: frozenset[str] = frozenset(),
) -> Choice | None:
    """Pick the shop and SKU with the best expected surplus; None when no stock fits."""
    acceptable = set(order.acceptable)
    best: Choice | None = None
    for shop in shops:
        if shop.name in blocked or shop.url.rstrip("/") in blocked:
            continue
        weight = shop_factor(shop.url, outcomes, reasons) * review_factor(shop)
        candidates = [
            o
            for o in shop.offers
            if o.qty > 0 and (not acceptable or o.sku in acceptable)
        ]
        for offer in candidates:
            item = catalog.get(offer.sku)
            list_price = item.list_price if item is not None else 0
            name = (item.name if item is not None else "") or offer.name or offer.sku
            score = (list_price if list_price > 0 else 1) * weight
            if best is None or score > best.score:
                best = Choice(
                    shop,
                    offer.sku,
                    name,
                    list_price,
                    f"I want to buy {name} ({offer.sku})",
                    score,
                )
    if best is None and not acceptable and shops:
        # Rounds 0-2: no stock lists; choose by evidence and ask for the item itself.
        ranked = sorted(
            (s for s in shops if s.name not in blocked),
            key=lambda s: -(shop_factor(s.url, outcomes, reasons) * review_factor(s)),
        )
        if ranked:
            shop = ranked[0]
            best = Choice(
                shop, order.item, order.item, 0, f"I want to buy {order.item}", 1.0
            )
    return best


def seller_ask(task: Task) -> int | None:
    """Read the seller's ask from metadata or text; None when it named none."""
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
    return int(numbers[0]) if len(numbers) == 1 else None


def receipt_from(task: Task) -> dict[str, object] | None:
    """Return the data part the seller attached; the kit verifies its signature."""
    receipts = [
        part.data
        for artifact in task.artifacts
        for part in artifact.parts
        if part.data is not None and "price" in part.data
    ]
    return dict(receipts[-1]) if receipts else None


def accept_policy(order: Order, policy: BuyerPolicy) -> dict[str, object] | None:
    """Build what the checkout must show before the kit pays; None before Round 3."""
    if not order.acceptable:
        return None
    accept: dict[str, object] = {}
    if policy.require_cert:
        accept["cert"] = True
    if order.kind == "food" and order.use_within_hours is not None:
        accept["min_hours_left"] = round(
            float(order.use_within_hours) + policy.food_margin_hours, 2
        )
    if order.kind == "goods":
        accept["grades"] = list(policy.goods_grades)
        if policy.goods_warranty_hours > 0:
            accept["warranty_hours"] = policy.goods_warranty_hours
    return accept


async def negotiate(  # noqa: PLR0913 - one negotiation, explicit inputs
    remote: Remote,
    endpoint: str,
    choice: Choice,
    budget: int,
    *,
    policy: BuyerPolicy,
    emit: Callable[[str], None],
) -> tuple[Task, int | None]:
    """Two to four turns: open, read the ask, accept or counter, never above cap."""
    cap = (
        budget
        if choice.list_price <= 0
        else min(budget, int(choice.list_price * policy.max_price_ratio))
    )
    if cap < 1:
        message = "No affordable price under this order's cap"
        raise HarnessError(message)
    task = await remote.send(endpoint, choice.request)
    my_offer: int | None = None
    for round_number in range(policy.max_rounds + 1):
        state = task.status.state
        if state == "TASK_STATE_COMPLETED":
            return task, my_offer
        if state != "TASK_STATE_INPUT_REQUIRED":
            return task, None
        ask = seller_ask(task)
        reference = choice.list_price if choice.list_price > 0 else budget
        quick = int(reference * policy.quick_accept_ratio)
        last_round = round_number >= policy.max_rounds - 1
        if ask is not None and ask <= cap and (ask <= quick or last_round):
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
            if my_offer is not None and offer == my_offer:
                return task, None
            my_offer = offer
        emit(
            f"[R3 BUYER] round={round_number + 1} ask={ask} offer={my_offer} cap={cap}"
        )
        task = await remote.send(endpoint, str(my_offer), task)
    return task, my_offer if task.status.state == "TASK_STATE_COMPLETED" else None


def valid_receipt(  # noqa: PLR0911 - each check names its reason
    receipt: dict[str, object],
    order: Order,
    choice: Choice,
    price: int | None,
) -> str | None:
    """Return why a receipt cannot be used, or None when it matches the deal."""
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
    shops = read_shops(shops_path)
    catalog = read_catalog(state)
    events: EventLedger | None = None
    outcomes: dict[str, dict[str, int]] = {}
    reasons: dict[str, str] = {}
    try:
        events = EventLedger(
            events_file if events_file is not None else events_path(state)
        )
        _ = events.sync_payments(read_payments(state))
        outcomes = events.shop_outcomes(policy.buyer.refusal_memory_hours)
        reasons = events.refusal_reasons(policy.buyer.refusal_memory_hours)
    except OSError:
        emit("[R3 BUYER] events ledger unavailable; choosing without own history")
    choice = choose(order, shops, catalog, outcomes, reasons, blocked=blocked)
    if choice is None:
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
    )
    security = SecurityEvents()
    reason, price, receipt, task_state = "", None, None, "none"
    try:
        with anyio.fail_after(policy.buyer.order_seconds):
            async with create_client() as client:
                remote = Remote(client, emit, security)
                card = await remote.card(choice.shop.url)
                task, price = await negotiate(
                    remote,
                    card.endpoint(),
                    choice,
                    order.budget,
                    policy=policy.buyer,
                    emit=emit,
                )
                task_state = task.status.state
                if task_state == "TASK_STATE_COMPLETED":
                    receipt = receipt_from(task)
                    if receipt is None:
                        reason = "completed without a receipt"
                    else:
                        agreed = (
                            price
                            if price is not None
                            else int(float(str(receipt.get("price", 0))))
                        )
                        problem = valid_receipt(receipt, order, choice, agreed)
                        if problem is not None:
                            reason, receipt = problem, None
                        else:
                            price = agreed
                else:
                    reason = f"{task_state}: {task.observation()[:120]}"
    except TimeoutError:
        reason = "timed out"
    except (HarnessError, httpx2.HTTPError, ValidationError) as exc:
        reason = f"{type(exc).__name__}: {exc}"[:200]
    if security.snapshot().operator_directive_observed:
        reason, receipt = "operator directive observed in seller text", None
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
        outcome = "no_deal"
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
            )
        except OSError:
            emit("[R3 BUYER] events write failed")
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
