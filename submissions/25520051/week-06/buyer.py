"""A harness-driven buyer: an A2A v1.0 client over JSON-RPC, written with plain httpx.

    python buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120

Reads the seller's Agent Card, opens a task, and keeps negotiating on the same
taskId. The harness decides every offer and when to walk away. The code only
speaks A2A and guards the budget: it never sends an offer above it.
"""
import argparse
import re
import sys
import time
import uuid

import httpx

from harness import ask_json

sys.stdout.reconfigure(encoding="utf-8")   # the trade test redirects stdout to a file (cp949 on Windows)

HEADERS = {"A2A-Version": "1.0"}
MAX_ROUNDS = 10
DEADLINE = 240          # seconds; the trade test kills the buyer at 300

SYSTEM = """You are a careful buyer bargaining with a shop over one item.
You have a hard budget and must never pay more than it. Try to pay as little as possible,
but buying within the budget is better than not buying at all.
If the shop clearly will not come down to your budget, walk away.
Reply with one line of JSON and nothing else:
{"action": "offer" | "accept" | "walk", "price": <integer>, "reason": "<short reason>"}
- offer: you propose "price" to the shop.
- accept: you agree to the shop's current asking price; "price" is that ask.
- walk: you stop negotiating."""


def rpc(url: str, method: str, params: dict) -> dict:
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    r = httpx.post(url, json=body, headers=HEADERS, timeout=200).json()
    if "error" in r:
        print(f"[buyer] <- error {r['error']}", flush=True)
        return {}
    return r["result"].get("task", r["result"])


def send(url: str, text: str, task_id: str | None = None) -> dict:
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    print(f"[buyer] -> {text!r}" + (f" taskId={task_id[:8]}" if task_id else ""), flush=True)
    t = rpc(url, "SendMessage", {"message": msg})
    print(f"[buyer] <- {state(t)} | {status_text(t)}", flush=True)
    return t


def state(t: dict) -> str:
    return t.get("status", {}).get("state", "")


def status_text(t: dict) -> str:
    return " ".join(p.get("text", "") for p in t.get("status", {}).get("message", {}).get("parts", []))


def last_number(text: str) -> int | None:
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    return int(float(nums[-1])) if nums else None


def decide(item: str, budget: int, history: list, ask: int | None) -> dict:
    lines = [f"Item: {item}", f"Your budget (hard maximum): {budget}", "Conversation so far:"]
    lines += [f"  {who}: {text}" for who, text in history]
    lines.append(f"The shop's current asking price: {ask}. Decide your next move.")
    return ask_json(SYSTEM, "\n".join(lines)) or {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    args = ap.parse_args()
    start = time.time()

    card = httpx.get(args.url.rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS, timeout=30).json()
    iface = next((i for i in card.get("supportedInterfaces", []) if i.get("protocolBinding") == "JSONRPC"), None)
    if iface is None:
        print("[buyer] seller has no JSON-RPC interface; giving up", flush=True)
        return
    url = iface["url"]
    print(f"[buyer] card: {card.get('name')!r} at {url}", flush=True)

    t = send(url, f"I want to buy a {args.item}")
    task_id = t.get("id")
    history = [("you", f"I want to buy a {args.item}"), ("shop", status_text(t))]

    for _ in range(MAX_ROUNDS):
        if state(t) != "TASK_STATE_INPUT_REQUIRED":
            break
        if time.time() - start > DEADLINE:
            print("[buyer] out of time; walking away", flush=True)
            rpc(url, "CancelTask", {"id": task_id})
            return
        ask = last_number(status_text(t))
        d = decide(args.item, args.budget, history, ask)
        action = d.get("action")
        print(f"[buyer] harness -> {d}", flush=True)
        if action == "walk":
            rpc(url, "CancelTask", {"id": task_id})
            print("[buyer] walked away (CancelTask)", flush=True)
            return
        try:
            price = int(round(float(d.get("price"))))
        except (TypeError, ValueError):
            price = None
        if action == "accept" and ask is not None:
            price = ask
        if price is None:
            print("[buyer] harness gave no price; walking away", flush=True)
            rpc(url, "CancelTask", {"id": task_id})
            return
        if price > args.budget:                                       # guard: never above the budget
            print(f"[buyer] guard: {price} > budget {args.budget}, offering {args.budget}", flush=True)
            price = args.budget
        if ask is not None and price > ask:                           # no reason to pay more than asked
            price = ask
        t = send(url, str(price), task_id)
        history += [("you", str(price)), ("shop", status_text(t))]

    if state(t) == "TASK_STATE_COMPLETED":
        receipt = [p.get("data") for a in t.get("artifacts", []) for p in a.get("parts", []) if p.get("data")]
        print(f"[buyer] bought: {receipt}", flush=True)
    elif state(t) == "TASK_STATE_INPUT_REQUIRED":
        rpc(url, "CancelTask", {"id": task_id})
        print("[buyer] no deal after max rounds (CancelTask)", flush=True)
    else:
        print(f"[buyer] ended without a deal: {state(t)}", flush=True)


if __name__ == "__main__":
    main()
