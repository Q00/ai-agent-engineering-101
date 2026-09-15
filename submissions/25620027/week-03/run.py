#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["openai==3.8.0", "pydantic==2.13.5", "typer==0.27.2"]
# ///
# ─── How to run ───
# Install uv, then run: ./run_lab.sh --runs 3
# Or with an exported key: uv run --frozen python run.py --runs 3
# ──────────────────
"""Run the frozen Contract Net experiment and retain every attempt."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer
from openai import OpenAI

from cnp.adapter import ChatAdapter
from cnp.archive import prepare
from cnp.batch import Batch
from cnp.settings import InputError, load_inputs

ROOT = Path(__file__).resolve().parent


def main(
    output: Annotated[
        Path, typer.Option(help="Evidence destination; existing matching experiments resume.")
    ] = ROOT,
    runs: Annotated[int, typer.Option(min=1, help="Completed repetitions per condition.")] = 3,
    allow_paid: Annotated[
        bool, typer.Option(help="Explicitly permit a non-free model in config.json.")
    ] = False,
) -> None:
    """Run baseline, homogeneous and overconfident under one frozen configuration."""
    settings, tasks = load_inputs(ROOT)
    if not settings.model.endswith(":free") and not allow_paid:
        msg = "Paid model requires explicit --allow-paid."
        raise typer.BadParameter(msg)
    if not os.environ.get("OPENAI_API_KEY"):
        msg = "OPENAI_API_KEY is not set; use an existing private env file."
        raise typer.BadParameter(msg)
    output.mkdir(parents=True, exist_ok=True)
    lock = output / ".run.lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        msg = "Another run owns .run.lock; do not run the experiment twice."
        raise typer.BadParameter(msg) from exc
    try:
        prepare(ROOT, output, settings)
        with OpenAI(
            base_url=settings.base_url, max_retries=0, timeout=settings.request_timeout_s
        ) as client:
            complete = Batch(output, settings, tasks, ChatAdapter(client, settings)).run(runs)
        if not complete:
            raise typer.Exit(2)
    except InputError as exc:
        raise typer.BadParameter(exc.code) from exc
    finally:
        lock.rmdir()


if __name__ == "__main__":
    typer.run(main)
