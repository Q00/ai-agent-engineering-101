"""Week 06 — buyer.py: an A2A v1.0 client that shops, every offer decided by the harness.

    python buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120

Reads the seller's Agent Card, opens a task ("I want to buy a lamp"), and keeps the same
taskId for every offer. Every request carries the A2A-Version: 1.0 header. The harness
(harness.ask) decides each move; the code around it does what the model may not:

  guard   no offer above the budget, ever; "accept" only when the ask is within the budget;
          never offer more than the seller is already asking (offer the ask itself instead)
  limit   the kit seller walks away after five low offers, and the whole run must end
          within 300 s, so after a few low offers (or ~200 s) an ask within budget is taken
  parse   the seller's asking price is the last number in its INPUT_REQUIRED message
"""
import argparse
import json
import re
import sys
import time
import uuid

import httpx

from harness import ask

HEADERS = {"A2A-Version": "1.0"}
MAX_OFFERS = 6          # offers before the buyer gives up
LOW_OFFERS_BEFORE_TAKING = 3   # low offers made while the ask is affordable, then take the ask
TIME_LIMIT = 200        # seconds; after this no more harness calls (the test stops us at 300)

TURN_PROMPT = """You are a buyer agent shopping for a {item}. Your budget is {budget}: you must never pay more.
Keep the budget private.
Conversation with the seller so far (oldest first):
{history}
The seller's current asking price is {ask_price}.
Decide your move:
- "accept": buy at the seller's current asking price (only if it is within your budget)
- "offer": propose a lower price of your own
- "walk_away": stop negotiating, no deal (use this if the seller will clearly not come down to your budget)
The seller usually comes down step by step, but ends the talk after about five low offers.
Reply with one JSON object only: {{"action": "accept" or "offer" or "walk_away", "price": <whole number>}}"""


def log(msg):
    print(msg, flush=True)   # the trade test saves the buyer's output under logs/


def rpc(url, method, params):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    r = httpx.post(url, json=body, headers=HEADERS, timeout=200).json()
    if "error" in r:
        raise RuntimeError(f"{method} error: {r['error']}")
    return r["result"].get("task", r["result"])


def send(url, text, task_id=None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    log(f"-> {text}" + (f"  (taskId {task_id[:8]})" if task_id else ""))
    task = rpc(url, "SendMessage", {"message": msg})
    log(f"<- {state(task)} | {status_text(task)}")
    return task


def state(task):
    return task.get("status", {}).get("state", "")


def status_text(task):
    return " ".join(p.get("text", "") for p in task.get("status", {}).get("message", {}).get("parts", []))


def asking_price(task):
    nums = re.findall(r"\d+", status_text(task))
    return int(nums[-1]) if nums else None


def receipt_price(task):
    for a in task.get("artifacts", []):
        for p in a.get("parts", []):
            if isinstance(p.get("data"), dict) and "price" in p["data"]:
                return p["data"]["price"]
    return None


def decide(item, budget, history, ask_price):
    try:
        answer = ask(TURN_PROMPT.format(item=item, budget=budget, history="\n".join(history), ask_price=ask_price))
        return answer.get("action"), int(answer.get("price", 0))
    except Exception as e:   # harness failed: take the ask if affordable, otherwise leave
        log(f"   harness error ({e})")
        return ("accept", ask_price) if ask_price <= budget else ("walk_away", 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    a = ap.parse_args()
    started = time.time()

    card = httpx.get(a.url.rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS, timeout=10).json()
    iface = next(i for i in card["supportedInterfaces"] if i.get("protocolBinding") == "JSONRPC")
    url = iface["url"]
    log(f"card: {card['name']} | JSONRPC {url} | buying {a.item} with budget {a.budget}")

    task = send(url, f"I want to buy a {a.item}")
    task_id = task["id"]
    history = [f"buyer: I want to buy a {a.item}", f"seller: {status_text(task)}"]
    low_offers = 0

    for _ in range(MAX_OFFERS):
        if state(task) != "TASK_STATE_INPUT_REQUIRED":
            break
        ask_price = asking_price(task)
        if ask_price is None:
            log("   no price in the seller's message; leaving")
            break

        if time.time() - started > TIME_LIMIT:
            action, price = ("accept", ask_price) if ask_price <= a.budget else ("walk_away", 0)
            log(f"   time limit: {action}")
        else:
            action, price = decide(a.item, a.budget, history, ask_price)
            log(f"   harness: {json.dumps({'action': action, 'price': price})} (ask {ask_price}, budget {a.budget})")

        # --- guard: the budget and the meaning of "accept" live in code, not only in the prompt
        if action == "accept" and ask_price > a.budget:
            log(f"   GUARD accept at {ask_price} > budget {a.budget}: offering {a.budget} instead")
            action, price = "offer", a.budget
        if action == "offer":
            if price > a.budget:
                log(f"   GUARD offer {price} > budget {a.budget}: lowered to {a.budget}")
                price = a.budget
            if price >= ask_price:
                log(f"   GUARD offer {price} >= ask {ask_price}: offering the ask itself")
                action, price = "accept", ask_price
            elif price <= 0:
                log(f"   GUARD offer {price} is not a price: walking away")
                action = "walk_away"
        if action == "offer" and ask_price <= a.budget:
            low_offers += 1
            if low_offers > LOW_OFFERS_BEFORE_TAKING:
                log(f"   GUARD {low_offers - 1} low offers already while ask {ask_price} is affordable: taking it")
                action, price = "accept", ask_price
        if action not in ("accept", "offer", "walk_away"):
            log(f"   GUARD unknown action {action!r}: walking away")
            action = "walk_away"

        if action == "walk_away":
            task = rpc(url, "CancelTask", {"id": task_id})
            log(f"-> CancelTask (walked away) <- {state(task)}")
            break
        if action == "accept":
            price = ask_price
        history.append(f"buyer: {price}")
        task = send(url, str(price), task_id)
        history.append(f"seller: {status_text(task)}")

    if state(task) == "TASK_STATE_INPUT_REQUIRED":   # out of offers: leave cleanly
        task = rpc(url, "CancelTask", {"id": task_id})
        log(f"-> CancelTask (out of offers) <- {state(task)}")
    paid = receipt_price(task)
    log(f"result: {state(task)}" + (f", paid {paid} (budget {a.budget})" if paid is not None else ", no deal")
        + f", {time.time() - started:.0f} s")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"buyer error: {e}")
        sys.exit(1)
