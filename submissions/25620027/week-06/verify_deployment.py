#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx2[http2,brotli,zstd]>=2,<3", "pydantic>=2.12,<3",
#   "typer>=0.16,<1", "anyio>=4.9,<5",
# ]
# ///
# ─── How to run ───
# Install uv: https://docs.astral.sh/uv/getting-started/installation/
# uv run verify_deployment.py --url https://seller.example --output logs/new-attempt
# ──────────────────
"""Bounded public seller verification: four cases, not the 21-check trade kit."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path  # noqa: TC003 - Typer resolves CLI annotations at runtime.
from typing import Annotated, Final

import anyio
import httpx2
import typer
from pydantic import JsonValue, ValidationError

from http_client import create_client
from market_a2a.models import HarnessError
from market_a2a.remote import Remote, Response, Task

MAX_OFFERS: Final = 8
TERMINAL_ERROR: Final = -32004


@dataclass(frozen=True, slots=True)
class Scenario:
    """Fixed counterpart's authorized product and offer ladder."""

    item: str
    budget: int
    reserve: int
    ladder: tuple[int, ...]


SCENARIOS: Final = (
    Scenario("lamp", 120, 100, (60, 80, 100, 110, 120)),
    Scenario("chair", 200, 180, (100, 150, 180, 190, 200)),
    Scenario("bookshelf", 300, 260, (150, 200, 260, 280, 300)),
    Scenario("bookshelf", 230, 260, (140, 170, 200, 230)),
)


class Recorder:
    """Accumulate observed wire counts and archive each completed HTTP exchange."""

    def __init__(self, output: Path) -> None:
        """Keep an isolated, caller-created attempt directory."""
        self.output: Path = output
        self.count: int = 0
        self.posts: int = 0
        self.valid_headers: int = 0
        self.case: str = "card"
        self.last_body: str = ""

    def emit(self, line: str) -> None:
        """Append the existing transport's credential-free protocol transcript."""
        with (self.output / "transcript.log").open("a", encoding="utf-8") as stream:
            _ = stream.write(f"[{self.case}] {line}\n")

    async def capture(self, reply: httpx2.Response) -> None:
        """Save raw bodies before HTTP or protocol parsing can fail."""
        _ = await reply.aread()
        self.count += 1
        self.posts += int(reply.request.method == "POST")
        self.valid_headers += int(reply.request.headers.get("A2A-Version") == "1.0")
        self.last_body = reply.text
        prefix = self.output / f"{self.count:03d}-{self.case}"
        _ = prefix.with_suffix(".response.json").write_text(
            reply.text, encoding="utf-8"
        )
        if reply.request.content:
            _ = prefix.with_suffix(".request.json").write_bytes(reply.request.content)
        self.emit(
            json.dumps({"http_status": reply.status_code, "exchange": prefix.name})
        )


def require(*, valid: bool, detail: str) -> None:
    """Fail explicitly even when Python assertions are disabled."""
    if not valid:
        raise HarnessError(detail)


def asking_price(task: Task) -> int:
    """Match the official fixed buyer's last-number, rounded-up interpretation."""
    numbers = [m.group() for m in re.finditer(r"\d+(?:\.\d+)?", task.observation())]
    require(valid=bool(numbers), detail="input-required message has no asking price")
    return math.ceil(float(numbers[-1]))


async def negotiate(
    remote: Remote, endpoint: str, scenario: Scenario
) -> tuple[Task, int | None]:
    """Accept affordable asks, otherwise advance the ladder for at most eight offers."""
    task = await remote.send(endpoint, f"I want to buy a {scenario.item}")
    require(
        valid=task.status.state == "TASK_STATE_INPUT_REQUIRED",
        detail="seller did not open",
    )
    require(
        valid=bool(task.id and task.context_id), detail="missing task/context identity"
    )
    ladder_index = 0
    last_offer: int | None = None
    for _ in range(MAX_OFFERS):
        if task.status.state != "TASK_STATE_INPUT_REQUIRED":
            break
        ask = asking_price(task)
        require(valid=ask >= scenario.reserve, detail="seller asked below reserve")
        offer = ask
        if ask > scenario.budget:
            offer = (
                scenario.ladder[ladder_index]
                if ladder_index < len(scenario.ladder)
                else scenario.budget
            )
            ladder_index += 1
        last_offer = offer
        task = await remote.send(endpoint, str(offer), task)
    if task.status.state == "TASK_STATE_INPUT_REQUIRED":
        require(
            valid=asking_price(task) >= scenario.reserve,
            detail="final ask below reserve",
        )
    return task, last_offer


async def verify_case(
    remote: Remote, recorder: Recorder, scenario: Scenario
) -> JsonValue:
    """Check receipt bounds/product identity or an ordinary bounded no-deal outcome."""
    endpoint = str(remote.client.base_url)
    task, last_offer = await negotiate(remote, endpoint, scenario)
    receipts = [p.data for a in task.artifacts for p in a.parts if p.data is not None]
    result: dict[str, JsonValue] = {
        "case": recorder.case,
        "pass": True,
        "state": task.status.state,
        "task_id": task.id,
        "context_id": task.context_id,
    }
    if scenario.budget < scenario.reserve:
        require(
            valid=task.status.state
            in {"TASK_STATE_REJECTED", "TASK_STATE_INPUT_REQUIRED"}
            and not receipts,
            detail="expected ordinary no-deal without a receipt",
        )
        return result
    require(
        valid=task.status.state == "TASK_STATE_COMPLETED",
        detail="deal did not complete",
    )
    require(valid=len(receipts) == 1, detail="expected exactly one receipt")
    receipt = receipts[0]
    require(
        valid=scenario.reserve <= receipt.price <= scenario.budget
        and last_offer is not None
        and receipt.price == last_offer
        and receipt.item == scenario.item
        and receipt.sku == scenario.item,
        detail="receipt price or basic SKU differs from authorization",
    )
    result["receipt"] = {
        "price": receipt.price,
        "item": receipt.item,
        "sku": receipt.sku,
    }
    recorder.last_body = ""
    try:
        _ = await remote.send(endpoint, str(scenario.budget), task)
    except HarnessError:
        terminal = Response.model_validate_json(recorder.last_body)
        require(
            valid=terminal.error is not None and terminal.error.code == TERMINAL_ERROR,
            detail="finished task must return terminal-task error -32004",
        )
    else:
        require(valid=False, detail="finished task accepted a new message")
    result["terminal_error"] = TERMINAL_ERROR
    return result


async def execute(url: str, output: Path) -> bool:
    """Run sequential bounded cases and preserve failure diagnostics in the attempt."""
    recorder = Recorder(output)
    cases: list[JsonValue] = []
    summary: dict[str, JsonValue] = {"scope": "public-seller-four-cases", "url": url}
    passed = False
    try:
        async with create_client(model=True) as client:
            client.follow_redirects = False
            client.base_url = httpx2.URL(url)
            client.event_hooks["response"].append(recorder.capture)
            remote = Remote(client, recorder.emit)
            card = await remote.card(url)
            require(
                valid=bool(card.name.strip() and card.skills),
                detail="card needs name and skills",
            )
            require(
                valid=bool(card.supported_interfaces), detail="card has no interface"
            )
            route = card.supported_interfaces[0]
            require(
                valid=route.protocol_binding == "JSONRPC"
                and route.protocol_version == "1.0"
                and route.url.rstrip("/") == url.rstrip("/"),
                detail="first interface must match public URL and JSONRPC 1.0",
            )
            summary["card"] = {
                "pass": True,
                "name": card.name,
                "skills": len(card.skills),
            }
            for scenario in SCENARIOS:
                recorder.case = f"{scenario.item}-{scenario.budget}"
                try:
                    with anyio.fail_after(300):
                        cases.append(await verify_case(remote, recorder, scenario))
                except (
                    HarnessError,
                    httpx2.HTTPError,
                    ValidationError,
                    TimeoutError,
                ) as exc:
                    cases.append(
                        {"case": recorder.case, "pass": False, "error": str(exc)}
                    )
                    recorder.emit(f"[FAIL] {type(exc).__name__}: {exc}")
            passed = all(
                isinstance(case, dict) and case.get("pass") is True for case in cases
            )
    except (HarnessError, httpx2.HTTPError, ValidationError) as exc:
        summary["error"] = str(exc)
        recorder.emit(f"[FAIL] {type(exc).__name__}: {exc}")
    passed = passed and recorder.count == recorder.valid_headers
    summary.update(
        {
            "pass": passed,
            "cases": cases,
            "http_responses": recorder.count,
            "post_payloads": recorder.posts,
            "valid_version_headers": recorder.valid_headers,
        }
    )
    _ = (output / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return passed


def main(
    url: Annotated[str, typer.Option("--url")],
    output: Annotated[Path, typer.Option("--output")],
) -> None:
    """Verify a public HTTPS seller and write only to a new evidence directory."""
    target = httpx2.URL(url)
    require(
        valid=target.scheme == "https"
        and bool(target.host)
        and not target.userinfo
        and not target.query
        and not target.fragment,
        detail="URL must use HTTPS without credentials, query or fragment",
    )
    require(
        valid=target.host not in {"localhost", "localhost.localdomain"},
        detail="public host required",
    )
    try:
        address = ip_address(target.host)
    except ValueError:
        require(valid="." in target.host, detail="public DNS hostname required")
    else:
        require(valid=address.is_global, detail="public IP address required")
    output.mkdir(parents=True, exist_ok=False)
    passed = anyio.run(execute, str(target), output)
    typer.echo(f"{'PASS' if passed else 'FAIL'}: {output / 'summary.json'}")
    raise typer.Exit(code=0 if passed else 1)


if __name__ == "__main__":
    typer.run(main)
