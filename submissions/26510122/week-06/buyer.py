"""A2A v1.0 buyer that asks Codex to choose each offer or walk-away."""

from __future__ import annotations

import argparse
import json
import uuid

import httpx

from harness import decide, trace


def card_and_url(client: httpx.Client, base_url: str) -> tuple[dict, str]:
    card_url = base_url.rstrip("/") + "/.well-known/agent-card.json"
    response = client.get(card_url)
    response.raise_for_status()
    card = response.json()
    interfaces = [
        interface for interface in card.get("supportedInterfaces", [])
        if interface.get("protocolBinding") == "JSONRPC"
        and str(interface.get("protocolVersion")) == "1.0"
    ]
    if not interfaces:
        raise RuntimeError("seller has no A2A 1.0 JSON-RPC interface")
    trace("card", url=card_url, name=card.get("name"), skills=card.get("skills"))
    return card, interfaces[0]["url"]


def rpc(client: httpx.Client, url: str, method: str, params: dict) -> dict:
    request = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}
    trace("a2a_request", url=url, method=method, params=params)
    response = client.post(url, json=request, headers={"A2A-Version": "1.0"})
    response.raise_for_status()
    result = response.json()
    trace("a2a_response", response=result)
    if "error" in result:
        raise RuntimeError(f"A2A error: {result['error']}")
    return result["result"].get("task", result["result"])


def send(client: httpx.Client, url: str, text: str, task_id: str | None = None) -> dict:
    message = {
        "role": "ROLE_USER", "messageId": str(uuid.uuid4()),
        "parts": [{"text": text}],
    }
    if task_id:
        message["taskId"] = task_id
    return rpc(client, url, "SendMessage", {"message": message})


def state(task: dict) -> str:
    return task.get("status", {}).get("state", "")


def status_text(task: dict) -> str:
    return " ".join(
        part.get("text", "") for part in task.get("status", {}).get("message", {}).get("parts", [])
    )


def shop(base_url: str, item: str, budget: int, max_turns: int = 8) -> dict:
    with httpx.Client(timeout=200) as client:
        card, url = card_and_url(client, base_url)
        task = send(client, url, f"I want to buy a {item}")
        history: list[dict] = []
        offers: list[int] = []
        for _ in range(max_turns):
            current = state(task)
            if current != "TASK_STATE_INPUT_REQUIRED":
                break
            message = status_text(task)
            history.append({"seller_message": message})
            answer = decide("buyer", {
                "item": item, "private_budget": budget,
                "seller_card_name": card.get("name"),
                "seller_skills": card.get("skills", []),
                "seller_message": message, "history": history,
                "objective": (
                    "Buy this item if the seller can accept a price within budget. "
                    "Make a concrete offer; a high opening ask does not prove a deal is impossible. "
                    "Increase toward the budget if needed. Walk away if agreement is not possible."
                ),
            }, ["offer", "walk"])
            if answer["action"] == "walk":
                trace("buyer_walk", item=item, budget=budget, task_id=task.get("id"))
                break
            proposed = int(answer["price"])
            offer = min(max(1, proposed), budget)
            if offer != proposed:
                trace("price_guard", reason="buyer offer outside budget", proposed=proposed, effective=offer)
            offers.append(offer)
            history[-1]["buyer_offer"] = offer
            task = send(client, url, str(offer), task["id"])
        outcome = {
            "item": item, "budget": budget, "state": state(task),
            "offers": offers, "task_id": task.get("id"),
            "artifacts": task.get("artifacts", []),
        }
        trace("buyer_result", **outcome)
        return outcome


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", required=True, type=int)
    args = parser.parse_args()
    shop(args.url, args.item, args.budget)


if __name__ == "__main__":
    main()
