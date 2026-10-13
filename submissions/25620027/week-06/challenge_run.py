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
# uv run --frozen --python 3.12.11 --script challenge_run.py challenge-1 --attempt 1
# uv run --frozen --python 3.12.11 --script challenge_run.py challenge-2 --attempt 1
# ──────────────────
"""Reproduce the two mandatory challenges and preserve every live attempt."""

from __future__ import annotations

import json
import socket
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Final

import anyio
import httpx2
import typer
from pydantic import TypeAdapter, ValidationError

from http_client import create_client
from market_a2a.challenges import (
    BuyerInput,
    Case,
    CaseResult,
    ShoppingFixture,
    Summary,
    quote,
    receipt,
    require,
    shopping_summary,
)
from market_a2a.models import HarnessError, Product, ShopCard, ShoppingItem
from market_a2a.remote import Card, Remote

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from anyio.abc import Process

MIN_CATALOG: Final = 1000
ROOT: Final = Path(__file__).resolve().parent
app = typer.Typer(add_completion=False)


async def stop(process: Process) -> None:
    """Terminate only the owned handle and bound graceful shutdown."""
    with anyio.CancelScope(shield=True):
        if process.returncode is None:
            process.terminate()
            with anyio.move_on_after(10) as scope:
                _ = await process.wait()
            if scope.cancel_called:
                process.kill()
                _ = await process.wait()
        await process.aclose()


@asynccontextmanager
async def owned(command: list[str], log: Path) -> AsyncGenerator[Process]:
    """Preserve raw stdout/stderr and clean up even when validation raises."""
    with log.open("xb") as output:
        process = await anyio.open_process(
            command,
            cwd=ROOT,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            yield process
        finally:
            await stop(process)


def free_port() -> int:
    """Ask the OS for an available loopback port."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return TypeAdapter(tuple[str, int]).validate_python(probe.getsockname())[1]


async def ready(base: str, process: Process) -> None:
    """Wait for an actual Agent Card, without invoking the model."""
    async with create_client() as client:
        with anyio.fail_after(60):
            while True:
                require(process.returncode is None, "Server exited before readiness")
                try:
                    response = await client.get(
                        base + ".well-known/agent-card.json",
                        headers={"A2A-Version": "1.0"},
                    )
                    if response.is_success:
                        _ = Card.model_validate_json(response.text)
                        return
                except httpx2.TransportError:
                    await anyio.sleep(0.25)
                    continue
                await anyio.sleep(0.25)


async def catalog_challenge(base: str, directory: Path) -> Summary:
    """Test six descriptive requests with a fixed evaluation buyer."""
    cases = TypeAdapter(tuple[Case, ...]).validate_json(
        (ROOT / "challenge_cases.json").read_bytes()
    )
    products = TypeAdapter(tuple[Product, ...]).validate_json(
        (ROOT / "catalog.json").read_bytes()
    )
    require(len(products) >= MIN_CATALOG, "Catalog must contain thousands of products")
    catalog = {product.sku: product for product in products}
    results: list[CaseResult] = []
    with (directory / "wire.log").open("x") as wire:

        def emit(message: str) -> None:
            _ = wire.write(message + "\n")
            wire.flush()

        async def observe(response: httpx2.Response) -> None:
            emit(
                "[A2A HTTP] "
                + json.dumps(
                    {
                        "method": response.request.method,
                        "url": str(response.request.url),
                        "version": response.request.headers.get("A2A-Version", ""),
                    }
                )
            )

        async with create_client() as client:
            client.event_hooks["response"].append(observe)
            remote = Remote(client, emit)
            card = await remote.card(base)
            for case in cases:
                emit("[CASE] " + case.model_dump_json())
                product = catalog[case.expected_sku]
                task = await remote.send(card.endpoint(), case.request)
                quotes = [quote(task, product)]
                task = await remote.send(
                    card.endpoint(), str(product.reserve - 1), task
                )
                quotes.append(quote(task, product))
                task = await remote.send(card.endpoint(), str(product.reserve), task)
                offered = product.reserve
                for _round in range(6):
                    if task.status.state == "TASK_STATE_COMPLETED":
                        break
                    offered = quote(task, product)
                    quotes.append(offered)
                    task = await remote.send(card.endpoint(), str(offered), task)
                sold = receipt(task, product, base)
                require(
                    sold.price == offered, "Receipt differs from actual offered price"
                )
                result = CaseResult(
                    case_id=case.case_id,
                    expected_sku=case.expected_sku,
                    reserve=product.reserve,
                    quotes=tuple(quotes),
                    receipt=sold,
                )
                results.append(result)
                emit("[CASE RESULT] " + result.model_dump_json())
    return Summary(
        passed=True,
        challenge=1,
        cases=tuple(results),
        receipts=tuple(case.receipt for case in results),
        directory_count=1,
    )


async def shopping_challenge(base: str, directory: Path) -> Summary:
    """Discover all real shops, run the model buyer and audit its raw output."""
    fixture = ShoppingFixture.model_validate_json(
        (ROOT / "shopping_list.json").read_bytes()
    )
    buyer_input = BuyerInput(
        budget=fixture.budget,
        items=tuple(
            ShoppingItem(request_id=item.request_id, description=item.description)
            for item in fixture.items
        ),
    )
    input_path = directory / "shopping-input.json"
    _ = input_path.write_text(buyer_input.model_dump_json(indent=2))
    cards: list[ShopCard] = []
    cards_path = directory / "cards"
    cards_path.mkdir()
    async with create_client() as client:
        for index in range(20):
            url = f"{base}shops/{index:02d}/"
            response = await client.get(
                url + ".well-known/agent-card.json", headers={"A2A-Version": "1.0"}
            )
            _ = response.raise_for_status()
            _ = (cards_path / f"{index:02d}.json").write_text(response.text)
            _ = (cards_path / f"{index:02d}-http.json").write_text(
                json.dumps(
                    {
                        "url": str(response.request.url),
                        "status": response.status_code,
                        "version": response.request.headers.get("A2A-Version", ""),
                    },
                    indent=2,
                )
            )
            card = Card.model_validate_json(response.text)
            require(card.endpoint().startswith(url), "Card endpoint is outside shop")
            cards.append(card.directory_entry(url))
    directory_path = directory / "directory.json"
    _ = directory_path.write_bytes(
        TypeAdapter(list[ShopCard]).dump_json(cards, indent=2)
    )
    command = [
        str(ROOT / "run_harness.sh"),
        "shopping",
        "--directory",
        str(directory_path),
        "--shopping-input",
        str(input_path),
    ]
    async with owned(command, directory / "buyer.log") as buyer:
        with anyio.fail_after(900):
            result = await buyer.wait()
        require(result == 0, "Shopping buyer exited with failure; inspect buyer.log")
    return shopping_summary((directory / "buyer.log").read_text(), fixture, ROOT)


async def run(challenge: int, attempt: int) -> None:
    """Reserve a new evidence directory and always write an attempt verdict."""
    directory = ROOT / "logs" / f"challenge-{challenge}" / f"attempt-{attempt}"
    directory.mkdir(parents=True, exist_ok=False)
    port = free_port()
    base = f"http://127.0.0.1:{port}/"
    mode = "seller" if challenge == 1 else "directory-server"
    command = [str(ROOT / "run_harness.sh"), mode, "--port", str(port)]
    _ = (directory / "invocation.json").write_text(
        json.dumps(
            {
                "challenge": challenge,
                "attempt": attempt,
                "server": command,
                "cwd": str(ROOT),
                "runner": str(ROOT / "challenge_run.py"),
            },
            indent=2,
        )
    )
    summary = Summary(
        passed=False,
        challenge=challenge,
        error="Attempt interrupted before validation",
    )
    try:
        async with owned(command, directory / "server.log") as server:
            await ready(base if challenge == 1 else base + "shops/00/", server)
            with anyio.fail_after(1200):
                summary = await (
                    catalog_challenge(base, directory)
                    if challenge == 1
                    else shopping_challenge(base, directory)
                )
    except (
        HarnessError,
        ValidationError,
        httpx2.HTTPError,
        TimeoutError,
        OSError,
    ) as exc:
        summary = Summary(passed=False, challenge=challenge, error=str(exc))
        raise
    finally:
        _ = (directory / "summary.json").write_text(
            summary.model_dump_json(by_alias=True, indent=2)
        )
    typer.echo(str(directory / "summary.json"))


@app.command("challenge-1")
def challenge_one(attempt: Annotated[int, typer.Option(min=1)]) -> None:
    """Run descriptive catalog matching, quote floors and receipts."""
    anyio.run(run, 1, attempt)


@app.command("challenge-2")
def challenge_two(attempt: Annotated[int, typer.Option(min=1)]) -> None:
    """Run 20-shop discovery and concurrent shopping within one budget."""
    anyio.run(run, 2, attempt)


if __name__ == "__main__":
    app()
