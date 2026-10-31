"""Prepare files locally; market entry additionally requires two activation gates."""

from __future__ import annotations

import json
import os
import shlex
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Final, assert_never

import anyio
import typer

from deploy.vps.settings import LaunchSettings, verify_kit
from market_a2a.anthropic_fallback import HAIKU_MODEL
from market_a2a.models import HarnessError
from market_a2a.paid_fallback import DailyQuota, configured_paid_provider

if TYPE_CHECKING:
    from anyio.abc import Process

APP: Final = Path(__file__).resolve().parents[2]
KEYS: Final = ("shop_key.pem", "buyer_key.pem")


def prepare(settings: LaunchSettings, app: Path) -> None:
    """Reuse existing key paths; never generate a replacement market identity."""
    verify_kit(app)
    for name in KEYS:
        if not (settings.private_directory / name).is_file():
            message = f"Existing private identity file is required: {name}"
            raise HarnessError(message)
    settings.run_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    settings.run_directory.chmod(0o700)
    (settings.run_directory / "comparison-cache").mkdir(mode=0o700, exist_ok=True)
    for name in KEYS:
        target = settings.run_directory / name
        source = settings.private_directory / name
        if target.is_symlink() and target.resolve() == source.resolve():
            continue
        if target.exists() or target.is_symlink():
            message = "Refusing to replace an existing identity path"
            raise HarnessError(message)
        target.symlink_to(source)
    commands = {
        "seller": {
            "cmd": shlex.join(
                ["python", str(app / "harness_app.py"), "seller", "--port", "{port}"]
            )
        },
        "buyer": {
            "cmd": shlex.join(
                [
                    "python",
                    str(app / "harness_app.py"),
                    "buyer",
                    "--url",
                    "{url}",
                    "--item",
                    "{item}",
                    "--budget",
                    "{budget}",
                ]
            )
        },
        "market_buyer": {
            "cmd": shlex.join(
                [
                    "python",
                    str(app / "harness_app.py"),
                    "market-buyer",
                    "--shops",
                    "{shops}",
                    "--item",
                    "{item}",
                    "--budget",
                    "{budget}",
                    "--order",
                    "{order}",
                    "--memory",
                    str(settings.run_directory / "buyer-memory.json"),
                ]
            ),
        },
        "restock": {
            "cmd": shlex.join(
                ["python", str(app / "harness_app.py"), "restock", "--state", "{state}"]
            )
        },
        "review": {
            "cmd": shlex.join(
                ["python", str(app / "harness_app.py"), "review", "--deal", "{deal}"]
            )
        },
    }
    _ = (settings.run_directory / "agent.json").write_text(json.dumps(commands))


async def stop(process: Process) -> None:
    """Give the instructor kit time to stop its own seller child process."""
    with anyio.CancelScope(shield=True):
        if process.returncode is None:
            process.terminate()
        with anyio.move_on_after(10):
            _ = await process.wait()
        if process.returncode is None:
            process.kill()
            _ = await process.wait()


def validate_paid_launch(settings: LaunchSettings) -> None:
    """Permit only the authorized Haiku route with one existing shared ledger."""
    provider = configured_paid_provider()
    match provider:
        case "none":
            return
        case "anthropic":
            if (
                not os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_MODEL", HAIKU_MODEL) != HAIKU_MODEL
            ):
                message = "Approved Haiku API key and model configuration are required"
                raise HarnessError(message)
            quota = settings.run_directory / "paid-quota.sqlite3"
            if (
                os.environ.get("AX_PAID_QUOTA_PATH") != str(quota)
                or not quota.is_file()
            ):
                message = "Existing shared R3 paid quota ledger is required"
                raise HarnessError(message)
            _ = DailyQuota(
                quota, int(os.environ.get("AX_PAID_DAILY_LIMIT", "5000"))
            )
        case "openai":
            message = "This deployment authorizes Haiku fallback only"
            raise HarnessError(message)
        case _:
            assert_never(provider)


async def activate(settings: LaunchSettings, app: Path = APP) -> int:
    """No market requests occur unless the explicit activation flag is present."""
    if os.environ.get("AX_R3_ACTIVATE") != "1":
        message = "Set AX_R3_ACTIVATE=1 only after approving the R3 cutover"
        raise HarnessError(message)
    legacy_model = (
        os.environ.get("AX_BUYER_MODE") == "model"
        or os.environ.get("AX_SELLER_MODE") == "legacy"
    )
    if legacy_model and os.environ.get("AX_MODEL_PROVIDER") != "openrouter-free":
        message = "Legacy model agents require the explicit free OpenRouter route"
        raise HarnessError(message)
    validate_paid_launch(settings)
    if not os.environ.get("MARKET_OWNER"):
        message = "Private market owner environment is required"
        raise HarnessError(message)
    if legacy_model and not os.environ.get("OPENROUTER_API_KEY"):
        message = "Legacy model agents require the OpenRouter environment"
        raise HarnessError(message)
    _ = settings.seconds_remaining(datetime.now(UTC))
    await anyio.to_thread.run_sync(prepare, settings, app)
    seconds = settings.seconds_remaining(datetime.now(UTC))
    journal = settings.run_directory / "memory.sqlite"
    environment = {
        **os.environ,
        "AX_MEMORY_JOURNAL": str(journal),
        "AX_META_PROFILE": str(settings.run_directory / "meta-profile.json"),
        "AX_MARKET_ROUND": "3",
        "AX_R3_POLICY": str(settings.run_directory / "r3-policy.json"),
        "AX_R3_EVENTS": str(settings.run_directory / "r3-events.sqlite"),
        "AX_R3_LEDGER": str(settings.run_directory / "r3-ledger.sqlite"),
        "PYTHONPATH": str(app),
    }
    manager = (
        sys.executable,
        "-m",
        "r3.manager",
        "--dir",
        str(settings.run_directory),
        "--market",
        settings.market_url,
        "--shop-url",
        settings.public_url,
        "--buyer-name",
        "25620027",
    )
    recorder = (
        sys.executable,
        str(app / "r3" / "recorder.py"),
        "--journal",
        str(journal),
        "--snapshot",
        str(settings.run_directory / "buyer-memory.json"),
    )
    recorder_environment = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "PYTHONPATH": str(app),
        "PYTHONUNBUFFERED": "1",
        "AX_META_FEEDBACK": str(settings.private_directory / "human-feedback.json"),
        "AX_META_PROFILE": str(settings.run_directory / "meta-profile.json"),
    }
    async with await anyio.open_process(
        recorder,
        stdout=None,
        stderr=None,
        env=recorder_environment,
    ) as worker, await anyio.open_process(
        manager,
        cwd=settings.run_directory,
        stdout=None,
        stderr=None,
        env=environment,
    ) as policy_loop:
        try:
            async with await anyio.open_process(
                settings.command(app),
                cwd=settings.run_directory,
                stdout=None,
                stderr=None,
                env=environment,
            ) as process:
                try:
                    code = 0
                    with anyio.move_on_after(seconds) as deadline:
                        code = await process.wait()
                    return 0 if deadline.cancel_called else code
                finally:
                    await stop(process)
        finally:
            await stop(policy_loop)
            await stop(worker)


def main(
    config: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    *,
    start: Annotated[bool, typer.Option("--activate")] = False,
) -> None:
    """Default to a credential-free configuration check, not market participation."""
    settings = LaunchSettings.model_validate_json(config.read_bytes())
    _ = settings.seconds_remaining(datetime.now(UTC))
    verify_kit(APP)
    if start:
        raise SystemExit(anyio.run(activate, settings))
    typer.echo("R3 configuration and kit hash valid; no activation or network requests")


if __name__ == "__main__":
    typer.run(main)
