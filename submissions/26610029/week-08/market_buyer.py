"""real_yellow's Round 3 buyer: a frugal shopper that picks shops from the shared DB, not from a harness.

    python market_buyer.py --shops .market/shops.json --item 침구 --budget 162700 --order .market/order.json

1. Every shop's offers for this order go into the DB (offers_seen): what competitors stock feeds our own shop.
2. Shops that stock an acceptable SKU are scored from the market's settles (price vs list, honest claims,
   breaks) and our own past tries with them; the pick is one of the top three, weighted, so we do not all pile
   onto one shop.
3. Haggling is a concession curve in code, from a target under the market's going rate up to min(ask, budget).
4. accept: food has to last use_within_hours, or the deal is worth nothing (it would count -1 in saved rate).

Week 7's harness-picked buyer fell back to a random shop whenever the reply did not parse, and under load it
almost always did (Round 3, Nov 3: 22 orders, 0 deals). Nothing here waits on a harness.
"""
import argparse
import json
import math
import random
import re
import time
from pathlib import Path

from buyer import fetch_card, receipt_data, rpc, send, state, status_text
from db import DB

HERE = Path(__file__).resolve().parent
SELF = "real_yellow"                      # same owner: our own shop can never settle with us
BLACKLIST_PATH = HERE / "market_buyer_blacklist.json"
DEFAULT_POLICY = {"target_ratio": 0.55,   # first offer, as a share of list price
                  "rounds": 6, "tries": 2}
GOODS_WARRANTY_MIN_PRICE = 15000          # require a warranty on goods at or above this list price
GOODS_WARRANTY_BREAK_RATE = 0.03          # ...or from a shop whose own measured break rate exceeds this


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def load_jsonl(path):
    try:
        return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return []


def parse_ask(text, list_price):
    """The seller's price in its message. Shops write it differently, and product names carry numbers too
    (500mL x 60), so prefer a number right after asking/counter/price, else the last plausible one."""
    clean = text.replace(",", "")
    m = re.findall(r"(?:ask\w*|counter\w*|price|offer)\D{0,12}?(\d+(?:\.\d+)?)", clean, re.I)
    if m:
        return math.ceil(float(m[-1]))
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", clean)]
    plausible = [n for n in nums if 0.2 * list_price <= n <= 2 * list_price]
    return math.ceil(plausible[-1]) if plausible else None


def haggle(url, sku, name, list_price, budget, target_ratio, rounds, need_h=None):
    card = fetch_card(url)
    rpc_url = ((card.get("supportedInterfaces") or [{}])[0]).get("url") or url
    ask = f"I want to buy {name} ({sku})"
    if need_h:                                   # a shop that reads it can ship a fresher unit
        ask += f", with at least {int(need_h)} hours of shelf life left"
    t, _ = send(rpc_url, ask)
    task_id = (t or {}).get("id")
    start = min(budget, int(list_price * target_ratio))
    offers, offer = [], None
    for k in range(rounds):
        if state(t) != "TASK_STATE_INPUT_REQUIRED":
            break
        ask = parse_ask(status_text(t), list_price)
        if ask is None:
            break
        top = min(ask, budget)
        if ask <= start * 1.03:
            offer = ask                                   # already at our target: take it
        elif k == rounds - 1:
            offer = top                                   # last round: best we can do inside the budget
        else:
            offer = int(start + (top - start) * k / (rounds - 1))
        offers.append(offer)
        t, _ = send(rpc_url, str(offer), task_id)
        if t is None:
            break
    if state(t) == "TASK_STATE_COMPLETED":
        r = receipt_data(t) or {}
        honest = (bool(r.get("sig_seller")) and r.get("item") == sku and offer is not None
                  and abs(float(r.get("price", -1)) - offer) < 1e-6)   # unsigned: the kit will never pay it
        return {"deal": True, "receipt": r, "price": r.get("price"), "offers": offers, "honest": honest,
                "agreed": offer}
    if task_id and state(t) not in ("TASK_STATE_REJECTED", "TASK_STATE_CANCELED", ""):
        rpc(rpc_url, "CancelTask", {"id": task_id})
    return {"deal": False, "receipt": None, "price": None, "offers": offers}


def shop_scores(d, cands):
    """(name -> score, name -> break_rate). Unknown shops get a neutral prior instead of zero, so new
    shops still get tried. The break rate is returned separately too: picking a shop and deciding whether
    to insist on a warranty from it are two different questions and both want this same number."""
    names = [c["name"] for c in cands]
    # by name, through every URL the collector ever saw for that shop: a tunnel URL changes on each restart
    market = {r["name"]: r for r in d.q(
        "SELECT sh.name, count(*) AS n, avg(s.amount::float / s.list_price) AS ratio, "
        "avg(CASE WHEN s.claim_honest IS FALSE THEN 0 ELSE 1 END) AS honest, count(b.unit) AS broke "
        "FROM settles s JOIN shops sh ON sh.url = s.shop_url LEFT JOIN broken b ON b.unit = s.unit "
        "WHERE sh.name = ANY($1) AND s.list_price > 0 GROUP BY sh.name", names)}
    mine = {r["shop"]: r for r in d.q(
        "SELECT context->>'shop' AS shop, count(*) AS n, "
        "sum(CASE WHEN (outcome->>'deal')::boolean THEN 1 ELSE 0 END) AS ok "
        "FROM trajectory WHERE role = 'buyer' AND context->>'shop' = ANY($1) GROUP BY 1", names)}
    scores, break_rates = {}, {}
    for c in cands:
        m, me = market.get(c["name"]), mine.get(c["name"])
        n = int(m["n"]) if m else 0
        ratio = float(m["ratio"]) if m else 0.65
        honest = float(m["honest"]) if m else 0.9
        brk = int(m["broke"]) / n if n else 0.02
        my_rate = (int(me["ok"]) + 1) / (int(me["n"]) + 2) if me else 0.5
        scores[c["name"]] = (1 - ratio) + 0.3 * honest + 0.3 * my_rate - 3 * brk + 0.03 * math.log1p(n)
        break_rates[c["name"]] = brk
    return scores, break_rates


def pick_sku(offers, catalog, budget):
    """Among this shop's acceptable SKUs: the priciest one whose list price fits the budget (value is the
    list price, and paying up to list never breaks the budget), else the cheapest."""
    priced = [(catalog.get(o["sku"], {}).get("list_price") or 0, o) for o in offers]
    fits = [p for p in priced if 0 < p[0] <= budget]
    lp, o = max(fits, key=lambda p: p[0]) if fits else min(priced, key=lambda p: p[0] or float("inf"))
    return o["sku"], o["name"], lp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    ap.add_argument("--order", default=str(HERE / ".market" / "order.json"))
    args = ap.parse_args()

    order = load_json(args.order, {})
    shops = load_json(args.shops, [])
    catalog = load_json(HERE / ".market" / "catalog.json", {})
    blacklist = load_json(BLACKLIST_PATH, {})
    acceptable = set(order.get("acceptable") or [])
    kind = order.get("kind")

    need_h = order.get("use_within_hours") if kind == "food" else None

    def lasts(sku):
        """Even a grade-A unit has only >= 80% of its shelf life left: skip SKUs that cannot last the order."""
        shelf = (catalog.get(sku) or {}).get("shelf_hours")
        return not need_h or not shelf or shelf * 0.8 >= need_h

    cands = []
    for s in shops:
        if s["name"] == SELF or s["name"] in blacklist:
            continue
        offers = [o for o in s.get("offers") or [] if o.get("qty", 0) > 0
                  and (not acceptable or o["sku"] in acceptable) and lasts(o["sku"])]
        if offers:
            cands.append(dict(s, offers=offers))

    if not cands:
        print(f"[choose] no shop stocks an acceptable SKU for {args.item} ({len(shops)} shops)")
        try:
            with DB() as d:
                # a category restock's own settles-based ranking will never discover on its own: nobody
                # has ever sold it, so it never looks like "something that sells". Give restock this signal.
                d.log_trajectory(time.time(), "buyer", "stockout",
                                 {"category": order.get("category"), "item": args.item, "kind": kind}, None)
        except Exception as e:
            print(f"[db] stockout not logged: {type(e).__name__}: {e}")
        print(json.dumps({"shop": None, "receipt": None, "stars": 3, "review": "Nobody had it in stock."}))
        return

    policy, scores, break_rates = dict(DEFAULT_POLICY), {}, {}
    try:
        with DB() as d:
            now = time.time()
            d.batch(("INSERT INTO offers_seen VALUES ($1, $2, $3, $4, $5, $6)",
                     (now, s["name"], o["sku"], o["name"], o["qty"], order.get("category")))
                    for s in shops for o in s.get("offers") or [])
            policy.update(d.get_policy("buyer", {}))
            scores, break_rates = shop_scores(d, cands)
    except Exception as e:                       # no DB: still buy, with every shop on the prior
        print(f"[db] {type(e).__name__}: {e}")

    # the kit refused to pay these shops before (short shelf life, no certificate...): push them down
    refused = {}
    for p in load_jsonl(HERE / ".market" / "payments.jsonl"):
        if p.get("paid") is False:
            refused[p.get("shop")] = refused.get(p.get("shop"), 0) + 1
    for c in cands:
        if refused.get(c["url"]):
            scores[c["name"]] = scores.get(c["name"], 0.6) - 0.2 * min(refused[c["url"]], 3)

    ranked = sorted(cands, key=lambda c: scores.get(c["name"], 0.6), reverse=True)
    top = ranked[:3]
    first = random.choices(top, weights=[0.6, 0.25, 0.15][:len(top)])[0]
    queue = [first] + [c for c in ranked if c is not first]

    result, shop, sku, name, lp = {"deal": False}, None, None, None, 0
    for shop in queue[:policy["tries"]]:
        sku, name, lp = pick_sku(shop["offers"], catalog, args.budget)
        print(f"[choose] {shop['name']} score {scores.get(shop['name'], 0.6):.3f}: {name} ({sku}), list {lp}")
        try:
            result = haggle(shop["url"], sku, name, lp, args.budget, policy["target_ratio"], policy["rounds"], need_h)
        except Exception as e:                   # a dead tunnel or a bad card: try the next shop
            print(f"[haggle] {shop['name']}: {type(e).__name__}: {e}")
            result = {"deal": False, "offers": []}
        if result["deal"]:
            break

    if result["deal"] and not result["honest"]:
        r = result["receipt"]
        why = ("receipt carried no sig_seller" if not r.get("sig_seller") else
               f"receipt said {r.get('item')} at {r.get('price')}, agreed {sku} at {result['agreed']}")
        blacklist[shop["name"]] = why
        BLACKLIST_PATH.write_text(json.dumps(blacklist, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[flag] {shop['name']}: {why}")

    try:
        with DB() as d:
            d.log_trajectory(time.time(), "buyer", "buy",
                             {"order": order.get("order"), "item": args.item, "kind": kind, "budget": args.budget,
                              "shop": shop["name"], "sku": sku, "list": lp, "score": scores.get(shop["name"])},
                             {"deal": result["deal"], "price": result.get("price"), "offers": result.get("offers")})
    except Exception as e:
        print(f"[db] trajectory not written: {type(e).__name__}")

    accept = {"cert": True, "min_hours_left": order["use_within_hours"]} if kind == "food" and order.get("use_within_hours") else {}
    shop_break_rate = break_rates.get(shop["name"], 0.02) if shop else 0.02
    if kind != "food" and (lp >= GOODS_WARRANTY_MIN_PRICE or shop_break_rate > GOODS_WARRANTY_BREAK_RATE):
        # unwarrantied goods that later broke cost 1,090,586 won total with zero refund (measured live):
        # require a warranty on pricier goods, or on ANY goods from a shop whose own measured break rate
        # is already high (e.g. gom31-certified-goods at 4.25%, well above the market's usual ~1-2%), so
        # a break comes out of the shop's balance, not ours (agent.md: surplus = value - price + refund,
        # so an unwarrantied break is -price)
        accept["warranty_hours"] = 6

    if result["deal"]:
        ratio = result["price"] / lp if lp else 1
        stars = 5 if ratio <= 0.6 else 4 if ratio <= 0.72 else 3
        review = f"{name} at {result['price']} (list {lp}, {round((1 - ratio) * 100)}% off)."
    else:
        stars, review = 3, "No deal at a price inside my budget."
    print(json.dumps({"shop": shop["url"], "receipt": result["receipt"] if result["deal"] else None,
                      "stars": stars, "review": review, "accept": accept}, ensure_ascii=False))


if __name__ == "__main__":
    main()
