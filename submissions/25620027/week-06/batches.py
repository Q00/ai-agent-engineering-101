"""Paired discovery order and append-only captures for the required matrix."""

from __future__ import annotations

import json
import random
from contextlib import redirect_stdout
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import typer

from episode import Context, Episode, EpisodeRunner
from models import Condition, Config, ExperimentError, Scenario, Shop

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx2
    from openai import AsyncOpenAI

    from archive import Archive

MIN_SCENARIOS: Final = 4


@dataclass(frozen=True, slots=True)
class Resources:
    """Reuse runner-owned handles while every episode gets a fresh conversation."""

    http: httpx2.AsyncClient
    model: AsyncOpenAI
    archive: Archive


def schedule(
    config: Config,
    scenarios: Sequence[Scenario],
) -> tuple[Episode, ...]:
    """Draw order once per pair; condition never changes order or private state."""
    rng = random.Random(config.card_order_seed)
    orders = {
        (run, scenario.id): (
            (Shop.CHEAP, Shop.PRICEY)
            if rng.getrandbits(1)
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
    """Check eligibility cases before any network or paid model call."""
    if len(scenarios) < MIN_SCENARIOS or len({s.id for s in scenarios}) != len(
        scenarios
    ):
        raise ExperimentError("scenarios", "need four or more unique IDs")
    if not all(s.cheap_reserve < s.pricey_reserve for s in scenarios):
        raise ExperimentError("scenarios", "cheap must be cheaper for every item")
    if not any(s.cheap_reserve <= s.budget < s.pricey_reserve for s in scenarios):
        raise ExperimentError("scenarios", "need a cheap-only eligible scenario")
    if not any(s.pricey_reserve <= s.budget for s in scenarios):
        raise ExperimentError("scenarios", "need a both-shops eligible scenario")


async def run_condition(
    condition: Condition,
    config: Config,
    scenarios: Sequence[Scenario],
    resources: Resources,
) -> None:
    """Capture one file per run and persist each cell immediately for resume."""
    archive = resources.archive
    recorded = archive.completed()
    directory = archive.root / "logs"
    directory.mkdir(exist_ok=True)
    for run in range(1, config.repetitions + 1):
        path = directory / f"{condition.value}-{run:02d}.txt"
        cells = [
            cell
            for cell in schedule(config, scenarios)
            if cell.condition is condition
            and cell.run == run
            and (run, condition, cell.scenario.id) not in recorded
        ]
        if not cells:
            typer.echo(f"skip {condition.value}-{run:02d}: all cells already recorded")
            continue
        with path.open("a", encoding="utf-8") as console:
            for cell in cells:
                with redirect_stdout(console):
                    print(
                        " ".join(
                            (
                                f"[EPISODE] run={run} condition={condition.value}",
                                f"scenario={cell.scenario.id}",
                                f"card_order={'/'.join(s.value for s in cell.order)}",
                            )
                        )
                    )
                    context = Context(
                        config,
                        resources.model,
                        resources.http,
                        archive,
                        print,
                    )
                    result = await EpisodeRunner(context, cell).run()
                    archive.append(result)
                    print(f"[RESULT] {result.csv_record()}")
                    console.flush()
                typer.echo(
                    " ".join(
                        (
                            f"{condition.value}-{run:02d}/{cell.scenario.id}:",
                            f"{result.chosen or '-'} {result.outcome or 'CRASH'}",
                            f"price={result.price} offers={result.turns}",
                            f"llm_calls={result.llm_calls}",
                        )
                    )
                )
                if result.outcome is None:
                    raise ExperimentError(
                        "experiment stopped",
                        "crashed episode retained in results.csv",
                    )


def reserve_argument(scenarios: Sequence[Scenario], shop: Shop) -> str:
    """Provide evaluator reserve data only to the server process."""
    prices: dict[str, int] = {}
    for scenario in scenarios:
        amount = (
            scenario.cheap_reserve if shop is Shop.CHEAP else scenario.pricey_reserve
        )
        previous = prices.setdefault(scenario.item, amount)
        if previous != amount:
            raise ExperimentError("scenarios", "one item's reserves differ across rows")
    return json.dumps(prices, sort_keys=True)
