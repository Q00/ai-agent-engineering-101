"""Market buyer: the harness picks one shop from {shops}, then negotiates over A2A v1.0 as in week 6.

    python market_buyer.py --shops .market/shops.json --item lamp --budget 120

Reviews reach the harness as a summary the code makes (count and average stars per shop),
next to this buyer's own past trades from MEMORY. The last output line is the result for the kit.
"""
import argparse
import json
import re
import time
import uuid
from pathlib import Path

import httpx

from harness import ask_json

MAX_OFFERS = 8
MEMORY = Path("buyer_memory.jsonl")   # one JSON line per trade, shops remembered by name

# DECIDE: the wording of this prompt is the student's choice (how the harness picks a shop).
CHOOSE = """You are a buying agent. Your user wants one {item}. The budget is {budget}.
Pick one shop to negotiate with. For each shop you see a summary of its market reviews and of your own past trades with it.

{summary}

Answer with JSON only:
{{"shop": "<shop name from the list>", "reason": "<one sentence>"}}"""

PROMPT = """You are a buying agent. Your user wants one {item}. The budget is {budget}.
Never offer or agree to more than {budget}. Pay as little as you can, but shops may end the negotiation after a few low offers,
so move toward the shop's price in real steps rather than by one or two. Walking away is fine when the shop will not come down to the budget.

Shop: {card}

Conversation so far:
{transcript}

Decide your next move. Answer with JSON only, one of:
{{"action": "offer", "price": <whole number>}}   send an offer (send the shop's price to accept it)
{{"action": "walk"}}                              walk away without buying"""

REVIEW = """You are a buying agent and just finished a negotiation for one {item} (budget {budget}) with the shop {shop}.

Conversation:
{transcript}

Facts: result {result}; your last offer {agreed}; price on the shop's receipt {receipt_price}.

Rate the shop for other buyers. Answer with JSON only:
{{"stars": <whole number 1 to 5>, "review": "<one sentence>"}}"""


def load_memory():
    if not MEMORY.exists():
        return []
    return [json.loads(l) for l in MEMORY.read_text(encoding="utf-8").splitlines() if l.strip()]


def summarize(shops, memory):
    lines = []
    for s in shops:
        stars = [r["stars"] for r in s.get("reviews", []) if isinstance(r.get("stars"), (int, float))]
        line = f"- {s['name']}: {len(stars)} reviews" + (f", average {sum(stars) / len(stars):.1f} stars" if stars else "")
        mine = [m for m in memory if m["shop"] == s["name"]]
        if mine:
            deals = [m for m in mine if m["result"] == "deal"]
            differed = sum(1 for m in deals if m["receipt_price"] != m["agreed"])
            line += (f" | your trades: {len(mine)}, deals {len(deals)}"
                     + "".join(f", {m['item']} at {m['receipt_price']}" for m in deals[-3:])
                     + f", receipt differed from your last offer {differed} times")
        else:
            line += " | your trades: none"
        if s.get("offers"):                             # Round 3: what this shop has of the ordered category
            line += " | in stock: " + "; ".join(f"{o['name'][:40]} x{o['qty']}" for o in s["offers"][:3])
        lines.append(line)
    return "\n".join(lines)


def pick_offer(shop):
    """Round 3: the SKU to ask for. Temporary rule: the one this shop has most of (least likely sold out
    by the time we ask). None before Round 3 (shops.json has no offers)."""
    offers = shop.get("offers") or []
    return max(offers, key=lambda o: o.get("qty") or 0) if offers else None


def choose(shops, memory, item, budget):
    stocked = [s for s in shops if s.get("offers")]     # Round 3: only shops that have the ordered category
    shops = stocked or shops
    names = {s["name"]: s for s in shops}
    for _ in range(2):
        move, raw = ask_json(CHOOSE.format(item=item, budget=budget, summary=summarize(shops, memory)))
        print(f"[harness] {raw}", flush=True)
        if move and move.get("shop") in names:
            return names[move["shop"]], move.get("reason", "")
    return shops[0], "harness named no shop from the list twice, took the first one"


def receipt_of(t):
    for a in (t or {}).get("artifacts", []):
        for p in a.get("parts", []):
            d = p.get("data")
            if isinstance(d, dict) and isinstance(d.get("price"), (int, float)):
                return d
    return None


def negotiate(shop_url, item, budget, ask=None):
    """Week-06 buyer loop. Returns (final task or None, transcript lines, last offer sent).
    ask: the first message; Round 3 asks for one SKU by name, as the market's agent.md suggests."""
    card = httpx.get(shop_url.rstrip("/") + "/.well-known/agent-card.json", timeout=10).json()
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

    ask = ask or f"I want to buy a {item}"
    t, said = send(ask)
    lines = [f"you: {ask}", f"shop: {said}"]
    offer = None
    for _ in range(MAX_OFFERS):
        if t["status"]["state"] != "TASK_STATE_INPUT_REQUIRED":
            break
        move, raw = ask_json(PROMPT.format(item=item, budget=budget, card=card.get("description", ""),
                                           transcript="\n".join(lines)))
        print(f"[harness] {raw}", flush=True)
        if not move or move.get("action") != "offer" or not isinstance(move.get("price"), (int, float)):
            rpc("CancelTask", {"id": t["id"]})
            print("walked away (CancelTask)", flush=True)
            lines.append("you: walked away")
            return t, lines, offer
        offer = int(move["price"])
        if offer > budget:                              # the harness may forget the budget; the code does not
            print(f"[guard] offer {offer} above budget {budget} -> {budget}", flush=True)
            offer = budget
        t, said = send(str(offer), t["id"])
        lines += [f"you: {offer}", f"shop: {said}"]
    if t["status"]["state"] == "TASK_STATE_INPUT_REQUIRED":
        rpc("CancelTask", {"id": t["id"]})
        print("offer limit reached (CancelTask)", flush=True)
        lines.append("you: offer limit reached")
    return t, lines, offer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    a = ap.parse_args()

    shops = json.loads(Path(a.shops).read_text(encoding="utf-8"))
    memory = load_memory()
    shop, reason = choose(shops, memory, a.item, a.budget)
    print(f"[choose] {shop['url']}: {reason}", flush=True)
    want = pick_offer(shop)
    item = want["name"] if want else a.item             # what the negotiation prompts call the item
    ask = f"I want to buy {want['name']} ({want['sku']})" if want else None

    try:
        t, lines, offer = negotiate(shop["url"], item, a.budget, ask)
        receipt = receipt_of(t) if t["status"]["state"] == "TASK_STATE_COMPLETED" else None
        result = "deal" if receipt else "no deal"
    except Exception as e:                              # unreachable shop, protocol error: still close the order
        print(f"[error] {e!r}", flush=True)
        lines, offer, receipt, result = [f"error: {e!r}"[:300]], None, None, "error"

    receipt_price = int(receipt["price"]) if receipt else None
    move, raw = ask_json(REVIEW.format(item=a.item, budget=a.budget, shop=shop["name"], transcript="\n".join(lines),
                                       result=result, agreed=offer, receipt_price=receipt_price))
    print(f"[harness] {raw}", flush=True)
    stars = (move or {}).get("stars")
    stars = min(5, max(1, int(stars))) if isinstance(stars, (int, float)) else 3
    review = str((move or {}).get("review") or f"{result} on {a.item}").strip()

    with MEMORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "shop": shop["name"], "item": a.item,
                            "sku": want["sku"] if want else None, "budget": a.budget, "result": result, "agreed": offer, "receipt_price": receipt_price,
                            "stars": stars}, ensure_ascii=False) + "\n")
    print(json.dumps({"shop": shop["url"], "receipt": receipt, "stars": stars, "review": review}, ensure_ascii=False),
          flush=True)


if __name__ == "__main__":
    main()
