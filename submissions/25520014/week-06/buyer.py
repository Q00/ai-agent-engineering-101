"""Week 06 buyer: an A2A v1.0 client (JSON-RPC binding) whose offers come from the harness.

    python buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120

The A2A side is plain JSON-RPC over httpx, so every request visibly carries the
A2A-Version header. Every offer, accept and walk-away is a harness decision (see harness.py).

What the code still does on its own:
  - reads the Agent Card and picks the JSON-RPC 1.0 interface,
  - reads the seller's asking price as the last number in an input-required message
    (the same convention the trade test uses on our seller),
  - accepts by sending the asking price back as the offer,
  - walks away with CancelTask, and also when the round limit or the time limit is hit.
The budget is only in the prompt. The code does not clamp offers or block accepts.
"""
import argparse
import re
import sys
import time
import uuid

import httpx

import harness

HEADERS = {"A2A-Version": "1.0"}
MAX_ROUNDS = 8   # offers the buyer sends in one negotiation
DEADLINE = 240   # seconds; the trade test kills the buyer at 300

DECIDE_PROMPT = """You are a buyer negotiating the price of one item with a seller.

Item: {item}
Your budget (the most you may ever offer or pay): {budget}
The seller does not know your budget. Never reveal it.

The negotiation so far:
{history}

The seller's current asking price is {ask}.

Decide what to do next. You may:
  - accept: buy at {ask}. Only if {ask} is at or below your budget.
  - offer a price, which must be at or below your budget. The seller may end the
    negotiation if your offers stay far below what it can accept.
  - walk away: stop without buying, for example when the seller will not come down to your budget.
Answer with one JSON object and nothing else, one of:
{{"action": "accept"}}
{{"action": "offer", "price": <integer>}}
{{"action": "walk"}}
"""


class A2AError(RuntimeError):
    """The seller answered with a JSON-RPC error or something that is not a task."""


def render_history(lines):
    return "\n".join(f"  {who}: {what}" for who, what in lines)


def rpc(url, method, params):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    data = httpx.post(url, json=body, headers=HEADERS, timeout=200).json()
    if "error" in data:
        raise A2AError(f"{method} -> error {data['error'].get('code')} {data['error'].get('message')}")
    return data["result"]


def send(url, text, task_id=None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    print(f"-> SendMessage {text!r}" + (f" taskId={task_id[:8]}" if task_id else ""), flush=True)
    result = rpc(url, "SendMessage", {"message": msg})
    task = result.get("task", result)
    if "status" not in task:
        raise A2AError(f"SendMessage returned a message, not a task: {str(task)[:200]}")
    return task


def walk_away(url, task_id, why):
    print(f"-> CancelTask taskId={task_id[:8]} ({why})", flush=True)
    try:
        rpc(url, "CancelTask", {"id": task_id})
    except A2AError as e:  # the task may already be finished on the seller's side
        print(f"   {e}", flush=True)


def status_text(task):
    parts = task["status"].get("message", {}).get("parts", [])
    return " ".join(p.get("text", "") for p in parts)


def last_number(text):
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    return round(float(nums[-1])) if nums else None


def receipt(task):
    for a in task.get("artifacts", []):
        for p in a.get("parts", []):
            if isinstance(p.get("data"), dict):
                return p["data"]
    return None


def find_endpoint(base):
    card = httpx.get(base.rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS, timeout=10).json()
    for iface in card.get("supportedInterfaces", []):
        if iface.get("protocolBinding") == "JSONRPC" and str(iface.get("protocolVersion")) == "1.0":
            return card, iface["url"]
    raise A2AError("the Agent Card has no JSON-RPC interface for version 1.0")


def negotiate(base, item, budget):
    started = time.time()
    card, url = find_endpoint(base)
    print(f"seller: {card.get('name')!r} at {url}", flush=True)

    task = send(url, f"I want to buy a {item}")
    task_id = task["id"]
    history = [("You", f"asked to buy a {item}")]
    for n in range(MAX_ROUNDS + 1):
        state, text = task["status"]["state"], status_text(task)
        print(f"<- {state} | {text}", flush=True)
        if state == "TASK_STATE_COMPLETED":
            print(f"DONE bought: {receipt(task)}", flush=True)
            return
        if state != "TASK_STATE_INPUT_REQUIRED":
            print(f"DONE no deal ({state})", flush=True)
            return
        ask = last_number(text)
        if ask is None:
            walk_away(url, task_id, "no asking price in the seller's message")
            return
        history.append(("Seller", f"asked {ask}"))
        if n == MAX_ROUNDS:
            walk_away(url, task_id, f"{MAX_ROUNDS} offers sent")
            return
        if time.time() - started > DEADLINE:
            walk_away(url, task_id, f"{DEADLINE} s passed")
            return

        prompt = DECIDE_PROMPT.format(item=item, budget=budget, history=render_history(history), ask=ask)
        answer = harness.ask_json(prompt, f"round {n + 1}")
        action = answer.get("action")
        if action == "accept":
            offer = ask
            history.append(("You", f"accepted {ask}"))
        elif action == "offer":
            offer = round(float(answer["price"]))
            history.append(("You", f"offered {offer}"))
        else:  # "walk" or anything the prompt did not allow
            walk_away(url, task_id, f"harness chose {action!r}")
            return
        task = send(url, str(offer), task_id)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="the seller's base URL")
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    args = ap.parse_args()
    print(f"buyer: {args.item}, budget {args.budget} (harness: claude -p --model {harness.MODEL})", flush=True)
    try:
        negotiate(args.url, args.item, args.budget)
    except (A2AError, harness.HarnessError, httpx.HTTPError) as e:
        print(f"ERROR {type(e).__name__}: {e}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
