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
# uv run --frozen --python 3.12.11 --env-file PRIVATE_ENV run.py
# uv run --frozen --python 3.12.11 run.py --dry-run
# ──────────────────
"""Start owned seller processes and run the precommitted Week 06 matrix."""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Final

import anyio
import httpx2
import typer
from openai import AsyncOpenAI
from pydantic import TypeAdapter

from archive import Archive
from batches import (
    Resources,
    reserve_argument,
    run_condition,
    schedule,
    validate_scenarios,
)
from http_client import create_client
from models import Condition, Config, ExperimentError, Scenario, Shop

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

ROOT: Final = Path(__file__).resolve().parent
app = typer.Typer(add_completion=False)


async def wait_ready(client: httpx2.AsyncClient, port: int) -> None:
    """Use this server's discovery endpoint rather than guess startup timing."""
    for _attempt in range(80):
        try:
            response = await client.get(
                f"http://127.0.0.1:{port}/.well-known/agent-card.json",
            )
            _ = response.raise_for_status()
        except (httpx2.ConnectError, httpx2.ConnectTimeout):
            await anyio.sleep(0.1)
        else:
            return
    raise ExperimentError("seller startup", f"port {port} did not become ready")


@asynccontextmanager
async def sellers(
    condition: Condition,
    config: Config,
    scenarios: tuple[Scenario, ...],
    output: Path,
    http: httpx2.AsyncClient,
) -> AsyncGenerator[None]:
    """Start and terminate only the two process handles owned by this invocation."""
    process_env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"}
    }
    processes: list[subprocess.Popen[bytes]] = []
    (output / "logs").mkdir(exist_ok=True)
    with ExitStack() as stack:
        try:
            for shop, name, port in (
                (Shop.CHEAP, config.cheap_name, config.cheap_port),
                (Shop.PRICEY, config.pricey_name, config.pricey_port),
            ):
                log = stack.enter_context(
                    (
                        output / "logs" / f"server-{condition.value}-{shop.value}.txt"
                    ).open("ab"),
                )
                command = [
                    sys.executable,
                    str(ROOT / "seller.py"),
                    "--name",
                    name,
                    "--port",
                    str(port),
                    "--reserve-json",
                    reserve_argument(scenarios, shop),
                    "--low-offer-limit",
                    str(config.low_offer_limit),
                ]
                if condition is Condition.INFLATED and shop is Shop.PRICEY:
                    command.append("--inflated")
                process = await anyio.to_thread.run_sync(
                    lambda command=command, log=log: subprocess.Popen(
                        command,
                        cwd=ROOT,
                        env=process_env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    ),
                )
                processes.append(process)
                await wait_ready(http, port)
                if process.poll() is not None:
                    raise ExperimentError("seller startup", "owned process exited")
            yield
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                try:
                    _ = process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    _ = process.wait(timeout=8)


async def execute(
    config: Config,
    scenarios: tuple[Scenario, ...],
    output: Path,
) -> None:
    """Avoid requiring a credential when every cell has already been archived."""
    archive = Archive(output)
    expected = schedule(config, scenarios)
    recorded = archive.completed()
    pending = [
        cell
        for cell in expected
        if (cell.run, cell.condition, cell.scenario.id) not in recorded
    ]
    if not pending:
        typer.echo(f"resume: {len(expected)} cells already recorded; 0 new model calls")
        return
    if not os.environ.get("OPENAI_API_KEY"):
        raise ExperimentError("credentials", "OPENAI_API_KEY is not set")
    async with (
        create_client() as http,
        create_client(model=True) as model_http,
        AsyncOpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=config.base_url,
            http_client=model_http,
            max_retries=0,
            timeout=90,
        ) as model,
    ):
        for condition in Condition:
            if not any(cell.condition is condition for cell in pending):
                continue
            async with sellers(condition, config, scenarios, output, http):
                await run_condition(
                    condition,
                    config,
                    scenarios,
                    Resources(http, model, archive),
                )


@app.command()
def main(
    *,
    output: Annotated[Path, typer.Option(help="Evidence directory.")] = ROOT,
    dry_run: Annotated[
        bool, typer.Option(help="Validate the matrix without network or model calls.")
    ] = False,
) -> None:
    """Execute the fixed matrix, or validate its preregistered settings."""
    config = Config.model_validate_json(
        (ROOT / "experiment_config.json").read_text(encoding="utf-8"),
    )
    scenarios = TypeAdapter(tuple[Scenario, ...]).validate_json(
        (ROOT / "scenarios.json").read_text(encoding="utf-8"),
    )
    validate_scenarios(scenarios)
    if dry_run:
        message = " ".join(
            (
                f"validated: {len(scenarios)} scenarios x 2 conditions x",
                f"{config.repetitions} repeats =",
                f"{len(schedule(config, scenarios))} episodes",
            )
        )
        typer.echo(message)
        return
    output.mkdir(parents=True, exist_ok=True)
    try:
        anyio.run(execute, config, scenarios, output)
    except ExperimentError as exc:
        raise typer.BadParameter(str(exc)) from exc


if __name__ == "__main__":
    app()
