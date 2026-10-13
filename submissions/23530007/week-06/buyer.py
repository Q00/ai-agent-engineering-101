"""Week 06 buyer: shops at any A2A v1.0 seller; the harness decides every offer.

    python buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120

It reads the seller's Agent Card, sends every request with the `A2A-Version: 1.0` header, and
continues the negotiation on the task's `taskId`. The seller's asking price is read as the last
number in its INPUT_REQUIRED message.

What the code still enforces (the harness is told the same thing in the prompt):
  - never offer more than the budget: a higher offer is lowered to the budget
  - "accept" means sending the seller's ask back as the offer, and only if the ask is within budget
  - walk away (CancelTask) after MAX_ROUNDS offers or DEADLINE seconds, whichever comes first
"""
import argparse
import json
import re
import sys
import time
import uuid

import httpx

from harness import HARNESS_CMD, as_int, decide, log

MAX_ROUNDS = 8
DEADLINE = 240          # the trade test kills the buyer at 300 s

PROMPT = ("You are the buyer in a price negotiation for a {item}. You may pay at most {budget} (your "
          "budget). Never offer or accept more than {budget}. Pay as little as you can, but buying "
          "within your budget is better than not buying. If the seller cannot come down to your "
          "budget, walk away.\n\n"
          "Negotiation so far (oldest first):\n{history}\n\n"
          "The seller's current asking price is {ask}.\n"
          "Choose one action:\n"
          '- {{"action": "accept"}} to pay {ask}\n'
          '- {{"action": "offer", "price": <whole number>}} to make an offer\n'
          '- {{"action": "walk"}} to leave without buying\n'
          "Reply with only one JSON object.")


class Seller:
    """A tiny JSON-RPC client for the A2A v1.0 binding."""

    def __init__(self, base: str):
        self.http = httpx.Client(headers={"A2A-Version": "1.0"}, timeout=200)
        card = self.http.get(base.rstrip("/") + "/.well-known/agent-card.json").json()
        iface = next(i for i in card["supportedInterfaces"]
                     if i.get("protocolBinding") == "JSONRPC" and str(i.get("protocolVersion")) == "1.0")
        self.url = iface["url"]
        log("buyer", f"card: {card['name']!r}, JSON-RPC {self.url}, skills {[s['id'] for s in card['skills']]}")

    def call(self, method: str, params: dict) -> dict:
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
        r = self.http.post(self.url, json=body).json()
        if "error" in r:
            raise RuntimeError(f"{method}: {r['error']}")
        return r["result"].get("task", r["result"])

    def send(self, text: str, task_id: str | None = None) -> dict:
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        log("buyer", f"-> SendMessage {text!r}" + (f" taskId={task_id[:8]}" if task_id else ""))
        t = self.call("SendMessage", {"message": msg})
        log("buyer", f"<- {state(t)} | {said(t)}")
        return t


def state(t: dict) -> str:
    return t.get("status", {}).get("state", "")


def said(t: dict) -> str:
    return " ".join(p.get("text", "") for p in t.get("status", {}).get("message", {}).get("parts", []))


def last_number(t: dict):
    nums = re.findall(r"\d+", said(t))
    return int(nums[-1]) if nums else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    a = ap.parse_args()
    start = time.time()
    log("buyer", f"harness: {HARNESS_CMD}; item {a.item}, budget {a.budget}")

    seller = Seller(a.url)
    t = seller.send(f"I want to buy a {a.item}")
    tid = t["id"]
    history = []

    for _ in range(MAX_ROUNDS):
        if state(t) != "TASK_STATE_INPUT_REQUIRED":
            break
        if time.time() - start > DEADLINE:
            log("buyer", "deadline reached; walking away")
            break
        ask = last_number(t)
        if ask is None:
            log("buyer", "no asking price in the message; walking away")
            break
        history.append(("seller asked", ask))
        answer = decide("buyer", PROMPT.format(
            item=a.item, budget=a.budget, ask=ask,
            history="\n".join(f"- {w}: {p}" for w, p in history))) or {}
        action, price = answer.get("action"), as_int(answer.get("price"))

        if action == "walk":
            log("buyer", "harness walks away")
            break
        if action == "accept":
            if ask > a.budget:
                log("buyer", f"harness accepted {ask} above budget {a.budget}; code offers the budget instead")
                price = a.budget
            else:
                price = ask
        elif price is None:
            log("buyer", f"unreadable answer {answer!r}; walking away")
            break
        if price > a.budget:
            log("buyer", f"code lowered offer {price} to budget {a.budget}")
            price = a.budget
        history.append(("you offered", price))
        t = seller.send(str(price), tid)

    if state(t) == "TASK_STATE_COMPLETED":
        receipt = [p.get("data") for art in t.get("artifacts", []) for p in art.get("parts", []) if p.get("data")]
        log("buyer", f"bought: {json.dumps(receipt)}")
    elif state(t) == "TASK_STATE_INPUT_REQUIRED":
        t = seller.call("CancelTask", {"id": tid})
        log("buyer", f"canceled the task -> {state(t)}; no purchase")
    else:
        log("buyer", f"ended in {state(t)}; no purchase")
    log("buyer", f"done in {time.time() - start:.0f} s, harness calls {__import__('harness').CALLS['n']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:   # a crash still exits, with the reason in the log
        log("buyer", f"error: {e!r}")
        sys.exit(1)
