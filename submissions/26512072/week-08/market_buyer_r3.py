"""Round 3 market buyer: picks a shop and a SKU, negotiates over A2A, and prints the result line for the kit.

    python market_buyer_r3.py --shops .market/shops.json --item 김치 --budget 52000 --order .market/order.json

Before Round 3 (orders without "acceptable" SKUs) it hands over to the week-07 market_buyer.py.

Context the model sees is built by code, one line per candidate (shop x SKU): the SKU's list price (its value to
the order), the shop's reviews, and my own memory of that shop (settled deals, dishonest grade claims, goods that
broke, refused payments, errors). The model (harness.py, claude -p haiku) picks the candidate and makes every offer.
Code enforces: offers never above min(budget, list price); food that cannot last use_within_hours is never a
candidate; the payment policy ("accept") asks for the market certificate and enough shelf life.
Memory: buyer_memory_r3.jsonl, one line per order, updated later by review_r3.py (what settled) and here (payments
the kit refused, goods that broke).
"""
import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

from harness import ask_json
from r3lib import BUYER_MEM, HERE, MD, append_jsonl, catalog, read_json, read_jsonl

MAX_OFFERS = 6
TOP = 8

CHOOSE = """You are a buying agent in an online market. The order: one item from the category "{item}", budget {budget}{food}.
A settled deal is worth the item's list price to your user, so the gain is list price minus the price you pay; a food
item that spoils before it is used, or goods that break, are worth nothing. Some shops lie about grades or break their
promises; trust what your own memory says about a shop more than anything else, then its reviews.

Candidates (shop | sku | name | list price | units | reviews | my memory of the shop):
{lines}

Pick one candidate. Answer with JSON only: {{"shop": "<shop name>", "sku": "<sku>", "reason": "<one sentence>"}}"""

OFFER = """You are a buying agent. You want one {name} (list price {list_price}). The most you may pay is {cap}.
Pay as little as you can: every credit below the list price is your user's gain. Shops end the negotiation after a few
offers, so move toward the shop's price in real steps. Walk away if the shop will not come down to {cap}.

Shop: {card}

Conversation so far:
{transcript}

Answer with JSON only, one of:
{{"action": "offer", "price": <whole number>}}   send an offer (send the shop's price to accept it)
{{"action": "walk"}}                              walk away without buying"""


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
                                     "errors": 0})
    for m in mem:
        if m.get("kind") == "trade":
            x = s(m["shop"])
            x["trades"] += 1
            x["errors"] += m.get("outcome") == "error"
        elif m.get("kind") == "settled" and m.get("order") in by_order:
            x = s(by_order[m["order"]]["shop"])
            x["settled"] += 1
            x["gain"] += m.get("surplus") or 0
            x["lied"] += m.get("claim_honest") is False
        elif m.get("kind") == "payment" and m.get("order") in by_order and m.get("paid") is False:
            s(by_order[m["order"]]["shop"])["refused"] += 1
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
    return ", ".join(parts)


def trust(x, reviews):
    """A code score to order the candidates before the model sees them (higher is better)."""
    st = [r["stars"] for r in reviews or [] if isinstance(r.get("stars"), (int, float))]
    t = (sum(st) / len(st) if st else 3.0) / 5
    if x:
        t += 0.1 * min(x["settled"], 5) - 0.5 * x["lied"] - 0.3 * x["broken"] - 0.2 * x["refused"] - 0.2 * x["errors"]
    return t


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
    use_h = order.get("use_within_hours")
    acceptable = set(order["acceptable"])
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
            cands.append({"shop": s, "sku": sku, "name": c["name"], "list": c["list_price"], "qty": off["qty"],
                          "kind": c["kind"], "score": trust(x, s.get("reviews")) * min(c["list_price"], a.budget * 1.5)})
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
                      f"{rv(c['shop'])} | {mem_text(mem.get(c['shop']['name']))}" for c in top)
    food = f", the food must last {use_h} h" if use_h else ""
    move, raw = ask_json(CHOOSE.format(item=a.item, budget=a.budget, food=food, lines=lines))
    print(f"[harness] {raw}", flush=True)
    pick = next((c for c in top if move and c["shop"]["name"] == move.get("shop") and c["sku"] == move.get("sku")), None)
    reason = (move or {}).get("reason", "")
    if pick is None:
        pick, reason = top[0], "fallback: the best code score (the model named no listed candidate)"
    shop = pick["shop"]
    cap = min(a.budget, pick["list"])                   # paying more than the list price is a loss for the user
    print(f"[choose] {shop['url']}: {shop['name']}: {pick['sku']} {pick['name'][:40]} (list {pick['list']}, cap {cap}): "
          f"{reason}", flush=True)
    row = {"kind": "trade", "time": time.strftime("%Y-%m-%d %H:%M:%S"), "order": order.get("order"),
           "shop": shop["name"], "item": a.item, "sku": pick["sku"], "name": pick["name"], "goods_kind": pick["kind"],
           "list": pick["list"], "budget": a.budget, "outcome": "walked", "agreed": None}

    accept = {"cert": True}
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

        t, said = send(f"I want to buy {pick['name']} ({pick['sku']})")
        lines = [f"you: I want to buy {pick['name']} ({pick['sku']})", f"shop: {said}"]
        offers = []
        for _ in range(MAX_OFFERS):
            if t["status"]["state"] != "TASK_STATE_INPUT_REQUIRED":
                break
            move, raw = ask_json(OFFER.format(name=pick["name"], list_price=pick["list"], cap=cap,
                                              card=card.get("description", "")[:300], transcript="\n".join(lines)))
            print(f"[harness] {raw}", flush=True)
            if not move or move.get("action") != "offer" or not isinstance(move.get("price"), (int, float)):
                rpc("CancelTask", {"id": t["id"]})
                print("walked away (CancelTask)", flush=True)
                break
            offer = int(move["price"])
            if offer > cap:                             # the model may forget the cap; the code does not
                print(f"[guard] offer {offer} above cap {cap} -> {cap}", flush=True)
                offer = cap
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
    append_jsonl(BUYER_MEM, row)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
