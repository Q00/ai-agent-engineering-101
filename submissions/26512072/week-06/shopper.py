"""Challenge 2: the buyer with a directory of shops, a shopping list, and one total budget.

  python -u shopper.py --directory logs/challenge-2/directory.json --list shopping_list.json --budget 1500

1. Reads every Agent Card in the directory (A2A-Version: 1.0 on every request).
2. Plan (harness): from one line per shop (name + what its skill says it sells), pick two shops per item.
3. Quote: opens a task with both shops for every item at once and reads the opening asks.
4. Allocate (harness): picks one shop per item and a price cap per item; the other task is canceled.
   Code check: the caps never add up to more than the budget.
5. Negotiate all items at once, each on its own task, offers capped by the item's cap (code guard).
6. Retry: items without a deal get the leftover budget and their other shop (harness re-allocates).

State: the harness never sees a transcript. Each negotiation is carried as a one-line summary
(item, shop, quoted item, asks, offers, status), and each step's prompt holds only that line.
"""
import argparse
import asyncio
import json

import harness
from catalog import _tokens
from buyer import A2AClient, negotiate, state, text_of, last_number

SYSTEM = ("You are a purchasing agent that buys a shopping list from many shops with one total budget. "
          "Answer with exactly one JSON object and nothing else.")
PLAN = ("Shopping list:\n{items}\n\nShops (id | name | what the shop says it sells):\n{shops}\n\n"
        "For every item, pick the two shops most likely to sell it.\n"
        'JSON: {{"choices": {{"<item number>": ["<shop id>", "<shop id>"], ...}}}}')
ALLOC = ("Total budget for everything: {budget}. Already spent: {spent}.\n"
         "Open quotes (item | shop | the item the shop offers | its current ask):\n{board}\n\n"
         "For every item number listed, choose exactly one shop from its quotes and a price cap: the most you "
         "will pay for that item. Every item must be bought, so the caps must add up to at most {left}, and "
         "should use most of it: money left unused while an item is not bought is wasted. "
         "Shops usually come down 10 to 25 percent from their opening ask, so a cap below the ask is normal.\n"
         'JSON: {{"items": {{"<item number>": {{"shop": "<shop id>", "cap": <whole number>}}, ...}}}}')


def log(msg):
    print(msg, flush=True)


def summary(n):
    """The one-line state of a negotiation that the harness and the log see."""
    return (f"item {n['item']} | {n['shop']} | {n.get('quoted') or '?'} | asks {n.get('asks', [])} | "
            f"offers {n.get('offers', [])} | {n.get('status', 'open')}" + (f" at {n['price']}" if n.get("price") else ""))


async def quote(shop, item_no, desc):
    t = await shop["client"].send(f"I want to buy {desc}")
    q = {"item": item_no, "shop": shop["id"], "task": t, "status": "open" if state(t) == "TASK_STATE_INPUT_REQUIRED" else "no item"}
    q["ask"] = last_number(text_of(t)) if q["status"] == "open" else None
    q["quoted"] = text_of(t).split(":")[0][:60] if q["status"] == "open" else None
    q["asks"] = [q["ask"]] if q["ask"] else []
    log(f"[quote] item {item_no} @ {shop['id']} ({shop['name']}): {state(t)} | {text_of(t)}")
    return q


def fit_caps(choice, quotes, left):
    """Code check on the harness's allocation: a quoted shop per item, caps <= ask, sum <= left."""
    out = {}
    for item_no, qs in quotes.items():
        c = choice.get(str(item_no)) or {}
        q = next((q for q in qs if q["shop"] == c.get("shop")), None) or min(qs, key=lambda q: q["ask"])
        cap = c.get("cap") if isinstance(c.get("cap"), (int, float)) else q["ask"]
        out[item_no] = [q, int(min(cap, q["ask"]))]
    total = sum(cap for _, cap in out.values())
    if total > left:  # scale down so the caps fit the budget
        log(f"[guard] caps add up to {total} > {left}: scaled down")
        for v in out.values():
            v[1] = int(v[1] * left / total)
    elif total < left:  # spread the unused budget, but never above a shop's ask
        room = {k: q["ask"] - cap for k, (q, cap) in out.items()}
        spare = left - total
        if sum(room.values()) > 0:
            for k, v in out.items():
                v[1] += min(room[k], int(spare * room[k] / sum(room.values())))
            log(f"[guard] caps added up to {total} < {left}: unused budget spread up to the asks "
                f"-> {sum(v[1] for v in out.values())}")
    return out


def shops_for(desc, shops, skip):
    """Shops whose card names a kind of item that matches the description, longest match first."""
    want = set(_tokens(desc))
    ranked = []
    for s in shops:
        if s["id"] in skip:
            continue
        best = max((len(p.split()) for p in s["sells"].split(", ") if set(_tokens(p)) <= want), default=0)
        if best:
            ranked.append((-best, s["id"], s))
    return [s for _, _, s in sorted(ranked)]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--directory", required=True)
    ap.add_argument("--list", required=True)
    ap.add_argument("--budget", type=int, required=True)
    args = ap.parse_args()
    urls = json.load(open(args.directory))
    wanted = json.load(open(args.list))
    log(f"[shopper] {len(urls)} shops, {len(wanted)} items, budget {args.budget}, harness: {harness.NAME}")

    shops = []
    for k, url in enumerate(urls, 1):
        c = A2AClient(url)
        card = await c.card()
        shops.append({"id": f"S{k:02d}", "name": card["name"], "client": c,
                      "sells": card["description"].removeprefix("Sells ").split(". ")[0]})
    by_id = {s["id"]: s for s in shops}
    shop_lines = "\n".join(f"{s['id']} | {s['name']} | {s['sells']}" for s in shops)
    item_lines = "\n".join(f"{k}. {d}" for k, d in enumerate(wanted, 1))

    plan = (await harness.decide(SYSTEM, PLAN.format(items=item_lines, shops=shop_lines), log, "plan")).get("choices", {})
    picks = {k: [by_id[s] for s in plan.get(str(k), []) if s in by_id][:2] for k in range(1, len(wanted) + 1)}
    log("[plan] " + "; ".join(f"item {k}: {[s['id'] for s in v]}" for k, v in picks.items()))

    bought, spent, tried = {}, 0, {k: set() for k in picks}
    for rnd in (1, 2, 3):
        todo = [k for k in picks if k not in bought]
        if not todo:
            break
        # quote: every remaining item at its (untried) shops, all at once
        jobs = [(k, s) for k in todo for s in picks[k] if s["id"] not in tried[k]]
        if rnd > 1:  # later rounds: untried shops whose card names this kind of item
            jobs = [(k, s) for k in todo for s in shops_for(wanted[k - 1], shops, tried[k])[:2]]
        got = await asyncio.gather(*(quote(s, k, wanted[k - 1]) for k, s in jobs))
        quotes = {}
        for q in got:
            tried[q["item"]].add(q["shop"])
            if q["status"] == "open" and q["ask"]:
                quotes.setdefault(q["item"], []).append(q)
        if not quotes:
            log(f"[round {rnd}] no open quotes")
            continue
        left = args.budget - spent
        board = "\n".join(f"{q['item']} | {q['shop']} | {q['quoted']} | {q['ask']}" for qs in quotes.values() for q in qs)
        alloc = (await harness.decide(SYSTEM, ALLOC.format(budget=args.budget, spent=spent, board=board, left=left),
                                      log, f"allocate round {rnd}")).get("items", {})
        chosen = fit_caps(alloc, quotes, left)
        for item_no, qs in quotes.items():  # cancel the quotes we do not follow up
            for q in qs:
                if q is not chosen[item_no][0]:
                    await by_id[q["shop"]]["client"].cancel(q["task"]["id"])
                    log(f"[cancel] item {item_no} @ {q['shop']}")
        log(f"[round {rnd}] caps: " + "; ".join(f"item {k} @ {q['shop']} cap {cap}" for k, (q, cap) in chosen.items()))

        async def run(item_no, q, cap):
            res = await negotiate(by_id[q["shop"]]["client"], wanted[item_no - 1], cap, wanted[item_no - 1],
                                  log, f"item {item_no} @ {q['shop']}", task=q["task"])
            q.update(res)
            return item_no, q
        for item_no, q in await asyncio.gather(*(run(k, q, cap) for k, (q, cap) in chosen.items())):
            log(f"[state] {summary(q)}")
            if q["status"] == "deal" and item_no not in bought:
                bought[item_no] = q
                spent += q["price"]

    total = sum(q["price"] for q in bought.values())
    ok = len(bought) == len(wanted) and total <= args.budget
    result = {"items": len(wanted), "bought": len(bought), "total": total, "budget": args.budget,
              "duplicates": 0, "pass": ok,
              "purchases": {k: {"shop": q["shop"], "seller_item": (q.get("receipt") or {}).get("item"),
                                "price": q["price"]} for k, q in sorted(bought.items())}}
    log(f"[result] {json.dumps(result)}")
    for s in shops:
        await s["client"].close()
    return result


if __name__ == "__main__":
    asyncio.run(main())
