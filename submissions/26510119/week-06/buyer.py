"""A harness-driven buyer, an A2A v1.0 client over JSON-RPC.

    python buyer.py <seller base url> <item> <budget>

The harness picks every offer and when to walk away. The code reads the Agent Card,
sends every request with A2A-Version: 1.0, continues the task by taskId, and never
offers more than the budget. A2A has no "accept" act: accepting means sending the
seller's own ask back as the offer.
"""
import os
import re
import sys
import time
import uuid

import httpx

import harness

HEADERS = {"A2A-Version": "1.0"}
DEADLINE = 240   # seconds; the trade test stops the buyer at 300
MAX_ROUNDS = 10
GUARDS = os.environ.get("GUARDS", "on") != "off"   # off: the budget lives in the prompt only

PROMPT = """You are buying a {item} from a shop.
Your budget is {budget}. Never pay more than {budget} and never reveal it.
Pay as little as you can, but buying within budget is better than not buying.
So far:
{history}
The shop is now asking {ask}.
Choose one:
- accept: pay {ask}
- offer: propose a lower price
- walk: leave without buying
Reply with exactly one JSON object and nothing else:
{{"action": "accept" | "offer" | "walk", "price": <integer, your offer if you offer>}}"""


def log(msg):
    print(msg, flush=True)


def to_int(v):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def rpc(url, method, params):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    reply = httpx.post(url, json=body, headers=HEADERS, timeout=200).json()
    if "error" in reply:
        log(f"  <- error {reply['error'].get('code')} {reply['error'].get('message')}")
        return None
    return reply["result"].get("task", reply["result"])


def state(task):
    return (task or {}).get("status", {}).get("state", "")


def says(task):
    return " ".join(p.get("text", "") for p in (task or {}).get("status", {}).get("message", {}).get("parts", []))


def send(url, text, task_id=None):
    message = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        message["taskId"] = task_id
    log(f"  -> {text!r}" + (f" (taskId {task_id[:8]})" if task_id else ""))
    task = rpc(url, "SendMessage", {"message": message})
    log(f"  <- {state(task) or 'no task'} | {says(task)}")
    return task


def main():
    base, item, budget = sys.argv[1].rstrip("/"), sys.argv[2], int(sys.argv[3])
    started = time.time()
    log(f"buyer: {item}, budget {budget}, seller {base}, harness: {harness.CMD}, "
        f"budget guard {'on' if GUARDS else 'OFF'}")

    card = httpx.get(base + "/.well-known/agent-card.json", headers=HEADERS, timeout=30).json()
    url = next(i["url"] for i in card["supportedInterfaces"] if i.get("protocolBinding") == "JSONRPC")
    log(f"card: {card['name']!r}, JSONRPC at {url}, skills {[s['id'] for s in card.get('skills', [])]}")

    task = send(url, f"I want to buy a {item}")
    if state(task) != "TASK_STATE_INPUT_REQUIRED":
        log(f"result: the shop did not open a negotiation ({state(task) or 'error'})")
        return
    task_id = task["id"]
    history = [f"you: I want to buy a {item}", f"shop: {says(task)}"]

    for _ in range(MAX_ROUNDS):
        if state(task) != "TASK_STATE_INPUT_REQUIRED":
            break
        if time.time() - started > DEADLINE:
            log("deadline reached; walking away")
            rpc(url, "CancelTask", {"id": task_id})
            break
        numbers = re.findall(r"\d+", says(task))
        ask = int(numbers[-1]) if numbers else None
        answer = harness.ask(PROMPT.format(item=item, budget=budget, ask=ask, history="\n".join(history)), log) or {}
        action, price = answer.get("action"), to_int(answer.get("price"))

        if action == "accept":
            price = ask
        if action in ("accept", "offer"):
            if price is None:
                log(f"[guard] no price in {answer}; walking away")
                action = "walk"
            elif GUARDS and price > budget:
                log(f"[guard] {action} at {price} is above the budget {budget}; offering {budget}")
                price = budget
        if action not in ("accept", "offer"):
            log(f"walking away ({answer})")
            rpc(url, "CancelTask", {"id": task_id})
            break

        history.append(f"you: {price}")
        task = send(url, str(price), task_id)
        history.append(f"shop: {says(task)}")

    receipt = next((p["data"] for a in (task or {}).get("artifacts", []) for p in a.get("parts", [])
                    if isinstance(p.get("data"), dict)), None)
    log(f"result: {state(task) or 'error'}"
        + (f", bought at {receipt.get('price')}" if receipt else ", nothing bought")
        + f", {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
