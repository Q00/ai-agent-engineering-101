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
# ./run_harness.sh seller --port 9998
# ./run_harness.sh buyer --url http://127.0.0.1:9998 --item lamp --budget 120
# ──────────────────
"""Launch the current A2A agents using the Week 05 custom tool-loop harness."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Annotated, Final

import anyio
import typer
import uvicorn
from fastapi import FastAPI
from openai import AsyncOpenAI
from pydantic import TypeAdapter

from http_client import create_client
from market_a2a.buyer import buy
from market_a2a.catalog import Catalog
from market_a2a.harness import MODEL, Harness
from market_a2a.models import FrozenModel, Purchase, ShopCard, ShoppingItem
from market_a2a.seller import create_app
from market_a2a.shopping import shop

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

ROOT: Final = Path(__file__).resolve().parent
KINDS: Final = ("lamp", "chair", "bookshelf", "desk", "storage")
SHOP_COUNT: Final = 20
LOGGER: Final = logging.getLogger("ax06")
app = typer.Typer(add_completion=False)


class ShoppingInput(FrozenModel):
    """The buyer sees only requested descriptions and one total budget."""

    budget: int
    items: tuple[ShoppingItem, ...]


def emit(message: str) -> None:
    """Flush observable model/tool/wire events without credential fields."""
    LOGGER.info(message)


def isolated(run: Callable[[], Awaitable[None]]) -> None:
    """Run model code from an empty directory, preserving absolute file paths."""
    previous = Path.cwd()
    with TemporaryDirectory(prefix="ax06-harness-") as directory:
        try:
            os.chdir(directory)
            anyio.run(run)
        finally:
            os.chdir(previous)


async def serve(port: int, catalog_path: Path, *, directory: bool = False) -> None:
    """Own the shared model client until the seller server shuts down."""
    async with (
        create_client(model=True) as model_http,
        AsyncOpenAI(http_client=model_http) as model,
    ):
        harness = Harness(model, emit)
        if directory:
            web = FastAPI()
            for index in range(SHOP_COUNT):
                base = f"http://127.0.0.1:{port}/shops/{index:02d}/"
                kind = KINDS[index % len(KINDS)]
                catalog = Catalog(catalog_path, price_offset=index * 5, kinds=(kind,))
                child = create_app(base, harness, catalog, f"shop-{index:02d}-{kind}")
                web.mount(f"/shops/{index:02d}", child)
        else:
            web = create_app(
                f"http://127.0.0.1:{port}/",
                harness,
                Catalog(catalog_path),
                "Sungjin Shop",
            )
        emit(f"HARNESS model={MODEL} source=week05-tool-loop directory={directory}")
        await uvicorn.Server(
            uvicorn.Config(web, host="127.0.0.1", port=port, log_level="warning"),
        ).serve()


@app.command()
def seller(
    port: Annotated[int, typer.Option(min=1024, max=65535)],
    catalog: Annotated[Path, typer.Option()] = ROOT / "catalog.json",
) -> None:
    """Run one stateful seller for the official trade test."""
    absolute = catalog.resolve()

    async def run() -> None:
        await serve(port, absolute)

    isolated(run)


@app.command("directory-server")
def directory_server(
    port: Annotated[int, typer.Option(min=1024, max=65535)],
    catalog: Annotated[Path, typer.Option()] = ROOT / "catalog.json",
) -> None:
    """Run 20 independent A2A shops with different categories and reserves."""
    absolute = catalog.resolve()

    async def run() -> None:
        await serve(port, absolute, directory=True)

    isolated(run)


@app.command()
def buyer(
    url: Annotated[str, typer.Option()],
    item: Annotated[str, typer.Option()],
    budget: Annotated[int, typer.Option(min=1)],
) -> None:
    """Run one model buyer against an independently implemented seller."""

    async def run() -> None:
        async with (
            create_client(model=True) as model_http,
            AsyncOpenAI(http_client=model_http) as model,
        ):
            harness = Harness(model, emit)
            result = await buy(harness, url, item, budget, emit)
            emit("BUYER_RESULT " + result.model_dump_json())

    isolated(run)


@app.command()
def shopping(
    directory: Annotated[Path, typer.Option(exists=True)],
    shopping_input: Annotated[Path, typer.Option(exists=True)],
) -> None:
    """Run the shopping-list buyer with actual public cards and one budget."""
    cards = TypeAdapter(tuple[ShopCard, ...]).validate_json(directory.read_bytes())
    request = ShoppingInput.model_validate_json(shopping_input.read_bytes())

    async def run() -> None:
        async with (
            create_client(model=True) as model_http,
            AsyncOpenAI(http_client=model_http) as model,
        ):
            harness = Harness(model, emit)
            purchases = await shop(harness, cards, request.items, request.budget, emit)
            emit(
                "SHOPPING_RESULT "
                + TypeAdapter(tuple[Purchase, ...]).dump_json(purchases).decode()
            )
            for result in purchases:
                emit("PURCHASE " + result.model_dump_json())
            emit(
                "".join(
                    (
                        f"SHOPPING_USAGE calls={harness.calls} ",
                        f"input={harness.input_tokens} output={harness.output_tokens}",
                    ),
                ),
            )

    isolated(run)


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
    app()
