"""My buyer as an A2A v1.0 client. The code speaks A2A; the harness decides every offer.

    python buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120
"""
import argparse
import json
import sys
import uuid

import httpx

import harness

MAX_ROUNDS = 8
HEADERS = {"A2A-Version": "1.0"}

SYSTEM = (
    "You are the bargaining brain of a shopping agent that buys one item from a shop over a protocol. "
    "You are given a budget. Never offer more than the budget. Start low and come up gradually. "
    "If the shop's ask is at or below the budget, offer exactly that ask to close the deal. "
    "If after a few rounds the shop clearly will not come down to the budget, walk away. "
    'Answer with ONE line of JSON and nothing else: {"action":"offer"|"walk_away","price":<integer>}'
)


def rpc(url, method, params):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    r = httpx.post(url, json=body, headers=HEADERS, timeout=200).json()
    if "error" in r:
        print(f"[buyer] <- error {r['error']}", flush=True)
        return None
    return r["result"].get("task", r["result"])


def send(url, text, task_id=None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    print(f"[buyer] -> {text!r}" + (f" (taskId {task_id[:8]})" if task_id else ""), flush=True)
    return rpc(url, "SendMessage", {"message": msg})


def state(t):
    return (t or {}).get("status", {}).get("state", "")


def status_text(t):
    return " ".join(p.get("text", "") for p in (t or {}).get("status", {}).get("message", {}).get("parts", []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    a = ap.parse_args()

    card = httpx.get(a.url.rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS, timeout=10).json()
    iface = next((i for i in card.get("supportedInterfaces", []) if i.get("protocolBinding") == "JSONRPC"), None)
    if iface is None:
        sys.exit("[buyer] the card has no JSONRPC interface")
    rpc_url = iface["url"]
    print(f"[buyer] card: {card.get('name')!r}, skills {[s.get('id') for s in card.get('skills', [])]}, rpc {rpc_url}", flush=True)

    opening = f"I want to buy a {a.item}"
    t = send(rpc_url, opening)
    print(f"[buyer] <- {state(t)} | {status_text(t)}", flush=True)
    if state(t) != "TASK_STATE_INPUT_REQUIRED":
        sys.exit(0)
    tid = t["id"]
    log = [("you", opening), ("shop", status_text(t))]

    for _ in range(MAX_ROUNDS):
        transcript = "\n".join(f"{who}: {what}" for who, what in log)
        d = harness.decide(SYSTEM, f"Item: {a.item}. Budget: {a.budget}. Transcript so far:\n{transcript}\nDecide your next move.")
        if d is None or d.get("action") == "walk_away" or "price" not in d:
            print("[buyer] walking away", flush=True)
            rpc(rpc_url, "CancelTask", {"id": tid})
            break
        if d["price"] > a.budget:
            print(f"[guard] harness offered {d['price']} above the budget {a.budget}; capped at the budget", flush=True)
            d["price"] = a.budget
        t = send(rpc_url, str(d["price"]), tid)
        print(f"[buyer] <- {state(t)} | {status_text(t)}", flush=True)
        log += [("you", str(d["price"])), ("shop", status_text(t))]
        if state(t) != "TASK_STATE_INPUT_REQUIRED":
            for art in (t or {}).get("artifacts", []):
                print(f"[buyer] artifact {art.get('name')}: {json.dumps([p.get('data', p.get('text')) for p in art.get('parts', [])])}", flush=True)
            break
    else:
        print("[buyer] round limit reached, walking away", flush=True)
        rpc(rpc_url, "CancelTask", {"id": tid})


if __name__ == "__main__":
    main()
