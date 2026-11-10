"""Round 3 market_buyer: picks (shop, SKU) by expected surplus from memory, negotiates over A2A, and hands the
kit an accept policy so it never pays for something that is worth 0 to the order.

    python3 buyer_r3.py --shops {shops} --item {item} --budget {budget} --order {order}

Score of a candidate = (list price - expected price) / list price * reliability (the market scores the share of
list saved, not won; until 2026-11-06 the score was in won and favoured big-ticket SKUs at smaller discounts), where
  expected price  = this shop's median settled price / list for the category (memory), else the market's, else 0.85
  reliability     = (1 - shop's dishonest-claim rate) * (1 - breaks and worthless deals we saw) * our payment success
The last JSON line is what the kit reads. Orders before Round 3 go to the week-07 buyer (market_buyer.py).
"""
import argparse
import json
import os
import re
import sys
import time

from r3lib import a2a, config, initiative, llm, market, pricing, score_model
from r3lib.memory import Memory
from shop_r3 import parse_offer

DEADLINE_S = 240          # the kit stops a buyer after 300 s
PER_SHOP_S = 90


def say(event, **kw):
    print(json.dumps({"event": event, **kw}, ensure_ascii=False), flush=True)


def decision_log(row):
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.LOG_DIR / "buyer-decisions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": round(time.time(), 1), **row}, ensure_ascii=False) + "\n")


def c_covered(rec):
    return ((rec or {}).get("warranty_hours") or 0) >= config.BUYER_C_MIN_WARRANTY_H


def accept_policy(order, rec=None):
    """What the kit checks before paying. Goods: grade C only when the receipt carries a warranty long enough to
    cover a break, so a C unit shipped against an A/B claim without one is not paid for either. A warranty the
    receipt promises is required of the seller's copy for every grade: the price we agreed to counted on it."""
    if order.get("kind") == "food":
        return {"cert": True, "min_hours_left": float(order.get("use_within_hours") or 0) + config.HOURS_MARGIN}
    if c_covered(rec):
        grades = list(config.BUYER_GRADES) if (rec or {}).get("claim") == "C" else \
            [g for g in config.BUYER_GRADES if g != "C"]
        return {"cert": True, "grades": grades, "warranty_hours": config.BUYER_C_MIN_WARRANTY_H}
    return {"cert": True, "grades": [g for g in config.BUYER_GRADES if g != "C"]}


GRADE_RE = re.compile(r"\bgrade\s*[:=]?\s*([ABC])\b|\b([ABC])\s*등급|등급\s*[:=]?\s*([ABC])\b", re.I)
WARRANTY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-\s*)?(?:h|hrs?|hours?|시간)\b[^.\n]{0,24}?(?:warranty|guarantee|보증)"
                         r"|(?:warranty|guarantee|보증)[^.\n\d]{0,30}?(\d+(?:\.\d+)?)\s*(?:h|hrs?|hours?|시간)", re.I)
NO_WARRANTY_RE = re.compile(r"\bno\s+warranty|without\s+(?:a\s+)?warranty|warranty\s*:?\s*(?:0\s*h|none|no)\b|보증\s*없", re.I)


def quote_hints(text):
    """(grade or None, warranty hours) a shop states in its reply. Only a hint for how far to bid: the receipt's
    claim and warranty_hours decide whether we pay (valid, accept_policy)."""
    text = text or ""
    m = GRADE_RE.search(text)
    grade = next((g.upper() for g in (m.groups() if m else ()) if g), None)
    hours = 0.0
    if not NO_WARRANTY_RE.search(text):
        w = WARRANTY_RE.search(text)
        hours = float(next((g for g in (w.groups() if w else ()) if g), 0) or 0)
    return grade, hours


def limit_for(c, grade, warranty_hours):
    """Most we pay this shop for a goods unit of this grade, with or without a warranty long enough to cover a
    break (breaks come within 6 h of the sale)."""
    p = (c.get("p_grade") or {}).get(grade)
    if p is None:
        return c["max_pay"]
    return pricing.buyer_limits(c["list"], c["budget"], "goods", p,
                                warranty=warranty_hours >= config.BUYER_C_MIN_WARRANTY_H, min_surplus=c.get("theta"))


def candidates(order, shops, mem, my_urls, theta=None):
    """theta: expected surplus / list a deal must reach (pricing.buyer_threshold); MIN_SURPLUS_RATIO when None."""
    cat = market.catalog()
    acceptable = set(order.get("acceptable") or [])
    out = []
    seen = {"shops": len(shops), "with_offers": 0, "offers": 0, "mine": 0, "not_acceptable": 0, "no_qty": 0,
            "no_list_price": 0}
    try:
        table = mem.break_table()
    except Exception:                                 # no table: the grade priors still price the risk
        table = {"grade": {}, "cat": {}, "shop": {}}
    for s in shops:
        seen["with_offers"] += bool(s.get("offers"))
        url = (s.get("url") or "").rstrip("/")
        if not url or url in my_urls or s.get("name") in config.MY_NAMES:
            seen["mine"] += 1
            continue                                  # same owner: the market will not settle it
        rep = mem.shop_reputation(url)
        for o in s.get("offers") or []:
            sku = o.get("sku")
            info = cat.get(sku) or {}
            seen["offers"] += 1
            if sku not in acceptable or (o.get("qty") or 0) <= 0 or not info.get("list_price"):
                seen["not_acceptable" if sku not in acceptable else "no_qty" if (o.get("qty") or 0) <= 0
                     else "no_list_price"] += 1
                continue
            L = info["list_price"]
            goods = info.get("kind") == "goods"
            # Before the shop names a grade: its usual grade mix, no warranty. negotiate() moves the limit once the
            # reply states the grade and the warranty (2026-11-10: grade alone sets the break rate, A 2.9% B 11.9%
            # C 33.9%, the same in every category).
            p_grade = {g: mem.p_break(g, info.get("category"), s.get("name"), table) for g in "ABC"} if goods else {}
            p_break = mem.p_break_mix(info.get("category"), s.get("name"), table) if goods else 0.0
            max_pay = pricing.buyer_limits(L, order["budget"], info.get("kind"), p_break, min_surplus=theta)
            ratio_m, _ = mem.price_ratio(sku=sku, category=info.get("category"))
            exp = pricing.expected_price(L, mem.shop_price_ratio(url, info.get("category")), ratio_m)
            ours = 1.0 if rep["our_buys"] < 3 else (rep["our_paid"] + 1) / (rep["our_buys"] + 1)
            bad = rep["our_bad"] / max(1, rep["our_paid"])
            reliability = (1 - rep["lie_rate"]) * max(0.1, 1 - bad) * ours
            if rep["talks_24h"] >= 4 and rep["deals_24h"] == 0:
                reliability *= 0.5                    # talked a lot, never closed: likely too expensive or broken
            surplus = L - min(exp, max_pay)
            # The market scores buyers by saved = sum(surplus / list) / (deals + 100) ("a sofa and a snack at 30%
            # off score the same", /agent.md), so rank by the expected share of list saved, not by won.
            saved = surplus / L
            opening = pricing.memory_opening(L, max_pay, mem.shop_profile(url))
            out.append({"shop": url, "name": s.get("name"), "sku": sku, "item_name": info.get("name"), "list": L,
                        "kind": info.get("kind"), "max_pay": max_pay, "expected": exp, "budget": order["budget"],
                        "theta": theta, "p_grade": {g: round(p, 4) for g, p in p_grade.items()},
                        "score": round(saved * reliability, 4), "saved": round(saved, 4),
                        "reliability": round(reliability, 3),
                        "opening": opening})
    out.sort(key=lambda c: -c["score"])
    candidates.last_filter = seen
    return out


def read_ask(reply, c):
    """Shop's price from its reply; regex first, the LLM only when the regex finds nothing plausible."""
    p = parse_offer(reply)
    if p is not None and 0.1 * c["list"] <= p <= 5 * c["list"]:
        return p
    out = llm.ask_json("A shop replied in a price negotiation. Extract the price it is asking now, as a whole "
                       'number of credits, as {"price": n}, or {"price": null} if it names none.',
                       {"shop_reply": reply[:1500]}, timeout=30, purpose="read_ask")
    v = (out or {}).get("price")
    return int(v) if isinstance(v, (int, float)) and 0.1 * c["list"] <= v <= 5 * c["list"] else None


SHELF_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h|hours?|시간)\s*(?:of shelf life|left|남)", re.I)


def stated_shelf_hours(text):
    """Hours of shelf life the shop says the unit has left, if it says so."""
    m = SHELF_RE.search(text or "")
    return float(m.group(1)) if m else None


def negotiate(c, order, deadline):
    """Returns (receipt or None, transcript)."""
    client = a2a.Client(c["shop"], timeout=60)
    want = f"I want to buy {c['item_name']} ({c['sku']})."
    if c["kind"] == "food":
        want += f" Need at least {accept_policy(order)['min_hours_left']:.0f} h of shelf life."
    t = client.send(want)
    lines, ask, offer, n = [("me", want), ("shop", a2a.said(t))], None, None, 0
    try:
        if c["kind"] == "food":
            left, need = stated_shelf_hours(a2a.said(t)), accept_policy(order)["min_hours_left"]
            if left is not None and left < need:            # the kit would refuse to pay: try the next shop now
                client.cancel(t.get("id"))
                return None, {"lines": lines, "ask": None, "offer": None, "state": "skipped",
                              "task": t.get("id"), "skip": f"shop states {left:g} h left, order needs {need:g} h"}
        while a2a.state(t) == "TASK_STATE_INPUT_REQUIRED" and n < config.MAX_OFFERS and time.time() < deadline:
            got = read_ask(a2a.said(t), c)
            ask = got if got is not None else ask
            if c["kind"] == "goods":
                grade, hours = quote_hints(a2a.said(t))
                if grade:                              # the shop named the grade: bid to that grade's own limit
                    c["hint"] = {"grade": grade, "warranty_hours": hours}
                    c["max_pay"] = limit_for(c, grade, hours)
            action, price = pricing.buyer_next_offer(c["list"], c["max_pay"], ask, offer, n, opening=c.get("opening"))
            if offer is not None and price <= offer and action == "offer":
                break                                  # at our limit and the shop does not move
            offer, n = price, n + 1
            t = client.send(str(price), t.get("id"))
            lines += [("me", str(price)), ("shop", a2a.said(t))]
        if a2a.state(t) == "TASK_STATE_INPUT_REQUIRED":
            client.cancel(t.get("id"))
        rec = a2a.receipt(t) if a2a.state(t) == "TASK_STATE_COMPLETED" else None
        return rec, {"lines": lines, "ask": ask, "offer": offer, "state": a2a.state(t), "task": t.get("id")}
    finally:
        client.close()


def judge_buy(rec, c, order, p_break, bought):
    """The same purchase in Horvitz's terms (RL_PROACTIVE.md 3), logged before the payment: p = the unit holds,
    B = Buyer ALL of the deal when it does, C = what a break takes (0 surplus under a warranty, -1 without). The
    rule in valid() still decides; `bought` is what it said, `decision` what p > C/(B+C) says. A purchase cannot be
    undone unless a warranty refunds it, so without one it needs the wait margin."""
    try:
        b = score_model.cached()
        if b is None:
            return None
        covered, disc = bool(c_covered(rec)), 1 - rec["price"] / c["list"]
        gain = score_model.delta(b, {"kind": "buy", "r": disc})[1]
        loss = -score_model.delta(b, {"kind": "buy", "r": 0.0 if covered else -1.0})[1]
        return initiative.judge("buy", 1 - p_break, max(0.0, gain), max(0.0, loss), "saved, breaks without a warranty",
                                reversible=covered, order=order.get("id"), sku=rec.get("item"), claim=rec.get("claim"),
                                discount=round(disc, 4), warranty_hours=rec.get("warranty_hours") or 0, bought=bought)
    except Exception:                                 # a logging fault must not lose a deal
        return None


def valid(rec, c, order):
    if not rec:
        return "no receipt"
    price = rec.get("price")
    if isinstance(price, float) and price.is_integer():
        rec["price"] = price = int(price)
    if rec.get("item") not in (order.get("acceptable") or []):
        return f"receipt item {rec.get('item')} is not acceptable"
    if not isinstance(price, int) or price > order["budget"] or price > c["max_pay"]:
        return f"receipt price {price} above our limit {c['max_pay']}"
    if order.get("kind") != "food" and rec.get("claim") == "C" and not c_covered(rec):
        return f"grade C without a {config.BUYER_C_MIN_WARRANTY_H} h warranty (C breaks ~35%, a break counts -1 saved)"
    p = (c.get("p_grade") or {}).get(rec.get("claim"))
    if order.get("kind") != "food" and p is not None and c.get("list"):
        # What the receipt actually promises decides, not what the reply said: expected surplus / list of this
        # grade with this warranty against the threshold the price limit was built on.
        theta = pricing.buyer_threshold(c.get("theta"))
        r = score_model.expected_r(1 - price / c["list"], p, warranty=c_covered(rec))
        judge_buy(rec, c, order, p, r >= theta - 1e-6)
        if r < theta - 1e-6:
            return (f"expected saved {r:.3f} under {theta:.3f}: grade {rec.get('claim')} breaks {p:.1%}, "
                    f"warranty {rec.get('warranty_hours') or 0:g} h, discount {1 - price / c['list']:.3f}")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    ap.add_argument("--order")
    a = ap.parse_args()
    order = market.read_json(os.path.basename(a.order), {}, os.path.dirname(a.order)) if a.order else {}
    if not order.get("acceptable"):                   # Round 0-2: the week-07 buyer still trades
        os.execvp(sys.executable, [sys.executable, "market_buyer.py", "--shops", a.shops, "--item", a.item,
                                   "--budget", str(a.budget)])
    order["budget"] = a.budget
    started, deadline = time.time(), time.time() + DEADLINE_S
    mem = Memory()
    for name, step in (("ledger", lambda: mem.sync_ledger(max_pages=4)), ("payments", mem.ingest_payments),
                       ("broken", mem.ingest_broken)):
        try:
            got = step()
            for r in (got if name != "ledger" else []) or []:   # important events become notes for recall
                if name == "payments" and not r.get("paid"):
                    mem.note("payment_refused", r.get("shop"), f"kit refused to pay {r.get('price')} for "
                             f"{r.get('item')}: {r.get('reason')}", 0.7)
                elif name == "broken":
                    mem.note("goods_broke", r.get("shop_url"), f"{r.get('name')} broke; warranty covered: "
                             f"{r.get('covered')}", 0.9)
        except Exception as e:
            say("memory_sync_error", error=str(e)[:200])
    shops = json.loads(open(a.shops, encoding="utf-8").read())
    try:
        board = score_model.load()
        model_theta = score_model.saved_threshold(board) if board else None
    except Exception as e:
        model_theta = None
        say("score_model_error", error=str(e)[:200])
    theta = pricing.buyer_threshold(model_theta)
    cands = candidates(order, shops, mem, mem.my_shop_urls(), theta=theta)
    decision_log({"order": order.get("order"), "category": order.get("category"), "budget": a.budget,
                  "theta": round(theta, 4), "model_theta": model_theta and round(model_theta, 4),
                  "kind": order.get("kind"), "use_within_hours": order.get("use_within_hours"),
                  "candidates": cands[:8], "filter": getattr(candidates, "last_filter", None)})
    say("candidates", n=len(cands), top=cands[:3])
    if not cands:
        mem.put("unmet_" + str(order.get("order")), {"category": order.get("category"), "ts": time.time()})
        print(json.dumps({"shop": "", "receipt": None, "stars": None, "review": None}))
        return
    tried = set()
    for c in cands:
        if c["shop"] in tried or len(tried) >= config.SHOPS_TO_TRY or time.time() > deadline - 20:
            continue
        tried.add(c["shop"])
        try:
            rec, talk = negotiate(c, order, min(deadline, time.time() + PER_SHOP_S))
        except Exception as e:
            rec, talk = None, {"error": f"{type(e).__name__}: {str(e)[:160]}"}
        problem = valid(rec, c, order)
        mem.record_negotiation("buyer", order.get("order"), c["shop"], c["sku"], talk.get("ask"),
                               rec and rec.get("price"), talk.get("lines"), "deal" if not problem else "no_deal")
        decision_log({"order": order.get("order"), "shop": c["shop"], "sku": c["sku"], "max_pay": c["max_pay"],
                      "hint": c.get("hint"), "claim": rec and rec.get("claim"),
                      "warranty_hours": rec and rec.get("warranty_hours"),
                      "talk": talk, "problem": problem, "price": rec and rec.get("price")})
        if problem:
            say("no_deal", shop=c["name"], reason=problem, ask=talk.get("ask"), offer=talk.get("offer"))
            continue
        mem.record_purchase_intent(order, c["shop"], c["name"], rec)
        surplus = c["list"] - rec["price"]
        say("deal", shop=c["name"], sku=c["sku"], price=rec["price"], list=c["list"], seconds=round(time.time() - started))
        print(json.dumps({"shop": c["shop"], "receipt": rec, "stars": 4,
                          "review": f"Agreed {c['item_name']} at {rec['price']} (list {c['list']}, surplus {surplus}).",
                          "accept": accept_policy(order, rec)}, ensure_ascii=False))
        return
    print(json.dumps({"shop": "", "receipt": None, "stars": None, "review": None}))


if __name__ == "__main__":
    main()
