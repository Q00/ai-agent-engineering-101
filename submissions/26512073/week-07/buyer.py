"""A small A2A buyer: the harness chooses offers; code enforces the budget."""
import argparse
import json
import re
import time
import uuid

import httpx

from harness import call_harness


def decide_buy(item, budget, asking_price, offers, seconds_left):
    prompt = (
        "You are a buyer negotiating a purchase. Do not use tools.\n"
        "Your goal is to buy within your private budget. Accept an asking "
        "price within budget by offering that exact price. If it is above "
        "budget, negotiate upward with offers within budget. Reach your "
        "budget by your fourth offer if necessary; do not repeat an offer "
        "that was already refused. If your budget offer was refused and "
        "the ask remains too high, walk away.\n"
        "Return only JSON: action (offer or walk_away), price (integer for "
        "offer, null for walk_away), reason (one short sentence).\n"
        "State:\n" + json.dumps({"item": item, "private_budget": budget,
            "asking_price": asking_price, "previous_offers": offers})
    )
    decision = call_harness(prompt, timeout=min(45, seconds_left))
    action, price = decision.get("action"), decision.get("price")
    if action == "walk_away" and price is None:
        return decision
    if action != "offer" or type(price) is not int or not 0 <= price <= budget:
        raise ValueError(f"Invalid buyer decision or price outside budget: {decision}")
    return decision


def buy(url, item, budget, time_budget=270):
    # Leave time for HTTP cleanup before the assignment's 300-second deadline.
    deadline = time.monotonic() + time_budget
    offers = []
    task_id = None
    with httpx.Client(headers={"A2A-Version": "1.0"}, timeout=30) as client:
        card_response = client.get(url.rstrip("/") + "/.well-known/agent-card.json")
        card_response.raise_for_status()
        card = card_response.json()
        interfaces = [i for i in card.get("supportedInterfaces", [])
                      if i.get("protocolBinding") == "JSONRPC"
                      and i.get("protocolVersion") == "1.0"]
        if not interfaces:
            raise ValueError("Seller has no A2A v1.0 JSON-RPC interface")
        endpoint = interfaces[0]["url"]

        def rpc(method, params):
            response = client.post(endpoint, json={"jsonrpc": "2.0",
                "id": str(uuid.uuid4()), "method": method, "params": params})
            response.raise_for_status()
            reply = response.json()
            print(json.dumps({"method": method, "params": params, "reply": reply}), flush=True)
            if "error" in reply:
                raise RuntimeError(f"Seller RPC error: {reply['error']}")
            return reply["result"].get("task", reply["result"])

        def send(text):
            message = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()),
                       "parts": [{"text": text}]}
            if task_id:
                message["taskId"] = task_id
            return rpc("SendMessage", {"message": message})

        task = send(f"I want to buy a {item}")
        task_id = task["id"]
        for _ in range(9):
            state = task["status"]["state"]
            if state == "TASK_STATE_COMPLETED":
                prices = [p["data"]["price"] for a in task.get("artifacts", [])
                          for p in a.get("parts", [])
                          if isinstance(p.get("data"), dict) and "price" in p["data"]]
                if not prices or any(type(p) not in (int, float) or not 0 <= p <= budget for p in prices):
                    raise ValueError("Missing receipt price or receipt exceeds budget")
                print(json.dumps({"outcome": "deal", "prices": prices}), flush=True)
                receipt = next(p["data"] for a in task.get("artifacts", [])
                               for p in a.get("parts", [])
                               if isinstance(p.get("data"), dict) and "price" in p["data"])
                return {"outcome": "deal", "receipt": receipt, "offers": offers}
            if state in {"TASK_STATE_REJECTED", "TASK_STATE_CANCELED", "TASK_STATE_FAILED"}:
                print(json.dumps({"outcome": "no_deal", "state": state}), flush=True)
                return {"outcome": "no_deal", "receipt": None, "offers": offers}
            if state != "TASK_STATE_INPUT_REQUIRED":
                raise ValueError(f"Unexpected seller state: {state}")
            if len(offers) >= 8:
                break
            remaining = deadline - time.monotonic()
            if remaining < 50:
                break
            text = " ".join(p.get("text", "") for p in
                task["status"].get("message", {}).get("parts", []))
            numbers = re.findall(r"\d+(?:\.\d+)?", text)
            if not numbers:
                raise ValueError("Seller response has no asking price")
            ask = float(numbers[-1])
            decision = decide_buy(item, budget, ask, offers, remaining - 30)
            print(json.dumps({"buyer_decision": decision}), flush=True)
            if decision["action"] == "walk_away":
                break
            offers.append(decision["price"])
            task = send(str(decision["price"]))
        # Stopping is permitted by the assignment; cancel also informs the seller.
        rpc("CancelTask", {"id": task_id})
        print(json.dumps({"outcome": "walked_away"}), flush=True)
        return {"outcome": "walked_away", "receipt": None, "offers": offers}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--item", choices=["lamp", "chair", "bookshelf"], required=True)
    parser.add_argument("--budget", type=int, required=True)
    args = parser.parse_args()
    if args.budget < 0:
        parser.error("Budget must be nonnegative")
    buy(args.url, args.item, args.budget)
