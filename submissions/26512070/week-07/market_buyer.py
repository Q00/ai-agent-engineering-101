"""The market buyer (LAB 1): pick a shop from {shops}, trade with it, remember, review.

    py market_buyer.py --shops .market/shops.json --item lamp --budget 120

Who decides what:
- haiku picks the shop (one react.run turn). It sees, for every shop, the market's
  reviews and this buyer's own record with that shop, written out plainly.
- code keeps the record (memory.jsonl, one line per order) and enforces two rules:
  never offer more than the budget (buyer.negotiate's wallet and guard), and never
  trade again with a shop that cheated us once (it is left out of haiku's list).
- code writes the stars and the review from the facts of the trade, so other
  buyers read what happened (agreed price vs. receipt price), not an opinion.

"Cheated" means: the receipt's price is above the price we agreed, the receipt names
another item, or the receipt is above the budget. A missing receipt or a missing
sig_seller is recorded and shown to haiku, but does not block the shop.

Shops are remembered by name: a shop's URL changes each time it restarts its kit.
The last line on stdout is the result the kit sends to the market.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

import buyer
import react
from model import log

HERE = os.path.dirname(os.path.abspath(__file__))
MEMORY = os.path.join(HERE, "memory.jsonl")
REVIEWS_SHOWN = 5  # newest reviews per shop shown to haiku (the market sends up to 10)

SYSTEM = """You are a shopping agent choosing which shop to buy from in an open market.
Some shops are dishonest: for example, the receipt they issue says a higher price than the one agreed.
You get, for every shop, reviews other buyers wrote and your own record of past trades with it.

How to choose:
- Avoid shops whose reviews or your own record show receipts that do not match the agreed price,
  wrong items, or other cheating. Your own record counts more than strangers' reviews.
- Among the rest, prefer shops where you (or reviewers) bought this item cheaply and the receipt matched.
- A shop with no reviews and no record is unknown: better than a shop with bad reviews,
  worse than a shop with a good honest record.
Pick exactly one shop by its name."""


# ---- memory: one JSON line per order -------------------------------------------------------

def load_memory():
    if not os.path.exists(MEMORY):
        return []
    out = []
    with open(MEMORY, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def remember(entry):
    with open(MEMORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def blocked(memory):
    """{shop name: reason} for every shop that cheated us at least once. Permanent."""
    out = {}
    for m in memory:
        if m.get("verdict", "").startswith("cheated"):
            out.setdefault(m["shop"], f'{m["verdict"]} ({m["item"]}, {m["time"]})')
    return out


def record_text(name, memory):
    """This buyer's own record with one shop, one line per trade, newest first."""
    mine = [m for m in memory if m.get("shop") == name]
    if not mine:
        return "  your record: never traded with this shop"
    deals = [m for m in mine if m.get("receipt_price") is not None]
    lines = [f"  your record: {len(mine)} order(s), {len(deals)} deal(s)"]
    for m in reversed(mine[-5:]):
        if m.get("receipt_price") is None:
            lines.append(f"    - {m['item']}, budget {m['budget']}: no deal ({m['verdict']})")
        else:
            lines.append(f"    - {m['item']}, budget {m['budget']}: agreed {m['agreed']}, receipt {m['receipt_price']}, "
                         f"{round(100 * m['receipt_price'] / m['budget'])}% of budget, {m['verdict']}")
    return "\n".join(lines)


def reviews_text(reviews):
    if not reviews:
        return "  market reviews: none"
    stars = [r.get("stars") for r in reviews if isinstance(r.get("stars"), (int, float))]
    avg = f", average {sum(stars) / len(stars):.1f} stars" if stars else ""
    lines = [f"  market reviews: {len(reviews)}{avg}; newest first:"]
    for r in reviews[:REVIEWS_SHOWN]:
        lines.append(f"    - {r.get('stars')}* {str(r.get('review', ''))[:160]!r}")
    return "\n".join(lines)


# ---- choosing a shop -----------------------------------------------------------------------

def choose(shops, item, budget, memory):
    """Return (shop dict, reason). haiku picks among the shops that never cheated us."""
    bad = blocked(memory)
    for s in shops:
        if s["name"] in bad:
            log(f"[choose] blocked {s['name']!r}: cheated us before: {bad[s['name']]}")
    allowed = [s for s in shops if s["name"] not in bad]
    if not allowed:
        raise RuntimeError("every shop in the list has cheated us before")
    if len(allowed) == 1:
        return allowed[0], f"the only shop that never cheated us"

    by_name = {s["name"]: s for s in allowed}
    situation = "\n".join(
        [f"You want to buy one {item}; your budget is {budget}. Shops you may choose from:"]
        + [f"\n* {s['name']}\n{reviews_text(s.get('reviews') or [])}\n{record_text(s['name'], memory)}"
           for s in allowed])
    finals = {"pick": {"args": "(shop)", "description": "buy from this shop (its exact name)"}}

    def validate(action, args):
        return (True, "ok") if args.get("shop") in by_name else \
            (False, f"{args.get('shop')!r} is not one of {list(by_name)}")

    d, _ = react.run(SYSTEM, situation, {}, finals, validate, max_steps=3, deadline=time.time() + 90)
    if d:
        return by_name[d["shop"]], d.get("thought") or "(haiku gave no reason)"
    # haiku gave no valid pick: fall back to the best average stars, unknown shops last
    def score(s):
        st = [r["stars"] for r in s.get("reviews") or [] if isinstance(r.get("stars"), (int, float))]
        return sum(st) / len(st) if st else 0
    s = max(allowed, key=score)
    log(f"GUARD: haiku gave no valid pick; falling back to the best average stars -> {s['name']!r}")
    return s, "fallback: best average stars (haiku gave no valid pick)"


# ---- judging the trade ---------------------------------------------------------------------

def judge(res, item, budget):
    """(verdict, stars, review) from the facts of the trade."""
    r = res["receipt"]
    if r is None:
        if res["final_state"] == "TASK_STATE_COMPLETED":
            return "no receipt on a completed deal", 2, "Said deal, but sent no receipt."
        ask = res["last_ask"]
        if ask is not None and ask > budget:
            return "no deal (ask above budget)", 3, f"No deal: lowest ask {ask} stayed above my budget {budget}."
        return "no deal", 3, f"No deal for the {item} (task ended {res['final_state']})."
    price = buyer.last_number(str(r.get("price")))
    agreed = res["last_offer"]
    if price is None:
        return "cheated: receipt has no price", 1, "Receipt has no readable price."
    if r.get("item") not in (None, item):
        return f"cheated: receipt names {r.get('item')!r}, ordered {item!r}", 1, \
            f"Ordered {item}, receipt says {r.get('item')}."
    if agreed is not None and price > agreed:
        return f"cheated: agreed {agreed}, receipt {price}", 1, f"Agreed {agreed}, receipt says {price}."
    if price > budget:
        return f"cheated: receipt {price} above budget {budget}", 1, f"Receipt {price} is above my budget {budget}."
    if "sig_seller" not in r:
        return "fair but unsigned", 3, f"{item} at {price}, receipt matched but was not signed."
    stars = 5 if price <= 0.9 * budget else 4
    return "fair", stars, f"{item.capitalize()} at {price}, receipt matched the agreed price."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=float, required=True)
    args = ap.parse_args()
    budget = int(args.budget) if args.budget.is_integer() else args.budget
    t0 = time.time()

    with open(args.shops, encoding="utf-8") as f:
        shops = json.load(f)
    memory = load_memory()
    log(f"[order] {args.item}, budget {budget}; {len(shops)} shop(s); {len(memory)} remembered order(s)")

    shop, why = choose(shops, args.item, budget, memory)
    log(f"[choose] {shop['url']}: {shop['name']}: {why}")

    try:
        res = buyer.negotiate(shop["url"], args.item, budget, t0)
    except Exception as e:
        log(f"negotiation failed: {e!r}")
        res = {"final_state": f"error: {type(e).__name__}", "receipt": None, "offers": [],
               "last_offer": None, "last_ask": None, "seller_said": "", "guards": []}

    verdict, stars, review = judge(res, args.item, budget)
    r = res["receipt"]
    remember({"time": datetime.now().isoformat(timespec="seconds"), "shop": shop["name"], "item": args.item,
              "budget": budget, "offers": res["offers"], "agreed": res["last_offer"] if r else None,
              "receipt_price": buyer.last_number(str(r.get("price"))) if r else None,
              "receipt_item": r.get("item") if r else None, "signed": bool(r and "sig_seller" in r),
              "final_state": res["final_state"], "verdict": verdict, "stars": stars, "review": review,
              "why_picked": why})
    log(f"[memory] {shop['name']}: {verdict}")
    print(json.dumps({"shop": shop["url"], "receipt": r, "stars": stars, "review": review}, ensure_ascii=False),
          flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"market buyer crashed: {e!r}")
        sys.exit(1)
