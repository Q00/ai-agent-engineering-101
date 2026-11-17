"""Round 3 market_buyer: picks (shop, SKU) by expected surplus from memory, negotiates over A2A, and hands the
kit an accept policy so it never pays for something that is worth 0 to the order.

    python3 buyer_r3.py --shops {shops} --item {item} --budget {budget} --order {order}

Score of a candidate = (list price - expected price) / list price * reliability (the market scores the share of
list saved, not won; until 2026-11-06 the score was in won and favoured big-ticket SKUs at smaller discounts), where
  expected price  = this shop's median settled price / list for the category (memory), else the market's, else 0.85
  reliability     = (1 - shop's dishonest-claim rate) * (1 - breaks and worthless deals we saw) * our payment success
The last JSON line is what the kit reads. An order without the Round 3 fields is declined.
"""
import argparse
import hashlib
import threading
import json
import os
import re
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
                                warranty=warranty_hours >= config.BUYER_C_MIN_WARRANTY_H, min_surplus=c.get("theta"),
                                grade=grade)


def candidates(order, shops, mem, my_urls, theta=None):
    """theta: expected margin share a deal must reach (pricing.buyer_threshold, patch 3.6); MIN_SAVED_SHARE when
    None."""
    cat = market.catalog()
    acceptable = set(order.get("acceptable") or [])
    out = []
    seen = {"shops": len(shops), "with_offers": 0, "offers": 0, "mine": 0, "not_acceptable": 0, "no_qty": 0,
            "no_list_price": 0}
    try:
        table = mem.break_table()
    except Exception:                                 # no table: the grade priors still price the risk
        table = {"grade": {}, "cat": {}, "shop": {}}
    memo = {}                                         # one answer per (question, shop, category / sku) in a call:
    ask = lambda key, f: memo[key] if key in memo else memo.setdefault(key, f())   # 236 offers came from 34 shops
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
            ratio_m, _ = ask(("sku", sku), lambda: mem.price_ratio(sku=sku, category=info.get("category")))
            shop_ratio = ask(("shop", url, info.get("category")), lambda: mem.shop_price_ratio(url, info.get("category")))
            exp = pricing.expected_price(L, shop_ratio, ratio_m)
            ours = 1.0 if rep["our_buys"] < 3 else (rep["our_paid"] + 1) / (rep["our_buys"] + 1)
            bad = rep["our_bad"] / max(1, rep["our_paid"])
            reliability = (1 - rep["lie_rate"]) * max(0.1, 1 - bad) * ours
            if rep["talks_24h"] >= 4 and rep["deals_24h"] == 0:
                reliability *= 0.5                    # talked a lot, never closed: likely too expensive or broken
            # The market scores buyers by saved = sum((list - price) / (list - cost)) / (deals + 100) (patch 3.6,
            # 2026-11-12; share of list before), so rank by the expected margin share, not by won.
            saved = pricing.margin_share(min(exp, max_pay), L, info.get("kind"))
            opening = pricing.memory_opening(L, max_pay, ask(("profile", url), lambda: mem.shop_profile(url)))
            out.append({"shop": url, "name": s.get("name"), "sku": sku, "item_name": info.get("name"), "list": L,
                        "kind": info.get("kind"), "max_pay": max_pay, "expected": exp, "budget": order["budget"],
                        "theta": theta, "p_grade": {g: round(p, 4) for g, p in p_grade.items()},
                        "score": round(saved * reliability, 4), "saved": round(saved, 4),
                        "reliability": round(reliability, 3),
                        "opening": opening})
    out.sort(key=lambda c: -c["score"])
    candidates.last_filter = seen
    return out


# 2026-11-11: of 9,277 shop lines, 368 had no number (330 of them "none of that in stock" / "No deal") and 530 gave
# parse_offer an out-of-range one ("5cm", "6-hour warranty", "about 2% over"), so 898 replies went to the LLM at a
# p50 of 4.2 s each, 1,756 calls a day, mostly to learn that there is no price.
NO_PRICE_RE = re.compile(r"\b(none of that in stock|no matching (unit|stock)|no available unit|out of stock|no deal\b)",
                         re.I)
ASK_RE = re.compile(r"(?:counteroffer|asking|reply with|firm price|final price is|price is|my price|lowest is)\s*:?\s*"
                    r"(?:₩|krw\s*)?(\d[\d,]{2,})|(\d[\d,]{2,})\s+is our lowest", re.I)


def read_ask(reply, c):
    """Shop's price from its reply: a number after the words shops price with, then the last plausible number; a
    reply that says it has nothing to sell has no price. The LLM only when none of these reads it."""
    lo, hi = 0.1 * c["list"], 5 * c["list"]
    for m in ASK_RE.finditer(reply or ""):
        v = int((m.group(1) or m.group(2)).replace(",", ""))
        if lo <= v <= hi:
            return v
    p = parse_offer(reply)
    if p is not None and lo <= p <= hi:
        return p
    if NO_PRICE_RE.search(reply or ""):
        return None
    out = llm.ask_json("A shop replied in a price negotiation. Extract the price it is asking now, as a whole "
                       'number of credits, as {"price": n}, or {"price": null} if it names none.',
                       {"shop_reply": reply[:1500]}, timeout=30, purpose="read_ask")
    v = (out or {}).get("price")
    return int(v) if isinstance(v, (int, float)) and 0.1 * c["list"] <= v <= 5 * c["list"] else None


FINAL_RE = re.compile(r"\bfinal (?:price|offer)\b|\bfirm price\b|\b(?:lowest|floor) (?:price|we can)|already (?:at|as low)"
                      r"|as low as it goes|can(?:no|')t go (?:any )?lower|\bno lower\b|최종|더 이상 (?:못|안)", re.I)


def is_final(reply):
    """The shop says its price will not move (wording collected from the logged replies)."""
    return bool(FINAL_RE.search(reply or ""))


SHELF_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h|hours?|시간)\s*(?:of shelf life|left|남)", re.I)


def stated_shelf_hours(text):
    """Hours of shelf life the shop says the unit has left, if it says so."""
    m = SHELF_RE.search(text or "")
    return float(m.group(1)) if m else None


def opening_line(c, order):
    want = f"I want to buy {c['item_name']} ({c['sku']})."
    if c["kind"] == "food":
        want += f" Need at least {accept_policy(order)['min_hours_left']:.0f} h of shelf life."
    return want


def negotiate(c, order, deadline, opened=None):
    """Returns (receipt or None, transcript). opened: (client, first task, our first line) when the shop was already
    asked (fan-out quotes); the talk goes on in that task."""
    if opened:
        client, t, want = opened
    else:
        client = a2a.Client(c["shop"], timeout=60)
        want = opening_line(c, order)
        t = client.send(want)
    lines, ask, offer, n, final = [("me", want), ("shop", a2a.said(t))], None, None, 0, False
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
            final = got is not None and is_final(a2a.said(t))
            haggle = haggle_arm(order.get("order"))
            counter_below = int(c["list"] * (1 - config.BUYER_HAGGLE_MS * (1 - open_floor(c)))) if haggle else None
            action, price = pricing.buyer_next_offer(c["list"], c["max_pay"], ask, offer, n, opening=c.get("opening"),
                                                     final=final,
                                                     open_floor=open_floor(c) if cost_open_arm(order.get("order")) else None,
                                                     counter_below=counter_below, accept_after=1 if haggle else 3)
            if action == "walk":
                break                                  # a final price over our limit: the next shop, not 6 more turns
            if offer is not None and price <= offer and action == "offer":
                break                                  # at our limit and the shop does not move
            offer, n = price, n + 1
            t = client.send(str(price), t.get("id"))
            lines += [("me", str(price)), ("shop", a2a.said(t))]
        if a2a.state(t) == "TASK_STATE_INPUT_REQUIRED":
            client.cancel(t.get("id"))
        rec = a2a.receipt(t) if a2a.state(t) == "TASK_STATE_COMPLETED" else None
        return rec, {"lines": lines, "ask": ask, "offer": offer, "state": a2a.state(t), "task": t.get("id"),
                     "final": final}
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
        covered = bool(c_covered(rec))
        disc = pricing.margin_share(rec["price"], c["list"], "goods", rec.get("claim"))
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
        theta = c["theta"] if isinstance(c.get("theta"), (int, float)) else config.MIN_SAVED_SHARE   # already final
        r = score_model.expected_r(pricing.margin_share(price, c["list"], "goods", rec.get("claim")), p,
                                   warranty=c_covered(rec))
        judge_buy(rec, c, order, p, r >= theta - 1e-6)
        if r < theta - 1e-6:
            return (f"expected saved {r:.3f} under {theta:.3f}: grade {rec.get('claim')} breaks {p:.1%}, "
                    f"warranty {rec.get('warranty_hours') or 0:g} h, discount {1 - price / c['list']:.3f}, "
                    f"margin share {pricing.margin_share(price, c['list'], 'goods', rec.get('claim')):.3f}")
    return None


def fanout_arm(order_id):
    """True for the BUYER_FANOUT_SHARE of orders that get fan-out quotes; stable per order id."""
    h = int(hashlib.sha256(str(order_id).encode()).hexdigest()[:8], 16)
    return h % 10_000 < config.BUYER_FANOUT_SHARE * 10_000


def cost_open_arm(order_id):
    """True for the BUYER_COST_OPEN_SHARE of orders that open at the estimated cost; stable per order id and
    independent of fanout_arm (its own salt)."""
    h = int(hashlib.sha256(f"cost_open:{order_id}".encode()).hexdigest()[:8], 16)
    return h % 10_000 < config.BUYER_COST_OPEN_SHARE * 10_000


def quote_early_arm(order_id):
    """True for the BUYER_QUOTE_EARLY_SHARE of orders whose fan-out stops waiting once BUYER_QUOTE_ENOUGH usable quotes
    are in (plus BUYER_QUOTE_GRACE_S); stable per order id, own salt."""
    h = int(hashlib.sha256(f"quote_early:{order_id}".encode()).hexdigest()[:8], 16)
    return h % 10_000 < config.BUYER_QUOTE_EARLY_SHARE * 10_000


def quote_short_arm(order_id):
    """True for the BUYER_QUOTE_SHORT_SHARE of orders whose fan-out stops at BUYER_QUOTE_SHORT_S once at least one usable
    quote is in (the next order waits for ours to end, so seconds past ~16 s cost orders); own salt."""
    h = int(hashlib.sha256(f"quote_short:{order_id}".encode()).hexdigest()[:8], 16)
    return h % 10_000 < config.BUYER_QUOTE_SHORT_SHARE * 10_000


def haggle_arm(order_id):
    """True for the BUYER_HAGGLE_SHARE of orders that counter a first ask under BUYER_HAGGLE_MS margin share; own salt."""
    h = int(hashlib.sha256(f"haggle:{order_id}".encode()).hexdigest()[:8], 16)
    return h % 10_000 < config.BUYER_HAGGLE_SHARE * 10_000


def open_floor(c):
    """Estimated cost / list of a candidate: the grade the shop named, else the kind's market mix."""
    return pricing.cost_ratio(c.get("kind"), (c.get("hint") or {}).get("grade"))


def fanout_quotes(cands, order):
    """Ask each shop (its best candidate, up to BUYER_FANOUT_MAX shops) at once. Returns [(c, opened, ask)] of the
    shops that answered within BUYER_QUOTE_TIMEOUT_S and can sell, cheapest expected saved first; every other open
    task is cancelled, a late one as soon as it answers."""
    picked, seen = [], set()
    for c in cands:
        if c["shop"] not in seen:
            seen.add(c["shop"])
            picked.append(c)
        if len(picked) >= config.BUYER_FANOUT_MAX:
            break
    lock, closed, got, usable_at = threading.Lock(), [False], [], []
    early = quote_early_arm(order.get("order"))
    t0 = time.time()

    def ask_one(c):
        client = a2a.Client(c["shop"], timeout=60)
        want = opening_line(c, order)
        try:
            t = client.send(want)
        except Exception:
            client.close()
            return
        with lock:
            if not closed[0]:
                got.append((c, (client, t, want)))
                if a2a.state(t) == "TASK_STATE_INPUT_REQUIRED":
                    usable_at.append(round(time.time() - t0, 1))
                return
        client.cancel(t.get("id"))                     # answered after the deadline: release the shop's unit
        client.close()

    threads = [threading.Thread(target=ask_one, args=(c,), daemon=True) for c in picked]
    for th in threads:
        th.start()
    end = t0 + config.BUYER_QUOTE_TIMEOUT_S
    short = quote_short_arm(order.get("order"))
    while time.time() < end and any(th.is_alive() for th in threads):
        if short and time.time() - t0 >= config.BUYER_QUOTE_SHORT_S:
            with lock:
                if usable_at:
                    break
        if early:
            with lock:
                n = len(usable_at)
                enough_at = usable_at[config.BUYER_QUOTE_ENOUGH - 1] if n >= config.BUYER_QUOTE_ENOUGH else None
            if enough_at is not None and time.time() - t0 >= max(enough_at + config.BUYER_QUOTE_GRACE_S,
                                                                  config.BUYER_QUOTE_MIN_WAIT_S):
                break
        time.sleep(0.1)
    with lock:
        closed[0] = True
        answers = list(got)
        fanout_quotes.last = {"early": early, "short": short, "quote_s": round(time.time() - t0, 1), "usable_at": list(usable_at)}
    out, need = [], accept_policy(order).get("min_hours_left") if order.get("kind") == "food" else None
    for c, (client, t, want) in answers:
        said = a2a.said(t)
        left = stated_shelf_hours(said) if c["kind"] == "food" else None
        if a2a.state(t) != "TASK_STATE_INPUT_REQUIRED" or (left is not None and need and left < need):
            client.cancel(t.get("id"))
            client.close()
            continue
        try:
            ask = read_ask(said, c)
        except Exception:
            ask = None
        grade = quote_hints(said)[0] if c["kind"] == "goods" else None
        out.append((c, (client, t, want), ask, grade))
    # Highest expected margin share first (patch 3.6): the same discount on a grade C unit is worth less than on A.
    # Ties (margin share capped at 1 below cost) go to the lower ask / list, not to whichever shop answered first.
    key = lambda x: ((-pricing.margin_share(x[2], x[0]["list"], x[0]["kind"], x[3]) * x[0].get("reliability", 1.0),
                      x[2] / x[0]["list"]) if x[2] else (1.0, 9.9))
    return [(c, op, ask) for c, op, ask, _ in sorted(out, key=key)]


def close_quotes(rest):
    for _, (client, t, _), _ in rest:
        client.cancel(t.get("id"))
        client.close()


def run_order(cands, order, mem, deadline, arm="serial"):
    """Try shops until one deal passes valid(); returns the line the kit reads."""
    quotes = fanout_quotes(cands, order) if arm == "fanout" else None
    if quotes is not None:
        decision_log({"order": order.get("order"), "arm": arm, "asked": min(len({c["shop"] for c in cands}),
                      config.BUYER_FANOUT_MAX), "quotes": [(c["shop"], c["sku"], ask, c["list"]) for c, _, ask in quotes],
                      **getattr(fanout_quotes, "last", {})})
        plan = [(c, op) for c, op, _ in quotes]
    else:
        plan = [(c, None) for c in cands]
    tried = set()
    try:
        while plan:
            c, op = plan.pop(0)
            if c["shop"] in tried or len(tried) >= config.SHOPS_TO_TRY or time.time() > deadline - 20:
                if op:
                    close_quotes([(c, op, None)])
                continue
            tried.add(c["shop"])
            try:
                rec, talk = negotiate(c, order, min(deadline, time.time() + PER_SHOP_S), opened=op)
            except Exception as e:
                rec, talk = None, {"error": f"{type(e).__name__}: {str(e)[:160]}"}
            problem = valid(rec, c, order)
            mem.record_negotiation("buyer", order.get("order"), c["shop"], c["sku"], talk.get("ask"),
                                   rec and rec.get("price"), talk.get("lines"), "deal" if not problem else "no_deal")
            decision_log({"order": order.get("order"), "arm": arm, "shop": c["shop"], "sku": c["sku"],
                          "max_pay": c["max_pay"], "hint": c.get("hint"), "claim": rec and rec.get("claim"),
                          "warranty_hours": rec and rec.get("warranty_hours"),
                          "talk": talk, "problem": problem, "price": rec and rec.get("price")})
            if problem:
                say("no_deal", shop=c["name"], reason=problem, ask=talk.get("ask"), offer=talk.get("offer"))
                continue
            mem.record_purchase_intent(order, c["shop"], c["name"], rec)
            say("deal", shop=c["name"], sku=c["sku"], price=rec["price"], list=c["list"], arm=arm)
            return {"shop": c["shop"], "receipt": rec, "stars": 4,
                    "review": f"Agreed {c['item_name']} at {rec['price']} (list {c['list']}, "
                              f"surplus {c['list'] - rec['price']}).",
                    "accept": accept_policy(order, rec)}
        return {"shop": "", "receipt": None, "stars": None, "review": None}
    finally:
        close_quotes([(c, op, None) for c, op in plan if op])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    ap.add_argument("--order")
    a = ap.parse_args()
    order = market.read_json(os.path.basename(a.order), {}, os.path.dirname(a.order)) if a.order else {}
    if not order.get("acceptable"):                   # not a Round 3 order (the week-07 buyer that took these is gone)
        say("no_round3_order", item=a.item)
        print(json.dumps({"shop": "", "receipt": None, "stars": None, "review": None}))
        return
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
    arm = "fanout" if fanout_arm(order.get("order")) else "serial"
    decision_log({"order": order.get("order"), "arm": arm, "open": "cost" if cost_open_arm(order.get("order")) else "base",
                  "haggle": haggle_arm(order.get("order")),
                  "category": order.get("category"), "budget": a.budget,
                  "theta": round(theta, 4), "model_theta": model_theta and round(model_theta, 4),
                  "kind": order.get("kind"), "use_within_hours": order.get("use_within_hours"),
                  "candidates": cands[:8], "filter": getattr(candidates, "last_filter", None)})
    say("candidates", n=len(cands), top=cands[:3])
    if not cands:
        mem.put("unmet_" + str(order.get("order")), {"category": order.get("category"), "ts": time.time()})
        print(json.dumps({"shop": "", "receipt": None, "stars": None, "review": None}))
        return
    out = run_order(cands, order, mem, deadline, arm=arm)
    say("done", arm=arm, seconds=round(time.time() - started), deal=bool(out.get("receipt")))
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
