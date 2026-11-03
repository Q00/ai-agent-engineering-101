"""Exercise the real SDK JSON-RPC routes without model calls."""

import json
from collections.abc import AsyncIterator
from typing import ClassVar, Literal
from uuid import uuid4

import httpx2
import pytest
from pydantic import BaseModel, ConfigDict, Field

from seller import create_app
from seller_state import Reserves, SellerConfig


class WireModel(BaseModel):
    """Freeze parsed wire observations."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, populate_by_name=True)


class WireReceipt(WireModel):
    """Normalize integral protobuf JSON numbers at the wire boundary."""

    seller: str
    item: str
    price: int = Field(gt=0)


class Part(WireModel):
    """Parse seller message text or a receipt."""

    text: str = ""
    data: WireReceipt | None = None


class Message(WireModel):
    """Collect typed A2A message parts."""

    parts: list[Part] = Field(default_factory=list)


class Status(WireModel):
    """Observe the protocol state and seller reply."""

    state: str
    message: Message = Field(default_factory=Message)


class Artifact(WireModel):
    """Observe the receipt artifact parts."""

    parts: list[Part]


class Task(WireModel):
    """Observe task identity, state and receipt artifacts."""

    id: str
    context_id: str = Field(alias="contextId")
    status: Status
    artifacts: list[Artifact] = Field(default_factory=list)


class Sent(WireModel):
    """Parse the SendMessage result wrapper."""

    task: Task


class Error(WireModel):
    """Observe the JSON-RPC failure code."""

    code: int


class Reply(WireModel):
    """Parse successful task responses or JSON-RPC errors."""

    result: Sent | Task | None = None
    error: Error | None = None


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client() -> AsyncIterator[httpx2.AsyncClient]:
    config = SellerConfig(
        name="Birch", port=9411, reserves=Reserves(lamp=100, chair=180, bookshelf=260)
    )
    transport = httpx2.ASGITransport(app=create_app(config))
    async with httpx2.AsyncClient(
        transport=transport,
        base_url="http://seller",
        follow_redirects=True,
        timeout=httpx2.Timeout(connect=5, read=30, write=10, pool=10),
        headers={"A2A-Version": "1.0"},
    ) as connection:
        yield connection


async def send(
    client: httpx2.AsyncClient, text: str, task: Task | None = None
) -> Reply:
    body = {
        "jsonrpc": "2.0",
        "id": str(uuid4()),
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": str(uuid4()),
                "role": "ROLE_USER",
                "parts": [{"text": text}],
                "taskId": task.id if task else "",
                "contextId": task.context_id if task else "",
            },
        },
    }
    response = await client.post(
        "/", content=json.dumps(body), headers={"Content-Type": "application/json"}
    )
    print(response.text)
    return Reply.model_validate_json(response.content)


async def open_task(client: httpx2.AsyncClient) -> Task:
    reply = await send(client, "lamp")
    assert isinstance(reply.result, Sent)
    return reply.result.task


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("offer", "state"),
    [
        (99, "TASK_STATE_INPUT_REQUIRED"),
        (100, "TASK_STATE_COMPLETED"),
        (101, "TASK_STATE_COMPLETED"),
    ],
)
async def test_reserve_boundary(
    client: httpx2.AsyncClient, offer: int, state: str
) -> None:
    # Given an open negotiation with reserve 100.
    task = await open_task(client)
    # When the boundary offer is sent.
    reply = await send(client, str(offer), task)
    # Then the exact boundary controls acceptance and the task ID is stable.
    assert isinstance(reply.result, Sent)
    assert (reply.result.task.id, reply.result.task.status.state) == (task.id, state)


@pytest.mark.anyio
async def test_fifth_low_offer_rejects(client: httpx2.AsyncClient) -> None:
    # Given four low offers in one task.
    task = await open_task(client)
    for _ in range(4):
        reply = await send(client, "99", task)
        assert isinstance(reply.result, Sent)
        assert reply.result.task.status.state == "TASK_STATE_INPUT_REQUIRED"
    # When the fifth low offer arrives.
    reply = await send(client, "99", task)
    # Then the seller walks away on exactly that offer.
    assert isinstance(reply.result, Sent)
    assert reply.result.task.status.state == "TASK_STATE_REJECTED"


@pytest.mark.anyio
async def test_completed_task_contains_receipt(client: httpx2.AsyncClient) -> None:
    # Given a selected inventory item.
    task = await open_task(client)
    # When the buyer accepts at reserve.
    reply = await send(client, "100", task)
    # Then the completion artifact records the actual transaction.
    assert isinstance(reply.result, Sent)
    assert reply.result.task.artifacts[0].parts[0].data == WireReceipt(
        seller="Birch", item="lamp", price=100
    )


@pytest.mark.anyio
async def test_completed_task_rejects_new_messages(client: httpx2.AsyncClient) -> None:
    # Given a completed purchase.
    task = await open_task(client)
    _ = await send(client, "100", task)
    # When a new message targets that terminal task.
    reply = await send(client, "101", task)
    # Then it cannot create a second transaction.
    assert reply.error is not None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "state"),
    [("GetTask", "TASK_STATE_INPUT_REQUIRED"), ("CancelTask", "TASK_STATE_CANCELED")],
)
async def test_task_methods(
    client: httpx2.AsyncClient, method: Literal["GetTask", "CancelTask"], state: str
) -> None:
    # Given one open task.
    task = await open_task(client)
    # When the SDK task method is called.
    response = await client.post(
        "/",
        json={
            "jsonrpc": "2.0",
            "id": "task-method",
            "method": method,
            "params": {"id": task.id},
        },
    )
    print(response.text)
    reply = Reply.model_validate_json(response.content)
    # Then the method returns the same task in its expected state.
    assert isinstance(reply.result, Task)
    assert (reply.result.id, reply.result.status.state) == (task.id, state)


@pytest.mark.anyio
async def test_missing_version_rejected(client: httpx2.AsyncClient) -> None:
    # Given an otherwise valid JSON-RPC client missing its version header.
    del client.headers["A2A-Version"]
    # When it attempts to open a task.
    reply = await send(client, "lamp")
    # Then the SDK rejects the unsupported protocol version.
    assert reply.error is not None
    assert reply.error.code == -32009


@pytest.mark.anyio
async def test_unavailable_item_rejected(client: httpx2.AsyncClient) -> None:
    # Given the three-item inventory.
    # When the buyer requests an unavailable item.
    reply = await send(client, "sofa")
    # Then the task ends without a receipt.
    assert isinstance(reply.result, Sent)
    assert reply.result.task.status.state == "TASK_STATE_REJECTED"
    assert reply.result.task.artifacts == []
