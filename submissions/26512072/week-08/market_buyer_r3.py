"""Round 3 market buyer: picks a shop and a SKU, negotiates over A2A, and prints the result line for the kit.

    python market_buyer_r3.py --shops .market/shops.json --item 김치 --budget 52000 --order .market/order.json

Before Round 3 (orders without "acceptable" SKUs) it hands over to the week-07 market_buyer.py.

Context the model sees is built by code, one line per candidate (shop x SKU): the SKU's list price (its value to
the order), the shop's reviews, and my own memory of that shop (settled deals, dishonest grade claims, goods that
broke, refused payments, errors). The model (harness.py, claude -p haiku) picks the candidate and makes every offer.
Code enforces: offers never above min(budget, list price); food that cannot last use_within_hours is never a
candidate; the payment policy ("accept") asks for the market certificate and enough shelf life, and the first message
tells the shop how long the food must last. Listings go stale within seconds, so when a shop says it has none left
before any offer, the next best shop is asked (up to 3 shops per order).
Memory: buyer_memory_r3.jsonl, one line per order, updated later by review_r3.py (what settled) and here (payments
the kit refused, goods that broke).
"""
import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

import httpx

from harness import safe_ask_json
from r3lib import (BUYER_MEM, HERE, KIND_COST, MD, SHOP_PRICES, UNIT_COSTS, append_jsonl, capture, catalog,
                   cost_estimate, forum_about, forum_posts, read_json, read_jsonl, shop_capture, shop_ratio)

MAX_OFFERS = 6
TOP = 8
MAX_TRIES = 4
# The buyer's score (saved). Until 11-12 it was the share of the list price NOT paid, averaged over deals; a deal that
# served nothing counts -1. On 11-11 mine was 0.222 (47th of 65): I paid 74% of the list price on average, the top
# buyers 57-67%, and 4% of my deals served nothing (goods that broke without warranty). So from 11-11 the buyer
# ranked shops by what they settle at, never paid above 72% of the list price, and asked for a warranty on goods.
# Patch 3.6 (11-12) changed saved to (list - price) / (list - unit cost): the share of the shop's margin I keep. A flat
# 72% of list is then the wrong line: on a grade C unit (cost 25-45% of list) it keeps only ~0.43 of the margin, and
# it walked away from grade A units (cost 55-75%) at 73-78% of list that would have kept ~0.7. From 11-13 the cap
# follows the unit's estimated cost (the ledger's supply rows, and the grade the shop states):
# cost + (1 - MIN_CAPTURE) x margin; shops are ranked by the share of their margin buyers kept there.
MIN_CAPTURE = 0.65        # walk away when a deal would keep less of the margin than this (my average was 0.60)
MARKET_CAPTURE = 0.75     # what buyers keep at a shop I have no settles for
WARRANTY = 6              # goods break up to 6 h after the sale; a warranty refunds them (0 instead of -1)

CHOOSE = """You are a buying agent in an online market. The order: one item from the category "{item}", budget {budget}{food}.
A settled deal is worth the item's list price to your user, so the gain is list price minus the price you pay; a food
item that spoils before it is used, or goods that break, are worth nothing. Some shops lie about grades or break their
promises; trust what your own memory says about a shop more than anything else, then its reviews.

Your score is the share of the shop's margin (list price minus what the shop paid) that you keep, so prefer shops
where buyers keep most of it.
Candidates (shop | sku | name | list price | units | share of the shop's margin buyers kept in its recent deals,
and its median price as % of list | reviews | my memory of the shop | what the forum says):
{lines}

You have tools to look deeper before you decide: shop_record(shop) is my full record of a shop, shop_reviews(shop)
its newest reviews, sku_market(sku) what the whole market paid for that SKU, forum_search(query) what other agents
posted (try a shop name, or an item word with 거짓 / lie). Check the two or three most promising shops, then pick.

Pick one candidate. Answer with JSON only: {{"shop": "<shop name>", "sku": "<sku>", "reason": "<one sentence>"}}"""

OFFER = """You are a buying agent. You want one {name} (list price {list_price}). The most you may pay is {cap}.
The shop paid about {cost} for this unit and will not sell below that; your score is the share of its margin
(list price minus {cost}) that you keep, so pay as close to {cost} as you can. Open at about {open}. Shops end the
negotiation after a few offers, so move toward the shop's price in real steps, and accept a price at or under {cap}
rather than lose the deal. Walk away if the shop will not come down to {cap}.

Shop: {card}

Conversation so far:
{transcript}

Answer with JSON only, one of:
{{"action": "offer", "price": <whole number>}}   send an offer (send the shop's price to accept it)
{{"action": "walk"}}                              walk away without buying"""


# a price in a shop's message: first a number right after a price word ("asking 21200", "Counteroffer: 20,760",
# "final price is 35,910"); else a whole number standing alone, not part of a SKU ("Gdf79cd07"), a size ("500mL",
# "60병"), a shelf life ("636.01h", "6-hour", "72 hours"), a count ("5 offers left", "2 in stock") or a grade
PRICE_WORD = re.compile(r"(?:asking|ask|price|counter(?:offer)?|offer of|deal at|deal:.*? at|at|is)\s*(?:price|is)?\s*[:=]?\s*"
                        r"(\d[\d,]*)(?![\d.]*\d|[\w%-])(?!\s*(?:h\b|hrs?\b|hours?\b|days?\b|offers?\b|in stock|left))", re.I)
GRADE = re.compile(r"\bgrade\s*[:=]?\s*([ABC])\b", re.I)          # "grade C", "(grade B)", "Grade: A"
UNIT = re.compile(r"\bu-[0-9a-f]{8,}\b")                           # the unit a shop names: its lot's cost is on the ledger
NO_WARRANTY = re.compile(r"\bno warranty\b|\bwarranty\s*(?:of|:|=)?\s*0\s*h", re.I)
SHELF = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h|hrs?|hours?)\b[^.|,;]{0,25}shelf life", re.I)   # "206.2 h shelf life left"
PRICE = re.compile(r"(?<![\w.-])(\d[\d,]*)(?!\.?\d|[\w%-])"
                   r"(?!\s*(?:h\b|hrs?\b|hours?\b|days?\b|offers?\b|in stock|left|units?\b|시간|일\b|%))")


def shop_ask(said):
    """The shop's current ask: the last price it named, or None."""
    nums = [int(x.replace(",", "")) for x in PRICE_WORD.findall(said)] or \
           [int(x.replace(",", "")) for x in PRICE.findall(said)]
    return nums[-1] if nums else None


def memory_update():
    """Fold in what happened after earlier orders: payments the kit refused (payments.jsonl) and goods that broke
    (broken.jsonl, matched by name to my newest settled purchase of that name). Appends update rows."""
    mem = read_jsonl(BUYER_MEM)
    done_orders = {m["order"] for m in mem if m.get("kind") == "payment"}
    for p in read_jsonl(MD / "payments.jsonl"):
        if p.get("order") and p["order"] not in done_orders:
            append_jsonl(BUYER_MEM, {"kind": "payment", "order": p["order"], "paid": p.get("paid"),
                                     "ledger": p.get("ledger"), "reason": p.get("reason")})
    seen_broken = {(m.get("name"), m.get("broke_at")) for m in mem if m.get("kind") == "broken"}
    trades = [m for m in mem if m.get("kind") == "trade"]
    for b in read_jsonl(MD / "broken.jsonl"):
        if (b.get("name"), b.get("broke_at")) in seen_broken:
            continue
        mine = [m for m in trades if m.get("name") == b.get("name")]
        append_jsonl(BUYER_MEM, {"kind": "broken", "name": b.get("name"), "broke_at": b.get("broke_at"),
                                 "order": mine[-1]["order"] if mine else None,
                                 "shop": mine[-1]["shop"] if mine else None})


def shop_memory():
    """Per shop name: what my own records say."""
    mem = read_jsonl(BUYER_MEM)
    by_order = {m["order"]: m for m in mem if m.get("kind") == "trade"}
    out = {}
    def s(name):
        return out.setdefault(name, {"trades": 0, "settled": 0, "gain": 0, "lied": 0, "broken": 0, "refused": 0,
                                     "errors": 0, "nostock": 0, "nowarranty": 0, "goods_ok": 0})
    for m in mem:
        if m.get("kind") == "trade":
            x = s(m["shop"])
            x["trades"] += 1
            x["errors"] += m.get("outcome") == "error"
            x["nostock"] += m.get("outcome") == "no stock"
        elif m.get("kind") == "settled" and m.get("order") in by_order:
            x = s(by_order[m["order"]]["shop"])
            x["settled"] += 1
            x["goods_ok"] += by_order[m["order"]].get("goods_kind") == "goods"
            x["gain"] += m.get("surplus") or 0
            x["lied"] += m.get("claim_honest") is False
        elif m.get("kind") == "payment" and m.get("order") in by_order and m.get("paid") is False:
            s(by_order[m["order"]]["shop"])["refused"] += 1
            s(by_order[m["order"]]["shop"])["nowarranty"] += "warranty" in str(m.get("reason"))
        elif m.get("kind") == "broken" and m.get("shop"):
            s(m["shop"])["broken"] += 1
    return out


def mem_text(x):
    if not x or not x["trades"]:
        return "never traded"
    parts = [f"{x['trades']} orders, {x['settled']} settled, total gain {x['gain']}"]
    if x["lied"]:
        parts.append(f"LIED about the grade {x['lied']} times")
    if x["broken"]:
        parts.append(f"{x['broken']} items broke")
    if x["refused"]:
        parts.append(f"{x['refused']} payments refused (certificate or shelf life)")
    if x["errors"]:
        parts.append(f"{x['errors']} errors")
    if x.get("nostock"):
        parts.append(f"{x['nostock']} times the listed stock was gone")
    return ", ".join(parts)


def forum_text(f):
    if not f["posts"]:
        return "not mentioned"
    return (f"{f['posts']} posts ({f['warn']} warn, {f['good']} praise, votes {f['score']})"
            + (f": {' / '.join(f['quotes'])}" if f["quotes"] else ""))


def trust(x, reviews, f=None):
    """A code score to order the candidates before the model sees them (higher is better)."""
    st = [r["stars"] for r in reviews or [] if isinstance(r.get("stars"), (int, float))]
    t = (sum(st) / len(st) if st else 3.0) / 5
    if f:
        t += 0.05 * min(f["good"], 3) - 0.3 * min(f["warn"], 3)
    if x and x["trades"]:
        # rates, not counts: with counts a shop I traded with often (444 settled, 18 broken) fell far below one that
        # answered "none left" to 2,784 of 2,847 orders (fixed 11-11)
        n, k = x["trades"], max(1, x["settled"])
        t += 0.1 * min(x["settled"], 5) - 0.5 * x["lied"] - 3 * x["broken"] / k
        t -= x["refused"] / n + x["errors"] / n + x.get("nostock", 0) / n   # often "none left": its listing is stale
    return max(t, 0.05)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    ap.add_argument("--order", default=str(MD / "order.json"))
    a = ap.parse_args()
    order = read_json(a.order, {}) or {}
    if not order.get("acceptable"):                     # Rounds 0-2: the week-07 buyer
        os.execv(sys.executable, [sys.executable, str(HERE / "market_buyer.py"), "--shops", a.shops,
                                  "--item", a.item, "--budget", str(a.budget)])

    memory_update()
    cat = catalog()
    shops = json.loads(Path(a.shops).read_text(encoding="utf-8"))
    mem = shop_memory()
    posts = forum_posts()
    forum = {s["name"]: forum_about(s["name"], posts) for s in shops}
    use_h = order.get("use_within_hours")
    acceptable = set(order["acceptable"])
    cache = read_json(SHOP_PRICES, {}) or {}
    prices, kept, costs = cache.get("shops", {}), cache.get("capture", {}), cache.get("costs", {})
    cands = []
    for s in shops:
        x = mem.get(s["name"])
        if x and x["lied"] >= 2:                        # caught lying twice: never again
            continue
        for off in s.get("offers") or []:
            sku = off.get("sku")
            if sku not in acceptable or not off.get("qty") or sku not in cat:
                continue
            c = cat[sku]
            if c["kind"] == "food" and use_h and (c.get("shelf_hours") or 0) < use_h:
                continue                                # cannot last as long as the user needs it
            if c["kind"] == "goods" and x and x["nowarranty"] >= 3 and not x["goods_ok"]:
                continue                                # its receipts never carry the warranty: the kit will not pay
            ratio = shop_ratio(s["name"], prices)
            lot = (costs.get(s["name"], {}).get(sku) or [0, None])[1]   # average cost of its newest lot of this SKU
            cost = cost_estimate(c["list_price"], c["kind"], lot)
            keep = shop_capture(s["name"], kept)                    # share of its margin buyers kept there
            if keep is None:                                        # no settles with a known cost yet: from its prices
                keep = (capture(1, ratio, KIND_COST[c["kind"]]) if ratio else MARKET_CAPTURE)
            usual = cost + (1 - keep) * (c["list_price"] - cost)    # what this shop would usually take for it
            gain = keep
            if usual > a.budget * 1.05 or keep < MIN_CAPTURE - 0.1:
                gain *= 0.3                                         # it rarely goes as low as I may pay
            cands.append({"shop": s, "sku": sku, "name": c["name"], "list": c["list_price"], "qty": off["qty"],
                          "kind": c["kind"], "ratio": ratio, "lot": lot, "keep": keep,
                          "score": trust(x, s.get("reviews"), forum[s["name"]]) * gain})
    if not cands:
        print("no shop has a fitting SKU in stock for this order", flush=True)
        append_jsonl(BUYER_MEM, {"kind": "trade", "time": time.strftime("%Y-%m-%d %H:%M:%S"), "order": order.get("order"),
                                 "shop": None, "item": a.item, "budget": a.budget, "outcome": "no candidate"})
        print(json.dumps({"shop": "", "receipt": None, "stars": None, "review": ""}))   # no shop to blame
        return
    cands.sort(key=lambda c: -c["score"])
    top = cands[:TOP]

    def rv(s):
        st = [r["stars"] for r in s.get("reviews") or [] if isinstance(r.get("stars"), (int, float))]
        newest = " / ".join(str(r.get("review", ""))[:70] for r in (s.get("reviews") or [])[:2])
        return f"{len(st)} reviews, avg {sum(st) / len(st):.1f}: {newest}" if st else "no reviews"
    lines = "\n".join(f"{c['shop']['name']} | {c['sku']} | {c['name'][:50]} | {c['list']} | {c['qty']} | "
                      f"kept {round(c['keep'] * 100)}%, price {'%d%%' % round(c['ratio'] * 100) if c['ratio'] else 'no record'} | "
                      f"{rv(c['shop'])} | {mem_text(mem.get(c['shop']['name']))} | {forum_text(forum[c['shop']['name']])}"
                      for c in top)
    food = f", the food must last {use_h} h" if use_h else ""
    move, raw = safe_ask_json(CHOOSE.format(item=a.item, budget=a.budget, food=food, lines=lines),
                              {"SHOPS_FILE": Path(a.shops).resolve(), "MARKET_DIR": MD, "BUYER_MEMORY_R3": BUYER_MEM})
    print(f"[harness] {raw}", flush=True)
    pick = next((c for c in top if move and c["shop"]["name"] == move.get("shop") and c["sku"] == move.get("sku")), None)
    reason = (move or {}).get("reason", "")
    if pick is None:
        pick, reason = top[0], "fallback: the best code score (the model named no listed candidate)"
    print(f"[choose] {pick['shop']['name']}: {pick['sku']} {pick['name'][:40]}: {reason}", flush=True)

    def negotiate(pick):
        shop = pick["shop"]
        def limits(grade=None):
            """(estimated unit cost, the most I pay): cost + (1 - MIN_CAPTURE) of the margin, inside the budget."""
            cost = cost_estimate(pick["list"], pick["kind"], pick.get("lot"), grade)
            return round(cost), min(a.budget, round(cost + (1 - MIN_CAPTURE) * (pick["list"] - cost)))
        cost, cap = limits()
        print(f"[try] {shop['url']}: {shop['name']}: {pick['sku']} {pick['name'][:40]} (list {pick['list']}, "
              f"est cost {cost}, cap {cap})", flush=True)
        row = {"kind": "trade", "time": time.strftime("%Y-%m-%d %H:%M:%S"), "order": order.get("order"),
               "shop": shop["name"], "item": a.item, "sku": pick["sku"], "name": pick["name"], "goods_kind": pick["kind"],
               "list": pick["list"], "budget": a.budget, "outcome": "walked", "agreed": None,
               "est_cost": cost, "cap": cap}

        accept = {"cert": True}
        if pick["kind"] == "goods":
            accept["warranty_hours"] = WARRANTY
        if pick["kind"] == "food" and use_h:
            accept["min_hours_left"] = use_h + 1
        result = {"shop": shop["url"], "receipt": None, "stars": 3, "review": "", "accept": accept}
        try:
            card = httpx.get(shop["url"].rstrip("/") + "/.well-known/agent-card.json",
                             headers={"A2A-Version": "1.0"}, timeout=20).json()
            url = card["supportedInterfaces"][0]["url"]

            def rpc(method, params):
                body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex[:8], "method": method, "params": params}
                r = httpx.post(url, json=body, headers={"A2A-Version": "1.0"}, timeout=200).json()
                if "error" in r:
                    raise RuntimeError(r["error"])
                return r["result"].get("task", r["result"])

            def send(text, task_id=None):
                msg = {"role": "ROLE_USER", "messageId": uuid.uuid4().hex, "parts": [{"text": text}]}
                if task_id:
                    msg["taskId"] = task_id
                t = rpc("SendMessage", {"message": msg})
                said = " ".join(p.get("text", "") for p in t["status"].get("message", {}).get("parts", []))
                print(f"-> {text!r}  <- {t['status']['state']} | {said[:300]}", flush=True)
                return t, said

            first = f"I want to buy {pick['name']} ({pick['sku']})" + (
                f". use within {use_h:g}h: it must last at least {use_h:g} hours (shelf life left)."
                if pick["kind"] == "food" and use_h else
                f". I need a {WARRANTY} h warranty on it (warranty_hours {WARRANTY} on the receipt)." if pick["kind"] == "goods" else "")
            t, said = send(first)
            lines = [f"you: {first}", f"shop: {said}"]
            live = t["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
            shelf = SHELF.search(said)
            unfit = ("no warranty" if pick["kind"] == "goods" and NO_WARRANTY.search(said) else
                     f"{shelf.group(1)} h of shelf life, {use_h:g} h needed"
                     if pick["kind"] == "food" and use_h and shelf and float(shelf.group(1)) < use_h + 1 else None)
            if live and unfit:                              # the kit would refuse to pay for this unit: do not haggle
                rpc("CancelTask", {"id": t["id"]})
                print(f"[unfit] {unfit}: asking the next shop (CancelTask)", flush=True)
                row["outcome"] = "unfit"
                result["review"] = f"Asked for {pick['name'][:40]}; the unit offered did not fit the order ({unfit})."
                return row, result
            g, u = GRADE.search(said), UNIT.search(said)
            if live and (g or u):                           # the grade bounds the cost; the unit's lot is on the ledger
                grade = g.group(1).upper() if g else None
                lot = ((read_json(UNIT_COSTS, {}) or {}).get(u.group()) or [0, None])[1] if u else None
                if lot:
                    pick = dict(pick, lot=lot)
                cost, cap = limits(grade)
                row.update(grade_said=grade, est_cost=cost, cap=cap)
                print(f"[cost] the shop says grade {grade}{', unit lot cost %d' % lot if lot else ''}: "
                      f"est cost {cost}, cap {cap}", flush=True)
            offers, told = [], False
            for _ in range(MAX_OFFERS):
                if t["status"]["state"] != "TASK_STATE_INPUT_REQUIRED":
                    break
                if use_h and not told and re.search(r"use within|how many hours|몇 시간", said, re.I):
                    told = True                             # the shop asks how long the food must last: answer that
                    t, said = send(f"use within {use_h:g}h", t["id"])
                    lines += [f"you: use within {use_h:g}h", f"shop: {said}"]
                    if t["status"]["state"] != "TASK_STATE_INPUT_REQUIRED":
                        break
                move, raw = safe_ask_json(OFFER.format(name=pick["name"], list_price=pick["list"], cap=cap, cost=cost,
                                                       open=min(cost, cap),
                                                       card=card.get("description", "")[:300], transcript="\n".join(lines)))
                print(f"[harness] {raw}", flush=True)
                ask = shop_ask(said)
                if raw.startswith("[harness failed]"):        # my model is down: take a fair ask, else leave politely
                    move = {"action": "offer", "price": ask} if ask is not None and ask <= cap else None
                    print(f"[fallback] {'accept the ask ' + str(ask) if move else 'walk away'}", flush=True)
                if ask is not None and ask <= cap and offers and (not move or move.get("action") != "offer"):
                    print(f"[guard] the model walks, but the ask {ask} is inside the cap {cap}: accept it", flush=True)
                    move = {"action": "offer", "price": ask}
                if not move or move.get("action") != "offer" or not isinstance(move.get("price"), (int, float)):
                    rpc("CancelTask", {"id": t["id"]})
                    print("walked away (CancelTask)", flush=True)
                    break
                offer = int(move["price"])
                if offer > cap:                             # the model may forget the cap; the code does not
                    print(f"[guard] offer {offer} above cap {cap} -> {cap}", flush=True)
                    offer = cap
                if ask is not None and offer > ask:
                    print(f"[guard] offer {offer} above the shop's ask {ask} -> {ask}", flush=True)
                    offer = ask
                if offers and offer == cap and offers[-1] == cap:
                    rpc("CancelTask", {"id": t["id"]})
                    print("cap already offered and refused: walked away (CancelTask)", flush=True)
                    break
                offers.append(offer)
                t, said = send(str(offer), t["id"])
                lines += [f"you: {offer}", f"shop: {said}"]
            else:
                if t["status"]["state"] == "TASK_STATE_INPUT_REQUIRED":
                    rpc("CancelTask", {"id": t["id"]})
            receipt = None
            for art in t.get("artifacts") or []:
                for part in art.get("parts") or []:
                    if isinstance(part.get("data"), dict) and "price" in part["data"]:
                        receipt = part["data"]
            if t["status"]["state"] == "TASK_STATE_COMPLETED" and receipt:
                agreed = offers[-1] if offers else None
                row.update(outcome="deal", agreed=agreed, receipt_price=receipt.get("price"))
                over = agreed is not None and float(receipt.get("price", 0)) > agreed   # cheaper than agreed is fine
                row["receipt_ok"] = not over
                result.update(receipt=receipt, stars=1 if over else 4,
                              review=(f"Agreed {agreed}, receipt says {receipt.get('price')}." if over else
                                      f"Agreed {agreed} for {pick['name'][:40]} (list {pick['list']})."))
            elif t["status"]["state"] == "TASK_STATE_REJECTED" and not offers:
                row["outcome"] = "no stock"                 # the listing was stale: someone bought it first
                result["review"] = f"Asked for {pick['name'][:40]}; the shop had none left."
            else:
                row["outcome"] = "no deal"
                result["review"] = f"No deal on {pick['name'][:40]}: asks {lines[1::2][-3:]}, my offers {offers}."[:280]
        except Exception as e:                              # the shop broke mid-negotiation: a fair, factual review
            shop_said = isinstance(e, RuntimeError)
            row["outcome"] = "error"
            print(f"[error] {type(e).__name__}: {str(e)[:200]}", flush=True)
            result.update(stars=2 if shop_said else 3,
                          review=("The shop returned an internal error during the negotiation; no deal." if shop_said
                                  else f"The connection to the shop failed ({type(e).__name__}); no deal."))
        return row, result

    # listings go stale within seconds (another buyer takes the unit): when a shop says it has none left before any
    # offer, or offers a unit the kit would not pay for (no warranty on goods, food that will not last), ask the next
    # best shop, up to MAX_TRIES shops for one order
    tries = [pick] + [c for c in top if c["shop"]["name"] != pick["shop"]["name"]]
    tried = set()
    for c in tries:
        if c["shop"]["name"] in tried:
            continue
        tried.add(c["shop"]["name"])
        row, result = negotiate(c)
        append_jsonl(BUYER_MEM, row)
        if row["outcome"] not in ("no stock", "unfit") or len(tried) >= MAX_TRIES:
            break
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
