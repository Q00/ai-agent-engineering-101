from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest
from openai import APIStatusError, AsyncOpenAI
from pydantic import JsonValue, TypeAdapter

from market_a2a.harness import Harness
from market_a2a.model_provider import DEFAULT_MODEL, FREE_ROUTER
from market_a2a.models import HarnessError
from market_a2a.paid_fallback import DailyQuota, OpenAIFallback

if TYPE_CHECKING:
    from pathlib import Path


def test_quota_survives_restart_and_rolls_over_at_korean_midnight(
    tmp_path: Path,
) -> None:
    path = tmp_path / "quota.sqlite3"
    now = datetime(2026, 10, 21, 14, 59, tzinfo=UTC)
    assert DailyQuota(path, 1).reserve(now) == 1
    with pytest.raises(HarnessError, match="limit reached"):
        _ = DailyQuota(path, 1).reserve(now)
    assert DailyQuota(path, 1).reserve(now + timedelta(minutes=1)) == 1


def test_parallel_reservations_never_exceed_limit(tmp_path: Path) -> None:
    quota = DailyQuota(tmp_path / "quota.sqlite3", 5)

    def attempt(_index: int) -> int:
        try:
            return quota.reserve()
        except HarnessError:
            return 0

    with ThreadPoolExecutor(max_workers=8) as workers:
        values = tuple(workers.map(attempt, range(16)))
    assert sorted(value for value in values if value) == [1, 2, 3, 4, 5]


def test_lost_ledger_cannot_reset_paid_quota(tmp_path: Path) -> None:
    quota = DailyQuota(tmp_path / "quota.sqlite3", 1)
    assert quota.reserve() == 1
    quota.path.unlink()
    with pytest.raises(HarnessError, match="missing"):
        _ = DailyQuota(quota.path, 1)
    with pytest.raises(HarnessError, match="disappeared"):
        _ = quota.reserve()


@pytest.mark.parametrize("limit", [0, 5001])
def test_invalid_or_excessive_paid_limit_is_rejected(
    tmp_path: Path, limit: int,
) -> None:
    with pytest.raises(HarnessError, match="between1 and5000"):
        _ = DailyQuota(tmp_path / "quota.sqlite3", limit)


def test_failed_paid_attempt_is_counted_without_sdk_retry(tmp_path: Path) -> None:
    requests: list[str] = []

    def fail(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.url.host)
        return httpx2.Response(500, json={"error": {"message": "offline outage"}})

    async def scenario() -> None:
        async with (
            httpx2.AsyncClient(transport=httpx2.MockTransport(fail)) as http,
            AsyncOpenAI(api_key="offline", max_retries=0, http_client=http) as client,
        ):
            fallback = OpenAIFallback(client, DailyQuota(tmp_path / "quota.sqlite3", 1))
            with pytest.raises(APIStatusError):
                _ = await fallback.complete([], [], print)
            with pytest.raises(HarnessError, match="limit reached"):
                _ = await fallback.complete([], [], print)
            assert requests == ["api.openai.com"]

    anyio.run(scenario)


@pytest.mark.parametrize("model", [FREE_ROUTER, DEFAULT_MODEL])
def test_free_outage_uses_original_openai_with_persistent_quota(
    tmp_path: Path, model: str,
) -> None:
    requested: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requested.append(request.url.host)
        if request.url.host == "openrouter.ai":
            return httpx2.Response(404, json={"error": {"message": "No free endpoint"}})
        body = TypeAdapter(dict[str, JsonValue]).validate_json(request.content)
        assert body["model"] == DEFAULT_MODEL
        assert body["max_completion_tokens"] == 450
        assert "provider" not in body
        assert request.headers["authorization"] == "Bearer offline-openai"
        return httpx2.Response(200, json={
            "id": "paid", "object": "chat.completion", "created": 0,
            "model": DEFAULT_MODEL,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    async def scenario() -> None:
        async with (
            httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http,
            AsyncOpenAI(api_key="offline-free", base_url="https://openrouter.ai/api/v1",
                        max_retries=0, http_client=http) as free,
            AsyncOpenAI(api_key="offline-openai", max_retries=0,
                        http_client=http) as paid,
        ):
            harness = Harness(
                free if model == FREE_ROUTER else paid, print, model=model,
                fallback=OpenAIFallback(
                    paid, DailyQuota(tmp_path / "quota.sqlite3", 1),
                ),
            )
            _ = await harness.complete([], [])
            with pytest.raises(HarnessError, match="limit reached"):
                _ = await harness.complete([], [])
            assert requested == (
                ["openrouter.ai", "api.openai.com"]
                if model == FREE_ROUTER else ["api.openai.com"]
            )
            assert harness.calls == 1

    anyio.run(scenario)
