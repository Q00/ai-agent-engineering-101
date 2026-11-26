# Copyright (c) 2026 25620027
"""Model-driven buyer decisions; the runner owns A2A transport and turn limits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Literal, TypeVar, final, override

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Callable

    from openai import AsyncOpenAI
    from openai.types.chat import ChatCompletionMessageParam


class ShopChoice(BaseModel):
    """A seller name from a presented card and a brief stated reason."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="forbid", strict=True
    )
    seller: str
    reason: str


class OfferChoice(BaseModel):
    """An unmodified model proposal, including possible budget violations."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="forbid", strict=True
    )
    action: Literal["offer", "walk_away"]
    amount: int | None
    reason: str


class CardIdentity(BaseModel):
    """Read only the name for checking whether a choice names a served card."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, strict=True)
    name: str


@dataclass(frozen=True, slots=True)
class BuyerDecisionError(ValueError):
    """A response cannot represent a decision; the runner preserves the failure."""

    detail: str

    @override
    def __str__(self) -> str:
        """Describe the observed decision failure."""
        return self.detail


Decision = TypeVar("Decision", bound=BaseModel)


@final
class BuyerConversation:
    """Accumulate one episode's messages and usage through successive decisions.

    Mutation is deliberate: each response and seller observation becomes context
    for the next offer. A fresh instance starts every episode. The supplied client
    remains owned by the runner. Constructor parameters implement its fixed API.
    """

    # The runner contract supplies these six independent episode dependencies.
    def __init__(  # noqa: PLR0913, PLR0917
        self,
        client: AsyncOpenAI,
        model: str,
        temperature: float,
        item: str,
        budget: int,
        emit: Callable[[str], None],
    ) -> None:
        """Initialize the episode; the budget appears only in the system message."""
        self.client = client
        self.model = model
        self.temperature = temperature
        self.emit = emit
        self.llm_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.messages: list[ChatCompletionMessageParam] = [
            {
                "role": "system",
                "content": (
                    "You are a buyer negotiating to purchase one requested item. "
                    f"Your total budget is {budget} monetary units. "
                    "Seek the lowest purchase price and do not exceed your budget. "
                    "Choose exactly one seller using the presented Agent Cards, "
                    "then negotiate only with that seller. You have at most six "
                    "offers. You may walk away. Use whole-number prices. "
                    "Return the requested JSON decision with a short reason, "
                    "not a detailed reasoning trace. For an offer, amount must be "
                    "an integer; for walk_away, amount must be null."
                ),
            },
            {"role": "user", "content": f"Requested item: {item}"},
        ]

    async def choose(self, cards: tuple[str, str]) -> ShopChoice:
        """Choose from the two raw cards in their caller-supplied order."""
        names = tuple(CardIdentity.model_validate_json(card).name for card in cards)
        self.messages.extend({"role": "user", "content": card} for card in cards)
        self.messages.append(
            {
                "role": "user",
                "content": "Choose a seller by its exact Agent Card name.",
            },
        )
        choice = await self._complete(ShopChoice)
        if choice.seller not in names:
            detail = f"Unknown seller name: {choice.seller}"
            raise BuyerDecisionError(detail)
        self.emit(f"BUYER_CHOICE {choice.model_dump_json()}")
        return choice

    async def propose(self, observation: str) -> OfferChoice:
        """Append the seller observation and return the model's unmodified offer."""
        self.messages.append({"role": "user", "content": observation})
        self.messages.append(
            {"role": "user", "content": "Make your next offer or walk away."},
        )
        choice = await self._complete(OfferChoice)
        self.emit(f"BUYER_PROPOSAL {choice.model_dump_json()}")
        return choice

    async def _complete(self, response_type: type[Decision]) -> Decision:
        """Record the actual final response before parsing so failures survive."""
        self.llm_calls += 1
        completion = await self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=self.messages,
            max_completion_tokens=300,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": response_type.__name__,
                    "strict": True,
                    "schema": response_type.model_json_schema(),
                },
            },
        )
        usage = completion.usage
        if usage is not None:
            self.input_tokens += usage.prompt_tokens
            self.output_tokens += usage.completion_tokens
            self.emit(f"BUYER_USAGE {usage.model_dump_json()}")
        response = completion.choices[0]
        content = response.message.content
        self.emit(f"BUYER_RESPONSE {content}")
        if response.message.refusal is not None:
            detail = f"Model refusal: {response.message.refusal}"
            raise BuyerDecisionError(detail)
        if content is None:
            detail = "Model response has no content"
            raise BuyerDecisionError(detail)
        self.messages.append({"role": "assistant", "content": content})
        return response_type.model_validate_json(content)
