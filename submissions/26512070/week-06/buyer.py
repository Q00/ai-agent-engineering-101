"""The buyer: an A2A v1.0 client whose every decision comes from buyer_brain.

    py buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120

This file only speaks A2A: it reads the Agent Card, sends every request with
A2A-Version: 1.0, keeps the negotiation on one taskId, and asks
buyer_brain.decide() what to send next.

Keeping to the budget is the brain's job (wallet.check_offer is its tool).
guard() is the last line of defense behind it: an offer over the budget, a
broken decision or a brain crash becomes a walk-away, and every such case is
logged as GUARD and kept in the trade record's "guards".

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
DEADLINE = 280         # the trade test kills us at 300 s
MIN_TURN_SECONDS = 45  # don't start a brain turn with less time than this left
MAX_TURNS = 12
WALK = {"action": "walk_away"}


def rpc(url, method, params, timeout=200):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    r = httpx.post(url, json=body, headers=HEADERS, timeout=timeout).json()
    if "error" in r:
        raise RuntimeError(f"{method} error {r['error'].get('code')}: {r['error'].get('message')}")
    return r["result"].get("task", r["result"])


def send(url, text, task_id=None, timeout=200):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    log(f"-> {text!r}" + (f" taskId={task_id[:8]}" if task_id else ""))
    t = rpc(url, "SendMessage", {"message": msg}, timeout)
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


def guard(d, wallet):
    """Last line of defense. Returns (decision to carry out, reason it was replaced or None)."""
    action = d.get("action") if isinstance(d, dict) else None
    if action == "walk_away":
        return d, None
    if action != "offer":
        return WALK, f"unknown decision {d!r}"
    try:
        price = float(d["price"])
    except (KeyError, TypeError, ValueError):
        return WALK, f"offer without a usable price: {d.get('price')!r}"
    price = int(price) if price.is_integer() else price
    ok, reason = wallet.check_offer(price)
    if not ok:
        return WALK, reason
    return {"action": "offer", "price": price}, None


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
    # The server lists its interfaces in order of preference; take the first one we can speak.
    iface = next((i for i in card.get("supportedInterfaces", [])
                  if i.get("protocolBinding") == "JSONRPC" and str(i.get("protocolVersion")) == "1.0"), None)
    if iface is None:
        raise RuntimeError(f"no JSONRPC 1.0 interface in the card: {card.get('supportedInterfaces')}")
    rpc_url = iface["url"]
    log(f"card: {card['name']!r} {iface['protocolBinding']} {iface['protocolVersion']} {rpc_url}")

    wallet = Wallet(args.budget)
    # Skills have no argument schema: the brain reads their words and examples to decide what to send.
    skills = [{"name": s.get("name", ""), "description": s.get("description", ""),
               "examples": s.get("examples", [])} for s in card.get("skills", [])]
    deal = {"item": args.item, "budget": args.budget, "seller": card["name"],
            "seller_description": card.get("description", ""), "skills": skills, "ask": None,
            "seconds_left": DEADLINE, "history": [], "guards": []}
    opening = f"I want to buy a {args.item}"
    deal["history"].append({"from": "buyer", "text": opening, "price": None})
    t = send(rpc_url, opening)
    task_id = t["id"]
    left = lambda: DEADLINE - (time.time() - t0)

    for turn in range(1, MAX_TURNS + 1):
        if state(t) != "TASK_STATE_INPUT_REQUIRED":
            break
        words = status_text(t)
        deal["ask"] = last_number(words)
        deal["history"].append({"from": "seller", "text": words, "price": deal["ask"]})
        deal["seconds_left"] = int(left())
        if left() < MIN_TURN_SECONDS:
            d, why = WALK, f"only {left():.0f} s left"
        else:
            try:
                raw = buyer_brain.decide(deal, wallet)
            except Exception as e:
                raw = {"action": "brain_error", "error": repr(e)}
            d, why = guard(raw, wallet)
            if why is None and left() < 5:
                d, why = WALK, "the brain ran past the deadline"
            if why:
                why = f"{why} (brain said {raw!r})"
        if why:
            log(f"GUARD: {why} -> {d}")
            deal["guards"].append({"turn": turn, "reason": why, "used": d})
        log(f"decision: {d}")
        if d["action"] == "offer":
            price = d["price"]
            wallet.record_offer(price)
            deal["history"].append({"from": "buyer", "text": str(price), "price": price})
            t = send(rpc_url, str(price), task_id, timeout=max(5, left() + 15))
        else:
            break

    if state(t) == "TASK_STATE_INPUT_REQUIRED":  # walked away, or ran out of turns
        deal["history"].append({"from": "buyer", "text": "(walked away)", "price": None})
        t = rpc(rpc_url, "CancelTask", {"id": task_id})
        log(f"walked away (CancelTask) -> {state(t)}")

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
