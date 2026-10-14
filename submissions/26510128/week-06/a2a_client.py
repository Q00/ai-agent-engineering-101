"""A2A v1.0 JSON-RPC client used by both the buyer and Challenge 2."""
from __future__ import annotations

import json
import math
import re
import uuid
from typing import Any
from urllib.parse import urljoin

import httpx

VERSION = "1.0"


def read_card(base_url: str) -> dict[str, Any]:
    response = httpx.get(
        urljoin(base_url.rstrip("/") + "/", ".well-known/agent-card.json"),
        headers={"A2A-Version": VERSION}, timeout=30,
    )
    response.raise_for_status()
    card = response.json()
    interfaces = [x for x in card.get("supportedInterfaces", [])
                  if x.get("protocolBinding") == "JSONRPC"
                  and str(x.get("protocolVersion")) == VERSION]
    if not interfaces:
        raise ValueError("seller has no A2A JSONRPC v1.0 interface")
    return card


def jsonrpc_url(card: dict[str, Any]) -> str:
    return next(x["url"] for x in card["supportedInterfaces"]
                if x.get("protocolBinding") == "JSONRPC"
                and str(x.get("protocolVersion")) == VERSION)


def rpc(url: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "jsonrpc": "2.0", "id": str(uuid.uuid4()),
        "method": method, "params": params,
    }
    response = httpx.post(
        url,
        json=payload,
        headers={"A2A-Version": VERSION},
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise RuntimeError(f"A2A {method} error: {data['error']}")
    result = data.get("result")
    if not isinstance(result, dict):
        raise ValueError(f"bad JSON-RPC result: {data}")
    return result.get("task", result)


def send_message(url: str, text: str, task_id: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "ROLE_USER", "messageId": str(uuid.uuid4()),
        "parts": [{"text": text}],
    }
    if task_id:
        message["taskId"] = task_id
    print(f"[a2a send] text={text!r} taskId={task_id or '-'} version={VERSION}", flush=True)
    task = rpc(url, "SendMessage", {"message": message})
    print(f"[a2a recv] taskId={task.get('id')} state={task_state(task)} text={task_text(task)!r}", flush=True)
    return task


def task_state(task: dict[str, Any]) -> str:
    return task.get("status", {}).get("state", "")


def task_text(task: dict[str, Any]) -> str:
    msg = task.get("status", {}).get("message") or {}
    return " ".join(str(x.get("text", "")) for x in msg.get("parts", []))


def asking_price(task: dict[str, Any]) -> int | None:
    # The Week 06 trade-test-specific app convention (not a general A2A rule).
    nums = re.findall(r"\d+(?:\.\d+)?", task_text(task))
    return math.ceil(float(nums[-1])) if nums else None


def receipt_price(task: dict[str, Any]) -> int | None:
    for artifact in task.get("artifacts", []):
        for part in artifact.get("parts", []):
            data = part.get("data")
            if isinstance(data, dict) and isinstance(data.get("price"), (int, float)):
                return int(data["price"])
    return None
