"""One bounded model call for every role, with a shared dollar cap and downgrades.

Both slow loops (scout, tactical, strategy) and the two in-trade readers go through
this client. Every call reserves a slot in the existing attempt ledger, is priced
from the response usage into a per-day spend table, and is logged with a hash of
its input. When the day's spend passes the cap the strategy model is downgraded to
Haiku; past twice the cap the slow loops stop calling; past three times the cap the
in-trade readers stop too, and the deterministic paths carry on alone. An explicit
OpenAI fallback may serve unavailable Claude requests within the same limits.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Final, Literal, TypedDict

import anyio
from openai import APIError, APIStatusError, AsyncOpenAI
from openai.types.chat import ChatCompletionFunctionToolParam
from openai.types.shared_params import FunctionDefinition
from pydantic import TypeAdapter, ValidationError

from market_a2a.anthropic_fallback import ANTHROPIC_URL, HAIKU_MODEL
from market_a2a.models import HarnessError
from market_a2a.paid_fallback import KST, DailyQuota
from r3.model_failover import (
    OPENAI_MODELS,
    OPENAI_PRICES,
    OPENAI_URL,
    OpenAIFailover,
    validate_openai_fallback_env,
)

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
    **OPENAI_PRICES,
}
DEFAULT_DAILY_USD: Final = 3.0
COOLDOWN_SECONDS: Final = 1800.0
COOLDOWN_KEY: Final = "model_cooldown_until"
OPENAI_COOLDOWN_KEY: Final = "model_openai_cooldown_until"
ACCOUNTING_BLOCK_KEY: Final = "model_accounting_blocked"
RATE_LIMIT_COOLDOWN: Final = 60.0
UNAVAILABLE_COOLDOWN: Final = 30.0
HAIKU_LONG_CONTEXT: Final = 100_000
Role = Literal["scout", "tactical", "strategy", "trade"]
Effort = Literal["low", "medium", "high"]
Provider = Literal["anthropic", "openai"]
HTTP_PAYMENT_REQUIRED: Final = 402
HTTP_BAD_REQUEST: Final = 400
HTTP_RATE_LIMIT: Final = 429
HTTP_SERVER_ERROR: Final = 500
ARGUMENTS: Final = TypeAdapter(dict[str, object])


class _ReasoningOptions(TypedDict, total=False):
    reasoning_effort: Literal["minimal", "low", "medium", "high"]


@dataclass(frozen=True, slots=True)
class _Attempt:
    completion: ChatCompletion | None
    failover: bool = False
    retry: bool = False


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

    def __init__(  # noqa: PLR0913 - retains the existing constructor contract
        self,
        api_key: str,
        *,
        events: EventLedger | None,
        quota: DailyQuota | None,
        daily_usd: float = DEFAULT_DAILY_USD,
        base_url: str = ANTHROPIC_URL,
        openai: OpenAIFailover | None = None,
    ) -> None:
        """Bind to the shared ledgers; the key is never logged."""
        self.api_key: str = api_key
        self.events: EventLedger | None = events
        self.quota: DailyQuota | None = quota
        self.daily_usd: float = daily_usd
        self.base_url: str = base_url
        self.effort_supported: bool = True
        self.openai: OpenAIFailover | None = openai
        self.accounting_failed: bool = False

    @classmethod
    def from_env(  # noqa: PLR0911 - each unavailable configuration fails closed
        cls, events: EventLedger | None, *, gate: str
    ) -> ModelClient | None:
        """Build from the deployment environment, or None when the gate is closed."""
        if os.environ.get(gate, "1") != "1":
            return None
        try:
            alternate = validate_openai_fallback_env()
        except HarnessError:
            LOGGER.warning("model_openai_configuration_unavailable; calls disabled")
            return None
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key and alternate is None:
            return None
        if events is None:
            LOGGER.warning("model_events_unavailable; keep rule-based trading")
            return None
        quota: DailyQuota | None = None
        quota_path = os.environ.get("AX_PAID_QUOTA_PATH")
        if alternate is not None and not quota_path:
            LOGGER.warning("model_quota_unavailable; OpenAI fallback disabled")
            return None
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
        return cls(
            key,
            events=events,
            quota=quota,
            daily_usd=max(0.1, daily),
            openai=alternate,
        )

    def spend_today(self) -> float:
        """Return USD recorded today (KST) across every process sharing the ledger."""
        if self.events is None:
            return 0.0
        try:
            return self.events.spend(kst_day())[0]
        except (OSError, sqlite3.Error):
            LOGGER.warning("model_spend_unavailable; calls disabled")
            return float("inf")

    def _provider_cooling(self, provider: Provider) -> bool | None:
        """Return None for uncertain ledger state so alternate routing fails closed."""
        if self.events is None:
            return None if self.openai is not None else False
        key = COOLDOWN_KEY if provider == "anthropic" else OPENAI_COOLDOWN_KEY
        try:
            raw = self.events.get_value(key)
        except (OSError, sqlite3.Error):
            LOGGER.warning("model_cooldown_unavailable; calls disabled")
            return None
        try:
            return raw is not None and time.time() < float(raw)
        except ValueError:
            LOGGER.warning("model_cooldown_invalid; calls disabled")
            return None

    def cooling_down(self) -> bool:
        """Report the Claude cooldown (the legacy key never blocks OpenAI itself)."""
        return self._provider_cooling("anthropic") is not False

    def _within_budget(self, role: Role) -> bool:
        limit = 3 if role == "trade" else 2
        return (
            self._accounting_available() and self.spend_today() < limit * self.daily_usd
        )

    def _accounting_available(self) -> bool:
        if self.accounting_failed:
            return False
        if self.events is None:
            return self.openai is None
        try:
            return (
                not self._accounting_marker().exists()
                and self.events.get_value(ACCOUNTING_BLOCK_KEY) is None
            )
        except (OSError, sqlite3.Error):
            LOGGER.warning("model_accounting_unavailable; calls disabled")
            return False

    def _accounting_marker(self) -> Path:
        if self.events is None:
            message = "A model usage ledger is required"
            raise HarnessError(message)
        return self.events.path.with_suffix(self.events.path.suffix + ".model-blocked")

    def _block_accounting(self) -> None:
        """Stop further paid calls until accounting has been explicitly repaired."""
        self.accounting_failed = True
        if self.events is None:
            return
        try:
            self.events.set_value(ACCOUNTING_BLOCK_KEY, "1")
        except (OSError, sqlite3.Error):
            try:
                marker = self._accounting_marker()
                _ = marker.write_text("model-accounting-blocked\n")
                marker.chmod(0o600)
            except OSError:
                LOGGER.warning(
                    "model_accounting_block_unwritable; process calls disabled"
                )

    def _alternate_model(self, role: Role) -> str | None:
        if (
            self.openai is None
            or not self._within_budget(role)
            or self._provider_cooling("openai") is not False
        ):
            return None
        return self.openai.trade_model if role == "trade" else self.openai.model

    def pick_model(self, wanted: str, role: Role) -> str | None:
        """Apply the spend ladder: downgrade the strategy model, then stop by role."""
        if not self._within_budget(role):
            return None
        cooling = self._provider_cooling("anthropic")
        if cooling is None:
            return None
        if cooling or not self.api_key:
            return self._alternate_model(role)
        spent = self.spend_today()
        cap = self.daily_usd
        if role == "trade":
            return HAIKU_MODEL
        if role == "strategy" and spent >= cap:
            return HAIKU_MODEL
        return wanted if wanted in APPROVED_MODELS else HAIKU_MODEL

    async def call(self, request: Request) -> ModelReply | None:
        """Bound all provider attempts by one clock, quota and spend ladder."""
        started = monotonic()
        deadline = started + request.deadline_seconds
        chosen = self.pick_model(request.model, request.role)
        if chosen is None:
            LOGGER.info(
                "model_skipped role=%s spend=%.3f", request.role, self.spend_today()
            )
            return None
        digest = hashlib.sha256(request.user.encode("utf-8")).hexdigest()
        outcome = await self._complete(request, chosen, deadline, digest)
        if (
            outcome.completion is None
            and outcome.failover
            and chosen not in OPENAI_MODELS
        ):
            alternate = self._alternate_model(request.role)
            if alternate is not None:
                LOGGER.info("model_failover role=%s provider=openai", request.role)
                chosen = alternate
                outcome = await self._complete(request, chosen, deadline, digest)
        completion = outcome.completion
        if completion is None:
            return None
        tokens = self._tokens(completion)
        actual = self._actual_model(completion, chosen)
        arguments = self._arguments(request, completion)
        return ModelReply(
            arguments,
            actual,
            int((monotonic() - started) * 1000),
            tokens[0],
            tokens[1],
            price(actual, *tokens),
        )

    @staticmethod
    def _tokens(completion: ChatCompletion) -> tuple[int, int]:
        usage = completion.usage
        return (
            (int(usage.prompt_tokens), int(usage.completion_tokens))
            if usage is not None
            else (0, 0)
        )

    @staticmethod
    def _actual_model(completion: ChatCompletion, chosen: str) -> str:
        actual = getattr(completion, "model", None)
        return actual if isinstance(actual, str) and actual in PRICES else chosen

    @staticmethod
    def _arguments(request: Request, completion: ChatCompletion) -> str | None:
        if completion.choices:
            choice = completion.choices[0]
            for call in choice.message.tool_calls or []:
                if call.type == "function" and call.function.name == request.tool_name:
                    return call.function.arguments
        LOGGER.info("model_no_tool_call role=%s", request.role)
        return None

    async def _reserve(self, deadline: float) -> bool:
        """Reserve every physical request, including compatibility and failover."""
        remaining = deadline - monotonic()
        if remaining <= 0:
            return False
        if self.quota is None:
            return True
        try:
            with anyio.fail_after(remaining):
                _ = await anyio.to_thread.run_sync(
                    self.quota.reserve, abandon_on_cancel=True
                )
        except (HarnessError, OSError, sqlite3.Error, TimeoutError) as exc:
            LOGGER.warning("model_quota_refused %s", type(exc).__name__)
            return False
        return monotonic() < deadline

    async def _complete(
        self, request: Request, chosen: str, deadline: float, digest: str
    ) -> _Attempt:
        provider = "openai" if chosen in OPENAI_MODELS else "anthropic"
        key = self.openai.key if provider == "openai" and self.openai else self.api_key
        endpoint = OPENAI_URL if provider == "openai" else self.base_url
        async with AsyncOpenAI(base_url=endpoint, api_key=key, max_retries=0) as client:
            for _ in range(2):
                if not self._within_budget(request.role) or not await self._reserve(
                    deadline
                ):
                    return _Attempt(None)
                outcome = await self._send(client, request, chosen, deadline, digest)
                if not outcome.retry:
                    return outcome
        return _Attempt(None)

    async def _send(  # noqa: PLR0911 - distinct failure classes stop or recover explicitly
        self,
        client: AsyncOpenAI,
        request: Request,
        chosen: str,
        deadline: float,
        digest: str,
    ) -> _Attempt:
        remaining = deadline - monotonic()
        if remaining <= 0:
            return _Attempt(None)
        provider: Provider = "openai" if chosen in OPENAI_MODELS else "anthropic"
        extra: _ReasoningOptions = {}
        if provider == "openai" and chosen.startswith("gpt-5-mini"):
            extra["reasoning_effort"] = "minimal"
        elif (
            provider == "anthropic"
            and request.effort is not None
            and self.effort_supported
        ):
            extra["reasoning_effort"] = request.effort
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ]
        started = monotonic()
        try:
            with anyio.fail_after(remaining):
                completion = await client.chat.completions.create(
                    model=chosen,
                    messages=messages,
                    tools=[request.tool],
                    tool_choice=(
                        "required"
                        if provider == "openai" or chosen.startswith("claude-haiku")
                        else "auto"
                    ),
                    parallel_tool_calls=False,
                    max_completion_tokens=request.max_tokens,
                    **extra,
                )
        except APIStatusError as exc:
            logged = self._log(request, chosen, started, (0, 0), digest, accepted=False)
            if (
                provider == "anthropic"
                and extra
                and exc.status_code == HTTP_BAD_REQUEST
                and "reasoning" in str(exc).lower()
            ):
                self.effort_supported = False
                return _Attempt(None, retry=logged)
            LOGGER.warning(
                "model_failed role=%s provider=%s status=%s",
                request.role,
                provider,
                exc.status_code,
            )
            eligible = self._status_failure(provider, exc)
            return _Attempt(None, logged and eligible)
        except TimeoutError:
            _ = self._log(request, chosen, started, (0, 0), digest, accepted=False)
            LOGGER.info(
                "model_deadline_exhausted role=%s provider=%s", request.role, provider
            )
            return _Attempt(None)
        except (APIError, OSError) as exc:
            logged = self._log(request, chosen, started, (0, 0), digest, accepted=False)
            LOGGER.warning(
                "model_failed role=%s provider=%s %s",
                request.role,
                provider,
                type(exc).__name__,
            )
            remembered = self._remember_cooldown(provider, UNAVAILABLE_COOLDOWN)
            return _Attempt(None, logged and remembered)
        except ValueError:
            _ = self._log(request, chosen, started, (0, 0), digest, accepted=False)
            LOGGER.warning(
                "model_invalid_response role=%s provider=%s", request.role, provider
            )
            return _Attempt(None)
        actual = self._actual_model(completion, chosen)
        logged = self._log(
            request,
            actual,
            started,
            self._tokens(completion),
            digest,
            accepted=self._arguments(request, completion) is not None,
        )
        return _Attempt(completion) if logged else _Attempt(None)

    def _status_failure(self, provider: Provider, exc: APIStatusError) -> bool:
        """Only provider unavailability can consume the separately approved fallback."""
        text = str(exc).lower()
        billing = exc.status_code == HTTP_PAYMENT_REQUIRED or (
            exc.status_code == HTTP_BAD_REQUEST
            and ("credit" in text or "billing" in text)
        )
        if billing or exc.status_code in {401, 403}:
            return self._remember_cooldown(provider, COOLDOWN_SECONDS)
        if exc.status_code == HTTP_RATE_LIMIT:
            return self._remember_cooldown(provider, RATE_LIMIT_COOLDOWN)
        if exc.status_code >= HTTP_SERVER_ERROR:
            return self._remember_cooldown(provider, UNAVAILABLE_COOLDOWN)
        return False

    def _remember_cooldown(self, provider: Provider, seconds: float) -> bool:
        if self.events is None:
            return self.openai is None
        key = COOLDOWN_KEY if provider == "anthropic" else OPENAI_COOLDOWN_KEY
        try:
            self.events.set_value(key, str(time.time() + seconds))
        except (OSError, sqlite3.Error):
            LOGGER.warning("model_cooldown_write_unavailable; calls disabled")
            self._block_accounting()
            return False
        return True

    def _log(  # noqa: PLR0913 - one row
        self,
        request: Request,
        model: str,
        started: float,
        tokens: tuple[int, int],
        digest: str,
        *,
        accepted: bool,
    ) -> bool:
        if self.events is None:
            return self.openai is None
        try:
            self.events.record_model_decision(
                role=request.role,
                model=model,
                latency_ms=int((monotonic() - started) * 1000),
                input_tokens=tokens[0],
                output_tokens=tokens[1],
                cost_usd=price(model, *tokens),
                accepted=accepted,
                summary=request.summary,
                input_sha256=digest,
                day=kst_day(),
            )
        except (OSError, sqlite3.Error):
            LOGGER.warning("model_log_failed")
            self._block_accounting()
            return False
        return True


def parse_arguments(arguments: str | None) -> dict[str, object] | None:
    """Parse tool arguments into a JSON object; anything else is None."""
    if not arguments:
        return None
    try:
        return ARGUMENTS.validate_json(arguments)
    except ValidationError:
        return None
