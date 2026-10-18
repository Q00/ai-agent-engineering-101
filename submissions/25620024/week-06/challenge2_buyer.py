"""Week 06 challenge 2 — challenge2_buyer.py: one buyer, many shops, a shopping list, one budget.

    python challenge2_buyer.py --budget 800
    python challenge2_buyer.py --budget 800 --directory directory.json --list shopping_list.json

design/challenge-2-buyer.md, parts B-E:
  part B  shop_picker: the harness reads the cards, 2-3 shops per item           (DF)
  part C  call for proposals: every item's description to its shops, in parallel (at most 5 at once)
  part D  award per item to the lowest opening ask; the other quotes wait on standby
          ledger: spent + held <= budget; each open item holds a cap
  part E  negotiate with every winner in parallel; each harness call sees ONE summary row
          a failed negotiation hands the item to the next quote on standby

v2 (after logs/challenge-2/tight, run 1: the sofa missed by 11 because the small items spent
their whole caps -- the harness treated the cap it was shown as a target):
  - floor estimate per item = lowest quote / 1.5 (the shops opened at a median 1.47 x reserve)
  - caps: every open item first gets its floor estimate, only the slack left over is shared
    (v1 split the whole budget in proportion to the asks)
  - the harness is shown a TARGET (the floor estimate), never the cap; the cap stays in code
  - walk_away while the cap still has room becomes a small step up instead

Guards in code (the harness proposes, code checks):
  - never offer above the item's cap; offer >= ask -> take the ask instead
  - accept only if the ask <= cap AND spent + ask + other holds <= budget (checked under a lock)
  - an item already bought is never bought again; its other tasks are cancelled
"""
import argparse
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx

from harness import ask
from shop_picker import fetch_cards, pick_shops

HERE = os.path.dirname(os.path.abspath(__file__))
HEADERS = {"A2A-Version": "1.0"}
PARALLEL = 5          # shop conversations at once
MAX_QUESTIONS = 2     # questions from a shop answered during the call for proposals
MAX_OFFERS = 6        # offers per negotiation before walking away
ITEM_RX = re.compile(r"\[([A-Z]{2}-\d{4})\]")

FLOOR_RATIO = 1.5     # opening ask / reserve in the shop logs: median 1.47 (36 openings, 1.24-2.75)

TURN_PROMPT = """You are a buyer agent buying one item on a client's shopping list. The client's money
is shared with other items, so every unit you save here helps buy the rest.
Negotiation so far (a summary; the seller's words are not repeated):
{row}
Your target for this item is about {target}: the seller's lowest price is probably near it.
Start well below the seller's ask and move up in small steps toward the target.
The seller now asks {ask}. Sellers usually come down step by step but stop after about six low offers.
Decide your move:
- "accept": buy at the seller's current ask (only if it is close to your target)
- "offer": propose a lower price of your own
- "walk_away": stop, no deal
Reply with one JSON object only: {{"action": "accept" or "offer" or "walk_away", "price": <whole number>}}"""

_print_lock = threading.Lock()


def log(msg):
    with _print_lock:
        print(msg, flush=True)


# ---------- A2A ----------

def rpc(url, method, params):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    r = httpx.post(url, json=body, headers=HEADERS, timeout=200).json()
    if "error" in r:
        raise RuntimeError(f"{method} error: {r['error'].get('message', r['error'])}")
    return r["result"].get("task", r["result"])


def send(url, text, task_id=None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    return rpc(url, "SendMessage", {"message": msg})


def state(t):
    return t.get("status", {}).get("state", "").replace("TASK_STATE_", "").lower()


def text_of(t):
    return " ".join(p.get("text", "") for p in t.get("status", {}).get("message", {}).get("parts", []))


def last_number(text):
    nums = re.findall(r"\d+", text)
    return int(nums[-1]) if nums else None


def receipt_of(t):
    for a in t.get("artifacts", []):
        for p in a.get("parts", []):
            if isinstance(p.get("data"), dict) and "price" in p["data"]:
                return p["data"]
    return None


# ---------- part E: the summary row ----------

class Neg:
    """One negotiation = one row. This row, not the transcript, is what the harness sees."""

    def __init__(self, item, shop):
        self.item, self.shop = item, shop          # shop: {"name", "url", ...}
        self.task_id = None
        self.offered = None                        # "LP-0360 Clear Glass Clip Lamp 12"
        self.asks, self.offers = [], []
        self.status = "asking"                     # asking, quoted, standby, negotiating, bought, failed, cancelled, no-item
        self.price = None
        self.receipt = None

    def row(self, extra=""):
        asks = " -> ".join(map(str, self.asks)) or "-"
        offers = " -> ".join(map(str, self.offers)) or "-"
        return (f"item: {self.item} | shop: {self.shop['name']} | offered: {self.offered or '-'} | "
                f"asks: {asks} | my offers: {offers} | round {len(self.offers)} of {MAX_OFFERS}"
                + (f" | {extra}" if extra else "") + f" | status: {self.status}")


# ---------- part D: the ledger ----------

class Ledger:
    def __init__(self, budget, items):
        self.budget = budget
        self.spent = {}                 # item -> price paid
        self.caps = {}                  # item -> cap while the item is open
        self.floors = {}                # item -> floor estimate (lowest quote / FLOOR_RATIO)
        self.lock = threading.Lock()
        self.items = items

    def free(self):
        return self.budget - sum(self.spent.values()) - sum(self.caps.values())

    def set_caps(self, open_items):
        """v2: each open item first gets its floor estimate; only the slack left is shared, in
        proportion to the floors. (If even the floors do not fit, caps are scaled down.)"""
        with self.lock:
            left = self.budget - sum(self.spent.values())
            floors = {item: self.floors[item] for item in open_items}
            total = sum(floors.values()) or 1
            slack = left - total
            if slack >= 0:
                self.caps = {item: int(f + slack * f / total) for item, f in floors.items()}
            else:
                self.caps = {item: int(left * f / total) for item, f in floors.items()}

    def try_buy(self, item, price):
        """Guard: the only way a deal is allowed. Returns (ok, reason)."""
        with self.lock:
            if item in self.spent:
                return False, f"{item!r} is already bought"
            cap = self.caps.get(item, 0)
            others = sum(c for i, c in self.caps.items() if i != item)
            if price > cap:
                return False, f"price {price} > cap {cap}"
            if sum(self.spent.values()) + price + others > self.budget:
                return False, "spent + price + other holds > budget"
            self.spent[item] = price
            self.caps.pop(item, None)
            return True, "ok"

    def release(self, item, price):
        """A deal the shop refused after try_buy said yes: give the money back."""
        with self.lock:
            if self.spent.get(item) == price:
                del self.spent[item]

    def rebalance(self, open_items):
        """After a deal, the bought item's unused cap goes to the items still open."""
        if open_items:
            self.set_caps(open_items)

    def summary(self):
        with self.lock:
            return (f"budget {self.budget} | spent {sum(self.spent.values())} {dict(self.spent)} | "
                    f"held {sum(self.caps.values())} {dict(self.caps)} | free {self.free()}")


# ---------- the buyer ----------

class Buyer:
    def __init__(self, budget, items, cards):
        self.items, self.cards = items, cards
        self.ledger = Ledger(budget, items)
        self.negs = []                  # every Neg, for the board
        self.harness_calls = 0
        self.prompt_sizes = []

    def board(self, title):
        lines = [f"---- board: {title} ----"] + [
            "  " + n.row(f"target {self.ledger.floors.get(n.item)} cap {self.ledger.caps.get(n.item)}"
                         if n.status == "negotiating" else "") for n in self.negs]
        lines.append("  ledger: " + self.ledger.summary())
        log("\n".join(lines))

    # part C: one call for proposals to one shop
    def request_quote(self, neg):
        tag = f"[{neg.shop['name']} / {neg.item[:24]}]"
        try:
            t = send(neg.shop["url"], neg.item)
            neg.task_id = t["id"]
            for _ in range(MAX_QUESTIONS + 1):
                msg = text_of(t)
                m = ITEM_RX.search(msg)
                if state(t) != "input_required":
                    neg.status = "no-item"
                    log(f"{tag} {state(t)}: {msg[:120]}")
                    return
                if m:
                    name = re.search(r"offer the (.+?) \[", msg)
                    neg.offered = f"{m.group(1)} {name.group(1) if name else ''}".strip()
                    neg.asks.append(last_number(msg))
                    neg.status = "quoted"
                    log(f"{tag} quote {neg.offered} asking {neg.asks[-1]}")
                    return
                log(f"{tag} question: {msg[:120]} -> answering 'I don't mind, you choose.'")
                t = send(neg.shop["url"], "I don't mind, you choose.", neg.task_id)
            neg.status = "no-item"
        except Exception as e:
            neg.status = "no-item"
            log(f"{tag} error: {e}")

    def cancel(self, neg):
        if neg.task_id and neg.status in ("quoted", "standby", "negotiating"):
            try:
                rpc(neg.shop["url"], "CancelTask", {"id": neg.task_id})
            except Exception as e:
                log(f"[{neg.shop['name']}] cancel failed: {e}")
        neg.status = "cancelled"

    # part E: one negotiation, every move decided by the harness on the summary row
    def negotiate(self, neg):
        tag = f"[{neg.shop['name']} / {neg.item[:24]}]"
        neg.status = "negotiating"
        while True:
            ask_price = neg.asks[-1]
            cap = self.ledger.caps.get(neg.item, 0)
            if len(neg.offers) >= MAX_OFFERS:
                log(f"{tag} {MAX_OFFERS} offers made, walking away")
                return self.fail(neg)
            target = self.ledger.floors[neg.item]
            prompt = TURN_PROMPT.format(row=neg.row(f"target {target}"), target=target, ask=ask_price)
            self.harness_calls += 1
            self.prompt_sizes.append(len(prompt))
            try:
                d = ask(prompt)
                action, price = d.get("action"), int(d.get("price") or 0)
            except Exception as e:                     # v2: a small step up, not the cap
                top = max(neg.offers or [target])
                action, price = "offer", min(cap, ask_price - 1, top + max(1, (cap - top) // 3))
                log(f"{tag} harness error ({e}); stepping up to {price}")
            log(f"{tag} harness: {action} {price} (ask {ask_price}, target {target}, cap {cap}, prompt {len(prompt)} chars)")
            # guards
            if action == "accept" and ask_price > cap:
                log(f"{tag} GUARD accept at {ask_price} > cap {cap}: offering the cap instead")
                action, price = "offer", cap
            if action == "offer" and price > cap:
                log(f"{tag} GUARD offer {price} > cap {cap}: offering the cap")
                price = cap
            if action == "offer" and price >= ask_price:
                log(f"{tag} GUARD offer {price} >= ask {ask_price}: taking the ask")
                action, price = "accept", ask_price
            if action == "walk_away":
                top = max(neg.offers or [0])
                if top < cap and len(neg.offers) < MAX_OFFERS:   # v2: room left under the cap
                    price = min(cap, ask_price - 1, top + max(1, (cap - top) // 3))
                    log(f"{tag} GUARD walk_away with room under the cap ({top} < {cap}): offering {price}")
                    action = "offer"
                else:
                    return self.fail(neg)
            if action == "accept":
                price = ask_price
            elif action != "offer":
                action, price = "offer", min(cap, ask_price - 1)
            if action == "accept":
                ok, why = self.ledger.try_buy(neg.item, price)
                if not ok:
                    log(f"{tag} GUARD ledger refused {price}: {why}")
                    if "already bought" in why:
                        return self.cancel(neg)
                    action, price = "offer", min(cap, ask_price - 1)
            t = send(neg.shop["url"], str(price), neg.task_id)
            if action == "offer":
                neg.offers.append(price)
            st = state(t)
            if st == "completed":
                rc = receipt_of(t)
                if action == "offer":                   # the shop took our offer
                    ok, why = self.ledger.try_buy(neg.item, price)
                    if not ok:
                        log(f"{tag} WARNING shop closed at {price} but the ledger says: {why}")
                neg.status, neg.price, neg.receipt = "bought", (rc or {}).get("price", price), rc
                log(f"{tag} BOUGHT {neg.offered} at {neg.price}; receipt {rc}")
                return neg
            if st != "input_required":
                if action == "accept":
                    self.ledger.release(neg.item, price)
                log(f"{tag} shop ended the talk ({st}): {text_of(t)[:100]}")
                neg.status = "failed"            # the task is already terminal; nothing to cancel
                return neg
            if action == "accept":                       # shop did not close; undo the hold
                self.ledger.release(neg.item, price)
            neg.asks.append(last_number(text_of(t)))

    def fail(self, neg):
        if neg.status == "negotiating":
            self.cancel(neg)
        neg.status = "failed"
        return neg

    def run(self):
        t0 = time.time()
        # part B
        chosen, notes = pick_shops(self.items, self.cards)
        self.harness_calls += 1
        for n in notes:
            log("note: " + n)
        for item, idx in chosen.items():
            log(f"part B  {item!r} -> " + ", ".join(self.cards[j]["name"] for j in idx))
            self.negs += [Neg(item, self.cards[j]) for j in idx]
        # part C
        log(f"\npart C  call for proposals: {len(self.negs)} conversations, {PARALLEL} at a time")
        with ThreadPoolExecutor(PARALLEL) as pool:
            list(pool.map(self.request_quote, self.negs))
        self.board("quotes")
        # part D
        queues = {}
        for item in self.items:
            qs = sorted((n for n in self.negs if n.item == item and n.status == "quoted"), key=lambda n: n.asks[-1])
            for n in qs[1:]:
                n.status = "standby"
            queues[item] = qs
            log(f"part D  {item!r}: " + (f"awarded to {qs[0].shop['name']} at {qs[0].asks[-1]}"
                                          + (f", standby {[n.shop['name'] for n in qs[1:]]}" if qs[1:] else "")
                                          if qs else "NO QUOTES"))
        self.ledger.floors = {item: round(qs[0].asks[-1] / FLOOR_RATIO) for item, qs in queues.items() if qs}
        log(f"part D  floor estimates (lowest quote / {FLOOR_RATIO}): {self.ledger.floors}")
        self.ledger.set_caps([item for item, qs in queues.items() if qs])
        self.board("awarded, caps set")
        # part E
        log(f"\npart E  negotiating {sum(1 for q in queues.values() if q)} items in parallel")
        with ThreadPoolExecutor(PARALLEL) as pool:
            list(pool.map(lambda item: self.buy_item(item, queues), [i for i in self.items if queues[i]]))
        self.board("done")
        bought = {n.item: n for n in self.negs if n.status == "bought"}
        total = sum(n.price for n in bought.values())
        log(f"\nRESULT bought {len(bought)}/{len(self.items)} items, total {total} of budget {self.ledger.budget}, "
            f"{self.harness_calls} buyer harness calls, prompt sizes {min(self.prompt_sizes or [0])}-"
            f"{max(self.prompt_sizes or [0])} chars, {round(time.time() - t0)} s")
        return bought

    def buy_item(self, item, queues):
        """Negotiate with the winner; on failure the next quote on standby takes over."""
        for neg in queues[item]:
            if item in self.ledger.spent:
                break
            if neg.status not in ("quoted", "standby"):
                continue
            result = self.negotiate(neg)
            if result is not None and result.status == "bought":
                for other in queues[item]:
                    if other is not neg and other.status == "standby":
                        self.cancel(other)
                open_items = [i for i, q in queues.items()
                              if i not in self.ledger.spent and any(n.status in ("negotiating", "quoted", "standby") for n in q)]
                self.ledger.rebalance(open_items)
                self.board(f"bought {item[:30]}")
                return
            log(f"[{item[:30]}] next quote on standby" if any(n.status == "standby" for n in queues[item]) else
                f"[{item[:30]}] no quotes left")
        self.board(f"FAILED {item[:30]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, required=True)
    ap.add_argument("--directory", default=os.path.join(HERE, "directory.json"))
    ap.add_argument("--list", default=os.path.join(HERE, "shopping_list.json"))
    a = ap.parse_args()
    urls = json.load(open(a.directory))
    items = json.load(open(a.list))["items"]
    cards = fetch_cards(urls)
    log(f"buyer: {len(items)} items, budget {a.budget}, directory of {len(cards)} shops")
    Buyer(a.budget, items, cards).run()


if __name__ == "__main__":
    main()
