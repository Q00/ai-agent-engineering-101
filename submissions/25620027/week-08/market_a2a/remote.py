"""Typed, logged A2A v1.0 transport and receipt trust boundary."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, ClassVar
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from market_a2a.models import HarnessError, Purchase, ShopCard
from market_a2a.security import SecurityEvents

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx2


class Wire(BaseModel):
    """Allow protocol extensions while parsing the fields we consume."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


class Interface(Wire):
    """Public route advertised by a real Agent Card."""

    url: str
    protocol_binding: str = Field(alias="protocolBinding")
    protocol_version: str = Field(alias="protocolVersion")


class Skill(Wire):
    """Directory search text from a public skill."""

    name: str
    description: str = ""


class Card(Wire):
    """Discover the JSONRPC interface independently of the base URL."""

    name: str
    supported_interfaces: tuple[Interface, ...] = Field(alias="supportedInterfaces")
    skills: tuple[Skill, ...]

    def directory_entry(self, url: str) -> ShopCard:
        """Expose only public capabilities to the shopping planner."""
        return ShopCard(
            url=url,
            name=self.name,
            skills=tuple(f"{skill.name}: {skill.description}" for skill in self.skills),
        )

    def endpoint(self) -> str:
        """Select the supported v1.0 JSON-RPC binding."""
        for route in self.supported_interfaces:
            if route.protocol_binding == "JSONRPC" and route.protocol_version == "1.0":
                return route.url
        msg = "Agent Card has no JSONRPC 1.0 interface"
        raise HarnessError(msg)


class Receipt(Wire):
    """Strict monetary values cannot be coerced from strings or booleans."""

    price: int = Field(strict=True, ge=1)

    @field_validator("price", mode="before")
    @classmethod
    def integral_price(cls, value: JsonValue) -> int:
        """Protobuf numeric Values use doubles; permit only exact integers."""
        numeric = TypeAdapter(int | float).validate_python(value, strict=True)
        if not float(numeric).is_integer():
            message = "Receipt price must be integral"
            raise ValueError(message)
        return int(numeric)

    item: str
    sku: str = ""
    task: str = ""


class Part(Wire):
    """Relevant public text and structured receipt parts."""

    text: str = ""
    data: dict[str, JsonValue] | None = None


class Identity(Wire):
    """Selected product identity advertised before checkout."""

    sku: str = ""
    item: str = ""
    ask: JsonValue = None


class Message(Wire):
    """Public seller reply."""

    parts: tuple[Part, ...] = ()
    metadata: Identity = Field(default_factory=Identity)


class Status(Wire):
    """Status names are consumed as protocol strings, not policy inputs."""

    state: str
    message: Message = Field(default_factory=Message)


class Artifact(Wire):
    """A structured seller artifact."""

    parts: tuple[Part, ...] = ()


class Task(Wire):
    """Only bounded public task fields enter the buyer state."""

    id: str
    context_id: str = Field(alias="contextId")
    status: Status
    artifacts: tuple[Artifact, ...] = ()
    metadata: Identity = Field(default_factory=Identity)

    def observation(self) -> str:
        """Keep bounded raw seller text for logs and failure evidence only."""
        return " ".join(part.text for part in self.status.message.parts)[:4000]

    def asking_price(self) -> int | None:
        """Expose only positive whole-number asks as untrusted price observations."""
        for value in (self.status.message.metadata.ask, self.metadata.ask):
            try:
                amount = TypeAdapter(int | float).validate_python(value, strict=True)
            except ValidationError:
                continue
            if amount > 0 and amount % 1 == 0:
                return int(amount)
        first_line = self.observation().split("\n", 1)[0].strip()
        for pattern in (
            r"Counteroffer: ([0-9]{1,9})\.",
            r"[0-9]{1,9} is too low\. Counteroffer: ([0-9]{1,9})\.",
            (
                r"[A-Za-z][A-Za-z0-9 _-]{0,79}: asking ([0-9]{1,9})\. "
                r"Send your offer as a number\."
            ),
        ):
            matched = re.fullmatch(pattern, first_line)
            if matched is not None and int(matched[1]) > 0:
                return int(matched[1])
        return None

    def commercial_observation(self) -> dict[str, JsonValue]:
        """Expose only allowlisted protocol state and a claimed numeric ask."""
        state = self.status.state
        if state not in (
            "TASK_STATE_SUBMITTED",
            "TASK_STATE_WORKING",
            "TASK_STATE_INPUT_REQUIRED",
            "TASK_STATE_COMPLETED",
            "TASK_STATE_CANCELED",
            "TASK_STATE_FAILED",
            "TASK_STATE_REJECTED",
            "TASK_STATE_AUTH_REQUIRED",
        ):
            state = "UNKNOWN"
        return {"state": state, "ask": self.asking_price()}


class Result(Wire):
    """SendMessage result envelope."""

    task: Task


class RpcError(Wire):
    """A server error is a transport failure, never a receipt."""

    code: int
    message: str


class Response(Wire):
    """Parse the JSON-RPC result at the trust boundary."""

    result: Result | None = None
    error: RpcError | None = None


class Remote:
    """Caller-owned transport; each send preserves task and context identity."""

    def __init__(
        self,
        client: httpx2.AsyncClient,
        emit: Callable[[str], None],
        events: SecurityEvents | None = None,
    ) -> None:
        """Reuse the caller-owned transport and trace sink."""
        self.client: httpx2.AsyncClient = client
        self.emit: Callable[[str], None] = emit
        self.events: SecurityEvents = events if events is not None else SecurityEvents()

    async def card(self, url: str) -> Card:
        """Read the actual card with the mandatory protocol header."""
        reply = await self.client.get(
            url.rstrip("/") + "/.well-known/agent-card.json",
            headers={"A2A-Version": "1.0"},
        )
        self.trace(reply)
        _ = reply.raise_for_status()
        self.emit("[A2A CARD] " + reply.text)
        return Card.model_validate_json(reply.text)

    async def send(self, endpoint: str, text: str, task: Task | None = None) -> Task:
        """Send one message; reject task switching by a remote peer."""
        message = {
            "role": "ROLE_USER",
            "messageId": str(uuid4()),
            "parts": [{"text": text}],
        }
        if task is not None:
            message.update(taskId=task.id, contextId=task.context_id)
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": "SendMessage",
            "params": {"message": message},
        }
        self.emit("[A2A REQUEST] " + json.dumps(payload))
        reply = await self.client.post(
            endpoint, json=payload, headers={"A2A-Version": "1.0"}
        )
        self.trace(reply)
        _ = reply.raise_for_status()
        self.emit("[A2A RESPONSE] " + reply.text)
        parsed = Response.model_validate_json(reply.text)
        if parsed.error is not None:
            msg = f"A2A error {parsed.error.code}: {parsed.error.message}"
            raise HarnessError(msg)
        if parsed.result is None:
            msg = "A2A response has no task"
            raise HarnessError(msg)
        received = parsed.result.task
        self.events.observe(received.observation())
        if task is not None and (
            received.id != task.id or received.context_id != task.context_id
        ):
            msg = "A2A peer changed negotiation identity"
            raise HarnessError(msg)
        return received

    def trace(self, reply: httpx2.Response) -> None:
        """Observe the sent protocol version without exposing auth headers."""
        self.emit(
            "[A2A HTTP] "
            + json.dumps(
                {
                    "method": reply.request.method,
                    "url": str(reply.request.url),
                    "version": reply.request.headers.get("A2A-Version"),
                },
            ),
        )


class Checkout(Wire):
    """Authorization against which a receipt must be checked exactly."""

    item: str
    sku: str = ""
    price: int
    budget: int
    shop: str


def purchase(task: Task, authorization: Checkout) -> Purchase:
    """Accept exactly one matching receipt after an authorized offer."""
    receipts = [
        part.data
        for artifact in task.artifacts
        for part in artifact.parts
        if part.data is not None
    ]
    if len(receipts) != 1:
        msg = "Completed task must contain exactly one receipt"
        raise HarnessError(msg)
    raw_receipt = receipts[0]
    receipt = Receipt.model_validate(raw_receipt)
    if (
        receipt.price != authorization.price
        or receipt.price > authorization.budget
        or receipt.item != authorization.item
        or receipt.sku != authorization.sku
        or (bool(receipt.task) and receipt.task != task.id)
    ):
        msg = "Receipt differs from authorized price or product"
        raise HarnessError(msg)
    return Purchase(
        completed=True,
        item=receipt.item,
        sku=receipt.sku,
        price=receipt.price,
        task_id=task.id,
        shop=authorization.shop,
        receipt=raw_receipt,
    )
