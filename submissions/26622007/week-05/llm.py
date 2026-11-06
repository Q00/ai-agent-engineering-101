"""Week 05: the model call the host makes, with the settings fixed in one place.

    OPENROUTER_API_KEY=<key> (or OPENAI_API_KEY with OPENAI_BASE_URL=https://openrouter.ai/api/v1)

- model openai/gpt-6-luna on OpenRouter, pinned to the OpenAI endpoint, no fallbacks,
  require_parameters so an unsupported parameter fails instead of being dropped
  (AGENT_MODEL / AGENT_PROVIDER switch the model and its pinned provider, e.g.
  anthropic/claude-haiku-5.5 on anthropic, for the second-model run)
- reasoning effort low (AGENT_REASONING); temperature is not sent (neither Luna nor
  Haiku 5.5 supports it)
- every request carries response_format = json_schema, strict (repository rule)
- every request body is read back from the wire (an httpx2 request hook) and the
  fields that matter are logged, so the log shows what was actually sent
- 429, 5xx, timeouts, and a reply without `choices` are retried with a growing wait
"""
from __future__ import annotations

import hashlib
import json
import os
import time

import httpx2
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

MODEL = os.environ.get("AGENT_MODEL", "openai/gpt-6-luna")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
PROVIDER = os.environ.get("AGENT_PROVIDER", "openai")
REASONING = os.environ.get("AGENT_REASONING", "low")
EXTRA_BODY = {
    "reasoning": {"effort": REASONING},
    "provider": {"only": [PROVIDER], "allow_fallbacks": False, "require_parameters": True},
}
RETRY_WAITS = (5, 10, 20, 40)       # seconds; a fifth failure gives up

FINAL = {                            # the host's last message, after its tool calls
    "type": "json_schema",
    "json_schema": {
        "name": "turn_summary",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "price", "reason"],
            "properties": {
                "action": {"type": "string",
                           "enum": ["propose", "accept_proposal", "reject_proposal", "refuse", "none"]},
                "price": {"type": ["integer", "null"]},
                "reason": {"type": "string"},
            },
        },
    },
}


def _api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("set OPENROUTER_API_KEY (never commit it)")
    return key


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:12]


class LLM:
    """One OpenAI-compatible client. `emit(event, **fields)` receives the wire log."""

    def __init__(self, emit):
        self.emit = emit
        self.calls = 0
        http = httpx2.Client(timeout=httpx2.Timeout(120.0, connect=15.0),
                             event_hooks={"request": [self._on_request]})
        self.client = OpenAI(api_key=_api_key(), base_url=BASE_URL, max_retries=0, http_client=http)

    def _on_request(self, request: httpx2.Request):
        """Log the body as sent. The Authorization header is never read or logged."""
        try:
            body = json.loads(request.content or b"{}")
        except ValueError:
            body = {}
        self.emit("llm_request_wire", url=str(request.url), model=body.get("model"),
                  response_format=body.get("response_format"), reasoning=body.get("reasoning"),
                  provider=body.get("provider"), tool_choice=body.get("tool_choice"),
                  temperature_sent="temperature" in body,
                  tools_sha=sha(body.get("tools")), n_messages=len(body.get("messages", [])),
                  keys=sorted(body))

    def chat(self, messages: list, tools: list, tool_choice: str = "auto"):
        last = None
        for attempt, wait in enumerate((*RETRY_WAITS, None), start=1):
            try:
                resp = self.client.chat.completions.create(
                    model=MODEL, messages=messages, tools=tools, tool_choice=tool_choice,
                    response_format=FINAL, extra_body=EXTRA_BODY)
                self.calls += 1
                if not getattr(resp, "choices", None):
                    raise RuntimeError("response without choices")
                return resp
            except APIStatusError as e:
                if e.status_code != 429 and e.status_code < 500:
                    self.emit("llm_error", attempt=attempt, status=e.status_code, error=str(e)[:500])
                    raise
                last = e
            except (APITimeoutError, APIConnectionError, RuntimeError) as e:
                last = e
            self.emit("llm_retry", attempt=attempt, error=str(last)[:300], wait=wait)
            if wait is None:
                break
            time.sleep(wait)
        raise RuntimeError(f"model call failed after retries: {last}")
