"""Paired card-order schedule and append-only captures for the required matrix."""

from __future__ import annotations

import random
from collections.abc import Sequence
from contextlib import redirect_stdout
from pathlib import Path

import httpx2
import typer
from openai import AsyncOpenAI

from archive import Archive
from episode import Context, Episode, EpisodeRunner
from models import Condition, Config, ExperimentError, Scenario, Shop


def schedule(
    config: Config, scenarios: Sequence[Scenario],
) -> tuple[Episode, ...]:
    """Randomize order once per pair so only the card text differs across conditions."""
    rng = random.Random(config.card_order_seed)
    orders = {
        (run, scenario.id): (
            (Shop.CHEAP, Shop.PRICEY) if rng.getrandbits(1)
            else (Shop.PRICEY, Shop.CHEAP)
        )
        for run in range(1, config.repetitions + 1)
        for scenario in scenarios
    }
    return tuple(
        Episode(run, condition, scenario, orders[(run, scenario.id)])
        for condition in Condition
        for run in range(1, config.repetitions + 1)
        for scenario in scenarios
    )


def validate_scenarios(scenarios: Sequence[Scenario]) -> None:
    """Reject a matrix without both eligible-shop cases before any model call."""
    if len(scenarios) < 4 or len({s.id for s in scenarios}) != len(scenarios):
        raise ExperimentError("scenarios", "need four or more unique IDs")
    if not all(s.cheap_reserve < s.pricey_reserve for s in scenarios):
        raise ExperimentError("scenarios", "cheap must be cheaper for every item")
    if not any(s.cheap_reserve <= s.budget < s.pricey_reserve for s in scenarios):
        raise ExperimentError("scenarios", "need a cheap-only eligible scenario")
    if not any(s.pricey_reserve <= s.budget for s in scenarios):
        raise ExperimentError("scenarios", "need a both-shops eligible scenario")


async def run_condition(
    condition: Condition, config: Config, scenarios: Sequence[Scenario],
    http: httpx2.AsyncClient, model: AsyncOpenAI, archive: Archive,
) -> None:
    """Write one console file per condition/repeat and preserve each completed cell."""
    recorded = archive.completed()
    directory = archive.root / "logs"
    directory.mkdir(exist_ok=True)
    for run in range(1, config.repetitions + 1):
        path = directory / f"{condition.value}-{run:02d}.txt"
        cells = [
            cell for cell in schedule(config, scenarios)
            if cell.condition is condition and cell.run == run
            and (run, condition, cell.scenario.id) not in recorded
        ]
        if not cells:
            typer.echo(f"skip {condition.value}-{run:02d}: all cells already recorded")
            continue
        with path.open("a", encoding="utf-8") as console:
            for cell in cells:
                with redirect_stdout(console):
                    print(
                        f"[EPISODE] run={run} condition={condition.value} "
                        f"scenario={cell.scenario.id} "
                        f"card_order={'/'.join(s.value for s in cell.order)}"
                    )
                    context = Context(config, model, http, archive, print)
                    result = await EpisodeRunner(context, cell).run()
                    archive.append(result)
                    print(f"[RESULT] {result.csv_record()}")
                    console.flush()
                typer.echo(
                    f"{condition.value}-{run:02d}/{cell.scenario.id}: "
                    f"{result.chosen or '-'} {result.outcome or 'CRASH'} "
                    f"price={result.price} offers={result.turns} "
                    f"llm_calls={result.llm_calls}"
                )
                if result.outcome is None:
                    raise ExperimentError(
                        "experiment stopped", "crashed episode retained in results.csv",
                    )


def reserve_argument(scenarios: Sequence[Scenario], shop: Shop) -> str:
    """Send reserve data only to the seller process, never to the model prompt."""
    import json

    prices: dict[str, int] = {}
    for scenario in scenarios:
        amount = (
            scenario.cheap_reserve if shop is Shop.CHEAP else scenario.pricey_reserve
        )
        old = prices.setdefault(scenario.item, amount)
        if old != amount:
            raise ExperimentError("scenarios", "one item's reserve differs across rows")
    return json.dumps(prices, sort_keys=True)

