"""A stand-in for the OpenAI-compatible client: scripted tool calls, no network."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import ClassVar, Self


class FakeModel:
    """Return scripted tool arguments in order; record every request."""

    scripts: ClassVar[list[object]] = []
    requests: ClassVar[list[dict[str, object]]] = []

    def __init__(self, **_: object) -> None:
        """Mimic ``AsyncOpenAI(...)``."""
        self.chat = SimpleNamespace(completions=self)

    async def __aenter__(self) -> Self:
        """Mimic the async context manager."""
        return self

    async def __aexit__(self, *_: object) -> None:
        """Nothing to close."""

    @classmethod
    def script(cls, *scripts: object) -> type[FakeModel]:
        """Reset the scripted replies (dicts become JSON, strings pass through)."""
        cls.scripts = list(scripts)
        cls.requests = []
        return cls

    async def create(self, **kwargs: object) -> SimpleNamespace:
        """Pop the next scripted reply; ``None`` means the model made no tool call."""
        type(self).requests.append(kwargs)
        tools = kwargs.get("tools")
        name = ""
        if isinstance(tools, list) and tools:
            name = str(tools[0]["function"]["name"])  # pyright: ignore[reportIndexIssue]
        reply = type(self).scripts.pop(0) if type(self).scripts else None
        calls = []
        if reply is not None:
            arguments = reply if isinstance(reply, str) else json.dumps(reply)
            calls = [
                SimpleNamespace(
                    type="function",
                    function=SimpleNamespace(name=name, arguments=arguments),
                )
            ]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=calls))],
            usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=100),
        )
