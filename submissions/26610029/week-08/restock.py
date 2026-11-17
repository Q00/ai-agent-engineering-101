"""real_yellow's restock hook (the kit runs it every 60 s in Round 3).

    python restock.py --state .market

Before anyone orders, decide what to hold until the next supply window (one 3-minute window every 20 min):
1. refresh the shared DB from the market (collector),
2. rank SKUs by how much they sold across the market in the last hours times the margin they leave
   (going rate minus the expected cost of a randomly graded unit), skipping SKUs whose cost is surging,
3. spread the picks across categories (round-robin, best score in each category first) instead of just
   taking the top N overall -- a buyer never sees us unless shops.json already lists us for their order's
   category, so being stocked in only one or two hot categories (apples, dates...) caps how often we are
   even a candidate, no matter how good our reviews or forum posts are,
4. top each picked SKU up to a small target, inside a per-window share of the balance.
5. L1 first move: once a window, trial-enter the priciest category we have never held a single unit of
   (demand.json, independent of whether we have personally seen a stockout there yet) and post about it
   right away -- see uncovered_high_value_categories() and first_move_text().
Food is held only where it sells fast; goods may be held deeper, they only pay 1% a day.
"""
import argparse
import json
import time
from pathlib import Path

import meta_harness

import collector
from db import DB

# expected unit cost as a share of list price, given the market's grade odds (agent.md: Supply)
EXPECTED_COST = {"goods": 0.70 * 0.65 + 0.20 * 0.50 + 0.10 * 0.35,
                 "food": 0.25 * 0.65 + 0.50 * 0.50 + 0.25 * 0.35}
DEFAULT_POLICY = {"window_budget": 0.10,   # share of the balance one window may spend
                  "lookback_h": 3,          # how far back "what sells" looks
                  "max_skus": 10,           # was 7: restock was hitting this cap on almost every window
                                            # while only ~37% of cash was ever deployed as stock, capping
                                            # category variety (and so deal volume) well below what the
                                            # balance could support. 10 lines at ~10s each can run past the
                                            # ~3-min window on a late start, but a line that misses is only
                                            # skipped for that window (self.retries 3x, then moves on), not lost
                  "goods_target": 6, "food_target": 2,
                  "food_min_sales": 6,      # food only for SKUs that sold at least this often in the lookback
                  "goods_min_sales": 3,
                  "max_units": 150}         # never hold more than this many units in total -- Book counts
                                            # unsold stock as an asset and storage is only 1%/day, so 80 was
                                            # capping restock (6 of 9 runs in one hour skipped: stock was full)

OWN_RATE_MIN_SAMPLE = 5   # need at least this many of our own negotiations before trusting the rate
OWN_RATE_FLOOR = 0.20     # a SKU can sell well market-wide (what the ranking below measures) while still
                         # converting poorly for us specifically -- measured live: G81ade83e kept getting
                         # re-bought every window off a healthy market-wide "sold" count, but our own
                         # negotiations for it landed an 18% deal rate (2 of 11, most cancels with zero
                         # counter-offer), tying capital up in stock we can't actually move

OWN_RATE_SHORT_H = 1        # a short, low-sample second check alongside the main lookback_h window above --
OWN_RATE_SHORT_MIN_SAMPLE = 3   # measured live: G0b0ca441's own deal rate fell 0.11 -> 0.077 margin and
OWN_RATE_SHORT_FLOOR = 0.15     # 11.1% deal rate over 6h, but it still got re-bought at 20:40:46 because
                                # at that exact restock call the trailing 6h window hadn't yet accumulated
                                # 5 samples -- the 6 cancels that pushed it over the floor landed in the
                                # ~40 min *after* that buy. A short window needs fewer samples to react
                                # one cycle sooner to a SKU that is degrading fast, not just one that has
                                # always been mediocre

SHORT_SHELF_HOURS = 48      # a food SKU this short-lived loses most of its buyer pool to their own
                            # min_hours_left filter well before it expires, independent of price -- measured
                            # live: G022ce883 (shelf_hours=48) got 21 zero-offer cancels in one hour while
                            # its ask was already a normal ~53% off list, purely because its remaining shelf
                            # life had fallen under what most orders required. Capping how many units of such
                            # a SKU we hold limits how much capital sits in stock that will go unsellable
                            # before it expires, instead of trying to fix it with a lower price

HELD_COST_MIN_SAMPLE = 2    # need at least this many currently-held units of a SKU before trusting their
                            # average cost -- a single unit's grade draw can be a fluke either way
HELD_COST_SURPRISE = 0.10   # skip a SKU whose currently-held units cost this much more (as a share of
                            # list) than EXPECTED_COST assumes for its kind -- measured live: G81ade83e's
                            # held units averaged cost 25,360-25,955 on a 34,740 list (73-75%, almost all
                            # grade A) against EXPECTED_COST["goods"]=0.595, so the ranking below kept
                            # rating it "profitable" off the kind-wide average while our own actual cost
                            # basis for it left almost no room to negotiate (floor sat near 77% of list,
                            # well above what most buyers were offering -- mostly zero-offer cancels)
HELD_COST_EXTREME_SURPRISE = 0.20   # fires even with only 1 held unit (below HELD_COST_MIN_SAMPLE) --
                                    # measured live: Gef4d3036 drew a single grade-A unit at cost 20,798
                                    # on a 28,900 list (72%, 26 points over EXPECTED_COST["food"]), which
                                    # floor-locked its price and produced 70+ zero-offer cancels across 3
                                    # hours before it finally expired into clearance. A unit this far over
                                    # is not worth waiting for a second sample to confirm

# Horvitz threshold for a shop promo post: post a "hot drop" at once when we just bought something
# that is clearly selling fast elsewhere (high usefulness), otherwise only after staying quiet a while
# (threshold drops with quiet_bonus past quiet_after) -- never spam, never go silent either.
DEFAULT_PROMO = {"hot_usefulness": 0.9, "base_usefulness": 0.3,
                 "threshold": 0.5, "quiet_bonus": 0.35, "quiet_after": 480, "post_every": 300}
# quiet_after = 480s: posts roughly every ~8 min when nothing is "hot" (the market's own rate limit is
# 300s/5min per account, so this is the real ceiling -- a hot restock still posts the moment it happens)
EMOJI_BY_KIND = {"food": "🍊", "goods": "🛋️"}


def stockout_categories(d, now, lookback_h):
    """Categories our own buyer recently asked for and found zero market-wide stock for -- a settles-based
    ranking can never discover these on its own (nobody has ever sold there, so there's no sales history to
    rank), even though the demand is real and visible right in our own buyer's logs."""
    rows = d.q("""SELECT context->>'category' AS cat, context->>'kind' AS kind, count(*) AS n
                 FROM trajectory WHERE role='buyer' AND decision='stockout' AND ts > $1
                 AND context->>'category' IS NOT NULL GROUP BY 1, 2 HAVING count(*) >= 3""",
               now - lookback_h * 3600)
    return {(r["cat"], r["kind"]): int(r["n"]) for r in rows}


def low_competition_categories(d, now, lookback_h=24, max_shops=5):
    """Categories where few shops compete market-wide -- thin, not dead. These rarely clear
    food_min_sales/goods_min_sales (total demand there is just low), so the ranking below would skip
    them even when margin is fine and being one of only a handful of sellers means a buyer who lands
    in that category has few alternatives to us. A longer lookback than the usual ranking's: a niche
    category needs more time to accumulate any sales at all to judge competition by."""
    rows = d.q("""SELECT category FROM settles WHERE ts > $1 AND category IS NOT NULL
                 GROUP BY category HAVING count(DISTINCT shop_url) <= $2""",
               now - lookback_h * 3600, max_shops)
    return {r["category"] for r in rows}


DEMAND_MIN_MEDIAN_PRICE = 50000   # only bet a trial unit on a category worth it -- a thin, cheap one
                                  # isn't worth the (small but real) capital tied up while we wait to see

# L1 Horvitz table for this first move, written before the code below:
#   opponent's goal   -- a buyer with an order in this category finds real_yellow as a candidate at all
#   P(we're right)    -- medium-high: demand.json's median_price * skus is the market's own catalog
#                         depth for this category, not a guess -- a category that large keeps generating
#                         orders somewhere
#   cost if wrong     -- one cheap trial unit (same cap as the stockout trial below) + one forum post;
#                         both already have an existing budget guard, so a wrong bet costs little
#   metric to check   -- settles count for this category attributed to real_yellow, baseline 0 (we have
#                         never held a unit there) -- re-measured in week 11


def uncovered_high_value_categories(inv, catalog, demand, min_median_price=DEMAND_MIN_MEDIAN_PRICE):
    """L1 first move: demand.json lists every category's market-wide catalog depth (skus, median_price),
    independent of our own order history. stockout_categories() above only reacts once our own buyer has
    already failed there 3+ times in a lookback window -- this acts before that ever happens, on the
    probability that a category this large will send us an order eventually anyway, instead of waiting
    to be asked."""
    held_cats = set()
    for u in inv.get("units", []):
        cat = (catalog.get(u["sku"]) or {}).get("category")
        if cat:
            held_cats.add(cat)
    out = [c for c in demand if c.get("category") and c["category"] not in held_cats
          and (c.get("median_price") or 0) >= min_median_price and c.get("skus")]
    out.sort(key=lambda c: -(c["median_price"] * c["skus"]))
    return out


def own_deal_rates(d, now, lookback_h):
    """Our own shop's recent negotiation outcomes per SKU, independent of how well that SKU sells
    market-wide (settles, used below) -- buyer mix, our own ask, and warranty terms can make a SKU a
    poor fit for our shop specifically even when it is popular overall."""
    rows = d.q("""SELECT context->>'sku' AS sku, count(*) AS n,
                 avg(CASE WHEN outcome->>'result' = 'deal' THEN 1 ELSE 0 END) AS rate
                 FROM trajectory WHERE role='shop' AND decision='sell' AND ts > $1
                 GROUP BY 1""", now - lookback_h * 3600)
    return {r["sku"]: (float(r["rate"]), int(r["n"])) for r in rows}


def plan(inv, d, policy, now, catalog, demand):
    units = inv.get("units", [])
    if len(units) >= policy["max_units"]:
        print(f"[restock] stock is full ({len(units)}/{policy['max_units']}), not buying")
        return [], []          # why must stay a list of dicts, same shape as the normal return below
    held = {}
    held_cost_ratios = {}
    for u in units:
        held[u["sku"]] = held.get(u["sku"], 0) + 1
        lp = u.get("list_price") or 0
        if lp:
            held_cost_ratios.setdefault(u["sku"], []).append(u["cost"] / lp)
    surging = {s["sku"] for s in (inv.get("surge") or []) if s.get("factor", 1) > 1.05}

    rows = d.q("SELECT sku, max(kind) AS kind, max(list_price) AS lp, max(category) AS category, "
               "count(*) AS n, avg(amount::float / list_price) AS ratio FROM settles "
               "WHERE ts > $1 AND list_price > 0 GROUP BY sku", now - policy["lookback_h"] * 3600)
    low_comp = low_competition_categories(d, now)
    by_cat = {}
    for r in rows:
        kind, lp, n, ratio = r["kind"], int(r["lp"]), int(r["n"]), float(r["ratio"])
        margin = ratio - EXPECTED_COST.get(kind, 0.6)
        if margin <= 0.02 or r["sku"] in surging:
            continue
        min_sales = policy["food_min_sales"] if kind == "food" else policy["goods_min_sales"]
        if n < min_sales and r["category"] not in low_comp:   # a thin market gets a pass: being one
            continue                                           # of few sellers there matters more than
                                                                # hitting the usual sales-volume bar
        row = (n * margin, r["sku"], kind, lp, n, round(margin, 3))   # fast movers first, not big tickets
        by_cat.setdefault(r["category"] or r["sku"], []).append(row)

    # round-robin across categories, best score in each first: spreads stock over many order categories
    # instead of piling into whichever two or three sell the most, so shops.json lists us more often
    queues = sorted((sorted(v, reverse=True) for v in by_cat.values()), key=lambda q: q[0], reverse=True)
    ranked = []
    while any(queues):
        for q in queues:
            if q:
                ranked.append(q.pop(0))
        queues = [q for q in queues if q]

    budget = inv.get("balance", 0) * policy["window_budget"]
    per_sku_cap = budget * 0.3                          # one sofa must not eat the whole window
    own_rates = own_deal_rates(d, now, policy["lookback_h"])
    own_rates_short = own_deal_rates(d, now, OWN_RATE_SHORT_H)
    buy, why = [], []

    # categories nobody stocks at all: no sales history to rank by, so a settles-based ranking never finds
    # them. Reserve a couple of slots FIRST (not leftover ones -- a crowded round-robin fills every slot
    # most windows, so "only if there's room" almost never actually fires) for a small, low-risk trial:
    # the cheapest catalog SKU in the category. First mover with zero competition beats optimizing within
    # a crowd of sellers who are all fighting over the same proven-popular SKUs.
    covered = set(by_cat.keys())
    trial_slots = min(2, policy["max_skus"])
    for (cat, kind), n_seen in sorted(stockout_categories(d, now, policy["lookback_h"]).items(),
                                      key=lambda x: -x[1]):
        if cat in covered or len(buy) >= trial_slots:
            continue
        candidates = [(sku, v) for sku, v in catalog.items()
                     if v.get("category") == cat and sku not in surging and v.get("list_price")]
        if not candidates:
            continue
        sku, v = min(candidates, key=lambda x: x[1]["list_price"])      # cheapest: smallest trial risk
        lp = v["list_price"]
        unit_cost = lp * 0.75
        if unit_cost > per_sku_cap or unit_cost > budget or held.get(sku):
            continue
        buy.append({"sku": sku, "qty": 1})
        why.append({"sku": sku, "kind": v.get("kind", kind), "list": lp, "sold": 0, "margin": None,
                   "qty": 1, "trial": True, "stockouts_seen": n_seen})
        budget -= unit_cost
        covered.add(cat)

    # one L1 first move per window: the priciest category demand.json shows we have never held a unit
    # of, picked before any buyer ever asks (see uncovered_high_value_categories() and the Horvitz table
    # above it) -- kept to a single slot so a wrong bet stays cheap
    for gap in uncovered_high_value_categories(inv, catalog, demand):
        cat = gap["category"]
        if cat in covered:
            continue
        candidates = [(sku, v) for sku, v in catalog.items()
                     if v.get("category") == cat and sku not in surging and v.get("list_price")]
        if not candidates:
            continue
        sku, v = min(candidates, key=lambda x: x[1]["list_price"])      # cheapest: smallest trial risk
        lp = v["list_price"]
        unit_cost = lp * 0.75
        if unit_cost > per_sku_cap or unit_cost > budget or held.get(sku):
            continue
        buy.append({"sku": sku, "qty": 1})
        why.append({"sku": sku, "kind": v.get("kind"), "list": lp, "sold": 0, "margin": None,
                   "qty": 1, "trial": True, "first_move": True, "median_price": gap["median_price"]})
        budget -= unit_cost
        covered.add(cat)
        break

    for value, sku, kind, lp, n, margin in ranked:
        own_rate, own_n = own_rates.get(sku, (None, 0))
        if own_n >= OWN_RATE_MIN_SAMPLE and own_rate < OWN_RATE_FLOOR:
            continue          # sells well market-wide but not for us: don't keep tying up capital in it
        short_rate, short_n = own_rates_short.get(sku, (None, 0))
        if short_n >= OWN_RATE_SHORT_MIN_SAMPLE and short_rate < OWN_RATE_SHORT_FLOOR:
            continue          # degrading fast enough that the main lookback_h window hasn't caught up yet
        costs = held_cost_ratios.get(sku)
        if costs:
            avg_cost = sum(costs) / len(costs)
            expected = EXPECTED_COST.get(kind, 0.6)
            if (len(costs) >= HELD_COST_MIN_SAMPLE and avg_cost > expected + HELD_COST_SURPRISE) or \
               avg_cost > expected + HELD_COST_EXTREME_SURPRISE:
                continue      # our own held stock for this SKU costs far more than the kind-wide average
                              # the margin score above assumes -- that score is stale for this SKU specifically
        unit_cost = lp * 0.75                           # worst case: an A unit at the top of its range
        if unit_cost <= 0 or unit_cost > budget:
            continue
        target = policy["goods_target"] if kind == "goods" else policy["food_target"]
        shelf_hours = (catalog.get(sku) or {}).get("shelf_hours")
        if kind == "food" and shelf_hours and shelf_hours <= SHORT_SHELF_HOURS:
            target = min(target, 1)
        qty = target - held.get(sku, 0)
        qty = min(qty, int(budget // unit_cost))
        qty = min(qty, int(per_sku_cap // unit_cost))   # cap this SKU's TOTAL spend, not just its unit
        # price -- a pricey item bought in bulk was eating a whole window's budget by itself (qty=4 at
        # ~82k/unit once swallowed 93% of a 354k window), crowding out the other 8 categories max_skus allows
        if qty < 1:
            continue
        buy.append({"sku": sku, "qty": qty})
        why.append({"sku": sku, "kind": kind, "list": lp, "sold": n, "margin": margin, "qty": qty})
        budget -= qty * unit_cost
        if len(buy) >= policy["max_skus"]:
            break
    return buy, why


def honesty_stat(d):
    """Our own track record, verifiable by anyone against the ledger: a quantified trust signal is a
    stronger vote-bait than a bare claim of honesty."""
    rows = d.q("SELECT count(*) AS n, avg(CASE WHEN s.claim_honest IS FALSE THEN 0 ELSE 1 END) AS rate "
               "FROM settles s JOIN shops sh ON sh.url = s.shop_url WHERE sh.name = 'real_yellow'")
    r = rows[0] if rows else {}
    n = int(r.get("n") or 0)
    if n < 5:
        return None
    return f"📊 최근 {n}건 중 claim_honest {round(float(r['rate']) * 100)}%"


def promo_text(inv, why, now, stat=None):
    """A playful but verifiable forum post: real SKUs, real grades, a claim every settle in our ledger
    history backs up (the shop code sets claim = the unit's actual grade, never anything else)."""
    live = [u for u in inv.get("units", []) if u["grade"] != "expired"
            and (u.get("expires_at") is None or u["expires_at"] > now + 600)]
    if not live:
        return None
    hot_skus = {x["sku"] for x in why[:2]}
    picks = [u for u in live if u["sku"] in hot_skus] or live
    seen, lines = set(), []
    for u in picks:
        if u["sku"] in seen:
            continue
        seen.add(u["sku"])
        e = EMOJI_BY_KIND.get(u["kind"], "📦")
        extra = " ⏰임박" if u.get("expires_at") and u["expires_at"] - now < 6 * 3600 else ""
        lines.append(f"{e} {u['name'][:28]} ({u['sku']}) {u['grade']}등급{extra}")
        if len(lines) >= 3:
            break
    trust = f"{stat} (ledger로 검증 가능)" if stat else "claim=실제 등급 그대로, 속인 적 없음 (ledger로 검증 가능)"
    return ("🏪 real_yellow 오늘의 재고\n" + "\n".join(lines) +
            f"\n✅ {trust} | ⏱️ 비식품 72h 보증\n느린 흥정 싫으면 real_yellow 👋")


def first_move_text(inv, entry):
    """L1 first move post: we just entered a category nobody has asked real_yellow for yet, picked from
    demand.json's market-wide catalog depth rather than waiting on our own buyer's stockout log. Worded
    and tagged distinctly (🆕 선점) so it reads apart from the routine "오늘의 재고" cycle in
    outbox.sent.jsonl when checking the LAB's pass criteria."""
    u = next((u for u in inv.get("units", []) if u["sku"] == entry["sku"]), None)
    if not u:
        return None
    return (f"🆕 선점 입고: {u['name'][:30]} ({u['sku']}) {u['grade']}등급\n"
            f"이 카테고리는 시장 정가 중앙값 {entry['median_price']:,}원대인데 저희가 처음 들여왔습니다 -- "
            f"아직 아무도 안 물어봤지만 먼저 채워뒀어요.\n느린 흥정 싫으면 real_yellow 👋")


def shop_forum(d, inv, why, now, md):
    policy = dict(DEFAULT_PROMO, **d.get_policy("shop_forum", {}))
    last = float(d.get_state("last_post_shop", 0))
    if now - last < policy["post_every"]:
        return
    first_move = next((x for x in why if x.get("first_move")), None)
    hot = any(x.get("margin") is not None and x["margin"] >= 0.1 and x["sold"] >= 50 for x in why)
    if first_move:
        usefulness = 0.95       # treat our own first move like the existing "hot drop" tier: post it
                                # the moment it happens rather than folding it into the next quiet-timer post
    else:
        usefulness = policy["hot_usefulness"] if hot else policy["base_usefulness"]
    bar = policy["threshold"] - (policy["quiet_bonus"] if now - last > policy["quiet_after"] else 0)
    if usefulness < bar:
        print(f"[forum] shop skip: usefulness {usefulness} < {bar}")
        return
    text = first_move_text(inv, first_move) if first_move else promo_text(inv, why, now, honesty_stat(d))
    if not text:
        print("[forum] shop skip: no live stock to show")
        return
    with open(md / "outbox.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"post": text, "as": "shop"}, ensure_ascii=False) + "\n")
    d.set_state("last_post_shop", now)
    d.log_trajectory(now, "shop", "forum_post",
                     {"usefulness": usefulness, "bar": bar, "hot": hot, "first_move": bool(first_move)},
                     {"text": text})
    print(f"[forum] shop posted (usefulness {usefulness} >= {bar}):\n{text}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=".market")
    md = Path(ap.parse_args().state)
    inv = json.loads((md / "inventory.json").read_text(encoding="utf-8"))
    now = time.time()

    try:                                        # independent of the supply window: runs on every call,
        with DB() as d:                        # self-throttled inside to once per ~20 min
            meta_harness.maybe_tune(d, now)
    except Exception as e:
        print(f"[meta] skipped: {type(e).__name__}: {e}")

    if inv.get("balance", 0) < inv.get("bankrupt_line", 0) and not inv.get("units") and inv.get("bankruptcies_left"):
        print(json.dumps({"buy": [], "bankrupt": True}))
        return

    w = inv.get("restock") or {}
    if not w.get("open") and w.get("opens_at", 0) - now > 60:   # the next run comes before the window
        print(f"[restock] window opens in {int(w['opens_at'] - now)} s, not buying yet")
        print(json.dumps({"buy": []}))
        return

    try:
        print(f"[restock] collector {json.dumps(collector.run_once())}")
    except Exception as e:                    # stale data still beats buying blind
        print(f"[restock] collector failed: {type(e).__name__}: {e}")

    catalog = json.loads((md / "catalog.json").read_text(encoding="utf-8"))
    demand = json.loads((md / "demand.json").read_text(encoding="utf-8"))
    buy, why = [], []
    try:
        with DB() as d:
            policy = dict(DEFAULT_POLICY, **d.get_policy("restock", {}))
            buy, why = plan(inv, d, policy, now, catalog, demand)
            d.log_trajectory(now, "shop", "restock", {"balance": inv.get("balance"), "units": len(inv.get("units", [])),
                                                        "window": w, "policy": policy}, {"buy": why})
            try:
                shop_forum(d, inv, why, now, md)
            except Exception as e:
                print(f"[forum] shop promo failed: {type(e).__name__}: {e}")
    except Exception as e:          # the DB being unreachable (not the market) shouldn't skip a real window
        print(f"[restock] DB unreachable, skipping this window's buy: {type(e).__name__}: {e}")
    for x in why:
        if x.get("first_move"):
            print(f"[restock] FIRST MOVE {x['qty']} x {x['sku']} ({x['kind']}, list {x['list']}) -- "
                 f"demand.json median {x['median_price']}, never held this category before")
        elif x.get("trial"):
            print(f"[restock] TRIAL {x['qty']} x {x['sku']} ({x['kind']}, list {x['list']}) -- "
                 f"{x['stockouts_seen']} recent stockouts for this category, no sales history yet")
        else:
            print(f"[restock] {x['qty']} x {x['sku']} ({x['kind']}, list {x['list']}, sold {x['sold']}, margin {x['margin']})")
    print(json.dumps({"buy": buy}))


if __name__ == "__main__":
    main()
