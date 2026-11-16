"""Round 3 path of the market buyer (kit v4): real products, stock, quality, and surplus.

    python market_buyer.py --shops .market/shops.json --item 김치 --budget 30000 --order .market/order.json

What counts for a buyer in Round 3 is surplus (the SKU's list price minus what we paid, zero value if the food
did not last `use_within_hours` or the goods broke), forum votes on our posts, and the number of settled deals.

1. Learn. New lines of the kit's payments.jsonl (why it did not pay) and broken.jsonl (goods that broke) become
   memory evidence first, so this order already avoids last order's trap.
2. Pick. Every (shop, SKU) the kit lists for the order is a candidate. Code ranks them by expected surplus:
   list price minus the price we expect to pay (what this shop took from us before, as a share of list price; with a
   shop we never paid, what it settled for with everyone in the market's ledger),
   with shops our memory or the ledger caught lying about grades, or whose goods broke on us, left out.
   Food whose shelf life cannot cover `use_within_hours` even at grade B is a last resort.
3. Trade. buyer.shop() haggles over A2A as in weeks 6-7 (harness offers, code guards), opening with
   "I want to buy <name> (<sku>)" and the shelf life or certificate we need. The cap is min(budget, CAP x list):
   above the list price a deal loses surplus, and near it a broken or spoiled unit costs more than it saves.
4. Accept. The last line carries an accept policy the kit checks before paying: the market's certificate,
   enough shelf life for food, and no refurbished (grade C) goods, whose hidden break rate can turn a deal
   into minus the price.
5. Remember and post. Each trade is one raw line in memory.jsonl; at most every 5 minutes, a factual note on
   the forum (what we paid against list price, whether claims were true, with ledger refs).
"""
import json
import os
import re
import statistics
import time
from pathlib import Path

import asyncio

import httpx

import buyer
import code_ab_r3
import demand_r3
import forum_r3
import prices_r3
import team_r3
import market_intel
import harness
from harness import log

HERE = Path(__file__).resolve().parent
MD = Path(os.environ.get("MARKET_DIR") or HERE / ".market")
CAP = 0.9            # never pay more than this share of the SKU's list price
PRICE_GUESS = 0.75   # expected price as a share of list price with a shop we never paid (cost is 25-75% of list)
FOOD_MARGIN = 1.1    # food must have use_within_hours x this left at checkout (the kit's clock and the shop's differ)
GRADE_SECS = 3 * 3600   # a shop and SKU that sent a grade we do not pay for is skipped this long
BREAK_HOURS = 6          # goods break only within 6 h of the sale (agent.md)
GOOD_GRADES = ["A", "B"]  # C is refurbished: the most likely to break after the sale
# Grade C goods (11/4, the ledger's last 24 h: A broke 1.4%, B 5.2%, C 17.9% of settled units). A settled deal is
# worth the list price, 0 if the goods break, and a warranty refunds the price, so C is worth buying with a warranty
# covering the 6 h in which goods can break, or at C_CAP of list or less (0.82 - 0.65 = +0.17 of list expected).
# Asking for A or B only made shops holding C answer "none in stock" (458 quick rejects 06:00-09:40).
C_CAP = 0.65
C_WARRANTY = 6
B_SHARE = 0.45       # grade B food has at least 45% of its shelf life left (agent.md)
MAX_OFFERS = 8
MAX_TRIES = 3        # shops tried per order when a negotiation ends without a deal
RETRY_UNTIL = 150    # seconds into the order after which no new shop is tried (the kit stops the buyer at 300 s)
MAX_RAISE = 0.4      # each offer moves at most this share of the gap to the shop's ask
# how far an offer may rise, by what we buy (11/11 13:2x, the user's plan: "Buyer 특성별 차별화, 가구 < 식품"): goods
# shops concede far in haggling (rivals open at 0.74-0.80 of list on our dear SKUs and settle near 0.69), so goods rise
# slower and keep that concession; food must be bought within its use window, so food rises faster and closes
RAISE_GOODS = 0.8    # x the member's MAX_RAISE on goods
RAISE_FOOD = 1.2     # x the member's MAX_RAISE on food
SCOUTS = 3           # buyer team: shops asked for a first quote at once before one negotiation starts
SCOUT_WAIT = 10      # seconds the scouts wait for quotes; a slower shop is tried after the quoted ones (11/10: at 6 s,
                     # 2,732 slow drops; 1,715 of 2,210 scouted orders lost a shop and 230 got no quote at all)
# The meta-harness tunes these numbers from how our buyer did (meta_r3.py BUYER_BOUNDS, the user on 11/4: the
# harness should make such fixes, not a person); each run reads its plan, the defaults above when it has none.
# shop/SKU pairs that just failed us (the meta-harness's code idea, 11/4: 422 of ~680 orders without a deal ended in
# "none in stock", the same shops dropping the same SKU again and again, and 15 of 100 payments were refused for a
# unit with too little shelf life). A pair that dropped us waits until the next supply window; one that shipped too
# short a unit waits an hour for orders asking more hours than it shipped. Waiting pairs move to the back, not out.
AVOID = MD / "avoid_r3.json"
AVOID_SECS = 900
SHORT_SECS = 3600
# A pair with 2+ earlier quick rejects in memory waits an hour, not 900 s (11/4: 252 of 1,423 trades were rejects with no
# price; Gc01583a5 at 25512088 had 5 at differing budgets and food_hours, so the 900 s wait ran out between orders).
DEAD_SECS = 3600
# A talk that ended with every ask above our limit (11/4 since 15:00: 14 of 28 talks were no-deals, 8 of them three pairs
# retried across orders with budgets at or below list, e.g. 25620027 G1d19ebee four times at limits of 206100 or less
# against a 229000 list) leaves that lowest ask as the pair's floor for an hour: the next order expects at least that.
FLOOR_SECS = 3600
SHELF_LEFT = re.compile(r"([\d.]+) h of shelf life left")
# Skip deals that lower the board's saved term (the mean of (list - price) / list over deals + 100, with -1 for a deal
# whose goods broke uncovered): while another shop is still to be tried, pay at most (1 - t) x list, t = min(SKIP_BELOW,
# our board saved mean, logs/board_terms_r3.jsonl, else SAVED_GUESS). Since 11/12 16:0x the last talk too. 0 = off.
# Off by default (11/10, last 6 h): 56 of 364 paid deals (15%) saved 0.25-0.30 of list (mean 0.275, none under 0.25
# with CAP 0.75), only 0.025 under the board mean 0.2997 with 2 of 718 paid deals broken uncovered in 12 h; over
# 11,738 deals they lower saved by ~1e-4 per 6 h, less than the deals they bring (0.1 weight), and a CAP 0.70 trial
# was not significant. The meta-harness can set it in buyer_plan_r3.json.
SKIP_BELOW = 1.0     # 11/11 13:4x on: t = our board saved mean (see below)
SAVED_GUESS = 0.30   # our board saved mean when no board row is at hand (0.2996 on 11/10 20:00; old scale)
COST_GUESS = 0.60    # a unit's cost as a share of list, unknown to the buyer (patch 3.6 saved; see deal_limit)
# food units cost 0.496 of list on average (leak audit #22), but 0.50 here cut food deals 58 -> 31 an hour (close 73% ->
# 62%) while the market's food volume held (-7%): food A costs ~0.6+ and its good deals (0.68 of list scores ~0.84) were
# lost with the bad ones, and the deals above the board mean that still count for saved fell. Back to the goods value
# (11/13 08:0x); a grade-aware guess from the quoted grade is the better fix.
COST_GUESS_FOOD = 0.60
# Once the quote states a grade, the guess follows it (11/13: saved is the largest board loss, 0.2 points; deals at 0.65-0.75 of
# list average 0.673, 45 of 79 under 0.70, buyers rank 33rd of 46; our own supply costs, restock_r3 GRADE_COST: A ~0.65, B
# ~0.48-0.50, C ~0.38), so cheap B/C units stop being bought at prices that lower saved while good A keeps its limit.
COST_GUESS_GRADE = {"A": 0.65, "B": 0.50, "C": 0.35}
BOARD_TERMS = HERE / "logs" / "board_terms_r3.jsonl"
PLAN = Path(os.environ.get("BUYER_PLAN") or Path(__file__).resolve().parent / "buyer_plan_r3.json")
PLAN_KEYS = ("CAP", "FOOD_MARGIN", "C_CAP", "C_WARRANTY", "MAX_TRIES", "MAX_RAISE", "FIRM_GAP", "SCOUTS", "CLOSE_GAP",
             "SKIP_BELOW", "RAISE_GOODS", "RAISE_FOOD")


def set_number(k, v):
    if k in ("FIRM_GAP", "CLOSE_GAP"):
        setattr(buyer, k, v)
    else:
        globals()[k] = int(v) if k in ("MAX_TRIES", "C_WARRANTY", "SCOUTS") else v


def load_plan(order_id=None):
    """The meta-harness's numbers; while one of them is on trial (buyer_ab_r3.json, started by meta_r3), half the
    orders, by the same hash as a code trial (code_ab_r3.arm), run its new value B, so it is judged over the same hours
    (11/4: CAP went 0.85 -> 0.80 on thin evidence while 29 of 538 deals had been paid at 0.80-0.85 of list)."""
    try:
        plan = json.loads(PLAN.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    for k, v in (plan if isinstance(plan, dict) else {}).items():
        if k in PLAN_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool):
            set_number(k, v)
    trial = load(PLAN.with_name("buyer_ab_r3.json"), {})
    if order_id and isinstance(trial, dict) and trial.get("param") in PLAN_KEYS and \
            isinstance(trial.get("B"), (int, float)) and code_ab_r3.arm(trial.get("key"), order_id) == "B":
        set_number(trial["param"], trial["B"])
    return plan


def board_saved(path=None):
    """Our buyer saved term from the last row of the board history (board_terms_r3.py), or SAVED_GUESS."""
    try:
        with open(path or BOARD_TERMS, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            row = json.loads(f.read().decode("utf-8", "replace").strip().splitlines()[-1])
        v = row.get("saved")
        return float(v) if isinstance(v, (int, float)) and 0 < v < 1 else SAVED_GUESS
    except (OSError, ValueError, IndexError, AttributeError):
        return SAVED_GUESS


def deal_limit(order, lp, more=False, grade=None):
    """The most we pay for this SKU: min(budget, CAP x list), and with SKIP_BELOW on, no more than leaves
    min(SKIP_BELOW, board saved) of list, on the last talk of an order too (11/12 leak audit #17: the last talk kept
    CAP 0.75; in 24 h 69 deals paid over 0.687 of list at a mean surplus 0.282 while saved is the mean over deals only,
    so each lowered it; saved ranks are about 0.001 apart (one rank ~0.008 buyer ALL) and deals weigh 0.1: ~+0.002
    saved for ~365 fewer deals by 11/17). `more` is kept for callers; SKIP_BELOW 0 turns the limit off."""
    limit = min(order["budget"], int(lp * CAP))
    if SKIP_BELOW > 0:
        t = min(SKIP_BELOW, board_saved())
        # patch 3.6 (11/12 17:4x): a deal now scores (list - price) / (list - unit cost), at most 1, and the board's saved
        # jumped 0.31 -> 0.69. Read as before, 0.69 left a limit of 0.31 of list and the buyer would have stopped. A
        # board value of 0.5 or more is on the new scale: the most to pay without lowering it is list - t x (list - cost),
        # with the unit's cost unknown to the buyer and taken as COST_GUESS of list (A 0.55-0.75, B 0.40-0.60: agent.md)
        cg = COST_GUESS_FOOD if order.get("kind") == "food" else COST_GUESS
        cg = COST_GUESS_GRADE.get(grade, cg)      # a grade the quote states, else the flat guess
        frac = (1 - t * (1 - cg)) if t >= 0.5 else (1 - t)
        limit = min(limit, int(lp * frac))
    return limit


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def avoid_load():
    now = time.time()
    a = load(AVOID, {})
    return {k: v for k, v in (a if isinstance(a, dict) else {}).items()
            if isinstance(v, dict) and v.get("until", 0) > now}


def avoid_add(shop, sku, why, secs, hours=None, floor=None):
    a = avoid_load()
    a[f"{shop}|{sku}" + ("|floor" if floor else "")] = {"until": time.time() + secs, "why": why,
                                                       **({"hours": hours} if hours is not None else {}),
                                                       **({"floor": floor} if floor else {})}
    try:
        tmp = AVOID.with_name(f"{AVOID.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(a), encoding="utf-8")
        os.replace(tmp, AVOID)
    except OSError:
        pass


def avoided(avoid, shop, sku, order):
    """Why this pair waits for this order, or None."""
    # a shop whose receipt came without the certificate the policy asks for waits, for every SKU, on orders that ask
    # for one (leak audit #40: one shop 0 paid of 20 in 22 h, 5 refusals 12:14-12:38, as its talks never say "no cert")
    if avoid.get(f"{shop}|*cert") and accept_policy(order).get("cert"):
        return "sent no certificate lately"
    v = avoid.get(f"{shop}|{sku}")
    if not v:
        return None
    if "hours" in v:
        need = accept_policy(order).get("min_hours_left")
        return f"shipped a unit with {v['hours']:.0f} h left lately" if need and need > v["hours"] else None
    return f"dropped us lately ({v.get('why')})"


def lines(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]
    except (OSError, ValueError):
        return []


def catalog():
    """sku -> {name, category, list_price, kind, shelf_hours}; the kit writes a dict, the market API a list."""
    c = load(MD / "catalog.json", {})
    return {x["sku"]: x for x in c} if isinstance(c, list) else c


# ---------------------------------------------------------------- learn from the kit's files

def sync_kit_files(memory, remember):
    """Payment refusals and broken goods the kit reported since we last looked, as raw memory lines.
    A broken item is matched to our latest settled purchase of that name before it broke, so we know
    which shop sold it and whether a warranty covered it."""
    seen_pay = {m.get("order") for m in memory if m.get("kind") == "payment"}
    for p in lines(MD / "payments.jsonl"):
        if p.get("order") in seen_pay:
            continue
        shop = next((m["shop"] for m in reversed(memory) if m.get("order") == p.get("order") and m.get("shop")), p.get("shop"))
        entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": "payment", "round": 3, "order": p.get("order"),
                 "shop": shop, "sku": p.get("item"), "price": p.get("price"), "paid": p.get("paid"),
                 "ledger": p.get("ledger"), "reason": p.get("reason")}
        remember(entry)
        memory.append(entry)
        h = SHELF_LEFT.search(p.get("reason") or "")
        if h and not p.get("paid") and shop and p.get("item"):
            avoid_add(shop, p["item"], "short shelf life", SHORT_SECS, hours=float(h.group(1)))
        # a grade the policy does not pay for (a B/C with no 6 h warranty): the same shop sent the same SKU as grade C
        # three times in an hour on 11/9 20:1x, each a full talk for a receipt we would not pay
        g = re.search(r"grade ([ABC]), the policy accepts", p.get("reason") or "")
        if g and not p.get("paid") and shop and p.get("item"):
            avoid_add(shop, p["item"], f"sent grade {g.group(1)} without a warranty", GRADE_SECS)
        # a unit the shop cannot hand over (checkout 409 "not in stock"; leak audit #21, 11/13: 16 of 61 unpaid in 14 h,
        # one shop's unit refused 9 times): that pair waits an hour like a short shelf life
        if not p.get("paid") and "not in stock" in (p.get("reason") or "") and shop and p.get("item"):
            avoid_add(shop, p["item"], "unit not in stock at checkout", SHORT_SECS)
        # a receipt without the certificate the policy asks for: the shop, not the SKU, waits (leak audit #40)
        if not p.get("paid") and "no certificate" in (p.get("reason") or "") and shop:
            avoid_add(shop, "*cert", "sent no certificate", GRADE_SECS)
        # a receipt without the warranty the policy asks for (audit #22: 8 of 10 such refusals in 14 h were one pair)
        if not p.get("paid") and re.search(r"warranty \d+ h, the policy asks", p.get("reason") or "") and shop and p.get("item"):
            avoid_add(shop, p["item"], "sent no warranty", GRADE_SECS)
    seen_broken = {(m.get("name"), m.get("broke_at")) for m in memory if m.get("kind") == "broken"}
    for b in lines(MD / "broken.jsonl"):
        key = (b.get("name"), b.get("broke_at"))
        if key in seen_broken:
            continue
        bought = [m for m in memory if m.get("kind") == "settled" and m.get("name") == b.get("name")
                  and (m.get("settled_at") or 0) <= (b.get("broke_at") or 0)]
        src = bought[-1] if bought else {}
        # the same name bought from two shops within the 6 h goods can break in: we cannot tell which one broke
        recent = {m.get("shop") for m in bought if (b.get("broke_at") or 0) - (m.get("settled_at") or 0) <= 6 * 3600}
        ambiguous = len(recent) > 1
        hours = ((b.get("broke_at") or 0) - (src.get("settled_at") or 0)) / 3600 if src else None
        entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": "broken", "round": 3, "name": b.get("name"),
                 "broke_at": b.get("broke_at"), "shop": src.get("shop"), "order": src.get("order"),
                 "price": src.get("price"), "warranty_hours": src.get("warranty_hours"),
                 "covered": bool(src.get("warranty_hours")) and hours is not None and hours <= src["warranty_hours"],
                 "ambiguous": ambiguous}
        remember(entry)
        memory.append(entry)


# ---------------------------------------------------------------- derived on read

def shop_record(name, memory, soft_breaks=False):
    """What our own Round 3 evidence says about a shop: (note, banned, price share of list or None)."""
    mine = [m for m in memory if m.get("round") == 3 and m.get("shop") == name]
    if not mine:
        return "no Round 3 trades with us", False, None
    settled = [m for m in mine if m.get("kind") == "settled"]
    lies = [m for m in settled if m.get("claim_honest") is False]
    broke = [m for m in mine if m.get("kind") == "broken" and not m.get("covered")]
    refused = [m for m in mine if m.get("kind") == "payment" and not m.get("paid")]
    shares = [m["price"] / m["list_price"] for m in settled if m.get("price") and m.get("list_price")]
    notes = [f"{len(settled)} settled with us"]
    if shares:
        notes.append(f"we paid {statistics.median(shares):.0%} of list (median)")
    if lies:
        notes.append(f"GOTCHA claimed a better grade than it shipped {len(lies)} times (ledger #{lies[-1].get('ledger')})")
    if broke:
        notes.append(f"GOTCHA {len(broke)} goods broke without warranty cover")
    if refused:
        notes.append(f"the kit refused to pay {len(refused)} times, latest: {harness.clip(refused[-1].get('reason'), 120)}")
    # transitions (LongMemEval-V2): how this shop moved in our last talks, as shares of list price, newest last, so
    # the next opening offer starts near where it actually gave in rather than where it opened
    talks = [m for m in mine if m.get("kind") == "trade" and m.get("list_price") and m.get("asks")][-5:]
    moves = []
    for m in talks:
        asks = [a for a in m["asks"] if isinstance(a, (int, float))]
        if asks:
            end = f"paid {m['price'] / m['list_price']:.0%}" if m.get("price") else "no deal"
            moves.append(f"{asks[0] / m['list_price']:.0%}->{min(asks) / m['list_price']:.0%} {end}")
    if moves:
        notes.append("its asks in our last talks (first->lowest, of list): " + ", ".join(moves))
    banned = len(lies) >= 2 or (len(lies) >= 1 and len(settled) <= 3) or (len(broke) >= 2 and not soft_breaks)
    return "; ".join(notes), banned, statistics.median(shares[-5:]) if shares else None


CLAIMS_CACHE = MD / "ledger_claims_r3.json"   # not claims_r3.json (claims_r3.py's forum claims)
CLAIMS_SECS = 120    # ledger_claims is reused this long: it read, filtered and rewrote the 64 MB six-hour ledger cache on
                     # every order (1.4 s with no network, 11/10), for numbers over six hours that barely move in two minutes


def cached_claims(cat=None, now=None):
    """ledger_claims(cat), from CLAIMS_CACHE when it is under CLAIMS_SECS old."""
    now = now or time.time()
    c = load(CLAIMS_CACHE, {})
    if isinstance(c, dict) and isinstance(c.get("claims"), dict) and 0 <= now - c.get("t", 0) < CLAIMS_SECS:
        return {k: tuple(v) for k, v in c["claims"].items()}
    claims = ledger_claims(cat)
    if claims:
        try:
            tmp = CLAIMS_CACHE.with_name(f"{CLAIMS_CACHE.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"t": now, "claims": claims}), encoding="utf-8")
            os.replace(tmp, CLAIMS_CACHE)
        except OSError:
            pass
    return claims


def ledger_claims(cat=None):
    """Per shop name: (settles, dishonest claims, median price as a share of list or None) from the market's ledger,
    the last six hours. The share is what the shop settled for with any buyer: a shop that sells cheap to others is
    worth trying first even before we have paid it ourselves."""
    try:
        entries = market_intel.load_recent(timeout=5.0)
    except Exception:
        return {}
    names = {e["data"]["url"].rstrip("/"): e["data"]["name"] for e in entries
             if e["kind"] == "join" and e["data"].get("role") == "shop" and e["data"].get("url")}
    out, shares = {}, {}
    for e in entries:
        if e["kind"] != "settle":
            continue
        d = e["data"]
        shop = str(d.get("shop") or d.get("payee") or "").rstrip("/")
        name = names.get(shop, shop.rsplit("/shop/", 1)[-1])
        n, bad = out.get(name, (0, 0))
        out[name] = (n + 1, bad + (d.get("claim_honest") is False))
        lp = ((cat or {}).get(d.get("item")) or {}).get("list_price")
        if lp and isinstance(d.get("amount"), (int, float)):
            shares.setdefault(name, []).append(d["amount"] / lp)
    return {k: (n, bad, statistics.median(shares[k][-10:]) if len(shares.get(k, [])) >= 3 else None)
            for k, (n, bad) in out.items()}


FAVOR = HERE / "favor_r3.json"   # shops clearing stock cheap: {"shops": [{"shop", "keywords", "share", "until", "why"}]}
FAVOR_BONUS = 1.5


def favored(shop, name, now=None):
    """The price share a shop is clearing this item at (favor_r3.json: the user's or the supervisor's tip), or None.
    11/10 10:4x: dragonash cleared bedding (pads, microfiber quilts, sofa pads, hotel bedding) near cost, 1,023 units of
    one pad set, at 0.64 of list against the market's 0.69: a clearance tip has to reach the buyer's ranking."""
    now = now or time.time()
    try:
        rows = json.loads(FAVOR.read_text(encoding="utf-8")).get("shops") or []
    except (OSError, ValueError, AttributeError):
        return None
    for f in rows:
        if isinstance(f, dict) and f.get("shop") == shop and now < f.get("until", 0) and \
                any(k in (name or "") for k in f.get("keywords") or []):
            return f.get("share")
    return None


def retry_until(order):
    # seconds into the order after which no new shop is tried: 26% of orders try 4+ shops and take a median 46-64 s
    # (576 orders in 6 h against ~1,000 for the top buyers); a talk that fails after 60 s rarely closes on a late retry
    return 60 if code_ab_r3.on("cb28e41d75a7", order.get("order")) else RETRY_UNTIL


def candidates(order, shops, cat, memory, claims):
    """(expected surplus, shop, offer, list price, note) for every listed offer that fits the order."""
    acceptable = set(order.get("acceptable") or [])
    need = order.get("use_within_hours")
    out, skipped = [], []
    avoid = avoid_load()
    # our Round 3 lines per shop, read once: each shop and each offer scanned all of memory (79k lines on 11/10, 1.6 s
    # of the 4.4 s between the kit starting the buyer and its first candidate line)
    by_shop = {}
    for m in memory:
        if m.get("round") == 3 and m.get("shop"):
            by_shop.setdefault(m["shop"], []).append(m)
    for s in shops:
        soft = code_ab_r3.on("a9b2ad097b35", order.get("order"))
        mine = by_shop.get(s["name"], [])
        note, banned, share = shop_record(s["name"], mine, soft_breaks=soft)
        n, bad, market_share = (tuple(claims.get(s["name"], ())) + (0, 0, None))[:3]
        if n >= 5 and bad / n >= 0.3:
            banned = True
            note += f"; market ledger: {bad} of its last {n} settled grade claims were false"
        for off in s.get("offers") or []:
            sku = off.get("sku")
            info = cat.get(sku) or {}
            lp = info.get("list_price")
            if sku not in acceptable or not lp or not off.get("qty"):
                continue
            if banned:
                skipped.append(s["name"])
                continue
            # what we expect to pay there, and whether our limit reaches it: an expensive SKU whose shop floor sits
            # above our limit only looks like a big surplus (review, 11/2), so the chance of a deal weighs it
            expect = lp * (share or market_share or PRICE_GUESS)
            fav = favored(s["name"], info.get("name"))
            if fav:
                expect = min(expect, lp * fav)
            expect = max(expect, (avoid.get(f"{s['name']}|{sku}|floor") or {}).get("floor", 0))
            limit = min(order["budget"], int(lp * CAP))
            p_deal = 1.0 if limit >= expect else (limit / expect) ** 4
            surplus = (lp - min(expect, limit)) * p_deal
            # the factors below are risks a shop's quote does not answer (whether the kit pays, breaks, shelf life it
            # does not state), so the scouts weigh quotes by them too (team(): 11/10, elm-street was quoted first and
            # agreed 21 times in 29 h with 0 of 21 receipts paid, while this ranking had it at (1/22)^2)
            risk = 1.0
            if code_ab_r3.on("6796c7e893b6", order.get("order")):
                # 88 of 826 payments were refused (25620027 22 of 48, jiseokkk 21 of 79): an agreed deal the kit refuses
                # is an order with no sale, so a shop that gets paid ranks above one that is only quoted lower
                pays = [m for m in mine if m.get("kind") == "payment"]
                # 12-13 of 100 agreed payments are refused for grade C or too little shelf life, all six seen from one
                # shop: with the second trial on, act from 3 payments, on the last 20 only, and square the factor
                second = code_ab_r3.on("0eb2c0ca6505", order.get("order"))
                if second:
                    pays = pays[-20:]
                if len(pays) >= (3 if second else 5):
                    paid = sum(1 for m in pays if m.get("paid"))
                    risk *= ((paid + 1) / (len(pays) + 1)) ** (2 if second else 1)
            if soft:
                # 219 goods broke, 20 in the last 12 h, and a break counts -1 on saved against ~0.29 a deal: instead of a
                # ban at 2 uncovered breaks, weight the shop by its break rate over our last 30 buys from it
                kept = [m for m in mine if m.get("kind") == "settled"][-30:]
                broke = [m for m in mine if m.get("kind") == "broken" and not m.get("covered")][-30:]
                if kept:
                    risk *= max(0.1, 1 - 3.5 * len(broke) / len(kept))
            short =order.get("kind") == "food" and need and info.get("shelf_hours") and \
                info["shelf_hours"] * B_SHARE < need * FOOD_MARGIN
            if short:
                # even a grade-B unit may not last: a last resort. Shelf-life refusals were 415 of the last 1,000 unpaid
                # talks (119 within 2% of the hours needed) and 0.3 still picked them first, so 0.05 ranks them behind
                risk *= 0.05 if code_ab_r3.on("a9d6314e01de", order.get("order")) else 0.3
            surplus *= risk
            wait = avoided(avoid, s["name"], sku, order)
            if wait and "shipped" in wait:
                # the same pair shipped too short a unit again and again within the hour (11/4 15:47-16:18:
                # 25620027 G476cc094 three times, 355-431 h against 594 asked): the kit would refuse it again
                skipped.append(s["name"])
                continue
            if wait:
                surplus *= 0.05                         # tried after every other pair, not dropped
            if fav:
                surplus *= FAVOR_BONUS                  # in the scouted set: a clearance is checked, not guessed
                note += f"; clearing this at ~{fav:.0%} of list (tip)"
            out.append((surplus, s, {**off, "_risk": risk}, lp, note + ("; short shelf life for this order" if short else "")
                        + (f"; {wait}" if wait else "")))
    if skipped:
        log("GUARD", f"not offering shops caught lying about grades, selling goods that broke or shipping too short a unit lately: {sorted(set(skipped))}")
    if code_ab_r3.on("0ceb91bf4389", order.get("order")):
        # the board's buyer saved term is the mean of (list - price) / list per deal, so rank by the saved rate, still
        # weighted by the chance of a deal: surplus~205579 for a 600k SKU against ~529 for a 30.9k SKU ranked the dear
        # one first, and the 100-300k band saved 0.260 against 0.326 under 30k (12 h counts)
        return sorted(out, key=lambda c: -c[0] / c[3])
    return sorted(out, key=lambda c: -c[0])


def accept_policy(order, receipt=None, lp=None):
    """What the kit checks before paying (agent.md: cert, min_hours_left, grades, warranty_hours). Goods: A or B, and
    C when the receipt carries a warranty of C_WARRANTY hours (or, off trial, its price is at most C_CAP of list)."""
    if order.get("kind") == "food":
        return {"cert": True, "min_hours_left": round((order.get("use_within_hours") or 0) * FOOD_MARGIN, 1)}
    r = receipt if isinstance(receipt, dict) else {}
    price = buyer.as_int(r.get("price"))
    # 11/7: C broke 15.7% of units (A 1.5%); a C at 0.65 of list is worth ~0.14 per deal, half the 0.279 mean, so only a warranty
    # 11/9 audit: B goods break 9-18% within 6 h and a break without a warranty counts -1 on saved (a B at 0.6 of
    # list: 0.87 x 0.4 - 0.13 = 0.22 a deal against our mean 0.29; with a warranty the break is 0, so 0.35). 35 of
    # our 198 broken buys were not covered. A without a warranty breaks ~2%: still worth it. So B and C need the
    # 6 h break window covered; the C_CAP branch never ran (code_ab_r3.on is True with no trial) and is gone
    warrantied = (r.get("warranty_hours") or 0) >= BREAK_HOURS
    # leak audit #17: 34 unwarranted grade A deals in 24 h saved 0.271 against 0.324 warranted (board 0.313); our A broke
    # 4.8% against 1.5% for the market and a break counts -1, so a dear A (above 0.68 of list) needs the 6 h warranty too
    dear = lp and price and price > 0.68 * lp and code_ab_r3.on("7cc785db92f2", order.get("order"))
    # with "warranty_hours" the kit checks the CHECKOUT (the seller's copy), not our copy of the receipt (review)
    if dear and not warrantied:
        return {"cert": True, "grades": ["A"], "warranty_hours": BREAK_HOURS}
    return {"cert": True, "grades": GOOD_GRADES + ["C"], "warranty_hours": BREAK_HOURS} if warrantied else \
        {"cert": True, "grades": ["A"]}


# the shop's own words for the unit's grade: "grade C", "Grade now A", "grade=C", "condition: B", "등급 B", and
# yeonsong2's "(G1324bb95): C (near its date)" (11/10: 15 grade refusals of 23 talks at yeonsong2 said it that way)
GRADE_SAID = re.compile(r"\b(?:grade|condition)\s*(?:now\s+|is\s+)?[:=]?\s*([ABC])\b|등급\s*:?\s*([ABC])\b"
                        r"|\)\s*:\s*([ABC])\s*\(", re.I)
WARRANTY_SAID = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h|hours?|시간)\s*(?:of\s+)?(?:warranty|보증)", re.I)
# any mention of a warranty, and a stated lack of one (11/9 review: "6-hour warranty", "three-day warranty" were
# missed by WARRANTY_SAID, 43 of 68 mentions, and talks with shops that did warrant were ended). 11/10: ddolcom's
# "sold as is, without a break warranty" and "No breakage warranty offered" passed as a mention of a warranty, and 44
# grade-B/C receipts in 29 h went unpaid; "warranty 0 hours", "warranty_hours=0" are a lack of one too
WARRANTY_ANY = re.compile(r"warrant|보증|refund", re.I)
WARRANTY_NONE = re.compile(r"\b(?:no|without)\s+(?:\w+\s+){0,2}warrant|warranty:\s*none|보증\s*(?:없|불가)"
                           r"|warranty(?:[_ ]hours)?\s*[:=]?\s*0+(?:\.0+)?(?!\.?\d)|(?<![\d.])0+(?:\.0+)?\s*(?:h|hours?)\s*(?:of\s+)?warranty",
                           re.I)
HOURS_SAID = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h|hours?|시간)\s*(?:of\s+)?(?:shelf[ -]life|left|남)", re.I)
# the number after the words: "Shelf life left 670.50 hours", "shelf_life_left=310.5"; and an expiry time:
# kcwarni's "Expires at 2026-12-02T13:56:46+00:00" (536 h; 621 asked, unpaid 11/10 14:16)
HOURS_AFTER = re.compile(r"shelf[ _-]?life[ _]left\s*[:=]?\s*(?:about\s+)?(\d+(?:\.\d+)?)", re.I)
EXPIRES_SAID = re.compile(r"expir(?:es|y)\s*(?:at|on)?\s*:?\s*(\d{4}-\d\d-\d\d[T ]\d\d:\d\d(?::\d\d(?:\.\d+)?)?(?:Z|[+-]\d\d:?\d\d))", re.I)
# a stated lack of the market's certificate (the kit pays only with one: elm-street, 21 receipts unpaid in 29 h)
CERT_NONE = re.compile(r"\bno\s+(?:market(?:'s)?\s+)?certificate|without\s+(?:a\s+|the\s+)?(?:market(?:'s)?\s+)?certificate"
                       r"|\bcert\s*[:=]\s*(?:false|none|no)\b|certificate\s*:\s*(?:none|no)\b|인증서?\s*(?:없|미)", re.I)
# the kit grades food by the share of its shelf life left (market_kit.FOOD_GRADE_CUTS): A at 0.8 or more, B at 0.45
# or more, C below. A shop that says B or C has told us the most hours it can have: under 0.8 or 0.45 x shelf_hours
FOOD_UNDER = {"B": 0.8, "C": 0.45}


def said_grade(m):
    return next((x for x in m.groups() if x), "").upper() or None


def hours_said(text, now=None):
    """Hours of shelf life left the shop states, or None."""
    for rx in (HOURS_SAID, HOURS_AFTER):
        h = rx.search(text)
        if h:
            return float(h.group(1))
    e = EXPIRES_SAID.search(text)
    if e:
        try:
            from datetime import datetime
            return (datetime.fromisoformat(e.group(1).replace("Z", "+00:00")).timestamp() - (now or time.time())) / 3600
        except ValueError:
            return None
    return None


def screen_for(order, lp=None, shelf_hours=None):
    """A check of the shop's own words against our payment policy (accept_policy): a grade, shelf life or missing
    certificate the shop states that the kit would refuse to pay for ends the talk before a deal. On 11/3 six receipts
    went unpaid this way (shelf life 138-338 h against 198-594 h asked, one grade C against A/B), each a result with no
    settle. `shelf_hours` (the catalog's) turns a stated food grade into the most hours the unit can have left."""
    pol = accept_policy(order)
    need = pol.get("min_hours_left")

    def graded(grade):
        # a stated B/C costs less than the flat guess: cap the talk at the per-grade limit (COST_GUESS_GRADE)
        if grade in ("B", "C") and lp and order.get("budget") and code_ab_r3.on("1109593c287d", order.get("order")):
            return {"cap": deal_limit(order, lp, grade=grade), "why": f"grade {grade} costs less than the flat guess"}
        return None

    def screen(text):
        text = text or ""
        if pol.get("cert") and CERT_NONE.search(text):
            return "the shop says the unit has no market certificate, we pay only with one"
        g = GRADE_SAID.search(text)
        grade = said_grade(g) if g else None
        if grade in ("B", "C") and pol.get("grades"):
            # a shop that also offers a grade A unit, or mentions a warranty at all, goes on: the checkout decides
            # (accept_policy asks the seller's copy for a 6 h warranty); only a B/C with no word of one, or a stated
            # lack of one, ends the talk
            if any(said_grade(m) == "A" for m in GRADE_SAID.finditer(text)):
                return None
            # a stated warranty shorter than the break window ends it now, not at checkout (dragonash: "3 h warranty")
            w = WARRANTY_SAID.search(text)
            if w and float(w.group(1)) < BREAK_HOURS:
                return f"the shop says grade {grade} with a {float(w.group(1)):g} h warranty, we pay for B/C only with {BREAK_HOURS} h"
            if WARRANTY_ANY.search(text) and not WARRANTY_NONE.search(text):
                return graded(grade)
            return f"the shop says grade {grade} with no {BREAK_HOURS} h warranty, we pay only for A or a warranted B/C"
        if grade and pol.get("grades") and grade not in pol["grades"]:
            return f"the shop says grade {grade}, we pay only for {'/'.join(pol['grades'])}"
        if need:
            h = hours_said(text)
            if h is not None and h < need:
                return f"the shop says {h:.0f} h of shelf life left, we need {need:.0f}"
            under = FOOD_UNDER.get(grade)
            if h is None and under and shelf_hours and under * shelf_hours < need:
                return (f"the shop says grade {grade}, under {under:.0%} of the {shelf_hours:g} h shelf life left, "
                        f"we need {need:.0f}")
        return graded(grade)
    return screen


def policy_miss(receipt, order, lp=None):
    """The receipt's own grade claim or warranty against accept_policy, for goods: the kit refuses such a checkout,
    and a refused receipt ended the order with no other shop tried (leak audit #51, 11/16: 30 of 370 receipts went
    unpaid, 12 of them stating on the receipt a C with no warranty or a 0-1 h warranty). Checked only when the
    receipt states the field, since the kit checks the seller's checkout copy, not ours. Audit #52 (11/17 00:4x): all 4
    unpaid goods receipts since were `cert: False` on the receipt, or a dear A whose receipt had no warranty_hours
    key: the kit signs a shop's terms into the receipt, so a missing key means no warranty at checkout either."""
    if not isinstance(receipt, dict):
        return None
    pol = accept_policy(order, receipt, lp)
    if receipt.get("cert") is False and pol.get("cert"):
        return "the receipt says the unit comes without the market's certificate"
    if order.get("kind") != "goods":
        return None
    claim = receipt.get("claim")
    if claim in ("A", "B", "C") and pol.get("grades") and claim not in pol["grades"]:
        return f"the receipt claims grade {claim}, we pay only for {'/'.join(pol['grades'])}"
    # a warranted C breaks 13.9% of the time against 1.2% for A, and a break scores -1: a C deal above 0.55 of list saves
    # ~0.2 against the 0.738 board mean, so the next shop is tried (the 0.68 bar for a dear A is the same idea)
    price = buyer.as_int(receipt.get("price"))
    if claim == "C" and lp and price and price > 0.55 * lp and code_ab_r3.on("f70a5768c42f", order.get("order")):
        return f"the receipt claims grade C at {price / lp:.0%} of list, its 13.9% break rate makes that deal worth less than the next shop"
    if pol.get("warranty_hours"):
        try:
            wh = float(receipt.get("warranty_hours") or 0)
        except (TypeError, ValueError):
            wh = 0.0
        if wh < pol["warranty_hours"]:
            return f"the receipt's warranty is {wh:g} h, we need {pol['warranty_hours']}"
    return None


def check_receipt(receipt, order, agreed, task_id, lp=None):
    if not isinstance(receipt, dict):
        return "no receipt"
    price = buyer.as_int(receipt.get("price"))
    if receipt.get("item") not in set(order.get("acceptable") or []) | {order.get("item")}:
        return f"receipt names {receipt.get('item')!r}, not a SKU of this order"
    if price is None or (agreed is not None and price > agreed):   # a lower price than our offer is fine
        return f"agreed {agreed}, receipt says {receipt.get('price')}"
    if price > order["budget"]:
        return f"receipt price {price} above budget {order['budget']}"
    if receipt.get("task") != task_id:
        return f"receipt task {receipt.get('task')!r} is not this negotiation"
    return policy_miss(receipt, order, lp)


def review(result, name, lp, problem):
    """Stars before payment; the review hook replaces them with what actually arrived."""
    asks = [a for a in result.get("asks", []) if a is not None]
    offers = result.get("offers", [])
    if result.get("state") == "TASK_STATE_COMPLETED" and problem:
        return 1, f"Bad receipt: {problem}."
    if result.get("state") == "error":
        return 1, f"No deal: the shop could not be traded with ({result.get('error', 'error')})."
    if result.get("state") == "TASK_STATE_COMPLETED":
        # a deal with no offer of ours (the shop's first ask taken): 11/10 02:16 offers[-1] raised IndexError and the
        # agreed deal was lost before payment
        price = offers[-1] if offers else (asks[-1] if asks else None)
        if price is None or not lp:
            return 4, f"{name}: agreed."
        return 4, f"{name}: agreed {price} ({price / lp:.0%} of list {lp}) after {len(offers)} offers."
    if not asks:
        return 2, f"No deal on {name}: the shop named no price."
    return 3, f"No deal on {name}: lowest ask {min(asks)} ({min(asks) / lp:.0%} of list {lp}), above what we pay."


async def main(args, shops, memory, remember):
    order = load(args.order, {})
    load_plan(order.get("order"))
    cat = catalog()
    demand_r3.record_order(order, cat, shops)            # a sample of the hidden demand (demand_r3.py)
    sync_kit_files(memory, remember)
    # an order we already paid for can come back when the kit's result post timed out (11/4 11:47: paid, the post
    # timed out, the same order came again, a second shop agreed, the market refused the second payment): no new talk
    try:
        paid = any(json.loads(l).get("order") == order.get("order") and json.loads(l).get("paid")
                   for l in (MD / "payments.jsonl").read_text(encoding="utf-8").splitlines()[-500:])
    except (OSError, ValueError):
        paid = False
    if paid:
        log("PICK", f"order {order.get('order')} is already paid: no new talk")
        print(json.dumps({"shop": "", "receipt": None, "stars": None, "review": None}), flush=True)
        return
    cands = candidates(order, shops, cat, memory, cached_claims(cat))
    if not cands:
        log("PICK", f"no listed shop has a SKU of {order.get('item')} we can buy")
        print(json.dumps({"shop": "", "receipt": None, "stars": None, "review": None}), flush=True)
        return
    for c in cands[:5]:
        log("CAND", f"{c[1]['name']} {c[2]['sku']} {c[2].get('name', '')[:30]} list {c[3]} surplus~{c[0]:.0f}: {c[4]}")
    cands = await team(order, cat, cands)
    # try the best candidate, then up to two more at other shops while the kit's 300 s per order allows
    # a shop that answers "none in stock" or a broken reply at once costs no time and does not use up a try
    # (11/3 21:06-21:33: 30 of 69 talks ended that way, while three tries ended many orders)
    started, tried, talks, out = time.time(), set(), 0, None
    skus, quick_shops = {}, set()
    for i, (surplus, s, off, lp, note) in enumerate(cands):
        # a shop that said "none in stock" for one SKU may still have another the order accepts: up to two SKUs a
        # shop, the second only after a quick reject (the meta-harness's code idea, 11/4: 714 of ~1,000 talks ended so)
        if (s["name"], off["sku"]) in tried or skus.get(s["name"], 0) >= (2 if s["name"] in quick_shops else 1) \
                or talks >= MAX_TRIES or (tried and time.time() - started > retry_until(order)):
            continue
        tried.add((s["name"], off["sku"]))
        skus[s["name"]] = skus.get(s["name"], 0) + 1
        more = talks + 1 < MAX_TRIES and any(c[1]["name"] != s["name"] for c in cands[i + 1:])   # another shop to try
        out = await attempt(order, cat, s, off, lp, note, remember, rejects_after_two(s["name"], memory), more=more)
        quick = out.pop("_quick", False)
        if out["receipt"]:
            break
        talks += not quick
        if quick:
            quick_shops.add(s["name"])
            avoid_add(s["name"], off["sku"], "quick reject",
                      DEAD_SECS if code_ab_r3.on("4583666882c6", order.get("order")) and dead_pair(s["name"], off["sku"], memory)
                      else AVOID_SECS)
    forum_r3.maybe_post(memory)
    try:                                   # the shop's first move: answer wanted posts with our stock (wanted_r3)
        import wanted_r3
        if wanted_r3.maybe_reply() is None:
            wanted_r3.maybe_ad()           # 11/11: slow stock advertised in busy categories, as the shop
    except Exception:
        pass
    print(json.dumps(out, ensure_ascii=False), flush=True)


def opening_for(order, name, sku):
    """The first message. It asks the shop to state the unit's grade and shelf life or warranty, which screen_for reads
    before any offer (11/10, 29 h: 106 of 174 unpaid receipts came from talks where the shop never stated them)."""
    opening = f"I want to buy {name} ({sku})"
    if order.get("kind") == "food" and order.get("use_within_hours"):
        return opening + (f". I need it to last {order['use_within_hours']} hours: please ship a unit with at least "
                          f"{accept_policy(order)['min_hours_left']} hours of shelf life left, with the market's certificate. "
                          "Please state the unit's grade and its hours of shelf life left.")
    return opening + (". Please ship a grade A unit with the market's certificate, or a grade B or C unit with at least "
                      f"a {BREAK_HOURS} h warranty (we pay for B or C only with one). Please state the unit's grade and "
                      "warranty hours.")


async def scout(order, cat, s, off, lp):
    """One scout of the buyer team: ask one shop for this SKU, read its first answer (no model), cancel the talk.
    {"state", "ask", "text"}; "error" when the shop cannot be reached."""
    name = harness.clip((cat.get(off["sku"]) or {}).get("name") or off.get("name") or off["sku"], 80)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            shop = buyer.Shop(client, s["url"])
            await shop.read_card()
            t = await shop.send(opening_for(order, name, off["sku"]))
            if t is None:
                return {"state": "error"}
            st, ask, text = buyer.state(t), buyer.asking_price(t), buyer.reply_text(t)
            if st == "TASK_STATE_INPUT_REQUIRED":
                await shop.cancel(t["id"])               # no unit held for us while the team decides
            return {"state": st, "ask": ask, "text": text}
    except Exception as e:
        return {"state": "error", "error": type(e).__name__}


async def team(order, cat, cands):
    """The buyer team (after dragonash's team-run agents, 11/4): SCOUTS scouts ask the best candidate shops for a quote
    at the same time, then the lead negotiates where the quote leaves the most surplus. Shops that answer "none in
    stock", fail to answer, or state a grade or shelf life our kit would not pay for are dropped before any talk; one
    negotiation at a time follows, so we never agree with two shops for one order."""
    if SCOUTS < 2 or len(cands) < 2:
        return cands
    # a second wave of scouts at other shops when every shop of the first wave dropped out (11/4: most orders without
    # a deal ended with the first shops out of stock while shops further down the list were never asked)
    picked, dropped, quoted = [], set(), []
    for wave in range(2):
        batch, seen = [], {c[1]["name"] for c in picked}
        for c in cands:
            if c[1]["name"] not in seen:
                batch.append(c)
                seen.add(c[1]["name"])
            if len(batch) >= SCOUTS:
                break
        if not batch:
            break
        if wave:
            log("SCOUT", f"no quote from the first wave: asking {len(batch)} more shops")
        picked += batch
        await scout_wave(order, cat, batch, dropped, quoted)
        # first ask 1.1-1.5x cap closed 19% of 206 talks, within-cap asks 87% of 371: when every quote of the first wave
        # is over cap, scout other shops so the over-cap drop can leave the 19% closers out
        if quoted and not (wave == 0 and all(q in _WAVE_OVER for q in quoted)
                           and code_ab_r3.on("37b6db373762", order.get("order"))):
            break
    lead = [c for _, c in sorted(quoted, key=lambda x: -x[0])]
    rest = [c for c in cands if c not in picked and (c[1]["name"], c[2]["sku"]) not in dropped]
    # a shop too slow for the scouts is still a shop: it goes last, not out (screened-out shops stay out)
    slow = [c for c in picked if c not in lead and (c[1]["name"], c[2]["sku"]) not in dropped]
    _WAVE_OVER.clear()
    return lead + rest + slow


_WAVE_OVER = []   # quotes asking over 1.1x their cap, over all waves of the team() run in progress


async def scout_wave(order, cat, picked, dropped, quoted):
    # wait at most SCOUT_WAIT seconds: the slowest shop set the pace (9.5 s median, one took 27 s)
    tasks = [asyncio.create_task(scout(order, cat, c[1], c[2], c[3])) for c in picked]
    done, _ = await asyncio.wait(tasks, timeout=SCOUT_WAIT)
    quotes = [t.result() if t in done else {"state": "slow"} for t in tasks]
    for t in tasks:
        if t not in done:
            t.cancel()
    over = []
    for c, q in zip(picked, quotes):
        shelf = (cat.get(c[2]["sku"]) or {}).get("shelf_hours")
        why = screen_for(order, c[3], shelf)(q.get("text") or "") if q["state"] == "TASK_STATE_INPUT_REQUIRED" else None
        if q["state"] == "slow":
            log("SCOUT", f"{c[1]['name']} {c[2]['sku']}: no quote in {SCOUT_WAIT} s, tried after the quoted shops")
            continue
        if q["state"] != "TASK_STATE_INPUT_REQUIRED" or isinstance(why, str):
            dropped.add((c[1]["name"], c[2]["sku"]))
            log("SCOUT", f"{c[1]['name']} {c[2]['sku']}: dropped ({why or q['state']})")
            avoid_add(c[1]["name"], c[2]["sku"], why or q["state"], AVOID_SECS)
            continue
        cap = why["cap"] if isinstance(why, dict) else min(order["budget"], int(c[3] * CAP))
        ask = q.get("ask")
        # what is left at the ask, weighed by the chance of getting there when the ask is above what we pay
        value = (c[3] - min(ask, cap)) * min(1.0, cap / ask) ** 4 if isinstance(ask, (int, float)) and ask > 0 else c[0]
        risk = c[2].get("_risk", 1.0) if isinstance(ask, (int, float)) and ask > 0 else 1.0   # c[0] has it already
        value *= risk
        quoted.append((value, c))
        if isinstance(ask, (int, float)) and ask > 1.1 * cap:
            _WAVE_OVER.append((value, c))
        log("SCOUT", f"{c[1]['name']} {c[2]['sku']}: asks {ask} of list {c[3]} (surplus at the ask ~{value:.0f}"
                     + (f", x{risk:.2f} for the kit refusing, breaks or shelf life" if risk < 1 else "") + ")")
    # first ask 1.1-1.5x cap closed 22% of 266 talks (85 min, median 13 s), repeated above-cap asks 0% of 192, within-cap
    # asks 91% of 329: with a shop asking within its cap, the over-cap shops get no retry (order cycle ~35 s vs 5-9 s)
    over = [q for q in quoted if q in _WAVE_OVER]    # the first wave's over-cap quotes count in the second wave
    if over and len(over) < len(quoted) and code_ab_r3.on("92c5ed5d93af", order.get("order")):
        for q in over:
            quoted.remove(q)
            dropped.add((q[1][1]["name"], q[1][2]["sku"]))
        log("SCOUT", f"{len(over)} shops asking over 1.1x their cap left out: {len(quoted)} asking within it")


def dead_pair(shop, sku, memory):
    """True when memory already holds at least 2 earlier quick rejects (rejected, no asks) of this shop/SKU pair."""
    return sum(m.get("kind") == "trade" and m.get("round") == 3 and m.get("shop") == shop and m.get("sku") == sku
               and m.get("outcome") == "no_deal" and m.get("end") == "TASK_STATE_REJECTED"
               and not [a for a in m.get("asks") or [] if a is not None] for m in memory) >= 2


def rejects_after_two(name, memory):
    """True when at least 3 of this shop's Round 3 talks with us ended in rejection after exactly two asks."""
    return sum(m.get("kind") == "trade" and m.get("round") == 3 and m.get("shop") == name
               and m.get("end") == "TASK_STATE_REJECTED"
               and len([a for a in m.get("asks") or [] if a is not None]) == 2 for m in memory) >= 3


async def attempt(order, cat, s, off, lp, note, remember, take_second=False, more=False):
    """One negotiation with one shop over one SKU; returns the result line for the kit. `more`: another shop is still
    to be tried for this order (deal_limit's SKIP_BELOW)."""
    # the catalog's name first: the name in a shop's offer is the shop's own text and goes into our prompt
    name, sku = harness.clip((cat.get(off["sku"]) or {}).get("name") or off.get("name") or off["sku"], 80), off["sku"]
    limit = deal_limit(order, lp, more)
    log("CHOOSE", f"{s['name']} {s['url']}: {name} ({sku}), list {lp}, paying at most {limit}")
    opening = opening_for(order, name, sku)
    try:                                                  # the buying team member this talk goes to (team_r3.py)
        member, style = team_r3.choose("buyer")
    except (OSError, ValueError, KeyError, TypeError):
        member, style = "", {}
    opening_style = {
        "low": "Open at about half of list or lower and raise slowly; losing a few talks for a better price is fine.",
        "median": "Open a little under what the market paid lately (its median, below) and close fast.",
    }.get(style.get("OPEN"), "Open below what the market paid lately (its lower quartile, below, is a fair opening; far "
                             "under it, shops walk away) and raise slowly.")
    firm_gap, buyer.FIRM_GAP = buyer.FIRM_GAP, style.get("FIRM_GAP", buyer.FIRM_GAP)
    hint = (f"The list price is {lp}. Shops buy stock at 25-75% of list price. {opening_style} "
            f"Our record with this shop: {note}.\n"
            f"What the market paid lately (public ledger): "
            f"{prices_r3.line(MD, sku, (cat.get(sku) or {}).get('category'))}.\n")
    close_gap = None
    # 11/4: of 78 talks on SKUs listed at 200k+ about 31 ended with no deal (line 1354: ask 1,777,577, our offer 1,769,104,
    # shop rejected, ~470k surplus lost); one dear deal is worth ~25 ordinary ones (17k surplus), so close 15% apart, not 5%
    if lp >= 200000 and code_ab_r3.on("a996d96f6081", order.get("order")):
        close_gap = 0.15
    # 11/6: the 300k+ band is 64% of buyer surplus (15.5M of 24.3M, 59 orders); the 423,810 mattress closed at 0.80 of list
    # because a counter within 12% of our 0.70 offer was taken at once, the market median is ~0.74, so close 6% apart there
    if lp >= 300000 and code_ab_r3.on("7f20fce51f03", order.get("order")):
        close_gap = 0.06
    # 11/13: an order takes ~28 s and ~4.6 model calls, ~470 orders per 6 h against ~1,000 for the leaders; plan mode is one
    # call, then code concedes (within-cap talks close 87% in a median 12 s), so half the orders run it, not only a PLAN style
    plan = bool(style.get("PLAN")) or code_ab_r3.on("7e4b208ce50d", order.get("order"))
    share = prices_r3.median_share(MD, sku, (cat.get(sku) or {}).get("category"))
    market = int(share * lp) if share and lp else None
    t0 = time.time()
    try:
        log("TEAM", f"{member or 'no team'} haggles this one {json.dumps(style)}")
        raise_k = RAISE_FOOD if order.get("kind") == "food" else RAISE_GOODS
        result = await buyer.shop(s["url"], name, limit, memory=hint,
                                  max_raise=min(0.9, style.get("MAX_RAISE", MAX_RAISE) * raise_k),
                                  max_offers=MAX_OFFERS, plan=plan, take_second=take_second, close_gap=close_gap,
                                  order_id=order.get("order"), opening=opening, market=market,
                                  screen=screen_for(order, lp, (cat.get(sku) or {}).get("shelf_hours")))
    except Exception as e:
        log("ERROR", f"trade with {s['name']} failed: {e!r}")
        result = {"state": "error", "asks": [], "offers": [], "error": type(e).__name__}

    buyer.FIRM_GAP = firm_gap
    secs = round(time.time() - t0, 1)
    receipt = result.get("receipt")
    deal = result.get("state") == "TASK_STATE_COMPLETED"
    agreed = result["offers"][-1] if deal and result.get("offers") else None
    problem = check_receipt(receipt, order, agreed, result.get("task"), lp) if deal else None
    if problem:
        log("GUARD", f"receipt problem: {problem}")
    stars, text = review(result, name, lp, problem)
    asked = [a for a in result.get("asks", []) if a is not None]
    if not deal and asked and min(asked) > limit:
        avoid_add(s["name"], sku, "no deal above our limit", FLOOR_SECS, floor=min(asked))
    remember({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": "trade", "round": 3, "order": order.get("order"),
              "shop": s["name"], "url": s["url"], "item": order.get("item"), "sku": sku, "name": name,
              "list_price": lp, "budget": order.get("budget"), "food_hours": order.get("use_within_hours"),
              "outcome": "bad_receipt" if problem else "deal" if deal else "no_deal", "end": result.get("state"),
              "price": agreed, "asks": result.get("asks", []), "offers": result.get("offers", []),
              "receipt": receipt, "problem": problem, "stars": stars, "screened": result.get("screened"),
              "member": member, "secs": secs})
    return {"shop": s["url"], "receipt": receipt if deal and not problem else None, "stars": stars,
            "review": text, "accept": accept_policy(order, receipt, lp),
            "_quick": not result.get("asks") and result.get("state") in ("TASK_STATE_REJECTED", "error")}
