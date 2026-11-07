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

CASH_FLOOR = 1_000_000      # never spend below this (bankruptcy line 500,000, and room to refill what sells);
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
FOCUS_SKUS = 10              # SKUs kept per focus category, by value (deals x list price)
FOCUS_QTY = [(0.02, 5), (0.005, 3)]     # (SKU's share of market deals, units to keep); below these, 2
FOCUS_DEAR = 500_000        # list price above this: at most FOCUS_DEAR_QTY units, however often it sells
FOCUS_DEAR_QTY = 3          # was 2 until 2026-11-06 20:23: stock 48.5% of book against the user's 60%, the best seller G24f5f979 sold out
UNIT_SHARE = 0.25           # one unit's cost at most this share of the balance: dearer sofas come as the balance grows
FOCUS_LINE_SHARE = 0.25     # one focus line spends at most this share of the balance
FOCUS_SPEND = 0.6           # focus lines spend at most this share of the spendable cash per run: food still refills
FOCUS_MAX_LINES = 6         # focus SKUs take at most this many of a run's MAX_LINES lines
MIN_VALUE = 0.003           # with MARKET_DATA deals seen: below this share of value, stock only what I already sold
EARLY = 60                  # buy when the window opens within this many seconds (the kit runs us every 60 s)
MIN_SHOPS = 3                # the user's choice (2026-11-07): a focus SKU above FOCUS_DEAR must have sold at this many other shops in the last 3 h
RECENT_MIN = 3000            # market deals in the last 3 h before focus SKUs follow them (about 8,000 at normal volume)
STOCK_CAP = 0.65             # the user's choice (2026-11-06): focus lines stop when stock cost, at COST_GUESS for new lines, passes this share of the book
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
    want = next((q for at, q in FOCUS_QTY if sku_share >= at), 2)
    return min(want, FOCUS_DEAR_QTY) if list_price > FOCUS_DEAR else want


def focus_lines(inv, catalog, focus_cats, cu):
    """[(sku, qty)] missing in the focus categories: sold-out SKUs first, then the SKU with the most market value."""
    balance = inv.get("balance") or 0
    recent = customers.recent_buys(cu, catalog)         # what sells now; until it has RECENT_MIN deals, all sales so far
    use_recent = sum(sum(s.values()) for s in recent.values()) >= RECENT_MIN
    shops = customers.recent_shop_counts(cu)
    total = sum(sum(s.values()) for s in recent.values()) if use_recent else sum(customers.popular(cu).values())
    have = {}
    for u in inv["units"]:
        if u.get("grade") != "expired":
            have[u["sku"]] = have.get(u["sku"], 0) + 1
    lines = []
    for cat in focus_cats:
        skus = recent.get(cat, {}) if use_recent else cu.get("sku_buys", {}).get(cat, {})
        fit = [s for s in skus if s in catalog and catalog[s]["list_price"] * COST_GUESS <= UNIT_SHARE * balance
               and (not use_recent or catalog[s]["list_price"] <= FOCUS_DEAR or shops.get(s, 0) >= MIN_SHOPS)]
        fit.sort(key=lambda s: -skus[s] * catalog[s]["list_price"])
        for s in fit[:FOCUS_SKUS]:
            missing = focus_want(skus[s] / total, catalog[s]["list_price"]) - have.get(s, 0)
            if missing > 0:
                lines.append((have.get(s, 0) > 0, -skus[s] * catalog[s]["list_price"], s, missing))
    return [(s, qty) for _, _, s, qty in sorted(lines)]     # sold-out SKUs first: extra units must not crowd them out


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
    mem["last_run"] = {"t": int(now), "balance": inv["balance"], "buy": buy}
    MEMORY.write_text(json.dumps(mem, ensure_ascii=False, indent=1), encoding="utf-8")
    top = sorted(customers.favourites(cu).items(), key=lambda x: -x[1])[:3]
    print(f"restock: balance {inv['balance']}, {len(inv['units'])} units, {len(cu.get('customers', {}))} customers, "
          f"favourites {top}, window {'open' if window.get('open') else 'opens in %d s' % ((window.get('opens_at') or now) - now)}, "
          f"buying {len(buy)} lines")
    print(json.dumps({"buy": buy}, ensure_ascii=False))


if __name__ == "__main__":
    main()
