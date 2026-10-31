"""Round 3 seller: sells the kit's real stock over A2A in milliseconds, no model.

Every turn reads ``inventory.json`` (the market's view of our units, cost and grade),
prices the unit from the policy table and answers at once. The receipt names the
exact unit, an honest grade claim and the warranty the policy promises, so the kit's
checkout ships what we said. The only model in this agent runs in the slow policy
loop (``r3.manager``); this server never waits on one.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, override

import anyio
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.utils.errors import InvalidParamsError
from fastapi import FastAPI
from google.protobuf.json_format import Parse
from google.protobuf.struct_pb2 import Value

from market_a2a.seller_offer import opening_offer
from r3.events import EventLedger, events_path
from r3.market_files import (
    CatalogItem,
    Inventory,
    Unit,
    category_leaf,
    market_dir,
    read_catalog,
    read_inventory,
)
from r3.policy import SellerPolicy, load_policy, policy_path
from r3.pricing import Quote, claimed_grade, decide, quote

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from a2a.server.events import EventQueue

    from market_a2a.receipts import ReceiptSigner

ECON_ROUND: Final = 3
HOLD_SECONDS: Final = 900.0  # a buyer that stops answering frees its unit
COMMIT_SECONDS: Final = 600.0  # a signed unit stays reserved until the kit ships it
LEGACY_RESERVE: Final = {"lamp": 100, "chair": 180, "bookshelf": 260}
SKU_TOKEN: Final = re.compile(r"\b[A-Za-z][0-9A-Fa-f]{8}\b")
OFFER_WORDS: Final = re.compile(
    r"(?:\bfor|\boffer|\bpay|\bat|\bprice|\bprice is|\bbid)\s*:?\s*\$?([0-9]{2,9})\b",
    re.IGNORECASE,
)
BARE_OFFER: Final = re.compile(
    r"^\s*(?:(?:i offer|i can pay|i will pay|i'll pay|my offer is|offer|ok|okay)\s+)?"
    r"\$?([0-9]{1,9})(?:\.0{1,2})?\s*(?:credits?|won|원)?\s*[.!]?\s*$",
    re.IGNORECASE,
)
STANDALONE_NUMBER: Final = re.compile(r"(?<![0-9A-Za-z.])([0-9]{1,9})(?![0-9A-Za-z])")
ASSENT: Final = re.compile(
    r"^\s*(?:ok|okay|yes|agreed|deal|accept|accepted|i accept|i agree|"
    r"i accept (?:your|the) (?:offer|quote|price|ask))\s*[.!]?\s*$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Deal:
    """One open negotiation; the unit is held for this task while it is open."""

    unit: Unit | None
    legacy_item: str | None
    quote: Quote
    ask: int
    rounds: int = 0
    opened_at: float = field(default_factory=time.time)
    last_offer: int | None = None


@dataclass(frozen=True, slots=True)
class Request:
    """What the buyer asked for in the opening message."""

    skus: tuple[str, ...]
    offer: int | None
    text: str


def parse_request(text: str, known_skus: frozenset[str]) -> Request:
    """Extract named SKUs and an explicit opening offer; never invent either."""
    lowered = text.lower()
    skus = tuple(
        dict.fromkeys(
            token for token in SKU_TOKEN.findall(text) if token.lower() in known_skus
        )
    )
    offer: int | None = None
    matched = OFFER_WORDS.search(text)
    if matched is not None:
        offer = int(matched[1])
    elif (bare := BARE_OFFER.match(text)) is not None:
        offer = int(bare[1])
    return Request(tuple(s for s in skus if s.lower() in lowered), offer, text)


def bargaining_offer(text: str, ask: int) -> int | None:
    """Parse a whole-message number, a ``pay N`` phrase, or assent to the ask."""
    if ASSENT.match(text) is not None:
        return ask
    matched = BARE_OFFER.match(text)
    if matched is not None:
        return int(matched[1])
    worded = OFFER_WORDS.search(text)
    if worded is not None:
        return int(worded[1])
    numbers = STANDALONE_NUMBER.findall(text)
    return int(numbers[0]) if len(numbers) == 1 else None


def legacy_item(text: str) -> str | None:
    """Return the week-07 item a message names; used before Round 3 (no stock)."""
    words = set(re.findall(r"[a-z]+", text.lower()))
    found = [item for item in LEGACY_RESERVE if item in words]
    return found[0] if len(found) == 1 else None


class StockView:
    """Cached, mtime-checked reads of the kit's inventory and catalog files."""

    def __init__(self, folder: Path) -> None:
        """Bind to one ``.market`` folder."""
        self.folder: Path = folder
        self._inventory_mtime: float = -1.0
        self._inventory = read_inventory(folder)
        self._catalog_mtime: float = -1.0
        self._catalog: dict[str, CatalogItem] = {}

    def inventory(self) -> Inventory:
        """Return the latest inventory snapshot."""
        try:
            mtime = (self.folder / "inventory.json").stat().st_mtime
        except OSError:
            return self._inventory
        if mtime != self._inventory_mtime:
            self._inventory = read_inventory(self.folder)
            self._inventory_mtime = mtime
        return self._inventory

    def catalog(self) -> dict[str, CatalogItem]:
        """Return the catalog (sku -> item), reloaded when the kit rewrites it."""
        try:
            mtime = (self.folder / "catalog.json").stat().st_mtime
        except OSError:
            return self._catalog
        if mtime != self._catalog_mtime:
            self._catalog = read_catalog(self.folder)
            self._catalog_mtime = mtime
        return self._catalog

    def shelf_hours(self, sku: str) -> float | None:
        """Shelf life of a food SKU from the catalog, if known."""
        item = self.catalog().get(sku)
        return float(item.shelf_hours) if item and item.shelf_hours else None


def pick_unit(
    units: tuple[Unit, ...], request: Request, now: float, excluded: set[str]
) -> Unit | None:
    """Pick the fitting unit that expires first (goods: oldest), SKU names first."""
    text = request.text.lower()
    available = [
        u
        for u in units
        if u.unit not in excluded
        and u.grade != "expired"
        and (u.expires_at is None or u.expires_at > now + 60)
    ]
    wanted = {s.lower() for s in request.skus}
    fit = [u for u in available if u.sku.lower() in wanted]
    if not fit:
        fit = [u for u in available if u.name and u.name.lower() in text]
    if not fit:
        fit = [
            u
            for u in available
            if category_leaf(u.category).lower() in text and category_leaf(u.category)
        ]
    if not fit:
        return None
    return min(fit, key=lambda u: (u.expires_at or float("inf"), u.supplied_at))


class R3Seller(AgentExecutor):
    """Deterministic A2A seller over the kit's stock, with honest receipts."""

    def __init__(  # noqa: PLR0913 - explicit wiring, no hidden globals
        self,
        name: str,
        *,
        signer: ReceiptSigner | None,
        stock: StockView,
        policy_file: Path,
        events: EventLedger | None,
        emit: Callable[[str], None],
    ) -> None:
        """Keep per-task state in memory; the kit owns the durable ledger."""
        self.name: str = name
        self.signer: ReceiptSigner | None = signer
        self.stock: StockView = stock
        self.policy_file: Path = policy_file
        self.events: EventLedger | None = events
        self.emit: Callable[[str], None] = emit
        self.open: dict[str, Deal] = {}
        self.committed: dict[str, float] = {}
        self.closed: set[str] = set()
        self.locks: dict[str, anyio.Lock] = {}

    def policy(self) -> SellerPolicy:
        """Read the active seller policy (cheap; the file is small)."""
        return load_policy(self.policy_file).seller

    def excluded_units(self, now: float) -> set[str]:
        """Units promised to another open task or just sold and awaiting checkout."""
        for task_id, deal in list(self.open.items()):
            if now - deal.opened_at > HOLD_SECONDS:
                del self.open[task_id]
                self.closed.add(task_id)
        for unit_id, stamp in list(self.committed.items()):
            if now - stamp > COMMIT_SECONDS:
                del self.committed[unit_id]
        held = {d.unit.unit for d in self.open.values() if d.unit is not None}
        return held | set(self.committed)

    @override
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Serialize turns per task; one tool-free decision per message."""
        task = context.current_task
        if task is None:
            if context.task_id is None or context.context_id is None:
                raise InvalidParamsError(message="Task identifiers are required.")
            task = Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                history=[context.message] if context.message is not None else [],
            )
            await event_queue.enqueue_event(task)
        async with self.locks.setdefault(task.id, anyio.Lock()):
            if task.id in self.closed:
                raise InvalidParamsError(message="The negotiation is finished.")
            updater = TaskUpdater(event_queue, task.id, task.context_id)
            await self.turn(task.id, context.get_user_input() or "", updater)

    @override
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Release the held unit."""
        task = context.current_task
        if task is None:
            raise InvalidParamsError(message="Cancellation requires an existing task.")
        async with self.locks.setdefault(task.id, anyio.Lock()):
            _ = self.open.pop(task.id, None)
            self.closed.add(task.id)
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()

    async def turn(self, task_id: str, text: str, updater: TaskUpdater) -> None:
        """Open a negotiation or answer an offer."""
        now = time.time()
        policy = self.policy()
        deal = self.open.get(task_id)
        if deal is None:
            deal = self.opening(task_id, text, now, policy)
            if deal is None:
                self.closed.add(task_id)
                await updater.reject(
                    updater.new_agent_message(
                        [Part(text="Sorry, we have none of that in stock.")]
                    )
                )
                return
            self.open[task_id] = deal
            offer = self.opening_offer(text, deal)
            if offer is None:
                await self.publish_ask(task_id, deal, updater, opening=True)
                return
        else:
            offer = bargaining_offer(text, deal.ask)
            deal.rounds += 1
        deal.last_offer = offer
        action, price = decide(
            offer, deal.ask, deal.quote.accept_at, deal.rounds, policy
        )
        match action:
            case "accept":
                await self.accept(task_id, deal, price, now, policy, updater)
            case "reject":
                _ = self.open.pop(task_id, None)
                self.closed.add(task_id)
                await updater.reject(updater.new_agent_message([Part(text="No deal.")]))
            case _:
                deal.ask = min(deal.ask, price)
                await self.publish_ask(task_id, deal, updater, opening=deal.rounds == 0)

    @staticmethod
    def opening_offer(text: str, deal: Deal) -> int | None:
        """Read an explicit opening offer; week-07 phrasing before Round 3."""
        if deal.legacy_item is not None:
            parsed = opening_offer(text)
            if parsed is not None and parsed.sku == deal.legacy_item:
                return parsed.price
            return None
        return parse_request(text, frozenset()).offer

    def opening(
        self, task_id: str, text: str, now: float, policy: SellerPolicy
    ) -> Deal | None:
        """Find the unit a buyer asked for and price it; Rounds 0-2 use fixed items."""
        inventory = self.stock.inventory()
        if inventory.round < ECON_ROUND:
            item = legacy_item(text)
            if item is None:
                return None
            reserve = LEGACY_RESERVE[item]
            ask = round(reserve * 1.10)
            legacy = Quote(ask, reserve, reserve, "A", clearance=False, stale=False)
            return Deal(None, item, legacy, ask)
        request = parse_request(text, frozenset(u.sku.lower() for u in inventory.units))
        unit = pick_unit(inventory.units, request, now, self.excluded_units(now))
        if unit is None:
            self.emit(f"seller.no_stock task={task_id} text={text[:80]!r}")
            return None
        priced = quote(unit, now, self.stock.shelf_hours(unit.sku), policy)
        self.emit(
            f"seller.open task={task_id} unit={unit.unit} sku={unit.sku} "
            f"grade={priced.grade_now} cost={unit.cost} ask={priced.ask} "
            f"accept_at={priced.accept_at} clearance={priced.clearance}"
        )
        return Deal(unit, None, priced, priced.ask)

    async def publish_ask(
        self, task_id: str, deal: Deal, updater: TaskUpdater, *, opening: bool
    ) -> None:
        """Quote in the starter's wording plus machine-readable metadata."""
        name = deal.unit.name if deal.unit is not None else str(deal.legacy_item)
        sku = deal.unit.sku if deal.unit is not None else str(deal.legacy_item)
        text = (
            f"{name}: asking {deal.ask}. Send your offer as a number."
            if opening
            else f"Counteroffer: {deal.ask}."
        )
        metadata: dict[str, object] = {
            "ask": deal.ask,
            "sku": sku,
            "item": sku,
            "name": name,
            "grade": deal.quote.grade_now,
            "certificate": deal.unit is not None,
        }
        if deal.unit is not None and deal.unit.kind == "goods":
            metadata["warranty_hours"] = self.policy().warranty(deal.quote.grade_now)
        self.emit(f"seller.ask task={task_id} ask={deal.ask} rounds={deal.rounds}")
        await updater.requires_input(
            updater.new_agent_message([Part(text=text)], metadata=metadata)
        )

    async def accept(  # noqa: PLR0913, PLR0917 - the sale needs every fact at once
        self,
        task_id: str,
        deal: Deal,
        price: int,
        now: float,
        policy: SellerPolicy,
        updater: TaskUpdater,
    ) -> None:
        """Sign the exact agreed price and publish the unit, claim and warranty."""
        unit = deal.unit
        item = unit.sku if unit is not None else str(deal.legacy_item)
        receipt: dict[str, object] = {
            "shop": self.name,
            "item": item,
            "price": int(price),
            "task": task_id,
        }
        claim, warranty = "", 0
        if unit is not None:
            receipt["unit"] = unit.unit
            shelf = self.stock.shelf_hours(unit.sku)
            if policy.claim_grade:
                claim = claimed_grade(unit, now, shelf, policy)
                if claim:
                    receipt["claim"] = claim
            if unit.kind == "goods":
                warranty = policy.warranty(deal.quote.grade_now)
                if warranty > 0:
                    receipt["warranty_hours"] = warranty
        if self.signer is not None:
            signed = self.signer.sign(
                shop=self.name, item=item, price=int(price), task=task_id
            )
            receipt["sig_seller"] = signed.sig_seller
        _ = self.open.pop(task_id, None)
        self.closed.add(task_id)
        if unit is not None:
            self.committed[unit.unit] = now
        await updater.add_artifact(
            [
                Part(
                    data=Parse(json.dumps(receipt), Value()),
                    media_type="application/json",
                )
            ],
            name="receipt",
        )
        name = unit.name if unit is not None else item
        await updater.complete(
            updater.new_agent_message([Part(text=f"Deal: {name} at {price}.")])
        )
        self.emit(
            f"seller.receipt task={task_id} sku={item} price={price} "
            f"unit={unit.unit if unit else '-'} claim={claim or '-'} "
            f"warranty={warranty} rounds={deal.rounds}"
        )
        if self.events is not None and unit is not None:
            try:
                self.events.record_receipt(
                    task=task_id,
                    unit=unit.unit,
                    sku=unit.sku,
                    price=int(price),
                    cost=unit.cost,
                    grade=deal.quote.grade_now,
                    claim=claim,
                    warranty_hours=warranty,
                    rounds=deal.rounds,
                )
            except OSError:
                self.emit("seller.events_write_failed")


def create_r3_app(  # noqa: PLR0913 - explicit wiring
    base_url: str,
    name: str,
    *,
    signer: ReceiptSigner | None,
    emit: Callable[[str], None],
    folder: Path | None = None,
    policy_file: Path | None = None,
    events_file: Path | None = None,
) -> FastAPI:
    """Build the A2A server for the stock seller."""
    state = market_dir(folder)
    stock = StockView(state)
    policy_file = policy_file if policy_file is not None else policy_path(state)
    events_file = events_file if events_file is not None else events_path(state)
    try:
        events: EventLedger | None = EventLedger(events_file)
    except OSError:
        events = None
    card = AgentCard(
        name=name,
        description=(
            "Sells from real market stock with the market's unit certificate "
            "attached. Grade claims match the certificate; A-grade goods carry a "
            "warranty. Ask for an item by SKU or name, then send a whole-number "
            "offer. Prices read metadata.ask; matching the ask closes the deal."
        ),
        version="2.0.0-r3",
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain", "application/json"],
        supported_interfaces=[
            AgentInterface(
                url=base_url,
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        skills=[
            AgentSkill(
                id="negotiate",
                name="Negotiate a purchase",
                description=(
                    "Name a SKU or product; the shop quotes from its certified stock. "
                    "Send a number to offer; the receipt names the exact unit."
                ),
                examples=["I want to buy 종가 알뜰맘 포기김치 10kg (G00001)", "52000"],
                tags=["shopping", "negotiation", "round-3"],
            )
        ],
    )
    executor = R3Seller(
        name,
        signer=signer,
        stock=stock,
        policy_file=policy_file,
        events=events,
        emit=emit,
    )
    handler = DefaultRequestHandler(executor, InMemoryTaskStore(), card)
    app = FastAPI(title=name)
    app.router.routes.extend(create_agent_card_routes(card))
    app.router.routes.extend(create_jsonrpc_routes(handler, rpc_url="/"))
    return app
