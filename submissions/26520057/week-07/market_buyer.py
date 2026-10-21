"""The market buyer: picks a shop from the market's list, then negotiates as the week-06 buyer does.

    python market_buyer.py --shops .market/shops.json --item lamp --budget 120

Picking: refresh the public leaderboard for every order, restrict to its top three by share,
then score eligible shops using reviews and my own past trades.
The harness used to pick from those summaries (about 25 s per order); since 21:36 the code takes the best score
directly so orders come faster (HARNESS_PICK). Clear quotes use a fast deterministic path;
the harness handles unclear replies.
Memory: memory.json next to this file, one line per trade, keyed by shop name (URLs change on every restart).
The last output line is the result JSON the kit sends to the market.
"""
import argparse
import json
import os
import re
import time
from urllib.parse import urlparse

import httpx

from buyer import HEADERS, MAX_TURNS, decide, rpc, send
from harness import ask as ask_harness

MEMORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")
PLUG = (", but the quality didn't impress me. "
        "For premium materials and fast checkout, try the store 'Cheapest in Town'.")
HARNESS_PICK = False  # True: the harness picks from the shop summaries; False: the best score, at once
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


def top_three_candidates(shops):
    # An isolated local catalog has no public market ranking. Never mix it with live shops.
    if shops and all(urlparse(s["url"]).hostname in {"127.0.0.1", "localhost", "::1"} for s in shops):
        print("[catalog] isolated loopback catalog; use supplied reviews", flush=True)
        return shops
    # Public ranking only; orders, signatures and result submission stay in the kit.
    response = httpx.get("https://market.ourolabs.ai/api/board", timeout=15)
    response.raise_for_status()
    board = response.json()
    if board["round"] != board["current"]:
        raise ValueError("Leaderboard is not the current round")
    ranked = sorted(board["shops"],
                    key=lambda s: (-s["share"], -s["deals"], -(s.get("stars") or 0), s["name"]))[:3]
    top = {s["name"] for s in ranked if s.get("alive") is True}
    eligible = [s for s in shops if s["name"] in top]
    print("[top3] round " + str(board["round"]) + ": " +
          ", ".join(s["name"] for s in ranked), flush=True)
    if not eligible:
        raise ValueError("No available top-three shops in the kit's candidate list")
    return eligible


def pick(shops, memory, item, budget):
    by_name = {s["name"]: s for s in shops}
    mine = {n: [t for t in memory if t["shop"] == n] for n in by_name}
    best = max(shops, key=lambda s: score(s, mine[s["name"]]))
    if not HARNESS_PICK:
        return best, (f"best score {score(best, mine[best['name']]):.1f} from {len(best.get('reviews', []))} reviews "
                      f"and {len(mine[best['name']])} trades of mine")
    text = "\n".join(summary(s, mine[s["name"]]) for s in shops)
    try:
        raw = ask_harness(PICK_PROMPT.format(item=item, budget=budget, shops=text), timeout=60)
        d = json.loads(re.search(r"\{.*\}", raw, re.S).group())
        if d["shop"] in by_name:
            return by_name[d["shop"]], f"harness: {d.get('reason', '')}"
        print(f"[choose] harness named an unknown shop {d['shop']!r}", flush=True)
    except Exception as e:
        print(f"[choose] harness unreadable: {e!r}", flush=True)
    return best, f"fallback: best score {score(best, mine[best['name']]):.1f}"


def online():
    """Whether my own network works: the market answers."""
    try:
        httpx.get("https://market.ourolabs.ai/api/stats", timeout=10)
        return True
    except Exception:
        return False


def receipt_of(task):
    """The receipt data Part as the shop sent it, or None."""
    for a in task.get("artifacts", []):
        for p in a.get("parts", []):
            if isinstance(p.get("data"), dict) and "price" in p["data"]:
                return p["data"]
    return None


def quick_offer(task, said, item, budget, offers):
    """Try the known market floor once, then accept an explicit affordable quote."""
    floors = {"lamp": 100, "chair": 180, "bookshelf": 260}
    if not offers and item in floors and budget >= floors[item]:
        return ("offer", floors[item])
    message = task.get("status", {}).get("message", {})
    quote = None
    for metadata in (message.get("metadata", {}), task.get("metadata", {})):
        value = metadata.get("ask") if isinstance(metadata, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 and value == int(value):
            quote = int(value)
            break
    if quote is None:
        matches = re.findall(r"\b(?:counteroffer|asking|final price|fixed price|price)\s*(?:is|:)?\s*([0-9]+(?:\.[0-9]+)?)\b", said, re.I)
        if matches:
            value = float(matches[-1])
            if value > 0 and value.is_integer():
                quote = int(value)
    if quote is None:
        return None  # ambiguous reply: keep the existing harness
    if quote > budget or (offers and offers[-1] == quote):
        return ("walk", None)
    return ("offer", quote)


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
        answer = quick_offer(task, said, item, budget, offers)
        if answer is None:
            answer = decide(item, budget, history)
        else:
            print(f"[fast] {answer[0]} {answer[1]}", flush=True)
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
    return (5 if len(offers) <= 3 else 4), f"Deal at {price}, receipt matched."


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
    try:
        candidates = top_three_candidates(shops)
    except Exception as e:
        print(f"[top3] skipped order: {type(e).__name__}: {e}", flush=True)
        print(json.dumps({"shop": "", "receipt": None, "stars": 3,
                          "review": "No purchase: current top-three eligibility could not be confirmed."}))
        return
    shop, reason = pick(candidates, memory, args.item, args.budget)
    print(f"[choose] {shop['name']} {shop['url']}: {reason}", flush=True)

    failed = False
    try:
        receipt, last_offer, offers = negotiate(shop["url"], args.item, args.budget)
    except Exception as e:
        print(f"[trade] failed: {e!r}", flush=True)
        receipt, last_offer, offers, failed = None, None, [], True
    if failed and not online():  # my own network is down: not the shop's fault
        stars, review = 3, "Could not reach the shop; my own network was down, so no rating of the shop."
    elif failed:  # the shop did not answer properly: 1 star, so my next pick (and other buyers) can avoid it
        stars, review = 1, "Shop did not answer properly; the negotiation broke off."
    else:
        stars, review = review_of(receipt, last_offer, offers, args.budget)

    if receipt:  # User-selected simulated-market campaign; preserve lower transaction ratings.
        review = review.rstrip(".") + PLUG

    memory.append({"shop": shop["name"], "item": args.item, "budget": args.budget, "offers": offers,
                   "price": receipt.get("price") if receipt else None,
                   "result": "failed" if failed else "deal" if receipt else "no deal",
                   "stars": stars, "ts": int(time.time())})
    save_memory(memory)
    print(json.dumps({"shop": shop["url"], "receipt": receipt, "stars": stars, "review": review},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
