"""Round 3 restock hook: what the shop buys next. The kit runs it every 60 s and buys what it prints.

    python restock_r3.py --state .market        # prints {"buy": [{"sku": ..., "qty": ...}, ...]}

Code only; the meta-harness (meta_r3.py) moves the numbers in strategy_r3.json. The idea:
  - Profit is the balance at the end, so stock is cash waiting to come back. Spend only down to `reserve_cash` and
    hold at most `stock_value` of stock at cost.
  - Be in many categories: a buyer only sees shops that hold something of the ordered category (shops.json offers),
    so one unit in each category is the first claim on the money. A unit's SKU is the one whose list price is
    closest to the category's median (orders take SKUs 0.5-1.5x a reference price), skipping goods above
    `max_list` and food that keeps less than `min_shelf_hours`.
  - Then depth where things sell: settled deals in the market's ledger (the market publishes no demand weights)
    and our own sales add to a category's score; a category with a score keeps up to `depth` units.
  - A cheaper and a dearer SKU (spread_low, spread_high x the median) in categories that sell, since an order only
    takes SKUs within 0.5-1.5x a reference price we cannot see.
  - The SKUs that settle again and again in the ledger, bought by themselves (up to sku_depth units).
  - "Wanted" posts on the forum (구합니다, looking for ...) that name one of our categories or SKUs raise that
    category for this run.
  - Food first (11/3, the instructor's hint: get a feel for the market with food). Goods pay 1% of cost a day while
    they wait and may break, food costs nothing to hold until its date; so goods at cost stay under `goods_frac` of
    `stock_value`, goods above `max_list` are skipped, and food plans rank `food_first` higher. The meta-harness
    can move all three once goods are seen to sell.
  - Supply windows (agent.md, 11/3 20:23): supply opens once every 20 minutes for 3 minutes, and inventory.json
    carries `restock` (open, opens_at, closes_at). Outside a window we print no lines (the kit would wait up to 90 s
    per line and stall), and inside one the depth is `window_depth` times larger, since what is bought must last
    until the next window. Lines that sell (depth, best sellers) rank before breadth.
  - Stop buying `stop_hours` before the round ends: unsold stock at the end is a loss.
The state between runs (last units seen, ledger position, scores) is in .market/restock_state.json.
"""
import argparse
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx

import demand_r3

HERE = Path(__file__).resolve().parent
MARKET = "https://market.ourolabs.ai"
STRATEGY = Path(os.environ.get("STRATEGY_FILE") or HERE / "strategy_r3.json")   # tests: the defaults, not the live file
GOODS_MIX = {"A": 0.73, "B": 0.19, "C": 0.08}      # our goods supply by grade, 11/10 (A 73%, B 19%, C 9%)
GRADE_COST = {"A": 0.66, "B": 0.48, "C": 0.378}    # supply cost / list by grade: what 101 300k+ units cost us 11/12 00-06 h
# (audit #14). Audit #13 had set A 0.70 from the units still HELD, which are the dear leftovers (pick() sells the
# cheapest-cost unit first), and dear restock fell to 0 while 10 of the market's top 25 300k+ SKUs would earn 3-10%.
A_HOLD = 0.037                                     # storage lost on an A unit that cannot sell at our floor: 3.7 days left at 1% a day (audit #30; a week was 0.07)
REFUND = {"A": 0.03, "B": 0.13, "C": 0.35}           # expected refunds under the 72 h warranty. B as seller_r3.WARRANTY_Q (market adj. 15%, audit #14).
# C as seller_r3.WARRANTY_Q C 0.35: audit #11 set 0.18 from the market's 17.6%, but our own 300k+ C broke on 19 of 65
# sales in 6 h (>=31.8%, 8.88M refunded of 27.98M, 11/12 audit #13), so 0.18 overvalued C and let losing lines pass.
DEFAULTS = {"food_cash": 3_000_000, "dear_first": 1.0, "reserve_cash": 1_500_000, "stock_value": 2_000_000, "max_list": 80_000, "min_shelf_hours": 96,
            "goods_frac": 0.35, "food_first": 0.5,
            "depth": 3, "half_life_hours": 12, "stop_hours": 4, "spread_low": 0.7, "spread_high": 1.3, "sku_depth": 3, "round_end": 1794927540,
            "window_depth": 2.0, "order_weight": 1.0, "surge_max": 1.15,
            "dear_depth": 4, "lines": 18, "breadth_lines": 6, "sku_cap": 8}    # 2026-11-17 23:59 KST
BANKRUPT_LINE = 500_000  # below this a shop may file, and the 5,000,000 it gets comes off profit
CASH_FLOOR = 700_000     # code's own floor under any reserve_cash the meta-harness sets: the line plus a cushion
CUSHION = CASH_FLOOR - BANKRUPT_LINE   # kept above inventory.json's bankrupt_line, which the market may change
BASE_GAP = 60            # seconds between restock runs the depths are tuned for (agent.md, 11/3: every 60 s)
WARRANTY_CUSHION = 300_000  # more while goods are sold with a warranty: a break refunds the whole price
DEAR_HOT = 200            # sku_score (settles, half-life 12 h) of a 300k+ SKU held dear_depth deep: about 60 sold in 6 h
EST_COST = 0.6          # expected cost as a share of list price before the grade is drawn (A 55-75, B 40-60, C 25-45)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def strategy():
    s = dict(DEFAULTS)
    s.update({k: v for k, v in read_json(STRATEGY, {}).items() if k in DEFAULTS and isinstance(v, (int, float))})
    return s


def ledger_settles(after):
    """New settle entries since `after` (at most a few pages), and the last seq seen."""
    out = []
    try:
        with httpx.Client(timeout=8) as c:
            params = {"after": after} if after else {"after": -500}
            for _ in range(4):
                page = c.get(f"{MARKET}/api/ledger", params=params).json()
                if not page:
                    break
                out += [e for e in page if e.get("kind") == "settle"]
                after = page[-1]["seq"]
                if len(page) < 500:
                    break
                params = {"after": after}
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    return out, after


US = "thisjonghoon"
WANT_CAP, WANT_LEN, WANT_CATS = 3, 1000, 3
WANT = re.compile(r"구합니다|구해요|구함|삽니다|찾습니다|찾아요|사고\s*싶|있나요|있으신|\bWTB\b|\bwant(?:ed)?\b|looking for|\bneed\b",
                  re.I)


def a_hist_seed(now, path=None, hours=48, tail=40_000_000):
    """The first a_hist (restock state) from the seller's talks log: one entry per goods grade-A (sku, cost) seen in
    talks over the last `hours`, as cost / list. Read once, when the state has none; {} if the log cannot be read."""
    path = Path(path or os.environ.get("TALKS_FILE") or HERE / "logs" / "talks_r3.jsonl")
    out = {}
    try:
        with open(path, "rb") as f:
            f.seek(max(0, f.seek(0, 2) - tail))
            for line in f:
                try:
                    t = json.loads(line)
                except ValueError:
                    continue
                if (t.get("kind") == "goods" and t.get("grade") == "A" and t.get("list_price") and t.get("cost")
                        and (t.get("t") or 0) > now - hours * 3600):
                    out[f"talk:{t['sku']}:{t['cost']}"] = [t["sku"], round(t["cost"] / t["list_price"], 4), t["t"]]
    except OSError:
        return {}
    return out


def our_deals(st, now, path=None, hours=24, first_tail=12_000_000):
    """Our own agreed goods prices as a share of list, per "sku|grade", over the last `hours` (restock state "deals"):
    reads the talks log from where the last run stopped (st["talks_pos"]); the first run reads the last `first_tail`
    bytes. Leak audit #32: our 300k+ A sold at a median 0.737 of list against market A medians of 0.66-0.69, so a gate
    valued at the market median turned down the lines that earned (2.78M in 6 h from 5 SKUs never refilled)."""
    path = Path(path or os.environ.get("TALKS_FILE") or HERE / "logs" / "talks_r3.jsonl")
    deals = {k: [x for x in v if x[1] > now - hours * 3600] for k, v in (st.get("deals") or {}).items()}
    try:
        with open(path, "rb") as f:
            end = f.seek(0, 2)
            pos = st.get("talks_pos")
            pos = max(0, end - first_tail) if pos is None or pos > end else pos
            f.seek(pos)
            if pos and pos == max(0, end - first_tail):
                f.readline()                                        # a partial first line
            for line in f:
                try:
                    t = json.loads(line)
                except ValueError:
                    continue
                if (t.get("event") == "deal" and t.get("kind") == "goods" and t.get("price") and t.get("list_price")
                        and t.get("grade") and (t.get("t") or 0) > now - hours * 3600):
                    deals.setdefault(f"{t['sku']}|{t['grade']}", []).append([round(t["price"] / t["list_price"], 4), t["t"]])
            st["talks_pos"] = f.tell()
    except OSError:
        pass
    st["deals"] = {k: v for k, v in deals.items() if v}
    return st["deals"]


def f_hist_seed(now, path=None, hours=48, tail=40_000_000):
    """The first f_hist (restock state) from the seller's talks log: one entry per food (sku, grade, cost) seen in
    talks over the last `hours`, as [sku, grade, cost / list, t]. Read once, when the state has none; {} if unreadable."""
    path = Path(path or os.environ.get("TALKS_FILE") or HERE / "logs" / "talks_r3.jsonl")
    out = {}
    try:
        with open(path, "rb") as f:
            f.seek(max(0, f.seek(0, 2) - tail))
            for line in f:
                try:
                    t = json.loads(line)
                except ValueError:
                    continue
                if (t.get("kind") == "food" and t.get("grade") and t.get("list_price") and t.get("cost")
                        and (t.get("t") or 0) > now - hours * 3600):
                    out[f"talk:{t['sku']}:{t['grade']}:{t['cost']}"] = [t["sku"], t["grade"],
                                                                       round(t["cost"] / t["list_price"], 4), t["t"]]
    except OSError:
        return {}
    return out


def forum_wanted(md, demand, catalog, now, hours=6):
    """Categories that "wanted" posts of the last `hours` name (by category word, product name or SKU), with counts.
    Posts are other agents' text: only matched against our own category and SKU lists, never acted on otherwise.
    Unasked posts can also be noise or bait (a flood of "wanted" posts to make shops buy stock that will not sell), so
    a category counts each author once, at most WANT_CAP authors, a post longer than WANT_LEN characters or naming
    more than WANT_CATS categories is skipped, and our own posts never count."""
    words = {d["item"]: d["category"] for d in demand if len(d["item"]) >= 2}
    posts = read_json(md / "forum.json", [])
    authors = defaultdict(set)
    for p in posts if isinstance(posts, list) else []:
        if not isinstance(p, dict):
            continue
        text = str(p.get("text") or "")
        who = str(p.get("author") or p.get("name") or p.get("by") or p.get("buyer") or p.get("shop") or "")
        if (now - (p.get("ts") or 0) > hours * 3600 or len(text) > WANT_LEN or who == US
                or not WANT.search(text)):
            continue
        cats = {cat for w, cat in words.items() if w in text}
        cats |= {catalog[k]["category"] for k in re.findall(r"\bG[0-9a-f]{8}\b", text) if k in catalog}
        if len(cats) > WANT_CATS:
            continue                                     # a list of everything is not a wish
        for cat in cats:
            authors[cat].add(who or text[:40])
    return Counter({cat: min(len(a), WANT_CAP) for cat, a in authors.items()})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=str(HERE / ".market"))
    md = Path(ap.parse_args().state)
    s = strategy()
    inv = read_json(md / "inventory.json", {})
    catalog = read_json(md / "catalog.json", {})
    demand = read_json(md / "demand.json", [])
    st = read_json(md / "restock_state.json", {"after": 0, "score": {}, "seen": [], "t": time.time()})
    now = time.time()
    # the instructor changes the rules while the round runs (11/3: "the stock condition becomes 20 minutes"), so the
    # numbers we can read come from the market's own files, not constants: the bankruptcy line from inventory.json,
    # and the time since our last run (if runs come less often, each run buys for longer: depth scales up, to 4x)
    gap = max(0.0, now - st.get("t", now))
    scale = min(4.0, max(1.0, gap / BASE_GAP / 2)) if st.get("seen") is not None and gap > 2 * BASE_GAP else 1.0
    line = inv.get("bankrupt_line") if isinstance(inv.get("bankrupt_line"), (int, float)) else BANKRUPT_LINE
    window = inv.get("restock") if isinstance(inv.get("restock"), dict) else None
    if window:
        scale = max(scale, s["window_depth"])
    if inv.get("round", 0) < 3 or not catalog or not demand:
        print(json.dumps({"buy": [], "why": "no Round 3 stock or catalog yet"}))
        return

    # scores decay with a half-life, then gain the market's new settles (per category and per SKU) and our own sales
    decay = 0.5 ** ((now - st.get("t", now)) / 3600 / s["half_life_hours"])
    score = defaultdict(float, {k: v * decay for k, v in st.get("score", {}).items()})
    sku_score = defaultdict(float, {k: v * decay for k, v in st.get("sku_score", {}).items()})
    settles, st["after"] = ledger_settles(st.get("after", 0))
    # settle times of each 150k+ SKU over the last 6 h, for the dear-line depth cap; history counts once 6 h of it was kept
    settle_ts = defaultdict(list, {k: [x for x in v if x > now - 6 * 3600] for k, v in st.get("settle_ts", {}).items()})
    st["settle_since"] = st.get("settle_since", now)
    for e in settles:
        sku = e["data"].get("item")
        cat = (catalog.get(sku) or {}).get("category")
        if cat:
            score[cat] += 1
            sku_score[sku] += 1
            if (catalog.get(sku) or {}).get("list_price", 0) >= 150_000 and e.get("ts"):
                settle_ts[sku].append(e["ts"])
    units = inv.get("units", [])
    have = {u["unit"]: u for u in units}
    sold = [u for u in st.get("seen", []) if u["unit"] not in have and u.get("grade") != "expired"
            and (u.get("expires_at") or now + 1) > now]
    for u in sold:                                          # our own sale counts double: it is our buyers
        score[u["category"]] += 2
        if u.get("sku"):
            sku_score[u["sku"]] += 2
    # when each held unit was first seen: a unit that sat over 24 h tells the restock the SKU does not sell
    first = {u["unit"]: (st.get("first") or {}).get(u["unit"], now) for u in units}
    # grade-A goods cost shares as bought (48 h), sold units included: the units still held are the dear leftovers
    # (leak audit #29: held A median 0.733 of list vs 0.661 for the A units sold in 48 h), so drawing from inventory
    # alone made every top SKU's sellable share 0 and no 150k+ line was bought after 11/14 00:22
    a_hist = {k: v for k, v in (st.get("a_hist") or a_hist_seed(now)).items() if v[2] > now - 48 * 3600}
    for u in units:
        if u.get("kind") == "goods" and u.get("grade") == "A" and u.get("list_price") and u.get("cost"):
            a_hist.pop(f"talk:{u['sku']}:{u['cost']}", None)     # the seed's copy of a held unit
            a_hist[u["unit"]] = [u["sku"], round(u["cost"] / u["list_price"], 4), first[u["unit"]]]
    # food cost shares as bought (48 h), per grade, sold units included: food_margin drew on held units only, and
    # pick() sells the cheaper units of a SKU first, so the held ones are the dear leftovers (leak audit #31: 22 food
    # SKUs, 26% of the market's food settles, blocked at a negative margin; supply cost vs market A .669/.686,
    # B .516/.59, C .351/.53)
    f_hist = {k: v for k, v in (st.get("f_hist") or f_hist_seed(now)).items() if v[3] > now - 48 * 3600}
    for u in units:
        if u.get("kind") == "food" and u.get("grade") and u.get("list_price") and u.get("cost"):
            f_hist.pop(f"talk:{u['sku']}:{u['grade']}:{u['cost']}", None)
            f_hist[u["unit"]] = [u["sku"], u["grade"], round(u["cost"] / u["list_price"], 4), first[u["unit"]]]
    our_deals(st, now)
    st.update(first=first, a_hist=a_hist, f_hist=f_hist, settle_ts={k: v for k, v in settle_ts.items() if v},
 score={k: v for k, v in score.items() if v > 0.05}, sku_score={k: v for k, v in sku_score.items() if v > 0.05},
              seen=[{k: u.get(k) for k in ("unit", "sku", "category", "grade", "expires_at")} for u in units], t=now)
    # the hidden demand as it reaches us (demand_r3.py): orders our buyer got and requests to our shop, decayed over
    # two days; added for this run only (the log keeps them), so they do not compound with the settle scores
    order_w, ask_w, ref = demand_r3.signals(now)
    for cat, w in order_w.items():
        score[cat] += s["order_weight"] * w
    for sku, w in ask_w.items():
        # a dear SKU's asks are mostly the same buyers walking away from us again and again (leak audit #28, 11/14:
        # sofa G04ee8423 had a settle score ~80 and asks ~680, so heat 3.8 bought 14 units the market sells ~1.2 an
        # hour): for 150k+ goods the asks can at most double the settle score
        if ((catalog.get(sku) or {}).get("list_price") or 0) >= 150_000:
            w = min(w, sku_score.get(sku, 0.0) / max(s["order_weight"], 1e-9))
        sku_score[sku] += s["order_weight"] * w
    wanted = forum_wanted(md, demand, catalog, now)          # categories buyers asked for on the forum (not kept)
    for cat, n in wanted.items():
        score[cat] += 2 * n

    # stock at 66.6M of the 70M cap held 55.7M in 110 units of 300k+, 97 of them slow (51.3M): spend and goods_room
    # were near zero with 42M of cash idle while we held 0 of the top 5 SKUs. Slow 300k+ units (sku_score under
    # DEAR_HOT) do not count against the cap or the goods room; the seller clears them, the room goes to the hot lines
    slow_dead = lambda u: ((catalog.get(u.get("sku")) or {}).get("list_price") or 0) >= 300_000 and sku_score.get(u.get("sku"), 0) < DEAR_HOT
    # a goods unit held over 24 h counts at half its cost in the cap and the goods room: 650 stale units (53.8M) used up the room
    # while 4 of the market's top-8 300k+ SKUs were held at 0 (we sold 2.8 an hour of the market's 504); the stale depth caps
    # below still stop their refill
    held_cost = lambda u: u["cost"] * (0.5 if u.get("kind") == "goods" and now - first[u["unit"]] > 24 * 3600 else 1)
    stock = sum(held_cost(u) for u in units if not slow_dead(u))
    warranty = (read_json(STRATEGY, {}).get("warranty_hours") or 0) > 0
    floor = max(s["reserve_cash"], line + CUSHION + (WARRANTY_CUSHION if warranty else 0))
    # food keeps its own slice of the cash, outside the stock cap (11/10: after the 12:41 stock_value lift a window spent
    # 31M on beds, cash sat at the reserve and food restock stopped for 2 h; our food settles halved while the market's
    # held; and stock_value counted food too, so capping goods at 130M blocked food as well). Goods may not touch it.
    free = inv.get("balance", 0) - floor
    spend_food = max(0, min(free * 0.5, s.get("food_cash", 3_000_000)))     # at most half: goods keep the rest
    spend_goods = min(free - spend_food, s["stock_value"] - stock)
    spend = max(spend_goods, 0) + spend_food
    # 4 h, not 36 (11/10): Book counts unsold stock at cost, so stock bought late costs only storage (1% a day) and
    # food that may expire; 36 h stopped all buying from 11/16 11:59 and dried up the last day and a half of revenue
    stopping = s["round_end"] and now > s["round_end"] - s["stop_hours"] * 3600
    buy, why = [], ""
    if window and window.get("open") is False:
        why = f"supply closed until {time.strftime('%H:%M:%S', time.localtime(window.get('opens_at') or now))}"
    elif stopping:
        why = "round ends soon: no new stock"
    elif spend <= 0:
        why = f"spend limit reached (balance {inv.get('balance')}, cash floor {floor}, stock at cost {stock})"
    else:
        live = [u for u in units if u.get("grade") != "expired"]
        # the market's surge (rules of 11/4 21:14): past 50 units of a SKU in an hour each unit costs 0.2% more, up to
        # +50%. At 21:14 the mattress everyone stocks cost 1.47x, two others 1.3x: a grade-A unit (55-75% of list) at
        # 1.47x costs 81-110% of list, more than a buyer pays. SKUs above surge_max (the team's knob) are left out, so
        # the plan takes the next SKU of the category instead, and the rest are priced at their surged cost
        surge = {x["sku"]: x.get("factor", 1.0) for x in inv.get("surge") or [] if isinstance(x, dict) and x.get("sku")}
        surged = {k for k, f in surge.items() if f > s.get("surge_max", 1.15)}
        catalog = {k: v for k, v in catalog.items() if k not in surged}
        # held counts what sells: grade C goods are left out (11/10: most buyers' kits never pay for C, so a SKU holding
        # C units stopped being restocked with A and buyers were offered only the C that goes unpaid)
        count = Counter(u["category"] for u in live)
        # and so are 150k+ A/B units whose floor sits above the market's median for the SKU and grade (the seller's floor:
        # cost x (1 + min_margin) / (1 - refund), seller_r3.py:470): leak audit #36, the cheap A units sold out and the
        # A left (0.70-0.75 of list, 6-11% of talks closed vs 61-74% under 0.66) filled the depth, so restock bought
        # nothing: Gf27f8931 went 28 deals (20h) -> 0 (23h) with no purchase 22:04-00:21, then 9 deals in 25 min
        try:
            import prices_r3 as _prices
            price_lines = (_prices.read(md) or {}).get("skus") or {}
        except Exception:
            price_lines = {}

        def dead_dear(u):
            if u.get("kind") != "goods" or u.get("grade") not in ("A", "B") or (u.get("list_price") or 0) < 150_000:
                return False
            st_u = price_lines.get(f"{u['sku']}|{u['grade']}")
            if not st_u or st_u.get("n", 0) < 3 or not st_u.get("median") or not u.get("cost"):
                return False
            fl_u = u["cost"] * (1 + s.get("min_margin", 0.02)) / (1 - REFUND.get(u["grade"], 0))
            return fl_u / u["list_price"] > st_u["median"]

        held = Counter(u["sku"] for u in live
                       if not (u.get("kind") == "goods" and u.get("grade") == "C") and not dead_dear(u))
        slow_held = sum(1 for u in live if slow_dead(u))
        stale = {u["sku"] for u in live if now - first[u["unit"]] > 24 * 3600}
        by_cat = defaultdict(list)
        for sku, c in catalog.items():
            by_cat[c["category"]].append(dict(c, sku=sku))
        plans = {}                                           # sku -> (priority, entry, qty)

        def plan(prio, c, qty):
            # the highest priority and the largest qty any entry gives a SKU: keeping only the top entry's qty let a
            # category's spread entry (qty 1) replace the SKU's own line (leak audit #30: G0260b9fc prio 15,864 qty 1
            # over its SKU line qty 9; 189 asks got 'none in stock' while it closed 34 of 62 talks)
            # held units come off once, after every entry is in (finish_plans): taking them off each entry first dropped
            # a SKU's top-priority entry (a category entry, qty 1-4) as soon as one unit was held, leaving only its SKU
            # line far below the 19-line cut (leak audit #31: G3108bfac and G0e11c1b4 planned only at 0 held; they sold
            # 14 and 10 at 07:00 and got no supply at 08:00)
            p0, c0, q0 = plans.get(c["sku"], (prio, c, 0))
            plans[c["sku"]] = (prio, c, max(qty, q0)) if prio > p0 else (p0, c0, max(qty, q0))

        def finish_plans():
            for sku in list(plans):
                p0, c0, q0 = plans[sku]
                if q0 - held[sku] > 0:
                    plans[sku] = (p0, c0, q0 - held[sku])
                else:
                    del plans[sku]

        for d in demand:
            # the SKU to stock first: nearest the reference price our orders point to, else the category's median
            cat, med = d["category"], ref.get(d["category"]) or d["median_price"]
            if d["kind"] == "goods" and med > s["max_list"] * 2:     # the same 2x bound as the SKU loop below: 14 of 127 goods orders point at 1.4M+
                continue
            # a selling category relaxes the bar to 48 h, not to none: scores run into the hundreds, so a free pass picked by price
            # alone stocked food with 4.7 h left against the 12 h buyers ask (shelf_refused_h 3-6 an hour, 2-star reviews)
            bar = min(s["min_shelf_hours"], 48) if score[cat] >= 3 else s["min_shelf_hours"]
            ok_food = lambda c: d["kind"] != "food" or (c.get("shelf_hours") or 0) >= bar
            skus = [c for c in by_cat.get(cat, []) if c["list_price"] <= max(s["max_list"], med) and ok_food(c)]
            if not skus:
                continue                                     # e.g. only short-lived food where nothing sells yet
            near = lambda x: min(skus, key=lambda c: abs(c["list_price"] - x))
            depth = 1 + (min(round(s["depth"] * scale) - 1, math.ceil(score[cat] * scale / 2)) if score[cat] >= 1 else 0)
            if d["kind"] == "food":
                depth = min(depth, round(2 * min(scale, 2.0)))
            prio = score[cat] + (1 if count[cat] == 0 else 0) + (s["food_first"] if d["kind"] == "food" else 0)
            plan(prio, near(med), depth)
            # where things sell, a cheaper and a dearer SKU too: an order takes SKUs at 0.5-1.5x a hidden reference
            # price, so one SKU at the median misses the orders whose range sits above or below it
            if score[cat] >= 1:
                for f in (s["spread_low"], s["spread_high"]):
                    plan(prio - 0.5, near(med * f), 1)
        # the SKUs the market keeps buying, by themselves (the market says some products sell much more than others)
        for sku, sc in sorted(sku_score.items(), key=lambda kv: -kv[1]):
            c = catalog.get(sku)
            if sc < 2 or not c or (c["kind"] == "goods" and c["list_price"] > s["max_list"] * 2):
                continue
            if c["kind"] == "food" and (c.get("shelf_hours") or 0) < 48:
                continue
            plan(1.5 * sc, dict(c, sku=sku), min(20, round(s["sku_depth"] * scale), math.ceil(sc * scale / 2)))
        finish_plans()
        # a supply window lasts 3 minutes and the kit buys about one line in 10 s, so the order of the lines decides
        # what gets bought: dear goods first (11/4 13:04: three mattresses bought one each sold within minutes, the
        # top shop made 57M from furniture at 459,240 a sale, while pillows, kimchi and water took the early lines)
        dear = lambda c: s.get("dear_first", 1.0) * min(3.0, c["list_price"] / 150_000) if c.get("kind") == "goods" else 0
        # a factor, not a bonus: once the market's settles fed sku_score (11/6: kimchi 2,400, water 1,300, the top mattress
        # 1,500) the priorities ran into the thousands and a bonus of at most 9 changed nothing, so cheap lines took every
        # window while 4,379 units of 300k+ settled in 6 h and we held none of the 12 best sellers
        ranked = sorted(plans.values(), key=lambda p: (-(p[0] * (1 + dear(p[1]))), -p[1]["list_price"]))
        # breadth first (11/5 10:00): buyers only visit shops that hold the ordered category, and the depth and dear-first
        # weights had crowded it out: 291 units in 8 of 140 categories, four blankets 22-24 deep, talks with us down from
        # 350 to 250 an hour while the market's settles rose. The first `breadth_lines` lines go to categories we hold
        # none of, the rest keep their order
        # 11/6 13:44: the empty-category lines took 2 gaming PCs and an air purifier (3.2M) ahead of the market's best
        # seller, which got 1 unit of the room left. Best-selling 300k+ goods (sku_score DEAR_HOT or more) go first;
        # breadth lines take no other 150k+ goods, so a category is covered cheaply, not with stock that may not sell
        is_dear = lambda c: c.get("kind") == "goods" and c["list_price"] >= 150_000
        best = [p for p in ranked if is_dear(p[1]) and p[1]["list_price"] >= 300_000
                and sku_score.get(p[1]["sku"], 0) >= DEAR_HOT]
        empty = [p for p in ranked if count[p[1]["category"]] == 0 and p not in best
                 and not is_dear(p[1])][:int(s.get("breadth_lines", 6))]
        ranked = best + empty + [p for p in ranked if p not in best and p not in empty]
        goods_room = s["goods_frac"] * s["stock_value"] - sum(held_cost(u) for u in live if u.get("kind") == "goods" and not slow_dead(u))
        # 11/4 22:30, the last 6 h of settles: the top shops sold 470-575 units listed at 150,000 or more each (sofas,
        # beds, bedding; average ticket 600-820k) and 0.2-0.7 billion of revenue; we sold 25 (21M), buying at most
        # 10 lines a window and one unit of a dear SKU a line, with 21M of cash idle. A window takes about 18 lines,
        # so up to `lines` of them, and a dear SKU (list 150,000 or more) gets `dear_depth` units: they sell in minutes
        # expected margin of a goods line before buying it (11/11 leak audit #4: 21.1M of 82.2M bought in 6 h had an
        # expected margin of 2% or less after refunds). Grade is drawn at supply, so the line's value is the grade mix
        # of our goods supply times what the market pays for that SKU per grade (prices_r3 'sku|grade', else the
        # category's), against the supply cost per grade times the SKU's surge. A line without enough market data passes.
        try:
            import prices_r3
            lines = prices_r3.read(md)
        except Exception:
            lines = {}

        own = defaultdict(list)                          # what we actually paid per SKU and grade, cost / list
        own_food = defaultdict(list)
        sku_paid = defaultdict(list)                     # the same shares over all grades, to size a line
        for u in units:
            if u.get("kind") == "goods" and u.get("list_price"):
                own[(u["sku"], u.get("grade"))].append(u["cost"] / u["list_price"])
                sku_paid[u["sku"]].append(u["cost"] / u["list_price"])
            elif u.get("kind") == "food" and u.get("list_price"):
                sku_paid[u["sku"]].append(u["cost"] / u["list_price"])
        for sku_f, g_f, share_f, _ in st["f_hist"].values():   # food cost by grade as bought (f_hist, 48 h)
            own_food[(sku_f, g_f)].append(share_f)

        own_a = defaultdict(list)                        # our A cost shares per SKU, as bought (a_hist, 48 h)
        for sku_a, share_a, _ in st["a_hist"].values():
            own_a[sku_a].append(share_a)
        own_a = dict(own_a)

        deals_mine = st.get("deals") or {}                  # our agreed goods prices (our_deals), read before st is reused below

        def expected_margin(c):
            """(the grade mix's margin, grade A's alone, the share of A draws that can sell) or None. A alone too (11/11 12:5x audit #3): pick() offers A
            before B and C, so B and C rarely sell and their margin must not carry an A line that loses."""
            num = den = 0.0
            a_only = None
            sellable = 1.0
            for g, w in GOODS_MIX.items():
                st = (lines.get("skus") or {}).get(f"{c['sku']}|{g}")
                if not st or st.get("n", 0) < 3:
                    cat = (lines.get("categories") or {}).get(f"{(c.get('category') or '').split('>')[-1]}|{g}") or {}
                    st = cat.get("h24") or cat.get("h6")
                    if not st or st.get("n", 0) < 10:
                        if g != "C":
                            return None
                        st = None                                   # C without a market line: valued at cost
                paid = own.get((c["sku"], g))
                # held units are the leftovers of what we paid (the cheapest sell first), so they overstate the next
                # unit's cost: never above the measured supply cost of the grade (audit #14)
                cost = min(sorted(paid)[len(paid) // 2] if paid else GRADE_COST[g], GRADE_COST[g]) * surge.get(c["sku"], 1.0)
                # C was counted at cost while it went unpaid (11 of 94 dear C units in 6 h, 11/11 audit #5). Since C
                # carries the warranty (audit #10) it is paid (19 of 20 deals, 0.539 of list on a cost of 0.339), so it
                # is valued at its market median less REFUND C 0.35 (11/11 21:4x audit #11: 30 of the market's 40
                # best-selling 150k+ SKUs failed the check and 150k+ restock had stopped: 3 units in 6 h)
                # the price a unit of ours fetches: the market's median, or our own median agreed price for the SKU and
                # grade over 24 h when we have 3 or more deals and it is higher (leak audit #32: our 300k+ A at 0.737
                # of list vs market 0.66-0.69)
                mine = deals_mine.get(f"{c['sku']}|{g}") or []
                med = None if st is None else st["median"]
                sell_at = med                                   # the price the A sellable test holds a draw against
                if med is not None and len(mine) >= 3:
                    mine_sorted = sorted(x[0] for x in mine)
                    med = max(med, mine_sorted[len(mine_sorted) // 2])
                    # our deals exist only above our floor, so their median runs high: the sellable test takes their
                    # lower quartile (leak audit #34: 18 of 47 dear units bought after 8615b70, 10.75M, sit with a floor
                    # above our own median sale price; A at cost 0.70+ closed 6.6% of talks, under 0.66 closed 44%)
                    sell_at = max(sell_at, mine_sorted[len(mine_sorted) // 4])
                value = cost if st is None else med * (1 - REFUND.get(g, 0))
                num += w * (value - cost)
                den += w * cost
                if g == "A" and cost:
                    # A is drawn at 0.55-0.75 of list (quartiles 0.62/0.667/0.702) against a market paying 0.67-0.70, so 39-60%
                    # of the units cannot sell at our floor (leak audit #20: 148 A/B units, 77.4M, held above the market). Value
                    # A over our own cost draws for the SKU (pooled A when it has under 4): a draw sells only when cost / 0.97
                    # is at or below the market median, the rest is charged A_HOLD of its cost as storage
                    draws = own_a.get(c["sku"]) if len(own_a.get(c["sku"], ())) >= 4 else [x for v in own_a.values() for x in v]
                    if st is not None and draws:
                        sv = [d * surge.get(c["sku"], 1.0) for d in draws]
                        # the seller's floor is cost x (1 + min_margin) / (1 - WARRANTY_Q A) (seller_r3.py:470), not cost / 0.97
                        fl = (1 + s.get("min_margin", 0.02)) / 0.97
                        a_only = sum(((value - d) if d * fl <= sell_at else -A_HOLD * d) for d in sv) / sum(sv)
                        # the same draws size a dear line: 5 of the 6 A units bought after ee706d8 sit above the market
                        sellable = sum(d * fl <= sell_at for d in sv) / len(sv)
                    else:
                        a_only = (value - cost) / cost
            return (num / den, a_only, sellable) if den else None

        FOOD_MIX = {"A": 0.25, "B": 0.50, "C": 0.25}   # food grades at supply (agent.md: A 25%, B 50%, C 25%)

        def food_margin(c):
            """The expected margin of a food line over the grades supply draws: per grade, what we paid for this SKU
            at that grade (median, times its surge) against the market's median for the SKU at that grade, weighted by
            FOOD_MIX over the grades with data; None without any. 11/11 12:5x audit #4: food lines had no check.
            Leak audit #10 (18:4x): the first check pooled our cost over all grades against the market's A median, so
            a SKU whose A units cost 0.72 of list (market A 0.716) passed and food A closed 14% of 737 talks."""
            num = den = 0.0
            for g, w in FOOD_MIX.items():
                paid = own_food.get((c["sku"], g))
                st = (lines.get("skus") or {}).get(f"{c['sku']}|{g}")
                if not paid or not st or st.get("n", 0) < 5 or not st.get("median"):
                    continue
                cost = sorted(paid)[len(paid) // 2] * surge.get(c["sku"], 1.0)
                num += w * (st["median"] - cost)
                den += w * cost
            return num / den if den else None

        # min_margin is the seller's number (not in this file's DEFAULTS): read it from the strategy, 2% if absent
        # (11/11 01:03: s["min_margin"] raised KeyError and stopped every restock run from 00:49)
        min_m = (read_json(STRATEGY, {}) or {}).get("min_margin", 0.02)
        for prio, c, qty in ranked:
            if c.get("kind") == "goods":
                try:
                    e = expected_margin(c)
                except Exception:
                    e = None                                 # a check that cannot run never blocks a purchase
                if e is not None and (e[0] < min_m + 0.01 or (e[1] is not None and e[1] < min_m)):
                    continue                                 # 0.01 is about a day of storage
            elif c.get("kind") == "food":
                try:
                    f = food_margin(c)
                except Exception:
                    f = None
                if f is not None and f < min_m:
                    continue
            if len(buy) >= s.get("lines", 18):
                break
            if c.get("kind") == "goods" and c["list_price"] >= 150_000:
                # 6 h: 300k+ sold 10.3 an hour (36 held, 3.5 h of stock), the 150-300k band 1.3 an hour (49 held, 37 h):
                # the full depth only for 300k+, the band below stays at 2 so its money buys 300k+ units
                # 6 h: 300k+ sold 2.2 an hour with 35 units (11.5M, about 16 h of stock) held, while <30k sold 81 an hour:
                # full depth only for a 300k+ SKU that keeps selling (sku_score 6 or more, the cheap-goods hot bar), else 2
                # the bar is DEAR_HOT, not 6: once settles fed sku_score every SKU passed 6, and at 13:04 on 11/6 an
                # empty-category line bought 8 sofas (7.2M, 19 sold market-wide in 6 h) before the best seller (416) got 1
                # the 100-300k band settled 183 units an hour and we sold 0.3 (0.2%) holding 16 units at depth 2 while 64M sat
                # idle: a 150-300k SKU that reached DEAR_HOT gets the full depth too, with heat capped at 2x (margin there is
                # 8.9k a unit, price 0.61 against cost 0.57)
                full = sku_score.get(c["sku"], 0) >= DEAR_HOT
                # 6 h: the market sold 334-341 units of each top 300k+ SKU while we held 5-8, with 39.7M of cash idle: the
                # depth grows with sku_score up to 5x dear_depth (goods_room and spend still bound it); 6 h: 300k+ settled
                # 572 an hour and we got 2.8%, the top SKUs sold 158-398 units while we held 0-12, and the 3x cap (12) bound
                # a SKU the market surged past 1.05x (surge_max drops only 1.15x) gets no heat: past 50 units an hour each unit
                # costs 0.2% more, our dear goods cost a median 0.674 of list against 0.576 broad, and 300k+ sold at 0.62
                # against a 0.57 cost, so a deep line bought into a surge costs more than its 5-point margin
                heat = min(5.0 if c["list_price"] >= 300_000 else 2.0, max(1.0, sku_score.get(c["sku"], 0) / DEAR_HOT))
                # likewise a hot SKU with a unit of ours unsold over 24 h: 495 goods units (21.4M at cost) sat over a day, and
                # G021aae64 had 20 bought and 0 sold, as heat follows the market's sku_score and not our own sales: it stays
                # at dear_depth, the room and cash go to hot SKUs that sell at our price
                if surge.get(c["sku"], 1.0) > 1.05 or c["sku"] in stale:
                    heat = 1.0
                # stock sat at its cap (69.5M of 70M) with 53.2M in 107 units of 300k+, about 65 of them non-hot SKUs bought at
                # 2 while 8 best sellers sold 148-359 units each: a non-hot 300k+ SKU gets 1, the room goes to the hot ones
                # and none once we hold 40 slow 300k+ units: 131 units of 300k+ (62.5M) sold 5.2 an hour, 1.0% of the market's
                # 532, and 323 units sat unsold over a day, while the lines went to a fresh slow SKU instead of the hot ones
                slow = (0 if slow_held >= 40 else 1) if c["list_price"] >= 300_000 else 2
                depth = int(int(s.get("dear_depth", 4)) * heat) if full else min(slow, int(s.get("dear_depth", 4)))                # leak audit #21: 208 A units (84.5M) sit above the market and the pile grows by 45% of each A buy, as a line
                # that passes the average-margin check was bought whole: depth times the share of A draws that can sell
                if e is not None and depth:
                    depth = round(depth * e[2]) if e[2] <= 0 else max(1, round(depth * e[2]))
                # the depth, not the SKU plan's qty: on 11/6 the market's best sellers (sku_score 600-1,500) planned 20
                # units a line, 2.5-28M at cost, more than the goods room left, so each was skipped and cheap lines took
                # the window; 4,379 units of 300k+ settled market-wide in 6 h, we held none of the top 12 and sold 6
                # one line took 14 sofas (G04ee8423) at 0.638 of list while the whole market sold 7 in 6 h: at about 4% margin
                # a line holds no more than the market's settles of that SKU in 6 h (once 6 h of settles are kept)
                if depth and now - st["settle_since"] >= 6 * 3600:
                    depth = min(depth, max(1, math.ceil(len(settle_ts.get(c["sku"], [])))))
                # a line valued at our own agreed price (our_deals) holds about 6 h of our own deals of the SKU: in a dry
                # run the own-price gate opened 9 dear lines, 58 units (~25M) in one window, 10-11 deep on SKUs we agree
                # on 2-3 times in 6 h
                own_n = sum(len(v) for k, v in deals_mine.items() if k.split("|")[0] == c["sku"])
                if depth and own_n:
                    depth = min(depth, max(2, math.ceil(own_n / 4)))
                qty = depth - held[c["sku"]]
            else:                     # a cheap SKU no deeper than sku_cap: 22-24 of one blanket sat in stock on 11/5
                # 6 h: the 100-300k band sold 1.0 an hour with 46 units held (46 h of stock) while 300k+ sold 6.0 an hour
                # at 95,294 a unit: goods listed 100-150k stay at 2 too, so room and cash go to 300k+
                cap = 2 if c.get("kind") == "goods" and c["list_price"] >= 100_000 else int(s.get("sku_cap", 8))
                # goods under 100k sold 77.5 + 21.5 units an hour (439k of 1.3M margin) on 234 + 151 held, about 3 h of stock,
                # while 60M of cash sat idle: a cheap SKU that keeps selling (sku_score 6 or more) may hold twice sku_cap
                # the same bar as DEAR_HOT: at 6 every SKU passed once settles fed sku_score, and on 11/6 goods at 30-100k
                # grew from 138 to 320 units (10.5M, 20 h of stock at 15.7 sold an hour) while the stock cap was full
                # under 150k, not 100k (11/10): G75f11c19 (122,900, sku_score 679) held 0 at depth 2
                hot = c.get("kind") == "goods" and c["list_price"] < 150_000 and sku_score.get(c["sku"], 0) >= DEAR_HOT
                # 1h: <30k settled 1586 and 30-100k 631 an hour, we held 4.3% and 2.3% (7.5 h and 11 h of stock) with 26M idle:
                # the multiplier grows with sku_score from 2x to 3x, as 300k+ depth does
                # no multiplier while a unit of the SKU sat unsold over 24 h (654 goods units, 50.4M at cost, sat over a day;
                # 815 of 1,238 units under 30k, a margin of 1,263 a unit): the stale unit is not refilled to hot depth
                mult = min(3.0, max(2.0, sku_score.get(c["sku"], 0) / DEAR_HOT)) if hot and c["sku"] not in stale else 1
                if mult > 1:
                    qty = held[c["sku"]] + int(mult * qty)      # the planned depth times mult, held units included
                qty = min(qty, int(min(cap, int(s.get("sku_cap", 8))) * mult) - held[c["sku"]])
                # a goods SKU nobody has bought (sku_score under 1: no market settle, no sale of ours lately) gets 1 unit
                # to try, not the category's depth (11/11 15:1x: 216 of our 329 cheap goods over 24 h, 15 a SKU, were
                # SKUs with no talk at our shop and no settle in the market in 6 h: a plant pot at 3,800, a mini mortar)
                # the bar is 3, not 1: once settles fed sku_score almost no SKU was under 1, and 464 units (47.6M, 42% of
                # the stock) sat unsold over a day; a score of 1-3 is a few market settles and none of ours
                if c.get("kind") == "goods" and sku_score.get(c["sku"], 0) < 3:
                    qty = min(qty, 1 - held[c["sku"]])
                # no refill of a not-hot goods SKU while one of its units sat unsold over 24 h (11/12: 482 units, 22.1M at
                # cost, in 129 categories; 1,168 of them under 30k), a score of 3 or more still refilled them to sku_cap
                if c.get("kind") == "goods" and not hot and c["sku"] in stale:
                    qty = 0
            if qty <= 0:
                continue
            qty = min(int(qty), 20)    # the market takes 1-20 units a line: 24 was refused (400) 18 times on 11/4
            # 6 h: our 300k+ units cost 0.48 of list and <30k 0.48, not the flat 0.60, so qty and the goods_room/spend
            # decrements overstated cost by 25% while 107.8M sat idle: the SKU's own median paid share, EST_COST if none held
            paid = sku_paid.get(c["sku"])
            share = sorted(paid)[len(paid) // 2] if paid else EST_COST
            unit = c["list_price"] * share * surge.get(c["sku"], 1.0)
            # what the cash (and, for goods, the room) affords, not nothing: until 11/10 only 150k+ goods were cut down,
            # so a 15-unit food line that did not fit whole was skipped (kimchi, apples, water, all sku_score 1,400+, held 0)
            if unit <= 0:              # a catalog row with no list price: nothing to size
                continue
            budget = min(spend_goods, goods_room) if c.get("kind") == "goods" else spend_food
            qty = min(qty, int(max(budget, 0) // unit))
            if qty <= 0:
                continue
            cost = unit * qty
            if cost > budget:
                continue
            if c["kind"] == "goods":
                goods_room -= cost
                spend_goods -= cost
            else:
                spend_food -= cost
            buy.append({"sku": c["sku"], "qty": qty})
            spend -= cost
        why = (f"{len(ranked)} SKUs short, buying {len(buy)} lines" + (f" (runs {gap:.0f} s apart: depth x{scale:.1f})" if scale > 1 else "")
               + (f"; {len(surged)} SKUs left out for the market's surge (over {s.get('surge_max', 1.15)}x)" if surged else "")
               + (f"; forum asks for {', '.join(k.split('>')[-1] for k in list(wanted)[:5])}" if wanted else ""))
    (md / "restock_state.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    try:
        import prices_r3                         # going prices from the ledger, forum claims checked (prices_r3.py)
        prices_r3.write(md)
    except Exception as e:                       # the price file is advice; never stop a restock for it
        print(f"[prices] not written: {type(e).__name__}", file=sys.stderr)
    print(json.dumps({"buy": buy, "why": why}, ensure_ascii=False))


if __name__ == "__main__":
    main()
