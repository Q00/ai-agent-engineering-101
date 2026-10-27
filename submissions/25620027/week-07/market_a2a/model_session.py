"""Own CLI model clients and the shared, pre-existing paid-call ledger."""

from __future__ import annotations

import os
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
from openai import AsyncOpenAI

from http_client import create_client
from market_a2a.harness import Harness
from market_a2a.model_provider import create_model_client
from market_a2a.models import HarnessError
from market_a2a.paid_fallback import DailyQuota, OpenAIFallback

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


@asynccontextmanager
async def harness_session(emit: Callable[[str], None]) -> AsyncIterator[Harness]:
    """Share the same explicit quota contract for seller and buyer CLI runs."""
    async with create_client(model=True) as model_http, AsyncExitStack() as stack:
        model = await stack.enter_async_context(create_model_client(model_http))
        fallback = None
        if os.environ.get("AX_OPENAI_FALLBACK") == "1":
            quota_path = Path(os.environ["AX_PAID_QUOTA_PATH"])
            if not await anyio.Path(quota_path).is_file():
                message = "Existing shared paid quota ledger is required"
                raise HarnessError(message)
            paid = await stack.enter_async_context(AsyncOpenAI(
                base_url="https://api.openai.com/v1",
                api_key=os.environ["OPENAI_API_KEY"], http_client=model_http,
                max_retries=0,
            ))
            fallback = OpenAIFallback(paid, DailyQuota(quota_path))
        yield Harness(model, emit, fallback=fallback)
