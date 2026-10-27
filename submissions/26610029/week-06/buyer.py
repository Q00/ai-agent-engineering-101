"""Week 06 -- a buyer driven by the harness, over A2A v1.0.

Speaks raw JSON-RPC over httpx (no a2a CLI dependency, since the grader's
machine is not guaranteed to have it). Every offer and the walk-away decision
come from calling the harness (harness.py) fresh each turn, with the
negotiation history written into the prompt. The only rule left in code is a
guard that never offers more than the budget, and never walks away from a
price that is already inside the budget.

    python buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120
"""
import argparse
import math
import re
import uuid

import httpx

from harness import call_harness

MAX_ROUNDS = 6


def fetch_card(base_url: str) -> dict:
    r = httpx.get(base_url.rstrip("/") + "/.well-known/agent-card.json", timeout=15)
    r.raise_for_status()
    return r.json()


def rpc(url: str, method: str, params: dict):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    r = httpx.post(url, json=body, headers={"A2A-Version": "1.0"}, timeout=120).json()
    if "error" in r:
        return None, r["error"]
    result = r["result"]
    return result.get("task", result), None


def send(url: str, text: str, task_id: str | None = None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    return rpc(url, "SendMessage", {"message": msg})


def state(t: dict | None) -> str:
    return (t or {}).get("status", {}).get("state", "")


def status_text(t: dict | None) -> str:
    return " ".join(p.get("text", "") for p in (t or {}).get("status", {}).get("message", {}).get("parts", []))


def last_number(t: dict | None):
    nums = re.findall(r"\d+(?:\.\d+)?", status_text(t))
    return math.ceil(float(nums[-1])) if nums else None


def receipt_price(t: dict | None):
    for a in (t or {}).get("artifacts", []):
        for p in a.get("parts", []):
            d = p.get("data")
            if isinstance(d, dict) and isinstance(d.get("price"), (int, float)):
                return d["price"]
    return None


def ask_harness_offer(item: str, budget: int, history: list[str], ask):
    hist = "\n".join(f"  {line}" for line in history)
    prompt = (
        f"You are a buyer shopping for a {item}. Your budget (maximum you can pay) is {budget}. "
        "Never reveal this number to the seller.\n\n"
        f"Negotiation history so far:\n{hist}\n\n"
        "Decide your next move.\n\n"
        "Rules:\n"
        "- If the asking price is at or below your budget, OFFER exactly that asking price to "
        "close the deal now.\n"
        "- Otherwise OFFER a lower price, leaving room to negotiate up later, but never exceed "
        "your budget.\n"
        "- If you believe the seller will never come down to your budget after several rounds, "
        "you may WALK.\n\n"
        "Respond with EXACTLY one line in this format and nothing else:\nOFFER <price>\nWALK"
    )
    reply = call_harness(prompt)
    m = re.search(r"OFFER\s+(\d+)", reply, re.IGNORECASE)
    if m:
        return "OFFER", int(m.group(1)), reply
    if re.search(r"\bWALK\b", reply, re.IGNORECASE):
        return "WALK", None, reply
    return "PARSE_FAILED", None, reply


def negotiate_one(base_url: str, item: str, budget: int) -> dict:
    """Negotiate for ONE item with ONE shop, start to finish. Reused as-is by
    shopping_buyer.py (challenge 2) to talk to several shops; the single-shop
    CLI below is a thin wrapper around this same function.

    Returns {"shop_url", "deal": bool, "price": float|None, "task_id": str|None}.
    """
    card = fetch_card(base_url)
    iface = (card.get("supportedInterfaces") or [{}])[0]
    rpc_url = iface.get("url") or base_url

    t, err = send(rpc_url, f"I want to buy a {item}")
    if t is None:
        return {"shop_url": base_url, "deal": False, "price": None, "task_id": None}
    task_id = t.get("id")
    history = [f"[you] I want to buy a {item}"]

    rounds = 0
    while state(t) == "TASK_STATE_INPUT_REQUIRED" and rounds < MAX_ROUNDS:
        history.append(f"[seller] {status_text(t)}")
        ask = last_number(t)

        if ask is not None and ask <= budget:
            # guard: never pass up, and never let the harness walk away from, an affordable price
            offer = ask
        else:
            action, price, raw = ask_harness_offer(item, budget, history, ask)
            if action == "WALK":
                history.append("[you] (walked away)")
                rpc(rpc_url, "CancelTask", {"id": task_id})
                return {"shop_url": base_url, "deal": False, "price": None, "task_id": task_id}
            if action == "OFFER" and price is not None:
                offer = min(price, budget)          # guard: never exceed the budget
            else:                                    # harness did not follow the format
                offer = min(ask - 1, budget) if ask is not None else budget

        history.append(f"[you] {offer}")
        t, err = send(rpc_url, str(offer), task_id)
        rounds += 1
        if t is None:
            return {"shop_url": base_url, "deal": False, "price": None, "task_id": task_id}

    if state(t) == "TASK_STATE_COMPLETED":
        return {"shop_url": base_url, "deal": True, "price": receipt_price(t), "task_id": task_id}
    if state(t) not in ("TASK_STATE_REJECTED",):
        rpc(rpc_url, "CancelTask", {"id": task_id})      # gave up after MAX_ROUNDS: leave cleanly
    return {"shop_url": base_url, "deal": False, "price": None, "task_id": task_id}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    args = ap.parse_args()
    result = negotiate_one(args.url, args.item, args.budget)
    if result["deal"]:
        print(f"deal: {args.item} at {result['price']} (budget {args.budget})")
    else:
        print(f"no deal (budget {args.budget})")


if __name__ == "__main__":
    main()
