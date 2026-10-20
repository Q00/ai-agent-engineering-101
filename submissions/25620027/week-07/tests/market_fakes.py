"""Offline model action queue and recording A2A transport."""

from __future__ import annotations

import json
from collections import deque

import httpx2
from pydantic import JsonValue, TypeAdapter

from market_a2a.models import HarnessError, ToolDispatch, ToolResult, ToolSpec


class ScriptedHarness:
    """Mutable recording harness substitutes only the unavailable model API."""

    def __init__(self, actions: list[tuple[str, JsonValue]]) -> None:
        """Queue model-selected actions and collect observable tool feedback."""
        self.actions: deque[tuple[str, JsonValue]] = deque(actions)
        self.states: list[JsonValue] = []
        self.feedback: list[ToolResult] = []

    async def run(
        self,
        prompt: str,
        state: JsonValue,
        tools: tuple[ToolSpec, ...],
        dispatch: ToolDispatch,
    ) -> ToolResult:
        """Apply queued actions until the dispatcher authorizes one."""
        _ = prompt, tools
        self.states.append(state)
        while self.actions:
            name, arguments = self.actions.popleft()
            result = await dispatch(name, json.dumps(arguments))
            self.feedback.append(result)
            if result.terminal:
                return result
        msg = "Scripted model exhausted"
        raise HarnessError(msg)


class ShopTransport:
    """Mutable in-memory wire peer records requests and returns a signed payload."""

    def __init__(self) -> None:
        """Initialize isolated request recording and the receipt payload."""
        self.requests: list[httpx2.Request] = []
        self.receipt: dict[str, JsonValue] = {
            "shop": "Stable Seller",
            "item": "lamp",
            "price": 120.0,
            "task": "trade-1",
            "sig_seller": "original-signature",
            "extension": {"number": 3.0},
        }

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        """Answer discovery and preserve task identity through checkout."""
        self.requests.append(request)
        if request.method == "GET":
            return httpx2.Response(
                200,
                json={
                    "name": "Stable Seller",
                    "skills": [],
                    "supportedInterfaces": [
                        {
                            "url": str(request.url.copy_with(path="/rpc")),
                            "protocolBinding": "JSONRPC",
                            "protocolVersion": "1.0",
                        }
                    ],
                },
            )
        payload = TypeAdapter(dict[str, JsonValue]).validate_json(request.content)
        continuing = "taskId" in json.dumps(payload)
        task: dict[str, JsonValue] = {
            "id": "trade-1",
            "contextId": "context-1",
            "status": {
                "state": (
                    "TASK_STATE_COMPLETED"
                    if continuing
                    else "TASK_STATE_INPUT_REQUIRED"
                ),
                "message": {"parts": [{"text": "The asking price is 120."}]},
            },
        }
        if continuing:
            task["artifacts"] = [{"parts": [{"data": self.receipt}]}]
        return httpx2.Response(200, json={"result": {"task": task}})
