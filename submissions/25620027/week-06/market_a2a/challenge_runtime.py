"""Owned processes and loopback discovery for live challenge attempts."""

from __future__ import annotations

import socket
import subprocess
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import anyio
import httpx2
from pydantic import TypeAdapter

from http_client import create_client
from market_a2a.challenges import require
from market_a2a.remote import Card

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from anyio.abc import Process


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
async def owned(
    command: list[str], log: Path, working_directory: Path
) -> AsyncGenerator[Process]:
    """Preserve raw stdout/stderr and clean up even when validation raises."""
    with log.open("xb") as output:
        process = await anyio.open_process(
            command,
            cwd=working_directory,
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
