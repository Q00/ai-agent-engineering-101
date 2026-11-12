"""Buyer strategist: the buyer's own analyst, beside strategist.py (the shop's) (user, 11-06).

    python buyer_strategist.py [--force]      (watch_round.sh runs it every 15 min; it works once an hour)

Since Round 3 patch 3.4 the buyer scores 0.5 saved rate (the mean share of list price kept per deal; a deal that
served nothing counts -1) + 0.3 forum + 0.1 deals + 0.1 posts. The shop's strategist does not see any of that,
so the buyer gets its own loop, the same shape:
  memory    buyer_memory.json (every visit: shop, SKU, category, asks, outcome, price) read in windows
  learn     its 6 h predictions are scored from buyer_memory; per-kind step limits, reverts, playbook rollback and
            a critic call after misses, all through strategist_memory.Memory (its own file)
  evidence  the buyer's board components with the ladder above it, our saved rate per deal now vs the running
            mean, per category and per shop: visits, deals, conversion, mean saved, first ask / list
  schedule  every 30 min in the even 15-minute slots (:00, :30); the shop strategist takes the odd ones, and each
            reads the other's read and notes from the shared SQLite exchange (strategist_memory.post_board)
  goal      first place among buyers on buyer ALL, as fast as possible (user, 11-06)
  decide    MODEL (opus) moves whitelisted knobs: value_share (cap, share of list), quick_accept, food_extra_hours,
            and per category "cap"; each with a checkable prediction
  act       mode as the shop strategist's (params.json strategist.mode); "apply" writes params_auto_buyer.json,
            which policy.py lays over the buyer's parameters (market_buyer reads the category cap)
  curator   (11-06, user) memory_curator.py measures the market (market_picture.py), watches rules, rank and
            saved_speed, answers this strategist's "requests" and briefs it through the digest (read_digest);
            moves are judged on saved_speed = deals/h x (saved per deal - our saved) (predictions scored after 1-6 h)
Log: logs/buyer_strategist.log.
"""
import argparse
import json
import sys
from collections import Counter
import time
from pathlib import Path

import market_probe as probe
import model_env
import strategist as shop
from harness import last_json_object
from strategist_memory import Memory, buyer_picture, market_picture, operator_note, my_turn, run_gate, post_board, read_board, read_digest, valid_requests

HERE = Path(__file__).resolve().parent
BUYER_WAKE = HERE / ".market" / "buyer_strategist_wake"   # 11-11: written by guards.py; honored at most hourly
AUTO = HERE / "params_auto_buyer.json"
STATE = HERE / "buyer_strategist_memory.json"
LOGF = HERE / "logs" / "buyer_strategist.log"
BMEM = HERE / "buyer_memory.json"
OPERATOR_NOTE = HERE / "operator_to_buyer.json"   # 11-08: the operator (with the user) asks; you answer (answer file)
OPERATOR_ANSWER = HERE / "buyer_answer_to_operator.json"
CATALOG = HERE / ".market" / "catalog.json"
EVERY = 3600 - 120
MAX_MOVES = 8
GLOBAL = {                                    # role, bounds, step
    "value_share": ((0.65, 0.85), 0.03),      # the most the buyer pays, as a share of list price
    "quick_accept": ((0.55, 0.75), 0.03),     # an ask at or under this share of list is taken at once
    "food_extra_hours": ((0.25, 12), 2),      # food must last the order's hours + this (a spoiled deal counts -1); floor 1 -> 0.25 on 11-11 (10 near-misses within 1 h per 6 h)
    "skip_below": ((0.15, 0.33), 0.02),       # skip an order when the best shop's expected saved per deal is under this
    "skip_below_food": ((0.05, 0.33), 0.03),  # the same bar for food orders (user 11-06: take every food order that lasts)
    "skip_below_goods": ((0.15, 0.4), 0.03),
    "value_half_life_h": ((0, 6), 1),         # 11-09: weight half-life of market deals in the shop values (0 = flat 6 h)  # the same bar for goods orders (goods ran 0.194 per deal with breaks at -1)
    "conv_weight": ((0.0, 0.3), 0.05),        # shop pick: expected saved + this x our close rate there
    "rival_penalty": ((0.0, 0.1), 0.02),      # shop pick: minus this for a classmate shop ranked above us
    "cap_margin": ((0.0, 0.1), 0.02),         # cap at most (shop's median price / list + this) x list
    "near_cap": ((0.0, 0.04), 0.005),         # an ask within this x list above the cap is taken (near misses)
    "open_offset": ((0.0, 0.08), 0.01),       # code haggling opens at (shop's lower-quartile price/list - this) x list
    "max_shops": ((1, 6), 1),                 # shops tried per order (an order comes every ~20 s; time under that is free)
    # 11-08 (user: like the shop, the buyer agent owns all of the buyer): the negotiation and choice arms the
    # operator ran as A/B tests, now yours; each *_share is the share of orders that use it (the rest is control)
    "push_share": ((0.0, 1.0), 0.3),          # counter once more on an ask still above the shop's p25 band
    "push3_share": ((0.0, 1.0), 0.3),         # of those, the deeper push3 (aim p25 - push3_below, two counters)
    "push3_below": ((0.0, 0.2), 0.02),
    "push5_share": ((0.0, 1.0), 0.25),
    "talk_share": ((0.0, 1.0), 0.25),         # 11-11: share of orders that first ask the shop for its lowest price, then haggle        # 11-11: of pushed orders, the deep push (offer the shop's low edge, climb in small steps)
    "push_below": ((0.0, 0.15), 0.02),        # push2's aim under p25
    "cheapfood_share": ((0.0, 1.0), 0.3),     # food: ask for the cheapest unit that lasts and open low
    "cheapfood_open": ((0.2, 0.6), 0.05),     # food opening offer, share of list
    "cheapgoods_share": ((0.0, 1.0), 0.3),
    "skip_delta_food": ((-0.15, 0.10), 0.02),   # 11-12 patch 3.6: the saved scale is ~2x wider (mean .72 vs .31)  # 11-11: skip bar = m* + delta (m* from buyer_score.json)
    "skip_delta_goods": ((-0.20, 0.10), 0.02),   # floor -0.06 -> -0.10 on 11-11 (user: volume; the grade ceilings at the receipt guard the value)
    "target_mean": ((0.60, 0.90), 0.02),      # 11-11: the mean a goods deal must beat (m*) until buyer_score.json writes it
    "goods_delta": ((0.0, 0.15), 0.02),       # 11-11: margin over m* a goods deal must clear after its grade's break rate
    "needwarr_share": ((0.0, 1.0), 0.3),      # 11-09 (user): goods orders that require a 6 h warranty before haggling
    "aim15_share": ((0.0, 1.0), 0.3),         # 11-09 (user): of the aimtop orders, the share capped at p15, not p25
    "aimtop_share": ((0.0, 1.0), 0.3),        # 11-09: cap at the shop's p25 (what the best buyers pay), not p50    # goods: ask for C/B with a warranty first, A only if nothing else
    "skubest_share": ((0.0, 1.0), 0.3),       # pick the SKU with the best market saved at the shop (not the dearest)
    "wpen_share": ((0.0, 1.0), 0.3),          # charge shops for goods sold without a warranty
    "wpen_cost": ((0.0, 0.12), 0.02),
    "gradeaware_share": ((0.0, 1.0), 0.3),    # goods: read the offered grade/warranty; warranted C/B get a higher cap
    "gradeaware_cap_c": ((0.65, 0.85), 0.03), # cap for a warranted C, share of list ((1-x)+0.16x beats the mean)
    "gradeaware_cap_b": ((0.62, 0.8), 0.03),
}
SHOP = {                                      # per shop (user 11-06: the strategist steers the rule-based buyer)
    "shop_bias": ((-0.1, 0.1), 0.03),         # added to the shop's value score (prefer > 0, avoid < 0)
    "bias_food": ((-0.1, 0.1), 0.03),         # 11-11: same, for food orders only (route food to the .40+ shops)
    "bias_goods": ((-0.1, 0.1), 0.03),        # 11-11: same, for goods orders only (beds/sofas to the .30+ shops)
    "shop_cap": ((-0.05, 0.05), 0.02),        # added to the cap at that shop, as a share of list
}
CAT = {"cap": ((0.6, 0.85), 0.04)}            # per category: replaces value_share for orders of that category
METRICS = ("deals_next_6h", "mean_saved_next_6h", "conversion_next_6h", "saved_speed")


def saved_speed(rows, hours, base):
    """How fast the scored saved rate climbs (user 11-06: compare settings on this, not on saved per deal or
    deals alone): deals per hour x (mean saved per deal - our current saved). Divided by the denominator it is
    the saved rise per hour; the product alone ranks settings the same way."""
    s = summary(rows)
    if not s["deals"] or s["mean_saved"] is None or not hours:
        return 0.0
    return round(s["deals"] / hours * (s["mean_saved"] - base), 2)


def log(text):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC {text}"
    print(line)
    with open(LOGF, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def trades(since, until=None):
    cat = shop.read(CATALOG, {})
    out = []
    for t in shop.read(BMEM, {}).get("trades", []):
        if t.get("t", 0) < since or (until and t["t"] > until):
            continue
        lp = (cat.get(t.get("item")) or {}).get("list_price")
        saved = 1 - t["price"] / lp if t.get("outcome") == "deal" and t.get("signed") is not False and t.get("price") and lp else None
        if saved is not None and t.get("broken"):          # the score: a break is -1, or 0 when refunded (agent.md; +1 before 11-10 was wrong)
            saved = 0.0 if (t.get("warranty") or 0) >= 6 else -1.0
        out.append(dict(t, list=lp, saved=saved))
    return out


def summary(rows):
    skipped = sum(str(r.get("outcome", "")).startswith("skipped") for r in rows)
    rows = [r for r in rows if not str(r.get("outcome", "")).startswith("skipped")]
    deals = [r for r in rows if r.get("outcome") == "deal" and r.get("signed") is not False]
    sv = [r["saved"] for r in deals if r["saved"] is not None]
    return {"visits": len(rows), "skipped_orders": skipped, "deals": len(deals), "conversion": round(len(deals) / len(rows), 2) if rows else None,
            "mean_saved": round(sum(sv) / len(sv), 3) if sv else None,
            "first_ask_over_list": round(sorted(r["asks"][0] / r["list"] for r in rows if r.get("asks") and r.get("list"))
                                         [len([1 for r in rows if r.get("asks") and r.get("list")]) // 2], 2)
            if any(r.get("asks") and r.get("list") for r in rows) else None}


def shop_prices(now):
    """What our own shop got per category in 24 h, as a share of list: the discount buyers actually win from a
    shop pricing like ours (seller_r3_log.jsonl deals)."""
    cat, by = shop.read(CATALOG, {}), {}
    for line in open(HERE / "seller_r3_log.jsonl", encoding="utf-8").readlines()[-30000:]:
        if '"deal"' not in line:
            continue
        e = json.loads(line)
        lp = (cat.get(e.get("sku")) or {}).get("list_price")
        if e.get("event") == "deal" and e.get("t", 0) >= now - 86400 and lp:
            by.setdefault(str(e.get("category") or "").split(">")[-1], []).append(e["price"] / lp)
    return {k: {"sales": len(v), "price_over_list_median": round(sorted(v)[len(v) // 2], 2)}
            for k, v in sorted(by.items(), key=lambda kv: -len(kv[1]))[:15]}


def rules_by_shop(r6):
    """How the rule-based buyer fared per shop (6 h): visits, deals, outcomes, and the asks against our cap, as
    shares of list: first ask, last ask, cap. A last ask just over the cap says a small shop_cap would close it."""
    by = {}
    for r in r6:
        if not r.get("shop") or str(r.get("outcome", "")).startswith("skipped"):
            continue
        by.setdefault(r["shop"], []).append(r)
    out = {}
    for name, rows in sorted(by.items(), key=lambda kv: -len(kv[1]))[:20]:
        lp = lambda r: r.get("list") or r.get("list_price") or 0
        fa = [r["asks"][0] / lp(r) for r in rows if r.get("asks") and lp(r)]
        la = [r["asks"][-1] / lp(r) for r in rows if r.get("asks") and lp(r)]
        cp = [r["cap"] / lp(r) for r in rows if r.get("cap") and lp(r)]
        med = lambda v: round(sorted(v)[len(v) // 2], 3) if v else None
        out[name] = {"visits": len(rows), "deals": sum(r.get("outcome") == "deal" and r.get("signed") is not False for r in rows),
                     "outcomes": dict(Counter(str(r.get("outcome"))[:16] for r in rows).most_common(4)),
                     "first_ask": med(fa), "last_ask": med(la), "cap": med(cp)}
    return out


def throughput(r6):
    """Orders, deals and skips per hour (6 h) and the median seconds per visit: the market sends an order every
    ~20 s, so time under that per order is free and max_shops can rise while visits stay short."""
    orders = {r.get("order") for r in r6}
    secs = sorted(r.get("secs") or 0 for r in r6 if not str(r.get("outcome", "")).startswith("skipped"))
    return {"orders_per_h": round(len(orders) / 6), "deals_per_h": round(sum(r.get("outcome") == "deal" and r.get("signed") is not False for r in r6) / 6),
            "skipped_per_h": round(sum(str(r.get("outcome", "")).startswith("skipped") for r in r6) / 6),
            "median_secs_per_visit": secs[len(secs) // 2] if secs else None}


def market_value(r6):
    """buyer_value.py's table: per kind the market mean and the 12 shops whose deals add most to the saved rate
    (all buyers, 6 h, breaks -1, refunded 0), with how many of our last-6 h deals went to each."""
    try:
        from buyer_value import table
        st = table()
    except Exception as e:
        return {"error": str(e)}
    ours = {}
    for r in r6:
        if r.get("outcome") == "deal" and r.get("signed") is not False:
            ours[r["shop"]] = ours.get(r["shop"], 0) + 1
    out = {}
    for kind in ("food", "goods"):
        rows = [(k.split("|")[0], v) for k, v in st.items() if k.endswith(f"|{kind}") and k.count("|") == 1]
        top = sorted((x for x in rows if x[1]["n"] >= 10 and x[0] != "*"), key=lambda kv: -kv[1]["saved"])[:12]
        out[kind] = {"market": st.get(f"*|{kind}"),
                     "best_shops": [dict(v, shop=n, our_deals_6h=ours.get(n, 0)) for n, v in top]}
    return out


def measure(pred, since, now, base=None):
    rows = [r for r in trades(since, now) if (not pred.get("category") or r.get("category") == pred["category"])
            and (not pred.get("shop") or r.get("shop") == pred["shop"])]
    if pred["metric"] == "saved_speed":
        return saved_speed(rows, (now - since) / 3600, base or 0)
    s = summary(rows)
    return {"deals_next_6h": s["deals"], "mean_saved_next_6h": s["mean_saved"] or 0,
            "conversion_next_6h": s["conversion"] or 0}[pred["metric"]]


def evidence(mem, b, auto, params):
    now = time.time()
    r6, r24 = trades(now - 6 * 3600), trades(now - 24 * 3600)
    cats, shops = {}, {}
    for r in r24:
        cats.setdefault(r.get("category") or "?", []).append(r)
        shops.setdefault(r.get("shop") or "?", []).append(r)
    top_c = sorted(cats.items(), key=lambda kv: -len(kv[1]))[:20]
    top_s = sorted(shops.items(), key=lambda kv: -len(kv[1]))[:15]
    rows = [x for x in b["buyers_all"] if x.get("saved") is not None]
    ladder = sorted({round(x["saved"], 3) for x in rows if x["saved"] > b["buyer"]["saved"]})[:6]
    return {"time_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime()), "round_ends": "2026-11-17T23:59 KST",

            "score": score_view(), "class_ladder": class_ladder(b),
            "buyer_board": b["buyer"], "saved_values_just_above_us": ladder,
            "class_rank": b["class_rank"], "class_top": b["class_top"],
            "now": {"last_6h": summary(r6), "last_24h": summary(r24)},
            "by_category_24h": {k: summary(v) | {"cap_now": ((auto.get("category") or {}).get(k) or {}).get("cap")}
                                for k, v in top_c},
            "by_shop_24h": {k: summary(v) for k, v in top_s},
            "knobs_now": {k: (auto.get("globals") or {}).get(k, params.get(k)) for k in GLOBAL},
            "from_shop_strategist": read_board("buyer"),
            "market_value_6h": market_value(r6),
            "rules_by_shop_6h": rules_by_shop(r6),
            "throughput": throughput(r6),
            "market_picture": buyer_picture(market_picture()),
            "ab_tests": ab_tests(), "buy_health": buy_health(now),
            "saved_mix_vs_peers_6h": saved_mix(r6),
            "peer_best_our_categories": peer_best_ours(),
            "from_operator": operator_note(OPERATOR_NOTE, OPERATOR_ANSWER)}


def peer_best_ours(n=3):
    """11-11 (user: 'why must I tell you the obvious'): every run, for our n most-ordered categories (24 h), who buys
    them cheapest and how (grade, warranty, shop), and the market's v by grade if warranted: the comparison that
    showed warranted B/C furniture at ~.36 against our .356 goods bar, which no evidence block carried before."""
    try:
        import market_tools
        from collections import Counter
        c = Counter()
        cut = time.time() - 24 * 3600
        with open(HERE / "market_intel.jsonl", encoding="utf-8") as f:
            for line in f:
                if '"order"' not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("type") == "order" and r.get("t", 0) >= cut:
                    c[(r.get("category") or "").split(">")[-1]] += 1
        tot = sum(c.values()) or 1
        out = {}
        for cat, k in c.most_common(n):
            pb = market_tools.peer_best(cat, 6, 4)
            out[cat] = {"our_order_share_24h": round(k / tot, 3), "market": pb.get("market"), "by_grade": pb.get("by_grade"),
                        "best_buyers": [{x: r[x] for x in ("buyer", "n", "v", "x_med", "grades", "warranty", "top_shops")}
                                        for r in pb.get("best_buyers") or []], "us": pb.get("us")}
        return out
    except Exception as e:
        return {"error": repr(e)[:200]}


def saved_mix(r6):
    """User 11-09: 'keep catching this and keep saved high; that is what the agent is for.' Saved per deal split
    into food and goods for us, the market and every buyer with 100+ settles in the 6 h market window (breaks -1,
    refunded 0), the market's per-deal saved by hour (is everyone getting cheaper?), and our orders by kind with
    how they ended (why is our food share what it is?). On 11-09 our .371 = food 49% at .413 + goods 51% at .329,
    while the buyers just above ran .38-.41: half of the gap was the mix, half the goods price."""
    try:
        from collections import defaultdict
        from buyer_value import _saved
        st = shop.read(HERE / ".market" / "shop_value.json", {})
        deals, broken = st.get("deals") or [], st.get("broken") or {}
        g = defaultdict(lambda: {"food": [], "goods": []})
        hours = defaultdict(list)
        kh = {"food": defaultdict(list), "goods": defaultdict(list)}
        t1 = max((d["ts"] for d in deals), default=0)
        for d in deals:
            v = _saved(d, broken)
            k = "goods" if d.get("goods") else "food"
            g[d.get("buyer")][k].append(v)
            g["*market*"][k].append(v)
            hours[int((t1 - d["ts"]) // 3600)].append(v)
            kh[k][int((t1 - d["ts"]) // 3600)].append(v)

        def row(x):
            f, go = x["food"], x["goods"]
            n = len(f) + len(go)
            m = lambda v: round(sum(v) / len(v), 3) if v else None
            return {"n": n, "food_share": round(len(f) / n, 2), "food": m(f), "goods": m(go),
                    "goods_breaks": sum(1 for v in go if v == -1), "all": m(f + go)}
        peers = {b: row(x) for b, x in g.items() if b and len(x["food"]) + len(x["goods"]) >= 100}
        ours = peers.get("yms979") or {}
        top = dict(sorted(peers.items(), key=lambda kv: -(kv[1]["all"] or 0))[:14])
        orders = defaultdict(lambda: defaultdict(int))
        for t in r6:
            orders[t.get("kind") or "?"][str(t.get("outcome"))[:20]] += 1
        return {"ours": ours, "market": peers.get("*market*"), "best_buyers": top,
                "market_per_deal_by_hours_ago": {h: round(sum(v) / len(v), 3) for h, v in sorted(hours.items())},
                "market_by_kind_by_hours_ago": {k: {h: round(sum(v) / len(v), 3) for h, v in sorted(x.items())}
                                                for k, x in kh.items()},
                "our_orders_by_kind_6h": {k: dict(v) for k, v in orders.items()},
                "read": "our all = food_share x food + (1 - food_share) x goods; compare both terms with the buyers "
                        "just above us and say which one is the gap now"}
    except Exception as e:
        return {"error": repr(e)}


def ab_tests():
    """The operator's A/B arms as the curator measures them: per arm orders, deals, saved per deal, deals per order
    and score gain per order (deals/order x (saved per deal - our running saved)). Decide the *_share knobs on it."""
    try:
        import memory_curator as mc
        b = mc.breakdown(sys.modules[__name__], time.time())
        return {k: [x for x in b.get(k, []) if x.get("key") not in ("other", "before_ab")]
                for k in ("push_ab", "cheapfood_ab_food", "cheapgoods_ab_goods", "skubest_ab", "wpen_ab_goods", "gradeaware_ab_goods", "aimtop_ab", "aim15_ab", "needwarr_ab_goods")}
    except Exception as e:
        return {"error": repr(e)}


def buy_health(now, hours=3):
    """Plumbing (11-08: the shop lost ~10 h of sales to refused settles nobody saw): our deal records against the
    market's settles for our buyer, and where the other visits ended."""
    from collections import Counter
    rows = [t for t in shop.read(BMEM, {}).get("trades", []) if t.get("t", 0) >= now - hours * 3600]
    out = Counter(str(t.get("outcome"))[:30] for t in rows)
    return {"window_h": hours, "deal_records": out.get("deal", 0), "outcomes": dict(out.most_common(10)),
            "note": "compare deal_records with the board's deal count growth; 'unpaid'/'refused' are plumbing"}


DECIDE = """You run the BUYER of yms979 in a course market (the shop has its own strategist). Read the scoring
rules first: {rules}

GOAL (user, 11-11): finish 1st overall; for the buyer, SAVED FIRST. saved is a cumulative mean scored by rank
among students, so every deal must beat the mean our saved will END at (evidence "score".m_star, the projected
final mean on the last 24 h pace), and we must gain per-deal value faster than the students just above us.
Fewer goods deals are accepted for that; do not take deals under m* to lift the deals percentile (one deals rank
is worth 0.0022, one saved rank 0.0111). Forum and posts are fine and not yours to move.

SCALE (11-12 patch 3.6): every saved number here (m*, v, expected, market_value) is on the new scale
min(1, (list - price)/(list - unit cost)); values before 11-12 ~10:30 UTC in old memory or notes were 1 - price/list
(about half as large). Do not compare across the two.

THE CODE BUYER (11-11): per order the code (no model call) scores the shops that hold an acceptable SKU by the
market's realized saved per deal there (evidence "market_value_6h"), calibrated with what WE realized at that shop
over 24 h (goods were overpredicted by .04-.08), plus conv_weight x our close rate, minus rival_penalty for a
classmate above us, plus your shop_bias and per-kind bias_food/bias_goods; it skips the order when the best
expectation is under max(fixed skip bar, m* + skip_delta_kind); it caps the price at min(category cap or
value_share, the shop's p50 (aimtop arm: p25, aim15: p15) + cap_margin) x list; when a shop states the unit's grade
the cap falls to that grade's ceiling x_bar_g = 1 - (m* + goods_delta)(1 - c)/(1 - p_g), c = the SKU's cost share
for that grade (patch 3.6, 11-12: about A .70, B .61, C .36 at m* .75); food is capped at 1 - (m* + skip_delta_food)(1 - c), and
the same ceiling is checked on the receipt; every goods receipt must carry a 6 h warranty or it is not paid; the
goods opener asks for B or A with a warranty, no C; food must last its hours + food_extra_hours; haggling opens at
the shop's p25 - open_offset, counters once or twice in the push arms, takes any ask <= quick_accept x list.
Code guards (guards.py, every 15 min) can freeze knobs, revert the last 2 h of moves after a deals collapse, and
wake you out of turn; evidence "score".guard shows their state. You run every hour at :00 plus guard wakes.

Shared memory digest (the curator's summary for you): {digest}

Knobs (only these; the steps allowed now: {steps}): global "value_share" (0.65-0.85), "quick_accept" (0.55-0.75),
"food_extra_hours" (0.25-12), "skip_below" (0.15-0.33), "skip_below_food" (0.05-0.33), "skip_below_goods" (0.15-0.4),
"skip_delta_food" (-0.15..0.10) and "skip_delta_goods" (-0.20..0.10) (the bar is max(fixed bar, m* + delta)),
"target_mean" (0.60-0.90, used only when buyer_score.json has no m*), "goods_delta" (0-0.15, margin over m* a goods
deal must clear after its grade's break rate), "conv_weight" (0-0.3), "rival_penalty" (0-0.1), "cap_margin"
(0-0.1), "near_cap" (0-0.04), "open_offset" (0-0.08), "max_shops" (1-6), "value_half_life_h" (0-6: how fast the
shop values follow the newest market deals); the arms (share of orders, 0-1): "push_share", "push3_share", "talk_share" (asks for the shop's lowest price before any number; compare talk=True/False
in arms_per_order_24h), "push5_share" (offers the shop's 5th-percentile price, for goods among its warranted A/B units, and climbs
.015 of list per round, up to 6 counters; compare push=push5-* with push=push3-* in arms_per_order_24h),
"cheapfood_share", "cheapgoods_share", "aimtop_share", "aim15_share" (within aimtop), "skubest_share",
"wpen_share"; "push3_below" (0-0.2), "push_below" (0-0.15), "cheapfood_open" (0.2-0.6), "wpen_cost" (0-0.12);
no effect since 11-11 (do not move): "needwarr_share", "gradeaware_share", "gradeaware_cap_c/b". Per shop (give
"shop": a name from the evidence): "bias_food"/"bias_goods" (-0.1..0.1, routing for orders of that kind),
"shop_bias" (-0.1..0.1), "shop_cap" (-0.05..0.05, added to the cap there as a share of list). Per category (give
"category": the order's item name as in the evidence): "cap" (0.6-0.85, replaces value_share there).

THREE RACES (user, 11-08): the total race (class total), the shop race and the buyer race have different rivals
and move for different reasons. Read your own race first and on its own numbers ("class_ladder": the students
just above and below us in saved, with the deals at a given per-deal value needed to pass each); a safe total does
not make your race safe. Alert the user when the gap to the next player falls under 0.01 or shrinks fast.
RULE CHANGES (user, 11-08): when the digest's "rules_change" holds a change, re-check every knob you own against
the new rule before anything else and move what it made wrong, now; say which rule changed and what you changed.
DIAGNOSE BEFORE YOU MOVE (user, 11-08: 'opus is here to find causes; otherwise how is it different from a rule?'):
when a number moves (saved per deal, deals/h, conversion, a rival's climb), name its cause with evidence before a
move and rule out the others: the orders (kind/category mix the market sends), prices (our x vs the shops' and
rivals'), terms (grade, warranty, shelf life), our rules (skip bars, caps, ceilings, the arms), plumbing
("buy_health": unpaid, refused, too short) and bad data. The A/B arms are yours: widen an arm that wins on score
per ORDER (evidence "score".arms_per_order_24h and "ab_tests"; a per-deal win that loses deals is not a win), cut one
that loses; one order carries several arms at once, so judge an arm only against its own control with n >= 100.
SAVED PER DEAL (user, 11-09): every run read "score" and "saved_mix_vs_peers_6h" first. Our saved per deal =
food share x food saved + goods share x goods saved. Say which term is our gap now (mix is assigned by the market;
the food price, the goods price, breaks, skips, lost food orders, shops chosen are yours), find its cause and move
the knob that closes it. When the market gets cheaper ("market_by_kind_by_hours_ago") we must get cheaper faster:
shorten value_half_life_h and lower caps on that kind. Breaks: a refunded break scores 0 (a deal at .40 lost),
an unrefunded one -1; "score".breaks shows our share against the market's; grade C breaks 29%.
THE OPERATOR (11-08): evidence "from_operator" may hold a question or analysis from the operator and the user;
answer it in "answer_to_operator" with numbers and act through your knobs if you agree.
THE CURATOR: a memory agent writes the digest above ("briefing_for_you", "rules_change", "rank_change",
"answers_to_your_requests", "notes_for_you"). Ask for what you need next in "requests" (up to 5):
{{"kind": "sku_prices", "sku"}}, {{"kind": "category", "category"}}, {{"kind": "competitor", "shop"}},
{{"kind": "unserved"}}, {{"kind": "rules", "term"}}, {{"kind": "buyer", "name"}}, {{"kind": "shop", "name"}},
{{"kind": "knobs", "sku"|"knob"|"category"}}, {{"kind": "trades", "outcome"|"shop"|"category"|"kind"}},
{{"kind": "question", "text"}}. Answers arrive within 1-2 h. Moves are judged by their prediction (metric
"saved_speed" = deals/h x (saved per deal - our saved), "mean_saved_next_6h", "deals_next_6h", "conversion");
a move whose knob you changed again later is not reverted by the scorer.
HOLDING IS A STRATEGY (user, 11-06): an empty "moves" list is a valid and often the best answer. Move a knob
only when the evidence shows the current setting is wrong or a clear gain is waiting; when the last moves'
predictions are not yet due, when the numbers are on track, or when the evidence is thin or noisy, hold and say
in the read why. Changing settings every run makes it impossible to tell what worked. At most {max_moves} moves,
each with a prediction checkable later from our records: {{"metric": one of {metrics}, "category": "<optional>",
"op": ">=" or "<=", "value": n, "hours": 1-6}}, and "uses": the lesson ids it relies on.

TOOLS (11-11): when the run allows them you have read-only tools (mcp__market__*): score, class_ladder,
our_trades(shop/kind/category/outcome/hours), shop_market(shop, kind), food_losses(hours), realized_by_shop(kind, hours),
buyer_profile(name), shop_profile(name), order_mix(hours), projection(rate, v) (our final saved rank at a
deals/h x v scenario), final_rank(rate, v) (projected final class TOTAL ranks), frontier(days, bucket) (top students' per-deal v and
food share over time; slow, once per run). Use them to CHECK a cause before a move (e.g. why food v is low at a shop,
whether a rejection was right: a deal helps only if v > m*), not to re-read what the evidence already shows; at most
6 calls. Your final message must still be the single JSON object below.

Your record: {record}
Your playbook: {playbook}
Evidence: {evidence}

WHEN THE USER IS SILENT (user 11-08): do not wait on the user for anything you can undo. Decide on the evidence,
prefer the option that is easy to reverse, alert the user with what you did and why, and keep it under watch.
Only these wait for the user: spending money for no sale, anything aimed at a classmate, and a change to the goals.
ALERTING THE USER (user 11-08): when you find something the user must know now, put it in "alert_user" (it goes
to their phone at once); keep it empty otherwise.
Answer with one JSON object only:
{{"read": "<6-10 sentences>",
  "moves": [{{"knob": "<a knob above>", "category": "<for cap>", "shop": "<for shop knobs>", "value": n, "why": "...",
             "uses": [...], "prediction": {{...}}}}],
  "lessons": [{{"op": "add"|"rewrite"|"retire", "id": "...", "text": "...", "why": "..."}}],
  "requests": [{{"kind": "...", ...}}],
  "notes_for_shop": ["<what the shop strategist should know or check>"],
  "answer_to_operator": "<only when evidence 'from_operator' holds a question: your answer, with numbers and what you will do>",
  "alert_user": ["<Korean, 1-3 short items, ONLY for what the user must know now: a threat to first place, money
                 leaking, a fault, a rule change, or a decision only the user can make; give the number and what
                 you do about it. Usually empty.>"]
}}
The shop strategist (it runs every 6 hours) left you its reads and notes in the evidence under
"from_shop_strategist"; use them."""


def score_view():
    """buyer_score.json (buyer_score.py, every 15 min) without its history, plus the guards' state."""
    sc = shop.read(HERE / ".market" / "buyer_score.json", {})
    sc = {k: v for k, v in sc.items() if k != "hist"}
    cal = sc.get("calibration") or {}
    shops_cal = sorted(((k, v) for k, v in cal.items() if "|" in k and v.get("n", 0) >= 8), key=lambda kv: kv[1]["gap"])
    sc["calibration"] = {k: v for k, v in cal.items() if "|" not in k} | dict(shops_cal[:8] + shops_cal[-4:])
    ours = sc.get("ours") or {}
    sc["ours"] = dict(sorted(ours.items(), key=lambda kv: -kv[1]["n"])[:20])
    g = shop.read(HERE / ".market" / "guard_state.json", {})
    now = time.time()
    sc["guard"] = {"fires_last_tick": g.get("fires"), "frozen": {k: round((u - now) / 60) for k, u in (g.get("frozen") or {}).items() if u > now},
                   "freeze_all_min": max(0, round((g.get("freeze_all_until", 0) - now) / 60)),
                   "freeze_shares_min": max(0, round((g.get("freeze_shares_until", 0) - now) / 60)),
                   "overrides": {k: v.get("value") for k, v in (shop.read(HERE / ".market" / "guard_overrides.json", {}) or {}).items() if v.get("until", 0) > now},
                   "spend_24h_usd": g.get("spend_24h"), "book_eta_h": g.get("book_eta_h")}
    return sc


def class_ladder(b):
    """Students only: the 8 just above and 3 below us in saved, with the deals at a per-deal value v needed to pass
    each (saved' = (S + k v)/(N + 100 + k)), for v = .36 and .42."""
    try:
        full = __import__("board_api").fetch(timeout=20)
        names = {(p.get("buyer") or {}).get("name") for p in full["players"] if p.get("student")}
        rows = sorted((x for x in full["buyers"] if x["name"] in names and x.get("saved") is not None), key=lambda x: -x["saved"])
        i = next(k for k, x in enumerate(rows) if x["name"] == "yms979")
        me = rows[i]
        S, N = me["saved"] * (me["deals"] + 100), me["deals"]
        out = []
        for k, x in enumerate(rows[max(0, i - 8): i + 4]):
            e = {"rank": max(0, i - 8) + k + 1, "name": x["name"], "saved": x["saved"], "deals": x["deals"]}
            if x["saved"] > me["saved"]:
                tgt = x["saved"] + 0.0005
                e["deals_to_pass_at_v"] = {str(v): (int((tgt * (N + 100) - S) / (v - tgt)) if v > tgt else None) for v in (0.36, 0.42)}
            if x["name"] == "yms979":
                e["us"] = True
            out.append(e)
        return {"students": len(rows), "our_saved_rank": i + 1, "rows": out}
    except Exception as e:
        return {"error": repr(e)}


def board_view():
    b = shop.board()
    full = __import__("board_api").fetch(timeout=30)
    me = next(x for x in full["buyers"] if x["name"] == "yms979")
    b["buyer"] = {k: me.get(k) for k in ("all", "saved", "forum", "deals", "orders", "posts")} | \
        {k: v for k, v in b["buyer"].items() if k.endswith("_rank")}
    rows = [x for x in full["buyers"] if x.get("all") is not None]
    b["buyers_all"] = [{"name": x["name"], "saved": x.get("saved")} for x in rows]
    order = sorted(rows, key=lambda x: -(x.get("all") or 0))
    b["buyer"]["rank_on_buyer_all"] = next((i + 1 for i, x in enumerate(order) if x["name"] == "yms979"), None)
    b["buyer_leaders"] = [{k: x.get(k) for k in ("name", "all", "saved", "forum", "deals", "posts")} for x in order[:6]]
    # user 11-06: learn from buyers ranked above us with a saved rate like ours (inextro 0.246 is 5th): which
    # components carry them, as percentiles, so the gap to close is visible per component
    def pct(k, v):
        vals = [x.get(k) or 0 for x in rows]
        return round((sum(1 for q in vals if q < v) + 0.5 * sum(1 for q in vals if q == v)) / len(vals), 2)
    b["peers_above"] = [{"rank": i + 1, "name": x["name"], "all": x["all"],
                         **{k: (x.get(k), pct(k, x.get(k) or 0)) for k in ("saved", "forum", "deals", "posts")}}
                        for i, x in enumerate(order[:20])]
    me = next(x for x in rows if x["name"] == "yms979")
    b["buyer"]["percentiles"] = {k: pct(k, me.get(k) or 0) for k in ("saved", "deals", "posts")} | \
        {"forum_rating_scored_as_is": me.get("forum")}          # patch 3.5: forum enters buyer ALL as the rating
    return b


def run(force=False):
    mem = Memory(STATE)
    for k_, v_ in list(mem.d.get("steps", {}).items()):          # 11-11: no step below half its base
        spec_ = GLOBAL.get(k_) or CAT.get(k_) or SHOP.get(k_)
        if spec_ and v_ < spec_[1] / 2:
            mem.d["steps"][k_] = spec_[1] / 2
    slot = my_turn("buyer")
    wake = shop.read(BUYER_WAKE, None)
    if wake and not force and time.time() - mem.d.get("t", 0) < 3600:
        wake = None                     # a guard may wake the buyer at most once an hour
    if not force and not wake and (slot is None or slot == mem.d.get("slot")):
        return
    if wake:
        BUYER_WAKE.unlink(missing_ok=True)
        log(f"woken early by a guard: {json.dumps(wake, ensure_ascii=False)[:300]}")
    guard = shop.read(HERE / ".market" / "guard_state.json", {})     # 11-11: guards.py
    if not force and not wake and guard.get("budget_block_until", 0) > time.time():
        log("slot skipped: G10 budget block (guard wakes still run)")
        return
    mem.d["slot"] = slot if slot is not None else mem.d.get("slot")
    mem.d["t"] = time.time()
    mem.d.setdefault("working", {})["woken_by"] = wake
    params = shop.read(shop.PARAMS, {})
    mode = params.get("strategist", {}).get("mode", "propose")
    auto = shop.read(AUTO, {"globals": {}, "category": {}})
    now = time.time()
    for e in mem.due(now):                                   # learn
        got = measure(e["prediction"], e["t"], now, e.get("saved_at"))
        p = e["prediction"]
        ok = got >= p["value"] if p["op"] == ">=" else got <= p["value"]
        mem.score(e, got, ok)
        log(f"scored {e['knob']} {e.get('sku') or ''} {p['metric']} {p['op']} {p['value']}: got {got} -> {'HIT' if ok else 'MISS'}")
        if not ok:
            mem.d["unreflected"] = mem.d.get("unreflected", 0) + 1
            if e.get("applied"):
                # 11-08: a revert restores the value from before the move; it used to drop the key, so the knob fell
                # back to params.json base (cheapgoods_share went 0 -> 0.9 at 14:00 after a 0.1 -> 0 move missed)
                b4 = e.get("before")
                if e["knob"] in CAT:
                    d_ = (auto.setdefault("category", {})).setdefault(e["sku"], {})
                elif e["knob"] in SHOP:
                    d_ = (auto.setdefault("shop", {})).setdefault(e["sku"], {})
                else:
                    d_ = auto.setdefault("globals", {})
                cur_v = d_.get(e["knob"])
                if e.get("value") is not None and (cur_v is None or abs(float(cur_v) - float(e["value"])) > 1e-9):
                    # 11-11: a later run moved this knob again (needwarr 0.6 -> 0.3 at 02:01 was overwritten with 0.7
                    # at 02:30 by the 23:01 move's failed prediction); the newer move's own prediction will judge it
                    log(f"not reverted {e['knob']} {e.get('sku') or ''}: moved since to {cur_v} (this move set {e['value']})")
                elif b4 is None:
                    d_.pop(e["knob"], None)
                    log(f"reverted {e['knob']} {e.get('sku') or ''} to base: its prediction failed")
                else:
                    d_[e["knob"]] = b4
                    log(f"reverted {e['knob']} {e.get('sku') or ''} to {b4}: its prediction failed")
    rec = mem.record()
    for knob, k in rec["by_kind"].items():
        if len(k["last"]) >= 8:
            base = (GLOBAL.get(knob) or CAT.get(knob) or SHOP.get(knob))[1]
            old = mem.d["steps"].get(knob, base)
            rate = sum(k["last"]) / len(k["last"])
            new = max(base / 2, old / 2) if rate < 0.5 else min(base, old * 1.5) if rate > 0.75 else old   # 11-11: floor base/2 (cap sat at 0.005)
            if new != old:
                mem.d["steps"][knob] = new
                log(f"method: {knob} hit {rate:.0%}; step limit {old:g} -> {new:g}")
    mem.maybe_rollback(log)
    mem.fold()
    if mem.d.get("unreflected", 0) >= shop.REFLECT_MISSES:
        shop.LOGF, keep = LOGF, shop.LOGF                     # the critic logs into the buyer's log
        shop.reflect(mem)
        shop.LOGF = keep
    go, shop.EFFORT_NOW, why = run_gate("buyer", mem.d, woken=bool(wake) or force)
    if not go:
        log(f"model skipped (cost gate): {why}")
        if mode == "apply":                  # keep the learn step's reverts
            auto["t"] = mem.d["t"]
            AUTO.write_text(json.dumps(auto, ensure_ascii=False, indent=1), encoding="utf-8")
        mem.save()
        return
    log(f"model run (effort {shop.EFFORT_NOW}): {why}")
    b = board_view()
    bp = params.get("buyer", {}).get("base", {})
    ev = evidence(mem, b, auto, bp)
    steps = {k: mem.d["steps"].get(k, v[1]) for k, v in (GLOBAL | CAT | SHOP).items()}
    lessons = [{k: x[k] for k in ("id", "text", "for", "against")} for x in mem.lessons()]
    rules = (HERE / "RULES_THAT_SCORE.md").read_text(encoding="utf-8")       # 11-11: replaces MARKET_GUIDE + BUYER_STRATEGY
    prompt = DECIDE.format(rules=rules, digest=read_digest("buyer"),
                           steps=json.dumps(steps), max_moves=MAX_MOVES,
                           metrics=", ".join(METRICS), record=json.dumps(mem.record(), ensure_ascii=False),
                           playbook=json.dumps(lessons, ensure_ascii=False) if lessons else "(empty: write one)",
                           evidence=json.dumps(ev, ensure_ascii=False))
    raw = shop.ask_model(prompt, "buyer strategist")
    ans = last_json_object(raw) or {}
    (HERE / "buyer_strategist_last.json").write_text(json.dumps({"t": mem.d["t"], "answer": ans, "raw": raw[-4000:]},
                                                                ensure_ascii=False, indent=1), encoding="utf-8")
    if not ans:
        log(f"no usable answer ({len(raw)} chars)")
        mem.save()
        return
    log(f"[{mode}] read: {str(ans.get('read'))[:1200]}")
    sent = model_env.alert_user("buyer", ans.get("alert_user"))
    if sent:
        log(f"alerted the user: {sent}")
    if ans.get("answer_to_operator") and shop.read(OPERATOR_NOTE, {}).get("note"):
        OPERATOR_ANSWER.write_text(json.dumps({"t": time.time(), "question": shop.read(OPERATOR_NOTE, {}).get("note"),
                                               "answer": ans["answer_to_operator"]}, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
        log(f"answered the operator: {str(ans['answer_to_operator'])[:600]}")
    requests = valid_requests(ans.get("requests") or ans.get("investigate"))
    post_board("buyer", ans.get("read"), {"moves": [(m.get("knob"), m.get("category"), m.get("value"))
                                                   for m in (ans.get("moves") or [])][:8],
                                          "categories_24h": {k: v.get("mean_saved") for k, v in ev["by_category_24h"].items()}},
               ans.get("notes_for_shop"), requests)
    for q in requests:
        log(f"asked the curator: {json.dumps(q, ensure_ascii=False)[:160]}")
    mem.edit_lessons(ans.get("lessons"), log)
    cats = set(ev["by_category_24h"])
    shops_known = set(ev["by_shop_24h"]) | set(ev["rules_by_shop_6h"]) | {
        x["shop"] for k in ("food", "goods") for x in (ev["market_value_6h"].get(k) or {}).get("best_shops", [])}
    for mv in (ans.get("moves") or [])[:MAX_MOVES]:
        knob, pred = mv.get("knob"), mv.get("prediction") or {}
        try:
            value = float(mv.get("value"))
        except (TypeError, ValueError):
            continue
        if pred.get("metric") not in METRICS or pred.get("op") not in (">=", "<=") or "value" not in pred:
            log(f"dropped {knob}: no checkable prediction")
            continue
        if knob in CAT:
            key = mv.get("category")
            if key not in cats:
                log(f"dropped {knob} {key}: not a category in the evidence")
                continue
            (lo, hi), _ = CAT[knob]
            cur = ((auto.get("category") or {}).get(key) or {}).get(knob, bp.get("value_share", 0.78))
        elif knob in SHOP:
            key = mv.get("shop")
            if key not in shops_known:
                log(f"dropped {knob} {key}: not a shop in the evidence")
                continue
            (lo, hi), _ = SHOP[knob]
            cur = ((auto.get("shop") or {}).get(key) or {}).get(knob, 0)
        elif knob in GLOBAL:
            key = None
            (lo, hi), _ = GLOBAL[knob]
            cur = (auto.get("globals") or {}).get(knob, bp.get(knob, value))
        else:
            log(f"dropped unknown knob {knob!r}")
            continue
        new = round(min(hi, max(lo, cur - steps[knob], min(cur + steps[knob], value))), 3)
        now_ = time.time()                                             # 11-11: guards.py freezes
        if (guard.get("frozen") or {}).get(knob, 0) > now_:
            log(f"dropped {knob}: frozen by G6 until {time.strftime('%H:%M', time.gmtime(guard['frozen'][knob]))} UTC")
            continue
        if guard.get("freeze_all_until", 0) > now_ or (knob.endswith("_share") and guard.get("freeze_shares_until", 0) > now_):
            log(f"dropped {knob}: moves frozen by a guard (G3 collapse / G1 reconcile)")
            continue
        log(f"[{mode}] {knob} {key or ''} {cur:g} -> {new:g} (asked {value:g}) | {str(mv.get('why'))[:200]} "
            f"| predicts {pred['metric']} {pred.get('category', '')} {pred['op']} {pred['value']} | uses {mv.get('uses')}")
        applied = mode == "apply"
        if applied:
            if knob in CAT:
                auto.setdefault("category", {}).setdefault(key, {})[knob] = new
            elif knob in SHOP:
                auto.setdefault("shop", {}).setdefault(key, {})[knob] = new
            else:
                auto.setdefault("globals", {})[knob] = new
        try:
            hours = min(6.0, max(1.0, float(pred.get("hours") or 2)))
        except (TypeError, ValueError):
            hours = 6.0
        mem.add_episode({"t": mem.d["t"], "due": mem.d["t"] + hours * 3600, "saved_at": b["buyer"].get("saved"), "knob": knob, "sku": key, "value": new, "before": cur,
                         "why": str(mv.get("why")), "uses": [str(x) for x in (mv.get("uses") or [])][:5],
                         "prediction": pred, "applied": applied})
    if mode == "apply":
        auto["t"] = mem.d["t"]
        AUTO.write_text(json.dumps(auto, ensure_ascii=False, indent=1), encoding="utf-8")
    mem.save()


if __name__ == "__main__":
    import fcntl
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    lock = open(HERE / ".market" / "buyer_strategist.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit(0)
    run(ap.parse_args().force)
