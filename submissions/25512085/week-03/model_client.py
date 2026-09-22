"""LM Studio native API transport for contractor model calls."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def normalize_server_url(value: str) -> str:
    """Return the LM Studio server root, accepting common endpoint suffixes."""
    url = value.rstrip("/")
    for suffix in ("/api/v1", "/v1"):
        if url.endswith(suffix):
            return url[:-len(suffix)]
    return url


def extract_message_content(payload: dict[str, Any]) -> str:
    """Read final message content while ignoring separate reasoning items."""
    output = payload.get("output")
    if not isinstance(output, list):
        raise RuntimeError("LM Studio response has no output list")

    messages = [
        item.get("content", "")
        for item in output
        if isinstance(item, dict) and item.get("type") == "message"
    ]
    content = "\n".join(part for part in messages if isinstance(part, str)).strip()
    if not content:
        raise RuntimeError("LM Studio response has no message content")
    return content


@dataclass
class Meter:
    calls: int = 0
    tokens: int = 0

    def add(self, input_tokens: int | None, output_tokens: int | None) -> None:
        self.calls += 1
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)


@dataclass(frozen=True)
class ModelSettings:
    provider: str
    model: str
    temperature: float
    server_url: str
    api_token: str | None
    timeout_seconds: float
    reasoning: str = "off"

    @classmethod
    def from_env(cls) -> "ModelSettings":
        model = os.environ.get("AGENT_MODEL")
        if not model:
            raise RuntimeError("AGENT_MODEL is not set")

        configured_url = (
            os.environ.get("LMSTUDIO_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or "http://127.0.0.1:1234"
        )
        return cls(
            provider="lmstudio",
            model=model,
            temperature=float(os.environ.get("AGENT_TEMPERATURE", "0.2")),
            server_url=normalize_server_url(configured_url),
            api_token=(
                os.environ.get("LMSTUDIO_API_TOKEN")
                or os.environ.get("OPENAI_API_KEY")
            ),
            timeout_seconds=float(os.environ.get("LMSTUDIO_TIMEOUT_SECONDS", "120")),
        )


class LMStudioCaller:
    """Call LM Studio once per bid with reasoning explicitly disabled."""

    def __init__(self, settings: ModelSettings, meter: Meter):
        self.settings = settings
        self.meter = meter

    def build_payload(self, system: str, user: str) -> dict[str, Any]:
        return {
            "model": self.settings.model,
            "system_prompt": system,
            "input": user,
            "temperature": self.settings.temperature,
            "reasoning": self.settings.reasoning,
            "max_output_tokens": 256,
            "store": False,
        }

    def __call__(self, system: str, user: str) -> str:
        body = json.dumps(self.build_payload(system, user)).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.settings.api_token:
            headers["Authorization"] = f"Bearer {self.settings.api_token}"

        request = Request(
            f"{self.settings.server_url}/api/v1/chat",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.settings.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"LM Studio returned HTTP {exc.code}: {detail}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(f"cannot reach LM Studio: {exc.reason}") from exc

        stats = payload.get("stats", {})
        self.meter.add(
            stats.get("input_tokens"),
            stats.get("total_output_tokens"),
        )
        return extract_message_content(payload)
