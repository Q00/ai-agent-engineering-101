"""Week 06 — the buyer: an A2A v1.0 client (raw JSON-RPC over httpx) whose offers come from the harness.

    python buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120
    python buyer.py --directory directory.json --list "lamp,chair,bookshelf" --budget 600   # challenge 2

The client code only speaks A2A: it reads the Agent Card, sends SendMessage
with the `A2A-Version: 1.0` header, continues the task by taskId, and cancels
when it walks away. Every offer and the decision to accept or walk come from
the harness. Code enforces the budget whatever the harness says.

Challenge 2: one shopping list, many shops, one total budget. The buyer reads
the cards, has the harness assign each item to a shop and split the budget,
then negotiates the items concurrently. Each negotiation is carried as a small
state dict (shop, ask, my last offer, rounds, status) — that summary, not the
transcript, is what the harness sees each round.
"""
import argparse
import concurrent.futures as cf
import json
import math
import re
import sys
import uuid

import httpx

import harness

HEADERS = {"A2A-Version": "1.0", "Content-Type": "application/json"}
MAX_MESSAGES = 8


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- A2A client

def get_card(base: str) -> dict:
    r = httpx.get(base.rstrip("/") + "/.well-known/agent-card.json", timeout=10)
    r.raise_for_status()
    return r.json()


def rpc_url(card: dict, base: str) -> str:
    for i in card.get("supportedInterfaces", []):
        if i.get("protocolBinding") == "JSONRPC" and str(i.get("protocolVersion")) == "1.0":
            return i["url"]
    return base.rstrip("/") + "/"


def rpc(url: str, method: str, params: dict):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    r = httpx.post(url, json=body, headers=HEADERS, timeout=200).json()
    if "error" in r:
        return None, r["error"]
    res = r["result"]
    return res.get("task", res), None


def send(url: str, text: str, task_id=None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    log(f"  -> {text!r}" + (f" (task {task_id[:8]})" if task_id else ""))
    t, err = rpc(url, "SendMessage", {"message": msg})
    if err:
        log(f"  <- error {err.get('code')} {str(err.get('message', ''))[:120]}")
    else:
        log(f"  <- {state(t)} | {status_text(t)[:160]}")
    return t, err


def cancel(url: str, task_id: str):
    log(f"  -> CancelTask {task_id[:8]}")
    rpc(url, "CancelTask", {"id": task_id})


def state(t):
    return (t or {}).get("status", {}).get("state", "")


def status_text(t):
    return " ".join(p.get("text", "") for p in (t or {}).get("status", {}).get("message", {}).get("parts", []))


def last_number(t):
    nums = re.findall(r"\d+(?:\.\d+)?", status_text(t))
    return math.ceil(float(nums[-1])) if nums else None


def receipt_price(t):
    for a in (t or {}).get("artifacts", []):
        for p in a.get("parts", []):
            d = p.get("data")
            if isinstance(d, dict) and isinstance(d.get("price"), (int, float)):
                return d["price"]
    return None


# ---------------------------------------------------------------- the harness decides

def decide(st: dict):
    """st: the negotiation's state dict. Returns (action, price)."""
    hist = "; ".join(f"ask {a} / my offer {o}" for a, o in st["history"]) or "none"
    data = harness.ask_json(
        f"You are buying a {st['item']} from the shop {st['shop']!r}. Your budget is {st['budget']}: "
        f"never pay more than {st['budget']}, and try to pay clearly less. "
        f"History (seller ask / your offer): {hist}. The seller now asks {st['ask']}. "
        f"Round {st['rounds'] + 1} of at most {MAX_MESSAGES - 1}. "
        'Decide: accept the ask, make a lower offer, or walk away if the seller cannot come within your budget. '
        'Reply with exactly one JSON object: {"action": "accept"} or {"action": "offer", "price": <integer>} or {"action": "walk"}.',
        log)
    action = (data or {}).get("action")
    price = _int((data or {}).get("price"))
    if action not in ("accept", "offer", "walk"):
        harness.CALLS["fallbacks"] += 1
        if st["ask"] <= st["budget"]:
            action = "accept"
        elif st["rounds"] >= 4:
            action = "walk"
        else:
            action, price = "offer", min(st["budget"], max((st["ask"] + (st["history"][-1][1] if st["history"] else 0)) // 2, 1))
    # code enforces the budget whatever the harness said
    if action == "accept" and st["ask"] > st["budget"]:
        log(f"    [guard] harness accepted ask {st['ask']} > budget {st['budget']}; offering the budget instead")
        action, price = "offer", st["budget"]
    if action == "offer":
        if price is None:
            price = min(st["budget"], st["ask"])
        if price > st["budget"]:
            log(f"    [guard] offer {price} > budget {st['budget']}; clamped")
            price = st["budget"]
    return action, price


def _int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- one negotiation

def negotiate(base: str, item: str, budget: int, request_text=None, shop=None) -> dict:
    card = get_card(base)
    url = rpc_url(card, base)
    shop = shop or card.get("name")
    st = {"shop": shop, "url": url, "item": item, "budget": budget, "ask": None, "history": [],
          "rounds": 0, "status": "opening", "task_id": None, "price": None}
    log(f"[{item} @ {shop}] card ok, budget {budget}")
    t, err = send(url, request_text or f"I want to buy a {item}")
    if err or state(t) != "TASK_STATE_INPUT_REQUIRED":
        st["status"] = "no_offer"
        return st
    st["task_id"], st["ask"] = t["id"], last_number(t)
    for _ in range(MAX_MESSAGES - 1):
        if st["ask"] is None:
            st["status"] = "no_ask"
            break
        action, price = decide(st)
        if action == "walk":
            cancel(url, st["task_id"])
            st["status"] = "walked"
            break
        offer = st["ask"] if action == "accept" else price
        st["history"].append((st["ask"], offer))
        st["rounds"] += 1
        t, err = send(url, str(offer), st["task_id"])
        if err:
            st["status"] = "error"
            break
        s = state(t)
        if s == "TASK_STATE_COMPLETED":
            st["price"], st["status"] = receipt_price(t) or offer, "bought"
            break
        if s == "TASK_STATE_INPUT_REQUIRED":
            st["ask"] = last_number(t)
            continue
        st["status"] = "rejected" if s == "TASK_STATE_REJECTED" else s
        break
    else:
        cancel(url, st["task_id"])
        st["status"] = "walked"
    log(f"[{item} @ {shop}] {st['status']}" + (f" at {st['price']}" if st["price"] else "") + f" after {st['rounds']} offers")
    return st


# ---------------------------------------------------------------- challenge 2: many shops, one list, one budget

def shop_for(items: list, cards: list, total: int):
    """Harness reads the cards (skills) and assigns each item a shop and a budget share."""
    listing = "\n".join(f'- url={c["_base"]} name="{c.get("name")}" skills={[s.get("name") + ": " + s.get("description", "") for s in c.get("skills", [])]} tags={[t for s in c.get("skills", []) for t in s.get("tags", [])]}'
                        for c in cards)
    data = harness.ask_json(
        f"You are a buyer with a shopping list {items} and a total budget of {total}. Here is a directory of shops:\n{listing}\n"
        "For each item pick ONE shop whose skills/tags say it sells that kind of item, and split the budget so the shares "
        'sum to at most the total. Reply with exactly one JSON object: {"plan": [{"item": "...", "url": "...", "budget": <integer>}, ...]}.',
        log)
    plan = (data or {}).get("plan")
    valid = isinstance(plan, list) and {p.get("item") for p in plan} == set(items) \
        and all(any(p.get("url") == c["_base"] for c in cards) for p in plan)
    if not valid:
        harness.CALLS["fallbacks"] += 1
        log("  [fallback] harness plan invalid; assigning by tag match and splitting the budget evenly")
        plan = []
        for it in items:
            c = next((c for c in cards if any(it in t for s in c.get("skills", []) for t in s.get("tags", []))), cards[0])
            plan.append({"item": it, "url": c["_base"], "budget": total // len(items)})
    # code enforces the total: scale shares down if the harness overspent
    s = sum(_int(p.get("budget")) or 0 for p in plan)
    if s > total:
        for p in plan:
            p["budget"] = int((_int(p.get("budget")) or 0) * total / s)
    return plan


def shop_many(directory: str, items: list, total: int):
    bases = json.loads(open(directory, encoding="utf-8").read())
    cards = []
    for b in bases:
        try:
            c = get_card(b); c["_base"] = b; cards.append(c)
        except Exception as e:
            log(f"[directory] {b}: card unavailable ({type(e).__name__})")
    log(f"[directory] {len(cards)} cards read for list {items}, total budget {total}")
    plan = shop_for(items, cards, total)
    for p in plan:
        log(f"[plan] {p['item']} <- {p['url']} with budget {p['budget']}")
    bought, spent = {}, 0
    with cf.ThreadPoolExecutor(max_workers=len(plan)) as ex:
        futs = {ex.submit(negotiate, p["url"], p["item"], int(p["budget"]), shop=p["url"]): p for p in plan}
        for f in cf.as_completed(futs):
            st = f.result()
            log(f"[state] {json.dumps({k: st[k] for k in ('item', 'shop', 'status', 'price', 'rounds', 'ask')})}")
            if st["status"] == "bought" and st["item"] not in bought:   # never buy an item twice
                bought[st["item"]], spent = st["price"], spent + st["price"]
    missing = [i for i in items if i not in bought]
    # second pass for items that did not close, with the budget that is left
    for it in missing[:]:
        left = total - spent
        if left <= 0:
            break
        alt = next((c for c in cards if c["_base"] != next(p["url"] for p in plan if p["item"] == it)
                    and any(it in t for s in c.get("skills", []) for t in s.get("tags", []))), None)
        if alt is None:
            continue
        log(f"[retry] {it} at another shop {alt['_base']} with the remaining {left}")
        st = negotiate(alt["_base"], it, left, shop=alt["_base"])
        if st["status"] == "bought":
            bought[it], spent = st["price"], spent + st["price"]
            missing.remove(it)
    log(f"[summary] bought {bought} spent {spent} of {total}; missing {missing}; "
        f"harness calls {harness.CALLS['n']} fallbacks {harness.CALLS['fallbacks']}")
    return {"bought": bought, "spent": spent, "total": total, "missing": missing}


# ---------------------------------------------------------------- main

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--item")
    ap.add_argument("--budget", type=int, required=True)
    ap.add_argument("--request", help="describe the item instead of naming it (challenge 1)")
    ap.add_argument("--directory", help="JSON list of shop base URLs (challenge 2)")
    ap.add_argument("--list", help="comma-separated shopping list (challenge 2)")
    args = ap.parse_args()
    log(f"buyer · harness: {harness.COMMAND}")
    if args.directory:
        res = shop_many(args.directory, [s.strip() for s in args.list.split(",")], args.budget)
        sys.exit(0 if not res["missing"] and res["spent"] <= res["total"] else 1)
    st = negotiate(args.url, args.item or "item", args.budget, request_text=args.request)
    log(f"[done] {st['status']} harness calls {harness.CALLS['n']} fallbacks {harness.CALLS['fallbacks']} "
        f"({harness.CALLS['seconds']:.0f}s in harness)")
