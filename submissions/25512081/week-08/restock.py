"""Round 3 restock hook: decides what stock to buy. The kit runs it every 60 s and buys what the last line says.

    python restock.py --state .market

The market publishes no demand weights, so this learns them:
  - from the public ledger: every settled deal names the SKU sold; its category earns a point (decayed a little
    each run, so the counts follow what sells now);
  - from our own stock: a unit that left inventory.json without expiring was sold by us.
Before there is enough data it seeds one unit in each category that is cheap to hold (goods under SEED_PRICE,
food that keeps a week or more). After that each category's target stock follows its share of market sales.
Every PLANNER_EVERY it starts the shop manager (planner.py) in the background: a model with MCP tools
(market_tools.py) that looks into the data it needs and leaves a plan in .market/plan.json. The plan's lines are
bought first; this baseline fills the rest of the run. The tool server already checked the plan; the cash rules
are checked here again: keep CASH_FLOOR on hand and at most STOCK_CAP of credits in stock.
State lives in .market/restock_state.json (memory, not committed).
Prints {"buy": [{"sku", "qty"}, ...]} as its last line.
"""
import argparse
import json
import math
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

import forum

MARKET = "https://market.ourolabs.ai"
CASH_FLOOR = 1_500_000    # round 3's first 20 minutes: 55-60% of the market's orders ended without a deal while
STOCK_CAP = 9_000_000     # (3M until Nov 4 13:00, 6M until 14:00; raised for big-ticket stock, see VALUE_BUDGET) shops' supply buys fell from 924 to 175 per 10 min; we sold 36 units in 21 min from
                          # about 60 in stock. Demand is far above supply, so we hold more (bankruptcy line 500,000)
SEED_PRICE = 80_000        # seed goods categories whose median price is at most this
SEED_FOOD_HOURS = 168      # seed food that keeps at least a week
MIN_DATA = 30              # market settles needed before targets follow the data
TOTAL_UNITS = 160          # the stock the targets add up to, once there is data
MAX_PER_CAT = 8
UNMET_FRESH = 6 * 3600     # a category our buyer found no shop for, this recently, gets UNMET_UNITS of stock
UNMET_UNITS = 2
# Supply opens for everyone once every 20 minutes for 3 (agent.md, Nov 3). Outside a window the kit waits up to
# 90 s per line and then skips it, which kept one of our runs busy for 15 minutes: so we print no lines unless the
# window is open or opens within WINDOW_LEAD, and keep the shop manager's plan for the window. Stock bought in a
# window has to last 20 minutes, so the shop manager runs in the 10 minutes before a window.
WINDOW_LEAD = 75
PLANNER_LEAD = 10 * 60
PULSE_DECAY = 0.97         # per run: the market's no-deal share over roughly the last half hour
DECAY = 0.995              # per run (about one a minute): half-life about 2 hours
MAX_LINES = 10
MAX_LINE_COST = 4_000_000  # one category's buy never ties up more than this (one unit always allowed)
# Nov 4 13:00: 91% of the market's revenue was sofas/chairs (avg sale 1.2M), mattresses (317k) and bedding (67k);
# the top shops sell those at 0.79-0.86 of list. Our 3M of stock was 102 cheap units (avg sale 34k), so our
# revenue and profit (absolute sums, 0.3 and 0.5 of shop ALL) trailed. Stock cost now also follows each
# category's share of market revenue, not only its share of settles.
VALUE_BUDGET = 7_000_000   # 4.5M until Nov 4 14:00
# Market research, Nov 4 14:00 (1.67 h of ledger, 3,652 settles): one mattress SKU (list 424k) was 20% of all
# revenue, four leather sofas (list 1.6-2.3M) about 25%; buyers pay about the same share of list for any price
# (grade A median 0.78-0.80), so a unit's profit grows with its list price. The top shops sold
# 825-840k per sale at 0.77-0.84 of list; we sold 31.6k per sale. So within a category we stock the SKU with the
# most revenue (sales x list price), not the most sales, and size its units on that SKU's cost.
# Supply vs demand per category (Nov 4 13:40: 45% of orders unmet; market stock covered 0.1-0.9 h of sales in
# kimchi, bedding, mattresses, one sofa for 100 sold per hour, while supply opens every 20 min). Each run we
# write .market/market_state.json: settles per hour on the ledger (last hour), units all shops held when our
# buyer last saw the category, and cover = units / rate. A category whose market stock runs out before the next
# window (cover < SHORT_COVER h) may hold up to SHORT_MAX units, sized to what the market sells until then
# times SHORT_SHARE (our guess of what we can take while others are out); the seller prices on cover too.
SHORT_COVER = 1.0
SHORT_MAX = 15
SHORT_SHARE = 0.25
PLANNER_EVERY = 60 * 60    # opus with tools: the costliest call; the code baseline restocks every minute between
LESSONS_EVERY = 6 * 3600   # lessons.py: failures of the last day -> rules the manager and the buyer read
PLAN_FRESH = 90 * 60       # plan lines older than this are dropped
END_TS = 1794927540        # Round 3 ends Tue Nov 17 23:59 KST
ENDGAME = 24 * 3600        # the last day buys nothing: stock left at the end is cost already paid, so it is sold off
# Our buyer sees every competitor's stock of the categories it is ordered (.market/competitors.json): a category
# few shops hold gets more of our stock, one many shops hold gets less.
FEW_SHOPS, MANY_SHOPS = 1, 5
COMP_FRESH = 12 * 3600


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} [restock] {msg}", flush=True)


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def catalog_of(state_dir):
    c = load(state_dir / "catalog.json", {})
    if isinstance(c, list):
        c = {x["sku"]: x for x in c}
    return c


def read_ledger(st, catalog):
    """New settles since the last run -> category and SKU points."""
    seq = st.get("seq", -500)
    with httpx.Client(timeout=15) as c:
        for _ in range(20):
            try:
                rows = c.get(f"{MARKET}/api/ledger", params={"after": seq}).json()
            except (httpx.HTTPError, ValueError) as e:
                log(f"ledger read failed: {e}")
                break
            if not rows:
                break
            for r in rows:
                d = r.get("data") or {}
                if r.get("kind") == "result":             # how much of the market's demand goes unmet now
                    st["pulse"][0] += 1
                    st["pulse"][1] += 0 if d.get("receipt") else 1
                if r.get("kind") == "broken":            # market-wide break rate per grade, for warranties
                    g = st["unit_grade"].get(str(d.get("unit")), "?")
                    st["breaks"].setdefault(g, [0, 0])[0] += 1
                if r.get("kind") == "settle":
                    sku = d.get("item")
                    c_ = (catalog.get(sku) or {}).get("category")
                    if c_:
                        st["sold_ts"][c_] = (st["sold_ts"].get(c_, []) + [int(r.get("ts") or 0)])[-400:]
                    if (catalog.get(sku) or {}).get("kind") == "goods":
                        st["breaks"].setdefault(d.get("grade") or "?", [0, 0])[1] += 1
                        if d.get("unit"):
                            st["unit_grade"][str(d["unit"])] = d.get("grade") or "?"
                    lp_ = (catalog.get(sku) or {}).get("list_price")
                    if d.get("amount"):                   # the latest settles whole, for the buyers' forum posts
                        st["recent"].append({"seq": r["seq"], "ts": int(r.get("ts") or 0), "shop": d.get("shop"),
                                             "sku": sku, "amount": d["amount"], "grade": d.get("grade"),
                                             "claim": d.get("claim"), "honest": d.get("claim_honest"),
                                             "warranty": d.get("warranty_hours")})
                    if d.get("shop") and d.get("amount") and lp_:     # price per shop, for the buyers' forum
                        st["shop_px"][d["shop"]] = (st["shop_px"].get(d["shop"], []) + [[r["seq"], round(d["amount"] / lp_, 3)]])[-20:]
                    cat = (catalog.get(sku) or {}).get("category")
                    amount, lp = (r.get("data") or {}).get("amount"), (catalog.get(sku) or {}).get("list_price")
                    if cat and amount and lp:              # what the market pays, as a share of list
                        st["sku_px"][sku] = (st["sku_px"].get(sku, []) + [round(amount / lp, 3)])[-10:]
                        st["cat_px"][cat] = (st["cat_px"].get(cat, []) + [round(amount / lp, 3)])[-30:]
                    if cat and d.get("amount"):
                        st["rev"][cat] = st["rev"].get(cat, 0) + d["amount"]
                        st["sku_rev"][sku] = st["sku_rev"].get(sku, 0) + d["amount"]
                    if cat:
                        st["pop"][cat] = st["pop"].get(cat, 0) + 1
                        st["sku_pop"][sku] = st["sku_pop"].get(sku, 0) + 1
                        st["settles"] += 1
            seq = rows[-1]["seq"]
            if len(rows) < 500:
                break
    st["seq"] = seq
    st["unit_grade"] = dict(list(st["unit_grade"].items())[-5000:])
    st["recent"] = st["recent"][-600:]
    nodeal = st["pulse"][1] / st["pulse"][0] if st["pulse"][0] >= 30 else None
    tmp = Path(".market") / "pulse.tmp"
    tmp.write_text(json.dumps({"ts": time.time(), "nodeal": nodeal, "results": round(st["pulse"][0])}), encoding="utf-8")
    tmp.replace(Path(".market") / "pulse.json")
    if nodeal is not None:                       # hourly samples for the report: is there a time of day to sell?
        hour = time.strftime("%m-%d %H")
        if not st["pulse_log"] or st["pulse_log"][-1][0] != hour:
            st["pulse_log"] = (st["pulse_log"] + [[hour, round(nodeal, 3), round(st["pulse"][0])]])[-400:]
    tmp = Path(".market") / "break_rates.tmp"
    tmp.write_text(json.dumps(st["breaks"]), encoding="utf-8")
    tmp.replace(Path(".market") / "break_rates.json")


def track_my_sales(st, inv):
    now = {u["unit"]: u for u in inv.get("units", [])}
    for uid, u in st.get("units", {}).items():
        if uid not in now and (not u.get("expires_at") or u["expires_at"] > time.time()):
            st["sold"][u["category"]] = st["sold"].get(u["category"], 0) + 1
            st["sold_log"].append({"ts": int(time.time()), "sku": u["sku"], "category": u["category"],
                                   "cost": u["cost"], "grade": u["grade"]})
    st["sold_log"] = st["sold_log"][-500:]
    st["units"] = {uid: {k: u.get(k) for k in ("sku", "category", "cost", "grade", "expires_at")}
                   for uid, u in now.items()}


VALUE_SKU = {}             # category -> (SKU, units of it) from the revenue budget; filled by targets()


def targets(st, demand, catalog=None):
    """category -> how many units to hold."""
    if st["settles"] < MIN_DATA:
        return {d["category"]: 1 for d in demand
                if (d["kind"] == "goods" and d["median_price"] <= SEED_PRICE)
                or (d["kind"] == "food" and (d.get("shelf_hours") or 0) >= SEED_FOOD_HOURS)}
    total = sum(st["pop"].values()) or 1
    kind = {d["category"]: d for d in demand}
    out = {}
    for cat, p in st["pop"].items():
        n = round(TOTAL_UNITS * p / total) + (1 if st["sold"].get(cat) else 0)
        if (kind.get(cat) or {}).get("shelf_hours") and kind[cat]["shelf_hours"] <= 48:
            n = min(n, 2)                    # food that spoils in two days: never hold much
        c = competition(cat)
        if c is not None and n > 0:
            n = round(n * (1.5 if c <= FEW_SHOPS else 0.6 if c >= MANY_SHOPS else 1.0)) or 1
        if n > 0:
            out[cat] = min(n, MAX_PER_CAT)
    rev_total = sum(st["rev"].values()) or 1
    for cat, v in st["rev"].items():                # money follows revenue: a few sofas, many mattresses
        share = v / rev_total
        top = pick_sku(cat, catalog, st, (kind.get(cat) or {}).get("median_price", 0)) if catalog else None
        unit_cost = (catalog[top]["list_price"] if top else (kind.get(cat) or {}).get("median_price", 0)) * 0.65
        if share < 0.01 or not unit_cost:
            continue
        n = int(VALUE_BUDGET * share // unit_cost)
        if (kind.get(cat) or {}).get("shelf_hours") and kind[cat]["shelf_hours"] <= 48:
            n = min(n, 2)
        if top and n > 0:                            # counted against units of that SKU, not of the category:
            VALUE_SKU[cat] = (top, min(n, SHORT_MAX))  # six cheap stools must not stand in for leather sofas
    for cat, m in market_state(st).items():          # short categories: stock for what sells until the next window
        if m["cover"] is not None and m["cover"] < SHORT_COVER and cat in kind:
            short_food = kind[cat].get("shelf_hours") and kind[cat]["shelf_hours"] <= 48
            n = math.ceil(m["rate"] / 3 * SHORT_SHARE)
            out[cat] = max(out.get(cat, 0), min(n, 2 if short_food else SHORT_MAX))
    for cat, c in load(Path(".market") / "competitors.json", {}).get("cats", {}).items():
        if c.get("shops") == 0 and time.time() - c["ts"] < UNMET_FRESH:   # nobody had it when it was ordered
            short = (kind.get(cat) or {}).get("shelf_hours") and kind[cat]["shelf_hours"] <= 48
            out[cat] = max(out.get(cat, 0), 1 if short else UNMET_UNITS)
    return out


def market_state(st):
    """category -> {rate: settles per hour over the last hour, units: rivals' stock last seen, cover: hours}."""
    comp = load(Path(".market") / "competitors.json", {}).get("cats", {})
    now, out = time.time(), {}
    for cat, ts in st["sold_ts"].items():
        rate = sum(1 for t in ts if now - t < 3600)
        if not rate:
            continue
        c = comp.get(cat) or {}
        units = c.get("units") if c and now - c["ts"] < COMP_FRESH else None
        out[cat] = {"rate": rate, "units": units, "cover": round(units / rate, 2) if units is not None else None}
    tmp = Path(".market") / "market_state.tmp"
    tmp.write_text(json.dumps({"ts": now, "cats": out}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(Path(".market") / "market_state.json")
    return out


def competition(cat):
    """How many other shops held this category when our buyer last saw it (None if not seen lately)."""
    c = load(Path(".market") / "competitors.json", {}).get("cats", {}).get(cat)
    return c["shops"] if c and time.time() - c["ts"] < COMP_FRESH else None


def plan_lines(sd, st, catalog, room):
    """The shop manager's latest plan, once: lines it kept that still fit the money free now."""
    plan = load(sd / "plan.json", {})
    if not plan.get("lines") or plan.get("ts", 0) <= st.get("plan_done", 0) or time.time() - plan["ts"] > PLAN_FRESH:
        return []
    st["plan_done"] = plan["ts"]
    out = []
    for ln in plan["lines"][:MAX_LINES]:
        est = (catalog.get(ln["sku"]) or {}).get("list_price", 0) * 0.6 * ln["qty"]
        if est <= room:
            room -= est
            out.append(ln)
    log(f"plan from the shop manager: {len(out)} lines ({plan.get('reason', '')[:120]})")
    return out


def pick_sku(cat, catalog, st, median):
    """A SKU of the category: one the market has bought before, else one an unmet order accepted, else the one
    priced nearest the median (orders accept SKUs at 0.5-1.5x a reference price, so the middle fits most orders)."""
    skus = [s for s, x in catalog.items() if x.get("category") == cat]
    if not skus:
        return None
    wanted = [s for s in (load(Path(".market") / "competitors.json", {}).get("cats", {}).get(cat) or {}).get("skus", [])
              if s in catalog]
    if wanted and not any(st["sku_pop"].get(s) for s in skus):
        return min(wanted, key=lambda s: abs(catalog[s]["list_price"] - median))
    seen = [s for s in skus if st["sku_pop"].get(s)]
    recent = [s for s in skus if st.get("sku_rev", {}).get(s)]
    if recent:                                       # most recent revenue (decayed like categories, ~2 h half-life):
        return max(recent, key=lambda s: st["sku_rev"][s])   # cumulative counts favoured an older, cheaper sofa
    if seen:                                         # most revenue: sales times list price
        return max(seen, key=lambda s: st["sku_pop"][s] * catalog[s]["list_price"])
    return min(skus, key=lambda s: abs(catalog[s]["list_price"] - median))


def keep_rotated_log():
    """The kit moves market-kit.log to market-kit.log.1 at 20 MB and overwrites .1 at the next turn, which
    would lose the early Round 3 settle lines. Copy each .1 once, under the time it was rotated."""
    rot = Path("logs") / "market-kit.log.1"
    if not rot.exists():
        return
    dest = Path("logs") / time.strftime("market-kit-%m%d-%H%M.log", time.localtime(rot.stat().st_mtime))
    if not dest.exists():
        shutil.copy2(rot, dest)
        log(f"rotated kit log kept as {dest}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=".market")
    sd = Path(ap.parse_args().state)
    inv = load(sd / "inventory.json", {})
    demand = load(sd / "demand.json", [])
    catalog = catalog_of(sd)
    st = load(sd / "restock_state.json", {})
    for k, v in (("pop", {}), ("sku_pop", {}), ("sold", {}), ("sold_log", []), ("settles", 0),
                 ("sku_px", {}), ("cat_px", {}), ("breaks", {}), ("unit_grade", {}), ("shop_px", {}), ("recent", []), ("pulse", [0, 0]), ("pulse_log", []), ("rev", {}), ("sold_ts", {}), ("sku_rev", {})):
        st.setdefault(k, v)
    st["pop"] = {k: v * DECAY for k, v in st["pop"].items()}
    st["rev"] = {k: v * DECAY for k, v in st["rev"].items()}
    st["sku_rev"] = {k: v * DECAY for k, v in st["sku_rev"].items() if v > 1000}
    if not st["sku_rev"]:                            # first run: seed from the latest settles
        for r in st.get("recent", []):
            st["sku_rev"][r["sku"]] = st["sku_rev"].get(r["sku"], 0) + r["amount"]
    if not st["rev"] and st.get("recent"):           # first run with revenue tracking: seed from recent settles
        for r in st["recent"]:
            c = (catalog.get(r["sku"]) or {}).get("category")
            if c:
                st["rev"][c] = st["rev"].get(c, 0) + r["amount"]
    st["pulse"] = [x * PULSE_DECAY for x in st["pulse"]]

    try:
        keep_rotated_log()
    except OSError as e:
        log(f"keeping the rotated log failed: {e}")
    read_ledger(st, catalog)
    track_my_sales(st, inv)

    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    held = Counter(u["category"] for u in units)
    stock_cost = sum(u["cost"] for u in units)
    cash = inv.get("balance", 0) - CASH_FLOOR
    room = min(cash, STOCK_CAP - stock_cost)
    medians = {d["category"]: d["median_price"] for d in demand}
    want = targets(st, demand, catalog)
    endgame = time.time() > END_TS - ENDGAME
    if endgame:
        want, room = {}, 0
        log("last day of the round: no new stock, the seller is selling off")
    win = inv.get("restock") or {}
    opens_in = (win.get("opens_at") or 0) - time.time()
    closed = bool(win) and not win.get("open") and opens_in > WINDOW_LEAD
    if closed:
        room = 0
    if inv.get("round", 0) >= 3 and not endgame and time.time() - st.get("planner_ts", 0) > PLANNER_EVERY \
            and (not win or win.get("open") or opens_in < PLANNER_LEAD):
        st["planner_ts"] = time.time()
        subprocess.Popen([sys.executable, "planner.py"], start_new_session=True,
                         stdout=open(sd / "planner.out", "a"), stderr=subprocess.STDOUT)
        log("shop manager (planner.py) started in the background")
    if inv.get("round", 0) >= 3 and time.time() - st.get("lessons_ts", 0) > LESSONS_EVERY:
        st["lessons_ts"] = time.time()
        subprocess.Popen([sys.executable, "lessons.py"], start_new_session=True,
                         stdout=open(sd / "lessons.out", "a"), stderr=subprocess.STDOUT)
        log("lessons.py started in the background")
    try:
        forum.shop_turn(inv, log)
    except Exception as e:
        log(f"forum skipped: {e}")
    order = sorted(want, key=lambda c: (-st["rev"].get(c, 0), -(st["pop"].get(c, 0) + 5 * st["sold"].get(c, 0))))

    buy = plan_lines(sd, st, catalog, room) if not endgame and not closed else []
    room -= sum(catalog[b["sku"]]["list_price"] * 0.6 * b["qty"] for b in buy)
    held_sku = Counter(u["sku"] for u in units)
    order = sorted(set(order) | set(VALUE_SKU), key=lambda c: (-st["rev"].get(c, 0), -st["pop"].get(c, 0)))
    for cat in order:
        if len(buy) >= MAX_LINES:
            break
        if cat in VALUE_SKU:                         # the revenue budget names the SKU and its units
            sku, n = VALUE_SKU[cat]
            gap = max(n - held_sku[sku], want.get(cat, 0) - held.get(cat, 0))
        else:
            gap = want[cat] - held.get(cat, 0)
            sku = pick_sku(cat, catalog, st, medians.get(cat, 0)) if gap > 0 else None
        if gap <= 0:
            continue
        if not sku:
            continue
        unit_est = catalog[sku]["list_price"] * 0.6        # a unit costs 25-75% of list
        gap = min(gap, max(1, int(MAX_LINE_COST // unit_est)))
        gap = min(gap, int(room // unit_est))       # as many as fit: two sofas over the room used to mean none
        if gap <= 0:
            continue
        est = unit_est * gap
        room -= est
        buy.append({"sku": sku, "qty": gap})

    Path(sd / "restock_state.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    log(f"round {inv.get('round')} balance {inv.get('balance')} stock {len(units)} units / {stock_cost} cost, "
        f"market settles seen {st['settles']}, our sales {sum(st['sold'].values())}, "
        f"targets {len(want)} categories, buying {len(buy)} lines"
        + (f", supply window opens in {opens_in / 60:.1f} min" if closed else ", supply window open"))
    if buy and win and not win.get("open") and opens_in > 0:
        # the kit tries a line 3 times about 30 s apart and then skips it; on Nov 3 the third try came 0.9 s before
        # the window opened, so we wait for the window ourselves (the kit allows a hook 300 s)
        time.sleep(opens_in + 2)
    print(json.dumps({"buy": buy}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
