"""Explicit OpenAI or zero-price OpenRouter setup, without paid model fallback."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Final

from openai import AsyncOpenAI

from market_a2a.models import HarnessError

if TYPE_CHECKING:
    import httpx2

DEFAULT_MODEL: Final = "gpt-4.1-nano-2025-04-14"
FREE_MODEL: Final = "minimax/minimax-m3.1-flash:free"
OPENROUTER_URL: Final = "https://openrouter.ai/api/v1"


def configured_model() -> str:
    """Reject unknown backends instead of silently selecting a paid default."""
    match os.environ.get("AX_MODEL_PROVIDER", "openai"):
        case "openai":
            return DEFAULT_MODEL
        case "openrouter-free":
            return FREE_MODEL
        case _:
            message = "AX_MODEL_PROVIDER must be openai or openrouter-free"
            raise HarnessError(message)


def create_model_client(http_client: httpx2.AsyncClient) -> AsyncOpenAI:
    """Keep the existing OpenAI environment unless free routing is explicit."""
    model = configured_model()
    if model == FREE_MODEL:
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            message = "OPENROUTER_API_KEY is required for openrouter-free"
            raise HarnessError(message)
        return AsyncOpenAI(
            base_url=OPENROUTER_URL,
            api_key=key,
            http_client=http_client,
            max_retries=0,
        )
    return AsyncOpenAI(http_client=http_client)
