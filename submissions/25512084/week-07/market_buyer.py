import argparse
import json
from pathlib import Path
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
- If the seller price is within budget, accept it by offering that price.
- If the seller price is above budget, do NOT walk away immediately.
- Negotiate actively for several rounds by making increasing offers within the budget.
- Treat the budget as your final possible offer.
- Walk away only after repeated negotiation shows the seller will not come down to the budget.

Return ONLY one JSON object:
{{"action":"offer","price":123}}
or
{{"action":"walk"}}
"""

    raw = run_harness(prompt)
    return json.loads(raw)


MEMORY_FILE = ".market/buyer-memory.json"


def load_memory():
    path = Path(MEMORY_FILE)
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save_memory(memory):
    path = Path(MEMORY_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(memory, ensure_ascii=False, indent=2))


def choose_shop(shops):
    memory = load_memory()

    summary = [
        {
            "name": shop["name"],
            "reviews": shop.get("reviews", []),
            "my_past_trades": [
                trade for trade in memory
                if trade.get("shop") == shop["name"]
            ][-5:],
        }
        for shop in shops
    ]

    prompt = f"""
You are choosing a shop for a purchase.

Available shops and their latest reviews:
{json.dumps(summary, ensure_ascii=False)}

Prefer shops with better reviews and avoid shops with repeated bad reviews.
If a shop has no reviews, treat it as unknown rather than automatically bad.

Return ONLY one JSON object:
{{"name":"shop-name","reason":"short reason"}}
"""

    decision = json.loads(run_harness(prompt))
    name = decision.get("name")

    chosen = next(
        (shop for shop in shops if shop["name"] == name),
        shops[0],
    )

    print(f"[choose] {chosen['name']}: {decision.get('reason', 'selected')}")
    return chosen


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

    try:
        response = httpx.post(
            url,
            headers=HEADERS,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return {
            "id": task_id,
            "contextId": context_id,
            "status": {"state": "failed"},
        }

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


def receipt_from_task(task):
    for artifact in task.get("artifacts", []):
        for part in artifact.get("parts", []):
            data = part.get("data")
            if isinstance(data, dict):
                return data
    return None


def emit_result(chosen, item, budget, task=None, completed=False):
    receipt = receipt_from_task(task or {}) if completed else None

    if receipt is not None:
        price = receipt.get("price")
        stars = 5
        review = f"Completed deal for {item} at {price} within budget {budget}."
        result = "deal"
    else:
        price = None
        stars = 2
        review = f"No deal for {item} within budget {budget}."
        result = "no deal"

    memory = load_memory()
    memory.append({
        "shop": chosen["name"],
        "item": item,
        "price": price,
        "result": result,
    })
    save_memory(memory)

    print(json.dumps({
        "shop": chosen["url"],
        "receipt": receipt,
        "stars": stars,
        "review": review,
    }, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", required=True, type=int)
    args = ap.parse_args()

    item = args.item
    budget = args.budget

    with open(args.shops) as f:
        shops = json.load(f)

    if not shops:
        raise RuntimeError("no shops available")

    chosen = choose_shop(shops)
    url = chosen["url"].rstrip("/") + "/"

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
            emit_result(
                chosen, item, budget,
                task=task,
                completed=True,
            )
            return

        if "rejected" in state or "canceled" in state or "failed" in state:
            emit_result(chosen, item, budget)
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
            emit_result(chosen, item, budget)
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
            emit_result(chosen, item, budget)
            return


if __name__ == "__main__":
    main()
