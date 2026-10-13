"""Typed challenge fixtures and independent outcome checks."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from pydantic import Field, TypeAdapter

from market_a2a.models import FrozenModel, HarnessError, Product, Purchase, ShoppingItem
from market_a2a.remote import Task, Wire

if TYPE_CHECKING:
    from pathlib import Path


SHOP_COUNT: Final = 20
MIN_SHOPS: Final = 2


class Case(FrozenModel):
    """Private challenge oracle, never supplied to a model."""

    case_id: str
    request: str
    expected_sku: str


class ExpectedItem(ShoppingItem):
    """Shopping request with a test-only SKU oracle."""

    expected_sku: str


class ShoppingFixture(FrozenModel):
    """Private fixture parsed before producing the public buyer input."""

    budget: int = Field(ge=1)
    items: tuple[ExpectedItem, ...]


class BuyerInput(FrozenModel):
    """Descriptions and total budget, without the answer key."""

    budget: int
    items: tuple[ShoppingItem, ...]


class Ledger(FrozenModel):
    """An actual emitted budget transition."""

    request_id: str
    spent: int = Field(ge=0)
    released: int = Field(ge=0)
    reserved: int = Field(ge=0)
    total_budget: int = Field(ge=1)


class HttpObservation(Wire):
    """Sanitized request headers observed on the actual response request."""

    method: str
    url: str
    version: str


class MessageReference(Wire):
    """Task correlation on an outbound A2A message."""

    task_id: str = Field(default="", alias="taskId")
    context_id: str = Field(default="", alias="contextId")


class Parameters(Wire):
    """SendMessage parameters."""

    message: MessageReference


class Request(Wire):
    """Actual wire request fields used for identity auditing."""

    params: Parameters


class CaseResult(FrozenModel):
    """Observed selected identity, quote floor and final receipt."""

    case_id: str
    expected_sku: str
    reserve: int
    quotes: tuple[int, ...]
    receipt: Purchase


class Summary(FrozenModel):
    """One immutable attempt verdict with concrete observations."""

    passed: bool = Field(serialization_alias="pass")
    challenge: int
    cases: tuple[CaseResult, ...] = ()
    receipts: tuple[Purchase, ...] = ()
    directory_count: int = 0
    total: int = 0
    budget: int | None = None
    correlations: tuple[str, ...] = ()
    header_count: int = 0
    state_count: int = 0
    ledger: tuple[Ledger, ...] = ()
    error: str = ""


def require(condition: bool, message: str) -> None:  # noqa: FBT001 — assertion-style evaluator
    """Fail the attempt instead of turning an invalid result into a pass."""
    if not condition:
        raise HarnessError(message)


def events(log: str, marker: str) -> tuple[str, ...]:
    """Extract complete JSON event lines from captured raw console output."""
    return tuple(
        line.split(marker, 1)[1] for line in log.splitlines() if marker in line
    )


def quote(task: Task, product: Product) -> int:
    """Check the public selected identity and every asking price independently."""
    require(task.status.state == "TASK_STATE_INPUT_REQUIRED", "Expected a quote")
    identity = task.status.message.metadata
    require(identity.sku == product.sku, "Seller selected the wrong product")
    numbers = TypeAdapter(list[str]).validate_python(
        re.findall(r"\d+", task.observation())
    )
    require(bool(numbers), "Quoted message contains no price")
    price = int(numbers[-1])
    require(price >= product.reserve, "Seller quoted below selected reserve")
    return price


def receipt(task: Task, product: Product, shop: str) -> Purchase:
    """Verify the final artifact without manufacturing seller decisions."""
    require(task.status.state == "TASK_STATE_COMPLETED", "Purchase did not complete")
    parts = [part.data for art in task.artifacts for part in art.parts if part.data]
    require(len(parts) == 1, "Expected exactly one receipt")
    sold = parts[0]
    require(sold.sku == product.sku, "Receipt SKU differs from expected product")
    require(sold.item == product.kind, "Receipt item differs from product kind")
    require(sold.price >= product.reserve, "Receipt price is below reserve")
    return Purchase(
        completed=True,
        item=sold.item,
        sku=sold.sku,
        price=sold.price,
        task_id=task.id,
        shop=shop,
    )


def shopping_summary(log: str, fixture: ShoppingFixture, root: Path) -> Summary:
    """Audit receipts, reservations, discovery and live task correlations."""
    raw_results = events(log, "SHOPPING_RESULT ")
    require(len(raw_results) == 1, "Expected one SHOPPING_RESULT")
    purchases = TypeAdapter(tuple[Purchase, ...]).validate_json(raw_results[0])
    products = TypeAdapter(tuple[Product, ...]).validate_json(
        (root / "catalog.json").read_bytes()
    )
    catalog = {product.sku: product for product in products}
    require(len(purchases) == len(fixture.items), "Shopping item count differs")
    for bought, expected in zip(purchases, fixture.items, strict=True):
        require(bought.completed, "Shopping contains an incomplete purchase")
        require(bought.sku == expected.expected_sku, "Shopping bought wrong SKU")
        shop_match = re.search(r"/shops/(\d{2})/$", bought.shop)
        if shop_match is None:
            message = "Receipt shop is outside directory"
            raise HarnessError(message)
        index = int(shop_match[1])
        require(0 <= index < SHOP_COUNT, "Invalid shop index")
        product = catalog[expected.expected_sku]
        kinds = ("lamp", "chair", "bookshelf", "desk", "storage")
        require(product.kind == kinds[index % len(kinds)], "Wrong shop category")
        require(bought.price is not None, "Missing receipt price")
        require(
            (bought.price or 0) >= product.reserve + index * 5, "Below shop reserve"
        )
    ids = {p.task_id for p in purchases}
    require(len(ids) == len(purchases) and "" not in ids, "Duplicate/missing tasks")
    require(len({p.sku for p in purchases}) == len(purchases), "Duplicate SKU")
    require(len({p.shop for p in purchases}) >= MIN_SHOPS, "Expected several shops")
    total = sum(p.price or 0 for p in purchases)
    require(total <= fixture.budget, "Shopping exceeded total budget")
    ledger = tuple(
        Ledger.model_validate_json(raw) for raw in events(log, "[BUDGET LEDGER] ")
    )
    require(len(ledger) == len(purchases), "Missing budget transitions")
    require(ledger[-1].reserved == 0 and ledger[-1].spent == total, "Unsettled ledger")
    require(
        {e.request_id for e in ledger} == {e.request_id for e in fixture.items},
        "Ledger IDs differ",
    )
    require(
        all(
            e.spent + e.reserved <= fixture.budget and e.total_budget == fixture.budget
            for e in ledger
        ),
        "Invalid ledger budget",
    )
    requests = tuple(
        Request.model_validate_json(raw) for raw in events(log, "[A2A REQUEST] ")
    )
    correlations = {
        r.params.message.task_id
        for r in requests
        if r.params.message.task_id and r.params.message.context_id
    }
    require(ids <= correlations, "Missing follow-up task/context correlation")
    headers = tuple(
        HttpObservation.model_validate_json(raw) for raw in events(log, "[A2A HTTP] ")
    )
    require(
        bool(headers) and all(h.version == "1.0" for h in headers),
        "Missing/invalid A2A headers",
    )
    require(
        sum(h.method == "POST" for h in headers) == len(requests),
        "Missing POST header observations",
    )
    states = events(log, "[NEGOTIATION SUMMARY] ")
    require(len(states) >= len(purchases), "Missing negotiation summaries")
    return Summary(
        passed=True,
        challenge=2,
        receipts=purchases,
        directory_count=20,
        total=total,
        budget=fixture.budget,
        correlations=tuple(sorted(correlations)),
        header_count=len(headers),
        state_count=len(states),
        ledger=ledger,
    )
