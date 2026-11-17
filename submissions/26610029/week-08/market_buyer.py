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
HAGGLE_ERRORS_PATH = HERE / ".market" / "haggle_errors.jsonl"
ZERO_OFFER_PATH = HERE / ".market" / "zero_offer.jsonl"
ZERO_OFFER_SKIP_MIN = 3    # skip a (shop, sku) pair after this many zero-offer failures -- measured live:
                           # (seochanit, G615a122d) went 20+ attempts in a row with offers=[] (never even
                           # made a first offer, not a price disagreement) while every other SKU from the
                           # same shop negotiated normally, so the fix targets the pair, not the whole shop
DEFAULT_POLICY = {"target_ratio": 0.55,   # first offer, as a share of list price (goods)
                  "food_target_ratio": 0.50,  # lower than goods: Round 3 patch 3.6 moved the buyer
                                              # "saved" score from discount-ratio to margin-based
                                              # ((list-paid)/(list-cost)), and measured live we were
                                              # already capturing ~92.8% of goods' available margin but
                                              # only ~84.4% of food's (avg paid ratio food 0.595 vs cost
                                              # ~0.52 of list, goods 0.638 vs cost ~0.61) -- food is also
                                              # the bulk of our order volume, so a harder first offer
                                              # there is where the new score formula pays off most
                  "rounds": 6, "tries": 2}
GOODS_WARRANTY_MIN_PRICE = 150000         # require a warranty on goods at or above this list price --
                                          # was 15,000: measured live, that threshold made us abandon
                                          # far more value in haggle-succeeded-but-never-paid goods deals
                                          # (~1.3-1.4M won/hour, mostly curtains/rugs 13k-160k whose
                                          # seller just wouldn't grant a warranty) than the one-time
                                          # 1,090,586 won break loss that justified requiring one at all.
                                          # Raised so the requirement only guards the few truly big-ticket
                                          # purchases (sofas, TVs, fridges: 300k-1.5M+) where an
                                          # unwarrantied break actually hurts
OWN_RATE_WINDOW_H = 24        # recency window for my_rate below -- a shop's recent swing (e.g. psyoung82
                              # measured live: 40.4% all-time -> 26%/24h -> 12-17%/1h across several
                              # hourly checks) should move its score faster than diluting it against
                              # everything we have ever tried with that shop
OWN_RATE_MIN_SAMPLE_RECENT = 5    # need this many recent tries before trusting the recent rate over all-time
OWN_RATE_HARD_FLOOR = 0.05         # a shop this bad, at this sample size, is not bad luck
OWN_RATE_HARD_MIN_SAMPLE = 20      # measured live: "ㅋㅋㅋ내이름뭐게" went 0/85 all-time on the same
                                   # fresh-fruit category yet kept scoring 0.57-0.58 and getting picked
                                   # into the top 3 every time, because my_rate is only 30% of the score
                                   # and the other terms (market ratio, honesty, low break) covered for it


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


def shop_scores(d, cands, now):
    """name -> score. Unknown shops get a neutral prior instead of zero, so new shops still get tried.
    A shop's own measured break rate already pulls its score down (-3 * brk below) -- that is the only
    place break rate should act: it used to also force a hard warranty requirement on any goods purchase
    from a high-break shop, which just blocked the purchase outright on a refusal (warranty_hours: 6 the
    shop wouldn't grant) instead of simply making us prefer a different shop when one was available."""
    names = [c["name"] for c in cands]
    # by name, through every URL the collector ever saw for that shop: a tunnel URL changes on each restart
    market = {r["name"]: r for r in d.q(
        "SELECT sh.name, count(*) AS n, avg(s.amount::float / s.list_price) AS ratio, "
        "avg(CASE WHEN s.claim_honest IS FALSE THEN 0 ELSE 1 END) AS honest, count(b.unit) AS broke "
        "FROM settles s JOIN shops sh ON sh.url = s.shop_url LEFT JOIN broken b ON b.unit = s.unit "
        "WHERE sh.name = ANY($1) AND s.list_price > 0 GROUP BY sh.name", names)}
    # all-time: used both as the fallback when we have too few recent tries, and as the hard-floor check
    # below (a shop that has been bad for a very long time and a very large sample is a structural no,
    # not a recent blip the window below would otherwise need time to pick up)
    mine_all = {r["shop"]: r for r in d.q(
        "SELECT context->>'shop' AS shop, count(*) AS n, "
        "sum(CASE WHEN (outcome->>'deal')::boolean THEN 1 ELSE 0 END) AS ok "
        "FROM trajectory WHERE role = 'buyer' AND context->>'shop' = ANY($1) GROUP BY 1", names)}
    mine_recent = {r["shop"]: r for r in d.q(
        "SELECT context->>'shop' AS shop, count(*) AS n, "
        "sum(CASE WHEN (outcome->>'deal')::boolean THEN 1 ELSE 0 END) AS ok "
        "FROM trajectory WHERE role = 'buyer' AND context->>'shop' = ANY($1) AND ts > $2 GROUP BY 1",
        names, now - OWN_RATE_WINDOW_H * 3600)}
    scores = {}
    for c in cands:
        m, all_, rec = market.get(c["name"]), mine_all.get(c["name"]), mine_recent.get(c["name"])
        n = int(m["n"]) if m else 0
        ratio = float(m["ratio"]) if m else 0.65
        honest = float(m["honest"]) if m else 0.9
        brk = int(m["broke"]) / n if n else 0.02
        rec_n = int(rec["n"]) if rec else 0
        if rec_n >= OWN_RATE_MIN_SAMPLE_RECENT:        # a recent trend outweighs the all-time average
            my_rate = (int(rec["ok"]) + 1) / (rec_n + 2)
        else:
            all_n = int(all_["n"]) if all_ else 0
            my_rate = (int(all_["ok"]) + 1) / (all_n + 2) if all_ else 0.5
        score = (1 - ratio) + 0.3 * honest + 0.3 * my_rate - 3 * brk + 0.03 * math.log1p(n)
        all_n = int(all_["n"]) if all_ else 0
        if all_n >= OWN_RATE_HARD_MIN_SAMPLE and (int(all_["ok"]) / all_n) < OWN_RATE_HARD_FLOOR:
            score -= 2      # a large, consistently near-zero sample: don't let a good market ratio or
                            # low break rate paper over a shop that structurally never closes with us
        scores[c["name"]] = score
    return scores


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

    zero_offer = {}
    for p in load_jsonl(ZERO_OFFER_PATH):
        key = (p.get("shop"), p.get("sku"))
        zero_offer[key] = zero_offer.get(key, 0) + 1

    cands = []
    for s in shops:
        if s["name"] == SELF or s["name"] in blacklist:
            continue
        offers = [o for o in s.get("offers") or [] if o.get("qty", 0) > 0
                  and (not acceptable or o["sku"] in acceptable) and lasts(o["sku"])
                  and zero_offer.get((s["url"], o["sku"]), 0) < ZERO_OFFER_SKIP_MIN]
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

    policy, scores = dict(DEFAULT_POLICY), {}
    try:
        with DB() as d:
            now = time.time()
            d.batch(("INSERT INTO offers_seen VALUES ($1, $2, $3, $4, $5, $6)",
                     (now, s["name"], o["sku"], o["name"], o["qty"], order.get("category")))
                    for s in shops for o in s.get("offers") or [])
            policy.update(d.get_policy("buyer", {}))
            scores = shop_scores(d, cands, now)
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

    # a shop whose haggle() call keeps raising (dead tunnel, timeout...) never reaches payment, so the
    # refused-payment penalty above never sees it and keeps re-trying it at full score (measured live:
    # totato2203-r3 timed out on us repeatedly across hours while its own score barely moved, 0.975 ->
    # 0.769, because nothing was tracking the exception itself)
    flaky = {}
    for p in load_jsonl(HAGGLE_ERRORS_PATH):
        flaky[p.get("shop")] = flaky.get(p.get("shop"), 0) + 1
    for c in cands:
        if flaky.get(c["url"]):
            # cap was 4 (max penalty -0.6): measured live, a shop mid-collapse (tunnel churn, dead
            # process...) can rack up 90-107 timeouts in a single hour while this penalty maxed out at
            # a fifth of that count, barely moving its score -- raised so a real burst of failures still
            # outweighs a good market ratio and low break rate
            scores[c["name"]] = scores.get(c["name"], 0.6) - 0.15 * min(flaky[c["url"]], 8)

    ranked = sorted(cands, key=lambda c: scores.get(c["name"], 0.6), reverse=True)
    top = ranked[:3]
    first = random.choices(top, weights=[0.6, 0.25, 0.15][:len(top)])[0]
    queue = [first] + [c for c in ranked if c is not first]

    target_ratio = policy["food_target_ratio"] if kind == "food" else policy["target_ratio"]

    result, shop, sku, name, lp = {"deal": False}, None, None, None, 0
    for shop in queue[:policy["tries"]]:
        sku, name, lp = pick_sku(shop["offers"], catalog, args.budget)
        print(f"[choose] {shop['name']} score {scores.get(shop['name'], 0.6):.3f}: {name} ({sku}), list {lp}")
        try:
            result = haggle(shop["url"], sku, name, lp, args.budget, target_ratio, policy["rounds"], need_h)
        except Exception as e:                   # a dead tunnel or a bad card: try the next shop
            print(f"[haggle] {shop['name']}: {type(e).__name__}: {e}")
            result = {"deal": False, "offers": []}
            try:
                with open(HAGGLE_ERRORS_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"shop": shop["url"], "error": type(e).__name__}, ensure_ascii=False) + "\n")
            except OSError:
                pass
        if not result["deal"] and not result.get("offers"):
            try:
                with open(ZERO_OFFER_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"shop": shop["url"], "sku": sku}, ensure_ascii=False) + "\n")
            except OSError:
                pass
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
    if kind != "food" and lp >= GOODS_WARRANTY_MIN_PRICE:
        # unwarrantied goods that later broke cost 1,090,586 won total with zero refund (measured live):
        # require a warranty on pricier goods, so a break comes out of the shop's balance, not ours
        # (agent.md: surplus = value - price + refund, so an unwarrantied break is -price). A high-break
        # shop is handled in shop_scores() instead (lower score -> picked less often): gating payment on
        # warranty_hours here too meant a flat refusal whenever that shop wouldn't grant it -- see
        # GOODS_WARRANTY_MIN_PRICE's comment for why the threshold itself moved from 15,000 to 150,000
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
