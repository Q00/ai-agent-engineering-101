"""Week 07 market buyer: read the shops and their reviews, pick one, talk the price down, review it.

    python market_buyer.py --shops shops.json --item lamp --budget 120

{shops} is a JSON list of {"name", "url", "reviews": [{"stars", "review", "round"}]}, newest first.

1. Which shop to enter. Each shop becomes one line: review count and average stars, the newest review
   texts, red flags found in reviews (overcharged, receipt higher than agreed, ...), and what my own
   memory says about it (past deals, prices, a receipt that did not match). The harness reads those
   lines and picks a shop, with a reason. The code then applies two rules, whatever the harness says:
     - a shop my memory caught cheating is never entered while another shop is listed
     - a shop whose reviews say it cheats (red flags, or average 2 stars or less over 2+ reviews)
       is not entered while a shop without such signs is listed
     - a shop whose last visit (within FAIL_MEMORY seconds) failed, by a connection or protocol
       error, by a rejection before any ask (market log 11-02: inextro answered "none in stock" to 228
       orders in a row while its reviews kept it first), or by a deal whose receipt had no sig_seller
       (it does not count in Round 2), is not
       entered while another shop is listed (market log 10-20: outseop-26510122 picked three times,
       ConnectError each time)
2. Too dear compared with others. If my memory has a lower price for this item at another shop that
   is in the list right now (a price I paid or an ask I was quoted), that price becomes my ceiling:
   I never offer above it, and if the shop will not come down to it, I walk away and buy nothing.
3. Talk and haggle. Each move comes from buyer.next_move with chat=True: the harness decides the move
   and writes one short sentence to the seller. The message carries exactly one number, the offer,
   so any shop reading "the first number" or "the last number" reads the right price.
4. Memory (buyer_memory.json): one record per order: shop name (URLs change on every restart), item,
   budget, outcome, price, the asks I saw, my last offer, whether the receipt matched. A receipt
   price above what I agreed marks the shop as dishonest for DISHONEST_HOURS (12 h; for good until 11-08).
5. Round 2. A deal counts only when its receipt is signed, names the ordered item, stays within the
   budget and carries a task id the market has not seen. Before I review, I check the receipt the
   same way: sig_seller present, item = the order, price <= the agreed offer and the budget,
   task = the A2A task id of my negotiation, and that task id never seen in an earlier receipt
   (a replayed receipt). A wrong price, item, task or a replay marks the shop dishonest; a missing
   signature marks it unsigned. Reviews from Round 2 are shown only for verified deals, so the shop
   view counts them apart and the harness is told to trust them more.
6. Output: why I picked the shop, then the result line the kit and the market test read:
   {"shop": <card URL>, "receipt": <data Part as received, or null>, "stars": 1-5, "review": "..."}
7. Round 3 (--order with "acceptable" SKUs). The order names a category; a deal is worth the SKU's list
   price to me (0 if food does not last use_within_hours, minus the price if goods break without warranty).
     - only shops whose offers hold an acceptable SKU in stock are considered
     - at the chosen shop I ask for the acceptable SKU whose market deals there gave the best saved per deal
       ("skubest", 11-08; before: the highest list price within my budget, a rule from when the score was the
       amount saved), with how many hours food must last and, for goods, which grades with a warranty I prefer
     - I never pay more than value_share of the list price (0.75 now, buyer_strategist.py moves it; per
       category caps too) and the shop's usual price + cap_margin; saved is a share of list, so a cheap item at
       40% off counts more than a dear one at 30% off
     - the result line carries "accept": the market's certificate always; food must last
       use_within_hours + 1 h; goods must be grade A or B. The kit pays nothing if the checkout fails it
     - payments.jsonl tells me afterwards when the kit did not pay; that visit counts as failed
       (the shop stays out for FAIL_MEMORY), like a connection error
   Memory keys Round 3 trades by SKU, so "cheaper elsewhere" compares the same product.
   Broken goods: the kit writes only the item's name and when it broke to broken.jsonl. I match each line to
   my latest deal for that name within BREAK_WINDOW before it broke, mark that trade broken and count it
   against the shop (mem["broken"]); the shop view shows "N of M goods broke", and the fallback ranking and
   the harness see it.
9. Paying (optional midterm part): for a signed, honest Round 3 deal, acp_tools.pay() reads the shop's
   acp.json, builds tools from it and lets the model drive the checkout; code keeps the money checks and signs
   the closed mandates. The result line then carries "mandate_receipt" and our kit copy (market_kit.py) uses
   it; if this path does not finish, the kit pays as before.
10. Turned away (Round 3, 11-03: 63 of 212 orders ended on a shop that listed the SKU but had none left, as
   many as all our deals): a rejection before any ask or a connection error moves on to the next shop, up to
   MAX_SHOPS per order while a minute is left; each try is remembered as a failed visit.
8. Market intelligence for our own shop. After each Round 3 negotiation one line goes to market_intel.jsonl:
   the other shop's first ask, lowest ask and deal price against the list price, for that SKU and category.
   The public ledger shows only settled prices; first asks are seen only by a buyer who negotiates, and
   seller_r3.py prices against them. Only negotiations this buyer had anyway: no probing for prices.
   Every food order also logs its use_within_hours there ("type": "need"): restock.py buys only food that will
   still last that long when it sells. Every Round 3 order logs the list-price range of its acceptable SKUs and
   whether any shop had one ("type": "order"): restock.py stocks the price tiers orders ask for and nobody serves
   (11-03: 352 of 410 sofa orders found no shop).
"""
import argparse
import json
import os
import random
import re
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

import harness
from buyer import RpcError, Seller, next_move, read_ask, receipt, said, state
from harness import as_int, decide, log
from buyer_value import expected, table as value_table
from policy import buyer_params

HERE = Path(__file__).resolve().parent
MEMORY = Path(os.environ.get("BUYER_MEMORY", HERE / "buyer_memory.json"))
INTEL = Path(os.environ.get("MARKET_INTEL", HERE / "market_intel.jsonl"))   # read by seller_r3.py
MAX_ROUNDS = 8
DEADLINE = 240          # the kit and the test stop the buyer at 300 s
SEEN_TASKS = 1000      # task ids of receipts I received, to catch a replayed receipt
WARRANTY_MIN = 6          # goods break within 6 h of the sale: a warranty this long covers every break
GRADE_C_MEMORY = 6 * 3600   # a shop that offered a grade-C unit of a SKU is not asked for that SKU again for this long
FAIL_MEMORY = 6 * 3600  # a failed visit keeps a shop out for this long; shops restart and get fixed
REJECT_SHOP_SECS = 15 * 60   # 11-09: an out-of-stock turn-away keeps the shop out this long (other items)
TOOLS_SHARE = 0.05         # deals paid through the shop's ACP tools (the model drives ~12 s of checkout);
                           # the kit pays the rest by code, so orders keep flowing
RIVALS_EVERY = 600         # seconds between Players board reads (user, 11-05: buy from players ranked below us)
ME = "yms979"
MARKET = "https://market.ourolabs.ai"
SLOW_SECS = 60             # median seconds per visit above which a shop is skipped (5+ visits in 24 h)
LOW_VISITS, LOW_RATE = 30, 0.25   # skip a shop that closed under 25 % of 30+ visits in 24 h (if others remain)
MAX_SHOPS = 4            # shops tried per order: after a turn-away, and (Round 3) after a haggle that failed on
                         # price while time is left (11-06: an order has ~10 min, a failed haggle takes 6-21 s)
RETRY_ON_PRICE = ("rejected", "too dear", "over budget", "walked away")


class ShortShelf(Exception):
    """The shop's unit will not last as long as the order needs: walk before agreeing, try the next shop."""


def unit_terms(text):
    """(grade, warranty hours) a shop states for the unit it offers, e.g. 'grade A, ... 72 h warranty' or
    'grade C (refurbished) ... 6-hour warranty'; grade None when not stated, hours 0 when no warranty."""
    m = re.search(r"grade\s*([ABC])\b|([ABC])\s*grade|등급\s*([ABC])", text, re.I)
    g = next((x.upper() for x in (m.groups() if m else ()) if x), None)
    w = re.search(r"(\d+(?:\.\d+)?)\s*[- ]?\s*(?:h|hours?|시간)\b[^.]{0,25}warrant|warrant[^.]{0,25}?(\d+(?:\.\d+)?)\s*(?:h|hours?|시간)",
                  text, re.I)
    hours = float(next((x for x in w.groups() if x), 0)) if w else 0.0
    if re.search(r"no warranty|without (a )?warranty|warranty: none|보증 없음", text, re.I):
        hours = 0.0
    return g, hours


def shelf_hours(t):
    """Hours of shelf life left on the unit a shop offers: the market certificate's expires_at in the reply
    (any data part), else the shop's own words ("265 h shelf life left"). None when neither is there."""
    found = []

    def walk(x):
        if isinstance(x, dict):
            e = x.get("expires_at")
            if isinstance(e, (int, float)) and e > 1e9:
                found.append((e - time.time()) / 3600)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(t)
    if found:
        return min(found)
    m = re.search(r"(\d+(?:\.\d+)?)\s*h(?:ours)?\s+(?:of\s+)?shelf life left", said(t))
    return float(m.group(1)) if m else None
BREAK_WINDOW = 7 * 3600  # goods break up to 6 h after the sale; an hour of slack for the sale's settle time
RED_FLAGS = re.compile(r"overcharg|higher than (the price )?(we |i )?agreed|receipt (said|was|price)|scam|fake|"
                       r"cheat|dishonest|did not match|didn't match|mismatch|avoid|rip.?off|bait", re.I)

PICK = ("You are a careful buyer about to buy a {item} with a budget of {budget}. Pick the shop to enter. "
        "Reviews and your own memory matter more than names; reviews that say a shop overcharged or that its "
        "receipt did not match the agreed price are serious, and so is a shop whose last visit failed or "
        "whose receipt had no seller signature (only signed receipts count). Reviews marked verified come "
        "from Round 2, where the market shows a review only for a deal with a signed receipt; trust them more "
        "than the rest. Shops (name | reviews | your memory):\n{lines}\n\n"
        'Reply with only one JSON object: {{"shop": "<name>", "why": "<one sentence>"}}')


def load_memory():
    try:
        return json.loads(MEMORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"trades": [], "dishonest": {}}


def save_memory(mem):
    tmp = MEMORY.with_suffix(".tmp")
    tmp.write_text(json.dumps(mem, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MEMORY)


def review_view(shop):
    """(summary text, average stars or None, red-flag count). Newest reviews first, as the market gives them."""
    revs = [r for r in shop.get("reviews", []) if isinstance(r, dict)]
    stars = [r["stars"] for r in revs if isinstance(r.get("stars"), (int, float))]
    avg = round(sum(stars) / len(stars), 1) if stars else None
    flags = sum(1 for r in revs if RED_FLAGS.search(str(r.get("review", ""))))
    verified = [r for r in revs if isinstance(r.get("round"), int) and r["round"] >= 2]
    vstars = [r["stars"] for r in verified if isinstance(r.get("stars"), (int, float))]
    newest = "; ".join(f"{r.get('stars')}* \"{str(r.get('review', ''))[:80]}\"" for r in revs[:3])
    text = (f"{len(revs)} reviews" + (f", average {avg} stars" if avg is not None else "")
            + (f", {len(verified)} verified (Round 2, signed receipts)" if verified else "")
            + (f" averaging {round(sum(vstars) / len(vstars), 1)}" if vstars else "")
            + (f", {flags} with red flags" if flags else "") + (f"; newest: {newest}" if newest else ""))
    return text, avg, flags, len(verified)


DISHONEST_HOURS = 12


def dishonest_live(entry):
    """A dishonest-receipt mark keeps the shop out for DISHONEST_HOURS (old string entries: expired)."""
    return isinstance(entry, dict) and time.time() - entry.get("t", 0) < DISHONEST_HOURS * 3600


def failed_last(mem, name, item=None):
    """Why my last visit to `name` failed (error, or a deal with an unsigned receipt), or None."""
    ts = [t for t in mem["trades"] if t["shop"] == name]
    if not ts or time.time() - ts[-1].get("t", 0) > FAIL_MEMORY:
        return None
    last = ts[-1]
    # 11-09: one 'none of that in stock' kept the whole shop out for 6 h, and with no visit the lock never cleared:
    # inextro (our best leg, .449 per deal, 248 deals/24 h) got 1 visit in 7 h, and 32 shops were out at once.
    # An out-of-stock turn-away is about that item: the shop is out for REJECT_SHOP_SECS, the item for FAIL_MEMORY
    if (last.get("outcome") == "rejected" and not last.get("asks") and last.get("item") != item
            and time.time() - last.get("t", 0) > REJECT_SHOP_SECS):
        return None
    if "grade C" in str(last.get("outcome", "")):     # a grade-C unit: only that SKU is skipped (grade_c_skip)
        return None
    if str(last.get("outcome", "")).startswith(("error", "unpaid")):
        return f"last visit failed: {last['outcome']}"
    if last.get("outcome") == "rejected" and not last.get("asks"):
        return "last visit turned me away without an ask (out of stock or not selling)"
    if last.get("outcome") == "deal" and last.get("signed") is False:
        return f"last deal did not count: {last.get('void') or 'the receipt had no seller signature'}"
    return None


def memory_view(mem, name, item):
    ts = [t for t in mem["trades"] if t["shop"] == name]
    if name in mem["dishonest"]:
        return f"CAUGHT CHEATING before: {mem['dishonest'][name]}"
    if not ts:
        return "never traded here"
    failed = failed_last(mem, name, item)
    deals = [t for t in ts if t["outcome"] == "deal"]
    same = [t for t in deals if t["item"] == item]
    goods = [t for t in deals if t.get("kind") == "goods"]
    broke = sum(1 for t in goods if t.get("broken"))
    return (f"{len(ts)} visits, {len(deals)} deals"
            + (f", paid {', '.join(str(t['price']) for t in same[-3:])} for a {item}" if same else "")
            + (f"; {broke} of {len(goods)} goods I bought here broke" if broke else "")
            + (f"; {failed.upper()}" if failed else ""))


ELSEWHERE_WINDOW = 24 * 3600


def best_elsewhere(mem, item, listed, exclude):
    """Lowest price for `item` I actually paid in the last 24 h at another shop that is listed now (not dishonest).
    Asks do not count: a shop can ask low and never close (myeonggi asked 74 for a lamp, closed 2 of 566 visits),
    and an all-time low never expires, so an ask-based ceiling stopped every deal for two days."""
    best, since = None, time.time() - ELSEWHERE_WINDOW
    for t in mem["trades"]:
        if t["item"] != item or t["shop"] == exclude or t["shop"] not in listed or t["shop"] in mem["dishonest"]:
            continue
        if t["outcome"] == "deal" and t.get("price") and t.get("t", 0) >= since:
            if best is None or t["price"] < best[0]:
                best = (t["price"], t["shop"])
    return best


def rivals_above():
    """Shop names of classmates ranked above us on the Players board (cached RIVALS_EVERY s in .market)."""
    path = HERE / ".market" / "rivals.json"
    try:
        c = json.loads(path.read_text())
        if time.time() - c["t"] < RIVALS_EVERY:
            return set(c["names"])
    except (OSError, ValueError, KeyError):
        pass
    try:
        with urllib.request.urlopen(f"{MARKET}/api/board?round=3", timeout=10) as r:
            b = json.load(r)
        # classmates only (the midterm ranks students; shaun0927 is an outside player, user 11-05)
        players = sorted((x for x in b["players"] if x.get("student")), key=lambda x: -(x.get("student_total") or 0))
        mine = next((i for i, x in enumerate(players) if x["player"] == ME), len(players))
        above = {x["player"] for x in players[:mine]}
        names = [s["name"] for s in b["shops"] if (s.get("owner") or {}).get("login") in above]
        path.write_text(json.dumps({"t": time.time(), "names": names}))
        return set(names)
    except Exception:
        return set()


def expected_in(why):
    """The expected saved per deal that choose() named for the picked (or best, when skipping) shop, so the
    curator can show what each expectation band realized (the buyer strategist asked for it on 11-06)."""
    m = re.search(r"expected saved ([0-9.]+)", str(why or ""))
    return round(float(m.group(1)), 3) if m else None


def choose(shops, mem, item, budget, ask_model=True, ctx=None):
    """Round 3 (ctx: stats, kind, cat, bp): the allowed shop whose deals add most to the saved rate (buyer_value:
    the whole market's realized saved per deal there, shrunk to the market mean), plus conv_weight x our close
    rate there, minus rival_penalty for a classmate ranked above us. Returns pick None (skip the order) when the
    best one is expected under skip_below: a deal under our running mean only pulls the score down."""
    views = {}
    for s in shops:
        text, avg, flags, nver = review_view(s)
        views[s["name"]] = {"text": text, "avg": avg, "flags": flags, "verified": nver,
                            "mem": memory_view(mem, s["name"], item)}
    lines = "\n".join(f"- {n} | {v['text']} | {v['mem']}" for n, v in views.items())
    log("buyer", "shops seen:\n" + lines)

    # 11-08: a receipt mismatch used to ban a shop for good; inextro (851 goods settles in 6 h at 0.345 saved, 95%
    # warranted, the market's best goods source) was out since one 1.6M -> 1.7M receipt. Every receipt is still
    # checked before paying, so a ban now lasts DISHONEST_HOURS; old entries without a time count as expired
    caught = {n for n in views if n in mem["dishonest"] and dishonest_live(mem["dishonest"][n])}
    n_reviews = {s["name"]: len(s.get("reviews", [])) for s in shops}
    flagged = {n for n, v in views.items() if v["flags"] or (v["avg"] is not None and v["avg"] <= 2 and n_reviews[n] >= 2)}
    allowed = [s for s in shops if s["name"] not in caught] or shops
    failed = {s["name"] for s in allowed if failed_last(mem, s["name"], item)}
    allowed = [s for s in allowed if s["name"] not in failed] or allowed
    clean = [s for s in allowed if s["name"] not in flagged]
    allowed = clean or allowed
    since, seen = time.time() - 86400, {}                 # our own success with each shop, last 24 h
    for t in mem["trades"]:
        if t.get("t", 0) >= since:
            v = seen.setdefault(t["shop"], [0, 0])
            v[0] += 1
            v[1] += t.get("outcome") == "deal"
    # 11-05: kcwarni closed 4 of 119 visits, bjeon01 7 of 69, while Kiro729 closed 85 %: a shop that rarely
    # closes with us costs the order's time (and orders only come after the last one ends)
    good = [s for s in allowed if seen.get(s["name"], [0, 0])[0] < LOW_VISITS
            or seen[s["name"]][1] / seen[s["name"]][0] >= LOW_RATE]
    allowed = good or allowed
    # 11-05: yeonsong2 and leeuichann took 30-55 s per answer, so one order there took 3-4 minutes and no new
    # order came meanwhile (orders/h fell from ~130 to 66). Skip shops whose visits take SLOW_SECS (median).
    took = defaultdict(list)
    for t in mem["trades"]:
        if t.get("t", 0) >= since and t.get("secs") is not None:
            took[t["shop"]].append(t["secs"])
    fast = [s for s in allowed if len(took.get(s["name"], [])) < 5
            or sorted(took[s["name"]])[len(took[s["name"]]) // 2] < SLOW_SECS]
    allowed = fast or allowed
    rivals = rivals_above()          # buying from a player ranked above us adds to their revenue and book
    if ctx is not None:              # user 11-06: buyer score first; a rival is a penalty, not a wall
        bp = ctx["bp"]

        def value(s):
            e = expected(ctx["stats"], s["name"], ctx["kind"], ctx["cat"])
            if ctx.get("wpen") and ctx["kind"] == "goods":
                # 11-07 A/B "wpen": our no-warranty goods ran 0.244 per deal (16 breaks at -1 in 439) against 0.324
                # with a warranty, below our running mean; charge a shop for the share of its goods sold without one
                e = dict(e, saved=round(e["saved"] - bp.get("wpen_cost", 0.06) * (1 - (e.get("warranty") or 0)), 4))
            v = seen.get(s["name"], [0, 0])
            conv = (v[1] + 1) / (v[0] + 2) if v[0] >= 5 else 0.6
            bias = ((bp.get("shop_auto") or {}).get(s["name"]) or {}).get("shop_bias", 0)   # the strategist's call
            return (e["saved"] + bp["conv_weight"] * conv - (bp["rival_penalty"] if s["name"] in rivals else 0) + bias,
                    e, conv)
        ranked = sorted(((value(s), s) for s in allowed), key=lambda x: -x[0][0])
        # skip only when no allowed shop reaches skip_below; else the best score among those that do
        # user 11-06: food 0.279 vs goods 0.194 saved per deal (goods breaks count -1): a bar per kind
        thr = bp.get(f"skip_below_{ctx['kind']}", bp["skip_below"])
        worth = [r for r in ranked if r[0][1]["saved"] >= thr]
    if ctx is not None and ranked:
        (score, e, conv), pick = (worth or ranked)[0]
        skipped = [n for n in views if n not in {s["name"] for s in allowed}]
        why = (f"value: {pick['name']} expected saved {e['saved']:.3f} per deal ({e['n']} market deals, "
               f"median {e['p50']:.2f} of list, warranty {e['warranty']:.0%}), our close rate {conv:.2f}"
               + (", a rival above us" if pick["name"] in rivals else "")
               + "; next: " + ", ".join(f"{s['name']} {v[1]['saved']:.3f}" for v, s in ranked[1:4]))
        if e["saved"] < thr:
            return None, f"skip: best expected saved {e['saved']:.3f} < skip_below_{ctx['kind']} {thr} ({why})", skipped, views
        return pick, why, skipped, views
    lower = [s for s in allowed if s["name"] not in rivals]
    allowed = lower or allowed

    # with our own record at 2+ of the allowed shops (5+ visits in 24 h) code picks the best closer: the model's
    # pick cost ~9 s an order, and order flow is what holds the buyer's deal count back (rank 29, 11-05)
    known = [s for s in allowed if seen.get(s["name"], [0, 0])[0] >= 5]
    if len(known) >= 2:
        pick = max(known, key=lambda s: ((seen[s["name"]][1] + 1) / (seen[s["name"]][0] + 2), views[s["name"]]["avg"] or 0))
        v = seen[pick["name"]]
        skipped = [n for n in views if n not in {s["name"] for s in allowed}]
        return pick, f"code: best closer with us in 24 h ({v[1]} deals of {v[0]} visits)", skipped, views
    answer = (decide("buyer", PICK.format(item=item, budget=budget, lines=lines))
              if len(shops) > 1 and ask_model else {})       # retries pick by code: ~5 s a model call
    pick = next((s for s in allowed if s["name"] == (answer or {}).get("shop")), None)
    why = (answer or {}).get("why", "")
    if pick is None:
        # the harness picked nothing usable (or a shop the rules exclude): best reviews, then memory
        def score(s):
            v = views[s["name"]]
            return ((v["avg"] if v["avg"] is not None else 3.5) - v["flags"] + min(v["verified"], 5) * 0.1
                    - 0.5 * mem.get("broken", {}).get(s["name"], 0))
        pick = max(allowed, key=score)
        reason = "only shop listed" if len(shops) == 1 else (
            f"the harness chose {(answer or {}).get('shop')!r}, which the rules exclude; best reviewed allowed shop instead"
            if (answer or {}).get("shop") else "best reviewed allowed shop")
        why = why or reason
    skipped = [n for n in views if n not in {s["name"] for s in allowed}]
    return pick, why, skipped, views


def receipt_problems(rec, items, budget, agreed, tid, mem):
    """Round 2 checks I can make myself: (dishonest reasons, why it will not count or '').

    A wrong price, a task that is not mine, or a replayed task id is cheating. A missing signature or
    an item name the market will not match (the kit compares it exactly) is not cheating, but the
    deal does not count in Round 2."""
    rec = rec if isinstance(rec, dict) else {}
    bad = []
    price = as_int(rec.get("price"))
    if price is not None and agreed is not None and price > agreed:
        bad.append(f"agreed {agreed}, receipt {price}")
    if price is not None and price > budget:
        bad.append(f"receipt {price} above my budget {budget}")
    if tid and rec.get("task") and rec["task"] != tid:
        bad.append(f"receipt task {str(rec['task'])[:8]} is not my negotiation {tid[:8]}")
    if rec.get("task") and rec["task"] in mem.get("seen_tasks", []):
        bad.append(f"receipt task {str(rec['task'])[:8]} was used in an earlier receipt (replay)")
    void = ("the receipt had no seller signature" if not rec.get("sig_seller") else
            f"the receipt names {rec.get('item')!r}, not one of the order's items" if rec.get("item") not in items else "")
    return bad, void


def stars_and_review(outcome, item, price, budget, agreed, lowest_ask, rounds, ceiling_note, honest, void="",
                     problems=""):
    if outcome == "deal" and not honest:
        return 1, f"Receipt for the {item} did not match the deal ({problems}). Avoid."
    if outcome == "deal" and void:
        return 2, f"Deal at {price} for the {item}, but {void}, so it does not count."
    if outcome == "deal":
        if price <= budget * 0.9:
            return 5, f"Fair price: {item} for {price} after {rounds} offer(s). Receipt matched the agreed price."
        return 4, f"Came down to {price} for the {item} after {rounds} offer(s); receipt matched."
    if outcome == "too dear":
        return 2, f"Would not come below {lowest_ask} for the {item}; {ceiling_note}. No deal."
    if outcome == "over budget":
        return 3, f"Polite, but the lowest ask for the {item} was {lowest_ask}, above my budget of {budget}. No deal."
    if outcome == "rejected":
        return 2, f"The shop ended the negotiation for the {item} without a deal (lowest ask {lowest_ask})."
    return 2, f"No deal for the {item}: {outcome}."


def note_broken(mem, md):
    """Round 3: broken.jsonl (name, broke_at), read from where I stopped. Each line marks my latest deal for that
    name sold within BREAK_WINDOW before the break, and counts it against the shop."""
    p = md / "broken.jsonl"
    if not p.is_file():
        return
    with open(p, encoding="utf-8") as f:
        f.seek(mem.get("broken_pos", 0))
        for line in f:
            try:
                b = json.loads(line)
            except ValueError:
                continue
            at = b.get("broke_at") or time.time()
            t = next((t for t in reversed(mem["trades"]) if t.get("outcome") == "deal" and t.get("name") == b.get("name")
                      and not t.get("broken") and at - BREAK_WINDOW <= t.get("t", 0) <= at), None)
            if t:
                t["broken"] = True
                mem.setdefault("broken", {})[t["shop"]] = mem.get("broken", {}).get(t["shop"], 0) + 1
                log("buyer", f"BROKEN: {b.get('name')} from {t['shop']} (paid {t.get('price')}), counted against it")
            else:
                log("buyer", f"broken item {b.get('name')!r} matches none of my recent deals")
        mem["broken_pos"] = f.tell()


def note_payments(mem, md):
    """Round 3: the kit's payments.jsonl, read from where I stopped; an unpaid deal turns that trade into
    'unpaid: <reason>' (a failed visit)."""
    p = md / "payments.jsonl"
    if not p.is_file():
        return
    with open(p, encoding="utf-8") as f:
        f.seek(mem.get("pay_pos", 0))
        for line in f:
            try:
                pay = json.loads(line)
            except ValueError:
                continue
            if pay.get("paid") is False:
                t = next((t for t in reversed(mem["trades"]) if t.get("order") == pay.get("order")), None)
                if t:
                    t["outcome"] = f"unpaid: {pay.get('reason', '')}"[:200]
                    if "grade C" in str(pay.get("reason")):
                        mem.setdefault("grade_c", {})[f"{t['shop']}|{t['item']}"] = time.time()
                    log("buyer", f"the kit did not pay {t['shop']} for {t['item']}: {pay.get('reason')}")
        mem["pay_pos"] = f.tell()


def grade_c_skip(mem):
    """'shop|sku' pairs that gave me a grade-C unit (claimed on the receipt or shipped) in the last GRADE_C_MEMORY."""
    now = time.time()
    mem["grade_c"] = {k: t for k, t in (mem.get("grade_c") or {}).items() if now - t < GRADE_C_MEMORY}
    return set(mem["grade_c"])


def pick_sku(shop, acceptable, catalog, budget, share=0.97, skip=(), stats=None):
    """The acceptable SKU in stock at `shop` with the highest list price I can pay for, else the cheapest one.
    `skip`: 'shop|sku' pairs that came in grade C lately (the kit would not pay for them).
    With `stats` (11-08 A/B "skubest"): among those I can pay for, the SKU whose market deals at this shop gave the
    best saved per deal (3+ deals; others count at the shop's level minus 0.01). The highest list price was the
    rule when the score was the amount saved; saved is a share now, and the dearest SKUs came mostly as new
    grade A stock, while the same shops sold B/C with a warranty to other buyers."""
    offers = [o for o in shop.get("offers") or [] if o.get("sku") in acceptable and (o.get("qty") or 0) > 0
              and f"{shop.get('name')}|{o.get('sku')}" not in skip]
    price = lambda o: (catalog.get(o["sku"]) or {}).get("list_price") or 0
    fits = [o for o in offers if 0 < price(o) * share <= budget]
    if stats is not None and len(fits) > 1:
        def sku_saved(o):
            g = stats.get(f"{shop.get('name')}|sku|{o['sku']}") or {}
            return g["saved"] if g.get("n", 0) >= 3 else -1.0
        best = max(fits, key=lambda o: (sku_saved(o), price(o)))
        if sku_saved(best) > -1.0:
            fits = [best]
    o = max(fits, key=price) if fits else min(offers, key=price, default=None)
    return o and {"sku": o["sku"], "name": o.get("name") or o["sku"], "list_price": price(o)}


def no_deal(review, stars=3):
    print(json.dumps({"shop": "", "receipt": None, "stars": stars, "review": review}, ensure_ascii=False), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    ap.add_argument("--order", help="Round 3: order.json (category, acceptable SKUs, use_within_hours)")
    a = ap.parse_args()
    start = time.time()
    shops = [s for s in json.load(open(a.shops, encoding="utf-8")) if s.get("url")]
    mem = load_memory()
    log("buyer", f"harness: {harness.HARNESS_CMD}; {a.item}, budget {a.budget}, {len(shops)} shops, "
                 f"memory {len(mem['trades'])} trades, {len(mem['dishonest'])} dishonest")

    order = {}
    if a.order:
        try:
            order = json.load(open(a.order, encoding="utf-8"))
        except (OSError, ValueError):
            order = {}
    acceptable = order.get("acceptable") or []
    vctx = None
    pricefirst = wpen = cheapfood = cheapgoods = skubest = gradeaware = False
    md = Path(a.order).parent if a.order else HERE / ".market"
    regime, bp = "STEADY", {}
    if acceptable:
        regime, bp, _ = buyer_params()
        pricefirst = random.random() < bp.get("pricefirst_share", 0)
        wpen = random.random() < bp.get("wpen_share", 0)
        cheapfood = order.get("kind") == "food" and random.random() < bp.get("cheapfood_share", 0)
        cheapgoods = order.get("kind") == "goods" and random.random() < bp.get("cheapgoods_share", 0)
        skubest = random.random() < bp.get("skubest_share", 0)
        gradeaware = order.get("kind") == "goods" and random.random() < bp.get("gradeaware_share", 0)
        if order.get("kind") == "food" and order.get("use_within_hours"):
            try:                                   # for our shop's restock: how long buyers need food to last
                with open(INTEL, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"t": int(time.time()), "type": "need", "category": order.get("category"),
                                        "hours": order["use_within_hours"]}, ensure_ascii=False) + "\n")
            except OSError:
                pass
        note_payments(mem, md)
        note_broken(mem, md)
        catalog = json.load(open(md / "catalog.json", encoding="utf-8"))
        share = (bp.get("cat_auto", {}).get(a.item) or {}).get("cap", bp["value_share"])   # buyer_strategist.py
        cskip = grade_c_skip(mem)
        # 11-07 A/B "pricefirst" (independent of the push arms): rank shops by price alone. conv_weight 0.1 gave a
        # shop that often agrees +0.06 over one that rarely does (psyoung82 over olstjr, whose buyers pay 0.59
        # against psyoung82's 0.71), though a failed try costs seconds and the next shop is tried; rival_penalty
        # 0.04 pushed down the cheap food at Kiro729 for a revenue effect on them our ~70 deals/day barely move
        sbp = dict(bp, conv_weight=bp.get("pricefirst_conv_weight", 0.02), rival_penalty=0) if pricefirst else bp
        vctx = {"stats": value_table(), "kind": order.get("kind") or "goods", "cat": order.get("category"), "bp": sbp,
                "wpen": wpen}
        shops = [s for s in shops if pick_sku(s, acceptable, catalog, a.budget, share, cskip)]
        prices = [(catalog.get(k) or {}).get("list_price") or 0 for k in acceptable]
        try:                                       # for our shop's restock: which prices this order accepts,
            with open(INTEL, "a", encoding="utf-8") as f:   # and whether any shop could serve it
                f.write(json.dumps({"t": int(time.time()), "type": "order", "category": order.get("category"),
                                    "kind": order.get("kind"), "budget": a.budget, "lo": min(prices, default=0),
                                    "hi": max(prices, default=0), "served": bool(shops)}, ensure_ascii=False) + "\n")
        except OSError:
            pass
        if not shops:
            log("buyer", f"no shop has any of the {len(acceptable)} acceptable SKUs for {a.item} in stock")
            save_memory(mem)
            no_deal(f"No shop had {a.item} in stock.")
            return

    max_shops = int(bp.get("max_shops", MAX_SHOPS)) if acceptable else MAX_SHOPS   # strategist knob
    for attempt in range(max_shops):              # a shop that turns me away before any ask: try the next one
        pick, why, skipped, views = choose(shops, mem, a.item, a.budget, ask_model=attempt == 0, ctx=vctx)
        t_shop = time.time()
        if pick is None:                           # every shop is expected under skip_below: no deal beats a bad one
            log("buyer", why)
            mem["trades"].append({"t": int(time.time()), "secs": 0, "shop": "", "item": "", "budget": a.budget,
                                  "order": order.get("order"), "category": a.item, "kind": order.get("kind"),
                                  "outcome": "skipped (low value)", "price": None, "asks": [], "agreed": None,
                                  "honest": True, "signed": True, "void": "", "expected": expected_in(why),
                                  "shopsel": "pricefirst" if pricefirst else "base", "wpen": wpen, "cheapfood": cheapfood, "cheapgoods": cheapgoods})
            save_memory(mem)
            no_deal(f"Skipped: no shop sells {a.item} cheaply enough for me now.")
            return
        print(f"WHY: picked {pick['name']} ({views[pick['name']]['text']}; memory: {views[pick['name']]['mem']}). {why}"
              + (f" Not entered: {', '.join(skipped)}." if skipped else ""), flush=True)

        item, label, cap, accept, aim, p25 = a.item, f"a {a.item}", a.budget, None, "", None
        first = f"Hi! I'd like to buy a {a.item}. What's your best price?"
        if acceptable:
            want = pick_sku(pick, acceptable, catalog, a.budget, share, cskip, stats=vctx["stats"] if skubest else None)
            item, label = want["sku"], f"{want['name']} ({want['sku']})"
            cap = min(a.budget, int(want["list_price"] * share))
            ev = expected(vctx["stats"], pick["name"], vctx["kind"], vctx["cat"])
            if ev["n"] >= 10:      # do not pay above what this shop usually takes from other buyers
                p25 = ev["p25"]
                cap = min(cap, int(want["list_price"] * (ev["p50"] + bp["cap_margin"])))
            adj = ((bp.get("shop_auto") or {}).get(pick["name"]) or {}).get("shop_cap", 0)   # strategist, per shop
            if adj:
                cap = max(1, min(a.budget, int(cap + want["list_price"] * adj)))
                aim = (f"Other buyers usually settle with this shop at {int(want['list_price'] * ev['p25'])}-"
                       f"{int(want['list_price'] * ev['p50'])}; aim for the low end.")
            hours = order.get("use_within_hours")
            first = f"I want to buy {label}." + (f" It must last {hours:g} hours." if hours else
                                                 " Grade A or B only, please." if order.get("kind") == "goods" else "")
            if cheapgoods and order.get("kind") == "goods":
                # 11-07 A/B "cheapgoods": market 6 h, goods with a warranty: A 0.261 saved per deal, B 0.362, C 0.460
                # (a break under warranty is refunded and counts +1); 81% of our goods were grade A. Ask for B or C
                # with a warranty first; without one only A or B is paid (the accept check below)
                first = (f"I want to buy {label}. I prefer grade C (refurbished) or B (damaged box) with a warranty of"
                         f" at least {WARRANTY_MIN} hours on the receipt, priced for its grade; grade A only if you have"
                         " nothing else. Please quote your cheapest unit that way.")
            if cheapfood and hours:
                # 11-07 A/B "cheapfood": food grade is only shelf life left, so any unit that lasts serves the order;
                # Kiro729 buys 43% grade C / 1% A at 0.577 of list against our 21% A at 0.662 (market A 0.746,
                # B 0.668, C 0.594). Ask for the cheapest unit that lasts, and open low (below) to draw the floor
                first += (" Any grade is fine as long as it lasts that long: please quote your cheapest unit that"
                          " does, and say how many hours it has left.")
            accept = {"cert": True}
            if order.get("kind") == "food" and hours:
                accept["min_hours_left"] = hours + bp["food_extra_hours"]
            elif order.get("kind") == "goods":
                accept["grades"] = bp["goods_grades"]
            log("buyer", f"{item} list price {want['list_price']}, cap {cap} ({regime}), accept {accept}")

        listed = {s["name"] for s in shops}
        elsewhere = best_elsewhere(mem, item, listed, pick["name"])
        ceiling_note = ""
        if elsewhere and elsewhere[0] < cap:      # a target, not a limit: as a hard cap it lost 245 of 612 orders
            ceiling_note = f"{elsewhere[1]} offered it for {elsewhere[0]}"   # (shop floors sat above it, below list)
            log("buyer", f"target {elsewhere[0]}: cheaper elsewhere ({ceiling_note}); I still pay up to {cap}")

        outcome, price, rec, asks, agreed, history, tid = "no answer", None, None, [], None, [], None
        # 11-07 A/B: at psyoung82 (our main food shop, 159 of 260 food deals in 6 h) we paid a median 0.71 of
        # list and dragonash 0.65; we take the shop's second ask (~0.65-0.73) as soon as it is under our cap.
        # In the "push" arm, an ask still above the shop's lower quartile + push_band gets one more counter
        # (halfway down to that quartile) before we take it; a rejection that names a floor reopens at it below.
        regraded = False
        graded = None     # 11-08 gradeaware: (grade, warranty hours) read from the shop's first reply
        short = None    # 11-07: 43 of 69 lost food orders in 6 h agreed a price, then the kit refused to pay (shelf life)
        push = random.random() < bp.get("push_share", 0)
        pushed = False
        # 11-07 push3: 39 of 89 push2 counters were taken at exactly our number, so the shops had room below it;
        # push3 aims at the shop's p25 - push3_below (0.10) and may counter twice. Split of the push arm.
        arm = "push3" if push and random.random() < bp.get("push3_share", 0) else "push2"
        below = bp.get("push3_below", 0.10) if arm == "push3" else bp.get("push_below", 0.05)
        npush, max_push = 0, 2 if arm == "push3" else 1
        try:
            seller = Seller(pick["url"])
            t = seller.send(first)
            tid, ctx = t.get("id"), t.get("contextId")
            for _ in range(MAX_ROUNDS):
                if state(t) not in ("TASK_STATE_INPUT_REQUIRED", "TASK_STATE_UNKNOWN") or time.time() - start > DEADLINE:
                    break
                if gradeaware and graded is None and acceptable and order.get("kind") == "goods":
                    # 11-08 A/B "gradeaware" (user: the last lever for the buyer rank): the shop states the unit's grade
                    # and warranty in its first reply. A warranted unit scores (1 - x) + p_break * x (a refunded break
                    # counts +1): C (p~0.16) still beats our running mean at x 0.78, B (p~0.057) at x 0.72, while A
                    # (p~0.013) needs x <= ~0.70. So a warranted C/B gets a higher cap and no extra counter that could
                    # lose it; an unwarranted C would be refused at checkout anyway, so we leave for the next shop now
                    graded = unit_terms(said(t))
                    g_, w_ = graded
                    if g_ in ("B", "C") and w_ >= WARRANTY_MIN:
                        cap = max(cap, min(a.budget, int(want["list_price"] * bp.get(f"gradeaware_cap_{g_.lower()}", 0.75))))
                        npush = max_push          # no extra counter (keeps the push A/B labels intact)
                        log("buyer", f"{pick['name']} offers grade {g_} with a {w_} h warranty: cap raised to {cap}, no push")
                    elif g_ == "C" and w_ < WARRANTY_MIN:
                        log("buyer", f"{pick['name']} offers grade C without a warranty: next shop")
                        outcome = "grade C claimed"
                        seller.cancel(tid)
                        raise ShortShelf("grade C unwarranted")
                if (cheapgoods and not regraded and acceptable and order.get("kind") == "goods"
                        and re.search(r"grade\s*A\b|A\s*grade|등급\s*A|A\s*등급", said(t), re.I)):
                    # 11-08: in the cheapgoods arm 71 of 94 goods still came as grade A (shops pick the unit; 14 C
                    # with a warranty did come): ask once for a C or B unit with a warranty before haggling
                    regraded = True
                    log("buyer", f"{pick['name']} offered grade A: asking once for a C or B unit with a warranty")
                    t = seller.send(f"Do you have a grade C or B unit of {label} with a warranty of at least "
                                    f"{WARRANTY_MIN} hours on the receipt? I would rather take that one, priced for "
                                    "its grade. If not, tell me and we go on with this unit.", tid, ctx)
                    tid, ctx = t.get("id") or tid, t.get("contextId") or ctx
                    continue
                if short is None and acceptable and order.get("kind") == "food" and accept.get("min_hours_left"):
                    hl = shelf_hours(t)
                    short = hl is not None and hl < accept["min_hours_left"]
                    if short:
                        log("buyer", f"{pick['name']}'s unit has {hl:.1f} h of shelf life, the order needs "
                                     f"{accept['min_hours_left']}: not haggling, trying another shop")
                        break
                ask = read_ask(t)[0]
                if ask is not None:
                    asks.append(ask)
                near = acceptable and ask is not None and cap < ask <= min(a.budget, cap + want["list_price"] * bp["near_cap"])
                # 11-07 push2: the quick accept took first asks up to 0.70 of list without a counter (Pringles at
                # 0.59 while Kiro729 sold it at 0.41); in the push arm a low first ask still gets one counter
                low_first = (push and npush < max_push and not near and ask is not None and p25 and vctx is not None
                             and bp.get("code_haggle") and ask > want["list_price"] * max(0.4, p25 - below))
                if not low_first and (near or (acceptable and ask is not None and ask <= cap
                                               and ask <= want["list_price"] * bp["quick_accept"])):
                    # near: 11-06, 43 of 54 rejections in an hour came after asks within ~1-2 % of list of our cap
                    # (ask 0.70, our offer 0.69); taking such an ask keeps saved ~0.29-0.30 and saves the order
                    # already at or under a typical deal price (median paid ~0.75-0.79 of list): take it now.
                    # Haggling costs ~5 s a model call; order flow, not a few % of price, held the buyer back
                    log("buyer", f"ask {ask} is {ask / want['list_price']:.0%} of list: taking it")
                    agreed = ask
                    t = seller.send(str(ask), tid, ctx)
                    tid, ctx = t.get("id") or tid, t.get("contextId") or ctx
                    continue
                if vctx is not None and bp.get("code_haggle") and ask is not None:
                    # 11-06: a model move took 4-5 s and an order 17-34 s (80-170 s over several shops), and the next
                    # order only comes when this one ends; the price logic was code already (cap, shop's band).
                    # Open just under the shop's lower quartile, then halfway to the cap, then the cap; take any
                    # ask at or under the cap; after 3 offers walk (the next shop is tried).
                    mine = [p for k, p in history if k == "you offered"]
                    history.append(("seller asked", ask))
                    lp = want["list_price"]
                    prev = [a for k, a in history[:-1] if k == "seller asked"]
                    if (push and npush < max_push and ask <= cap and p25 and len(mine) < 1 + max_push
                            and ask > lp * max(0.4, p25 - below)):
                        # counter halfway down to (shop p25 - push_below); push2 (11-07 07:00) also reaches here
                        # for low first asks the quick accept used to take, and aims below p25, not at it
                        pushed, npush = True, npush + 1
                        aim_p = int(lp * max(0.4, p25 - below))
                        action, offer = "offer", max(mine[-1] if mine else 0, (ask + aim_p) // 2)
                    elif ask <= cap:
                        action, offer = "offer", ask
                    elif len(mine) >= 3 and prev and ask >= prev[-1]:
                        action, offer = "walk", None       # the shop stopped coming down: next shop
                    elif len(mine) >= 3:
                        action, offer = "offer", cap       # still coming down (rounds take < 1 s): hold at the cap
                    elif not mine:
                        action, offer = "offer", min(cap, int(lp * (bp.get("cheapfood_open", 0.3) if cheapfood else
                                                                     max(0.4, (p25 or cap / lp) - bp.get("open_offset", 0.02)))))
                    elif len(mine) == 1:
                        action, offer = "offer", max(mine[-1], (mine[-1] + cap) // 2)
                    else:
                        action, offer = "offer", cap
                    if offer is not None:
                        history.append(("you offered", offer))
                    ask2, say = ask, ""
                else:
                    action, offer, ask2, say = next_move(
                    "buyer", label, cap, history, t, chat=True,
                    ledger=((f"Another shop sold this item for {elsewhere[0]} recently; aim near that, but this shop "
                             f"ends the talk after 2 or 3 low offers, so rather pay up to {cap} than lose the deal. "
                             if ceiling_note else "") + aim).strip())
                if ask2 is not None and ask2 not in asks:
                    asks.append(ask2)
                if action == "walk":
                    break
                accepting = ask2 is not None and offer >= ask2
                if accepting or re.search(r"number|numeric", said(t), re.I):
                    text = str(offer)        # 38 of 38 walk-aways on 11-05 had agreed, but the shop wanted the number
                else:                        # alone ("send an unconditional numeric offer") and re-quoted until we left
                    text = f"{say + ' ' if say else ''}I can offer {offer}."
                agreed = offer
                t = seller.send(text, tid, ctx)
                tid, ctx = t.get("id") or tid, t.get("contextId") or ctx
            if short:
                seller.cancel(tid)
                raise ShortShelf()
            named = re.search(r"(?:below|final price (?:is|was)|last ask was|Lowest for this unit:|our lowest is|"
                              r"cost floor \()\s*(\d[\d,]*)|(\d[\d,]*) is as low as we go", said(t))
            floor = as_int((named.group(1) or named.group(2)).replace(",", "")) if named else None
            if (state(t) == "TASK_STATE_REJECTED" and floor
                    and floor <= min(a.budget, cap + (want["list_price"] * bp["near_cap"] if acceptable else 0))
                    and time.time() - start < DEADLINE - 30):
                # most shops end the talk after 2 low offers and only then name their floor (146 of 221 rejections,
                # 65 of them under our cap): open a new talk and offer that floor, up to 3 times
                log("buyer", f"{pick['name']} named {floor} after ending the talk (cap {cap}); opening again at it")
                t = seller.send(first)
                tid, ctx = t.get("id"), t.get("contextId")
                for _ in range(3):
                    if state(t) not in ("TASK_STATE_INPUT_REQUIRED", "TASK_STATE_UNKNOWN"):
                        break
                    ask = read_ask(t)[0]
                    if ask is not None:
                        asks.append(ask)
                    agreed = min(floor, ask) if ask else floor
                    t = seller.send(f"Deal, I will take it at {agreed}.", tid, ctx)
                    tid, ctx = t.get("id") or tid, t.get("contextId") or ctx
            if state(t) == "TASK_STATE_COMPLETED":
                rec = receipt(t)
                price = as_int(rec.get("price"))
                outcome = "deal"
                claim = rec.get("claim")
                warranty = as_int(rec.get("warranty_hours")) or 0
                if acceptable and order.get("kind") == "goods" and warranty >= WARRANTY_MIN:
                    # a broken unit under warranty is refunded and counts +1 instead of -1, so any grade pays
                    # (C breaks 7 % of the time: with a warranty that is worth more than an A without one)
                    accept["grades"] = ["A", "B", "C"]
                    accept["warranty_hours"] = WARRANTY_MIN
                elif acceptable and order.get("kind") == "goods" and claim in ("A", "B", "C") \
                        and claim not in bp["goods_grades"]:
                    # the kit refuses to pay for it at checkout anyway (40 orders lost this way on 11-06):
                    # leave the receipt unpaid and try the next shop while there is time
                    mem.setdefault("grade_c", {})[f"{pick['name']}|{item}"] = time.time()
                    log("buyer", f"{pick['name']} signed {item} as grade {claim}; not paying, trying elsewhere")
                    outcome, rec, price = f"grade {claim} claimed", None, None
            elif state(t) == "TASK_STATE_REJECTED":
                outcome = "rejected"
            else:
                low = min(asks) if asks else None
                outcome = "too dear" if ceiling_note and low is not None and low > cap and low <= a.budget else (
                    "over budget" if low is not None and low > cap else "walked away")
                seller.cancel(tid)
        except ShortShelf as e:
            outcome = "grade C claimed" if "grade C" in str(e) else "too short (shelf life)"
        except (RpcError, Exception) as e:
            log("buyer", f"trade failed: {e!r}")
            outcome = f"error ({type(e).__name__})"
        turned_away = (outcome == "rejected" and not asks) or outcome.startswith(("error", "grade ", "too short"))
        on_price = vctx is not None and outcome in RETRY_ON_PRICE   # Round 3: the next-best shop may take our price
        others = [s for s in shops if s["name"] != pick["name"]]
        if (turned_away or on_price) and others and attempt < max_shops - 1 and time.time() - start < DEADLINE - 60:
            mem["trades"].append({"t": int(time.time()), "secs": int(time.time() - t_shop), "shop": pick["name"],
                                  "item": item, "budget": a.budget,
                                  "order": order.get("order"), "category": a.item if acceptable else None,
                                  "kind": order.get("kind"), "outcome": outcome, "price": None, "asks": asks,
                                  "agreed": agreed, "honest": True, "signed": True, "void": "",
                                  "shopsel": "pricefirst" if pricefirst else "base", "wpen": wpen, "cheapfood": cheapfood, "cheapgoods": cheapgoods})
            log("buyer", f"{pick['name']} {'turned me away' if turned_away else 'did not meet my price'} ({outcome}); "
                         f"trying another shop ({attempt + 2}/{max_shops})")
            shops = others
            continue
        break

    bad, void = receipt_problems(rec, {a.item, *acceptable}, a.budget, agreed, tid, mem) if outcome == "deal" else ([], "")
    honest = not bad
    if rec and rec.get("task"):
        mem["seen_tasks"] = (mem.get("seen_tasks", []) + [rec["task"]])[-SEEN_TASKS:]
    if void:
        log("buyer", f"UNSIGNED: {pick['name']}: {void}; remembered, the deal does not count in Round 2")
    lowest = min(asks) if asks else None
    stars, review = stars_and_review(outcome, label if acceptable else a.item, price, cap, agreed, lowest,
                                     len([h for h in history if h[0] == "you offered"]), ceiling_note, honest, void, "; ".join(bad))
    if not honest:
        mem["dishonest"][pick["name"]] = {"t": time.time(), "why": f"{'; '.join(bad)} ({label})"}
        log("buyer", f"DISHONEST: {pick['name']}: {'; '.join(bad)}; remembered")
    if acceptable and asks and want.get("list_price"):
        try:
            with open(INTEL, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": int(time.time()), "shop": pick["name"], "sku": item,
                                    "category": order.get("category"), "list_price": want["list_price"],
                                    "first_ask": asks[0], "min_ask": min(asks),
                                    "price": price if outcome == "deal" else None, "outcome": outcome},
                                   ensure_ascii=False) + "\n")
        except OSError:
            pass
    mem["trades"].append({"t": int(time.time()), "secs": int(time.time() - t_shop), "shop": pick["name"],
                          "item": item, "budget": a.budget,
                          "order": order.get("order"), "category": a.item if acceptable else None,
                          "name": want["name"] if acceptable else None, "kind": order.get("kind"),
                          "outcome": outcome, "price": price, "asks": asks, "agreed": agreed, "honest": honest,
                          "signed": not void, "void": void,
                          "warranty": as_int((rec or {}).get("warranty_hours")) or 0 if outcome == "deal" else 0,
                          "cap": cap, "list": want["list_price"] if acceptable else None,
                          "expected": expected_in(why),      # the shop's expected saved per deal when chosen
                          "shopsel": "pricefirst" if pricefirst else "base", "wpen": wpen, "cheapfood": cheapfood, "cheapgoods": cheapgoods, "regraded": regraded, "skubest": skubest,
                          "gradeaware": gradeaware, "graded": list(graded) if graded else None,
                          "push": (f"{arm}-pushed" if pushed else f"{arm}-arm") if push else "control2"})   # 11-07 A/B
    save_memory(mem)
    log("buyer", f"done: {outcome}" + (f" at {price}" if price is not None else "")
                 + f", {time.time() - start:.0f} s, harness calls {harness.CALLS['n']}")
    out = {"shop": pick["url"], "receipt": rec if outcome == "deal" else None, "stars": stars, "review": review}
    if accept:
        out["accept"] = accept
    mfile = md / "mandates.json"
    tf = mem.setdefault("tools_fail", {"n": 0, "t": 0})   # breaker: 3 failures in a row -> the kit pays for an hour
    tools_off = tf["n"] >= 3 and time.time() - tf["t"] < 3600
    if tools_off and acceptable and outcome == "deal":
        log("buyer", f"ACP tools off after {tf['n']} failures in a row; the kit pays")
    sampled = random.random() < TOOLS_SHARE
    if (acceptable and outcome == "deal" and honest and not void and pick.get("pubkey") and mfile.is_file()
            and not tools_off and sampled):
        try:                                       # pay through the shop's ACP tools (acp_tools.py)
            m = json.loads(mfile.read_text(encoding="utf-8"))
            if m.get("order") == order.get("order") and m.get("mandates"):
                from acp_tools import pay
                r2 = dict(rec)
                if isinstance(r2.get("price"), float) and r2["price"].is_integer():
                    r2["price"] = int(r2["price"])       # A2A carries numbers as doubles; the checkout wants an int
                mr, how = pay(order, m["mandates"], str(pick["url"]).rstrip("/"), pick["pubkey"], r2, accept)
                log("buyer", f"ACP tools: {'paid, ledger #' + str(mr.get('ledger')) if mr else 'not paid'} ({how})")
                if mr:
                    out["mandate_receipt"] = mr
                    tf.update(n=0)
                elif how.startswith("refused"):         # our own policy refusing is not a failure; the kit
                    out["receipt"] = None               # would refuse the same receipt (accept) and the tools'
                    mem["trades"][-1]["outcome"] = f"unpaid: not paying: {how[len('refused'):].lstrip(': ')}"
                    log("buyer", "not handing the receipt to the kit: the tools' session is open, so its checkout "
                                 "would only answer 'already has a checkout session' (79 a day on 11-06)")
                else:
                    tf.update(n=tf["n"] + 1, t=time.time())
                save_memory(mem)
        except Exception as e:                     # the kit pays the old way
            log("buyer", f"ACP tools failed: {type(e).__name__}: {e}; the kit pays instead")
            tf.update(n=tf["n"] + 1, t=time.time())
            save_memory(mem)
    print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:      # still end with a result line the kit and the test can read
        import traceback
        log("buyer", f"error: {e!r} at " + " < ".join(f"{f.name}:{f.lineno}" for f in reversed(traceback.extract_tb(e.__traceback__))))
        try:
            first = json.load(open(sys.argv[sys.argv.index("--shops") + 1], encoding="utf-8"))[0]["url"]
        except Exception:
            first = ""
        print(json.dumps({"shop": first, "receipt": None, "stars": 2, "review": "No deal: my buyer hit an error."}))
        sys.exit(1)
