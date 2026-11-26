#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["openai==3.8.0", "pydantic>=2.12,<3", "typer>=0.16,<1"]
# ///
"""Validate or run the preregistered Week 04 hybrid extension."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Annotated

import typer
from openai import OpenAI

from acl_lab.adapter import OpenAITransport, RetryingModel
from acl_lab.hybrid_archive import HybridArchive
from acl_lab.hybrid_batch import HybridBatch
from acl_lab.settings import InputError, load_inputs

ROOT = Path(__file__).resolve().parent
app = typer.Typer(add_completion=False, no_args_is_help=False)


@app.command()
def main(
    output: Annotated[
        Path,
        typer.Option(help="Separate evidence directory for the hybrid extension."),
    ] = ROOT / "extension",
    config: Annotated[
        Path,
        typer.Option(help="Frozen base model and runtime settings."),
    ] = ROOT / "config.json",
    dry_run: Annotated[
        bool,
        typer.Option(help="Validate the extension matrix without API calls."),
    ] = False,
    allow_paid: Annotated[
        bool,
        typer.Option(help="Explicitly permit the configured non-free model."),
    ] = False,
) -> None:
    """Run the state-routed hybrid protocol on the four frozen scenarios."""
    try:
        settings, scenarios = load_inputs(ROOT, config)
    except InputError as exc:
        raise typer.BadParameter(exc.code) from exc

    episodes = len(scenarios) * settings.repetitions
    if dry_run:
        summary = " ".join(
            (
                f"validated: {len(scenarios)} scenarios",
                f"x {settings.repetitions} repeats",
                f"= {episodes} hybrid episodes",
            )
        )
        typer.echo(summary)
        return
    if not settings.model.endswith(":free") and not allow_paid:
        msg = "Paid model requires explicit --allow-paid."
        raise typer.BadParameter(msg)
    if not os.environ.get("OPENAI_API_KEY"):
        msg = "OPENAI_API_KEY is not set; use an existing private env file."
        raise typer.BadParameter(msg)

    output.mkdir(parents=True, exist_ok=True)
    lock = output / ".hybrid-run.lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        msg = "Another process owns .hybrid-run.lock; do not run the extension twice."
        raise typer.BadParameter(msg) from exc

    try:
        archive = HybridArchive(output)
        with OpenAI(
            base_url=settings.base_url,
            max_retries=0,
            timeout=settings.request_timeout_s,
        ) as client:
            transport = OpenAITransport(client, settings)
            model = RetryingModel(transport, settings.retry_delays_s, time.sleep)
            complete = HybridBatch(archive, settings, scenarios, model).run()
        if not complete:
            raise typer.Exit(2)
    finally:
        lock.rmdir()


if __name__ == "__main__":
    app()
