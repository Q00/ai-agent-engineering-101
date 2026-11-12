"""Market buyer (week-07 LAB 1): pick a shop from {shops}, negotiate over A2A v1.0, remember, review.

    python market_buyer.py --shops .market/shops.json --item lamp --budget 120

How it works
  1. choose   : score every shop in code from (a) its public reviews, newest weighted most,
                (b) red-flag words in low-star reviews (overcharged, receipt said ...), and
                (c) this buyer's own memory of past trades with that shop name.
                The best score wins; the reason is printed as a [choose] line.
  2. negotiate: the week-06 harness loop. The harness also sees a short reputation summary.
                The budget is enforced in code: no offer above --budget is ever sent.
  3. check    : the receipt must name the item, carry the A2A task id, be signed (sig_seller)
                and show the price we offered last. A mismatch is remembered as "cheated".
  4. remember : one JSON object per trade appended to buyer_memory.json (shops by name).
  5. report   : the last stdout line is the result JSON the kit sends to the market.

Round 3 (week-08, ddolcom): with --order the buyer reads order.json. Only shops that stock one of the order's
acceptable SKUs (shops.json "offers") are scored; the buyer asks for one SKU by name, preferring the highest list
price within the budget (a deal is worth the SKU's list price to the user), checks the receipt names an acceptable
SKU, and adds an "accept" policy so the kit pays only with the market's certificate and, for food, enough hours left.
Since 2026-11-06 the code also sets the first offer, the top price and the step, weighs shops by what the market paid
them (shop_prices.json), and does not pay for grade C goods without a warranty.
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

from harness import HarnessError, ask_json

MAX_OFFERS = 8
# ddolcom (2026-11-06): saved = discount off list price, and over the last 12 h mine was 0.223, 35th of 41 buyers. The
# first offer was 75% of list and a deal took 2 offers. Most shops lower their ask with every offer, so open low and climb
# slowly. The harness still decides each move; the code holds the opening, the top price and the size of a step
# ddolcom (2026-11-12 10:30, the user's "1, 2를 적용"): saved is the sum of (1 - paid/list) over deals / (deals + 100)
# (/agent.md: value is the list price whatever the grade), so a deal above about 0.65 of list pulls my 0.3297 down. Over 6 h my
# buyer paid 0.634 of list, the dearest of the class's top 13 (dragonash22 0.582, inextro 0.555, Dae-yangKim 0.547), and saved
# ranks 12th of 46 at 0.3297 with 5th at 0.3382 (logs/top-shops-1112-1008.txt). Deals weigh 0.1, saved 0.5. Was 0.55, 0.75, 0.07
OPEN_SHARE = 0.50                             # first offer as a share of list price, sent by the code
LIMIT_SHARE = 0.65                            # never pay more than this share of list price
# Claude (2026-11-12 14:44, hourly check, the 10:27 revert line "deals/h under half -> LIMIT 0.70"): deals/h 54-81 before 10:27,
# then 42, 40.9, 33.0; class buyer pct 0.772 -> 0.761 (saved pct 0.756 -> 0.733) while saved itself rose 0.3297 -> 0.3307
LIMIT_SHARE = 0.70
# ddolcom (2026-11-12 19:30, the user's "진행해줘" on the Patch 3.6 plan): a deal now scores (list - price) / (list - cost), 1 at
# or below cost, 0 refunded, -1 served nothing, and saved = sum / (deals + 100), so a deal under my saved (0.76) pulls it down.
# 12 h of my deals (logs/saved-study-1112-1922.txt), score by kind, grade and paid/list: food A 0.60-0.65 0.803, 0.65-0.70 0.712,
# 0.70+ 0.691; food B 0.60-0.65 0.818, 0.65-0.70 0.735; food C 0.55-0.60 0.839; goods A 0.65-0.70 0.836; goods B 0.55-0.60 0.917,
# 0.60-0.65 0.223 (refunds); goods C 0.55+ 0.44 to -1. The limit follows the grade the shop states; LIMIT_SHARE when it states none
GRADE_LIMIT = {("food", "A"): 0.65, ("food", "B"): 0.65, ("food", "C"): 0.60,
               ("goods", "A"): 0.70, ("goods", "B"): 0.60, ("goods", "C"): 0.55}
GRADE_RE = re.compile(r"\b(?i:grade)\s*(?:now\s*)?(?:is\s*)?[:=]?\s*([ABC])\b")
STEP_SHARE = 0.05                             # one offer rises at most this share of list price
PRICE_WEIGHT = 10.0                           # shop score per unit of paid/list: 0.03 cheaper is worth 0.3 stars
PRICE_MIN_DEALS = 10                          # market deals of a shop before its prices count
SHOP_PRICES = Path("shop_prices.json")        # paid/list per shop and kind, from the ledger (restock.py writes it)
MEMORY_FILE = Path("buyer_memory.json")       # the kit runs us inside the week-07 directory
MEMORY_KEEP = 300                             # keep the newest N trades
PRIOR, PRIOR_WEIGHT, DECAY = 3.0, 2.0, 0.85   # review score: shrink toward 3 stars, newest weighs most
RED_FLAG = re.compile(r"receipt (said|says|was)|overcharg|higher than|more than (we|i) agreed|"
                      r"did not match|didn't match|mismatch|avoid|scam|cheat|fraud|fake|forged|unsigned", re.I)

PROMPT = """You are a buying agent. Your user wants one {item}.{worth} The budget is {budget}.
Never offer or agree to more than {budget}. Pay as little as you can, but shops may end the negotiation after a few low offers,
so move toward the shop's price in real steps rather than by one or two. Walking away is fine when the shop will not come down to the budget.{coach}

Shop: {card}
What other buyers and your own past trades say about this shop: {reputation}

Conversation so far:
{transcript}

Decide your next move. Answer with JSON only, one of:
{{"action": "offer", "price": <whole number>}}   send an offer (send the shop's price to accept it)
{{"action": "walk"}}                              walk away without buying"""


# ---------------------------------------------------------------- memory
def load_memory():
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return [m for m in data if not str(m.get("error", "")).startswith("harness")]   # not a fact about the shop
    except (OSError, ValueError):
        return []


def save_memory(mem, entry):
    mem.append(entry)
    try:
        MEMORY_FILE.write_text(json.dumps(mem[-MEMORY_KEEP:], ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        print(f"[memory] could not write {MEMORY_FILE}: {e}", flush=True)


# ---------------------------------------------------------------- choosing
def review_score(reviews):
    """Weighted average stars (newest first), shrunk toward PRIOR; plus the number of red-flag reviews."""
    num, den, flags = PRIOR * PRIOR_WEIGHT, PRIOR_WEIGHT, 0
    for i, r in enumerate(reviews or []):
        stars = r.get("stars")
        if isinstance(stars, (int, float)) and 1 <= stars <= 5:
            w = DECAY ** i
            num, den = num + w * stars, den + w
        text = str(r.get("review", ""))
        if RED_FLAG.search(text) and (not isinstance(stars, (int, float)) or stars <= 2):
            flags += 1
    return num / den, flags


def memory_score(mem, name, item):
    """Adjustment from our own trades with this shop name, and a short note for the log."""
    mine = [m for m in mem if m.get("shop") == name]
    if not mine:
        return 0.0, "no trades of mine"
    cheated = sum(1 for m in mine if m.get("result") == "cheated")
    deals = [m for m in mine if m.get("result") == "deal"]
    walks = sum(1 for m in mine if m.get("result") == "walk")
    errors = sum(1 for m in mine if m.get("result") == "error")
    adj = -3.0 * cheated + min(0.3 * len(deals), 1.0) - 0.1 * walks - 0.2 * errors
    same = [d for d in deals if d.get("item") == item and d.get("budget")]
    if same:                                   # cheaper than budget on average -> small bonus
        ratio = sum(d["price"] / d["budget"] for d in same) / len(same)
        adj += 0.5 * (1 - ratio)
    note = f"mine: {len(deals)} deals, {walks} walks, {cheated} cheated, {errors} errors"
    return adj, note


def load_prices():
    try:
        data = json.loads(SHOP_PRICES.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def price_adjust(shops, prices, kind):
    """Shop name -> score adjustment: above zero when the market pays this shop less of list than the other candidates."""
    ratios = {}
    for s in shops:
        r = ((prices.get(s.get("name")) or {}).get(kind) or [None, 0])
        if r[0] is not None and r[1] >= PRICE_MIN_DEALS:
            ratios[s.get("name")] = r[0]
    if not ratios:
        return {}
    mean = sum(ratios.values()) / len(ratios)
    return {name: PRICE_WEIGHT * (mean - r) for name, r in ratios.items()}


def choose(shops, mem, item, prices=None, kind=None):
    scored = []
    adjust = price_adjust(shops, prices or {}, kind)
    for s in shops:
        rev, flags = review_score(s.get("reviews"))
        madj, mnote = memory_score(mem, s.get("name"), item)
        padj = adjust.get(s.get("name"), 0.0)
        score = rev - 0.7 * min(flags, 3) + madj + padj + random.uniform(0, 0.05)   # tiny jitter: explore ties
        n = len(s.get("reviews") or [])
        scored.append((score, s, f"score {score:.2f} = reviews {rev:.2f} ({n}), red flags {flags}, {mnote}, price {padj:+.2f}"))
    scored.sort(key=lambda x: x[0], reverse=True)
    for score, s, why in scored:
        print(f"[score] {s.get('name')}: {why}", flush=True)
    return scored[0]


def stocked(shops, order):
    """Round 3: only shops that hold an acceptable SKU can sell; Rounds 0-2 (no acceptable list): all shops."""
    if not order.get("acceptable"):
        return shops
    return [s for s in shops if s.get("offers")]


def pick_offer(shop, budget, catalog):
    """Round 3: the SKU to ask for, the highest list price within the budget (worth most to the user),
    else the cheapest one the shop has. Returns the offer with its list price (None when unknown)."""
    offers = [dict(o, list_price=(catalog.get(o["sku"]) or {}).get("list_price")) for o in shop.get("offers") or []]
    if not offers:
        return None
    priced = [o for o in offers if o["list_price"]]
    within = [o for o in priced if o["list_price"] <= budget]
    if within:
        return max(within, key=lambda o: o["list_price"])
    return min(priced, key=lambda o: o["list_price"]) if priced else offers[0]


def accept_policy(order):
    """What the kit checks before paying: the market's certificate always; food must last the hours the user needs."""
    policy = {"cert": True}
    if order.get("kind") == "food" and order.get("use_within_hours"):
        policy["min_hours_left"] = order["use_within_hours"]
    return policy


def reputation(shop, why):
    recent = [f"{r.get('stars')}* {str(r.get('review', ''))[:80]}" for r in (shop.get("reviews") or [])[:3]]
    return f"{why}. Recent reviews: " + (" | ".join(recent) if recent else "none")


# ---------------------------------------------------------------- A2A
def make_rpc(url):
    def rpc(method, params):
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
        r = httpx.post(url, json=body, headers={"A2A-Version": "1.0"}, timeout=200).json()
        if "error" in r:
            raise RuntimeError(r["error"])
        return r["result"].get("task", r["result"])
    return rpc


def receipt_of(t):
    for a in (t or {}).get("artifacts", []) or []:
        for p in a.get("parts", []) or []:
            d = p.get("data")
            if isinstance(d, dict) and isinstance(d.get("price"), (int, float)):
                return d
    return None


def whole(x):
    return int(x) if isinstance(x, float) and x.is_integer() else x


def price_plan(budget, list_price):
    """(top price, first offer, largest rise per offer). Without a list price only the budget binds."""
    if not list_price:
        return budget, None, None
    limit = min(budget, int(LIMIT_SHARE * list_price))
    return limit, min(int(OPEN_SHARE * list_price), limit), max(1, int(STEP_SHARE * list_price))


def asked_below(said, anchor, list_price):
    """The first offer, lowered when the shop's own reply already names a smaller price than our anchor."""
    nums = [int(x.replace(",", "")) for x in re.findall(r"(?<![A-Za-z\d])\d[\d,]*", said)]
    nums = [n for n in nums if 0.2 * list_price <= n < anchor]
    return min(nums) if nums else anchor


def negotiate(base_url, item, budget, rep, opening=None, worth="", list_price=None, kind=None):
    """Returns (task, receipt or None, last offer we sent, number of offers, log of offers).
    Round 3 passes the opening line (name and SKU), what the SKU is worth to the user, its list price and the order's kind."""
    limit, anchor, step = price_plan(budget, list_price)
    graded = None

    def coaching():
        if anchor is None:
            return ""
        return ("\nThe user is scored on the discount off the list price. Most shops lower their price after each offer, "
                "so several small rises get a lower price than one big one: do not jump to the shop's price while it is "
                f"more than {step} above your last offer. Walk away when the shop will not come down to {limit}.")

    def regrade(said):
        """The limit for the grade the shop names (GRADE_LIMIT), once it names one."""
        nonlocal limit, anchor, graded
        m = GRADE_RE.search(said or "")
        if not m or not list_price or (kind, m.group(1)) not in GRADE_LIMIT or m.group(1) == graded:
            return
        graded = m.group(1)
        new = min(budget, int(GRADE_LIMIT[(kind, graded)] * list_price))
        if new != limit:
            print(f"[grade] {kind} grade {graded}: limit {limit} -> {new}", flush=True)
            limit = new
            if anchor is not None:
                anchor = min(anchor, limit)
    coach = coaching()
    card = httpx.get(base_url.rstrip("/") + "/.well-known/agent-card.json", timeout=15).json()
    ifaces = card.get("supportedInterfaces") or [{}]
    url = ifaces[0].get("url") or base_url
    print(f"card: {card.get('name')} -> {url}", flush=True)
    rpc = make_rpc(url)

    def send(text, task_id=None):
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        t = rpc("SendMessage", {"message": msg})
        said = " ".join(p.get("text", "") for p in t["status"].get("message", {}).get("parts", []))
        print(f"-> {text!r}  <- {t['status']['state']} | {said}", flush=True)
        return t, said

    opening = opening or f"I want to buy a {item}"
    t, said = send(opening)
    regrade(said)
    coach = coaching()
    lines = [f"you: {opening}", f"shop: {said}"]
    last_offer, offers, at_budget = None, [], 0
    for _ in range(MAX_OFFERS):
        if t["status"]["state"] != "TASK_STATE_INPUT_REQUIRED":
            break
        if anchor is not None and not offers:    # the first offer needs no model
            offer = asked_below(said, anchor, list_price)
            print(f"[anchor] first offer {offer} (list {list_price}, limit {limit})", flush=True)
        else:
            move, raw = ask_json(PROMPT.format(item=item, worth=worth, budget=limit, coach=coach, card=card.get("description", ""),
                                               reputation=rep, transcript="\n".join(lines)))
            print(f"[harness] {raw}", flush=True)
            if not move or move.get("action") != "offer" or not isinstance(move.get("price"), (int, float)):
                rpc("CancelTask", {"id": t["id"]})
                print("walked away (CancelTask)", flush=True)
                return t, None, last_offer, offers
            offer = int(move["price"])
            if step and last_offer is not None and offer > last_offer + step:
                print(f"[guard] offer {offer} rises more than {step} -> {last_offer + step}", flush=True)
                offer = last_offer + step
            if offer > limit:                    # the harness may forget the limit; the code does not
                print(f"[guard] offer {offer} above limit {limit} -> {limit}", flush=True)
                offer = limit
        if offer == limit:
            at_budget += 1
            if at_budget > 2:                    # the shop refused our limit twice already
                rpc("CancelTask", {"id": t["id"]})
                print("limit refused twice, walked away (CancelTask)", flush=True)
                return t, None, last_offer, offers
        t, said = send(str(offer), t["id"])
        regrade(said)
        coach = coaching()
        last_offer = offer
        offers.append(offer)
        lines += [f"you: {offer}", f"shop: {said}"]
    if t["status"]["state"] == "TASK_STATE_INPUT_REQUIRED":
        rpc("CancelTask", {"id": t["id"]})
        print("offer limit reached (CancelTask)", flush=True)
        return t, None, last_offer, offers
    rec = receipt_of(t) if t["status"]["state"] == "TASK_STATE_COMPLETED" else None
    return t, rec, last_offer, offers


def check_receipt(rec, t, item, budget, last_offer, acceptable=()):
    """List of problems with a receipt (empty when it looks honest). Round 3: any acceptable SKU is the item."""
    problems = []
    price = whole(rec.get("price"))
    if rec.get("item") != item and rec.get("item") not in acceptable:
        problems.append(f"item {rec.get('item')!r} is not {item!r}")
    if last_offer is not None and isinstance(price, (int, float)) and price > last_offer:
        problems.append(f"agreed {last_offer}, receipt says {price}")
    if isinstance(price, (int, float)) and price > budget:
        problems.append(f"receipt price {price} above budget {budget}")
    if rec.get("task") and rec.get("task") != t.get("id"):
        problems.append("receipt task id is not this negotiation")
    if not rec.get("task"):
        problems.append("receipt has no task id")
    if not rec.get("sig_seller"):
        problems.append("receipt is not signed")
    return problems


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    ap.add_argument("--order", help="Round 3: the kit's order.json (acceptable SKUs, kind, use_within_hours)")
    a = ap.parse_args()

    shops = json.loads(Path(a.shops).read_text(encoding="utf-8"))
    order = json.loads(Path(a.order).read_text(encoding="utf-8")) if a.order else {}
    acceptable = order.get("acceptable") or []
    if acceptable:
        shops = stocked(shops, order)
        if not shops:                                     # no line with "shop": the kit closes it as no deal, no review
            print(f"[choose] no shop stocks an acceptable SKU for {a.item}", flush=True)
            print(json.dumps({"receipt": None, "note": "no shop in stock"}), flush=True)
            return
    mem = load_memory()
    result = {"shop": shops[0]["url"] if shops else "", "receipt": None, "stars": 3, "review": "No shop to trade with."}
    if acceptable:
        result["accept"] = accept_policy(order)
    entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "item": a.item, "budget": a.budget}
    try:
        if not shops:
            raise RuntimeError("empty shop list")
        score, shop, why = choose(shops, mem, a.item, load_prices() if acceptable else None, order.get("kind"))
        print(f"[choose] {shop['url']}: {shop.get('name')} - {why}", flush=True)
        result["shop"] = shop["url"]
        entry["shop"] = shop.get("name")

        opening, worth, want, list_price = None, "", a.item, None
        if acceptable:
            catalog = json.loads((Path(os.environ.get("MARKET_DIR", ".market")) / "catalog.json").read_text(encoding="utf-8"))
            o = pick_offer(shop, a.budget, catalog)
            opening, want, entry["sku"] = f"I want to buy {o['name']} ({o['sku']})", o["name"], o["sku"]
            if order.get("kind") == "food" and order.get("use_within_hours"):   # the kit refuses to pay for less
                opening += f". It must have at least {int(order['use_within_hours'])} hours of shelf life left"
            if o.get("list_price"):
                worth = f" It is worth {o['list_price']} to them (its list price)."
                list_price = o["list_price"]
            print(f"[offer] {o['sku']} {o['name']} list {o.get('list_price')}, budget {a.budget}", flush=True)

        t, rec, last_offer, offers = negotiate(shop["url"], want, a.budget, reputation(shop, why), opening, worth, list_price,
                                                     order.get("kind"))
        entry["offers"] = offers
        if rec is None:
            entry["result"] = "walk"
            result.update(stars=3, review=f"No deal for the {a.item} within budget {a.budget}; "
                                          f"my offers {offers or 'none'}.")
        elif acceptable and order.get("kind") == "goods" and rec.get("claim") == "C" and not rec.get("warranty_hours"):
            # ddolcom (2026-11-06): grade C without a warranty broke 25% of the time in the market and scored 0.118 a deal
            # (grade A 0.217, grade C with a warranty 0.260). No "shop" in the last line: closed as no deal, no review
            entry.update(result="refused_c", price=whole(rec.get("price")))
            print("[guard] grade C without a warranty: not paying", flush=True)
            save_memory(mem, entry)
            print(json.dumps({"receipt": None, "note": "grade C without a warranty"}), flush=True)
            return
        else:
            result["receipt"] = rec                       # exactly the data Part the shop issued
            problems = check_receipt(rec, t, a.item, a.budget, last_offer, acceptable)
            price = whole(rec.get("price"))
            entry.update(price=price, task=rec.get("task"))
            if any(p.startswith(("agreed", "receipt price", "item")) for p in problems):
                entry["result"] = "cheated"
                entry["problems"] = problems
                result.update(stars=1, review=f"Receipt does not match the deal: {'; '.join(problems)}.")
            else:
                entry["result"] = "deal"
                stars = 5 if price <= 0.85 * a.budget else 4
                if problems:                              # e.g. unsigned: honest price, but not verifiable
                    stars, entry["problems"] = 3, problems
                rounds = len(offers)
                result.update(stars=stars, review=f"Bought the {a.item} at {price} (budget {a.budget}) after "
                                                  f"{rounds} offer{'s' if rounds != 1 else ''}; receipt matched"
                                                  + (f", but {'; '.join(problems)}." if problems else "."))
    except HarnessError as e:                             # my harness failed, not the shop: no shop line, so no review
        print(f"[error] {e}", flush=True)
        print(json.dumps({"receipt": None, "note": "my harness failed"}), flush=True)
        return
    except Exception as e:                                # always end with a result line
        entry["result"] = "error"
        entry["error"] = str(e)[:200]
        print(f"[error] {e}", flush=True)
        result.update(stars=2, review=f"Could not complete the trade: {str(e)[:120]}")
    if entry.get("shop"):
        save_memory(mem, entry)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if acceptable:
        forum_later(a.shops)


def forum_later(shops_path):
    """Round 3: forum.py decides whether there is news worth a post; detached, so the kit is not kept waiting."""
    try:
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        subprocess.Popen([sys.executable, str(Path(__file__).with_name("forum.py")), "--shops", shops_path],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags, start_new_session=os.name != "nt")
    except OSError as e:
        print(f"[forum] not started: {e}", flush=True)


if __name__ == "__main__":
    main()
