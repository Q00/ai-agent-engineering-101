"""Buyer Agent implementing CLI invocation, A2A-Version: 1.0 header, task continuation, and budget guardrails."""
import argparse
import json
import math
import re
import sys
import uuid
import httpx

def rpc(url, method, params):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    r = httpx.post(url, json=body, headers={"A2A-Version": "1.0"}, timeout=30)
    res = r.json()
    if "error" in res:
        return None, res["error"]
    t = res["result"].get("task", res["result"])
    return t, None

def send(url, text, task_id=None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    return rpc(url, "SendMessage", {"message": msg})

def state(t):
    return (t or {}).get("status", {}).get("state", "")

def last_number(t):
    text = " ".join(p.get("text", "") for p in (t or {}).get("status", {}).get("message", {}).get("parts", []))
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    return math.ceil(float(nums[-1])) if nums else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("item")
    ap.add_argument("budget", type=int)
    args = ap.parse_args()

    base_url = args.url.rstrip("/")
    card_url = f"{base_url}/.well-known/agent-card.json"
    
    try:
        r = httpx.get(card_url, timeout=10)
        card = r.json()
        iface = (card.get("supportedInterfaces") or [{}])[0]
        rpc_url = iface.get("url") or f"{base_url}/"
    except Exception as e:
        sys.exit(1)

    t, err = send(rpc_url, f"I want to buy a {args.item}")
    if t is None or state(t) != "TASK_STATE_INPUT_REQUIRED":
        sys.exit(1)

    tid = t["id"]
    
    max_turns = 10
    for _ in range(max_turns):
        st = state(t)
        if st == "TASK_STATE_COMPLETED":
            sys.exit(0)
        if st in ("TASK_STATE_REJECTED", "TASK_STATE_CANCELED"):
            sys.exit(0)
            
        ask = last_number(t)
        if ask is None:
            offer = args.budget
        elif ask <= args.budget:
            offer = ask
        else:
            offer = args.budget
            
        t, err = send(rpc_url, str(offer), tid)
        if t is None:
            break
            
    if state(t) == "TASK_STATE_COMPLETED":
        sys.exit(0)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()
