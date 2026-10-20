"""Market buyer: picks a shop from the market's list, trades over A2A, keeps a memory, writes a review.

    python market_buyer.py --shops <shops.json> --item <item> --budget <budget>

The harness picks the shop (from reviews and this buyer's own memory), every offer, and the
review. The code keeps the budget, checks the receipt against what was agreed, and appends
each trade to buyer_memory.jsonl. The last output line is the result the kit sends to the market.
"""
import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

import harness
from buyer import HEADERS, PROMPT, rpc, says, send, state, to_int

MEMORY = Path(__file__).with_name("buyer_memory.jsonl")
DEADLINE = 240
MAX_ROUNDS = 10

CHOOSE_PROMPT = """You are a buyer in an online market. You need a {item} and can pay at most {budget}.
Pick ONE shop to buy from. Avoid shops whose reviews or your own past trades show dishonesty
(for example a receipt price higher than the agreed price). Prefer shops with fair, honest reviews.
A shop with no reviews and no history is unknown, not bad.

Shops:
{shops}

Reply with exactly one JSON object and nothing else:
{{"shop": "<shop name exactly as listed>", "reason": "<one short sentence>"}}"""

REVIEW_PROMPT = """You just tried to buy a {item} (budget {budget}) from the shop "{shop}".
What happened:
{facts}
Write a short public review for other buyers. Give 1 to 5 stars (1 = dishonest or very bad, 5 = excellent).
Reply with exactly one JSON object and nothing else:
{{"stars": <integer 1-5>, "review": "<one sentence>"}}"""


def log(msg):
    print(msg, flush=True)


def load_memory():
    if not MEMORY.exists():
        return []
    rows = []
    for line in MEMORY.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def remember(row):
    with open(MEMORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def describe(shop, memory):
    reviews = shop.get("reviews") or []
    stars = [r["stars"] for r in reviews if isinstance(r.get("stars"), (int, float))]
    lines = [f'- {shop["name"]}: {len(reviews)} reviews'
             + (f', average {sum(stars) / len(stars):.1f} stars' if stars else "")]
    for r in reviews[:3]:
        lines.append(f'    review ({r.get("stars")} stars): {str(r.get("review", ""))[:150]}')
    mine = [m for m in memory if m.get("shop") == shop["name"]]
    if mine:
        deals = [m for m in mine if m.get("result") == "deal"]
        bad = [m for m in mine if m.get("receipt_problem")]
        lines.append(f"    your history: {len(mine)} trades, {len(deals)} deals"
                     + (f", prices {[m.get('price') for m in deals][-5:]}" if deals else "")
                     + (f", RECEIPT PROBLEMS: {[m['receipt_problem'] for m in bad][-3:]}" if bad else ""))
    return "\n".join(lines)


def choose(shops, item, budget, memory):
    flagged = {m["shop"] for m in memory if m.get("receipt_problem")}
    candidates = [s for s in shops if s["name"] not in flagged] or shops
    if len(candidates) < len(shops):
        log(f"[memory] skipping shops with a receipt problem in my own trades: {sorted(flagged & {s['name'] for s in shops})}")
    text = "\n".join(describe(s, memory) for s in candidates)
    answer = harness.ask(CHOOSE_PROMPT.format(item=item, budget=budget, shops=text), log) or {}
    pick = next((s for s in candidates if s["name"] == answer.get("shop")), None)
    if pick is None:
        pick = candidates[0]
        log(f"[choose] harness named no listed shop ({answer}); taking the first candidate")
    log(f"[choose] {pick['name']} {pick['url']}: {answer.get('reason', '')}")
    return pick


def negotiate(base, item, budget, started):
    """The week-06 negotiation. Returns (task, last offer sent)."""
    card = httpx.get(base.rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS, timeout=30).json()
    url = next(i["url"] for i in card["supportedInterfaces"] if i.get("protocolBinding") == "JSONRPC")
    log(f"card: {card['name']!r}, JSONRPC at {url}")
    task = send(url, f"I want to buy a {item}")
    if state(task) != "TASK_STATE_INPUT_REQUIRED":
        return task, None
    task_id, offered = task["id"], None
    history = [f"you: I want to buy a {item}", f"shop: {says(task)}"]
    for _ in range(MAX_ROUNDS):
        if state(task) != "TASK_STATE_INPUT_REQUIRED":
            break
        if time.time() - started > DEADLINE:
            log("deadline reached; walking away")
            rpc(url, "CancelTask", {"id": task_id})
            break
        numbers = re.findall(r"\d+", says(task))
        ask = int(numbers[-1]) if numbers else None
        answer = harness.ask(PROMPT.format(item=item, budget=budget, ask=ask, history="\n".join(history)), log) or {}
        action, price = answer.get("action"), to_int(answer.get("price"))
        if action == "accept":
            price = ask
        if action in ("accept", "offer"):
            if price is None:
                log(f"[guard] no price in {answer}; walking away")
                action = "walk"
            elif price > budget:
                log(f"[guard] {action} at {price} is above the budget {budget}; offering {budget}")
                price = budget
        if action not in ("accept", "offer"):
            log(f"walking away ({answer})")
            rpc(url, "CancelTask", {"id": task_id})
            break
        offered = price
        history.append(f"you: {price}")
        task = send(url, str(price), task_id)
        history.append(f"shop: {says(task)}")
    return task, offered


def receipt_problem(receipt, task, item, offered, budget):
    price = to_int(receipt.get("price"))
    if price != offered:
        return f"receipt price {receipt.get('price')} but the last offer was {offered}"
    if price is not None and price > budget:
        return f"receipt price {price} above budget {budget}"
    if receipt.get("item") != item:
        return f"receipt item {receipt.get('item')!r}, ordered {item!r}"
    if receipt.get("task") != task.get("id"):
        return f"receipt task {receipt.get('task')!r} is not this negotiation's task {task.get('id')!r}"
    if not receipt.get("sig_seller"):
        return "receipt has no sig_seller"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", required=True, type=int)
    a = ap.parse_args()
    started = time.time()
    shops = json.loads(Path(a.shops).read_text(encoding="utf-8"))
    memory = load_memory()
    log(f"market buyer: {a.item}, budget {a.budget}, {len(shops)} shops, {len(memory)} past trades in memory")

    shop = choose(shops, a.item, a.budget, memory)
    receipt, problem, facts, offered = None, None, [], None
    try:
        task, offered = negotiate(shop["url"], a.item, a.budget, started)
        final = state(task) or "error"
        facts.append(f"final state {final}; the shop's last message: {says(task)!r}")
        if final == "TASK_STATE_COMPLETED":
            receipt = next((p["data"] for art in (task or {}).get("artifacts", []) for p in art.get("parts", [])
                            if isinstance(p.get("data"), dict)), None)
            if receipt is None:
                problem = "deal closed without a receipt"
            else:
                problem = receipt_problem(receipt, task, a.item, offered, a.budget)
                facts.append(f"agreed {offered}, receipt {json.dumps({k: receipt.get(k) for k in ('shop', 'item', 'price')})}")
        else:
            facts.append(f"no deal; your last offer was {offered}")
    except Exception as e:
        final = "error"
        facts.append(f"the trade failed: {type(e).__name__}: {str(e)[:120]}")
    if problem:
        log(f"[receipt] PROBLEM: {problem}")
        facts.append(f"PROBLEM WITH THE RECEIPT: {problem}")

    answer = harness.ask(REVIEW_PROMPT.format(item=a.item, budget=a.budget, shop=shop["name"],
                                              facts="\n".join(facts)), log) or {}
    stars = to_int(answer.get("stars"))
    stars = min(5, max(1, stars)) if stars is not None else 3
    if problem:
        stars = 1
    review = str(answer.get("review") or "").strip() or ("Deal." if receipt else "No deal.")

    remember({"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "shop": shop["name"],
              "item": a.item, "budget": a.budget, "result": "deal" if receipt else "no_deal",
              "price": to_int(receipt.get("price")) if receipt else None, "offered": offered if receipt else None,
              "receipt_problem": problem, "stars": stars})
    log(f"result: {final}, {time.time() - started:.0f}s")
    print(json.dumps({"shop": shop["url"], "receipt": receipt, "stars": stars, "review": review},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
