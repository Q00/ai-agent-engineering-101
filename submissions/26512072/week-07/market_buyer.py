"""Market buyer (LAB 1): picks a shop from {shops} using reviews and its own memory, trades over A2A,
checks the receipt, remembers the trade, and prints one JSON result line for the kit.

    python market_buyer.py --shops .market/shops.json --item lamp --budget 120

The harness (harness.py, claude -p haiku) picks the shop, makes every offer, and writes the review.
The code enforces the budget, checks the receipt against the agreed price, and keeps the memory.

Memory: buyer_memory.jsonl, one line per trade:
    {"time", "shop" (name), "item", "budget", "outcome": deal|walked|rejected, "agreed", "receipt_price",
     "receipt_ok": true|false|null, "stars"}
Shops are remembered by name; their URLs change whenever their kit restarts.
"""
import argparse
import json
import os
import time
import uuid
from pathlib import Path

import httpx

from harness import ask_json

HERE = Path(__file__).resolve().parent
MEMORY = Path(os.environ.get("BUYER_MEMORY", HERE / "buyer_memory.jsonl"))
MAX_OFFERS = 8

CHOOSE = """You are a buying agent in a market. Your user wants one {item}; the budget is {budget}.
Some shops in this market are dishonest: after agreeing on a price they issue a receipt with a higher price.
Pick the one shop you trust most to sell honestly at a fair price.
- Avoid shops whose reviews or whose record in your own memory mention a receipt that differs from the agreed price,
  overcharging, or low stars.
- A shop with no reviews is better than a shop with bad reviews.
- Among honest shops, prefer the ones where you got lower prices before.

Shops (name | reviews | newest reviews | your own memory):
{shops}

Answer with JSON only: {{"shop": "<name exactly as listed>", "reason": "<one sentence>"}}"""

OFFER = """You are a buying agent. Your user wants one {item}. The budget is {budget}.
Never offer or agree to more than {budget}. Pay as little as you can, but shops may end the negotiation after a few low offers,
so move toward the shop's price in real steps rather than by one or two. Walking away is fine when the shop will not come down to the budget.

Shop: {card}

Conversation so far:
{transcript}

Decide your next move. Answer with JSON only, one of:
{{"action": "offer", "price": <whole number>}}   send an offer (send the shop's price to accept it)
{{"action": "walk"}}                              walk away without buying"""

REVIEW = """You just finished a negotiation in a market as the buyer. Write a short public review of the shop.
Item: {item}. Budget: {budget}. Outcome: {outcome}.
Shop's asks: {asks}. Your offers: {offers}.
Receipt check: {check}

Answer with JSON only: {{"stars": <whole number 1 to 5>, "review": "<one sentence, factual>"}}"""


def load_memory():
    if not MEMORY.exists():
        return []
    out = []
    for line in MEMORY.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def shop_line(shop, memory):
    """One line per shop: what the harness sees instead of every review and every past trade."""
    reviews = shop.get("reviews") or []
    stars = [r["stars"] for r in reviews if isinstance(r.get("stars"), (int, float))]
    avg = f"{sum(stars) / len(stars):.1f}" if stars else "none"
    newest = " / ".join(str(r.get("review", ""))[:90] for r in reviews[:3]) or "-"
    mine = [m for m in memory if m.get("shop") == shop["name"]]
    deals = [m for m in mine if m.get("outcome") == "deal"]
    bad = sum(1 for m in mine if m.get("receipt_ok") is False)
    my = (f"{len(mine)} trades, {len(deals)} deals"
          + (f", prices {[m['agreed'] for m in deals][-3:]}" if deals else "")
          + (f", {bad} receipts differed from the agreed price" if bad else "")) if mine else "never traded"
    return f"{shop['name']} | {len(reviews)} reviews, avg stars {avg} | {newest} | {my}"


def pick(shops, memory, item, budget):
    lines = "\n".join(shop_line(s, memory) for s in shops)
    move, raw = ask_json(CHOOSE.format(item=item, budget=budget, shops=lines))
    print(f"[harness] {raw}", flush=True)
    names = {s["name"]: s for s in shops}
    if move and move.get("shop") in names:
        return names[move["shop"]], str(move.get("reason", ""))
    # unusable answer: the shop with the best average stars, unreviewed counted as 3
    def score(s):
        st = [r["stars"] for r in s.get("reviews") or [] if isinstance(r.get("stars"), (int, float))]
        return sum(st) / len(st) if st else 3.0
    best = max(shops, key=score)
    return best, "fallback: best average stars (the harness answer named no listed shop)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    a = ap.parse_args()

    shops = json.loads(Path(a.shops).read_text(encoding="utf-8"))
    memory = load_memory()
    shop, reason = pick(shops, memory, a.item, a.budget)
    print(f"[choose] {shop['url']}: {shop['name']}: {reason}", flush=True)

    card = httpx.get(shop["url"].rstrip("/") + "/.well-known/agent-card.json",
                     headers={"A2A-Version": "1.0"}, timeout=20).json()
    url = card["supportedInterfaces"][0]["url"]
    print(f"card: {card['name']} -> {url}", flush=True)

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
    asks, offers, outcome = [], [], "walked"
    for _ in range(MAX_OFFERS):
        if t["status"]["state"] != "TASK_STATE_INPUT_REQUIRED":
            break
        move, raw = ask_json(OFFER.format(item=a.item, budget=a.budget, card=card.get("description", ""),
                                          transcript="\n".join(lines)))
        print(f"[harness] {raw}", flush=True)
        if not move or move.get("action") != "offer" or not isinstance(move.get("price"), (int, float)):
            rpc("CancelTask", {"id": t["id"]})
            print("walked away (CancelTask)", flush=True)
            break
        offer = int(move["price"])
        if offer > a.budget:                    # the harness may forget the budget; the code does not
            print(f"[guard] offer {offer} above budget {a.budget} -> {a.budget}", flush=True)
            offer = a.budget
        offers.append(offer)
        t, said = send(str(offer), t["id"])
        lines += [f"you: {offer}", f"shop: {said}"]
    else:
        if t["status"]["state"] == "TASK_STATE_INPUT_REQUIRED":
            rpc("CancelTask", {"id": t["id"]})
            print("offer limit reached (CancelTask)", flush=True)

    state = t["status"]["state"]
    receipt = None
    for art in t.get("artifacts") or []:
        for part in art.get("parts") or []:
            if isinstance(part.get("data"), dict) and "price" in part["data"]:
                receipt = part["data"]
    receipt_ok, check = None, "no deal, no receipt"
    if state == "TASK_STATE_COMPLETED" and receipt is not None:
        outcome = "deal"
        agreed = offers[-1] if offers else None
        problems = []
        if agreed is not None and float(receipt.get("price", -1)) != float(agreed):
            problems.append(f"agreed {agreed}, receipt says {receipt.get('price')}")
        if receipt.get("item") != a.item:
            problems.append(f"ordered {a.item}, receipt says {receipt.get('item')}")
        if receipt.get("task") not in (None, t["id"]):
            problems.append(f"receipt task {receipt.get('task')} is not this negotiation")
        if float(receipt.get("price", 0)) > a.budget:
            problems.append(f"receipt price {receipt.get('price')} is above the budget {a.budget}")
        receipt_ok = not problems
        check = "receipt matches the agreed price" if receipt_ok else "; ".join(problems)
    elif state == "TASK_STATE_REJECTED":
        outcome = "rejected"
    print(f"[receipt] {check}", flush=True)

    move, raw = ask_json(REVIEW.format(item=a.item, budget=a.budget, outcome=outcome, asks=lines[1::2],
                                       offers=offers, check=check))
    print(f"[harness] {raw}", flush=True)
    stars = move.get("stars") if move else None
    stars = min(5, max(1, int(stars))) if isinstance(stars, (int, float)) else 3
    review = str((move or {}).get("review") or f"{outcome} for {a.item}.")[:300]
    if receipt_ok is False:                     # a cheating receipt gets one star and the facts, whatever the harness wrote
        stars, review = 1, f"{check[0].upper()}{check[1:]}."

    MEMORY.parent.mkdir(parents=True, exist_ok=True)
    with MEMORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "shop": shop["name"], "item": a.item,
                            "budget": a.budget, "outcome": outcome, "agreed": offers[-1] if outcome == "deal" else None,
                            "receipt_price": receipt.get("price") if receipt else None, "receipt_ok": receipt_ok,
                            "stars": stars}) + "\n")
    print(json.dumps({"shop": shop["url"], "receipt": receipt if outcome == "deal" else None,
                      "stars": stars, "review": review}), flush=True)


if __name__ == "__main__":
    main()
