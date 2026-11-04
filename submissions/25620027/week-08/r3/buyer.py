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
import json
import random
import re
import sqlite3
import statistics
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
from r3.guard import suspicious
from r3.interpreter import read_seller_message
from r3.market_files import (
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

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from r3.market_files import CatalogItem

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


SHELF_LEFT: Final = re.compile(r"([0-9]+(?:\.[0-9]+)?) h of shelf life left")
# Only a statement about this unit counts: "certified grade B" or "grade B now",
# not a card's "grade A goods carry a warranty". The kit's reason: "grade C, ...".
STATED_GRADE: Final = re.compile(r"certified grade ([ABC])\b|\bgrade ([ABC]) now\b")
REFUSED_GRADE: Final = re.compile(r"\bgrade ([ABC]),")
# A remembered unit is assumed still on that shop's shelf for this long.
UNFIT_MEMORY_HOURS: Final = 6.0


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
    """Return the certified grade the seller's reply states, if it states one."""
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


# A food order the shop's unit cannot serve is found in its first reply (the
# market certificate carries expires_at) instead of at payment: 154 agreed deals
# on 11/4 were negotiated to the end and then refused for shelf life. With this
# share of the order's seconds still left, the next-best shop gets the order.
RETRY_BEFORE_SHARE: Final = 0.5
# Expiry is wall-clock time; negotiation timing uses an injectable monotonic clock.
WALL_CLOCK: Final = time.time


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
    return None


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


def choose(  # noqa: PLR0913, C901 - ranking needs every evidence source
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
) -> Choice | None:
    """Pick the shop and SKU with the best expected surplus; None when no stock fits.

    The scout's trust score (from reviews and forum text the model read) weighs a
    shop between 0.6 and 1.2; our own payment evidence still dominates, and our own
    past prices move it by up to 20 %. With probability ``explore`` the order goes
    to a random shop among the unpenalized candidates instead (e-greedy), so a
    cheaper or newer shop can be found; penalized shops are never explored.
    """
    acceptable = set(order.acceptable)
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
        weight = evidence * price_factor(shop.url, ratios or {})
        eligible = exploration_allowed(shop, explore_counts, reasons, picture)
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
            score = (list_price if list_price > 0 else 1) * weight
            choice = Choice(
                shop,
                offer.sku,
                name,
                list_price,
                f"I want to buy {name} ({offer.sku})",
                score,
            )
            if best is None or score > best.score:
                best = choice
            key = shop.url.rstrip("/")
            kept = by_shop.get(key)
            if eligible and (kept is None or score > kept.score):
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


def check_shelf_life(task: Task, order: Order | None, policy: BuyerPolicy) -> None:
    """Raise when the offered unit fails the order: grade we do not buy, or too old."""
    if order is not None and order.kind == "goods" and order.acceptable:
        grade = stated_grade(task)
        if grade is not None and grade not in policy.goods_grades:
            message = f"grade {grade} offered, we buy {list(policy.goods_grades)}"
            raise UnacceptedGradeError(message)
    if order is None or order.kind != "food" or not order.use_within_hours:
        return
    need = float(order.use_within_hours) + policy.food_margin_hours
    left = stated_hours_left(task, WALL_CLOCK())
    if left is not None and left < need:
        message = f"shelf life {left:.1f} h left, the order needs {need:.1f}"
        raise ShortShelfLifeError(message)


async def negotiate(  # noqa: PLR0913, PLR0912, C901 - one negotiation, explicit inputs
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
    started = clock()
    task = await remote.send(endpoint, choice.request)
    my_offer: int | None = None
    reads = 0
    reply = ""
    for round_number in range(policy.max_rounds + 1):
        state = task.status.state
        if state == "TASK_STATE_COMPLETED":
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
        if ask is None and reads < 1:
            reads += 1
            ask, stop, reply = await prose_ask(reader, task, choice, emit, order)
            if stop:
                return task, None
        reference = choice.list_price if choice.list_price > 0 else budget
        quick = int(reference * policy.quick_accept_ratio)
        last_round = round_number >= policy.max_rounds - 1
        late = clock() - started >= policy.order_seconds * policy.late_accept_share
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
            if my_offer is not None and offer == my_offer:
                return task, None
            my_offer = offer
        emit(
            f"[R3 BUYER] round={round_number + 1} ask={ask} offer={my_offer} cap={cap}"
        )
        # A screened, digit-free answer to the seller's question rides in front of
        # the offer; the offer keyword keeps every seller's number parser working.
        message = f"{reply} My offer: {my_offer}" if reply else str(my_offer)
        reply = ""
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
    ratios: dict[str, float] = {}
    recent: dict[str, dict[str, int]] | None = None
    cooling: frozenset[str] = frozenset()
    unfit: list[tuple[float, str, str, str]] = []
    try:
        events = EventLedger(
            events_file if events_file is not None else events_path(state)
        )
        _ = events.sync_payments(read_payments(state))
        outcomes = events.shop_outcomes(policy.buyer.refusal_memory_hours)
        reasons = events.refusal_reasons(policy.buyer.refusal_memory_hours)
        recent = events.shop_outcomes(policy.buyer.explore_memory_hours)
        ratios = events.price_ratios(policy.buyer.refusal_memory_hours)
        unfit = events.unfit_offers(UNFIT_MEMORY_HOURS)
        cooling = cooling_shops(
            events.failure_streaks(policy.buyer.refusal_memory_hours),
            policy.buyer,
        )
    except (OSError, sqlite3.Error):
        emit("[R3 BUYER] events ledger unavailable; choosing without own history")
    picture = load_picture(picture_path(state))
    skip_offer = unfit_skipper(unfit, order, policy.buyer, WALL_CLOCK())

    def pick(skip: frozenset[str] = frozenset()) -> Choice | None:
        return choose(
            order,
            shops,
            catalog,
            outcomes,
            reasons,
            blocked=blocked | cooling | skip,
            picture=picture,
            ratios=ratios,
            explore=policy.buyer.explore_rate,
            recent=recent,
            skip_offer=skip_offer,
        )

    choice = pick()
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
        + (" explore=1" if choice.explored else "")
        + (f" cooling={len(cooling)}" if cooling else "")
    )
    security = SecurityEvents()
    reader = ModelClient.from_env(events, gate=TRADE_GATE)
    reason, price, receipt, task_state = "", None, None, "none"
    suspicious = False
    try:
        with anyio.fail_after(policy.buyer.order_seconds):
            async with create_client() as client:
                remote = Remote(client, emit, security)
                skipped: set[str] = set()
                while True:
                    card = await remote.card(choice.shop.url)
                    try:
                        task, price = await negotiate(
                            remote,
                            card.endpoint(),
                            choice,
                            order.budget,
                            policy=policy.buyer,
                            emit=emit,
                            clock=time.monotonic,
                            reader=reader,
                            order=order,
                        )
                    except UnfitUnitError as short:
                        spent = time.monotonic() - started
                        skipped.add(choice.shop.name)
                        retry = (
                            pick(frozenset(skipped))
                            if spent < policy.buyer.order_seconds * RETRY_BEFORE_SHARE
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
                                    reason=f"{type(short).__name__}: {short}",
                                    elapsed=spent,
                                    market_round=3,
                                )
                        choice = retry
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
                        if problem is not None:
                            reason, receipt, price = problem, None, None
                else:
                    reason = f"{task_state}: {task.observation()[:120]}"
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
