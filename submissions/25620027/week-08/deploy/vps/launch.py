"""Prepare files locally; market entry additionally requires two activation gates."""

from __future__ import annotations

import json
import os
import shlex
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Final

import anyio
import typer

from deploy.vps.settings import LaunchSettings, verify_kit
from market_a2a.models import HarnessError

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
                    "--memory",
                    str(settings.run_directory / "buyer-memory.json"),
                ]
            ),
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


async def activate(settings: LaunchSettings, app: Path = APP) -> int:
    """No market requests occur unless the explicit activation flag is present."""
    if os.environ.get("AX_R3_ACTIVATE") != "1":
        message = "Set AX_R3_ACTIVATE=1 only after approving the R3 cutover"
        raise HarnessError(message)
    if os.environ.get("AX_MODEL_PROVIDER") != "openrouter-free":
        message = "Prepared deployment requires the explicit free OpenRouter route"
        raise HarnessError(message)
    if os.environ.get("AX_OPENAI_FALLBACK", "0") != "0":
        message = "Paid fallback needs a separate cutover review and shared quota mount"
        raise HarnessError(message)
    if not os.environ.get("MARKET_OWNER") or not os.environ.get("OPENROUTER_API_KEY"):
        message = "Private market owner and OpenRouter environment are required"
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
        "PYTHONPATH": str(app),
    }
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
    ) as worker:
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
