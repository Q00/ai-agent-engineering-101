"""Challenge 2: a harness-driven buyer that shops a shopping list across many A2A shops.

    python buyer_multi.py --directory dir.json --list list.json --budget 750 --out ledger.json

dir.json: {"shops": ["http://127.0.0.1:9101", ...]}   list.json: ["a dimmable desk lamp", ...]

Phases (each harness call sees a summary, never a raw transcript):
  1. DF      read every Agent Card; one line per shop from its skills
  2. PLAN    harness: shortlist <= 3 shops per item from those lines
  3. QUOTE   ask every shortlisted shop at once; keep (shop, product, opening ask) per task
  4. ALLOT   harness: one shop per item and a price cap per item; code makes the caps fit the budget
             and cancels every quote task that was not chosen
  5. HAGGLE  one thread per item, all at once; each round the harness sees one state line
  6. REPLAN  items that failed go back to 3-5 with the money that is left. A shop whose asks stopped
             falling (its floor showed) stays a candidate with "floor seen N"; other failed shops are dropped

Enforced in code (the harness is told too): the Ledger. A cap is reserved before a
negotiation starts, the sum of reservations never exceeds the budget, no offer goes
above its cap, an item is bought at most once, and a cap is released on failure.
"""
import argparse
import json
import math
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from buyer import A2A, last_number, state, text_of
from harness import ask_json, log
import harness

MAX_ROUNDS = 6
REPLANS = 2

PLAN_PROMPT = """You are a buyer with a shopping list and a directory of shops (from their Agent Cards).
Shopping list:
{items}
Directory (id | shop | what it sells):
{shops}
For every list item choose up to 3 shops that sell that kind of item.
Answer with JSON only: {{"plan": [{{"item": <list number>, "shops": ["S01", ...]}}, ...]}}"""

ALLOT_PROMPT = """You are a buyer. Total budget left: {budget}. You must buy every item below exactly once, within the budget.
Quotes (opening asks; shops usually come down 10-30% when you negotiate. "floor seen N" means that shop's asks
stopped falling at N in an earlier negotiation, so it will most likely sell at N but not below):
{quotes}
For each item choose one quote and a price cap (the most you will pay for it). The caps must add up to at most {budget}.
Prefer cheap quotes. A cap below a seen floor will fail again; give such a quote a cap of at least its floor when the
budget allows, and take the money from items with more slack.
Answer with JSON only: {{"choice": [{{"item": <item number>, "quote": "<quote id>", "cap": <integer>}}, ...]}}"""

HAGGLE_PROMPT = """You are a buyer negotiating with one shop. State of this negotiation:
{line}
Rules: never pay more than your cap ({cap}). Buy if the shop comes down to your cap or less; walk away if it clearly will not.
The first ask is an opening price: shops usually take 10-30% less, so do not accept it in round 1; make a lower offer.
Shops may end the talk after a few low offers, so move toward your cap.
Decide one of: "accept" (pay the shop's current ask), "offer" (your integer price), "walk".
Answer with JSON only: {{"action": "accept"|"offer"|"walk", "price": <integer>}}"""


class Ledger:
    """Budget and purchases. Every money decision passes through here."""

    def __init__(self, budget, n_items):
        self.budget, self.lock = budget, threading.Lock()
        self.reserved = {}            # item -> cap held for an open negotiation
        self.bought = {}              # item -> {"shop", "sku", "price", "task"}
        self.n_items = n_items

    def free(self):
        return self.budget - sum(self.reserved.values()) - sum(b["price"] for b in self.bought.values())

    def reserve(self, item, cap):
        with self.lock:
            if item in self.bought or item in self.reserved:
                raise ValueError(f"item {item} already bought or in negotiation")
            cap = min(cap, self.free())
            self.reserved[item] = cap
            return cap

    def release(self, item):
        with self.lock:
            self.reserved.pop(item, None)

    def commit(self, item, rec):
        with self.lock:
            if item in self.bought:
                raise ValueError(f"item {item} bought twice")
            cap = self.reserved.pop(item)
            if rec["price"] > cap:
                raise ValueError(f"item {item} bought at {rec['price']} above cap {cap}")
            self.bought[item] = rec


def ask_of(t):
    """The shop's current ask: metadata "ask" when the shop sends it, else the last number."""
    m = (t or {}).get("status", {}).get("message", {}).get("metadata") or {}
    return m.get("ask") if isinstance(m.get("ask"), (int, float)) else last_number(text_of(t))


def product_of(t):
    m = re.search(r"(?:^|\. |! )([^.]*?): asking", text_of(t))
    sku = ((t or {}).get("status", {}).get("message", {}).get("metadata") or {}).get("sku")
    return (m.group(1).strip() if m else "?"), sku


def receipt_of(t):
    for a in (t or {}).get("artifacts", []):
        for p in a.get("parts", []):
            if isinstance(p.get("data"), dict) and "price" in p["data"]:
                return p["data"]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--directory", required=True)
    ap.add_argument("--list", required=True)
    ap.add_argument("--budget", type=int, required=True)
    ap.add_argument("--out", default="ledger.json")
    args = ap.parse_args()
    t0 = time.time()
    urls = json.load(open(args.directory))["shops"]
    items = json.load(open(args.list))
    ledger = Ledger(args.budget, len(items))
    pool = ThreadPoolExecutor(max_workers=16)

    # 1. DF: one line per shop from its card
    def read_card(url):
        try:
            return A2A(url)
        except Exception as e:   # a dead shop is skipped, not fatal
            log(f"[df] {url}: {e}")
            return None
    clients = list(pool.map(read_card, urls))
    shops = {}
    for i, c in enumerate(clients):
        if c is None:
            continue
        card = c.http.get(urls[i].rstrip("/") + "/.well-known/agent-card.json").json()
        sells = "; ".join(s["description"] for s in card.get("skills", []) if s.get("id") != "negotiate")
        shops[f"S{i + 1:02d}"] = {"client": c, "name": card["name"], "line": f"{card['name']} | sells: {sells}"}
    log(f"[df] {len(shops)} shops read")
    for sid, s in shops.items():
        log(f"[df] {sid} | {s['line'][:160]}")

    # 2. PLAN
    data, _ = ask_json(PLAN_PROMPT.format(items="\n".join(f"{i + 1}. {x}" for i, x in enumerate(items)),
                                          shops="\n".join(f"{k} | {v['line']}" for k, v in shops.items())), "plan")
    shortlist = {i: [] for i in range(len(items))}
    for p in (data or {}).get("plan", []):
        try:
            i = int(p["item"]) - 1
        except (KeyError, TypeError, ValueError):
            continue
        if i in shortlist:
            shortlist[i] = [s for s in p.get("shops", []) if s in shops][:3]
    log(f"[plan] {json.dumps({items[i]: v for i, v in shortlist.items()})}")

    failed_at = {i: set() for i in range(len(items))}
    floors = {}   # (item, shop) -> the ask the shop stopped at in a failed negotiation
    notes = []
    for rnd in range(REPLANS + 1):
        todo = [i for i in range(len(items)) if i not in ledger.bought]
        if not todo:
            break
        log(f"\n[round {rnd}] to buy: {[items[i] for i in todo]}, free budget {ledger.free()}")

        # 3. QUOTE: every shortlisted shop for every open item, all at once
        def quote(i, sid):
            c = shops[sid]["client"]
            t = c.send(items[i])
            prod, sku = product_of(t)
            return {"item": i, "shop": sid, "client": c, "task": t, "ask": ask_of(t), "product": prod, "sku": sku}
        jobs = [(i, s) for i in todo for s in shortlist[i] if s not in failed_at[i]]
        if not jobs:
            log("[quote] no shop left to ask; stopping")
            break
        quotes = [q for q in pool.map(lambda a: quote(*a), jobs)]
        open_q = [q for q in quotes if state(q["task"]) == "TASK_STATE_INPUT_REQUIRED" and q["ask"] is not None]
        for q in quotes:
            if q not in open_q:
                failed_at[q["item"]].add(q["shop"])
                log(f"[quote] {q['shop']} cannot sell {items[q['item']]!r}: {state(q['task'])} {text_of(q['task'])[:80]}")
        for k, q in enumerate(open_q):
            q["id"] = f"Q{k + 1}"
        table = "\n".join(f"{q['id']} | item {q['item'] + 1} ({items[q['item']]}) | {shops[q['shop']]['name']} "
                          f"| {q['product']} | ask {q['ask']}"
                          + (f" | floor seen {floors[q['item'], q['shop']]}" if (q['item'], q['shop']) in floors else "")
                          for q in open_q)
        log("[quote] table\n" + table)

        # 4. ALLOT
        data, _ = ask_json(ALLOT_PROMPT.format(budget=ledger.free(), quotes=table
                                               + ("\nNotes: " + "; ".join(notes) if notes else "")), "allot")
        by_id = {q["id"]: q for q in open_q}
        chosen = {}
        for ch in (data or {}).get("choice", []):
            q = by_id.get(str(ch.get("quote")))
            if q and q["item"] in todo and q["item"] not in chosen:
                try:
                    chosen[q["item"]] = (q, max(1, int(ch.get("cap"))))
                except (TypeError, ValueError):
                    chosen[q["item"]] = (q, q["ask"])
        for i in todo:   # an item the harness skipped: cheapest open quote, cap = its ask
            if i not in chosen:
                cands = sorted((q for q in open_q if q["item"] == i), key=lambda q: q["ask"])
                if cands:
                    chosen[i] = (cands[0], cands[0]["ask"])
                    log(f"[guard] allot skipped item {i + 1}; cheapest quote {cands[0]['id']}")
        total = sum(c for _, c in chosen.values())
        if total > ledger.free():   # caps must fit what is left: scale them down
            f = ledger.free() / total
            log(f"[guard] caps {total} exceed free budget {ledger.free()}; scaling by {f:.2f}")
            chosen = {i: (q, math.floor(c * f)) for i, (q, c) in chosen.items()}
        keep = {id(q) for q, _ in chosen.values()}
        for q in open_q:   # never leave a quote task open that we will not pursue
            if id(q) not in keep:
                q["client"].call("CancelTask", {"id": q["task"]["id"]})
        caps = {i: ledger.reserve(i, c) for i, (q, c) in chosen.items()}
        log("[allot] " + "; ".join(f"item {i + 1} -> {shops[q['shop']]['name']} {q['id']} ask {q['ask']} cap {caps[i]}"
                                   for i, (q, _) in chosen.items()) + f" | free after reserve {ledger.free()}")

        # 5. HAGGLE: one thread per item
        def haggle(i, q, cap):
            c, t, tid = q["client"], q["task"], q["task"]["id"]
            asks, offers = [q["ask"]], []
            name = shops[q["shop"]]["name"]
            for r in range(1, MAX_ROUNDS + 1):
                if state(t) != "TASK_STATE_INPUT_REQUIRED":
                    break
                line = (f"item {i + 1} '{items[i]}' | shop {name} | product {q['product']} | cap {cap} | "
                        f"shop asks {asks} | your offers {offers} | round {r}/{MAX_ROUNDS}")
                log(f"[state] {line}")
                d, _ = ask_json(HAGGLE_PROMPT.format(line=line, cap=cap), f"haggle {i + 1}")
                act = str((d or {}).get("action", "walk")).lower()
                try:
                    price = asks[-1] if act == "accept" else math.floor(float(d.get("price")))
                except (TypeError, ValueError, AttributeError):
                    act = "walk"
                if act == "walk":
                    t = c.call("CancelTask", {"id": tid}) or t
                    break
                if price > cap:
                    log(f"[guard] item {i + 1}: offer {price} above cap {cap} -> {cap}")
                    price = cap
                offers.append(price)
                t = c.send(str(price), tid)
                if t is None:
                    break
                if state(t) == "TASK_STATE_INPUT_REQUIRED":
                    asks.append(ask_of(t))
            if state(t) == "TASK_STATE_INPUT_REQUIRED":
                t = c.call("CancelTask", {"id": tid}) or t
            rec = receipt_of(t)
            if state(t) == "TASK_STATE_COMPLETED" and rec:
                ledger.commit(i, {"item": items[i], "shop": name, "sku": rec.get("sku"), "product": rec.get("item"),
                                  "price": rec["price"], "task": tid})
                log(f"[deal] item {i + 1} '{items[i]}' at {name}: {rec.get('item')} ({rec.get('sku')}) for {rec['price']}"
                    f" | asks {asks} offers {offers}")
            else:
                ledger.release(i)
                if len(asks) >= 3 and asks[-1] == asks[-2]:   # the shop held its price: that is its floor
                    floors[i, q["shop"]] = asks[-1]
                else:
                    failed_at[i].add(q["shop"])
                notes.append(f"item {i + 1} failed at {name} (asks {asks}, offers {offers}, cap {cap})")
                log(f"[fail] item {i + 1} at {name}: {state(t)} | asks {asks} offers {offers} cap {cap}")
        list(pool.map(lambda a: haggle(*a), [(i, q, caps[i]) for i, (q, _) in chosen.items()]))

    spent = sum(b["price"] for b in ledger.bought.values())
    out = {"budget": args.budget, "spent": spent, "bought": {items[i]: b for i, b in ledger.bought.items()},
           "missing": [x for i, x in enumerate(items) if i not in ledger.bought],
           "harness_calls": harness.CALLS, "seconds": round(time.time() - t0)}
    json.dump(out, open(args.out, "w"), indent=2, ensure_ascii=False)
    log(f"\n[done] bought {len(ledger.bought)}/{len(items)}, spent {spent}/{args.budget}, "
        f"harness calls {harness.CALLS}, {out['seconds']}s")
    return 0 if not out["missing"] else 1


if __name__ == "__main__":
    sys.exit(main())
