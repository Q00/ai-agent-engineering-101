"""The buyer: an A2A v1.0 client whose every decision comes from buyer_brain.

    py buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120

This file only speaks A2A: it reads the Agent Card, sends every request with
A2A-Version: 1.0, keeps the negotiation on one taskId, and asks
buyer_brain.decide() what to send next. It does NOT check the budget; that is
the brain's job (wallet.check_offer is its tool).

A buyer cannot emit A2A artifacts (only the agent working the task can), so the
buyer keeps its own trade record in trades/buyer.jsonl, with the seller's
receipt and trade-record artifacts attached when the seller sent them.
"""
import argparse
import json
import re
import sys
import time
import uuid

import httpx

import buyer_brain
import trade_record
from model import log
from wallet import Wallet

HEADERS = {"A2A-Version": "1.0"}
DEADLINE = 280   # the trade test kills us at 300 s
MAX_TURNS = 12


def rpc(url, method, params):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    r = httpx.post(url, json=body, headers=HEADERS, timeout=200).json()
    if "error" in r:
        raise RuntimeError(f"{method} error {r['error'].get('code')}: {r['error'].get('message')}")
    return r["result"].get("task", r["result"])


def send(url, text, task_id=None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    log(f"-> {text!r}" + (f" taskId={task_id[:8]}" if task_id else ""))
    t = rpc(url, "SendMessage", {"message": msg})
    log(f"<- {state(t)} | {status_text(t)}")
    return t


def state(t):
    return t.get("status", {}).get("state", "")


def status_text(t):
    return " ".join(p.get("text", "") for p in t.get("status", {}).get("message", {}).get("parts", []))


def last_number(text):
    nums = re.findall(r"\d+(?:\.\d+)?", text or "")
    if not nums:
        return None
    n = float(nums[-1])
    return int(n) if n.is_integer() else n


def receipt(t):
    for a in t.get("artifacts", []):
        for p in a.get("parts", []):
            if isinstance(p.get("data"), dict) and "price" in p["data"]:
                return p["data"]
    return None


def artifact_data(t, name):
    for a in t.get("artifacts", []):
        if a.get("name") == name:
            for p in a.get("parts", []):
                if isinstance(p.get("data"), dict):
                    return p["data"]
    return None


OUTCOMES = {"TASK_STATE_COMPLETED": "completed", "TASK_STATE_REJECTED": "rejected",
            "TASK_STATE_CANCELED": "canceled", "TASK_STATE_FAILED": "failed"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=float, required=True)
    args = ap.parse_args()
    if args.budget.is_integer():
        args.budget = int(args.budget)
    t0 = time.time()

    card = httpx.get(args.url.rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS, timeout=30).json()
    iface = card["supportedInterfaces"][0]
    rpc_url = iface["url"]
    log(f"card: {card['name']!r} {iface['protocolBinding']} {iface['protocolVersion']} {rpc_url}")

    wallet = Wallet(args.budget)
    deal = {"item": args.item, "budget": args.budget, "seller": card["name"],
            "skill": (card.get("skills") or [{}])[0].get("description", ""), "ask": None, "history": []}
    opening = f"I want to buy a {args.item}"
    deal["history"].append({"from": "buyer", "text": opening, "price": None})
    t = send(rpc_url, opening)
    task_id = t["id"]

    for _ in range(MAX_TURNS):
        if state(t) != "TASK_STATE_INPUT_REQUIRED":
            break
        words = status_text(t)
        deal["ask"] = last_number(words)
        deal["history"].append({"from": "seller", "text": words, "price": deal["ask"]})
        if time.time() - t0 > DEADLINE:
            log("out of time, walking away")
            d = {"action": "walk_away"}
        else:
            d = buyer_brain.decide(deal, wallet)
        log(f"decision: {d}")
        if d.get("action") == "offer":
            price = d["price"]
            wallet.record_offer(price)
            deal["history"].append({"from": "buyer", "text": str(price), "price": price})
            t = send(rpc_url, str(price), task_id)
        else:
            deal["history"].append({"from": "buyer", "text": "(walked away)", "price": None})
            t = rpc(rpc_url, "CancelTask", {"id": task_id})
            log(f"walked away (CancelTask) -> {state(t)}")
            break

    if state(t) != "TASK_STATE_INPUT_REQUIRED" and status_text(t):
        deal["history"].append({"from": "seller", "text": status_text(t), "price": last_number(status_text(t))})
    r = receipt(t)
    if r is not None:
        wallet.record_purchase(args.item, r["price"], card["name"])
    rec = trade_record.build("buyer", deal, OUTCOMES.get(state(t), "open"),
                             r["price"] if r else None, counterpart=card["name"])
    rec["wallet"] = wallet.snapshot()
    rec["seller_receipt"] = r
    rec["seller_record"] = artifact_data(t, "trade-record")
    trade_record.save(rec)
    log("\n" + trade_record.to_text(rec))
    print(json.dumps({"item": args.item, "budget": args.budget, "final_state": state(t),
                      "receipt": r, "seconds": round(time.time() - t0, 1)}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"buyer crashed: {e!r}")
        sys.exit(1)
