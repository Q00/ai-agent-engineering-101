"""Round 3 restock hook (ddolcom: deep stock where the market's money is, then what my customers buy).

    python restock.py --state .market

The kit runs this every 60 s in Round 3 and buys what the last printed line asks for: {"buy": [{"sku", "qty"}, ...]}.
Buyers only see a shop for an order if it stocks that order's category, so the shop keeps one unit in as many
cheap categories as it can, refills what sold first, keeps two where it sold twice, and stops buying food that
rotted unsold. Memory: restock_memory.json in the agent folder (what we held, what sold, what expired).

Customers first (customers.py): buyers who settled with my shop are remembered from the public ledger, with
what they buy anywhere; their favourite categories are stocked before anything else, with more units.
Then what the whole market buys: categories by their share of settled deals on the ledger, more units for a
bigger share; once the market has MARKET_DATA deals, categories nobody bought are not stocked.
Money first: goods categories with VALUE_FOCUS of the market's sales value (deals x list price) are stocked by SKU,
not by category: the FOCUS_SKUS SKUs with the most value that the balance can carry, each to a target set by how
often it sells (focus_want). They come before all others, within FOCUS_SPEND of the spendable cash.
Categories with under MIN_VALUE of the value are not stocked unless the shop already sold there.
Cheap first: until the shop has CHEAP_PHASE sales, only categories and SKUs up to CHEAP_MAX_LIST, except hot ones.
Supply opens every 20 minutes for 3 minutes; outside a window the run only learns and buys nothing, because
the kit would wait 90 s per line and could miss the window itself.
"""
import argparse
import json
import time
from pathlib import Path

import customers

CASH_FLOOR = 40_000_000     # the user's choice (2026-11-09 15:40): "현금 4000만원만 남기고 가급적 사입"; was 1,000,000
# never spend below this (bankruptcy line 500,000, and room to refill what sells);
                            # was 1,500,000 until the shop stocked mattresses deep, which do not rot
MAX_LIST = 300_000          # skip categories whose median list price is above this: one unsold unit costs too much
MIN_SHELF_HOURS = 48        # food that lasts a day spoils before most buyers ask for it
COST_GUESS = 0.75           # a unit costs at most 75% of its list price (grade A); plan with the worst case
MAX_LINES = 10              # the kit buys at most 10 lines per run
CHEAP_PHASE = 20            # until the shop has sold this many units, stock cheap SKUs only
CHEAP_MAX_LIST = 50_000     # "cheap": list price up to this
CHEAP_SHARE = 0.6           # cheapest SKU at or above this share of the category median (orders accept 0.5-1.5x)
FOOD_MAX_QTY = 4            # food rots: never more than this per category
MARKET_DATA = 200           # market deals seen before "nobody bought it" means something
HOT_MAX_LIST = 700_000      # hot categories (e.g. mattresses) may go this dear; the SKU is the one the market buys
HOT_SHARE = 0.03            # a category with this share of market deals is hot: dear SKUs allowed in the cheap phase
POP_QTY = [(0.10, 5), (0.05, 4), (0.02, 3), (0.005, 2)]   # (share of market deals, units to keep)
DEAR = 100_000              # list price above this: at most DEAR_QTY units (outside the deep categories)
DEAR_QTY = 2
LINE_SHARE = 0.1            # one line spends at most this share of the balance (fewer units of dear SKUs)
# ddolcom: over the first 20 h of Round 3 mattresses were 44% of the money paid, sofas 31% (36% in the last 12 h),
# bedding 12%. Category counts kept us on one SKU: 5 unsold SS mattresses (G07ec17d5) stopped any purchase of the
# market's best seller G1515d951 (2,698 deals, about 73,000 margin a unit), which we never stocked
VALUE_FOCUS = 0.05          # a goods category with this share of the market's sales value is stocked by SKU and first
C_STOP = 3                   # a focus SKU with this many grade C goods in stock is not restocked (see focus_lines)
FOCUS_SKUS = 20              # SKUs kept per focus category, by value (deals x list price)
# Claude (2026-11-08 22:23, hourly check): 15 -> 20. Stock fell from 0.69 of book (17:23) to 0.50 with 116M in cash once grade C
# counted half; the 80% cap (user's instruction) no longer binds, so more SKUs is what lets the stock grow
FOCUS_QTY = [(0.02, 5), (0.005, 3)]     # (SKU's share of market deals, units to keep); below these, 2
FOCUS_DEAR = 500_000        # list price above this: at most FOCUS_DEAR_QTY units, however often it sells
FOCUS_DEAR_QTY = 5          # was 2 until 2026-11-06 20:23: stock 48.5% of book against the user's 60%, the best seller G24f5f979 sold out
UNIT_SHARE = 0.25           # one unit's cost at most this share of the balance: dearer sofas come as the balance grows
FOCUS_LINE_SHARE = 0.25     # one focus line spends at most this share of the balance
FOCUS_SPEND = 0.6           # focus lines spend at most this share of the spendable cash per run: food still refills
FOCUS_MAX_LINES = 8         # focus SKUs take at most this many of a run's MAX_LINES lines; 6 -> 8 (2026-11-09, more stock)
MIN_VALUE = 0.003           # with MARKET_DATA deals seen: below this share of value, stock only what I already sold
EARLY = 60                  # buy when the window opens within this many seconds (the kit runs us every 60 s)
MIN_SHOPS = 3                # the user's choice (2026-11-07): a focus SKU above FOCUS_DEAR must have sold at this many other shops in the last 3 h
RECENT_MIN = 3000            # market deals in the last 3 h before focus SKUs follow them (about 8,000 at normal volume)
STOCK_CAP = 0.90             # the user's choice (2026-11-06): focus lines stop when stock cost, at COST_GUESS for new lines, passes this share of the book
# 0.80 -> 0.90 (2026-11-09 15:40, the user's 40M cash floor): with a 290M book, 0.80 would stop buying at about 58M cash, so the floor binds instead
SPREAD = True                # the user's choice (2026-11-09 15:40): focus lines from spread_lines (all goods SKUs at MIN_SHOPS+ shops, tiers in turn)
TIER_HIGH = 1_000_000        # list price above this: high tier
TIER_LOW = 200_000           # list price up to this: low tier; between: mid
TIER_SKUS = 25               # best sellers kept per tier (23 high, 45 mid, 42 low SKUs sold at 3+ shops at 15:40)
HIGH_A_MIN = 0.72            # Claude (2026-11-09 20:22, hourly check): high tier only where the market's grade A sells at this share of list.
# My A units cost 0.55-0.75 of list (random), the floor is cost, and 1M+ A sold at 0.65-0.75: G24f5f979 (market A 0.676) had 121 offers
# and 1 deal against my 0.733 floor. Below 0.72 a new dear unit has almost no margin and about 1/3 comes as grade C
# ddolcom (2026-11-12 10:30, the user's "1, 2를 적용"): 0.72 -> 0.66. Over 6 h the class's top shops sold 53-96 units of 1M+ list
# against my 25 (SungJinho 96, dragonash22 68, Nocha12 59) at A 0.67-0.72 of list with supply at 0.585-0.62 of list like mine, and
# paid 2,371-2,841만 an hour against my 991만 (logs/top-shops-1112-1008.txt). Blocked at 0.72: G24f5f979 (market A 0.672, 22 deals/h),
# G00327e80 0.699, Gf6b70da8 0.700, Gbec2b68f 0.692
HIGH_A_MIN = 0.66
# Claude (2026-11-13 11:50, the user's "고가 사입기준도 권해준 대로 진행해"): my 1M+ grade A units cost 0.68-0.75 of list and the
# floor is the cost, while the market's A sells at 0.65-0.70 (trend 07:44 "COST ABOVE": G47788edd 0.75, Ge5fe0d41 0.72-0.73,
# G6cd0e9d4 0.73); sofas sold 0 and 3 at 06:43 and 07:44. High tier now only where the market's A sells at 0.70+ of list
HIGH_A_MIN = 0.70
A_MIN_DEALS = 5              # grade A settles seen (customers.py sku_a) before a high-tier SKU is judged; until then it is not bought
BREAK_MAX = 0.15             # Claude (2026-11-10 19:22, the user's plan step 3): a SKU with BREAK_MIN_SETTLES+ market settles
# Claude (2026-11-11 03:22, plan 2 step 3): spread SKUs that pass every filter above (3+ shops, A price x surge, breaks) keep one
# unit more than focus_want. Goods unsold 24 h+ were 18.4M against the 29.65M the user set as the line to revert at
SPREAD_BONUS = 1
# ddolcom (2026-11-12 20:20, the user's "1번부터 5번까지 ... 동시에 진행해", top-20 shop study logs/top-shops-1112-2002.txt):
# refunds 150만/h against Nocha12 73만 and DY-76 40만, about 43% of my margin; 16 SKUs with 100+ market settles break 0.105-0.126
# (market mean 0.037). Food: my deals 25% food against Nocha12 81%, DY-76 94% at 18.8-20.2% margin with no break refunds; a dry
# run at 20:15 gave food 2 of 10 lines (focus took 8), 4 with FOCUS_MAX_LINES 6
BREAK_MAX = 0.10
FOCUS_MAX_LINES = 6
FOOD_MAX_QTY = 6
BREAK_MIN_SETTLES = 100     # breaking above BREAK_MAX is not bought. 12 h ledger: goods break 0.090 on average and mostly evenly
#   (chi2 50 on 28 df); only G07ec17d5 (29 of 163, 0.178) and G03cab593 (19 of 117, 0.162) stood out. A broken unit under warranty
#   refunds the whole price, and a mid-tier A nets about 0.07 of list, so a 0.15+ break rate loses money on every sale
FOCUS_MIN_QTY = 2            # units kept of a mid or low focus SKU below the FOCUS_QTY shares. 3 at 10:22, back to 2 at
#   16:22 (2026-11-10): deals/h stayed 53-84 for six hours while 24 h+ stock grew 21.9M -> 32.6M
C_EXEMPT_QTY = 2            # units a run for a high-tier SKU exempt from C_STOP (no A or B in stock, high_ok)
MEMORY = Path("restock_memory.json")


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def learn(mem, units, now):
    """Units we held last run and no longer hold either sold or were thrown away at their date."""
    held = {u["unit"]: u for u in units}
    for uid, old in mem["held"].items():
        if uid in held:
            continue
        gone = "expired" if old.get("expires_at") and old["expires_at"] <= now else "sold"
        cat = mem.setdefault(gone, {})
        cat[old["category"]] = cat.get(old["category"], 0) + 1
    mem["held"] = {uid: {"category": u["category"], "expires_at": u.get("expires_at")} for uid, u in held.items()}


def fav_qty(w):
    return 3 if w >= 3 else 2 if w >= 1 else 1


def pick_sku(skus, median, best, cheap):
    """The SKU to stock: the one the market buys most if known, else (cheap phase) the cheapest an order of
    this category likely accepts, else the one closest to the median."""
    by_sku = {c["sku"]: c for c in skus}
    if best in by_sku and (not cheap or by_sku[best]["list_price"] <= CHEAP_MAX_LIST):
        return by_sku[best]
    if cheap:
        ok = [c for c in skus if CHEAP_SHARE * median <= c["list_price"] <= CHEAP_MAX_LIST]
        if ok:
            return min(ok, key=lambda c: c["list_price"])
    return min(skus, key=lambda c: abs(c["list_price"] - median))


def pop_qty(share):
    return next((q for at, q in POP_QTY if share >= at), 1)


def focus_want(sku_share, list_price):
    """Units to keep of one focus SKU: more where it sells often, at most 2 of the dearest."""
    # Claude (2026-11-10 10:22, hourly check): stock cost fell 126.6M -> 88.6M overnight with 150M cash above the user's 40M floor;
    # a dry run wanted 9 units, every target full. Mid and low SKUs keep FOCUS_MIN_QTY at least; high stays at 2
    want = next((q for at, q in FOCUS_QTY if sku_share >= at), FOCUS_MIN_QTY if list_price <= TIER_HIGH else 2)
    return min(want, FOCUS_DEAR_QTY) if list_price > FOCUS_DEAR else want


def focus_lines(inv, catalog, focus_cats, cu):
    """[(sku, qty)] missing in the focus categories: sold-out SKUs first, then the SKU with the most market value."""
    balance = inv.get("balance") or 0
    recent = customers.recent_buys(cu, catalog)         # what sells now; until it has RECENT_MIN deals, all sales so far
    use_recent = sum(sum(s.values()) for s in recent.values()) >= RECENT_MIN
    shops = customers.recent_shop_counts(cu)
    total = sum(sum(s.values()) for s in recent.values()) if use_recent else sum(customers.popular(cu).values())
    have, c_have = {}, {}
    for u in inv["units"]:          # grade C goods count half: buyers that refuse C would find only C and walk
        if u.get("grade") == "expired":
            continue
        if u.get("kind") == "goods" and u.get("grade") == "C":
            c_have[u["sku"]] = c_have.get(u["sku"], 0) + 1
        else:
            have[u["sku"]] = have.get(u["sku"], 0) + 1
    # Claude (2026-11-08 17:23, hourly check): with C not counted at all C piled up (137 units, 48.5M, e.g. G1515d951 C 10 A 3);
    # over 3 h on 1M+ goods I sold 2 C of the market's 60 even at 0.60 of list. Two C units now count as one held unit
    # ddolcom (2026-11-09 07:40, the user's instruction "C에 대해 1번부터"): the supply gives a random grade (POST /api/supply takes
    # only sku and qty) and the kit's default buyer accepts only A and B, so every new buy of a SKU full of C brings more C.
    # A SKU holding C_STOP or more grade C goods gets no new units until its C sells (Gf6b70da8 C 5 A 1, G03d0199f C 5 A 1)
    c_full = {s for s, n in c_have.items() if n >= C_STOP}
    for s, n in c_have.items():
        have[s] = have.get(s, 0) + n // 2
    # Claude (2026-11-09 23:22, hourly check): the market's top SKU G00327e80 (25.7 deals/h, grade A at 0.734 of list) held only
    # 3 grade C here, so C_STOP kept A out; 7 C deals agreed in 3 h, none settled. A high-tier SKU that passes high_ok and holds
    # no A or B goods is exempt from C_STOP, at most C_EXEMPT_QTY units a run
    surge = {x["sku"]: x.get("factor") for x in inv.get("surge") or [] if x.get("sku")}
    ab = {u["sku"] for u in inv["units"] if u.get("kind") == "goods" and u.get("grade") in ("A", "B")}
    exempt = {s for s in c_full if s not in ab and s in catalog and catalog[s]["list_price"] > TIER_HIGH
              and high_ok(cu.get("sku_a", {}), s, surge)}
    c_full -= exempt
    if use_recent and SPREAD:
        return [(s, min(q, C_EXEMPT_QTY) if s in exempt else q) for s, q in spread_lines(recent, catalog, shops, total, have, c_full, balance, cu.get("sku_a", {}), surge, cu.get("brk"))]
    lines = []
    for cat in focus_cats:
        skus = recent.get(cat, {}) if use_recent else cu.get("sku_buys", {}).get(cat, {})
        fit = [s for s in skus if s in catalog and catalog[s]["list_price"] * COST_GUESS <= UNIT_SHARE * balance
               and (not use_recent or catalog[s]["list_price"] <= FOCUS_DEAR or shops.get(s, 0) >= MIN_SHOPS)]
        fit.sort(key=lambda s: -skus[s] * catalog[s]["list_price"])
        for s in fit[:FOCUS_SKUS]:
            if s in c_full:
                continue
            missing = focus_want(skus[s] / total, catalog[s]["list_price"]) - have.get(s, 0)
            if missing > 0:
                lines.append((have.get(s, 0) > 0, -skus[s] * catalog[s]["list_price"], s, missing))
    return [(s, qty) for _, _, s, qty in sorted(lines)]     # sold-out SKUs first: extra units must not crowd them out


def tier(list_price):
    return 0 if list_price > TIER_HIGH else 1 if list_price > TIER_LOW else 2


def high_ok(sku_a, s, surge=None):
    """A SKU is bought only when grade A sells at HIGH_A_MIN x its supply surge of list or more (A_MIN_DEALS deals seen)."""
    # Claude (2026-11-10 17:22, hourly check, the user's plan step 1): two days of supply lines (2,282) kept cost/list flat at
    # 0.56-0.59, but A costs 0.55-0.75 of list times surge (G021aae64 x1.114: A at 0.68-0.78 against a market 0.711, 0 sold in
    # 3 h). The plan said 0.75 x surge; a dry run kept 5 of 73 top SKUs, so the high tier's 0.72 now holds for mid and low too
    # (mid 11 of 25, low 14 of 25, 70% and 60% of their market deals) and every threshold is scaled by the SKU's surge
    row = sku_a.get(s)
    need = HIGH_A_MIN * ((surge or {}).get(s) or 1)
    return bool(row) and row[1] >= A_MIN_DEALS and row[0] is not None and row[0] >= need


def spread_lines(recent, catalog, shops, total, have, c_full, balance, sku_a=None, surge=None, brk=None):
    """ddolcom (2026-11-09 15:40, the user's instruction): every goods SKU of any category that settled at MIN_SHOPS or more
    other shops in the last 3 h, the TIER_SKUS best sellers of each price tier (dear by value, mid and cheap by deals),
    taken in turn high, mid, low so one run's few lines do not all go to sofas. Sold-out SKUs first within the turn."""
    by_tier = {0: [], 1: [], 2: []}
    for skus in recent.values():
        for s, n in skus.items():
            c = catalog.get(s)
            if not c or c.get("kind") != "goods" or s in c_full or shops.get(s, 0) < MIN_SHOPS:
                continue
            if c["list_price"] * COST_GUESS > UNIT_SHARE * balance:
                continue
            if not high_ok(sku_a or {}, s, surge):
                continue
            nb = (brk or {}).get(s)
            if nb and nb[0] >= BREAK_MIN_SETTLES and nb[1] / nb[0] > BREAK_MAX:
                continue
            by_tier[tier(c["list_price"])].append((n, s))
    queues = []
    for t, xs in by_tier.items():
        xs.sort(key=lambda x: -(x[0] * catalog[x[1]]["list_price"] if t == 0 else x[0]))
        q = []
        for n, s in xs[:TIER_SKUS]:
            want = focus_want(n / total, catalog[s]["list_price"]) + SPREAD_BONUS
            if catalog[s]["list_price"] > FOCUS_DEAR:
                want = min(want, FOCUS_DEAR_QTY)
            missing = want - have.get(s, 0)
            if missing > 0:
                q.append((have.get(s, 0) > 0, s, missing))
        q.sort(key=lambda x: x[0])                      # stable: sold-out first, then by the tier's order
        queues.append(q)
    out = []
    while any(queues):
        for q in queues:
            if q:
                _, s, qty = q.pop(0)
                out.append((s, qty))
    return out


def plan(inv, catalog, demand, mem, cu=None):
    sold, expired = mem.get("sold", {}), mem.get("expired", {})
    cu = cu or {}
    fav = customers.favourites(cu)
    pop = customers.popular(cu)
    total = sum(pop.values())
    share = {cat: n / total for cat, n in pop.items()} if total else {}
    value = customers.sales_value(cu, catalog)
    vtotal = sum(value.values())
    vshare = {cat: v / vtotal for cat, v in value.items()} if vtotal else {}
    cheap = sum(sold.values()) < CHEAP_PHASE
    stock = {}
    for u in inv["units"]:
        if u.get("grade") != "expired":
            stock[u["category"]] = stock.get(u["category"], 0) + 1
    by_cat = {}
    for sku, c in catalog.items():
        by_cat.setdefault(c["category"], []).append(c)

    focus_cats = [d["category"] for d in demand if d["kind"] == "goods" and vshare.get(d["category"], 0) >= VALUE_FOCUS]

    cats = []
    for d in demand:
        cat = d["category"]
        if cat in focus_cats:                           # stocked by SKU, below
            continue
        hot = share.get(cat, 0) >= HOT_SHARE            # a regular's one-off buy does not make a sofa hot
        if d["median_price"] > (HOT_MAX_LIST if hot else CHEAP_MAX_LIST if cheap else MAX_LIST) or cat not in by_cat:
            continue
        if total >= MARKET_DATA and not (pop.get(cat) or fav.get(cat) or sold.get(cat)):
            continue                                    # the market has spoken: nobody buys this
        if total >= MARKET_DATA and vshare.get(cat, 0) < MIN_VALUE and not sold.get(cat):
            continue                                    # too little money moves here to tie up stock
        if d["kind"] == "food" and (d.get("shelf_hours") or 0) < MIN_SHELF_HOURS:
            continue
        if expired.get(cat, 0) >= 2 and not sold.get(cat):
            continue                                    # rotted twice and never sold: stop
        want = max(fav_qty(fav.get(cat, 0)), pop_qty(share.get(cat, 0)), 2 if sold.get(cat, 0) >= 2 else 1)
        if d["kind"] == "food":
            want = min(want, FOOD_MAX_QTY)
        if stock.get(cat, 0) < want:
            cats.append((-fav.get(cat, 0), -pop.get(cat, 0), -sold.get(cat, 0), d["median_price"], cat,
                         want - stock.get(cat, 0), cheap and not hot))
    cats.sort()             # customers' favourites, then the market's best sellers, my sales, cheapest

    spend = (inv.get("balance") or 0) - CASH_FLOOR
    buy = []
    focus_left = FOCUS_SPEND * spend
    held = sum(u.get("cost") or 0 for u in inv["units"] if u.get("grade") != "expired")
    cash = (inv.get("balance") or 0) - (inv.get("start") or 0) - (inv.get("injected") or 0)
    room = STOCK_CAP * (cash + held) - held     # book = cash + stock at cost; new lines move cash into stock, the book stays
    focus_left = min(focus_left, max(room, 0))
    for s, qty in focus_lines(inv, catalog, focus_cats, cu):
        if len(buy) >= FOCUS_MAX_LINES:
            break
        unit = catalog[s]["list_price"] * COST_GUESS
        qty = min(qty, int(FOCUS_LINE_SHARE * inv["balance"] / unit), int(focus_left / unit))
        if qty <= 0:
            continue
        focus_left -= unit * qty
        spend -= unit * qty
        buy.append({"sku": s, "qty": qty})
    for *_, median, cat, qty, cheap_cat in cats:
        if len(buy) >= MAX_LINES:
            break
        sku = pick_sku(by_cat[cat], median, customers.best_sku(cu, cat), cheap_cat)
        if sku["list_price"] > DEAR:                     # dear SKUs: at most DEAR_QTY in stock, not per line
            qty = min(qty, DEAR_QTY - stock.get(cat, 0))
            if qty <= 0:
                continue
        qty = max(1, min(qty, int(LINE_SHARE * inv["balance"] / (sku["list_price"] * COST_GUESS))))
        cost = sku["list_price"] * COST_GUESS * qty
        if cost > spend:
            continue
        if sku["list_price"] > DEAR and cost > room:    # STOCK_CAP holds here too: a TV was still bought with stock over the cap
            continue
        if sku["list_price"] > DEAR:
            room -= cost
        spend -= cost
        buy.append({"sku": sku["sku"], "qty": qty})
    return buy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=".market")
    state = Path(ap.parse_args().state)
    inv = load(state / "inventory.json", {})
    if inv.get("round", 0) < 3 or inv.get("balance") is None:
        print(json.dumps({"buy": []}))
        return
    now = time.time()
    mem = load(MEMORY, {"held": {}, "sold": {}, "expired": {}})
    learn(mem, inv["units"], now)
    catalog = load(state / "catalog.json", {})
    cu = customers.update(mem, catalog)
    try:                                        # market_buyer.py weighs shops by this; small, so it reads it per order
        Path("shop_prices.json").write_text(json.dumps(cu.get("prices", {}), ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        print(f"restock: shop_prices.json not written ({type(e).__name__})")
    window = inv.get("restock") or {}
    if window.get("open") or (window.get("opens_at") or 0) - now <= EARLY:
        buy = plan(inv, catalog, load(state / "demand.json", []), mem, cu)
    else:
        buy = []
    try:                                        # stock ads as the shop (promo.py decides whether one is due); never blocks a buy
        import subprocess
        import sys
        subprocess.Popen([sys.executable, "-X", "utf8", "promo.py", "--state", str(state)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=open("logs/promo.err", "a"), start_new_session=True)
    except OSError as e:
        print(f"restock: promo.py not started ({type(e).__name__})")
    mem["last_run"] = {"t": int(now), "balance": inv["balance"], "buy": buy}
    MEMORY.write_text(json.dumps(mem, ensure_ascii=False, indent=1), encoding="utf-8")
    top = sorted(customers.favourites(cu).items(), key=lambda x: -x[1])[:3]
    print(f"restock: balance {inv['balance']}, {len(inv['units'])} units, {len(cu.get('customers', {}))} customers, "
          f"favourites {top}, window {'open' if window.get('open') else 'opens in %d s' % ((window.get('opens_at') or now) - now)}, "
          f"buying {len(buy)} lines")
    print(json.dumps({"buy": buy}, ensure_ascii=False))


if __name__ == "__main__":
    main()
