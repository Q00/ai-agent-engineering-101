#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "a2a-sdk[http-server]==1.2.2", "fastapi>=0.115,<1", "uvicorn>=0.34,<1",
#   "httpx2[http2,brotli,zstd]>=2,<3", "openai==3.8.0",
#   "pydantic>=2.12,<3", "typer>=0.16,<1", "anyio>=4.9,<5",
#   "cryptography==50.0.2",
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
from pydantic import TypeAdapter

from market_a2a.buyer import buy
from market_a2a.catalog import Catalog
from market_a2a.market_buyer import MarketRequest, market_buy
from market_a2a.model_session import harness_session
from market_a2a.models import (
    FrozenModel,
    Purchase,
    ShopCard,
    ShoppingItem,
)
from market_a2a.receipts import ReceiptSigner
from market_a2a.seller import create_app
from market_a2a.shopping import shop
from r3 import buyer as r3_buyer
from r3 import restock as r3_restock
from r3 import review as r3_review
from r3.ledger_db import LedgerDB, ledger_path
from r3.market_files import read_catalog
from r3.seller import create_r3_app

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


async def serve(
    port: int,
    catalog_path: Path,
    *,
    directory: bool = False,
    first_shop_price_offset: int = 0,
) -> None:
    """Own the shared model client until the seller server shuts down."""
    signer = ReceiptSigner.load(ROOT / "shop_key.pem")
    async with harness_session(emit) as harness:
        if directory:
            web = FastAPI()
            for index in range(SHOP_COUNT):
                base = f"http://127.0.0.1:{port}/shops/{index:02d}/"
                kind = KINDS[index % len(KINDS)]
                offset = index * 5 + (first_shop_price_offset if index == 0 else 0)
                catalog = Catalog(catalog_path, price_offset=offset, kinds=(kind,))
                child = create_app(
                    base, harness, catalog, f"shop-{index:02d}-{kind}", signer=signer
                )
                web.mount(f"/shops/{index:02d}", child)
        else:
            web = create_app(
                f"http://127.0.0.1:{port}/",
                harness,
                Catalog(catalog_path),
                "Sungjin Shop",
                signer=signer,
            )
        emit(
            " ".join(
                (
                    f"HARNESS model={harness.model}",
                    f"source=week05-tool-loop directory={directory}",
                )
            )
        )
        await uvicorn.Server(
            uvicorn.Config(web, host="127.0.0.1", port=port, log_level="warning"),
        ).serve()


@app.command()
def seller(
    port: Annotated[int, typer.Option(min=1024, max=65535)],
    catalog: Annotated[Path, typer.Option()] = ROOT / "catalog.json",
    mode: Annotated[str, typer.Option()] = os.environ.get("AX_SELLER_MODE", "r3"),
) -> None:
    """Run the Round 3 stock seller (default) or the legacy model seller."""
    absolute = catalog.resolve()
    if mode == "legacy":

        async def legacy() -> None:
            await serve(port, absolute)

        isolated(legacy)
        return
    signer = ReceiptSigner.load(ROOT / "shop_key.pem")
    web = create_r3_app(
        f"http://127.0.0.1:{port}/", "Sungjin Shop", signer=signer, emit=emit
    )

    async def run() -> None:
        emit("SELLER mode=r3-stock model=none policy=table")
        await uvicorn.Server(
            uvicorn.Config(web, host="127.0.0.1", port=port, log_level="warning"),
        ).serve()

    isolated(run)


@app.command("r3-seller")
def r3_seller(
    config: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
) -> None:
    """Run local R3 ACP/A2A integration without joining the public market."""
    from r3.server import serve_r3

    absolute = config.resolve()

    async def run() -> None:
        await serve_r3(absolute, emit)

    isolated(run)


@app.command("directory-server")
def directory_server(
    port: Annotated[int, typer.Option(min=1024, max=65535)],
    catalog: Annotated[Path, typer.Option()] = ROOT / "catalog.json",
    first_shop_price_offset: Annotated[int, typer.Option(min=0)] = 0,
) -> None:
    """Run 20 independent A2A shops with different categories and reserves."""
    absolute = catalog.resolve()

    async def run() -> None:
        await serve(
            port,
            absolute,
            directory=True,
            first_shop_price_offset=first_shop_price_offset,
        )

    isolated(run)


@app.command()
def buyer(
    url: Annotated[str, typer.Option()],
    item: Annotated[str, typer.Option()],
    budget: Annotated[int, typer.Option(min=1)],
) -> None:
    """Run one model buyer against an independently implemented seller."""

    async def run() -> None:
        async with harness_session(emit) as harness:
            result = await buy(harness, url, item, budget, emit)
            emit("BUYER_RESULT " + result.model_dump_json())

    isolated(run)


@app.command("market-buyer")
def market_buyer(  # noqa: PLR0913 - the kit's hook arguments
    shops: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    item: Annotated[str, typer.Option()],
    budget: Annotated[int, typer.Option(min=1)],
    memory: Annotated[Path, typer.Option()] = ROOT / ".market" / "buyer-memory.json",
    *,
    order: Annotated[Path | None, typer.Option()] = None,
    mode: Annotated[str, typer.Option()] = os.environ.get("AX_BUYER_MODE", "fast"),
    promote_shop: Annotated[
        bool,
        typer.Option(help="Add one public shop introduction to the opening request."),
    ] = False,
) -> None:
    """Round 3: evidence-ranked shop, bounded negotiation, accept policy; in seconds."""
    shops_path = shops.resolve()
    memory_path = memory.resolve()
    order_path = order.resolve() if order is not None else None
    if mode != "model":

        async def fast() -> None:
            outcome = await r3_buyer.buy(shops_path, item, budget, order_path, emit)
            typer.echo(r3_buyer.result_line(outcome))

        isolated(fast)
        return

    async def run() -> None:
        async with harness_session(emit) as harness:
            result = await market_buy(
                harness,
                MarketRequest(
                    shops_path=shops_path,
                    item=item,
                    budget=budget,
                    memory_path=memory_path,
                    promote_shop=promote_shop,
                ),
                emit,
            )
        typer.echo(result.model_dump_json())

    isolated(run)


@app.command()
def restock(
    state: Annotated[Path, typer.Option(exists=True, file_okay=False)],
) -> None:
    """Print the restock plan for the kit: breadth first, then what sells."""
    folder = state.resolve()
    signal = None
    try:
        ledger_file = ledger_path(folder)
        if ledger_file.exists():
            by_category, by_sku = LedgerDB(ledger_file).demand_counts(
                read_catalog(folder), 24
            )
            signal = r3_restock.DemandSignal(by_category, by_sku)
    except (OSError, ValueError) as exc:
        emit(f"RESTOCK ledger signal unavailable: {type(exc).__name__}")
    typer.echo(r3_restock.run(folder, signal=signal))


@app.command()
def review(
    deal: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
) -> None:
    """Rate a settled deal from the market's receipt; no budget in public text."""
    typer.echo(r3_review.run(deal.resolve()))


@app.command()
def shopping(
    directory: Annotated[Path, typer.Option(exists=True)],
    shopping_input: Annotated[Path, typer.Option(exists=True)],
) -> None:
    """Run the shopping-list buyer with actual public cards and one budget."""
    cards = TypeAdapter(tuple[ShopCard, ...]).validate_json(directory.read_bytes())
    request = ShoppingInput.model_validate_json(shopping_input.read_bytes())

    async def run() -> None:
        async with harness_session(emit) as harness:
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
