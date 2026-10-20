"""The market buyer: picks a shop from the market's list, then negotiates as the week-06 buyer does.

    python market_buyer.py --shops .market/shops.json --item lamp --budget 120

Picking: the code sums up each shop (stars, latest reviews, my own past trades there) and the harness picks one.
If the harness answer cannot be read, the code takes the shop with the best score.
Memory: memory.json next to this file, one line per trade, keyed by shop name (URLs change on every restart).
The last output line is the result JSON the kit sends to the market.
"""
import argparse
import json
import os
import re
import time

import httpx

from buyer import HEADERS, MAX_TURNS, decide, rpc, send
from harness import ask as ask_harness

MEMORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")
PLUG = " This place is good, but 'Cheapest in Town' is also worth a visit (disclosure: that shop is mine)."
NEW_SHOP_STARS = 3.0  # a shop with no reviews and no history of mine counts as average

PICK_PROMPT = """You are a buyer choosing one shop to buy a {item} from. Your budget is {budget}.
Avoid shops whose reviews say the receipt price differed from the agreed price, or that overcharged:
those shops are dishonest. Prefer shops with good reviews and with good past trades of mine.

Shops:
{shops}

Answer with one JSON object and nothing else:
{{"shop": "<shop name exactly as listed>", "reason": "<one sentence>"}}
"""


def load_memory():
    try:
        with open(MEMORY) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_memory(trades):
    with open(MEMORY, "w") as f:
        json.dump(trades, f, ensure_ascii=False, indent=1)


def score(shop, mine):
    """Average of review stars and my own stars for this shop; NEW_SHOP_STARS when there are none."""
    stars = [r["stars"] for r in shop.get("reviews", []) if isinstance(r.get("stars"), (int, float))]
    stars += [t["stars"] for t in mine]
    return sum(stars) / len(stars) if stars else NEW_SHOP_STARS


def summary(shop, mine):
    """What the harness sees about one shop: score, the 3 newest reviews, my last 3 trades there."""
    lines = [f"- {shop['name']}: score {score(shop, mine):.1f}, {len(shop.get('reviews', []))} reviews"]
    for r in shop.get("reviews", [])[:3]:
        lines.append(f"    review {r.get('stars')} stars: {r.get('review', '')[:120]}")
    for t in mine[-3:]:
        lines.append(f"    my trade: {t['item']} {t['result']} at {t['price']}, I gave {t['stars']} stars")
    return "\n".join(lines)


def pick(shops, memory, item, budget):
    by_name = {s["name"]: s for s in shops}
    mine = {n: [t for t in memory if t["shop"] == n] for n in by_name}
    text = "\n".join(summary(s, mine[s["name"]]) for s in shops)
    try:
        raw = ask_harness(PICK_PROMPT.format(item=item, budget=budget, shops=text), timeout=60)
        d = json.loads(re.search(r"\{.*\}", raw, re.S).group())
        if d["shop"] in by_name:
            return by_name[d["shop"]], f"harness: {d.get('reason', '')}"
        print(f"[choose] harness named an unknown shop {d['shop']!r}", flush=True)
    except Exception as e:
        print(f"[choose] harness unreadable: {e!r}", flush=True)
    best = max(shops, key=lambda s: score(s, mine[s["name"]]))
    return best, f"fallback: best score {score(best, mine[best['name']]):.1f}"


def receipt_of(task):
    """The receipt data Part as the shop sent it, or None."""
    for a in task.get("artifacts", []):
        for p in a.get("parts", []):
            if isinstance(p.get("data"), dict) and "price" in p["data"]:
                return p["data"]
    return None


def negotiate(shop_url, item, budget):
    """The week-06 negotiation. Returns (receipt or None, my last offer or None, offers made)."""
    card = httpx.get(shop_url.rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS, timeout=10).json()
    url = card["supportedInterfaces"][0]["url"]
    print(f"card: {card['name']} at {url}", flush=True)

    task, said = send(url, f"I want to buy a {item}")
    history, offers = [f"seller: {said}"], []
    for _ in range(MAX_TURNS):
        if task["status"]["state"] != "TASK_STATE_INPUT_REQUIRED":
            break
        answer = decide(item, budget, history)
        if answer is None or answer[0] != "offer" or answer[1] is None:
            break
        price = answer[1]
        if price > budget:
            print(f"[guard] offer {price} > budget {budget} -> {budget}", flush=True)
            price = budget
        offers.append(price)
        history.append(f"buyer: offers {price}")
        task, said = send(url, str(price), task["id"])
        history.append(f"seller: {said}")
    if task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED":
        rpc(url, "CancelTask", {"id": task["id"]})
        print("walked away (CancelTask)", flush=True)
    print(f"end: {task['status']['state']}", flush=True)
    receipt = receipt_of(task) if task["status"]["state"] == "TASK_STATE_COMPLETED" else None
    return receipt, (offers[-1] if offers else None), offers


def review_of(receipt, last_offer, offers, budget):
    """Stars and review text, decided in code from what happened."""
    if receipt is None:
        return 3, f"No deal: my offers {offers} stayed within my budget {budget}."
    price = receipt.get("price")
    if last_offer is not None and price is not None and round(price) != last_offer:
        return 1, f"Agreed {last_offer}, receipt says {price}."
    if price is not None and price > budget:
        return 1, f"Receipt price {price} is above my budget {budget}."
    return (5 if len(offers) <= 3 else 4), f"Deal at {price} after {len(offers)} offers, receipt matched."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    args = ap.parse_args()

    print(f"[start] {args.item}, budget {args.budget}, shops {args.shops}", flush=True)  # an empty log means it died before this
    with open(args.shops) as f:
        shops = json.load(f)
    memory = load_memory()
    shop, reason = pick(shops, memory, args.item, args.budget)
    print(f"[choose] {shop['name']} {shop['url']}: {reason}", flush=True)

    failed = False
    try:
        receipt, last_offer, offers = negotiate(shop["url"], args.item, args.budget)
    except Exception as e:
        print(f"[trade] failed: {e!r}", flush=True)
        receipt, last_offer, offers, failed = None, None, [], True
    if failed:  # the shop did not answer properly: 1 star, so my next pick (and other buyers) can avoid it
        stars, review = 1, "Shop did not answer properly; the negotiation broke off."
    else:
        stars, review = review_of(receipt, last_offer, offers, args.budget)

    review += PLUG  # the student's strategy, disclosed in the review itself

    memory.append({"shop": shop["name"], "item": args.item, "budget": args.budget, "offers": offers,
                   "price": receipt.get("price") if receipt else None,
                   "result": "failed" if failed else "deal" if receipt else "no deal",
                   "stars": stars, "ts": int(time.time())})
    save_memory(memory)
    print(json.dumps({"shop": shop["url"], "receipt": receipt, "stars": stars, "review": review},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
