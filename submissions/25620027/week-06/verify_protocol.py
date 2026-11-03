#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "a2a-sdk[http-server]==1.2.2", "fastapi>=0.115,<1", "uvicorn>=0.34,<1",
#   "httpx2[http2,brotli,zstd]>=2,<3", "openai==3.8.0",
#   "pydantic>=2.12,<3", "typer>=0.16,<1", "anyio>=4.9,<5",
# ]
# ///
# ─── How to run ───
# Install uv: https://docs.astral.sh/uv/getting-started/installation/
# uv run --python 3.12.11 verify_protocol.py
# ──────────────────
"""Exercise actual SDK servers without model calls or experiment CSV writes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, ClassVar, Final

import anyio
from pydantic import BaseModel, ConfigDict, TypeAdapter

from a2a_client import A2AClient, IdRequest, observation, receipt_price
from http_client import create_client
from models import (
    Condition,
    Config,
    ExperimentError,
    RpcError,
    RpcResponse,
    Scenario,
    Task,
)
from run import ROOT, sellers

if TYPE_CHECKING:
    import httpx2

    from models import Card

RESERVE: Final = 100
PRICEY_RESERVE: Final = 125
VERSION_ERROR: Final = -32009
LOW_OFFER_LIMIT: Final = 5


class SkillSnapshot(BaseModel):
    """Keep every extra card field while exposing the changed description."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="allow")
    description: str


class CardSnapshot(BaseModel):
    """Preserve all fields so normalization excludes only the allowed text."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="allow")
    description: str
    skills: tuple[SkillSnapshot, ...]

    def normalized(self) -> CardSnapshot:
        """Erase exactly description fields before comparing two conditions."""
        return self.model_copy(
            update={
                "description": "",
                "skills": tuple(
                    skill.model_copy(update={"description": ""})
                    for skill in self.skills
                ),
            }
        )


class TaskReply(BaseModel):
    """GetTask and CancelTask return the task directly in result."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    result: Task
    error: RpcError | None = None


def require(*, valid: bool, label: str) -> None:
    """Fail the smoke invocation with a named observable rather than continue."""
    if not valid:
        failure = ExperimentError("protocol smoke", label)
        raise failure
    print(f"[CHECK PASS] {label}")


async def task_rpc(
    http: httpx2.AsyncClient,
    card: Card,
    payload: IdRequest,
) -> Task:
    """Log the wire exchange and parse the actual direct task envelope."""
    print("[A2A REQUEST] " + json.dumps(payload))
    response = await http.post(
        card.url,
        json=payload,
        headers={"A2A-Version": "1.0"},
    )
    _ = response.raise_for_status()
    print("[A2A RESPONSE] " + response.text)
    parsed = TaskReply.model_validate_json(response.text)
    require(valid=parsed.error is None, label=payload["method"] + " succeeded")
    return parsed.result


async def check_prices(remote: A2AClient, cheap: Card, pricey: Card) -> str:
    """Observe reserve boundaries and terminal rejection on real tasks."""
    task = await remote.send(cheap, "lamp")
    task_id = task.id
    require(
        valid=task.status.state == "TASK_STATE_INPUT_REQUIRED",
        label="initial input_required",
    )
    task = await remote.send(cheap, str(RESERVE - 1), task_id)
    require(
        valid=task.id == task_id
        and task.status.state == "TASK_STATE_INPUT_REQUIRED"
        and str(RESERVE) in observation(task),
        label="offer 99 counter 100, same task",
    )
    task = await remote.send(cheap, str(RESERVE), task_id)
    require(
        valid=task.id == task_id
        and task.status.state == "TASK_STATE_COMPLETED"
        and receipt_price(task) == RESERVE,
        label="offer 100 completed, receipt 100, same task",
    )
    terminal_error = ""
    try:
        _ = await remote.send(cheap, str(RESERVE), task_id)
    except ExperimentError as exc:
        terminal_error = exc.source
    require(
        valid=terminal_error == "A2A error -32004",
        label="terminal send rejected -32004",
    )
    expensive = await remote.send(pricey, "lamp")
    require(
        valid=expensive.status.state == "TASK_STATE_INPUT_REQUIRED",
        label="pricey initial input_required",
    )
    for count in range(1, LOW_OFFER_LIMIT + 1):
        expensive = await remote.send(pricey, str(RESERVE), expensive.id)
        expected = (
            "TASK_STATE_REJECTED"
            if count == LOW_OFFER_LIMIT
            else "TASK_STATE_INPUT_REQUIRED"
        )
        require(
            valid=expensive.status.state == expected,
            label=f"pricey low offer {count}: {expected}",
        )
    expensive = await remote.send(pricey, "lamp")
    expensive = await remote.send(pricey, str(PRICEY_RESERVE), expensive.id)
    require(
        valid=expensive.status.state == "TASK_STATE_COMPLETED"
        and receipt_price(expensive) == PRICEY_RESERVE,
        label="pricey reserve 125 accepted",
    )
    return task_id


async def check_lifecycle(
    http: httpx2.AsyncClient,
    remote: A2AClient,
    cheap: Card,
) -> None:
    """Read and cancel an active task and check the mandatory version header."""
    task = await remote.send(cheap, "chair")
    query: IdRequest = {
        "jsonrpc": "2.0",
        "id": 1001,
        "method": "GetTask",
        "params": {"id": task.id},
    }
    fetched = await task_rpc(http, cheap, query)
    require(
        valid=fetched.id == task.id
        and fetched.status.state == "TASK_STATE_INPUT_REQUIRED",
        label="GetTask returns active task",
    )
    cancellation: IdRequest = {
        "jsonrpc": "2.0",
        "id": 1002,
        "method": "CancelTask",
        "params": {"id": task.id},
    }
    canceled = await task_rpc(http, cheap, cancellation)
    require(
        valid=canceled.id == task.id and canceled.status.state == "TASK_STATE_CANCELED",
        label="CancelTask returns canceled task",
    )
    fetched = await task_rpc(http, cheap, query)
    require(
        valid=fetched.status.state == "TASK_STATE_CANCELED",
        label="GetTask persists cancellation",
    )
    print("[A2A REQUEST WITHOUT VERSION] " + json.dumps(query))
    response = await http.post(cheap.url, json=query)
    _ = response.raise_for_status()
    print("[A2A RESPONSE] " + response.text)
    parsed = RpcResponse.model_validate_json(response.text)
    require(
        valid=parsed.error is not None and parsed.error.code == VERSION_ERROR,
        label="missing A2A-Version rejected -32009",
    )


async def main() -> None:
    """Run both advertising conditions in separate owned server lifetimes."""
    config = Config.model_validate_json(
        (ROOT / "experiment_config.json").read_text(encoding="utf-8"),
    )
    scenarios = TypeAdapter(tuple[Scenario, ...]).validate_json(
        (ROOT / "scenarios.json").read_text(encoding="utf-8"),
    )
    output = ROOT / "logs" / "protocol-smoke"
    output.mkdir(parents=True, exist_ok=True)
    snapshots: dict[Condition, tuple[CardSnapshot, CardSnapshot]] = {}
    async with create_client() as http:
        for condition in Condition:
            print(f"[CONDITION] {condition.value}")
            async with sellers(condition, config, scenarios, output, http):
                remote = A2AClient(http, print)
                cheap = await remote.card(f"http://127.0.0.1:{config.cheap_port}")
                pricey = await remote.card(f"http://127.0.0.1:{config.pricey_port}")
                snapshots[condition] = (
                    CardSnapshot.model_validate_json(cheap.raw),
                    CardSnapshot.model_validate_json(pricey.raw),
                )
                _ = await check_prices(remote, cheap, pricey)
                await check_lifecycle(http, remote, cheap)
    plain_cheap, plain_pricey = snapshots[Condition.PLAIN]
    inflated_cheap, inflated_pricey = snapshots[Condition.INFLATED]
    require(
        valid=plain_cheap == inflated_cheap,
        label="cheap card identical across conditions",
    )
    require(
        valid=plain_pricey.description != inflated_pricey.description
        and tuple(s.description for s in plain_pricey.skills)
        != tuple(s.description for s in inflated_pricey.skills),
        label="pricey descriptions changed",
    )
    require(
        valid=plain_pricey.normalized() == inflated_pricey.normalized(),
        label="pricey card differs only in description and skill descriptions",
    )
    print("[PROTOCOL SMOKE PASS] both conditions; 0 model calls; CSV untouched")


if __name__ == "__main__":
    anyio.run(main)
