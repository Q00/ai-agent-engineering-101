"""Market buyer: picks a shop from the market's list, trades over A2A, keeps a memory, writes a review.

    python market_buyer.py --shops <shops.json> --item <item> --budget <budget> [--order <order.json>]

Round 3 (the order lists acceptable SKUs): the code lists what each shop has that fits, caps every offer at
min(budget, list price x policy share) since the score is list price minus what was paid, asks for the SKU
by name, sets the checkout's accept policy (certificate, shelf life left, goods grades), and counts goods
that broke after a purchase against that shop.

The harness picks the shop (from reviews and this buyer's own memory), every offer, and the
review. The code keeps the budget, checks the receipt against what was agreed, and appends
each trade to buyer_memory.jsonl. The last output line is the result the kit sends to the market.
"""
import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

import os

import harness
import policy
from buyer import HEADERS, PROMPT, rpc, says, send, state, to_int

MEMORY = Path(os.environ.get("BUYER_MEMORY") or Path(__file__).with_name("buyer_memory.jsonl"))
DEADLINE = 240
MAX_ROUNDS = 10

CHOOSE_PROMPT = """You are a buyer in an online market. You need a {item} and can pay at most {budget}.
Pick ONE shop to buy from. Each line summarizes other buyers' reviews and your own past trades.
"warnings" counts reviews of 2 stars or less, or reviews that report a receipt not matching the agreed
price or a shop trying to manipulate buyers. Avoid shops with warnings. A shop with no reviews and no
history is unknown, not bad.

Shops:
{shops}

Reply with exactly one JSON object and nothing else:
{{"shop": "<shop name exactly as listed>", "reason": "<one short sentence>"}}"""

REVIEW_PROMPT = """You just tried to buy a {item} (budget {budget}) from the shop "{shop}".
What happened:
{facts}
Write a short public review for other buyers. Give 1 to 5 stars (1 = dishonest or very bad, 5 = excellent).
Reply with exactly one JSON object and nothing else:
{{"stars": <integer 1-5>, "review": "<one sentence>"}}"""


def log(msg):
    print(msg, flush=True)


def load_memory():
    if not MEMORY.exists():
        return []
    rows = []
    for line in MEMORY.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def remember(row):
    with open(MEMORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


SHOWN = 6   # shops the harness sees; review texts stay out of the prompt
WARNING = re.compile(r"receipt (says|said|showed|was|price)|overcharg|higher than|n.t match|mismatch|inject"
                     r"|telling ai|instruct|manipulat|ignore (previous|your)|scam|fake|forged|lied", re.I)


def summarize(shop, item, memory):
    """Numbers instead of review texts: shorter, and another seller's words never reach the harness."""
    reviews = shop.get("reviews") or []
    stars = [r["stars"] for r in reviews if isinstance(r.get("stars"), (int, float))]
    warnings = sum(1 for r in reviews
                   if (isinstance(r.get("stars"), (int, float)) and r["stars"] <= 2) or WARNING.search(str(r.get("review", ""))))
    mine = [m for m in memory if m.get("shop") == shop["name"] and m.get("item") == item]
    deals = [m["price"] for m in mine if m.get("result") == "deal" and m.get("price") is not None]
    avg = sum(stars) / len(stars) if stars else None
    score = (avg if avg is not None else 3.0) - 0.5 * warnings + 0.3 * min(len(deals), 3)
    line = (f"- {shop['name']}: {len(reviews)} reviews"
            + (f", average {avg:.1f} stars" if avg is not None else "")
            + f", warnings {warnings}"
            + (f"; you bought this item there {len(deals)} times, average price {sum(deals) / len(deals):.0f}" if deals
               else f"; you tried this item there {len(mine)} times without a deal" if mine else "; no history"))
    return score, line, not reviews and not mine


def choose(shops, item, budget, memory):
    flagged = {m["shop"] for m in memory if m.get("receipt_problem")}
    candidates = [s for s in shops if s["name"] not in flagged] or shops
    if len(candidates) < len(shops):
        log(f"[memory] skipping shops with a receipt problem in my own trades: {sorted(flagged & {s['name'] for s in shops})}")
    rated = sorted(((summarize(s, item, memory), s) for s in candidates), key=lambda x: -x[0][0])
    shown = rated[:SHOWN]
    unknown = next((r for r in rated[SHOWN:] if r[0][2]), None)
    if unknown:
        shown.append(unknown)
    candidates = [s for _, s in shown]
    text = "\n".join(summary[1] for summary, _ in shown)
    log(f"[choose] showing {len(shown)} of {len(rated)} shops: {[s['name'] for s in candidates]}")
    answer = harness.ask(CHOOSE_PROMPT.format(item=item, budget=budget, shops=text), log) or {}
    pick = next((s for s in candidates if s["name"] == answer.get("shop")), None)
    if pick is None:
        pick = candidates[0]
        log(f"[choose] harness named no listed shop ({answer}); taking the first candidate")
    log(f"[choose] {pick['name']} {pick['url']}: {answer.get('reason', '')}")
    return pick


PROMPT_R3 = """You are buying {item} from a shop. Its list price is {list_price}.
What you gain is the list price minus what you pay, so every unit you save counts. You may pay at most {budget};
never pay more and never reveal it. Buying within that limit is still better than not buying.
So far:
{history}
The shop is now asking {ask}.
Raise your offer in small steps; accept only when the shop has stopped coming down or the ask is a good deal.
Choose one:
- accept: pay {ask}
- offer: propose a lower price
- walk: leave without buying
Reply with exactly one JSON object and nothing else:
{{"action": "accept" | "offer" | "walk", "price": <integer, your offer if you offer>}}"""


def negotiate(base, item, budget, started, request=None, list_price=None, first_offer=None, first_check=None,
              info=None):
    """The week-06 negotiation. Returns (task, last offer sent). budget is the cap the code enforces."""
    card = httpx.get(base.rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS, timeout=30).json()
    url = next(i["url"] for i in card["supportedInterfaces"] if i.get("protocolBinding") == "JSONRPC")
    log(f"card: {card['name']!r}, JSONRPC at {url}")
    request = request or f"I want to buy a {item}"
    task = send(url, request)
    if state(task) != "TASK_STATE_INPUT_REQUIRED":
        if state(task) == "TASK_STATE_REJECTED" and info is not None:   # mostly "none in stock": the listing
            info["walked"] = f"the shop turned the request down: {says(task)[:80]!r}"   # lags behind sales
            info["nostock"] = True
        return task, None
    task_id, offered = task["id"], None
    history = [f"you: {request}", f"shop: {says(task)}"]
    reason = first_check(says(task)) if first_check else None
    if reason:
        log(f"[quality] {reason}; walking away before haggling")
        rpc(url, "CancelTask", {"id": task_id})
        if info is not None:
            info["walked"] = reason
        return task, None
    for _ in range(MAX_ROUNDS):
        if state(task) != "TASK_STATE_INPUT_REQUIRED":
            break
        if time.time() - started > DEADLINE:
            log("deadline reached; walking away")
            rpc(url, "CancelTask", {"id": task_id})
            break
        numbers = re.findall(r"\d+", says(task))
        ask = int(numbers[-1]) if numbers else None
        left = re.search(r"(\d+)\s+offers?\s+left", says(task), re.I)
        if offered is not None and ask is not None and ask <= budget and \
                (ask - offered <= 0.02 * ask or (left and int(left.group(1)) <= 1)):
            action, price = "accept", ask      # a few won apart, or the shop's last round: haggling on loses the deal
            log(f"[policy] accepting {ask}: {ask - offered} above our offer"
                + (f", {left.group(1)} offers left" if left else ""))
        elif first_offer and offered is None and ask is not None and ask > first_offer:
            action, price = "offer", min(first_offer, budget)       # the opening offer comes from the policy
            log(f"[policy] opening offer {price} ({first_offer} from the list price)")
        else:
            prompt = (PROMPT_R3.format(item=item, list_price=list_price, budget=budget, ask=ask,
                                       history="\n".join(history)) if list_price else
                      PROMPT.format(item=item, budget=budget, ask=ask, history="\n".join(history)))
            answer = harness.ask(prompt, log) or {}
            action, price = answer.get("action"), to_int(answer.get("price"))
        if action == "accept":
            price = ask
        if action in ("accept", "offer"):
            if price is None:
                log(f"[guard] no price in {answer}; walking away")
                action = "walk"
            elif price > budget:
                log(f"[guard] {action} at {price} is above the budget {budget}; offering {budget}")
                price = budget
        if action not in ("accept", "offer"):
            log(f"walking away ({answer})")
            rpc(url, "CancelTask", {"id": task_id})
            break
        offered = price
        history.append(f"you: {price}")
        task = send(url, str(price), task_id)
        history.append(f"shop: {says(task)}")
    return task, offered


def receipt_problem(receipt, task, item, offered, budget):
    price = to_int(receipt.get("price"))
    if price != offered:
        return f"receipt price {receipt.get('price')} but the last offer was {offered}"
    if price is not None and price > budget:
        return f"receipt price {price} above budget {budget}"
    if receipt.get("item") != item:
        return f"receipt item {receipt.get('item')!r}, ordered {item!r}"
    if receipt.get("task") != task.get("id"):
        return f"receipt task {receipt.get('task')!r} is not this negotiation's task {task.get('id')!r}"
    if not receipt.get("sig_seller"):
        return "receipt has no sig_seller"
    return None


MARKET_DIR = Path(os.environ.get("MARKET_DIR", Path(__file__).with_name(".market")))


def catalog():
    try:
        c = json.loads((MARKET_DIR / "catalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return c if isinstance(c, dict) else {x["sku"]: x for x in c}


def broken_by_shop(memory):
    """Goods that broke after we bought them: .market/broken.jsonl gives only the name and when it broke;
    our memory says which shop sold that name shortly before."""
    f = MARKET_DIR / "broken.jsonl"
    if not f.exists():
        return {}
    out = {}
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            b = json.loads(line)
        except ValueError:
            continue
        buys = [m for m in memory if m.get("result") == "deal" and m.get("name") == b.get("name")
                and m.get("ts", 0) <= b.get("broke_at", 0) <= m.get("ts", 0) + 6 * 3600 + 600]
        if buys:
            shop = max(buys, key=lambda m: m["ts"])["shop"]
            out[shop] = out.get(shop, 0) + 1
    return out


def choose_r3(shops, order, memory, cat, p=None):
    """Each shop's best fitting offer (the acceptable SKU with the highest list price: the most to gain),
    then the harness picks the shop from numbers about it."""
    p = p or policy.get("buyer")
    flagged = {m["shop"] for m in memory if m.get("receipt_problem")}
    broken = broken_by_shop(memory)
    rows = []
    need = food_need(order, p)
    stale = stale_food(memory, need, p)
    for s in shops:
        offers = [o for o in s.get("offers") or [] if o.get("sku") in cat and (o.get("qty") or 0) > 0
                  and (not need or (cat[o["sku"]].get("shelf_hours") or 0) >= need)]   # food that cannot last: skip
        if not offers or s["name"] in flagged:
            continue
        lasts = lambda o: not need or 0.45 * (cat[o["sku"]].get("shelf_hours") or 0) >= need  # even grade B lasts
        o = max(offers, key=lambda o: (lasts(o), cat[o["sku"]]["list_price"]))
        lp = cat[o["sku"]]["list_price"]
        cap = min(order["budget"], int(lp * p["max_pay_share_of_list"]))
        if cap < 1:
            continue
        score, line, unknown = summarize(s, o["sku"], memory)
        score -= 1.0 * broken.get(s["name"], 0)
        score -= 2.0 if (s["name"], o["sku"]) in stale else 0     # it told us lately its unit would not last
        line += (f"; offers {o['name'][:40]} ({o['sku']}), list price {lp}, you may pay at most {cap}"
                 + (f"; {broken[s['name']]} goods bought there broke" if broken.get(s["name"]) else ""))
        rows.append((score + lp / max(1, order["budget"]) * 0.5 - (0 if lasts(o) else 1.0), line, unknown, s, o, cap))
    if not rows:
        return None, []
    rows.sort(key=lambda r: -r[0])
    shown = rows[:SHOWN] + [r for r in rows[SHOWN:] if r[2]][:1]
    log(f"[choose] {len(rows)} shops have a fitting SKU; showing {[r[3]['name'] for r in shown]}")
    answer = harness.ask(CHOOSE_PROMPT.format(item=order["item"], budget=order["budget"],
                                              shops="\n".join(r[1] for r in shown)), log) or {}
    pick = next((r for r in shown if r[3]["name"] == answer.get("shop")), shown[0])
    log(f"[choose] {pick[3]['name']}: {pick[4]['sku']} list {cat[pick[4]['sku']]['list_price']}, cap {pick[5]}; "
        f"{answer.get('reason', '')}")
    return pick, [pick] + [r for r in rows if r is not pick]


def stale_food(memory, need, p):
    """(shop, sku) pairs whose unit, by what the shop told us when we walked away lately, cannot last `need` hours
    now. A shop does not show shelf life in its listing; this is the only way to learn it before asking."""
    now, keep = time.time(), p.get("walk_memory_hours", 3) * 3600
    gone = {(m["shop"], m["sku"]) for m in memory    # said "none in stock": supply refills every 20 min
            if m.get("kind") == "walked" and m.get("nostock") and now - m.get("ts", 0) < 1200}
    if not need:
        return gone
    return gone | {(m["shop"], m["sku"]) for m in memory
                   if m.get("kind") == "walked" and now - m.get("ts", 0) < keep
                   and m.get("hours_left") is not None and m["hours_left"] - (now - m["ts"]) / 3600 < need}


def food_need(order, p):
    """Hours of shelf life a food order needs at settle (0 for goods)."""
    if order.get("kind") != "food" and not order.get("use_within_hours"):
        return 0
    return (order.get("use_within_hours") or 0) + p["hours_margin"]


GRADE = re.compile(r"\bgrade\s*:?\s*([ABC])\b", re.I)
HOURS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h|hours?)\b(?:\s+of)?\s+shelf", re.I)


def too_short(reply, need, shelf):
    """The shop's first reply already shows the food will not last: walk away before haggling."""
    m = HOURS.search(reply)
    if m and float(m.group(1)) < need:
        return f"the shop says {m.group(1)} h of shelf life left, the order needs {need:.0f}"
    g = GRADE.search(reply)
    top = {"C": 0.45, "B": 0.8}.get(g.group(1).upper()) if g else None
    if top and shelf and top * shelf < need:
        return f"grade {g.group(1).upper()} means at most {top * shelf:.0f} h left, the order needs {need:.0f}"
    return None


def accept_policy(order, sku, cat):
    """What the kit checks on the unit before it pays: the market's certificate always; food must last
    the order's use window plus a margin; goods only in the grades the policy accepts."""
    p = policy.get("buyer")
    if cat.get(sku, {}).get("kind") == "food":
        return {"cert": True, "min_hours_left": (order.get("use_within_hours") or 0) + p["hours_margin"]}
    return {"cert": True, "grades": p["goods_grades"]}


def main_r3(a, shops, order, memory, started):
    cat = catalog()
    acceptable = set(order.get("acceptable") or [])
    bp, arm, exp = policy.section("buyer", order.get("order"))   # this order's arm of a running experiment
    pick, ranked = choose_r3(shops, order, memory, {k: v for k, v in cat.items() if k in acceptable}, bp)
    if pick is None:
        log("[choose] no shop has a fitting SKU that can last the order; no deal")
        print(json.dumps({"shop": "", "receipt": None, "stars": None, "review": None}), flush=True)
        return
    need = food_need(order, bp)
    receipt, problem, facts, offered, task, error = None, None, [], None, None, None
    tries = bp.get("shops_to_try", 3)
    for attempt, pick in enumerate(ranked[:tries]):    # a shop out of stock, or whose food will not last, is left at once
        if attempt and time.time() - started > DEADLINE - 90:
            break
        _, _, _, shop, offer, cap = pick
        sku, name, lp = offer["sku"], cat[offer["sku"]]["name"], cat[offer["sku"]]["list_price"]
        sh = cat[sku].get("shelf_hours")
        info, error = {}, None
        try:
            task, offered = negotiate(shop["url"], name, cap, started, request=f"I want to buy {name} ({sku})",
                                      list_price=lp, first_offer=int(lp * bp["first_offer_share"]),
                                      first_check=(lambda reply: too_short(reply, need, sh)) if need else None,
                                      info=info)
        except Exception as e:
            error = e
            break
        if not info.get("walked"):
            break
        facts.append(f"{shop['name']}: {info['walked']}")
        h = re.search(r"says ([0-9.]+) h", info["walked"])
        remember({"kind": "walked", "ts": int(time.time()), "shop": shop["name"], "sku": sku,
                  "hours_left": float(h.group(1)) if h else None, "nostock": bool(info.get("nostock")),
                  "why": info["walked"]})
        log(f"[choose] {shop['name']}: {info['walked']}; next shop")
    if info.get("walked") and not error:      # every shop tried was out of stock or had food that would not last
        remember({"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ts": int(time.time()),
                  "round": 3, "order": order.get("order"), "arm": arm, "exp": exp, "shop": None, "item": None,
                  "category": order["item"], "result": "no_deal",
                  "why": f"none of {attempt + 1} shops had a unit in stock that lasts the order"})
        print(json.dumps({"shop": "", "receipt": None, "stars": None, "review": None}), flush=True)
        return
    try:
        if error:
            raise error
        final = state(task) or "error"
        facts.append(f"final state {final}; the shop's last message: {says(task)!r}")
        if final == "TASK_STATE_COMPLETED":
            receipt = next((p["data"] for art in (task or {}).get("artifacts", []) for p in art.get("parts", [])
                            if isinstance(p.get("data"), dict)), None)
            if receipt is None:
                problem = "deal closed without a receipt"
            else:
                problem = receipt_problem(receipt, task, sku, offered, cap)
                if problem and (receipt.get("item") in acceptable or receipt.get("item") == order["item"]) \
                        and "receipt item" in problem:
                    problem = None                        # another acceptable SKU is fine for the order
                facts.append(f"agreed {offered} (list price {lp}), receipt "
                             f"{json.dumps({k: receipt.get(k) for k in ('shop', 'item', 'price', 'claim', 'warranty_hours')})}")
        else:
            facts.append(f"no deal; your last offer was {offered}")
    except Exception as e:
        final = "error"
        facts.append(f"the trade failed: {type(e).__name__}: {str(e)[:120]}")
    if problem:
        log(f"[receipt] PROBLEM: {problem}")
        facts.append(f"PROBLEM WITH THE RECEIPT: {problem}")
    answer = harness.ask(REVIEW_PROMPT.format(item=name, budget=cap, shop=shop["name"], facts="\n".join(facts)),
                         log) or {}
    stars = to_int(answer.get("stars"))
    stars = 1 if problem else (min(5, max(1, stars)) if stars is not None else 3)
    review = str(answer.get("review") or "").strip() or ("Deal." if receipt else "No deal.")
    remember({"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ts": int(time.time()), "round": 3,
              "order": order.get("order"), "arm": arm, "exp": exp,
              "shop": shop["name"], "item": sku, "name": name, "category": order["item"],
              "kind": cat[sku].get("kind"), "list_price": lp, "budget": order["budget"], "cap": cap,
              "result": "deal" if receipt else "no_deal", "price": to_int(receipt.get("price")) if receipt else None,
              "offered": offered if receipt else None, "receipt_problem": problem, "stars": stars})
    accept = accept_policy(order, sku, cat)
    log(f"result: {final}, {time.time() - started:.0f}s, accept {accept}")
    print(json.dumps({"shop": shop["url"], "receipt": receipt if not problem else None, "stars": stars,
                      "review": review, "accept": accept}, ensure_ascii=False), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", required=True, type=int)
    ap.add_argument("--order")
    a = ap.parse_args()
    started = time.time()
    shops = json.loads(Path(a.shops).read_text(encoding="utf-8"))
    memory = load_memory()
    log(f"market buyer: {a.item}, budget {a.budget}, {len(shops)} shops, {len(memory)} past trades in memory")
    order = json.loads(Path(a.order).read_text(encoding="utf-8")) if a.order and Path(a.order).exists() else {}
    if order.get("acceptable"):
        order.setdefault("budget", a.budget)
        order.setdefault("item", a.item)
        main_r3(a, shops, order, memory, started)
        return

    shop = choose(shops, a.item, a.budget, memory)
    receipt, problem, facts, offered = None, None, [], None
    try:
        task, offered = negotiate(shop["url"], a.item, a.budget, started)
        final = state(task) or "error"
        facts.append(f"final state {final}; the shop's last message: {says(task)!r}")
        if final == "TASK_STATE_COMPLETED":
            receipt = next((p["data"] for art in (task or {}).get("artifacts", []) for p in art.get("parts", [])
                            if isinstance(p.get("data"), dict)), None)
            if receipt is None:
                problem = "deal closed without a receipt"
            else:
                problem = receipt_problem(receipt, task, a.item, offered, a.budget)
                facts.append(f"agreed {offered}, receipt {json.dumps({k: receipt.get(k) for k in ('shop', 'item', 'price')})}")
        else:
            facts.append(f"no deal; your last offer was {offered}")
    except Exception as e:
        final = "error"
        facts.append(f"the trade failed: {type(e).__name__}: {str(e)[:120]}")
    if problem:
        log(f"[receipt] PROBLEM: {problem}")
        facts.append(f"PROBLEM WITH THE RECEIPT: {problem}")

    answer = harness.ask(REVIEW_PROMPT.format(item=a.item, budget=a.budget, shop=shop["name"],
                                              facts="\n".join(facts)), log) or {}
    stars = to_int(answer.get("stars"))
    stars = min(5, max(1, stars)) if stars is not None else 3
    if problem:
        stars = 1
    review = str(answer.get("review") or "").strip() or ("Deal." if receipt else "No deal.")

    remember({"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "shop": shop["name"],
              "item": a.item, "budget": a.budget, "result": "deal" if receipt else "no_deal",
              "price": to_int(receipt.get("price")) if receipt else None, "offered": offered if receipt else None,
              "receipt_problem": problem, "stars": stars})
    log(f"result: {final}, {time.time() - started:.0f}s")
    print(json.dumps({"shop": shop["url"], "receipt": receipt, "stars": stars, "review": review},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
