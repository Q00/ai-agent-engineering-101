"""A harness-driven buyer that shops at an A2A seller (JSON-RPC binding, A2A v1.0).

    python buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120

The code only speaks A2A: reads the Agent Card, sends every request with the
A2A-Version: 1.0 header, and continues the negotiation by taskId. The harness
(see harness.py, prompts/buyer.md) decides every offer and when to walk away.
The code enforces the budget on top: it never offers or accepts above it.
"""
import argparse
import math
import re
import sys
import time
import uuid
from pathlib import Path

import httpx

import harness

HERE = Path(__file__).resolve().parent
PROMPT = (HERE / "prompts" / "buyer.md").read_text(encoding="utf-8")
HEADERS = {"A2A-Version": "1.0"}
MAX_MESSAGES = 8      # offers in one negotiation
DEADLINE = 240        # seconds; the trade test kills the buyer at 300


def log(line):
    print(f"[buyer] {line}", flush=True)


def rpc(url, method, params):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    r = httpx.post(url, json=body, headers=HEADERS, timeout=200).json()
    if "error" in r:
        log(f"<- error {r['error']}")
        return None
    t = r["result"].get("task", r["result"])
    return t if "status" in t else None


def send(url, text, task_id=None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    log(f"-> {text!r}" + (f" (task {task_id[:8]})" if task_id else ""))
    t = rpc(url, "SendMessage", {"message": msg})
    if t:
        log(f"<- {state(t)} | {status_text(t)[:160]}")
        for a in t.get("artifacts", []):
            log(f"   artifact {a.get('name')}: {[p.get('data', p.get('text')) for p in a.get('parts', [])]}")
    return t


def state(t):
    return (t or {}).get("status", {}).get("state", "")


def status_text(t):
    return " ".join(p.get("text", "") for p in (t or {}).get("status", {}).get("message", {}).get("parts", []))


def last_number(text):
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    return math.ceil(float(nums[-1])) if nums else None


def decide(item, budget, history, ask):
    """Ask the harness. Returns (action, price) after the budget guard."""
    lines = "\n".join(f"{who}: {what}" for who, what in history)
    prompt = (PROMPT.replace("[[item]]", item).replace("[[budget]]", str(budget))
              .replace("[[history]]", lines).replace("[[ask]]", str(ask) if ask is not None else "unknown"))
    ans = harness.ask(prompt) or {}
    action, price = ans.get("action"), ans.get("price")
    try:
        price = int(price)
    except (TypeError, ValueError):
        price = None
    log(f"harness said action={action!r} price={price!r} (ask {ask}, budget {budget})")

    # budget guard: the harness may be wrong, the budget may not
    if action == "accept" and (ask is None or ask > budget):
        log(f"guard: accept above budget -> offer {budget}")
        return "offer", budget
    if action == "offer" and (price is None or price > budget):
        log(f"guard: offer {price} above budget -> {budget}")
        return "offer", budget
    if action in ("accept", "offer", "walk"):
        return action, (ask if action == "accept" else price)
    log(f"guard: unusable answer {ans!r} -> walk")
    return "walk", 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    a = ap.parse_args()
    t0 = time.time()

    card = httpx.get(a.url.rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS, timeout=10).json()
    iface = (card.get("supportedInterfaces") or [{}])[0]
    url = iface.get("url") or a.url.rstrip("/") + "/"
    log(f"card: {card.get('name')!r} binding={iface.get('protocolBinding')} v{iface.get('protocolVersion')} url={url}")

    history = []
    opening = f"I want to buy a {a.item}"
    history.append(("me", opening))
    t = send(url, opening)
    if not t:
        log("no task; giving up")
        return
    tid = t["id"]

    for _ in range(MAX_MESSAGES):
        if state(t) != "TASK_STATE_INPUT_REQUIRED":
            break
        if time.time() - t0 > DEADLINE:
            log("deadline; walking away")
            t = rpc(url, "CancelTask", {"id": tid}) or t
            break
        seller_said = status_text(t)
        history.append(("seller", seller_said))
        ask = last_number(seller_said)
        action, price = decide(a.item, a.budget, history, ask)
        if action == "walk":
            log("walking away")
            t = rpc(url, "CancelTask", {"id": tid}) or t
            break
        history.append(("me", str(price)))
        t = send(url, str(price), tid)
        if not t:
            break
    log(f"done: {state(t)} after {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
