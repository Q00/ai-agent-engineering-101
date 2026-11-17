"""Week 06 — the buyer: my harness in front of an A2A v1.0 client.

    python buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120

Reads the seller's Agent Card, opens a task, and continues it by `taskId`.
Every offer and the decision to walk away come from `claude -p`; the A2A code
only carries them.

Budget in code, not only in the prompt. The prompt states it, and the harness
is asked to respect it, but an offer above the budget is clamped and an accept
above the budget is refused before it reaches the wire. A budget that only
lives in a prompt is a budget the model may spend.
"""
import argparse
import json
import re
import sys
import uuid

import httpx

import harness

MAX_ROUNDS = 8
HEADERS = {"A2A-Version": "1.0"}        # every request, as the test checks


def log(msg):
    print(msg, file=sys.stderr, flush=True)


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.rpc_url = self.base + "/"
        self.card = None

    def read_card(self):
        r = httpx.get(self.base + "/.well-known/agent-card.json",
                      headers=HEADERS, timeout=30)
        r.raise_for_status()
        self.card = r.json()
        iface = (self.card.get("supportedInterfaces") or [{}])[0]
        if iface.get("url"):
            self.rpc_url = iface["url"]
        log(f"[buyer] card {self.card.get('name')!r} -> {self.rpc_url}")
        return self.card

    def rpc(self, method: str, params: dict):
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8],
                "method": method, "params": params}
        r = httpx.post(self.rpc_url, json=body, headers=HEADERS, timeout=200).json()
        if "error" in r:
            log(f"[buyer] <- error {r['error'].get('code')} "
                f"{str(r['error'].get('message'))[:120]}")
            return None, r["error"]
        t = r["result"].get("task", r["result"])
        return t, None

    def send(self, text: str, task_id: str | None = None):
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()),
               "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        log(f"[buyer] -> {text!r}" + (f" taskId={task_id[:8]}" if task_id else ""))
        return self.rpc("SendMessage", {"message": msg})

    def cancel(self, task_id: str):
        log(f"[buyer] -> CancelTask {task_id[:8]}")
        return self.rpc("CancelTask", {"name": f"tasks/{task_id}"})


def state(t):
    return (t or {}).get("status", {}).get("state", "")


def message_text(t):
    return " ".join(p.get("text", "")
                    for p in (t or {}).get("status", {}).get("message", {}).get("parts", []))


def receipt_price(t):
    for a in (t or {}).get("artifacts", []):
        for p in a.get("parts", []):
            d = p.get("data")
            if isinstance(d, dict) and isinstance(d.get("price"), (int, float)):
                return d["price"]
    return None


def seller_ask(t):
    """The seller's current ask. Prefer metadata {"ask": N} (challenge 3's rule),
    fall back to the last number in the text (the trade test's rule)."""
    meta = (t or {}).get("status", {}).get("message", {}).get("metadata") or {}
    n = harness.as_int(meta.get("ask"))
    if n is not None:
        return n
    nums = re.findall(r"\d+(?:\.\d+)?", message_text(t))
    return int(float(nums[-1])) if nums else None


def decide(item: str, budget: int, ask, history: list) -> tuple:
    """Harness decides (action, price). Code clamps it to the budget after."""
    convo = "\n".join(history[-8:]) or "(nothing yet)"
    d = harness.ask(
        "You are buying one item and haggling over the price.\n"
        f"Item: {item}\n"
        f"Your budget (the most you may ever pay) is {budget}. Never go above it.\n"
        f"The seller's current asking price is {ask}.\n"
        f"Recent exchange:\n{convo}\n\n"
        "Accept if the asking price is at or below your budget. Otherwise counter with "
        "a number at or below your budget, moving up slowly. Walk away if the seller "
        "clearly cannot come down to your budget.\n"
        'Reply with one JSON object and nothing else: '
        '{"action": "accept" | "counter" | "walk_away", "price": <whole number>}.',
        want=("action",), tag=" buy")
    action = str(d.get("action", "")).lower().strip()
    price = harness.as_int(d.get("price"))

    if action not in ("accept", "counter", "walk_away"):
        action = "accept" if (ask is not None and ask <= budget) else "counter"
        log(f"[buyer] no usable action from the harness; falling back to {action}")

    if action == "accept":
        if ask is None or ask > budget:
            # The model agreed to pay above the budget. Code does not.
            log(f"[buyer] REFUSED harness accept at {ask} > budget {budget}; countering instead")
            action, price = "counter", None
        else:
            return "accept", ask
    if action == "walk_away":
        return "walk_away", None

    if price is None:
        price = budget if ask is None else min(budget, max(1, (ask + budget) // 2))
    clamped = min(price, budget)
    if clamped != price:
        log(f"[buyer] clamped offer {price} -> {clamped} (budget {budget})")
    return "counter", clamped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    args = ap.parse_args()

    c = Client(args.url)
    try:
        c.read_card()
    except Exception as e:                            # noqa: BLE001
        log(f"[buyer] could not read the Agent Card: {type(e).__name__}: {e}")
        return 1

    t, err = c.send(f"I want to buy a {args.item}")
    if t is None:
        log("[buyer] the seller refused the opening message")
        return 1
    task_id = t.get("id")
    history = [f"seller: {message_text(t)}"]
    log(f"[buyer] {state(t)} | {message_text(t)[:160]}")

    for _ in range(MAX_ROUNDS):
        st = state(t)
        if st == "TASK_STATE_COMPLETED":
            price = receipt_price(t)
            log(f"[buyer] BOUGHT {args.item} at {price} (budget {args.budget})")
            print(json.dumps({"bought": True, "item": args.item, "price": price}))
            return 0
        if st in ("TASK_STATE_REJECTED", "TASK_STATE_CANCELLED", "TASK_STATE_FAILED"):
            log(f"[buyer] seller ended the negotiation: {st}")
            print(json.dumps({"bought": False, "item": args.item, "reason": st}))
            return 0
        if st != "TASK_STATE_INPUT_REQUIRED":
            log(f"[buyer] unexpected state {st}; stopping")
            break

        ask = seller_ask(t)
        action, price = decide(args.item, args.budget, ask, history)
        if action == "walk_away":
            log(f"[buyer] walking away (ask {ask} > budget {args.budget})")
            if task_id:
                c.cancel(task_id)
            print(json.dumps({"bought": False, "item": args.item, "reason": "walked_away"}))
            return 0
        assert price <= args.budget, "code guard: never offer above the budget"
        history.append(f"me: {price}")
        t, err = c.send(str(price), task_id)
        if t is None:
            log("[buyer] the seller returned an error; stopping")
            print(json.dumps({"bought": False, "item": args.item, "reason": "error"}))
            return 0
        history.append(f"seller: {message_text(t)}")
        log(f"[buyer] {state(t)} | {message_text(t)[:160]}")

    # out of rounds without a deal: leave the task closed behind us
    if task_id and state(t) == "TASK_STATE_INPUT_REQUIRED":
        c.cancel(task_id)
    log("[buyer] out of rounds without a deal")
    print(json.dumps({"bought": False, "item": args.item, "reason": "out_of_rounds"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
