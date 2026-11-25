import json
import sys
import uuid

import httpx

from harness import run_harness


def buyer_decision(item, budget, seller_price, rounds):
    prompt = f"""
You are a buyer negotiating to purchase one item.

Item: {item}
Maximum budget: {budget}
Seller's current price: {seller_price}
Negotiation round: {rounds}

Decide whether to make an offer or walk away.

Rules:
- Never offer more than the budget.
- Try to get a lower price when possible.
- If the seller price is within budget, you may accept it by offering that price.
- If the seller cannot reach the budget, walk away.

Return ONLY one JSON object:
{{"action":"offer","price":123}}
or
{{"action":"walk"}}
"""

    raw = run_harness(prompt)
    return json.loads(raw)


HEADERS = {
    "A2A-Version": "1.0",
    "Content-Type": "application/json",
}


def fetch_card(base_url):
    card_url = base_url.rstrip("/") + "/.well-known/agent-card.json"
    response = httpx.get(
        card_url,
        headers={"A2A-Version": "1.0"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def send_message(url, text, task_id=None, context_id=None):
    message = {
        "role": "ROLE_USER",
        "messageId": str(uuid.uuid4()),
        "parts": [{"text": str(text)}],
    }

    if task_id:
        message["taskId"] = task_id

    if context_id:
        message["contextId"] = context_id

    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "SendMessage",
        "params": {"message": message},
    }

    response = httpx.post(
        url,
        headers=HEADERS,
        json=payload,
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()

    if "error" in data:
        raise RuntimeError(data["error"])

    return data["result"]["task"]


def task_text(task):
    status = task.get("status", {})
    message = status.get("message") or {}

    texts = []
    for part in message.get("parts", []):
        if isinstance(part, dict) and "text" in part:
            texts.append(part["text"])

    return " ".join(texts)


def last_number(text):
    import re

    numbers = re.findall(r"\d+", text or "")
    if not numbers:
        return None

    return int(numbers[-1])


def main():
    if len(sys.argv) != 4:
        raise SystemExit("usage: buyer.py <url> <item> <budget>")

    url = sys.argv[1].rstrip("/") + "/"
    item = sys.argv[2]
    budget = int(sys.argv[3])

    fetch_card(url)

    task = send_message(
        url,
        f"I want to buy a {item}",
    )

    rounds = 0

    while True:
        state = task.get("status", {}).get("state", "")
        state = str(state).lower()

        if "completed" in state:
            print("completed")
            return

        if "rejected" in state or "canceled" in state or "failed" in state:
            print("no deal")
            return

        if "input_required" not in state and "input-required" not in state:
            raise RuntimeError(f"unexpected task state: {state}")

        seller_price = last_number(task_text(task))
        if seller_price is None:
            raise RuntimeError("seller did not provide a price")

        decision = buyer_decision(
            item=item,
            budget=budget,
            seller_price=seller_price,
            rounds=rounds,
        )

        if decision.get("action") == "walk":
            print("walk away")
            return

        offer = min(
            budget,
            int(decision.get("price", budget)),
        )

        task = send_message(
            url,
            offer,
            task_id=task.get("id"),
            context_id=task.get("contextId"),
        )

        rounds += 1

        if rounds >= 6:
            print("walk away")
            return


if __name__ == "__main__":
    main()
