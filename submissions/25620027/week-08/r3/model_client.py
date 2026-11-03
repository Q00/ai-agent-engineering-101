"""One bounded model call for every role, with a shared dollar cap and downgrades.

Both slow loops (scout, tactical, strategy) and the two in-trade readers go through
this client. Every call reserves a slot in the existing attempt ledger, is priced
from the response usage into a per-day spend table, and is logged with a hash of
its input. When the day's spend passes the cap the strategy model is downgraded to
Haiku; past twice the cap the slow loops stop calling; past three times the cap the
in-trade readers stop too, and the deterministic paths carry on alone. A billing
refusal from the API pauses all calls for thirty minutes.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

import anyio
from openai import APIError, APIStatusError, AsyncOpenAI
from openai.types.chat import ChatCompletionFunctionToolParam
from openai.types.shared_params import FunctionDefinition

from market_a2a.anthropic_fallback import ANTHROPIC_URL, HAIKU_MODEL
from market_a2a.models import HarnessError
from market_a2a.paid_fallback import KST, DailyQuota

if TYPE_CHECKING:
    from collections.abc import Mapping

    from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

    from r3.events import EventLedger

LOGGER: Final = logging.getLogger("r3.model")
SONNET_MODEL: Final = "claude-sonnet-5-5"
OPUS_MODEL: Final = "claude-opus-5-5"
APPROVED_MODELS: Final = frozenset({HAIKU_MODEL, SONNET_MODEL, OPUS_MODEL})
# USD per million input / output tokens; unknown models price as Opus.
PRICES: Final[dict[str, tuple[float, float]]] = {
    HAIKU_MODEL: (0.10, 0.50),
    SONNET_MODEL: (2.00, 10.00),
    OPUS_MODEL: (4.00, 20.00),
}
DEFAULT_DAILY_USD: Final = 3.0
COOLDOWN_SECONDS: Final = 1800.0
COOLDOWN_KEY: Final = "model_cooldown_until"
HAIKU_LONG_CONTEXT: Final = 100_000
Role = Literal["scout", "tactical", "strategy", "trade"]
Effort = Literal["low", "medium", "high"]
HTTP_PAYMENT_REQUIRED: Final = 402
HTTP_BAD_REQUEST: Final = 400


def kst_day(now: float | None = None) -> str:
    """Return the KST calendar day used by the spend and attempt ledgers."""
    stamp = now if now is not None else time.time()
    return datetime.fromtimestamp(stamp, UTC).astimezone(KST).date().isoformat()


def price(model: str, input_tokens: int, output_tokens: int) -> float:
    """Price a call in USD from list prices; cache discounts are ignored (safe side)."""
    per_in, per_out = PRICES.get(model, PRICES[OPUS_MODEL])
    if model == HAIKU_MODEL and input_tokens > HAIKU_LONG_CONTEXT:
        per_in, per_out = 0.50, 2.50
    return round((input_tokens * per_in + output_tokens * per_out) / 1_000_000, 6)


@dataclass(frozen=True, slots=True)
class ModelReply:
    """What one call returned: the tool arguments (or None) and its measured cost."""

    arguments: str | None
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass(frozen=True, slots=True)
class Request:
    """One bounded tool-forced completion."""

    role: Role
    model: str
    system: str
    user: str
    tool: ChatCompletionFunctionToolParam
    max_tokens: int
    deadline_seconds: float
    effort: Effort | None
    summary: str

    @property
    def tool_name(self) -> str:
        """Name of the single tool the model may call."""
        return self.tool["function"]["name"]


def tool_param(
    name: str, description: str, parameters: Mapping[str, object]
) -> ChatCompletionFunctionToolParam:
    """Build the one function tool a request exposes."""
    return ChatCompletionFunctionToolParam(
        type="function",
        function=FunctionDefinition(
            name=name, description=description, parameters=dict(parameters)
        ),
    )


class ModelClient:
    """Quota, spend cap, downgrade and logging around one tool-forced completion."""

    def __init__(
        self,
        api_key: str,
        *,
        events: EventLedger | None,
        quota: DailyQuota | None,
        daily_usd: float = DEFAULT_DAILY_USD,
        base_url: str = ANTHROPIC_URL,
    ) -> None:
        """Bind to the shared ledgers; the key is never logged."""
        self.api_key: str = api_key
        self.events: EventLedger | None = events
        self.quota: DailyQuota | None = quota
        self.daily_usd: float = daily_usd
        self.base_url: str = base_url
        self.effort_supported: bool = True

    @classmethod
    def from_env(cls, events: EventLedger | None, *, gate: str) -> ModelClient | None:
        """Build from the deployment environment, or None when the gate is closed."""
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key or os.environ.get(gate, "1") != "1":
            return None
        if events is None:
            LOGGER.warning("model_events_unavailable; keep rule-based trading")
            return None
        quota: DailyQuota | None = None
        quota_path = os.environ.get("AX_PAID_QUOTA_PATH")
        if quota_path:
            try:
                quota = DailyQuota(
                    Path(quota_path), int(os.environ.get("AX_PAID_DAILY_LIMIT", "5000"))
                )
            except (HarnessError, ValueError, OSError) as exc:
                LOGGER.warning("model_quota_unavailable %s", type(exc).__name__)
                return None
        try:
            daily = float(os.environ.get("AX_R3_DAILY_USD", str(DEFAULT_DAILY_USD)))
        except ValueError:
            daily = DEFAULT_DAILY_USD
        return cls(key, events=events, quota=quota, daily_usd=max(0.1, daily))

    def spend_today(self) -> float:
        """Return USD recorded today (KST) across every process sharing the ledger."""
        if self.events is None:
            return 0.0
        try:
            return self.events.spend(kst_day())[0]
        except (OSError, sqlite3.Error):
            LOGGER.warning("model_spend_unavailable; calls disabled")
            return float("inf")

    def cooling_down(self) -> bool:
        """Report whether a billing refusal is pausing every model call."""
        if self.events is None:
            return False
        try:
            raw = self.events.get_value(COOLDOWN_KEY)
        except (OSError, sqlite3.Error):
            LOGGER.warning("model_cooldown_unavailable; calls disabled")
            return True
        try:
            return raw is not None and time.time() < float(raw)
        except ValueError:
            return False

    def pick_model(self, wanted: str, role: Role) -> str | None:
        """Apply the spend ladder: downgrade the strategy model, then stop by role."""
        if self.cooling_down():
            return None
        spent = self.spend_today()
        cap = self.daily_usd
        if role == "trade":
            return None if spent >= 3 * cap else HAIKU_MODEL
        if spent >= 2 * cap:
            return None
        if role == "strategy" and spent >= cap:
            return HAIKU_MODEL
        return wanted if wanted in APPROVED_MODELS else HAIKU_MODEL

    async def call(self, request: Request) -> ModelReply | None:
        """Make one tool call; any refusal, error or timeout returns None."""
        chosen = self.pick_model(request.model, request.role)
        if chosen is None:
            LOGGER.info(
                "model_skipped role=%s spend=%.3f", request.role, self.spend_today()
            )
            return None
        if self.quota is not None:
            try:
                _ = self.quota.reserve()
            except HarnessError as exc:
                LOGGER.warning("model_quota_refused %s", exc)
                return None
        digest = hashlib.sha256(request.user.encode("utf-8")).hexdigest()
        started = time.monotonic()
        completion = await self._complete(request, chosen)
        if completion is None:
            self._log(request, chosen, started, (0, 0), digest, accepted=False)
            return None
        usage = getattr(completion, "usage", None)
        tokens = (
            int(usage.prompt_tokens) if usage else 0,
            int(usage.completion_tokens) if usage else 0,
        )
        arguments: str | None = None
        choice = completion.choices[0]
        for call in choice.message.tool_calls or []:
            if call.type == "function" and call.function.name == request.tool_name:
                arguments = call.function.arguments
                break
        if arguments is None:
            LOGGER.info(
                "model_no_tool_call role=%s finish=%s output_tokens=%s",
                request.role,
                getattr(choice, "finish_reason", None),
                tokens[1],
            )
        self._log(
            request, chosen, started, tokens, digest, accepted=arguments is not None
        )
        return ModelReply(
            arguments,
            chosen,
            int((time.monotonic() - started) * 1000),
            tokens[0],
            tokens[1],
            price(chosen, *tokens),
        )

    async def _complete(self, request: Request, chosen: str) -> ChatCompletion | None:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ]
        forced = chosen.startswith("claude-haiku")
        async with AsyncOpenAI(
            base_url=self.base_url, api_key=self.api_key, max_retries=0
        ) as client:
            for attempt in range(2):
                extra: dict[str, object] = {}
                if request.effort is not None and self.effort_supported:
                    extra["reasoning_effort"] = request.effort
                try:
                    with anyio.fail_after(request.deadline_seconds):
                        return await client.chat.completions.create(
                            model=chosen,
                            messages=messages,
                            tools=[request.tool],
                            tool_choice="required" if forced else "auto",
                            parallel_tool_calls=False,
                            max_completion_tokens=request.max_tokens,
                            **extra,  # pyright: ignore[reportArgumentType]
                        )
                except APIStatusError as exc:
                    if (
                        attempt == 0
                        and extra
                        and exc.status_code == HTTP_BAD_REQUEST
                        and "reasoning" in str(exc).lower()
                    ):
                        self.effort_supported = False
                        continue
                    self._note_billing(exc)
                    LOGGER.warning(
                        "model_failed role=%s status=%s", request.role, exc.status_code
                    )
                    return None
                except (APIError, TimeoutError, ValueError, OSError) as exc:
                    LOGGER.warning(
                        "model_failed role=%s %s", request.role, type(exc).__name__
                    )
                    return None
        return None

    def _note_billing(self, exc: APIStatusError) -> None:
        text = str(exc).lower()
        if exc.status_code == HTTP_PAYMENT_REQUIRED or (
            exc.status_code == HTTP_BAD_REQUEST
            and ("credit" in text or "billing" in text)
        ):
            LOGGER.warning("model_billing_refused; cooling down 30 min")
            if self.events is not None:
                with contextlib.suppress(OSError):
                    self.events.set_value(
                        COOLDOWN_KEY, str(time.time() + COOLDOWN_SECONDS)
                    )

    def _log(  # noqa: PLR0913 - one row
        self,
        request: Request,
        model: str,
        started: float,
        tokens: tuple[int, int],
        digest: str,
        *,
        accepted: bool,
    ) -> None:
        if self.events is None:
            return
        try:
            self.events.record_model_decision(
                role=request.role,
                model=model,
                latency_ms=int((time.monotonic() - started) * 1000),
                input_tokens=tokens[0],
                output_tokens=tokens[1],
                cost_usd=price(model, *tokens),
                accepted=accepted,
                summary=request.summary,
                input_sha256=digest,
                day=kst_day(),
            )
        except OSError:
            LOGGER.warning("model_log_failed")


def parse_arguments(arguments: str | None) -> dict[str, object] | None:
    """Parse tool arguments into a JSON object; anything else is None."""
    if not arguments:
        return None
    try:
        parsed = json.loads(arguments)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
