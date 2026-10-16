"""Challenge 2 buyer: a shopping list, one total budget, a directory of about 20 A2A shops.

    python shop.py --directory directory.json --list list.json --budget 900 --out result.json

directory.json is a list of shop base URLs; list.json is a list of item descriptions.

1. Directory (the DF). Every Agent Card is read once and boiled down to one line per shop
   ("S03 | Seat Society | Sells: armchair, dining chair, ..."). The harness never sees raw cards.
2. Plan. The harness gets those lines, the list and the budget, and returns for each item a ranked
   list of shops and a spending cap. The code checks the plan: known shops only, every item once,
   and the caps add up to at most the budget (scaled down if not).
3. Negotiate. All items are negotiated at the same time, one thread per item, each at one shop at a
   time. A negotiation carries only its own compacted history (harness.compact) plus one ledger line
   shared by all ("bought: ...; still buying: ...; budget left ...") so the prompt does not grow
   with the number of shops. The moves come from buyer.next_move, with the item's cap as its budget.
4. Retry. An item not bought (shop rejected, held by someone else, too expensive, wrong item) moves to
   its next shop. After a pass, money left over from bought items is spread over the items still open
   and their caps go up; the next pass goes back first to the shops that quoted the item, cheapest
   last ask first. At most PASSES passes of SHOPS_PER_PASS shops each.

What the code guarantees, whatever the harness says: an offer never exceeds the item's cap; caps of
open items plus what was spent never exceed the budget, so the total stays within budget; an item is
negotiated by one thread only and the thread stops at the first deal, so nothing is bought twice.
"""
import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import harness
from buyer import RpcError, Seller, next_move, read_ask, receipt, state
from harness import as_int, decide, log

PASSES = 3
SHOPS_PER_PASS = 4
MAX_ROUNDS = 8
WORKERS = 4

PLAN = ("You are a buyer with one total budget of {budget} for this shopping list:\n{items}\n\n"
        "These shops are available (id | name | what they sell):\n{shops}\n\n"
        "For each list item choose up to 4 shops that are most likely to sell it, best first, and a spending "
        "cap so that all caps together are at most {budget}. Expensive items need bigger caps.\n"
        'Reply with only one JSON object: {{"plan": [{{"item": <item number>, "shops": ["S01", ...], '
        '"cap": <whole number>}}, ...]}}')


class Ledger:
    """Shared state of the whole shopping trip. All money checks go through here, under one lock."""

    def __init__(self, items, budget):
        self.items, self.budget = items, budget
        self.lock = threading.Lock()
        self.bought = {}        # item index -> purchase dict
        self.caps = {}          # item index -> current cap

    def spent(self):
        return sum(p["price"] for p in self.bought.values())

    def line(self, me):
        with self.lock:
            done = "; ".join(f"#{i + 1} for {p['price']} at {p['shop']}" for i, p in sorted(self.bought.items())) or "nothing yet"
            todo = ", ".join(f"#{i + 1}" for i in range(len(self.items)) if i not in self.bought and i != me) or "none"
            return (f"Shopping trip so far: bought {done}; other items still open: {todo}; total budget "
                    f"{self.budget}, spent {self.spent()}. Your cap for this item is {self.caps[me]}.")

    def record(self, i, purchase):
        with self.lock:
            if i in self.bought:
                raise RuntimeError(f"item {i + 1} bought twice")   # cannot happen: one thread per item
            self.bought[i] = purchase
            assert self.spent() <= self.budget, "budget exceeded"

    def rebalance(self, open_items):
        """Spread the money not spent and not committed over the open items."""
        with self.lock:
            free = self.budget - self.spent() - sum(self.caps[i] for i in open_items)
            for i in open_items:
                self.caps[i] += free // max(1, len(open_items))
            assert self.spent() + sum(self.caps[i] for i in open_items) <= self.budget


def load_shops(urls):
    def one(n_url):
        n, url = n_url
        try:
            s = Seller(url, who="shop")
            skills = " ".join(sk.get("description", "") for sk in s.card.get("skills", []))
            sells = skills.split("Sells:")[-1].split(".")[0].strip() if "Sells:" in skills else skills[:120]
            name = s.card.get("name") or url
            return {"sid": f"S{n:02d}", "url": url, "client": s, "name": name,
                    "line": f"S{n:02d} | {name} | {(sells or s.card.get('description', ''))[:160]}"}
        except Exception as e:
            log("shop", f"card at {url} unreadable: {e!r}")
            return None
    with ThreadPoolExecutor(8) as pool:
        return [s for s in pool.map(one, enumerate(urls, start=1)) if s]


def make_plan(items, shops, budget):
    answer = decide("shop", PLAN.format(budget=budget, items="\n".join(f"{n}. {it}" for n, it in enumerate(items, 1)),
                                        shops="\n".join(s["line"] for s in shops))) or {}
    ids = {s["sid"] for s in shops}
    plan = {}
    for row in answer.get("plan", []) if isinstance(answer.get("plan"), list) else []:
        i = as_int(row.get("item"))
        if i is None or not 1 <= i <= len(items) or i - 1 in plan:
            continue
        ranked = [sid for sid in row.get("shops", []) if sid in ids][:4]
        plan[i - 1] = {"shops": ranked, "cap": max(1, as_int(row.get("cap")) or 0)}
    for i in range(len(items)):                                  # anything the harness left out
        plan.setdefault(i, {"shops": [], "cap": 0})
        if not plan[i]["cap"]:
            plan[i]["cap"] = budget // len(items)
    total = sum(p["cap"] for p in plan.values())
    if total > budget:
        log("shop", f"plan caps add up to {total} > budget {budget}; code scales them down")
        for p in plan.values():
            p["cap"] = p["cap"] * budget // total
    for i, p in plan.items():                                    # fallback order: every other shop
        p["shops"] += [s["sid"] for s in shops if s["sid"] not in p["shops"]]
    return plan


def negotiate_at(shop, i, item, ledger, who, seen=None):
    """One negotiation for list item i at one shop. Returns a purchase dict or None.

    `seen` collects the lowest ask this shop made for the item, so a later pass knows where to go first.
    """
    seller = shop["client"]
    t = seller.send(f"I want to buy {item}")
    tid, ctx, history = t.get("id"), t.get("contextId"), []
    for _ in range(MAX_ROUNDS):
        if state(t) not in ("TASK_STATE_INPUT_REQUIRED", "TASK_STATE_UNKNOWN"):
            break
        if read_ask(t)[1] == "none":
            log(who, "no asking price (unclear item at this shop); moving on")
            break
        action, price, ask = next_move(who, item, ledger.caps[i], history, t, want=item, ledger=ledger.line(i))
        if seen is not None and ask is not None:
            seen[shop["sid"]] = min(ask, seen.get(shop["sid"], ask))
        if action == "walk":
            break
        try:
            t = seller.send(str(price), tid, ctx)
        except RpcError as e:
            log(who, f"{shop['sid']} error: {e}; moving on")
            break
        tid, ctx = t.get("id") or tid, t.get("contextId") or ctx
    if state(t) == "TASK_STATE_COMPLETED":
        r = receipt(t)
        offered = history[-1][1] if history and history[-1][0] == "you offered" else None
        if as_int(r.get("price")) is None or (offered is not None and as_int(r["price"]) > offered):
            # The deal happened, so it must count (or the item could be bought twice), but at the
            # price we agreed to; the seller's odd receipt is kept next to it.
            log(who, f"{shop['sid']} receipt {r!r} does not match our last offer {offered}; recording {offered}")
            r = {**r, "seller_receipt_price": r.get("price"), "price": offered}
        return {"item": item, "shop": shop["sid"], "shop_name": shop["name"], "url": shop["url"],
                "task_id": tid, "price": r.get("price"), "seller_item": r.get("item"), "item_id": r.get("item_id")}
    if state(t) in ("TASK_STATE_INPUT_REQUIRED", "TASK_STATE_UNKNOWN"):
        seller.cancel(tid)
        log(who, f"walked away from {shop['sid']}" + (f", canceled task {tid[:8]}" if tid else ""))
    else:
        log(who, f"{shop['sid']} ended in {state(t)}")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--directory", required=True)
    ap.add_argument("--list", required=True)
    ap.add_argument("--budget", type=int, required=True)
    ap.add_argument("--out", default="result.json")
    ap.add_argument("--cap-scale", type=float, default=1.0,
                    help="test only: scale the planned caps down to force the retry path")
    a = ap.parse_args()
    start = time.time()
    items = json.load(open(a.list, encoding="utf-8"))
    shops = load_shops(json.load(open(a.directory, encoding="utf-8")))
    by_sid = {s["sid"]: s for s in shops}
    log("shop", f"harness: {harness.HARNESS_CMD}; {len(items)} items, budget {a.budget}, {len(shops)} shops")
    for s in shops:
        log("shop", f"directory: {s['line']}")

    plan = make_plan(items, shops, a.budget)
    ledger = Ledger(items, a.budget)
    tried = {i: [] for i in range(len(items))}
    asks = {i: {} for i in range(len(items))}      # item -> {shop: lowest ask seen}
    for i, p in plan.items():
        ledger.caps[i] = int(p["cap"] * a.cap_scale)
        log("shop", f"plan: #{i + 1} {items[i]!r}: cap {p['cap']}, shops {p['shops'][:4]} ...")

    def order(i):
        """Shops to try for item i in this pass, SHOPS_PER_PASS of them.

        Pass 1: the plan's order. Later passes (the cap has gone up): shops that quoted the item, cheapest
        last ask first, then the plan's order. Attempt 2 of challenge 2 skipped every shop already
        tried, so with a higher cap it went on to a lighting shop instead of the bookshelf shops.
        """
        quoted = sorted(asks[i], key=asks[i].get)
        return (quoted + [sid for sid in plan[i]["shops"] if sid not in quoted])[:SHOPS_PER_PASS]

    def work(i):
        who = f"shop#{i + 1}"
        shops_now = order(i)
        log(who, f"pass {pass_no + 1}: cap {ledger.caps[i]}, shops {shops_now}"
                 + (f", last asks {asks[i]}" if asks[i] else ""))
        for sid in shops_now:
            tried[i].append(sid)
            try:
                got = negotiate_at(by_sid[sid], i, items[i], ledger, who, asks[i])
            except Exception as e:
                log(who, f"{sid} failed: {e!r}")
                continue
            if got:
                ledger.record(i, got)
                log(who, f"BOUGHT at {sid}: {got['seller_item']!r} for {got['price']} (cap {ledger.caps[i]})")
                return

    for pass_no in range(PASSES):
        open_items = [i for i in range(len(items)) if i not in ledger.bought]
        if not open_items:
            break
        if pass_no:
            ledger.rebalance(open_items)
            log("shop", f"pass {pass_no + 1}: open {[i + 1 for i in open_items]}, caps {[ledger.caps[i] for i in open_items]}")
        with ThreadPoolExecutor(WORKERS) as pool:
            list(pool.map(work, open_items))

    result = {"budget": a.budget, "spent": ledger.spent(), "items": items,
              "purchases": [ledger.bought[i] for i in sorted(ledger.bought)],
              "missing": [items[i] for i in range(len(items)) if i not in ledger.bought],
              "seconds": round(time.time() - start), "harness_calls": harness.CALLS["n"]}
    json.dump(result, open(a.out, "w", encoding="utf-8"), indent=1)
    log("shop", f"done: bought {len(result['purchases'])}/{len(items)}, spent {result['spent']}/{a.budget}, "
                f"{result['seconds']} s, harness calls {result['harness_calls']}")


if __name__ == "__main__":
    main()
