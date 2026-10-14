"""Minimal LM Studio transport for the Week 04 negotiation lab.

Connection settings stay outside the repository:
  LMSTUDIO_BASE_URL (defaults to http://127.0.0.1:1234)
  AGENT_MODEL (required; Week 03 used qwen/qwen3.8-27b)
  AGENT_TEMPERATURE (defaults to 0.2)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class Meter:
    """Count generation calls; Week 4 separately records reader calls."""

    calls: int = 0


@dataclass(frozen=True)
class ModelSettings:
    model: str
    temperature: float
    server_url: str
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> "ModelSettings":
        model = os.environ.get("AGENT_MODEL")
        if not model:
            raise RuntimeError("AGENT_MODEL is not set")
        return cls(
            model=model,
            temperature=float(os.environ.get("AGENT_TEMPERATURE", "0.2")),
            server_url=os.environ.get("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234").rstrip("/"),
            timeout_seconds=float(os.environ.get("LMSTUDIO_TIMEOUT_SECONDS", "120")),
        )


class LMStudioCaller:
    """One non-streaming chat completion with reasoning disabled."""

    def __init__(self, settings: ModelSettings, meter: Meter):
        self.settings = settings
        self.meter = meter

    def __call__(self, system: str, user: str) -> str:
        payload = {
            "model": self.settings.model,
            "system_prompt": system,
            "input": user,
            "temperature": self.settings.temperature,
            "reasoning": "off",
            "max_output_tokens": 256,
            "store": False,
        }
        request = Request(
            f"{self.settings.server_url}/api/v1/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.settings.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"LM Studio returned HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"cannot reach LM Studio: {exc.reason}") from exc

        messages = [
            item.get("content", "")
            for item in data.get("output", [])
            if isinstance(item, dict) and item.get("type") == "message"
        ]
        content = "\n".join(text for text in messages if isinstance(text, str)).strip()
        if not content:
            raise RuntimeError("LM Studio response has no message content")
        self.meter.calls += 1
        return content
