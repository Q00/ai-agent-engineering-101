from __future__ import annotations

from pathlib import Path

import httpx2
import pytest
from openai import OpenAI

from cnp.adapter import ChatAdapter
from cnp.domain import ContractId
from cnp.records import Announcement
from cnp.settings import load_inputs


def announcement() -> Announcement:
    return Announcement(
        contract_id=ContractId("1:T01"), task_id="T01", text="calculate", deadline="test"
    )


def test_adapter_sends_only_two_messages_and_no_tools() -> None:
    # Given a wire-level HTTP fixture and real SDK serialization.
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.content.decode())
        return httpx2.Response(
            200,
            json={
                "id": "fixture",
                "object": "chat.completion",
                "created": 0,
                "model": "fixture",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"bid":true,"confidence":91,"reason":"x"}',
                        },
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            },
        )

    settings, _ = load_inputs(Path(__file__).resolve().parents[1])
    with (
        httpx2.Client(transport=httpx2.MockTransport(handler)) as http,
        OpenAI(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            http_client=http,
            max_retries=0,
        ) as api,
    ):
        # When sending one bid.
        reply = ChatAdapter(api, settings).bid("test-role", announcement())
    # Then raw data and usage survive; there is exactly one HTTP call.
    assert reply.tokens == 20
    assert reply.error == ""
    assert len(seen) == 1
    assert '"tools"' not in seen[0]
    assert '"gold"' not in seen[0]


@pytest.mark.parametrize("code", [401, 402, 429, 503])
def test_http_failure_kept_without_retry(code: int) -> None:
    # Given a provider refusal.
    calls: list[int] = []

    def handler(_request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return httpx2.Response(code, json={"error": {"message": "fixture refusal", "code": code}})

    settings, _ = load_inputs(Path(__file__).resolve().parents[1])
    with (
        httpx2.Client(transport=httpx2.MockTransport(handler)) as http,
        OpenAI(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            http_client=http,
            max_retries=0,
        ) as api,
    ):
        # When the SDK receives the HTTP response.
        reply = ChatAdapter(api, settings).bid("test-role", announcement())
    # Then it is an API failure, not a model's abstention.
    assert reply.error == "api_error"
    assert reply.stop_batch == (code in (401, 402, 429))
    assert calls == [1]
