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
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

import forum

MARKET = "https://market.ourolabs.ai"
CASH_FLOOR = 2_000_000
STOCK_CAP = 2_500_000
SEED_PRICE = 80_000        # seed goods categories whose median price is at most this
SEED_FOOD_HOURS = 168      # seed food that keeps at least a week
MIN_DATA = 30              # market settles needed before targets follow the data
TOTAL_UNITS = 80           # the stock the targets add up to, once there is data
MAX_PER_CAT = 5
DECAY = 0.995              # per run (about one a minute): half-life about 2 hours
MAX_LINES = 10
MAX_LINE_COST = 300_000    # one category's buy never ties up more than this (one unit always allowed)
PLANNER_EVERY = 20 * 60
LESSONS_EVERY = 6 * 3600   # lessons.py: failures of the last day -> rules the manager and the buyer read
PLAN_FRESH = 40 * 60       # plan lines older than this are dropped


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
                if r.get("kind") == "settle":
                    sku = (r.get("data") or {}).get("item")
                    cat = (catalog.get(sku) or {}).get("category")
                    amount, lp = (r.get("data") or {}).get("amount"), (catalog.get(sku) or {}).get("list_price")
                    if cat and amount and lp:              # what the market pays, as a share of list
                        st["sku_px"][sku] = (st["sku_px"].get(sku, []) + [round(amount / lp, 3)])[-10:]
                        st["cat_px"][cat] = (st["cat_px"].get(cat, []) + [round(amount / lp, 3)])[-30:]
                    if cat:
                        st["pop"][cat] = st["pop"].get(cat, 0) + 1
                        st["sku_pop"][sku] = st["sku_pop"].get(sku, 0) + 1
                        st["settles"] += 1
            seq = rows[-1]["seq"]
            if len(rows) < 500:
                break
    st["seq"] = seq


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


def targets(st, demand):
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
        if n > 0:
            out[cat] = min(n, MAX_PER_CAT)
    return out


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
    """A SKU of the category: one the market has bought before, else the one priced nearest the median
    (orders accept SKUs at 0.5-1.5x a reference price, so the middle fits most orders)."""
    skus = [s for s, x in catalog.items() if x.get("category") == cat]
    if not skus:
        return None
    seen = [s for s in skus if st["sku_pop"].get(s)]
    if seen:
        return max(seen, key=lambda s: st["sku_pop"][s])
    return min(skus, key=lambda s: abs(catalog[s]["list_price"] - median))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=".market")
    sd = Path(ap.parse_args().state)
    inv = load(sd / "inventory.json", {})
    demand = load(sd / "demand.json", [])
    catalog = catalog_of(sd)
    st = load(sd / "restock_state.json", {})
    for k, v in (("pop", {}), ("sku_pop", {}), ("sold", {}), ("sold_log", []), ("settles", 0),
                 ("sku_px", {}), ("cat_px", {})):
        st.setdefault(k, v)
    st["pop"] = {k: v * DECAY for k, v in st["pop"].items()}

    read_ledger(st, catalog)
    track_my_sales(st, inv)

    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    held = Counter(u["category"] for u in units)
    stock_cost = sum(u["cost"] for u in units)
    cash = inv.get("balance", 0) - CASH_FLOOR
    room = min(cash, STOCK_CAP - stock_cost)
    medians = {d["category"]: d["median_price"] for d in demand}
    want = targets(st, demand)
    if inv.get("round", 0) >= 3 and time.time() - st.get("planner_ts", 0) > PLANNER_EVERY:
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
    order = sorted(want, key=lambda c: (-(st["pop"].get(c, 0) + 5 * st["sold"].get(c, 0)), medians.get(c, 0)))

    buy = plan_lines(sd, st, catalog, room)
    room -= sum(catalog[b["sku"]]["list_price"] * 0.6 * b["qty"] for b in buy)
    for cat in order:
        gap = want[cat] - held.get(cat, 0)
        if gap <= 0 or len(buy) >= MAX_LINES:
            continue
        sku = pick_sku(cat, catalog, st, medians.get(cat, 0))
        if not sku:
            continue
        unit_est = catalog[sku]["list_price"] * 0.6        # a unit costs 25-75% of list
        gap = min(gap, max(1, int(MAX_LINE_COST // unit_est)))
        est = unit_est * gap
        if est > room:
            continue
        room -= est
        buy.append({"sku": sku, "qty": gap})

    Path(sd / "restock_state.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    log(f"round {inv.get('round')} balance {inv.get('balance')} stock {len(units)} units / {stock_cost} cost, "
        f"market settles seen {st['settles']}, our sales {sum(st['sold'].values())}, "
        f"targets {len(want)} categories, buying {len(buy)} lines")
    print(json.dumps({"buy": buy}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
