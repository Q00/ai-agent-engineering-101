"""Week 07 — the market buyer.

    python market_buyer.py --shops shops.json --item lamp --budget 120

The kit hands it a shop list with reviews and one order. It picks a shop,
trades over A2A, and prints one JSON line:

    {"shop": "<card URL>", "receipt": {...} or null, "stars": 1-5, "review": "..."}

Three things decide the pick, in this order:

  1. **Own memory beats strangers' reviews.** `market_memory.json` records what
     actually happened to *this* buyer at each shop -- including whether the
     receipt matched the agreed price. A shop that overcharged me is dropped
     whatever its public reviews say, because a review is a claim and my own
     receipt is evidence.
  2. **Reviews, read by the harness.** Shops the memory has nothing bad about
     are summarised (name, star average, the newest review lines) and the
     harness picks one. That summary is small by construction, so a market with
     many shops does not grow the prompt.
  3. **A code floor under both.** Anything the harness picks must be in the
     list; a shop blacklisted by memory is removed before the harness sees it,
     so the model cannot choose it back.

Shops are remembered by `name`, never by URL: a shop's URL changes every time
its owner restarts the kit.
"""
import argparse
import json
import re
import statistics
import sys
import uuid
from pathlib import Path

import httpx

import harness

MEMORY = "market_memory.json"
MAX_ROUNDS = 8
HEADERS = {"A2A-Version": "1.0"}
REVIEWS_SHOWN = 3          # newest reviews per shop put in front of the harness


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# ------------------------------------------------------------------- memory

def load_memory(path=MEMORY) -> dict:
    p = Path(path)
    if not p.exists():
        return {"shops": {}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and "shops" in d else {"shops": {}}
    except json.JSONDecodeError:
        log("[buyer] memory file was unreadable; starting a fresh one")
        return {"shops": {}}


def remember(mem: dict, name: str, **fields):
    """One row per shop, by name.

    deals / no_deals are counts; `cheated` is the one that matters -- it is set
    when a receipt came back at a price I did not agree to, which is the thing
    reviews in this market are full of and the thing I can check myself.
    """
    row = mem["shops"].setdefault(name, {"deals": 0, "no_deals": 0, "cheated": 0,
                                         "prices": [], "last": None})
    for k, v in fields.items():
        if k in ("deals", "no_deals", "cheated"):
            row[k] = row.get(k, 0) + v
        elif k == "price" and v is not None:
            row["prices"].append(v)
        else:
            row[k] = v
    return row


def save_memory(mem: dict, path=MEMORY):
    Path(path).write_text(json.dumps(mem, indent=1, ensure_ascii=False) + "\n",
                          encoding="utf-8")


# -------------------------------------------------------------- picking a shop

def star_avg(reviews):
    stars = [r["stars"] for r in reviews if isinstance(r.get("stars"), (int, float))]
    return round(statistics.mean(stars), 2) if stars else None


def summarise(shop, mem):
    row = mem["shops"].get(shop.get("name"), {})
    revs = shop.get("reviews") or []
    return {
        "name": shop.get("name"),
        "stars": star_avg(revs),
        "reviews": [str(r.get("review", ""))[:120] for r in revs[:REVIEWS_SHOWN]],
        "my_deals": row.get("deals", 0),
        "my_no_deals": row.get("no_deals", 0),
        "my_avg_price": (round(statistics.mean(row["prices"]), 1)
                         if row.get("prices") else None),
    }


def pick_shop(shops, item, budget, mem):
    """Returns (shop, why). Memory filters; the harness chooses what is left."""
    blacklisted = {n for n, r in mem["shops"].items() if r.get("cheated", 0) > 0}
    allowed = [s for s in shops if s.get("name") not in blacklisted]
    dropped = [s.get("name") for s in shops if s.get("name") in blacklisted]
    if dropped:
        log(f"[buyer] memory: skipping {dropped} (overcharged me before)")
    if not allowed:
        log("[buyer] every shop is blacklisted; falling back to the whole list")
        allowed = list(shops)
    if len(allowed) == 1:
        return allowed[0], f"only {allowed[0].get('name')!r} was left after my memory"

    cards = [summarise(s, mem) for s in allowed]
    listing = "\n".join(
        f"{i}. {c['name']} | stars {c['stars']} | my past: {c['my_deals']} deals, "
        f"{c['my_no_deals']} without a deal, avg price {c['my_avg_price']}\n"
        + ("".join(f"     - {r}\n" for r in c["reviews"]) or "     - (no reviews)\n")
        for i, c in enumerate(cards)).rstrip()

    d = harness.ask(
        "You are buying one item and must choose which shop to approach.\n"
        f"Item: {item}\nYour budget: {budget}\n\n"
        "Shops, with what other buyers said and what happened to you before:\n"
        f"{listing}\n\n"
        "Reviews that mention a receipt not matching the agreed price are the "
        "serious ones; a shop that merely drove a hard bargain is fine.\n"
        'Reply with one JSON object and nothing else: '
        '{"choice": <number>, "why": "<one short sentence>"}.',
        want=("choice",), tag=" pick")
    i = harness.as_int(d.get("choice"))
    if i is None or not (0 <= i < len(allowed)):
        # Fall back on the numbers rather than on list order.
        ranked = sorted(range(len(cards)),
                        key=lambda j: (-(cards[j]["stars"] or 0), j))
        i = ranked[0]
        why = f"harness gave no usable choice; best star average ({cards[i]['stars']})"
        log(f"[buyer] {why}")
    else:
        why = str(d.get("why", "")).strip() or "chosen by the harness"
    return allowed[i], why


# ------------------------------------------------------------------- trading

class Shop:
    def __init__(self, base):
        self.base = str(base).rstrip("/")
        self.rpc_url = self.base + "/"

    def read_card(self):
        r = httpx.get(self.base + "/.well-known/agent-card.json",
                      headers=HEADERS, timeout=30)
        r.raise_for_status()
        card = r.json()
        iface = (card.get("supportedInterfaces") or [{}])[0]
        if iface.get("url"):
            self.rpc_url = iface["url"]
        return card

    def send(self, text, task_id=None):
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()),
               "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": "SendMessage",
                "params": {"message": msg}}
        log(f"[buyer] -> {text!r}" + (f" taskId={task_id[:8]}" if task_id else ""))
        r = httpx.post(self.rpc_url, json=body, headers=HEADERS, timeout=200).json()
        if "error" in r:
            log(f"[buyer] <- error {str(r['error'].get('message'))[:120]}")
            return None
        return r["result"].get("task", r["result"])


def state(t):
    return (t or {}).get("status", {}).get("state", "")


def text_of(t):
    return " ".join(p.get("text", "")
                    for p in (t or {}).get("status", {}).get("message", {}).get("parts", []))


def receipt_of(t):
    for a in (t or {}).get("artifacts", []):
        for p in a.get("parts", []):
            d = p.get("data")
            if isinstance(d, dict) and "price" in d:
                return d
    return None


def ask_of(t):
    meta = (t or {}).get("status", {}).get("message", {}).get("metadata") or {}
    n = harness.as_int(meta.get("ask"))
    if n is not None:
        return n
    nums = re.findall(r"\d+(?:\.\d+)?", text_of(t))
    return int(float(nums[-1])) if nums else None


def decide(item, budget, ask, history):
    d = harness.ask(
        "You are haggling for one item.\n"
        f"Item: {item}\nYour budget (the most you may ever pay): {budget}. Never go above it.\n"
        f"The shop is asking {ask}.\nRecent exchange:\n" + ("\n".join(history[-6:]) or "(none)")
        + "\n\nAccept if the ask is at or below your budget. Otherwise counter at or below "
          "your budget. Walk away if the shop clearly cannot reach it.\n"
          'Reply with one JSON object and nothing else: '
          '{"action": "accept"|"counter"|"walk_away", "price": <whole number>}.',
        want=("action",), tag=" haggle")
    action = str(d.get("action", "")).lower().strip()
    price = harness.as_int(d.get("price"))
    if action not in ("accept", "counter", "walk_away"):
        action = "accept" if (ask is not None and ask <= budget) else "counter"
    if action == "accept" and (ask is None or ask > budget):
        log(f"[buyer] REFUSED harness accept at {ask} > budget {budget}")
        action, price = "counter", None
    if action == "accept":
        return "accept", ask
    if action == "walk_away":
        return "walk_away", None
    if price is None:
        price = budget if ask is None else min(budget, max(1, (ask + budget) // 2))
    return "counter", min(price, budget)        # code enforces the budget


def review_for(deal_price, agreed, cheated, rounds, item):
    """Stars and a sentence. Written in code, not by the harness: a review is a
    claim other buyers will act on, and it should say what measurably happened."""
    if cheated:
        return 1, (f"Agreed {agreed} for the {item}, the receipt said {deal_price}. "
                   f"Receipt did not match the deal.")
    if deal_price is None:
        return 2, (f"No deal on the {item} within my budget after {rounds} rounds; "
                   f"the shop would not come down.")
    if rounds <= 2:
        return 5, f"Quick, fair deal on the {item} at {deal_price}; receipt matched."
    return 4, f"Deal on the {item} at {deal_price} after {rounds} rounds; receipt matched."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    ap.add_argument("--memory", default=MEMORY)
    args = ap.parse_args()

    shops = json.loads(Path(args.shops).read_text(encoding="utf-8"))
    mem = load_memory(args.memory)
    shop, why = pick_shop(shops, args.item, args.budget, mem)
    name, url = shop.get("name"), shop.get("url")
    log(f"[buyer] picked {name!r} ({url}) because: {why}")

    result = {"shop": url, "receipt": None, "stars": 3, "review": ""}
    agreed = None
    rounds = 0
    try:
        s = Shop(url)
        s.read_card()
        t = s.send(f"I want to buy a {args.item}")
        if t is None:
            raise RuntimeError("the shop refused the opening message")
        task_id = t.get("id")
        history = [f"shop: {text_of(t)[:160]}"]
        for _ in range(MAX_ROUNDS):
            st = state(t)
            if st != "TASK_STATE_INPUT_REQUIRED":
                break
            ask = ask_of(t)
            action, price = decide(args.item, args.budget, ask, history)
            if action == "walk_away":
                log(f"[buyer] walking away (ask {ask} > budget {args.budget})")
                break
            assert price <= args.budget, "code guard: never offer above the budget"
            agreed, rounds = price, rounds + 1
            history.append(f"me: {price}")
            t = s.send(str(price), task_id)
            if t is None:
                break
            history.append(f"shop: {text_of(t)[:160]}")
            log(f"[buyer] {state(t)} | {text_of(t)[:140]}")

        rec = receipt_of(t) if state(t) == "TASK_STATE_COMPLETED" else None
        if rec is not None:
            paid = harness.as_int(rec.get("price"))
            # The one thing I can check myself, and the thing the reviews in
            # this market keep complaining about.
            cheated = paid is not None and agreed is not None and paid != agreed
            if cheated:
                log(f"[buyer] RECEIPT MISMATCH: agreed {agreed}, receipt says {paid}")
            over = paid is not None and paid > args.budget
            result["receipt"] = rec
            result["stars"], result["review"] = review_for(paid, agreed,
                                                           cheated or over, rounds, args.item)
            remember(mem, name, deals=1, cheated=1 if (cheated or over) else 0,
                     price=paid, last=f"deal at {paid}")
        else:
            result["stars"], result["review"] = review_for(None, agreed, False,
                                                           rounds, args.item)
            remember(mem, name, no_deals=1, last="no deal")
    except Exception as e:                                  # noqa: BLE001
        log(f"[buyer] {type(e).__name__}: {e}")
        result["stars"] = 2
        result["review"] = f"Could not complete a trade for the {args.item}: {type(e).__name__}."
        remember(mem, name, no_deals=1, last=f"error: {type(e).__name__}")

    save_memory(mem, args.memory)
    print(json.dumps(result, ensure_ascii=False))           # the kit reads this line
    return 0


if __name__ == "__main__":
    sys.exit(main())
