"""Own CLI model clients and the shared, pre-existing paid-call ledger."""

from __future__ import annotations

import os
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, assert_never

import anyio
from openai import AsyncOpenAI

from http_client import create_client
from market_a2a.anthropic_fallback import (
    ANTHROPIC_URL,
    HAIKU_MODEL,
    AnthropicFallback,
)
from market_a2a.harness import Harness
from market_a2a.model_provider import configured_openai_model, create_model_client
from market_a2a.models import HarnessError
from market_a2a.paid_fallback import (
    DailyQuota,
    OpenAIFallback,
    PaidFallback,
    configured_paid_provider,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable


@asynccontextmanager
async def harness_session(emit: Callable[[str], None]) -> AsyncGenerator[Harness]:
    """Share the same explicit quota contract for seller and buyer CLI runs."""
    async with create_client(model=True) as model_http, AsyncExitStack() as stack:
        model = await stack.enter_async_context(create_model_client(model_http))
        fallback: PaidFallback | None = None
        provider = configured_paid_provider()
        if provider != "none":
            quota_path = Path(os.environ["AX_PAID_QUOTA_PATH"])
            if not await anyio.Path(quota_path).is_file():
                message = "Existing shared paid quota ledger is required"
                raise HarnessError(message)
            limit = int(os.environ.get(
                "AX_PAID_DAILY_LIMIT", os.environ.get("AX_OPENAI_DAILY_LIMIT", "5000")
            ))
            quota = DailyQuota(quota_path, limit)
            match provider:
                case "anthropic":
                    paid = await stack.enter_async_context(AsyncOpenAI(
                        base_url=ANTHROPIC_URL,
                        api_key=os.environ["ANTHROPIC_API_KEY"],
                        http_client=model_http, max_retries=0,
                    ))
                    fallback = AnthropicFallback(
                        paid, quota, os.environ.get("ANTHROPIC_MODEL", HAIKU_MODEL),
                    )
                case "openai":
                    paid = await stack.enter_async_context(AsyncOpenAI(
                        base_url="https://api.openai.com/v1",
                        api_key=os.environ["OPENAI_API_KEY"],
                        http_client=model_http, max_retries=0,
                    ))
                    bound = os.environ.get("AX_OPENAI_MAX_INPUT_BYTES")
                    fallback = OpenAIFallback(
                        paid, quota, configured_openai_model(),
                        int(bound) if bound is not None else None,
                    )
                case _:
                    assert_never(provider)
        yield Harness(model, emit, fallback=fallback)
