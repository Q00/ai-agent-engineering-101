"""Harness-driven buyer that shops over A2A v1.0 (JSON-RPC binding, plain httpx).

    python buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120

Flow:
  1. GET {url}/.well-known/agent-card.json, take supportedInterfaces[0] (JSONRPC, 1.0).
  2. SendMessage "I want to buy a <item>" -> a task in TASK_STATE_INPUT_REQUIRED.
  3. Loop: read the seller's ask (last number in its message), ask the harness
     for the next move, send it on the same taskId.
  4. Stop on COMPLETED (receipt), REJECTED/CANCELED, or walk away with CancelTask.

Every request carries the A2A-Version: 1.0 header (httpx client default headers).

Where the budget lives: in the prompt AND in code. guard_offer() clamps any
offer to the budget; a harness "accept" of an ask above budget becomes an
offer at the budget, never a payment above it.
"""
import argparse
import math
import re
import sys
import time
import uuid

import httpx

from harness import ask_json, log
import harness

MAX_ROUNDS = 8          # offers we send at most in one negotiation
DEADLINE = 270          # seconds; the trade test kills the buyer at 300

PROMPT = """You are a buyer negotiating to buy a {item} from a shop. Your budget is {budget}: you must never pay more than {budget}.
Your goal: buy the {item} as cheaply as possible, but do buy it if the shop can come down to {budget} or less.
If the shop clearly will not go down to {budget}, walk away.
History so far:
{history}
The shop's current asking price is {ask}. This is your offer number {round} (shops may end the talk after a few low offers).
Decide one of:
- "accept": pay the shop's current asking price (only if it is at most {budget}).
- "offer": propose your own price (an integer at most {budget}).
- "walk": stop negotiating without buying.
Answer with JSON only, no prose: {{"action": "accept"|"offer"|"walk", "price": <integer>}}"""


def last_number(text):
    nums = re.findall(r"\d+(?:\.\d+)?", text or "")
    return math.ceil(float(nums[-1])) if nums else None


def guard_offer(price, budget):
    """Integer offer, never above the budget. None if not a number."""
    try:
        p = math.floor(float(price))
    except (TypeError, ValueError):
        return None
    if p > budget:
        log(f"[guard] offer {p} above budget {budget} -> {budget}")
        p = budget
    return p


class A2A:
    def __init__(self, base):
        self.http = httpx.Client(headers={"A2A-Version": "1.0"}, timeout=200)
        card = self.http.get(base.rstrip("/") + "/.well-known/agent-card.json").json()
        iface = (card.get("supportedInterfaces") or [{}])[0]
        if iface.get("protocolBinding") != "JSONRPC" or str(iface.get("protocolVersion")) != "1.0":
            raise SystemExit(f"seller does not offer JSON-RPC A2A 1.0: {iface}")
        self.url = iface["url"]
        log(f"card: {card.get('name')!r} -> {self.url}")

    def call(self, method, params):
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
        r = self.http.post(self.url, json=body).json()
        if "error" in r:
            log(f"<- error {r['error'].get('code')} {r['error'].get('message')}")
            return None
        return r["result"].get("task", r["result"])

    def send(self, text, task_id=None):
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        log(f"-> SendMessage {text!r}" + (f" taskId={task_id[:8]}" if task_id else ""))
        t = self.call("SendMessage", {"message": msg})
        if t is not None:
            log(f"<- {state(t)} | {text_of(t)}")
        return t


def state(t):
    return (t or {}).get("status", {}).get("state", "")


def text_of(t):
    return " ".join(p.get("text", "") for p in (t or {}).get("status", {}).get("message", {}).get("parts", []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    args = ap.parse_args()
    t0 = time.time()

    a2a = A2A(args.url)
    t = a2a.send(f"I want to buy a {args.item}")
    if t is None or "id" not in t:
        log("no task came back; stopping")
        return 1
    tid, history = t["id"], []

    for rnd in range(1, MAX_ROUNDS + 1):
        if state(t) != "TASK_STATE_INPUT_REQUIRED":
            break
        if time.time() - t0 > DEADLINE:
            log("deadline reached; walking away")
            t = a2a.call("CancelTask", {"id": tid}) or t
            break
        ask = last_number(text_of(t))
        history.append(f"shop asked {ask}")
        data, _ = ask_json(PROMPT.format(item=args.item, budget=args.budget, ask=ask, round=rnd,
                                         history="\n".join(history)), "buyer")
        action = str((data or {}).get("action", "walk")).lower()   # harness failed twice: walk (safe)
        if action == "accept":
            offer = guard_offer(ask, args.budget)
        elif action == "offer":
            offer = guard_offer(data.get("price"), args.budget)
        else:
            offer = None
        if offer is None:
            log(f"harness says {action}; walking away (CancelTask)")
            t = a2a.call("CancelTask", {"id": tid}) or t   # canceled task, so the out-of-rounds cancel below is skipped
            break
        history.append(f"you offered {offer}")
        t = a2a.send(str(offer), tid)
        if t is None:
            break

    s = state(t)
    if s == "TASK_STATE_COMPLETED":
        for a in t.get("artifacts", []):
            log(f"receipt: {[p.get('data') for p in a.get('parts', [])]}")
    elif s == "TASK_STATE_INPUT_REQUIRED":
        log("out of rounds; walking away (CancelTask)")
        a2a.call("CancelTask", {"id": tid})
    log(f"done: {s or 'error'}, harness calls {harness.CALLS}, {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
