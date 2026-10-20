"""Buyer that speaks A2A v1.0 (JSON-RPC). The harness decides every offer and when to walk away.

    python buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120

Code-side rules (logged as [guard] when they fire):
  * never offer more than the budget                                   (limit guard)
  * at most MAX_OFFERS offers, then CancelTask
"""
import argparse
import json
import re
import sys
import uuid

import httpx

from harness import ask_json

MAX_OFFERS = 8

PROMPT = """You are a buying agent. Your user wants one {item}. The budget is {budget}.
Never offer or agree to more than {budget}. Pay as little as you can, but shops often end the negotiation
after several low offers, so move toward the shop's price in real steps. Walk away when the shop will not
come down to {budget}. To accept the shop's price, offer exactly that price.

Shop: {shop}
Offers you have made so far: {n}

Conversation so far:
{transcript}

Decide your next move. Answer with one JSON object and nothing else:
{{"action": "offer", "price": <whole number>}}
{{"action": "walk"}}"""


def last_number(text):
    nums = re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))
    return int(float(nums[-1])) if nums else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    a = ap.parse_args()

    card = httpx.get(a.url.rstrip("/") + "/.well-known/agent-card.json",
                     headers={"A2A-Version": "1.0"}, timeout=10).json()
    iface = card["supportedInterfaces"][0]
    url = iface["url"]
    print(f"card: {card['name']} -> {url} ({iface.get('protocolBinding')} {iface.get('protocolVersion')})", flush=True)

    def rpc(method, params):
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
        r = httpx.post(url, json=body, headers={"A2A-Version": "1.0"}, timeout=200).json()
        if "error" in r:
            raise RuntimeError(r["error"])
        return r["result"].get("task", r["result"])

    def send(text, task_id=None):
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        t = rpc("SendMessage", {"message": msg})
        said = " ".join(p.get("text", "") for p in t["status"].get("message", {}).get("parts", []))
        print(f"-> {text!r}  <- {t['status']['state']} | {said}", flush=True)
        return t, said

    t, said = send(f"I want to buy a {a.item}")
    lines = [f"you: I want to buy a {a.item}", f"shop: {said}"]
    offers = 0
    while t["status"]["state"] == "TASK_STATE_INPUT_REQUIRED" and offers < MAX_OFFERS:
        move, raw = ask_json(PROMPT.format(item=a.item, budget=a.budget, shop=card.get("description", ""),
                                           n=offers, transcript="\n".join(lines)))
        print(f"[harness] {raw}", flush=True)
        if not move or move.get("action") != "offer" or not isinstance(move.get("price"), (int, float)):
            rpc("CancelTask", {"id": t["id"]})
            print("walked away (CancelTask)", flush=True)
            break
        offer = int(move["price"])
        if offer > a.budget:
            print(f"[guard] offer {offer} is above the budget {a.budget} -> {a.budget}", flush=True)
            offer = a.budget
        t, said = send(str(offer), t["id"])
        offers += 1
        lines += [f"you: {offer}", f"shop: {said}"]
    else:
        if t["status"]["state"] == "TASK_STATE_INPUT_REQUIRED":
            rpc("CancelTask", {"id": t["id"]})
            print("offer limit reached (CancelTask)", flush=True)
    print(json.dumps({"state": t["status"]["state"], "artifacts": t.get("artifacts", [])}), flush=True)


if __name__ == "__main__":
    sys.exit(main())
