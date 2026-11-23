"""A harness-driven buyer that shops at an A2A seller (JSON-RPC binding, A2A v1.0).

    python buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120

The harness (harness.py) decides every offer and when to walk away.
The code speaks A2A and guards the budget: an offer above it goes out as the budget, whatever the harness says.
"""
import argparse
import json
import re
import uuid

import httpx

from harness import ask as ask_harness

HEADERS = {"A2A-Version": "1.0"}
MAX_TURNS = 10  # keeps one run well inside the 300 s limit

# What the harness sees on every turn. The wording is the buyer's whole strategy.
PROMPT = """You are the buyer in a price negotiation. You want to buy one {item}.
Your budget is {budget}: the most you may pay.

How you negotiate (find the seller's floor, then buy at it):
1. Your first offer is about half of the seller's asking price.
2. When the seller's counteroffer is within your budget, do not accept it at once: offer a little less, one time.
   When the seller repeats the same counteroffer, that price is the seller's floor: accept it if it is within your budget.
3. If the floor is above your budget, walk. The seller turns down at most 4 offers: after 4, accept the last
   counteroffer if it is within your budget, or walk.

The negotiation so far:
{history}

Answer with one JSON object and nothing else:
{{"action": "offer" or "walk", "price": <integer>}}
To accept the seller's price, offer exactly that price.
"""


def rpc(url, method, params):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    r = httpx.post(url, json=body, headers=HEADERS, timeout=60).json()
    if "error" in r:
        raise RuntimeError(f"{method}: {r['error']}")
    return r["result"].get("task", r["result"])


def send(url, text, task_id=None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    print(f"-> {text}", flush=True)
    task = rpc(url, "SendMessage", {"message": msg})
    said = " ".join(p.get("text", "") for p in task["status"].get("message", {}).get("parts", []))
    print(f"<- {task['status']['state']} | {said}", flush=True)
    return task, said


def decide(item, budget, history):
    """One harness call. Returns (action, price), or None when the answer cannot be read."""
    prompt = PROMPT.format(item=item, budget=budget, history="\n".join(history))
    try:
        raw = ask_harness(prompt)
        d = json.loads(re.search(r"\{.*\}", raw, re.S).group())
        action = str(d["action"])
        price = None if d.get("price") is None else int(d["price"])
    except Exception as e:
        print(f"[harness] unreadable: {e!r}", flush=True)
        return None
    print(f"[harness] {action} {price}", flush=True)
    return action, price


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    args = ap.parse_args()

    card = httpx.get(args.url.rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS, timeout=10).json()
    url = card["supportedInterfaces"][0]["url"]
    print(f"card: {card['name']} at {url}", flush=True)

    task, said = send(url, f"I want to buy a {args.item}")
    history = [f"seller: {said}"]
    for _ in range(MAX_TURNS):
        if task["status"]["state"] != "TASK_STATE_INPUT_REQUIRED":
            break  # completed, rejected, ...: nothing left to say
        answer = decide(args.item, args.budget, history)
        if answer is None or answer[0] != "offer" or answer[1] is None:
            rpc(url, "CancelTask", {"id": task["id"]})
            print("walked away (CancelTask)", flush=True)
            return
        price = answer[1]
        if price > args.budget:
            print(f"[guard] offer {price} > budget {args.budget} -> {args.budget}", flush=True)
            price = args.budget
        history.append(f"buyer: offers {price}")
        task, said = send(url, str(price), task["id"])
        history.append(f"seller: {said}")
    for a in task.get("artifacts", []):
        print(f"artifact {a.get('name')}: {json.dumps([p.get('data', p.get('text')) for p in a.get('parts', [])])}", flush=True)
    print(f"end: {task['status']['state']}", flush=True)


if __name__ == "__main__":
    main()
