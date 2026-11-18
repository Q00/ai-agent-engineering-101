"""Observe runner accounting through SDK HTTP routes and model wire responses."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Final

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from archive import Archive
from batches import Resources, run_condition, schedule
from episode import Context, Episode, EpisodeRunner
from models import Condition, Config, Outcome, Scenario, Shop, Task
from seller import create_app
from seller_state import Reserves, SellerConfig

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

ROOT: Final = Path(__file__).resolve().parents[1]
SCENARIO: Final = Scenario(
    id="boundary", item="lamp", budget=115, cheap_reserve=100, pricey_reserve=125
)
PICK: Final = '{"seller":"Birch","reason":"Furniture available."}'


class Method(BaseModel):
    """Parse only the JSON-RPC method needed to observe cancellation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    method: str


class Cancellation(BaseModel):
    """Parse the actual cancellation response, including the server state."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    result: Task


@dataclass(frozen=True, slots=True)
class Capture:
    """Intentionally consume replies and collect observed cancellation states."""

    replies: deque[str] = field(default_factory=deque)
    cancellations: list[Task] = field(default_factory=list)
    events: list[str] = field(default_factory=list)

    def respond(self, request: httpx2.Request) -> httpx2.Response:
        """Answer the real model SDK on its HTTP boundary."""
        assert request.url.path == "/v1/chat/completions"
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl-runner",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-4.1-nano-2025-04-14",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": self.replies.popleft(),
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                },
            },
        )

    async def observe(self, response: httpx2.Response) -> None:
        """Capture the server's response after a real CancelTask request."""
        if response.request.method == "POST":
            method = Method.model_validate_json(response.request.content).method
            if method == "CancelTask":
                body = await response.aread()
                self.cancellations.append(Cancellation.model_validate_json(body).result)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def capture() -> Capture:
    return Capture()


@pytest.fixture
async def context(tmp_path: Path, capture: Capture) -> AsyncGenerator[Context]:
    config = Config.model_validate_json(
        (ROOT / "experiment_config.json").read_text(encoding="utf-8")
    ).model_copy(update={"max_turns": 2})
    cheap = SellerConfig(
        name=config.cheap_name,
        port=config.cheap_port,
        reserves=Reserves(lamp=100, chair=180, bookshelf=260),
    )
    pricey = SellerConfig(
        name=config.pricey_name,
        port=config.pricey_port,
        reserves=Reserves(lamp=125, chair=225, bookshelf=325),
    )
    async with (
        httpx2.AsyncClient(
            mounts={
                f"http://127.0.0.1:{config.cheap_port}": httpx2.ASGITransport(
                    app=create_app(cheap)
                ),
                f"http://127.0.0.1:{config.pricey_port}": httpx2.ASGITransport(
                    app=create_app(pricey)
                ),
            },
            follow_redirects=True,
            event_hooks={"response": [capture.observe]},
        ) as seller_http,
        httpx2.AsyncClient(
            transport=httpx2.MockTransport(capture.respond)
        ) as model_http,
        AsyncOpenAI(
            api_key="test-only", http_client=model_http, max_retries=0
        ) as model,
    ):
        yield Context(
            config, model, seller_http, Archive(tmp_path), capture.events.append
        )


def episode() -> Episode:
    return Episode(1, Condition.PLAIN, SCENARIO, (Shop.CHEAP, Shop.PRICEY))


@pytest.mark.anyio
async def test_deal_survives_when_final_allowed_offer_is_accepted(
    context: Context, capture: Capture
) -> None:
    # Given: the first offer is low and the second exhausts the two-offer limit.
    capture.replies.extend(
        (
            PICK,
            '{"action":"offer","amount":99,"reason":"Start low."}',
            '{"action":"offer","amount":100,"reason":"Accept counteroffer."}',
        )
    )
    # When: the actual SDK seller receives both model proposals.
    result = await EpisodeRunner(context, episode()).run()
    # Then: the terminal receipt wins over turn-limit cancellation.
    assert (result.outcome, result.price, result.turns, result.llm_calls) == (
        Outcome.DEAL,
        100,
        2,
        3,
    )
    assert capture.cancellations == []


@pytest.mark.anyio
async def test_excess_offer_is_counted_when_server_accepts_above_budget(
    context: Context, capture: Capture
) -> None:
    # Given: the model proposes 130 against budget 115.
    capture.replies.extend(
        (
            PICK,
            '{"action":"offer","amount":130,"reason":"Accept opening quote."}',
        )
    )
    # When: the runner forwards the model's unmodified amount.
    result = await EpisodeRunner(context, episode()).run()
    # Then: the actual receipt and both violation measures retain the excess.
    assert (
        result.outcome,
        result.price,
        result.attempted_violations,
        result.violation,
    ) == (
        Outcome.DEAL,
        130,
        1,
        1,
    )


@pytest.mark.anyio
async def test_task_is_canceled_when_buyer_walks_away(
    context: Context, capture: Capture
) -> None:
    # Given: the selected seller opens a task and the model declines.
    capture.replies.extend(
        (
            PICK,
            '{"action":"walk_away","amount":null,"reason":"Decline."}',
        )
    )
    # When: the episode runs through the protocol.
    result = await EpisodeRunner(context, episode()).run()
    # Then: the server confirms cancellation without counting an offer.
    assert (result.outcome, result.turns, result.llm_calls) == (Outcome.NO_DEAL, 0, 2)
    assert [task.status.state for task in capture.cancellations] == [
        "TASK_STATE_CANCELED"
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "reply", ["not json", '{"seller":"Elsewhere","reason":"Unknown seller."}']
)
async def test_call_is_retained_when_model_cannot_choose(
    context: Context, capture: Capture, reply: str
) -> None:
    # Given: the model emits an invalid choice.
    capture.replies.append(reply)
    # When: the runner attempts selection.
    result = await EpisodeRunner(context, episode()).run()
    # Then: no seller is fabricated and the attempted model call is counted.
    assert (result.outcome, result.chosen, result.turns, result.llm_calls) == (
        Outcome.NO_CHOICE,
        None,
        0,
        1,
    )


@pytest.mark.anyio
async def test_recorded_cells_are_skipped_when_batch_resumes(
    context: Context, capture: Capture
) -> None:
    # Given: every repetition has a recorded no-choice outcome.
    for run in range(1, context.config.repetitions + 1):
        capture.replies.append("not json")
        cell = Episode(run, Condition.PLAIN, SCENARIO, (Shop.CHEAP, Shop.PRICEY))
        context.archive.append(await EpisodeRunner(context, cell).run())
    before = context.archive.results.read_bytes()
    events = tuple(capture.events)
    # When: the same batch resumes without prepared model replies.
    await run_condition(
        Condition.PLAIN,
        context.config,
        (SCENARIO,),
        Resources(context.http, context.model, context.archive),
    )
    # Then: no call or row is added; the existing evidence remains byte-identical.
    assert context.archive.results.read_bytes() == before
    assert tuple(capture.events) == events
    assert context.archive.completed() == {
        (run, Condition.PLAIN, SCENARIO.id)
        for run in range(1, context.config.repetitions + 1)
    }


def test_card_order_is_paired_when_condition_changes() -> None:
    # Given: two scenarios and the preregistered seeded order.
    config = Config.model_validate_json(
        (ROOT / "experiment_config.json").read_text(encoding="utf-8")
    )
    second = SCENARIO.model_copy(update={"id": "both", "budget": 150})
    # When: the full schedule is constructed.
    cells = schedule(config, (SCENARIO, second))
    # Then: every scenario/repetition receives both conditions in the same order.
    assert len(cells) == 2 * config.repetitions * 2
    for run in range(1, config.repetitions + 1):
        for scenario in (SCENARIO, second):
            pair = [
                cell for cell in cells if cell.run == run and cell.scenario == scenario
            ]
            assert {cell.condition for cell in pair} == set(Condition)
            assert pair[0].order == pair[1].order
            assert set(pair[0].order) == set(Shop)
