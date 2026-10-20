"""Harness-driven A2A buyer used by the week-06 trade test."""

from __future__ import annotations

import argparse
import json
import re
import uuid

import httpx

from codex_harness import decide


HEADERS = {"A2A-Version": "1.0"}


def rpc(url: str, method: str, params: dict) -> tuple[dict | None, dict | None]:
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}
    response = httpx.post(url, json=body, headers=HEADERS, timeout=200)
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        return None, payload["error"]
    result = payload["result"]
    return result.get("task", result), None


def send(url: str, text: str, task_id: str | None = None) -> tuple[dict | None, dict | None]:
    message = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        message["taskId"] = task_id
    return rpc(url, "SendMessage", {"message": message})


def state(task: dict | None) -> str:
    return (task or {}).get("status", {}).get("state", "")


def status_text(task: dict | None) -> str:
    parts = (task or {}).get("status", {}).get("message", {}).get("parts", [])
    return " ".join(part.get("text", "") for part in parts)


def last_number(text: str) -> int | None:
    values = re.findall(r"\d+", text)
    return int(values[-1]) if values else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", required=True, type=int)
    args = parser.parse_args()

    base = args.url.rstrip("/")
    card_response = httpx.get(base + "/.well-known/agent-card.json", timeout=20)
    card_response.raise_for_status()
    card = card_response.json()
    interface = (card.get("supportedInterfaces") or [{}])[0]
    rpc_url = interface.get("url") or base + "/"
    print("CARD", json.dumps({"name": card.get("name"), "skills": card.get("skills")}, ensure_ascii=False))

    task, error = send(rpc_url, f"I want to buy a {args.item}")
    if error or task is None:
        print("ERROR", error)
        return 1
    task_id = task.get("id")
    history: list[dict] = []

    for _ in range(8):
        current_state = state(task)
        text = status_text(task)
        print("STATE", current_state, text)
        if current_state == "TASK_STATE_COMPLETED":
            return 0
        if current_state in {"TASK_STATE_REJECTED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED"}:
            return 0
        if current_state != "TASK_STATE_INPUT_REQUIRED":
            return 1
        ask = last_number(text)
        history.append({"seller": text, "ask": ask})
        decision = decide(
            f"""You are the buyer decision engine for an A2A purchase.
Return only the required JSON object.
Seller: {card.get('name')!r}. Item: {args.item}. Hard total budget: {args.budget}.
Seller's latest message: {text!r}. Parsed current ask: {ask}.
Negotiation history: {json.dumps(history)}
Choose \"offer\" with an integer price no greater than budget when a deal is possible.
The seller accepts any offer at or above its reserve. For a lamp with budget 120, offer 100 first.
If the current ask proves the item cannot fit the budget, especially a bookshelf ask above 230, choose \"walk\".
Keep message to one short English sentence."""
        )
        if decision.get("action") == "walk":
            print("WALK", decision.get("message"))
            return 0
        offer = decision.get("price")
        if not isinstance(offer, int):
            raise RuntimeError(f"buyer decision lacks integer offer: {decision}")
        offer = max(0, min(args.budget, offer))
        history.append({"buyer_offer": offer})
        task, error = send(rpc_url, str(offer), task_id)
        if error or task is None:
            print("ERROR", error)
            return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
