#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx2[http2,brotli,zstd]>2,<3",
#   "mcp>=2,<3",
#   "openai==3.8.0",
#   "pydantic>=2.12,<3",
#   "typer>=0.16,<1",
#   "uvicorn>=0.35,<1",
# ]
# ///
"""Start the local MCP market and run the resumable Week 05 experiment."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Final

import anyio
import httpx2
import typer
from openai import AsyncOpenAI

from market.admin_client import AdminClient
from market.archive import ResultArchive
from market.auth_checks import capture_auth_checks
from market.experiment_plan import ExperimentPlan, select_experiment_plan
from market.http_client import create_async_client
from market.models import HostPolicy
from market.runner import ExperimentRunner, RunnerContext
from market.settings import load_config, load_scenarios

ROOT: Final = Path(__file__).resolve().parent
SERVER_PORT: Final = 8001
SERVER_BASE: Final = f"http://127.0.0.1:{SERVER_PORT}"
HTTP_OK: Final = 200
app = typer.Typer(add_completion=False, no_args_is_help=False)


async def _wait_for_server() -> None:
    async with create_async_client(base_url=SERVER_BASE) as client:
        for _attempt in range(40):
            try:
                response = await client.get("/health")
            except httpx2.ConnectError:
                await anyio.sleep(0.1)
                continue
            if response.status_code == HTTP_OK:
                return
            await anyio.sleep(0.1)
    message = "market server did not become ready"
    raise RuntimeError(message)


def read_api_key(env_file: Path | None) -> str:
    """Read only OPENAI_API_KEY from a user-selected private env file."""
    current = os.environ.get("OPENAI_API_KEY", "")
    if current or env_file is None:
        return current
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if line.startswith("OPENAI_API_KEY="):
            return line.split("=", maxsplit=1)[1].strip().strip('"').strip("'")
    return ""


async def _run(output: Path, plan: ExperimentPlan, env_file: Path | None) -> None:
    config = load_config(ROOT / "config.json")
    api_key = read_api_key(env_file)
    if not api_key:
        message = "OPENAI_API_KEY is not set; source the existing private env file"
        raise RuntimeError(message)
    admin_secret = os.environ["MIRROR_MARKET_ADMIN_SECRET"]
    async with create_async_client(base_url=SERVER_BASE) as admin_http:
        admin = AdminClient(admin_http, admin_secret)
        await capture_auth_checks(SERVER_BASE, admin, output / "auth_checks.txt")
        async with create_async_client(long_read=True) as model_http, AsyncOpenAI(
            api_key=api_key,
            base_url=config.base_url,
            timeout=config.request_timeout_s,
            max_retries=0,
            http_client=model_http,
        ) as model:
            context = RunnerContext(
                config=config,
                scenarios=plan.scenarios,
                admin=admin,
                model=model,
                archive=ResultArchive(
                    output,
                    extension_root=(
                        output
                        if plan.host_policy is HostPolicy.CLOSURE_AWARE
                        else None
                    ),
                ),
                server_base=SERVER_BASE,
                host_policy=plan.host_policy,
            )
            await ExperimentRunner(context).run(plan.conditions)


@app.command()
def main(  # noqa: PLR0913, PLR0917 - Typer exposes one parameter per CLI option.
    output: Annotated[Path, typer.Option(help="Evidence directory.")] = ROOT,
    all_conditions: Annotated[
        bool,
        typer.Option(
            help="Run prompt and server controls in addition to required injection cells."
        ),
    ] = False,
    liveness_extension: Annotated[
        bool,
        typer.Option(
            help="Run the isolated 18-episode closure-aware follow-up study."
        ),
    ] = False,
    allow_paid: Annotated[
        bool,
        typer.Option(help="Explicitly permit the configured paid model."),
    ] = False,
    env_file: Annotated[
        Path | None,
        typer.Option(help="Private env file containing OPENAI_API_KEY."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(help="Validate the matrix without starting a server or calling a model."),
    ] = False,
) -> None:
    """Run the required matrix, controls, or the isolated liveness follow-up."""
    config = load_config(ROOT / "config.json")
    scenarios = load_scenarios(ROOT / "scenarios.json")
    try:
        plan = select_experiment_plan(
            scenarios,
            all_conditions=all_conditions,
            liveness_extension=liveness_extension,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    cells = len(plan.conditions)
    episodes = cells * len(plan.scenarios) * config.repetitions
    if dry_run:
        parts = [
            f"validated: {len(plan.scenarios)} scenarios x {cells} conditions x",
            f"{config.repetitions} repeats = {episodes} episodes;",
            f"host_policy={plan.host_policy.value}",
        ]
        summary = " ".join(parts)
        typer.echo(summary)
        return
    if not config.model.endswith(":free") and not allow_paid:
        message = "configured model is paid; rerun with --allow-paid"
        raise typer.BadParameter(message)
    if liveness_extension and output == ROOT:
        output = ROOT / "extension" / "liveness"
    output.mkdir(parents=True, exist_ok=True)
    lock = output / ".run.lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        message = "another experiment process owns .run.lock"
        raise typer.BadParameter(message) from exc
    token_secret = secrets.token_urlsafe(32)
    admin_secret = secrets.token_urlsafe(32)
    server_env = os.environ.copy()
    server_env.update(
        {
            "MIRROR_MARKET_TOKEN_SECRET": token_secret,
            "MIRROR_MARKET_ADMIN_SECRET": admin_secret,
        }
    )
    os.environ["MIRROR_MARKET_ADMIN_SECRET"] = admin_secret
    server_log = output / "logs" / "server.txt"
    server_log.parent.mkdir(parents=True, exist_ok=True)
    with server_log.open("a", encoding="utf-8") as handle:
        process = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "market.server", "--port", str(SERVER_PORT)],
            cwd=ROOT,
            env=server_env,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
        try:
            anyio.run(_wait_for_server)
            anyio.run(_run, output, plan, env_file)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            lock.rmdir()
    typer.echo("experiment matrix complete")


if __name__ == "__main__":
    app()
