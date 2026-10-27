"""Market buyer (week-07 LAB 1): pick a shop from {shops}, negotiate over A2A v1.0, remember, review.

    python market_buyer.py --shops .market/shops.json --item lamp --budget 120

How it works
  1. choose   : score every shop in code from (a) its public reviews, newest weighted most,
                (b) red-flag words in low-star reviews (overcharged, receipt said ...), and
                (c) this buyer's own memory of past trades with that shop name.
                The best score wins; the reason is printed as a [choose] line.
  2. negotiate: the week-06 harness loop. The harness also sees a short reputation summary.
                The budget is enforced in code: no offer above --budget is ever sent.
  3. check    : the receipt must name the item, carry the A2A task id, be signed (sig_seller)
                and show the price we offered last. A mismatch is remembered as "cheated".
  4. remember : one JSON object per trade appended to buyer_memory.json (shops by name).
  5. report   : the last stdout line is the result JSON the kit sends to the market.
"""
import argparse
import json
import random
import re
import time
import uuid
from pathlib import Path

import httpx

from harness import ask_json

MAX_OFFERS = 8
MEMORY_FILE = Path("buyer_memory.json")       # the kit runs us inside the week-07 directory
MEMORY_KEEP = 300                             # keep the newest N trades
PRIOR, PRIOR_WEIGHT, DECAY = 3.0, 2.0, 0.85   # review score: shrink toward 3 stars, newest weighs most
RED_FLAG = re.compile(r"receipt (said|says|was)|overcharg|higher than|more than (we|i) agreed|"
                      r"did not match|didn't match|mismatch|avoid|scam|cheat|fraud|fake|forged|unsigned", re.I)

PROMPT = """You are a buying agent. Your user wants one {item}. The budget is {budget}.
Never offer or agree to more than {budget}. Pay as little as you can, but shops may end the negotiation after a few low offers,
so move toward the shop's price in real steps rather than by one or two. Walking away is fine when the shop will not come down to the budget.

Shop: {card}
What other buyers and your own past trades say about this shop: {reputation}

Conversation so far:
{transcript}

Decide your next move. Answer with JSON only, one of:
{{"action": "offer", "price": <whole number>}}   send an offer (send the shop's price to accept it)
{{"action": "walk"}}                              walk away without buying"""


# ---------------------------------------------------------------- memory
def load_memory():
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_memory(mem, entry):
    mem.append(entry)
    try:
        MEMORY_FILE.write_text(json.dumps(mem[-MEMORY_KEEP:], ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        print(f"[memory] could not write {MEMORY_FILE}: {e}", flush=True)


# ---------------------------------------------------------------- choosing
def review_score(reviews):
    """Weighted average stars (newest first), shrunk toward PRIOR; plus the number of red-flag reviews."""
    num, den, flags = PRIOR * PRIOR_WEIGHT, PRIOR_WEIGHT, 0
    for i, r in enumerate(reviews or []):
        stars = r.get("stars")
        if isinstance(stars, (int, float)) and 1 <= stars <= 5:
            w = DECAY ** i
            num, den = num + w * stars, den + w
        text = str(r.get("review", ""))
        if RED_FLAG.search(text) and (not isinstance(stars, (int, float)) or stars <= 2):
            flags += 1
    return num / den, flags


def memory_score(mem, name, item):
    """Adjustment from our own trades with this shop name, and a short note for the log."""
    mine = [m for m in mem if m.get("shop") == name]
    if not mine:
        return 0.0, "no trades of mine"
    cheated = sum(1 for m in mine if m.get("result") == "cheated")
    deals = [m for m in mine if m.get("result") == "deal"]
    walks = sum(1 for m in mine if m.get("result") == "walk")
    errors = sum(1 for m in mine if m.get("result") == "error")
    adj = -3.0 * cheated + min(0.3 * len(deals), 1.0) - 0.1 * walks - 0.2 * errors
    same = [d for d in deals if d.get("item") == item and d.get("budget")]
    if same:                                   # cheaper than budget on average -> small bonus
        ratio = sum(d["price"] / d["budget"] for d in same) / len(same)
        adj += 0.5 * (1 - ratio)
    note = f"mine: {len(deals)} deals, {walks} walks, {cheated} cheated, {errors} errors"
    return adj, note


def choose(shops, mem, item):
    scored = []
    for s in shops:
        rev, flags = review_score(s.get("reviews"))
        madj, mnote = memory_score(mem, s.get("name"), item)
        score = rev - 0.7 * min(flags, 3) + madj + random.uniform(0, 0.05)   # tiny jitter: explore ties
        n = len(s.get("reviews") or [])
        scored.append((score, s, f"score {score:.2f} = reviews {rev:.2f} ({n}), red flags {flags}, {mnote}"))
    scored.sort(key=lambda x: x[0], reverse=True)
    for score, s, why in scored:
        print(f"[score] {s.get('name')}: {why}", flush=True)
    return scored[0]


def reputation(shop, why):
    recent = [f"{r.get('stars')}* {str(r.get('review', ''))[:80]}" for r in (shop.get("reviews") or [])[:3]]
    return f"{why}. Recent reviews: " + (" | ".join(recent) if recent else "none")


# ---------------------------------------------------------------- A2A
def make_rpc(url):
    def rpc(method, params):
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
        r = httpx.post(url, json=body, headers={"A2A-Version": "1.0"}, timeout=200).json()
        if "error" in r:
            raise RuntimeError(r["error"])
        return r["result"].get("task", r["result"])
    return rpc


def receipt_of(t):
    for a in (t or {}).get("artifacts", []) or []:
        for p in a.get("parts", []) or []:
            d = p.get("data")
            if isinstance(d, dict) and isinstance(d.get("price"), (int, float)):
                return d
    return None


def whole(x):
    return int(x) if isinstance(x, float) and x.is_integer() else x


def negotiate(base_url, item, budget, rep):
    """Returns (task, receipt or None, last offer we sent, number of offers, log of offers)."""
    card = httpx.get(base_url.rstrip("/") + "/.well-known/agent-card.json", timeout=15).json()
    ifaces = card.get("supportedInterfaces") or [{}]
    url = ifaces[0].get("url") or base_url
    print(f"card: {card.get('name')} -> {url}", flush=True)
    rpc = make_rpc(url)

    def send(text, task_id=None):
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        t = rpc("SendMessage", {"message": msg})
        said = " ".join(p.get("text", "") for p in t["status"].get("message", {}).get("parts", []))
        print(f"-> {text!r}  <- {t['status']['state']} | {said}", flush=True)
        return t, said

    t, said = send(f"I want to buy a {item}")
    lines = [f"you: I want to buy a {item}", f"shop: {said}"]
    last_offer, offers, at_budget = None, [], 0
    for _ in range(MAX_OFFERS):
        if t["status"]["state"] != "TASK_STATE_INPUT_REQUIRED":
            break
        move, raw = ask_json(PROMPT.format(item=item, budget=budget, card=card.get("description", ""),
                                           reputation=rep, transcript="\n".join(lines)))
        print(f"[harness] {raw}", flush=True)
        if not move or move.get("action") != "offer" or not isinstance(move.get("price"), (int, float)):
            rpc("CancelTask", {"id": t["id"]})
            print("walked away (CancelTask)", flush=True)
            return t, None, last_offer, offers
        offer = int(move["price"])
        if offer > budget:                       # the harness may forget the budget; the code does not
            print(f"[guard] offer {offer} above budget {budget} -> {budget}", flush=True)
            offer = budget
        if offer == budget:
            at_budget += 1
            if at_budget > 2:                    # the shop refused our budget twice already
                rpc("CancelTask", {"id": t["id"]})
                print("budget refused twice, walked away (CancelTask)", flush=True)
                return t, None, last_offer, offers
        t, said = send(str(offer), t["id"])
        last_offer = offer
        offers.append(offer)
        lines += [f"you: {offer}", f"shop: {said}"]
    if t["status"]["state"] == "TASK_STATE_INPUT_REQUIRED":
        rpc("CancelTask", {"id": t["id"]})
        print("offer limit reached (CancelTask)", flush=True)
        return t, None, last_offer, offers
    rec = receipt_of(t) if t["status"]["state"] == "TASK_STATE_COMPLETED" else None
    return t, rec, last_offer, offers


def check_receipt(rec, t, item, budget, last_offer):
    """List of problems with a receipt (empty when it looks honest)."""
    problems = []
    price = whole(rec.get("price"))
    if rec.get("item") != item:
        problems.append(f"item {rec.get('item')!r} is not {item!r}")
    if last_offer is not None and isinstance(price, (int, float)) and price > last_offer:
        problems.append(f"agreed {last_offer}, receipt says {price}")
    if isinstance(price, (int, float)) and price > budget:
        problems.append(f"receipt price {price} above budget {budget}")
    if rec.get("task") and rec.get("task") != t.get("id"):
        problems.append("receipt task id is not this negotiation")
    if not rec.get("task"):
        problems.append("receipt has no task id")
    if not rec.get("sig_seller"):
        problems.append("receipt is not signed")
    return problems


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    a = ap.parse_args()

    shops = json.loads(Path(a.shops).read_text(encoding="utf-8"))
    mem = load_memory()
    result = {"shop": shops[0]["url"] if shops else "", "receipt": None, "stars": 3, "review": "No shop to trade with."}
    entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "item": a.item, "budget": a.budget}
    try:
        if not shops:
            raise RuntimeError("empty shop list")
        score, shop, why = choose(shops, mem, a.item)
        print(f"[choose] {shop['url']}: {shop.get('name')} - {why}", flush=True)
        result["shop"] = shop["url"]
        entry["shop"] = shop.get("name")

        t, rec, last_offer, offers = negotiate(shop["url"], a.item, a.budget, reputation(shop, why))
        entry["offers"] = offers
        if rec is None:
            entry["result"] = "walk"
            result.update(stars=3, review=f"No deal for the {a.item} within budget {a.budget}; "
                                          f"my offers {offers or 'none'}.")
        else:
            result["receipt"] = rec                       # exactly the data Part the shop issued
            problems = check_receipt(rec, t, a.item, a.budget, last_offer)
            price = whole(rec.get("price"))
            entry.update(price=price, task=rec.get("task"))
            if any(p.startswith(("agreed", "receipt price", "item")) for p in problems):
                entry["result"] = "cheated"
                entry["problems"] = problems
                result.update(stars=1, review=f"Receipt does not match the deal: {'; '.join(problems)}.")
            else:
                entry["result"] = "deal"
                stars = 5 if price <= 0.85 * a.budget else 4
                if problems:                              # e.g. unsigned: honest price, but not verifiable
                    stars, entry["problems"] = 3, problems
                rounds = len(offers)
                result.update(stars=stars, review=f"Bought the {a.item} at {price} (budget {a.budget}) after "
                                                  f"{rounds} offer{'s' if rounds != 1 else ''}; receipt matched"
                                                  + (f", but {'; '.join(problems)}." if problems else "."))
    except Exception as e:                                # always end with a result line
        entry["result"] = "error"
        entry["error"] = str(e)[:200]
        print(f"[error] {e}", flush=True)
        result.update(stars=2, review=f"Could not complete the trade: {str(e)[:120]}")
    if entry.get("shop"):
        save_memory(mem, entry)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
