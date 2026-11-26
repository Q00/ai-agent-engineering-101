"""Launch our seller with a local-only ACP checkout gateway."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final

import anyio
import uvicorn
from pydantic import Field

from market_a2a.catalog import Catalog
from market_a2a.model_session import harness_session
from market_a2a.models import FrozenModel
from market_a2a.receipts import ReceiptSigner
from market_a2a.seller import create_app
from r3.gateway import CheckoutBinding, create_gateway, require_local
from r3.transport import SettlementTransport, create_settlement_client

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT: Final = Path(__file__).resolve().parents[1]


class LocalSettings(FrozenModel):
    """One local merchant endpoint and its private settlement configuration."""

    port: int = Field(ge=1024, le=65535)
    market_url: str
    shop_token_file: Path
    shop_key_file: Path


async def serve_r3(settings_path: Path, emit: Callable[[str], None]) -> None:
    """Run only against an explicitly supplied local market and existing test key."""
    settings = LocalSettings.model_validate_json(
        await anyio.Path(settings_path).read_bytes(),
    )
    require_local(settings.market_url)
    key = await anyio.to_thread.run_sync(ReceiptSigner.load, settings.shop_key_file)
    token = (await anyio.Path(settings.shop_token_file).read_text()).strip()
    public = f"http://127.0.0.1:{settings.port}"
    with create_settlement_client(settings.market_url) as client:
        async with harness_session(emit) as harness:
            seller = create_app(
                public + "/",
                harness,
                Catalog(ROOT / "catalog.json"),
                "Sungjin Shop",
                signer=key,
            )
            gateway = create_gateway(
                seller,
                CheckoutBinding(public, key.key, SettlementTransport(client, token)),
            )
            await uvicorn.Server(
                uvicorn.Config(
                    gateway,
                    host="127.0.0.1",
                    port=settings.port,
                    log_level="warning",
                )
            ).serve()
