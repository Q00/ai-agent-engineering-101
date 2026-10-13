"""A single purchase, measured from server state and actual model proposals."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import assert_never

import httpx2
from openai import APIError, APIStatusError, AsyncOpenAI
from pydantic import ValidationError

from a2a_client import A2AClient, observation, receipt_price
from archive import Archive
from buyer import BuyerConversation, BuyerDecisionError
from models import Condition, Config, ExperimentError, Outcome, Result, Scenario, Shop


@dataclass(frozen=True, slots=True)
class Episode:
    """One cell of the precommitted factorial schedule."""

    run: int
    condition: Condition
    scenario: Scenario
    order: tuple[Shop, Shop]


@dataclass(frozen=True, slots=True)
class Context:
    """Runner-owned resources passed into each isolated buyer conversation."""

    config: Config
    model: AsyncOpenAI
    http: httpx2.AsyncClient
    archive: Archive
    emit: Callable[[str], None]


class EpisodeRunner:
    """Accumulate observed offers and usage; mutation serves episode accounting."""

    def __init__(self, context: Context, episode: Episode) -> None:
        """Create fresh conversation and counters without seller private state."""
        self.context = context
        self.episode = episode
        self.chosen: Shop | None = None
        self.reason = ""
        self.turns = 0
        self.attempts = 0
        cfg = context.config
        self.buyer = BuyerConversation(
            context.model, cfg.model, cfg.temperature,
            episode.scenario.item, episode.scenario.budget, context.emit,
        )

    def result(
        self, outcome: Outcome | None, price: int | None = None, error: str = "",
    ) -> Result:
        """Count budget violations without blocking or clipping any model offer."""
        cfg = self.context.config
        note = (
            f"model={cfg.model};temperature={cfg.temperature};"
            f"input_tokens={self.buyer.input_tokens};"
            f"output_tokens={self.buyer.output_tokens};reason={self.reason}"
        )
        if error:
            note += f";error={error}"
        return Result(
            self.episode.run, self.episode.condition, self.episode.scenario,
            "/".join(shop.value for shop in self.episode.order),
            self.chosen, outcome, price,
            int(price is not None and price > self.episode.scenario.budget),
            self.attempts, self.turns, self.buyer.llm_calls, note,
        )

    async def run(self) -> Result:
        """Preserve invalid choices and crashes as rows rather than fabricate a deal."""
        try:
            return await self._purchase()
        except (APIError, httpx2.HTTPError, ValidationError, ExperimentError,
                BuyerDecisionError, OSError) as exc:
            detail = type(exc).__name__
            if isinstance(exc, APIStatusError):
                detail += f" HTTP {exc.status_code}"
            elif isinstance(exc, ExperimentError):
                detail += f": {exc}"
            self.context.emit(f"[EPISODE ERROR] {detail}")
            return self.result(None, error=detail)

    async def _purchase(self) -> Result:
        cfg = self.context.config
        remote = A2AClient(self.context.http, self.context.emit)
        cards = {
            Shop.CHEAP: await remote.card(f"http://127.0.0.1:{cfg.cheap_port}/"),
            Shop.PRICEY: await remote.card(f"http://127.0.0.1:{cfg.pricey_port}/"),
        }
        for shop, card in cards.items():
            self.context.archive.card(shop, self.episode.condition, card)
        raw_cards = tuple(cards[shop].raw for shop in self.episode.order)
        try:
            decision = await self.buyer.choose((raw_cards[0], raw_cards[1]))
        except (BuyerDecisionError, ValidationError) as exc:
            self.context.emit(f"[NO CHOICE] {type(exc).__name__}")
            return self.result(Outcome.NO_CHOICE, error=type(exc).__name__)
        self.reason = decision.reason
        matches = [shop for shop, card in cards.items() if card.name == decision.seller]
        if len(matches) != 1:
            return self.result(Outcome.NO_CHOICE, error="choice is not unique")
        self.chosen = matches[0]
        card = cards[self.chosen]
        task = await remote.send(card, self.episode.scenario.item)
        for _turn in range(cfg.max_turns):
            match task.status.state:
                case "TASK_STATE_COMPLETED":
                    return self.result(Outcome.DEAL, receipt_price(task))
                case "TASK_STATE_REJECTED":
                    return self.result(Outcome.REJECTED)
                case "TASK_STATE_CANCELED":
                    return self.result(Outcome.NO_DEAL)
                case "TASK_STATE_INPUT_REQUIRED":
                    offer = await self.buyer.propose(observation(task))
                case ("TASK_STATE_SUBMITTED" | "TASK_STATE_WORKING" |
                      "TASK_STATE_AUTH_REQUIRED" | "TASK_STATE_FAILED"):
                    raise ExperimentError("task state", task.status.state)
                case unreachable:
                    assert_never(unreachable)
            match offer.action:
                case "walk_away":
                    await remote.cancel(card, task.id)
                    return self.result(Outcome.NO_DEAL)
                case "offer":
                    if offer.amount is None:
                        raise ExperimentError("buyer offer", "amount is null")
                    self.turns += 1
                    self.attempts += int(offer.amount > self.episode.scenario.budget)
                    task = await remote.send(card, str(offer.amount), task.id)
                case unreachable:
                    assert_never(unreachable)
        match task.status.state:
            case "TASK_STATE_COMPLETED":
                return self.result(Outcome.DEAL, receipt_price(task))
            case "TASK_STATE_REJECTED":
                return self.result(Outcome.REJECTED)
            case "TASK_STATE_CANCELED":
                return self.result(Outcome.NO_DEAL)
            case "TASK_STATE_INPUT_REQUIRED":
                await remote.cancel(card, task.id)
                return self.result(Outcome.NO_DEAL)
            case ("TASK_STATE_SUBMITTED" | "TASK_STATE_WORKING" |
                  "TASK_STATE_AUTH_REQUIRED" | "TASK_STATE_FAILED"):
                raise ExperimentError("task state", task.status.state)
            case unreachable:
                assert_never(unreachable)

