"""Run the pinned v4 kit with a narrow incoming ACP validation boundary."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, override

from scripts import market_kit as kit

from deploy.vps.settings import verify_kit
from market_a2a.models import HarnessError
from r3.boundary import validate_checkout

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response


class GuardedProxy(kit.Proxy):
    """Retain the official proxy, role tokens and A2A card rewriting unchanged."""

    @override
    async def handle(self, req: Request) -> Response:
        """Reject invalid ACP routes before any session or settlement mutation."""
        if self.acp is not None and (
            req.url.path == "/.well-known/acp.json" or req.url.path.startswith("/acp/")
        ):
            rejected = await validate_checkout(req)
            if rejected is not None:
                return rejected
        return await super().handle(req)


def main() -> None:
    """Keep source bytes pinned; patch only the proxy's request boundary in memory."""
    if os.environ.get("AX_R3_ACTIVATE") != "1":
        message = "R3 market entry is disabled until explicit cutover activation"
        raise HarnessError(message)
    verify_kit(Path(kit.__file__).resolve().parents[1])
    kit.Proxy = GuardedProxy
    kit.main()


if __name__ == "__main__":
    main()
