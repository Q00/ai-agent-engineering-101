"""Round 3 restock hook: what to buy for the shop, every 60 s.

    python restock.py --state .market          (agent.json "restock"; prints {"buy": [{"sku", "qty"}, ...]})

The market says neither what sells nor who wants what, so this learns it from two places:
  - the public ledger: every settled deal names the SKU sold (GET /api/ledger?after=N, read incrementally);
    counted per category with a half-life of HALF_LIFE hours, so the target follows what sells now
  - the shop's own log (seller_r3_log.jsonl): its deals, and buyers it had to turn away (no stock)

The limits come from policy.py (params.json) by the shop's situation: launch, cash tight (no expansion),
flush (deeper stock, dearer goods), wind-down (one unit per category, small stock), liquidation (buy nothing).

Per category the target stock is 1 unit (be in every order's shop list), plus up to `extra` more by the
category's share of recent market sales and our own sales. A category gets the SKU that sold most in it,
or, with no sales seen yet, the SKU nearest the category's median price (orders accept 0.5-1.5x a reference
price, so the median fits most). Limits, so stock never eats the shop:
  - cash kept back: keep_cash; stock at cost at most max_stock_share of equity (balance + stock at cost),
    so stock grows with the shop (unsold stock is a loss, goods pay 1 %/day)
  - goods listed above max_goods_price are skipped (one unit ties up too much cash), except up to
    max_goods_price_hot in a category with at least 30 % of the top category's market sales (Round 3 opened with
    beds/mattresses at 58 sales in 15 min, mean 209k, up to 392k)
  - food already in stock that no longer lasts the category's typical need is not counted as stock, so the
    category gets fresh units (the seller ships those to buyers who do not say their need); the stale ones still
    count against cash and stock limits, and sell to short-need buyers or at the near-date discount
  - food: only SKUs that still serve buyers when they sell: shelf life x 0.6 (a typical arrival) minus the time
    to sell must cover the use_within_hours buyers ask in that category (median of the food orders our buyer saw
    in 24 h, else NEED_SHARE x the SKU's shelf life: orders ask 10/25/50/75 % of it); if the best seller fails, a longer-lasting SKU of the
    category; quantity no more than our rate sells while it still lasts (food is thrown away at cost)
  - at most 3 units per line, 10 lines per run (the kit's limit); urgent lines first: sold-out categories
    with sales, then categories we do not cover yet
Supply windows (rule added when Round 3 opened, 11-03): supply opens once every 20 minutes for 3 minutes
(inventory.json "restock": open, opens_at, closes_at). Outside a window, more than LEAD seconds before it opens,
this buys nothing: the kit would spend up to 90 s per line waiting. In a window each category's target also
covers the next gap: our sales rate there x GAP_HOURS x 1.5, up to MAX_QTY units per line.
Within clear_window_hours of the end a category is only refilled up to what our sales rate in it can sell
before the end (policy.clearance's rule, seen from the buying side).
Lines go to goods first, food after (Round 3, first 13 h: goods took 31 % of our supply lines and made 75 % of
revenue, ~80k margin per line against ~17k for food; supply lines per window are the limit, so goods fill first
and food gets the lines left). Food is not dropped: it is most of our sales count, reviews and visits.
Price tiers (tier_wants): valuable goods categories also get one unit at each of up to 3 price points chosen to
answer the most orders (sofas: 86 % of our buyer's sofa orders found no shop on 11-03).
Below the bankruptcy line with (almost) nothing left to sell it files ("bankrupt": true).
The forum is not read (user, 11-06): posts are unverified, so demand comes only from the ledger, our own logs and
our buyer's orders.
It also keeps the shop's reputation for policy.py: the average stars of our newest Round 3 reviews on
/api/shops (the market shows the last 10), in restock_state.json "rep".
State (ledger position, decayed counts) in restock_state.json; every run logs one line to stderr.
"""
import argparse
import json
import math
import os
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

from policy import shop_params

HERE = Path(__file__).resolve().parent
MARKET = "https://market.ourolabs.ai"
STATE = Path(os.environ.get("RESTOCK_STATE", HERE / "restock_state.json"))
SHOP_NAME = os.environ.get("SHOP_NAME", "yms979")
SALES_LOG = Path(os.environ.get("SELLER_LOG", HERE / "seller_r3_log.jsonl"))
HALF_LIFE = 6.0
LEAD = 60                    # seconds before a window opens that a run may already send its lines
GAP_HOURS = 0.4              # until the window after this one: 20 min apart, 3 min open, slots move a little
MAX_QTY = 20                 # the market's limit per supply line
MAX_LINES = 18               # one supply call per 10 s fills a 3-minute window (our kit copy takes 18 per run)
LEDGER_LAG = 2 * 3600        # seconds behind the newest ledger entry before skipping ahead
LEDGER_SKIP = 20000          # entries kept when skipping (~1.3 h at ~15k entries/h on 11-05)
RECENT = 3600                # seconds of our own sales that set the current rate per category
ARRIVAL_SHARE = 0.6          # shelf life left at a typical arrival (A >= 80 % 25 %, B 45-80 % 50 %, C < 45 % 25 %)
NEED_SHARE = 0.25            # a food order's use_within_hours is the category's shelf life x 0.10, 0.25, 0.50 or
                             # 0.75 (all 28 orders our buyer saw on 11-03); 0.25 is the median, used until our buyer
                             # has seen orders in the category (a fixed 72 h shut out 24 h categories: ready meals)
INTEL = Path(os.environ.get("MARKET_INTEL", HERE / "market_intel.jsonl"))              # hours
SURGE_MAX = 0.15             # most cost surge we pay on a SKU (margins run ~20-25 % of list)
SLOW_UNIT = 0.25             # 11-11: what a grade C unit counts for in the SKU cover (paid 10-19% when not asked for)
OVER_UNIT = 0.5              # ... and an A/B unit whose floor is above the SKU's market median (sells, but slowly: ~27% close)
LINE_MAX_COST = 25e6         # 11-11: most one supply line may cost (at COST_GUESS), so one 3.8M sofa x20 does not take 46M
MUST_LINES = 4               # 11-11: most must_stock lines in one window
WINDOW_MAX_COST = 150e6      # ... and most one window may cost (90M -> 150M the same evening: user, 'buy more boldly, cash is idle')
COST_A, SURGE_MARGIN = 0.65, 0.03   # 11-11: typical grade A cost share; margin kept under the market median x
COST_GUESS = 0.6             # cost as a share of list price before a unit arrives (A 55-75 %, B 40-60 %, C 25-45 %)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def ledger_after(seq, pages=10):
    """Settled deals after seq: [(seq, ts, sku)]. Reads at most `pages` pages of 500 per run. More than
    LEDGER_LAG behind (11-05: 285k entries, ~15 h, because only window runs read it), it skips to the newest
    LEDGER_SKIP entries: counts decay with a 6 h half-life, so stale sales say little about now."""
    out, last, ts = [], seq, None
    for _ in range(pages):
        url = f"{MARKET}/api/ledger?after={last if last is not None else -500}"
        with urllib.request.urlopen(url, timeout=20) as r:
            page = json.load(r)
        if not page:
            break
        for e in page:
            last, ts = e["seq"], e.get("ts")
            d = e.get("data") or {}
            sku = d.get("item") or d.get("sku")
            if e.get("kind") == "settle" and isinstance(sku, str):
                out.append((e["seq"], e.get("ts") or time.time(), sku))
        if len(page) < 500:
            break
    if ts and time.time() - ts > LEDGER_LAG:
        with urllib.request.urlopen(f"{MARKET}/api/ledger?after=-500", timeout=20) as r:
            head = json.load(r)
        if head:
            last = max(last, head[-1]["seq"] - LEDGER_SKIP)
            print(f"[restock] ledger {time.time() - ts:.0f} s behind: skipping to #{last}", file=sys.stderr)
    return out, last


def market_sales(st, catalog):
    """Add the ledger's new settled deals to the decayed market counts per category and SKU."""
    try:
        hours = (time.time() - st["t"]) / 3600                # bring the counts to now first
        st["cat"], st["sku"] = decay(st["cat"], hours), decay(st["sku"], hours)
        st["own_cat"], st["forum_cat"] = decay(st["own_cat"], hours), decay(st.get("forum_cat", {}), hours)
        st["t"] = time.time()
        sold, st["seq"] = ledger_after(st["seq"])
        for _, _, sku in sold:
            cat = (catalog.get(sku) or {}).get("category")
            if cat:
                st["cat"][cat] = st["cat"].get(cat, 0) + 1
                st["sku"][sku] = st["sku"].get(sku, 0) + 1
    except Exception as e:
        print(f"[restock] ledger: {type(e).__name__}: {e}", file=sys.stderr)


def market_p50_x(hours=6, min_n=8):
    """Median price/list per goods SKU over the market's last `hours` of settles (buyer_value's ledger cache)."""
    try:
        deals = json.loads((HERE / ".market" / "shop_value.json").read_text()).get("deals") or []
    except (OSError, ValueError, NameError):
        return {}
    if not deals:
        return {}
    t1 = max(d["ts"] for d in deals)
    by = {}
    for d in deals:
        if d.get("goods") and t1 - d["ts"] <= hours * 3600 and d.get("sku"):
            by.setdefault(d["sku"], []).append(d["x"])
    return {k: sorted(v)[len(v) // 2] for k, v in by.items() if len(v) >= min_n}


def decay(counts, hours):
    k = 0.5 ** (hours / HALF_LIFE)
    return {c: v * k for c, v in counts.items() if v * k > 0.01}


def tier_wants(catalog, demand, value, top_cat, units, wants, p, now):
    """Price tiers in valuable goods categories. An order accepts the category's SKUs priced 0.5-1.5x a reference,
    so one price point only answers part of a category's orders (11-03: our single 279k mattress fit ~45% of
    mattress orders; 352 of 410 sofa orders, budgets around 2.7M, found no shop at all). For each goods category
    worth >= tier_value_share of the top category's market sales, or with >= tier_unserved orders our buyer saw
    go unserved in 24 h, pick up to tier_count price points that answer the most orders and hold one unit at each:
    the orders our buyer logged (acceptable price range, unserved ones counted twice) when there are 5+, else
    reference prices spread like the catalog. Prices up to tier_max_price."""
    orders = defaultdict(list)
    try:
        with open(INTEL, encoding="utf-8") as f:
            for line in f.readlines()[-20000:]:
                if '"type": "order"' in line:
                    r = json.loads(line)
                    if r.get("t", 0) >= now - 86400 and r.get("kind") == "goods" and r.get("hi"):
                        orders[r["category"]].append((r["lo"], r["hi"], 1 if r.get("served") else 2))
    except (OSError, ValueError):
        pass
    planned = {w[2]["sku"] for w in wants}             # SKUs already on this run's list
    out = []
    for d in demand:
        cat = d["category"]
        if d.get("kind") != "goods":
            continue
        unserved = sum(1 for o in orders.get(cat, []) if o[2] == 2)
        if value.get(cat, 0) / top_cat < p["tier_value_share"] and unserved < p["tier_unserved"]:
            continue
        skus = [v for v in catalog.values() if v["category"] == cat and v["list_price"] <= p["tier_max_price"]
                and v["sku"] not in planned]
        if not skus:
            continue
        if len(orders.get(cat, [])) >= 5:
            refs = orders[cat]
        else:
            refs = [(v["list_price"] * 0.5, v["list_price"] * 1.5, 1) for v in catalog.values() if v["category"] == cat]
        covers = lambda price: {i for i, (lo, hi, _) in enumerate(refs) if lo <= price <= hi}
        held = [u["list_price"] for u in units if u.get("category") == cat]
        got = set().union(*[covers(x) for x in held]) if held else set()
        for _ in range(p["tier_count"]):
            best = max(skus, key=lambda v: sum(refs[i][2] for i in covers(v["list_price"]) - got))
            gain = sum(refs[i][2] for i in covers(best["list_price"]) - got)
            if gain <= 0:
                break
            got |= covers(best["list_price"])
            out.append(((False, 0, -gain), cat, best, 2 if unserved >= 2 * p["tier_unserved"] else 1))
    return out


def sku_wants(catalog, have_sku, p, now, hours_left, market_sku, mkt_units=None, mkt_shops=None):
    """Goods SKUs stocked by their own demand. The category loop buys one SKU per category, and tiers hold one
    unit per price point, so a SKU buyers keep asking for runs out: on 11-04/05 the 1.87M sofa (216k margin a
    unit) turned away 66 buyers in 24 h, the 1.6M and 1.09M mattresses 26 and 17, with 90M of cash idle.
    Rate per SKU = (our deals + sku_miss_weight x turned-away asks naming it) over the last sku_hours; hold
    rate x sku_cover_hours, at most sku_lines SKUs (by margin-weighted rate), only goods (no expiry).
    The market's best sellers by value count too, at market_share of their market rate (ledger, half-life
    HALF_LIFE): on 11-05 the top SKU by value was a 2.32M sofa at ~35 sales/h market-wide that we never held
    (above every price cap), with 2.25M and 2.19M sofas next; prices up to sku_max_price.
    Near-monopolies: a SKU selling >= scarce_min_rate/h market-wide whose stock in all other shops lasts under
    scarce_market_hours, held by at most scarce_max_shops other shops, is taken at scarce_share of the market rate for scarce_cover_hours, ranked by sales value with the rest.
    Goods only. On 11-05: Zinus Lumi bed frame 10.8/h with 1.8 h of market stock, Downing Lilo chair and Boruneo
    LED bed ~1 h; we held none of them."""
    mkt_units, mkt_shops = mkt_units or {}, mkt_shops or {}
    sold, asked = Counter(), Counter()
    since = now - p["sku_hours"] * 3600
    if SALES_LOG.is_file():
        with open(SALES_LOG, encoding="utf-8") as f:
            for line in f.readlines()[-30000:]:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("t", 0) < since:
                    continue
                if ev.get("event") == "deal" and ev.get("sku"):
                    sold[ev["sku"]] += 1
                elif ev.get("event") == "no_stock" and ev.get("skus"):
                    asked[ev["skus"][0]] += 1
    # 11-11: 'deal' in the seller log is an agreed close, paid or not (25% of furniture closes were never paid,
    # and a unit stuck in an unpaid checkout was agreed again and again): the per-SKU demand is the PAID settles
    # of our shop on the ledger when the cache has them
    try:
        sv_ = json.loads((HERE / ".market" / "shop_value.json").read_text())
        paid_ = Counter(d["sku"] for d in sv_.get("deals") or [] if d.get("shop") == "yms979" and d.get("ts", 0) >= since)
        if sum(paid_.values()) >= 20:
            sold = paid_
    except (OSError, ValueError):
        pass
    mkt = {k: n * math.log(2) / HALF_LIFE for k, n in market_sku.items()}            # market sales per hour
    best = sorted((k for k in mkt if (catalog.get(k) or {}).get("kind") == "goods"),
                  key=lambda k: -mkt[k] * catalog[k]["list_price"])[:p["market_top"]]
    scarce = {k for k, r in mkt.items() if r >= p["scarce_min_rate"] and k in catalog
              and mkt_units.get(k, 0) / r < p["scarce_market_hours"] and mkt_shops.get(k, 0) <= p["scarce_max_shops"]
              and catalog[k]["kind"] == "goods"}             # goods only (user, 11-05: no food here)
    out = []
    for sku in set(sold) | set(asked) | set(best) | scarce:
        v = catalog.get(sku)
        if not v or v["list_price"] > p["sku_max_price"]:
            continue
        if sku in scarce:
            cover = p["scarce_cover_hours"]
            rate = p["scarce_share"] * mkt[sku]
            qty = min(MAX_QTY, math.ceil(rate * cover) - have_sku.get(sku, 0))
            if hours_left <= p["clear_window_hours"]:
                qty = min(qty, math.floor(sold[sku] / p["sku_hours"] * hours_left) - have_sku.get(sku, 0))
            if qty > 0:
                # ranked with the proven SKUs by sales value per hour, not ahead of them: first in line, cheap
                # near-monopolies filled the goods lines on 11-05 evening and the 2.32M Mate sofa went unbought
                # (big sales fell from 55/h to 9/h)
                out.append(((False, -1, -rate * v["list_price"]), v["category"], v, qty))
            continue
        if v["kind"] != "goods":
            continue
        own = (sold[sku] + p["sku_miss_weight"] * asked[sku]) / p["sku_hours"]
        rate = max(own, p["market_share"] * mkt.get(sku, 0))
        # our own demand is held deep, like inextro (155M list of goods vs our 43M, 11-05), so a buyer between
        # windows finds it; a share of the market's rate is only a guess, so it covers to the next window
        target = max(math.ceil(own * p["sku_cover_hours"]), math.ceil(p["market_share"] * mkt.get(sku, 0) * GAP_HOURS * 1.5))
        if hours_left <= p["clear_window_hours"]:          # near the end: only what sells before it
            target = min(target, math.floor(sold[sku] / p["sku_hours"] * hours_left))
        target = math.ceil(target * (p.get("sku_auto", {}).get(sku) or {}).get("cover", 1.0))   # strategist.py
        qty = min(MAX_QTY, target - have_sku.get(sku, 0))
        if qty > 0:
            out.append(((False, -1, -rate * v["list_price"]), v["category"], v, qty))
    return sorted(out, key=lambda w: w[0])[:p["sku_lines"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=".market")
    md = Path(ap.parse_args().state)
    inv = read_json(md / "inventory.json", {})
    regime, p, sig = shop_params()
    win = inv.get("restock") or {}
    shut = (not win.get("open") and win.get("opens_at") and win["opens_at"] - time.time() > LEAD)
    if inv.get("round", 0) < 3 or inv.get("balance") is None or not p["buy"] or shut:
        if inv.get("round", 0) >= 3:                   # keep up with the ledger every run, not only in windows
            st = read_json(STATE, None)
            if st:
                catalog = read_json(md / "catalog.json", {})
                market_sales(st, catalog)
                STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        if shut:
            print(f"[restock] supply closed, opens in {win['opens_at'] - time.time():.0f} s; nothing to buy now",
                  file=sys.stderr)
        if inv.get("round", 0) >= 3:
            print(f"[restock] {regime}: not buying", file=sys.stderr)
        print(json.dumps({"buy": []}))
        return
    catalog = read_json(md / "catalog.json", {})
    demand = read_json(md / "demand.json", [])
    st = read_json(STATE, {"seq": None, "t": time.time(), "cat": {}, "sku": {}, "own_cat": {}, "log_pos": 0})
    now = time.time()
    hours = (now - st["t"]) / 3600
    st["cat"], st["sku"], st["own_cat"] = decay(st["cat"], hours), decay(st["sku"], hours), decay(st["own_cat"], hours)
    # forum "wanted" posts are no longer demand (user, 11-06: the forum can carry false information; only the
    # ledger, our own records and the market's files count)
    st["forum_cat"] = {}
    st["t"] = now

    market_sales(st, catalog)

    try:                                                    # our reputation, for the seller's prices
        with urllib.request.urlopen(f"{MARKET}/api/shops", timeout=15) as r:
            shops = json.load(r).get("shops", [])
        me = next((x for x in shops if x.get("name") == SHOP_NAME), {})
        mkt_units, mkt_shops = Counter(), Counter()        # units of each SKU held by the other shops, and by how many
        for x in shops:
            if x.get("name") != SHOP_NAME:
                held = {k: q for k, q in (x.get("stock") or {}).items() if q}
                mkt_units.update(held)
                mkt_shops.update(held.keys())
        st["mkt_units"], st["mkt_shops"] = dict(mkt_units), dict(mkt_shops)
        stars = [v["stars"] for v in me.get("reviews") or []
                 if (v.get("round") or 0) >= 3 and isinstance(v.get("stars"), (int, float))]
        st["rep"] = {"avg": sum(stars) / len(stars) if stars else None, "n": len(stars), "t": now}
    except Exception as e:
        print(f"[restock] reviews: {type(e).__name__}: {e}", file=sys.stderr)

    needs = defaultdict(list)                               # food: use_within_hours our buyer saw, last 24 h
    try:
        with open(INTEL, encoding="utf-8") as f:
            for line in f.readlines()[-20000:]:
                if '"need"' not in line:
                    continue
                r = json.loads(line)
                if r.get("t", 0) >= now - 86400 and r.get("category") and r.get("hours"):
                    needs[r["category"]].append(r["hours"])
    except (OSError, ValueError):
        pass
    needs = {c: sorted(v)[len(v) // 2] for c, v in needs.items()}   # median: a unit that misses one order
                                                                         # still fits the shorter ones

    recent = Counter()                                      # our deals and turned-away buyers, last RECENT s
    if SALES_LOG.is_file():
        with open(SALES_LOG, encoding="utf-8") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("t", 0) < now - RECENT:
                    continue
                if ev.get("event") == "deal" and ev.get("category"):
                    recent[ev["category"]] += 1
                elif ev.get("event") == "no_stock":
                    for sku in ev.get("skus") or []:
                        if (catalog.get(sku) or {}).get("category"):
                            recent[catalog[sku]["category"]] += 1
    hours_seen = min(1.0, max(0.25, (now - (read_json(HERE / "policy_shop.json", {}).get("open_ts") or now)) / 3600))

    misses = Counter()                                      # our own sales and turned-away buyers
    if SALES_LOG.is_file():
        with open(SALES_LOG, encoding="utf-8") as f:
            f.seek(st.get("log_pos", 0))
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("event") == "deal" and ev.get("category"):
                    st["own_cat"][ev["category"]] = st["own_cat"].get(ev["category"], 0) + 1
                elif ev.get("event") == "no_stock":
                    for sku in ev.get("skus") or []:
                        misses[sku] += 1
            st["log_pos"] = f.tell()

    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    stale = lambda u: (u.get("kind") == "food" and u.get("expires_at")
                       and (u["expires_at"] - now) / 3600
                       < needs.get(u.get("category"), NEED_SHARE * ((catalog.get(u["sku"]) or {}).get("shelf_hours") or 0)))
    fresh = [u for u in units if not stale(u)]             # stale food does not count as stock: buy fresh ones,
    have = Counter(u.get("category") for u in fresh)       # the seller ships the freshest to buyers who do not
    have_sku = Counter(u["sku"] for u in fresh)            # say their need (stale units still count for cash)
    stock_cost = sum(u.get("cost") or 0 for u in units)
    cash = inv["balance"]

    if (cash < (inv.get("bankrupt_line") or 500_000) and stock_cost < 100_000
            and (inv.get("bankruptcies_left") or 0) > 0):
        print(f"[restock] balance {cash}, nothing left to sell: filing", file=sys.stderr)
        print(json.dumps({"buy": [], "bankrupt": True}))
        STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        return

    med_price = {d["category"]: d.get("median_price") or 0 for d in demand}
    value = {k: n * med_price.get(k, 0) for k, n in st["cat"].items()}  # market sales in won, not units
    by_cat = defaultdict(list)
    top_value = max(value.values(), default=0) or 1
    for sku, v in catalog.items():
        hot = value.get(v["category"], 0) / top_value >= 0.3           # a category the market is buying now
        cap = max(p["max_goods_price"], p.get("max_goods_price_hot", 0)) if hot else p["max_goods_price"]
        if v["kind"] == "goods" and v["list_price"] > cap:
            continue
        if v["kind"] == "food" and (v.get("shelf_hours") or 0) < 24:
            continue
        # 11-11 20:50 (user: sell the food we hold, stop restocking it): food was 0.23 M/h of 35.5 M/h paid revenue
        # and 5% of supply lines, with ~1 M/day of units passing their date (those leave the book at cost). Only
        # params must_stock (long-shelf foods for the accounts that lift our star mean) is still bought.
        if v["kind"] == "food" and not p.get("restock_food", True):
            continue
        by_cat[v["category"]].append(v)

    top_cat = max(value.values(), default=0) or 1                      # a mattress outweighs a kimchi
    top_own = max(st["own_cat"].values(), default=0) or 1
    top_forum = max(st["forum_cat"].values(), default=0) or 1
    # 11-11 (user: stock deep and wide, sell a little under the others): for the per-SKU cover only units a normal
    # buyer will pay for count as stock. A grade C unit offered to a buyer who did not ask for C is paid ~10-19% of
    # the time, and a grade A unit whose floor (cost x floor_mult) sits above the SKU's market median waits for a
    # price-insensitive buyer: both counted 1 before, so a SKU holding only such units looked covered, was not
    # restocked and lost its asks. They now count SLOW_UNIT each.
    mx_ = market_p50_x()
    fm_ = p.get("floor_mult", 1.05)
    sellable_sku = Counter()
    for u in fresh:
        if u.get("kind") != "goods":
            sellable_sku[u["sku"]] += 1
            continue
        x_ = mx_.get(u["sku"])
        over_ = x_ is not None and (u.get("cost") or 0) * fm_ > x_ * (u.get("list_price") or 1)
        sellable_sku[u["sku"]] += SLOW_UNIT if u.get("grade") == "C" else OVER_UNIT if over_ else 1
    wants = sku_wants(catalog, {k: int(v) for k, v in sellable_sku.items()}, p, now, sig["hours_left"], st["sku"],
                      st.get("mkt_units"), st.get("mkt_shops"))   # (priority, category, sku, qty)
    for _, cat, v, qty in wants:                            # the category loop sees these as stock already
        have[cat] += qty
        have_sku[v["sku"]] += qty
    for d in demand:
        cat = d["category"]
        options = by_cat.get(cat)
        if not options:
            continue
        heat = (value.get(cat, 0) / top_cat + st["own_cat"].get(cat, 0) / top_own
                + 0.5 * st["forum_cat"].get(cat, 0) / top_forum)
        selling = st["cat"].get(cat, 0) >= p["cover_min_sales"] or st["own_cat"].get(cat, 0) > 0 \
            or st["forum_cat"].get(cat, 0) > 0 or hours_seen < 1     # lean: no baseline unit in a dead category
        target = (1 if selling else 0) + min(p["extra"], math.floor(p["extra"] * heat / 2 + 0.5))
        rate = max(st["own_cat"].get(cat, 0) * math.log(2) / HALF_LIFE,  # our sales per hour here: the decayed
                   recent.get(cat, 0) / hours_seen)                   # count, or the last hour if higher
        target = max(target, math.ceil(rate * GAP_HOURS * 1.5))       # last until the next window
        missing = target - have.get(cat, 0)
        if missing <= 0:
            continue
        if sig["hours_left"] <= p["clear_window_hours"]:    # near the end: only what will sell before it
            rate = st["own_cat"].get(cat, 0) * math.log(2) / HALF_LIFE
            missing = min(missing, math.floor(rate * sig["hours_left"]) - have.get(cat, 0))
            if missing <= 0:
                continue
        need_of = lambda v: needs.get(cat, NEED_SHARE * (v.get("shelf_hours") or 0))   # food: hours buyers need
        sell_h = max(GAP_HOURS, 1 / max(rate, 0.5))  # until a unit sells, at our rate (at least to the next window)
        lasts = lambda v: v["kind"] != "food" or (v.get("shelf_hours") or 0) * ARRIVAL_SHARE - sell_h >= need_of(v)
        options = [v for v in options if lasts(v)]  # a typical arrival must still serve the order when it sells
        if not options:
            continue
        seen = [v for v in options if st["sku"].get(v["sku"], 0) > 0 or misses.get(v["sku"])]
        if seen:
            v = max(seen, key=lambda v: st["sku"].get(v["sku"], 0) + misses.get(v["sku"], 0))
        else:
            med = d.get("median_price") or 0
            v = min(options, key=lambda v: (abs(v["list_price"] - med), -(v.get("shelf_hours") or 0)))
        qty = min(MAX_QTY, missing)
        if v["kind"] == "food":                     # no more than sells while it still lasts `need`
            sellable = math.floor(max(rate, 0.5) * ((v.get("shelf_hours") or 0) * ARRIVAL_SHARE - need_of(v)))
            qty = max(0, min(qty, max(1, sellable) - have_sku.get(v["sku"], 0)))
        if qty <= 0:
            continue
        priority = (v["kind"] == "food",           # goods first: a goods line earned ~80k margin vs ~17k for food
                    0 if have.get(cat, 0) == 0 and heat > 0 else 1 if have.get(cat, 0) == 0 else 2, -heat)
        wants.append((priority, cat, v, qty))

    wants += tier_wants(catalog, demand, value, top_cat, units, wants, p, now)
    # 11-11 (user: raise stars): SKUs kept in stock for the accounts that would lift our per-account star mean:
    # JangSeongWoong rates every shop 5.0 and has never paid us (a new 5.0 account is worth ~+.009 stars_rank), and
    # gom31-certified-goods rates by grade (A 5, B 4, C 3) with only 6 reviews of us at 3.67. They buy long-shelf
    # foods we held none of, or only aged (grade C) units of. params.json shop.base.must_stock = {sku: fresh units};
    # only grade A/B units count; at most MUST_LINES lines a window so the goods lines keep theirs.
    must = []
    for sku_, q_ in (p.get("must_stock") or {}).items():
        v_ = catalog.get(sku_)
        if not v_:
            continue
        good_ = sum(1 for u in fresh if u["sku"] == sku_ and u.get("grade") in ("A", "B"))
        if good_ < q_:
            must.append((good_ - q_, ((False, -2, 0), v_["category"], v_, min(MAX_QTY, int(q_ - good_)))))
    wants += [w for _, w in sorted(must, key=lambda m: m[0])[:MUST_LINES]]
    wants.sort(key=lambda w: w[0])
    max_stock = p["max_stock_share"] * (cash + stock_cost)
    buy, spend = [], 0
    surge = {x["sku"]: x.get("units") or 0 for x in inv.get("surge") or [] if x.get("sku")}
    mkt_x = market_p50_x()
    goods_lines = 0
    for _, cat, v, qty in wants:
        # rule seen 11-05: past 50 units of a SKU bought market-wide in an hour, each unit adds 0.2 % to its cost
        # (up to +50 %); we paid +13 % on the 424k mattress. Stop where the cost would pass +SURGE_MAX.
        # 11-11 (user: defend revenue 2nd): the hot bed SKU G021aae64 sold 403 units market-wide in 6 h at x ~.70
        # while our grade A units cost up to .81 of list (bought at +15 % surge): unsellable, 325 of 837 goods units
        # sat with floors above the market median. Allow only the surge that keeps a typical A unit (COST_A of list)
        # under the SKU's market median minus a margin; hot SKUs then buy only in the no-surge zone.
        allowed = SURGE_MAX
        if v["kind"] == "goods" and v["sku"] in mkt_x:
            allowed = min(SURGE_MAX, max(0.0, (mkt_x[v["sku"]] - SURGE_MARGIN) / COST_A - 1))
        qty = min(qty, max(0, 50 + round(allowed / 0.002) - surge.get(v["sku"], 0)))
        if qty <= 0:
            continue
        if len(buy) >= MAX_LINES:
            break
        if v["kind"] == "goods" and goods_lines >= p["max_goods_lines"]:   # food keeps the rest of the lines
            continue
        if (p.get("sku_auto", {}).get(v["sku"]) or {}).get("cover") == 0:   # the strategist stopped this SKU
            continue
        unit = v["list_price"] * COST_GUESS
        # 11-06: a line that did not fit whole was skipped, so the top sellers (Plana 4 swing 11 % of revenue,
        # 20 x ~2.3M) got nothing for 2 h and sat at 0 stock while 64M cash waited; buy what fits instead
        room = min(cash - spend - p["keep_cash"], max_stock - stock_cost - spend, WINDOW_MAX_COST - spend)
        qty = min(qty, int(room // unit), max(1, int(LINE_MAX_COST // unit))) if unit > 0 else qty
        if qty <= 0:
            continue
        cost = unit * qty
        buy.append({"sku": v["sku"], "qty": qty})
        spend += cost
        goods_lines += v["kind"] == "goods"

    STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    print(f"[restock] {len(units) - len(fresh)} stale food units not counted as stock; "
          f"{regime}: cash {cash}, stock {len(units)} units at cost {stock_cost}, categories {len(have)}; "
          f"market sales seen {sum(st['cat'].values()):.1f}; buying {len(buy)} lines (~{spend:.0f})", file=sys.stderr)
    print(json.dumps({"buy": buy}))


if __name__ == "__main__":
    main()
