"""One purchase measured from actual server state and unmodified model proposals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

import httpx2
from openai import APIError, APIStatusError
from pydantic import ValidationError

from a2a_client import A2AClient, observation, receipt_price
from buyer import BuyerConversation, BuyerDecisionError
from models import Condition, Config, ExperimentError, Outcome, Result, Scenario, Shop

if TYPE_CHECKING:
    from collections.abc import Callable

    from openai import AsyncOpenAI

    from archive import Archive
    from models import Card, Task


@dataclass(frozen=True, slots=True)
class Episode:
    """One cell of the preregistered factorial schedule."""

    run: int
    condition: Condition
    scenario: Scenario
    order: tuple[Shop, Shop]


@dataclass(frozen=True, slots=True)
class Context:
    """Resources owned by the runner, shared without exposing reserve prices."""

    config: Config
    model: AsyncOpenAI
    http: httpx2.AsyncClient
    archive: Archive
    emit: Callable[[str], None]


class EpisodeRunner:
    """Mutate counters deliberately to retain the evidence of one purchase."""

    def __init__(self, context: Context, episode: Episode) -> None:
        """Start a fresh buyer conversation and empty offer accounting."""
        self.context: Context = context
        self.episode: Episode = episode
        self.chosen: Shop | None = None
        self.reason: str = ""
        self.choice_error: str = ""
        self.turns: int = 0
        self.attempts: int = 0
        cfg = context.config
        self.buyer: BuyerConversation = BuyerConversation(
            context.model,
            cfg.model,
            cfg.temperature,
            episode.scenario.item,
            episode.scenario.budget,
            context.emit,
        )

    def result(
        self,
        outcome: Outcome | None,
        price: int | None = None,
        error: str = "",
    ) -> Result:
        """Report actual prices; never prevent an over-budget offer or deal."""
        cfg = self.context.config
        note = "".join(
            (
                f"model={cfg.model};temperature={cfg.temperature};",
                f"input_tokens={self.buyer.input_tokens};",
                f"output_tokens={self.buyer.output_tokens};reason={self.reason}",
            )
        )
        if error:
            note += f";error={error}"
        return Result(
            self.episode.run,
            self.episode.condition,
            self.episode.scenario,
            "/".join(shop.value for shop in self.episode.order),
            self.chosen,
            outcome,
            price,
            int(price is not None and price > self.episode.scenario.budget),
            self.attempts,
            self.turns,
            self.buyer.llm_calls,
            note,
        )

    async def run(self) -> Result:
        """Keep invalid choices and crashes without leaking API error bodies."""
        try:
            return await self._purchase()
        except (
            APIError,
            httpx2.HTTPError,
            ValidationError,
            ExperimentError,
            BuyerDecisionError,
            OSError,
        ) as exc:
            detail = type(exc).__name__
            if isinstance(exc, APIStatusError):
                detail += f" HTTP {exc.status_code}"
            elif isinstance(exc, ExperimentError):
                detail += f": {exc}"
            self.context.emit(f"[EPISODE ERROR] {detail}")
            return self.result(None, error=detail)

    def _terminal(self, task: Task) -> Result | None:
        state = task.status.state
        match state:
            case "TASK_STATE_COMPLETED":
                return self.result(Outcome.DEAL, receipt_price(task))
            case "TASK_STATE_REJECTED":
                return self.result(Outcome.REJECTED)
            case "TASK_STATE_CANCELED":
                return self.result(Outcome.NO_DEAL)
            case "TASK_STATE_INPUT_REQUIRED":
                return None
            case (
                "TASK_STATE_SUBMITTED"
                | "TASK_STATE_WORKING"
                | "TASK_STATE_AUTH_REQUIRED"
                | "TASK_STATE_FAILED"
            ):
                raise ExperimentError("task state", state)
        assert_never(state)

    async def _choose(self, remote: A2AClient) -> Card | None:
        cfg = self.context.config
        cards = {
            Shop.CHEAP: await remote.card(f"http://127.0.0.1:{cfg.cheap_port}/"),
            Shop.PRICEY: await remote.card(f"http://127.0.0.1:{cfg.pricey_port}/"),
        }
        for shop, card in cards.items():
            self.context.archive.card(shop, self.episode.condition, card)
        try:
            first, second = self.episode.order
            decision = await self.buyer.choose((cards[first].raw, cards[second].raw))
        except (BuyerDecisionError, ValidationError) as exc:
            self.choice_error = type(exc).__name__
            self.context.emit(f"[NO CHOICE] {self.choice_error}")
            return None
        self.reason = decision.reason
        matches = [shop for shop, card in cards.items() if card.name == decision.seller]
        if len(matches) != 1:
            self.choice_error = "choice is not unique"
            return None
        self.chosen = matches[0]
        return cards[self.chosen]

    async def _purchase(self) -> Result:
        remote = A2AClient(self.context.http, self.context.emit)
        card = await self._choose(remote)
        if card is None:
            return self.result(Outcome.NO_CHOICE, error=self.choice_error)
        task = await remote.send(card, self.episode.scenario.item)
        for _turn in range(self.context.config.max_turns):
            done = self._terminal(task)
            if done is not None:
                return done
            offer = await self.buyer.propose(observation(task))
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
                    continue
            assert_never(offer.action)
        done = self._terminal(task)
        if done is not None:
            return done
        await remote.cancel(card, task.id)
        return self.result(Outcome.NO_DEAL)
