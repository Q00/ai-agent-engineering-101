"""A harness-driven buyer that shops at an A2A v1.0 seller (JSON-RPC binding).

    python buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120

The A2A side is code: read the Agent Card, send every request with the
A2A-Version: 1.0 header, continue the negotiation with the task's taskId, and
CancelTask when walking away. Every decision (each offer, when to walk away)
comes from the harness:

    claude -p --model sonnet --tools "" --setting-sources "" --no-session-persistence

The harness answers in JSON. The budget is in the prompt and is also enforced in
code: an offer above the budget is lowered to the budget.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import uuid

import httpx

HARNESS = ["claude", "-p", "--model", "sonnet", "--tools", "", "--setting-sources", "", "--no-session-persistence"]
WORKDIR = tempfile.mkdtemp(prefix="buyer-harness-")  # empty dir: no project instructions
MAX_OFFERS = 8  # safety net; the program must exit within 300 s
HEADERS = {"A2A-Version": "1.0"}

PROMPT = """You are buying one {item} from a shop. Your budget is {budget}: never offer more than {budget}.
A lower price is better. If the shop will not come down to your budget, walk away.
If the shop accepts an offer, the deal closes at that price, so only offer what you are willing to pay.

Negotiation so far:
{history}

Decide your next move. Answer with one JSON object and nothing else:
{{"action": "offer" | "walk", "price": <integer>, "reason": "<one short sentence>"}}
- "offer": send "price" to the shop as your offer.
- "walk": stop negotiating without a deal ("price" is ignored).
"""


def log(*a):
    print(*a, flush=True)


def harness(prompt):
    out = subprocess.run(HARNESS, input=prompt, capture_output=True, text=True, cwd=WORKDIR, timeout=120)
    return out.stdout


def decide(item, budget, history):
    prompt = PROMPT.format(item=item, budget=budget, history="\n".join(history))
    for attempt in range(2):
        raw = harness(prompt)
        log(f"[harness] {raw.strip()}")
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            d = json.loads(m.group())
            if d["action"] == "walk":
                return "walk", None
            if d["action"] == "offer" and isinstance(d["price"], (int, float)):
                return "offer", int(d["price"])
        except (AttributeError, KeyError, TypeError, ValueError):
            pass
        log(f"[buyer] unparsable answer, attempt {attempt + 1}")
    return "walk", None  # harness failed twice: walking away never breaks the budget


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
    log(f"-> {text}")
    t = rpc(url, "SendMessage", {"message": msg})
    status = t.get("status", {})
    reply = " ".join(p.get("text", "") for p in status.get("message", {}).get("parts", []))
    log(f"<- {status.get('state')} | {reply}")
    return t, status.get("state"), reply


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    args = ap.parse_args()

    try:  # in the market the other shop may be down: end with no deal instead of a traceback
        r = httpx.get(args.url.rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS, timeout=30)
        card = r.json()
        url = next(i["url"] for i in card["supportedInterfaces"] if i["protocolBinding"] == "JSONRPC")
    except (httpx.HTTPError, ValueError, KeyError, StopIteration) as e:
        log(f"[buyer] no usable agent card at {args.url}: {type(e).__name__}, "
            f"status {getattr(locals().get('r'), 'status_code', None)}; no deal")
        return 0
    log(f"[buyer] {card['name']} at {url}; buying {args.item} with budget {args.budget}")

    t, state, reply = send(url, f"I want to buy a {args.item}")
    task_id = t["id"]
    history = [f"You: I want to buy a {args.item}", f"Shop: {reply}"]

    for _ in range(MAX_OFFERS):
        if state != "TASK_STATE_INPUT_REQUIRED":
            break
        action, price = decide(args.item, args.budget, history)
        if action == "walk":
            log("[buyer] walking away")
            rpc(url, "CancelTask", {"id": task_id})
            state = "walked away"
            break
        if price > args.budget:
            log(f"[guard] offer {price} above budget {args.budget} -> {args.budget}")
            price = args.budget
        t, state, reply = send(url, str(price), task_id)
        history += [f"You offer {price}.", f"Shop: {reply}"]
    else:
        if state == "TASK_STATE_INPUT_REQUIRED":
            log("[buyer] offer limit reached, walking away")
            rpc(url, "CancelTask", {"id": task_id})
            state = "walked away"

    receipt = [p.get("data") for a in t.get("artifacts", []) for p in a.get("parts", []) if p.get("data")]
    log(f"[buyer] done: {state}" + (f", receipt {receipt}" if receipt else ""))


if __name__ == "__main__":
    sys.exit(main())
