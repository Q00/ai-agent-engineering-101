"""Explicit Haiku fallback through Anthropic's documented SDK compatibility API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Final

from market_a2a.models import HarnessError
from market_a2a.paid_fallback import KST, DailyQuota

if TYPE_CHECKING:
    from collections.abc import Callable

    from openai import AsyncOpenAI
    from openai.types.chat import (
        ChatCompletion,
        ChatCompletionMessageParam,
        ChatCompletionToolParam,
    )

ANTHROPIC_URL: Final = "https://api.anthropic.com/v1/"
HAIKU_MODEL: Final = "claude-haiku-5-5"


@dataclass(frozen=True, slots=True)
class AnthropicFallback:
    """Keep paid Haiku attempts in the same buyer/seller quota ledger."""

    client: AsyncOpenAI = field(repr=False)
    quota: DailyQuota
    model: str = HAIKU_MODEL

    def __post_init__(self) -> None:
        """Reject model or endpoint overrides before a paid request is possible."""
        if (
            self.client.max_retries != 0
            or str(self.client.base_url) != ANTHROPIC_URL
            or self.model != HAIKU_MODEL
        ):
            message = "Haiku fallback requires its approved model and direct endpoint"
            raise HarnessError(message)

    async def complete(
        self, messages: list[ChatCompletionMessageParam],
        tools: list[ChatCompletionToolParam], emit: Callable[[str], None],
    ) -> ChatCompletion:
        """Charge failures too; preserve tool validation in the existing loop."""
        used = self.quota.reserve()
        day = datetime.now(KST).date()
        emit(f"ANTHROPIC_ATTEMPT day_kst={day} used={used}/{self.quota.limit}")
        return await self.client.chat.completions.create(
            model=self.model, messages=messages, tools=tools,
            tool_choice="required", parallel_tool_calls=False,
            max_completion_tokens=450,
        )
