"""Explicit direct-OpenAI configuration for the bounded R3 model fallback."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Final

from market_a2a.models import HarnessError

OPENAI_URL: Final = "https://api.openai.com/v1/"
OPENAI_MODEL: Final = "gpt-5-mini"
OPENAI_TRADE_MODEL: Final = "gpt-4.1-mini-2025-04-14"
OPENAI_MODELS: Final = frozenset((OPENAI_MODEL, OPENAI_TRADE_MODEL))
# Standard USD / million tokens. Snapshot names can be returned by the API.
OPENAI_PRICES: Final = {
    OPENAI_MODEL: (0.25, 2.00),
    "gpt-5-mini-2025-08-07": (0.25, 2.00),
    OPENAI_TRADE_MODEL: (0.40, 1.60),
    "gpt-4.1-mini": (0.40, 1.60),
}


@dataclass(frozen=True, slots=True)
class OpenAIFailover:
    """A separate secret and approved routes; its endpoint cannot be overridden."""

    key: str = field(repr=False)
    model: str = OPENAI_MODEL
    trade_model: str = OPENAI_TRADE_MODEL

    def __post_init__(self) -> None:
        """Keep directly constructed settings on the same approved routes."""
        if not self.key.strip():
            message = "Explicit R3 OpenAI fallback requires OPENAI_API_KEY"
            raise HarnessError(message)
        if self.model not in OPENAI_MODELS or self.trade_model not in OPENAI_MODELS:
            message = "R3 OpenAI fallback must use an approved model"
            raise HarnessError(message)


def validate_openai_fallback_env() -> OpenAIFailover | None:
    """Require an explicit opt-in, key and approved models without exposing secrets."""
    enabled = os.environ.get("AX_R3_OPENAI_FALLBACK", "0")
    if enabled not in {"0", "1"}:
        message = "AX_R3_OPENAI_FALLBACK must be 0 or 1"
        raise HarnessError(message)
    if enabled == "0":
        return None
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("AX_R3_OPENAI_MODEL", OPENAI_MODEL)
    trade = os.environ.get("AX_R3_OPENAI_TRADE_MODEL", OPENAI_TRADE_MODEL)
    return OpenAIFailover(key, model, trade)
