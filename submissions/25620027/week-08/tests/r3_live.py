"""Own isolated loopback servers with an explicit startup event."""

from __future__ import annotations

import socket
from contextlib import asynccontextmanager
from functools import partial
from typing import TYPE_CHECKING, override

import anyio
import uvicorn

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from starlette.types import ASGIApp


class ReadyServer(uvicorn.Server):
    """Publish readiness after uvicorn finishes binding the supplied socket."""

    def __init__(self, config: uvicorn.Config) -> None:
        """Own a fresh startup event for this server instance."""
        super().__init__(config)
        self.ready: anyio.Event = anyio.Event()

    @override
    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets)
        self.ready.set()


@asynccontextmanager
async def live_app(build: Callable[[str], ASGIApp]) -> AsyncGenerator[str]:
    """Use a fresh OS-selected port and stop all owned tasks on exit."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = ReadyServer(
            uvicorn.Config(
                build(url),
                log_level="warning",
                lifespan="off",
                ws="none",
                timeout_graceful_shutdown=1,
            )
        )
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(partial(server.serve, sockets=[listener]))
            await server.ready.wait()
            try:
                yield url
            finally:
                server.should_exit = True
