"""Provider failures use one bounded, separately authorized OpenAI route."""

from __future__ import annotations

import sqlite3
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Final, Self

import anyio
import httpx2
import pytest
from openai import APIConnectionError, APIStatusError
from openai.types.chat import ChatCompletion, ChatCompletionFunctionToolParam
from pydantic import TypeAdapter

from market_a2a.anthropic_fallback import ANTHROPIC_URL, HAIKU_MODEL
from market_a2a.models import HarnessError
from market_a2a.paid_fallback import DailyQuota
from r3 import model_client
from r3.events import EventLedger
from r3.model_client import (
    ACCOUNTING_BLOCK_KEY,
    COOLDOWN_KEY,
    COOLDOWN_SECONDS,
    OPENAI_COOLDOWN_KEY,
    SONNET_MODEL,
    ModelClient,
    Request,
    Role,
    kst_day,
    price,
    tool_param,
)
from r3.model_failover import (
    OPENAI_MODEL,
    OPENAI_TRADE_MODEL,
    OPENAI_URL,
    OpenAIFailover,
    validate_openai_fallback_env,
)

if TYPE_CHECKING:
    from pathlib import Path


TOOLS: Final = TypeAdapter(list[ChatCompletionFunctionToolParam])
QUOTA_ROW: Final = TypeAdapter(tuple[int])


@dataclass(frozen=True, slots=True)
class Step:
    """One offline HTTP result, with optional delay and actual snapshot name."""

    status: int = 200
    arguments: str | None = '{"ok": true}'
    message: str = "provider unavailable"
    delay: float = 0.0
    model: str | None = None


@dataclass(frozen=True, slots=True)
class Seen:
    """The placeholder credential/endpoint and HTTP arguments seen by the fake."""

    endpoint: str
    key: str
    arguments: dict[str, object] = field(default_factory=dict)


class ProviderAPI:
    """Exercise the real routing and quota paths without making network calls."""

    steps: ClassVar[dict[str, deque[Step]]] = {}
    seen: ClassVar[list[Seen]] = []

    def __init__(self, *, base_url: str, api_key: str, max_retries: int) -> None:
        """Bind placeholder credentials and a direct provider endpoint."""
        assert max_retries == 0
        self.endpoint: str = base_url
        self.key: str = api_key

    @property
    def chat(self) -> Self:
        """Mirror the SDK namespace."""
        return self

    @property
    def completions(self) -> Self:
        """Mirror the SDK completion namespace."""
        return self

    async def __aenter__(self) -> Self:
        """No network initialization is performed."""
        return self

    async def __aexit__(self, *_args: object) -> None:
        """No resource is retained."""

    async def create(self, **kwargs: object) -> ChatCompletion:
        """Return one scripted typed result or provider error."""
        self.seen.append(Seen(self.endpoint, self.key, kwargs))
        step = self.steps[self.endpoint].popleft()
        if step.delay:
            await anyio.sleep(step.delay)
        request = httpx2.Request("POST", self.endpoint + "chat/completions")
        if step.status == 0:
            raise APIConnectionError(request=request)
        if step.status != 200:
            raise APIStatusError(
                step.message,
                response=httpx2.Response(step.status, request=request),
                body=None,
            )
        tools = TOOLS.validate_python(kwargs["tools"])
        name = tools[0]["function"]["name"]
        calls = (
            [
                {
                    "id": "tool-1",
                    "type": "function",
                    "function": {"name": name, "arguments": step.arguments},
                }
            ]
            if step.arguments is not None
            else []
        )
        return ChatCompletion.model_validate(
            {
                "id": "completion-1",
                "object": "chat.completion",
                "created": 0,
                "model": step.model or kwargs["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls" if calls else "stop",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": calls,
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 100,
                    "total_tokens": 1100,
                },
            }
        )


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> type[ProviderAPI]:
    ProviderAPI.steps = {ANTHROPIC_URL: deque(), OPENAI_URL: deque()}
    ProviderAPI.seen = []
    monkeypatch.setattr(model_client, "AsyncOpenAI", ProviderAPI)
    return ProviderAPI


def make_client(
    tmp_path: Path, *, limit: int = 5000, primary: str = "anthropic-placeholder"
) -> tuple[ModelClient, EventLedger, DailyQuota]:
    events = EventLedger(tmp_path / "events.sqlite")
    quota = DailyQuota(tmp_path / "quota.sqlite", limit)
    return (
        ModelClient(
            primary,
            events=events,
            quota=quota,
            daily_usd=6,
            openai=OpenAIFailover("openai-placeholder"),
        ),
        events,
        quota,
    )


def request_for(role: Role = "strategy", *, seconds: float = 1) -> Request:
    return Request(
        role=role,
        model=SONNET_MODEL if role == "strategy" else HAIKU_MODEL,
        system="Return only the requested tool.",
        user="An offline provider-routing test.",
        tool=tool_param("route_test", "Return one result", {"type": "object"}),
        max_tokens=64,
        deadline_seconds=seconds,
        effort="high",
        summary="offline provider test",
    )


def reserved(quota: DailyQuota) -> int:
    with sqlite3.connect(quota.path) as db:
        row = QUOTA_ROW.validate_python(
            db.execute("SELECT coalesce(sum(attempts),0) FROM daily_usage").fetchone()
        )
    return row[0]


def record_spend(events: EventLedger, dollars: float) -> None:
    events.record_model_decision(
        role="strategy",
        model=SONNET_MODEL,
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        cost_usd=dollars,
        accepted=True,
        summary="prior spend",
        input_sha256="",
        day=kst_day(),
    )


def test_healthy_claude_does_not_send_the_openai_key_or_an_extra_request(
    tmp_path: Path, providers: type[ProviderAPI]
) -> None:
    client, events, quota = make_client(tmp_path)
    providers.steps[ANTHROPIC_URL].append(Step())
    reply = anyio.run(client.call, request_for())
    assert reply is not None
    assert reply.model == SONNET_MODEL
    assert [(r.endpoint, r.key) for r in providers.seen] == [
        (ANTHROPIC_URL, "anthropic-placeholder")
    ]
    assert reserved(quota) == 1
    assert events.spend(kst_day())[1] == 1


@pytest.mark.parametrize("status", [0, 400, 401, 403, 402, 429, 503])
def test_provider_unavailability_uses_one_separate_openai_request(
    tmp_path: Path, providers: type[ProviderAPI], status: int
) -> None:
    client, events, quota = make_client(tmp_path)
    providers.steps[ANTHROPIC_URL].append(
        Step(status, message="credit balance unavailable")
    )
    providers.steps[OPENAI_URL].append(Step(model="gpt-5-mini-2025-08-07"))
    reply = anyio.run(client.call, request_for())
    assert reply is not None
    assert reply.arguments == '{"ok": true}'
    assert reply.model == "gpt-5-mini-2025-08-07"
    assert reply.cost_usd == pytest.approx(0.00045)
    assert [(r.endpoint, r.key) for r in providers.seen] == [
        (ANTHROPIC_URL, "anthropic-placeholder"),
        (OPENAI_URL, "openai-placeholder"),
    ]
    alternate = providers.seen[-1].arguments
    assert alternate["model"] == OPENAI_MODEL
    assert alternate["tool_choice"] == "required"
    assert alternate["parallel_tool_calls"] is False
    assert alternate["reasoning_effort"] == "minimal"
    assert reserved(quota) == 2
    assert events.spend(kst_day()) == (reply.cost_usd, 2)


@pytest.mark.parametrize("role", ["scout", "tactical", "strategy", "trade"])
def test_every_active_role_uses_the_approved_route(
    tmp_path: Path, providers: type[ProviderAPI], role: Role
) -> None:
    client, _, _ = make_client(tmp_path)
    providers.steps[ANTHROPIC_URL].append(Step(429))
    providers.steps[OPENAI_URL].append(Step())
    reply = anyio.run(client.call, request_for(role))
    expected = OPENAI_TRADE_MODEL if role == "trade" else OPENAI_MODEL
    assert reply is not None
    assert reply.model == expected
    alternate = providers.seen[-1].arguments
    assert alternate["model"] == expected
    if role == "trade":
        assert "reasoning_effort" not in alternate
        assert reply.cost_usd == pytest.approx(0.00056)


def test_schema_error_does_not_trigger_a_second_paid_provider(
    tmp_path: Path, providers: type[ProviderAPI]
) -> None:
    client, _, quota = make_client(tmp_path)
    providers.steps[ANTHROPIC_URL].append(Step(400, message="invalid tool schema"))
    assert anyio.run(client.call, request_for()) is None
    assert reserved(quota) == len(providers.seen) == 1


def test_valid_response_without_tool_is_charged_but_not_sent_again(
    tmp_path: Path, providers: type[ProviderAPI]
) -> None:
    client, events, quota = make_client(tmp_path)
    providers.steps[ANTHROPIC_URL].append(Step(arguments=None))
    reply = anyio.run(client.call, request_for())
    assert reply is not None
    assert reply.arguments is None
    assert reply.cost_usd > 0
    assert reserved(quota) == len(providers.seen) == 1
    assert events.model_summary(1)["strategy"]["accepted"] == 0


def test_primary_cooldown_survives_new_clients_and_recovers(
    tmp_path: Path, providers: type[ProviderAPI], monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [2_000_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    client, events, quota = make_client(tmp_path)
    providers.steps[ANTHROPIC_URL].extend([Step(402), Step()])
    providers.steps[OPENAI_URL].extend([Step(), Step()])
    assert anyio.run(client.call, request_for()) is not None
    fresh = ModelClient(
        "anthropic-placeholder", events=events, quota=quota, openai=client.openai
    )
    assert anyio.run(fresh.call, request_for()) is not None
    assert [r.endpoint for r in providers.seen] == [
        ANTHROPIC_URL,
        OPENAI_URL,
        OPENAI_URL,
    ]
    clock[0] += COOLDOWN_SECONDS + 1
    assert anyio.run(fresh.call, request_for()) is not None
    assert providers.seen[-1].endpoint == ANTHROPIC_URL


def test_both_failures_stop_and_openai_cooldown_does_not_block_recovered_claude(
    tmp_path: Path, providers: type[ProviderAPI], monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [2_000_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    client, events, quota = make_client(tmp_path)
    providers.steps[ANTHROPIC_URL].extend([Step(429), Step()])
    providers.steps[OPENAI_URL].append(Step(402))
    assert anyio.run(client.call, request_for()) is None
    assert events.get_value(OPENAI_COOLDOWN_KEY) is not None
    assert anyio.run(client.call, request_for()) is None
    assert reserved(quota) == len(providers.seen) == 2
    clock[0] += 61
    assert anyio.run(client.call, request_for()) is not None
    assert providers.seen[-1].endpoint == ANTHROPIC_URL


def test_legacy_cooldown_blocks_only_primary_when_alternate_is_enabled(
    tmp_path: Path, providers: type[ProviderAPI]
) -> None:
    client, events, quota = make_client(tmp_path)
    events.set_value(COOLDOWN_KEY, str(time.time() + 100))
    providers.steps[OPENAI_URL].append(Step())
    assert anyio.run(client.call, request_for()) is not None
    legacy = ModelClient("anthropic-placeholder", events=events, quota=quota)
    assert legacy.pick_model(SONNET_MODEL, "strategy") is None
    assert [r.endpoint for r in providers.seen] == [OPENAI_URL]


def test_shared_quota_exhaustion_cannot_be_bypassed_by_switching_provider(
    tmp_path: Path, providers: type[ProviderAPI]
) -> None:
    client, _, quota = make_client(tmp_path, limit=1)
    providers.steps[ANTHROPIC_URL].append(Step(429))
    assert anyio.run(client.call, request_for()) is None
    assert reserved(quota) == len(providers.seen) == 1


@pytest.mark.parametrize("role", ["scout", "tactical", "strategy", "trade"])
def test_shared_spend_stop_blocks_both_providers(
    tmp_path: Path, providers: type[ProviderAPI], role: Role
) -> None:
    client, events, quota = make_client(tmp_path)
    record_spend(events, 18 if role == "trade" else 12)
    events.set_value(COOLDOWN_KEY, str(time.time() + 100))
    assert anyio.run(client.call, request_for(role)) is None
    assert reserved(quota) == len(providers.seen) == 0


def test_compatibility_retry_reserves_and_logs_both_physical_requests(
    tmp_path: Path, providers: type[ProviderAPI]
) -> None:
    client, events, quota = make_client(tmp_path)
    providers.steps[ANTHROPIC_URL].extend(
        [Step(400, message="Unknown reasoning parameter"), Step()]
    )
    reply = anyio.run(client.call, request_for())
    assert reply is not None
    assert reply.model == SONNET_MODEL
    assert "reasoning_effort" in providers.seen[0].arguments
    assert "reasoning_effort" not in providers.seen[1].arguments
    assert reserved(quota) == events.spend(kst_day())[1] == 2


def test_fallback_does_not_restart_the_request_deadline(
    tmp_path: Path, providers: type[ProviderAPI], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = make_client(tmp_path)
    client.quota = None
    clock = [0.0]
    monkeypatch.setattr(model_client, "monotonic", lambda: clock[0])
    create = ProviderAPI.create

    async def consume_primary_budget(
        self: ProviderAPI, **kwargs: object
    ) -> ChatCompletion:
        if self.endpoint == ANTHROPIC_URL:
            clock[0] = 0.04
        return await create(self, **kwargs)

    monkeypatch.setattr(ProviderAPI, "create", consume_primary_budget)
    providers.steps[ANTHROPIC_URL].append(Step(429))
    providers.steps[OPENAI_URL].append(Step(delay=0.02))
    assert anyio.run(client.call, request_for(seconds=0.05)) is None
    assert len(providers.seen) == 2


def test_full_primary_timeout_has_no_time_for_another_request(
    tmp_path: Path, providers: type[ProviderAPI]
) -> None:
    client, _, quota = make_client(tmp_path)
    providers.steps[ANTHROPIC_URL].append(Step(delay=0.1))
    assert anyio.run(client.call, request_for(seconds=0.03)) is None
    assert len(providers.seen) <= 1
    assert reserved(quota) <= 1
    assert all(r.endpoint == ANTHROPIC_URL for r in providers.seen)


def test_uncertain_cooldown_ledger_blocks_instead_of_selecting_an_alternate(
    tmp_path: Path, providers: type[ProviderAPI], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, events, quota = make_client(tmp_path)
    original = events.get_value

    def read(key: str) -> str | None:
        if key == COOLDOWN_KEY:
            raise OSError
        return original(key)

    monkeypatch.setattr(events, "get_value", read)
    assert anyio.run(client.call, request_for()) is None
    assert reserved(quota) == len(providers.seen) == 0


def test_failed_usage_record_does_not_allow_a_fallback_attempt(
    tmp_path: Path, providers: type[ProviderAPI], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, events, quota = make_client(tmp_path)
    providers.steps[ANTHROPIC_URL].append(Step(429))

    def unavailable(**_kwargs: object) -> None:
        raise OSError

    monkeypatch.setattr(events, "record_model_decision", unavailable)
    assert anyio.run(client.call, request_for()) is None
    assert reserved(quota) == len(providers.seen) == 1


def test_explicit_openai_only_environment_still_requires_gate_and_shared_quota(
    tmp_path: Path, providers: type[ProviderAPI], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("AX_R3_OPENAI_FALLBACK", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-placeholder")
    monkeypatch.setenv("AX_R3_TRADE_MODEL", "1")
    events = EventLedger(tmp_path / "events.sqlite")
    monkeypatch.delenv("AX_PAID_QUOTA_PATH", raising=False)
    assert ModelClient.from_env(events, gate="AX_R3_TRADE_MODEL") is None
    monkeypatch.setenv("AX_PAID_QUOTA_PATH", str(tmp_path / "quota.sqlite"))
    client = ModelClient.from_env(events, gate="AX_R3_TRADE_MODEL")
    assert client is not None
    providers.steps[OPENAI_URL].append(Step())
    assert anyio.run(client.call, request_for("trade")) is not None
    assert [r.endpoint for r in providers.seen] == [OPENAI_URL]
    monkeypatch.setenv("AX_R3_TRADE_MODEL", "0")
    assert ModelClient.from_env(events, gate="AX_R3_TRADE_MODEL") is None
    monkeypatch.setenv("AX_R3_TRADE_MODEL", "1")
    monkeypatch.setenv("AX_R3_OPENAI_FALLBACK", "0")
    assert ModelClient.from_env(events, gate="AX_R3_TRADE_MODEL") is None


@pytest.mark.parametrize(
    ("flag", "key", "model"),
    [
        ("yes", "placeholder", OPENAI_MODEL),
        ("1", "", OPENAI_MODEL),
        ("1", "placeholder", "unapproved-model"),
    ],
)
def test_invalid_explicit_fallback_configuration_fails_closed(
    monkeypatch: pytest.MonkeyPatch, flag: str, key: str, model: str
) -> None:
    monkeypatch.setenv("AX_R3_OPENAI_FALLBACK", flag)
    monkeypatch.setenv("OPENAI_API_KEY", key)
    monkeypatch.setenv("AX_R3_OPENAI_MODEL", model)
    with pytest.raises(HarnessError):
        _ = validate_openai_fallback_env()


def test_disabled_configuration_ignores_an_unrelated_openai_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AX_R3_OPENAI_FALLBACK", "0")
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-placeholder")
    assert validate_openai_fallback_env() is None


def test_openai_prices_include_the_actual_snapshot_and_hide_secret_repr() -> None:
    assert price("gpt-5-mini-2025-08-07", 1000, 100) == pytest.approx(0.00045)
    assert price(OPENAI_TRADE_MODEL, 1000, 100) == pytest.approx(0.00056)
    assert "not-a-real-secret" not in repr(OpenAIFailover("not-a-real-secret"))


@pytest.mark.parametrize("alternate", [False, True])
def test_successful_but_unrecorded_usage_blocks_new_clients_and_both_providers(
    tmp_path: Path,
    providers: type[ProviderAPI],
    monkeypatch: pytest.MonkeyPatch,
    *,
    alternate: bool,
) -> None:
    client, events, quota = make_client(tmp_path)
    endpoint = OPENAI_URL if alternate else ANTHROPIC_URL
    if alternate:
        events.set_value(COOLDOWN_KEY, str(time.time() + 100))
    providers.steps[endpoint].append(Step())

    def unavailable(**_kwargs: object) -> None:
        raise OSError

    monkeypatch.setattr(events, "record_model_decision", unavailable)
    assert anyio.run(client.call, request_for()) is None
    assert events.get_value(ACCOUNTING_BLOCK_KEY) == "1"
    # A fresh client must read the shared block, rather than reset the spend to zero.
    fresh = ModelClient(
        "anthropic-placeholder", events=events, quota=quota, openai=client.openai
    )
    assert anyio.run(fresh.call, request_for("trade")) is None
    assert reserved(quota) == len(providers.seen) == 1


def test_accounting_marker_blocks_new_clients_if_usage_and_kv_writes_both_fail(
    tmp_path: Path, providers: type[ProviderAPI], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, events, quota = make_client(tmp_path)
    providers.steps[ANTHROPIC_URL].append(Step())

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise OSError

    monkeypatch.setattr(events, "record_model_decision", unavailable)
    monkeypatch.setattr(events, "set_value", unavailable)
    assert anyio.run(client.call, request_for()) is None
    assert events.path.with_suffix(events.path.suffix + ".model-blocked").is_file()
    fresh = ModelClient(
        "anthropic-placeholder", events=events, quota=quota, openai=client.openai
    )
    assert anyio.run(fresh.call, request_for()) is None
    assert reserved(quota) == len(providers.seen) == 1
