"""Logged A2A v1.0 JSON-RPC calls, with typed response parsing."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, ClassVar, Literal, TypedDict
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from models import Card, ExperimentError, RpcResponse, Task

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx2


class Interface(BaseModel):
    """The supported endpoint fetched from an actual Agent Card."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    url: str
    binding: str = Field(alias="protocolBinding")
    version: str = Field(alias="protocolVersion")


class CardIdentity(BaseModel):
    """Preserve the raw card while parsing its routing fields."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    name: str
    interfaces: tuple[Interface, ...] = Field(alias="supportedInterfaces")


class TextPart(TypedDict):
    """A plain text part sent over A2A."""

    text: str


class Message(TypedDict, total=False):
    """User message fields, including an optional continuing task ID."""

    role: Literal["ROLE_USER"]
    messageId: str
    parts: list[TextPart]
    taskId: str


class SendParams(TypedDict):
    """SendMessage parameters containing one user message."""

    message: Message


class SendRequest(TypedDict):
    """A JSON-RPC request to send one A2A message."""

    jsonrpc: Literal["2.0"]
    id: int
    method: Literal["SendMessage"]
    params: SendParams


class IdParams(TypedDict):
    """Task identity shared by cancellation and retrieval."""

    id: str


class IdRequest(TypedDict):
    """A JSON-RPC request operating on an existing task."""

    jsonrpc: Literal["2.0"]
    id: int
    method: Literal["CancelTask", "GetTask"]
    params: IdParams


class A2AClient:
    """One episode's mutable RPC counter; no private seller state is exposed."""

    def __init__(
        self,
        http: httpx2.AsyncClient,
        emit: Callable[[str], None],
    ) -> None:
        """Reuse the caller-owned HTTP transport and console capture callback."""
        self.http: httpx2.AsyncClient = http
        self.emit: Callable[[str], None] = emit
        self._request_id: int = 0

    async def card(self, url: str) -> Card:
        """Fetch the exact discovery document before the buyer chooses."""
        address = url.rstrip("/") + "/.well-known/agent-card.json"
        self.emit(f"[A2A GET] {address}")
        response = await self.http.get(address)
        _ = response.raise_for_status()
        self.emit(f"[A2A CARD] {response.text}")
        parsed = CardIdentity.model_validate_json(response.text)
        routes = [
            entry
            for entry in parsed.interfaces
            if entry.binding == "JSONRPC" and entry.version == "1.0"
        ]
        if not routes:
            raise ExperimentError("Agent Card", "no JSONRPC v1.0 interface")
        return Card(parsed.name, response.text, routes[0].url)

    async def send(self, card: Card, text: str, task_id: str | None = None) -> Task:
        """Send one message and carry the same taskId after a re-question."""
        self._request_id += 1
        message: Message = {
            "role": "ROLE_USER",
            "messageId": str(uuid4()),
            "parts": [{"text": text}],
        }
        if task_id is not None:
            message["taskId"] = task_id
        payload: SendRequest = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "SendMessage",
            "params": {"message": message},
        }
        self.emit("[A2A REQUEST] " + json.dumps(payload, ensure_ascii=False))
        response = await self.http.post(
            card.url,
            json=payload,
            headers={"A2A-Version": "1.0"},
        )
        _ = response.raise_for_status()
        self.emit("[A2A RESPONSE] " + response.text)
        result = RpcResponse.model_validate_json(response.text)
        if result.error is not None:
            raise ExperimentError(
                f"A2A error {result.error.code}",
                result.error.message,
            )
        if result.result is None or result.result.task is None:
            raise ExperimentError("SendMessage", "response contains no task")
        task = result.result.task
        self.emit(f"[TASK] {task.id} {task.status.state}")
        for artifact in task.artifacts:
            self.emit("[ARTIFACT] " + artifact.model_dump_json())
        return task

    async def cancel(self, card: Card, task_id: str) -> None:
        """Cancel only this episode's task, preserving its actual response."""
        self._request_id += 1
        payload: IdRequest = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "CancelTask",
            "params": {"id": task_id},
        }
        self.emit("[A2A REQUEST] " + json.dumps(payload, ensure_ascii=False))
        response = await self.http.post(
            card.url,
            json=payload,
            headers={"A2A-Version": "1.0"},
        )
        _ = response.raise_for_status()
        self.emit("[A2A RESPONSE] " + response.text)
        parsed = RpcResponse.model_validate_json(response.text)
        if parsed.error is not None:
            raise ExperimentError(
                f"CancelTask {parsed.error.code}",
                parsed.error.message,
            )


def observation(task: Task) -> str:
    """Only the public state and seller message go to the buyer."""
    message = task.status.message
    parts = message.parts if message is not None else ()
    text = " ".join(part.text for part in parts if part.text is not None)
    return f"{task.status.state}: {text}"


def receipt_price(task: Task) -> int:
    """Read the seller artifact; never infer a price from the buyer's reason."""
    for artifact in task.artifacts:
        for part in artifact.parts:
            if part.data is not None:
                return part.data.price
    raise ExperimentError("COMPLETED", "receipt artifact has no structured price")
