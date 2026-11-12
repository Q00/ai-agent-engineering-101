"""Demonstrate evidence-verified feedback without model or market access."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import JsonValue

from market_a2a.memory_journal import MemoryJournal
from market_a2a.memory_models import Trade
from market_a2a.meta_profile import (
    FeedbackBatch,
    Focus,
    OperatorFeedback,
    ProfileHistory,
    ProfilePaths,
    profile_context,
    refresh_profile,
)
from market_a2a.models import FrozenModel, HarnessError, Purchase


class DemoResult(FrozenModel):
    """Report deterministic policy-input changes, without claiming model gains."""

    synthetic_observation: Literal[True] = True
    model_calls: Literal[0] = 0
    market_actions: Literal[0] = 0
    first_profile: ProfileHistory
    second_profile: ProfileHistory
    buyer_context: dict[str, JsonValue]
    altered_evidence_rejected: bool
    previous_profile_preserved: bool
    behavior_or_profit_evaluated: Literal[False] = False


def run_demo(output: Path) -> DemoResult:
    """Write a fresh isolated example and refuse to replace prior evidence."""
    output = Path(output).absolute()
    if output.exists():
        message = "Use a new output directory; prior demo evidence is preserved"
        raise HarnessError(message)
    output.mkdir(parents=True)
    journal = MemoryJournal(output / "demo-only.sqlite")
    trade = Trade(
        item="lamp",
        budget=120,
        selection_reason="Synthetic documentation example, not a market order",
        observed_at=datetime.now(UTC),
        purchase=Purchase(
            completed=True,
            item="lamp",
            shop="http://demo.invalid",
            price=110,
            receipt={"task": "synthetic-example", "item": "lamp", "price": 110},
        ),
    )
    journal.record("synthetic-demo-shop", trade)
    paths = ProfilePaths(output / "feedback.json", output / "profile.json")
    feedback = OperatorFeedback(
        source="operator",
        event_id=trade.event_id,
        evidence_sha256=hashlib.sha256(trade.model_dump_json().encode()).hexdigest(),
        verdict="bad",
        focus=Focus.PRICE,
    )
    _ = paths.feedback.write_text(
        FeedbackBatch(entries=(feedback,)).model_dump_json()
    )
    _ = refresh_profile(journal, paths)
    first = ProfileHistory.model_validate_json(paths.profile.read_bytes())
    _ = paths.feedback.write_text(
        FeedbackBatch(
            entries=(feedback.model_copy(update={"focus": Focus.TURNS}),)
        ).model_dump_json()
    )
    _ = refresh_profile(journal, paths)
    second = ProfileHistory.model_validate_json(paths.profile.read_bytes())
    original = os.environ.get("AX_META_PROFILE")
    try:
        os.environ["AX_META_PROFILE"] = str(paths.profile)
        context = profile_context()
    finally:
        if original is None:
            _ = os.environ.pop("AX_META_PROFILE", None)
        else:
            os.environ["AX_META_PROFILE"] = original
    invalid = output / "altered-feedback.json"
    _ = invalid.write_text(
        FeedbackBatch(
            entries=(feedback.model_copy(update={"evidence_sha256": "0" * 64}),)
        ).model_dump_json()
    )
    prior = paths.profile.read_bytes()
    rejected = False
    try:
        _ = refresh_profile(journal, ProfilePaths(invalid, paths.profile))
    except HarnessError:
        rejected = True
    result = DemoResult(
        first_profile=first,
        second_profile=second,
        buyer_context=context,
        altered_evidence_rejected=rejected,
        previous_profile_preserved=paths.profile.read_bytes() == prior,
    )
    _ = (output / "result.json").write_text(result.model_dump_json(indent=2))
    return result


def main(output: Annotated[Path, typer.Option()]) -> None:
    """Run the example with an explicit new output directory."""
    typer.echo(run_demo(output).model_dump_json(indent=2))


if __name__ == "__main__":
    typer.run(main)
