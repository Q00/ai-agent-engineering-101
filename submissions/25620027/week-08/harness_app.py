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

import importlib
import logging
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Annotated, Any, Final

import anyio
import typer

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# 11/15 09:5x: the kit starts this file once per order (market-buyer) and once per
# deal (review). Importing every command's modules took 3.9 s under the trading
# container's one CPU, about 8 s of each ~35 s deal, and the market hands us the
# next order only when the last one ends. Each command now imports what it uses;
# the names stay reachable as module attributes (tests patch them).
_LAZY: Final[dict[str, tuple[str, str | None]]] = {
    "uvicorn": ("uvicorn", None),
    "FastAPI": ("fastapi", "FastAPI"),
    "TypeAdapter": ("pydantic", "TypeAdapter"),
    "buy": ("market_a2a.buyer", "buy"),
    "Catalog": ("market_a2a.catalog", "Catalog"),
    "MarketRequest": ("market_a2a.market_buyer", "MarketRequest"),
    "market_buy": ("market_a2a.market_buyer", "market_buy"),
    "harness_session": ("market_a2a.model_session", "harness_session"),
    "FrozenModel": ("market_a2a.models", "FrozenModel"),
    "Purchase": ("market_a2a.models", "Purchase"),
    "ShopCard": ("market_a2a.models", "ShopCard"),
    "ShoppingItem": ("market_a2a.models", "ShoppingItem"),
    "ReceiptSigner": ("market_a2a.receipts", "ReceiptSigner"),
    "create_app": ("market_a2a.seller", "create_app"),
    "shop": ("market_a2a.shopping", "shop"),
    "r3_buyer": ("r3.buyer", None),
    "r3_restock": ("r3.restock", None),
    "r3_review": ("r3.review", None),
    "LedgerDB": ("r3.ledger_db", "LedgerDB"),
    "ledger_path": ("r3.ledger_db", "ledger_path"),
    "read_catalog": ("r3.market_files", "read_catalog"),
    "load_picture": ("r3.picture", "load_picture"),
    "picture_path": ("r3.picture", "picture_path"),
    "create_r3_app": ("r3.seller", "create_r3_app"),
}


def __getattr__(name: str) -> object:
    """Import a command's dependency on first use (PEP 562)."""
    try:
        module_name, attribute = _LAZY[name]
    except KeyError:
        raise AttributeError(name) from None
    module = importlib.import_module(module_name)
    value = module if attribute is None else getattr(module, attribute)
    globals()[name] = value
    return value


def _dep(name: str) -> Any:  # noqa: ANN401 - a lazily imported module or object
    """Return a dependency, honouring a test's patch of the module attribute."""
    return getattr(sys.modules[__name__], name)


ROOT: Final = Path(__file__).resolve().parent
KINDS: Final = ("lamp", "chair", "bookshelf", "desk", "storage")
SHOP_COUNT: Final = 20
LOGGER: Final = logging.getLogger("ax06")
app = typer.Typer(add_completion=False)


def _shopping_input() -> Any:  # noqa: ANN401 - a pydantic model class
    """Build the shopping input: requested descriptions and one total budget."""
    frozen = _dep("FrozenModel")
    item = _dep("ShoppingItem")

    class ShoppingInput(frozen):  # type: ignore[misc, valid-type]
        budget: int
        items: tuple[item, ...]  # type: ignore[valid-type]

    return ShoppingInput


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
    signer = _dep("ReceiptSigner").load(ROOT / "shop_key.pem")
    async with _dep("harness_session")(emit) as harness:
        if directory:
            web = _dep("FastAPI")()
            for index in range(SHOP_COUNT):
                base = f"http://127.0.0.1:{port}/shops/{index:02d}/"
                kind = KINDS[index % len(KINDS)]
                offset = index * 5 + (first_shop_price_offset if index == 0 else 0)
                catalog = _dep("Catalog")(
                    catalog_path, price_offset=offset, kinds=(kind,)
                )
                child = _dep("create_app")(
                    base, harness, catalog, f"shop-{index:02d}-{kind}", signer=signer
                )
                web.mount(f"/shops/{index:02d}", child)
        else:
            web = _dep("create_app")(
                f"http://127.0.0.1:{port}/",
                harness,
                _dep("Catalog")(catalog_path),
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
        await (
            _dep("uvicorn")
            .Server(
                _dep("uvicorn").Config(
                    web, host="127.0.0.1", port=port, log_level="warning"
                ),
            )
            .serve()
        )


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
    signer = _dep("ReceiptSigner").load(ROOT / "shop_key.pem")
    web = _dep("create_r3_app")(
        f"http://127.0.0.1:{port}/", "Sungjin Shop", signer=signer, emit=emit
    )

    async def run() -> None:
        emit("SELLER mode=r3-stock model=none policy=table")
        await (
            _dep("uvicorn")
            .Server(
                _dep("uvicorn").Config(
                    web, host="127.0.0.1", port=port, log_level="warning"
                ),
            )
            .serve()
        )

    isolated(run)


@app.command("r3-seller")
def r3_seller(
    config: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
) -> None:
    """Run local R3 ACP/A2A integration without joining the public market."""
    from r3.server import serve_r3  # noqa: PLC0415 - keep server-only imports off hook startup

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
        async with _dep("harness_session")(emit) as harness:
            result = await _dep("buy")(harness, url, item, budget, emit)
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
            outcome = await _dep("r3_buyer").buy(
                shops_path, item, budget, order_path, emit
            )
            typer.echo(_dep("r3_buyer").result_line(outcome))

        isolated(fast)
        return

    async def run() -> None:
        async with _dep("harness_session")(emit) as harness:
            result = await _dep("market_buy")(
                harness,
                _dep("MarketRequest")(
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
    picture = _dep("load_picture")(_dep("picture_path")(folder))
    try:
        ledger_file = _dep("ledger_path")(folder)
        by_category: dict[str, int] = {}
        by_sku: dict[str, int] = {}
        if ledger_file.exists():
            by_category, by_sku = _dep("LedgerDB")(ledger_file).demand_counts(
                _dep("read_catalog")(folder), 24
            )
        if ledger_file.exists() or picture.hot_categories:
            signal = _dep("r3_restock").DemandSignal(
                by_category,
                by_sku,
                frozenset(picture.hot_categories),
                frozenset(picture.cold_categories),
            )
    except (OSError, ValueError) as exc:
        emit(f"RESTOCK ledger signal unavailable: {type(exc).__name__}")
    typer.echo(
        _dep("r3_restock").run(
            folder,
            signal=signal,
            shop_url=os.environ.get("AX_R3_SHOP_URL", ""),
        )
    )


@app.command()
def review(
    deal: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
) -> None:
    """Rate a settled deal from the market's receipt; no budget in public text."""
    typer.echo(_dep("r3_review").run(deal.resolve()))


@app.command()
def shopping(
    directory: Annotated[Path, typer.Option(exists=True)],
    shopping_input: Annotated[Path, typer.Option(exists=True)],
) -> None:
    """Run the shopping-list buyer with actual public cards and one budget."""
    cards = _dep("TypeAdapter")(tuple[_dep("ShopCard"), ...]).validate_json(
        directory.read_bytes()
    )
    request = _shopping_input().model_validate_json(shopping_input.read_bytes())

    async def run() -> None:
        async with _dep("harness_session")(emit) as harness:
            purchases = await _dep("shop")(
                harness, cards, request.items, request.budget, emit
            )
            emit(
                "SHOPPING_RESULT "
                + _dep("TypeAdapter")(tuple[_dep("Purchase"), ...])
                .dump_json(purchases)
                .decode()
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
