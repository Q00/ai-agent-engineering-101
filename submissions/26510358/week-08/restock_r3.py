"""Round 3 restock hook (the kit runs it every 60 s): keeps memory fresh, starts the forum agent when due, and
prints what to buy.

    python3 restock_r3.py --state {state}

For each category:
  rate   = market-wide settles per hour, decayed (memory.category_demand), plus forum want-to-buy signals
  share  = 1 / (1 + competitors holding stock in that category), or our realised share when higher (scout)
  gap    = competitor supply vs market sales from the ledger (scout.gap_factor, 0.7-1.3)
  target = ceil(rate * share * gap * HORIZON_H) units, capped for short-life food by half its shelf life
Categories without evidence get one unit for coverage (long-life food, cheap goods), within EXPLORE_SHARE of the
stock budget, so orders can find us while the ledger fills. No buying in the last STOP_BUYING_HOURS.

The plan itself is rule-based and answers within the hook's time. A background ReAct review
(restock_review_r3.py, every RESTOCK_REVIEW_EVERY_MIN) may scale a category's demand target by a clamped factor
(0 = skip it, also for coverage), e.g. after poor sell-through or near-expiry stock; factors expire after
ADJUST_TTL_H, so the rules take over again if the review stops.
"""
import argparse
import base64
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from r3lib import config, market, pricing, scout
from r3lib.memory import Memory
from r3lib.org import client

FORUM_EVERY = 10 * 60


def active_adjustments(mem, now=None):
    a = mem.get("restock_adjust") or {}
    if (now or time.time()) - (a.get("ts") or 0) > config.ADJUST_TTL_H * 3600:
        return {}
    return a.get("items") or {}


def my_pubkey():
    try:
        key = serialization.load_pem_private_key((config.ROOT / "shop_key.pem").read_bytes(), password=None)
        raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return base64.b64encode(raw).decode()
    except (OSError, ValueError):
        return None


def kick(mem, script, key, every_s):
    """Start a background job (forum agent, restock review) at most once per every_s."""
    if time.time() - (mem.get(key) or 0) < every_s:
        return
    mem.put(key, time.time())
    log = open(config.STATE_DIR / f"{Path(script).stem}-run.log", "a")
    subprocess.Popen([sys.executable, str(config.ROOT / script)], cwd=config.ROOT, stdout=log, stderr=log,
                     start_new_session=True, env={k: v for k, v in os.environ.items() if k != "MARKET_OWNER"})


def ensure_org_server(mem):
    """Start the internal A2A server (r3lib/org/server.py) when it is not running; at most once a minute."""
    if not config.ORG_A2A or client.server_port():
        return
    if time.time() - (mem.get("org_kick") or 0) < 60:
        return
    mem.put("org_kick", time.time())
    log = open(config.STATE_DIR / "org-server.log", "a")
    subprocess.Popen([sys.executable, "-m", "r3lib.org.server", "--port", "0"], cwd=config.ROOT, stdout=log,
                     stderr=log, start_new_session=True,
                     env={k: v for k, v in os.environ.items() if k != "MARKET_OWNER"})


def kick_forum(mem):
    kick(mem, "forum_r3.py", "forum_kick", FORUM_EVERY)


def window_near(inv, now=None, live=None):
    """True when supply is open or opens within RESTOCK_LEAD_S, judged on the market's clock: GET /api/restock gives
    open, opens_at and the server's now (2026-11-04: this machine ran 163 s fast without NTP, so a run 47 s before
    the window by our clock was 148 s before it by the market's, and the kit skipped every line). Falls back to
    inventory.json and our clock when the endpoint does not answer; True without any restock info."""
    if live is None and now is None:
        live = market.get_json("/api/restock")
    if isinstance(live, dict) and live.get("now"):
        r, now = live, live["now"]
    else:
        r = inv.get("restock") or {}
        now = now or time.time()
    if not r:
        return True
    if r.get("open"):
        return True
    if r.get("closes_at") and now > r["closes_at"]:
        return False                                      # stale: that window is over, the next is not posted yet
    return (r.get("opens_at") or 0) - now <= config.RESTOCK_LEAD_S


def refresh_harsh_styles(mem, now=None):
    """Buyer styles whose ratings of our deals average at or below HARSH_MAX_STARS; the shop declines them."""
    now = now or time.time()
    if now - (mem.get("harsh_at") or 0) < config.HARSH_EVERY_MIN * 60:
        return None
    mem.put("harsh_at", now)
    stats = mem.style_stars(min_n=config.HARSH_MIN_REVIEWS)
    harsh = {k: v for k, v in stats.items() if v[1] <= config.HARSH_MAX_STARS}
    if set(harsh) - set(mem.get("harsh_styles") or {}):
        mem.note("harsh_style", ",".join(sorted(harsh)), f"declining buyer styles {harsh} (ratings, mean stars)", 0.5)
    mem.put("harsh_styles", harsh)
    return harsh


def unit_cost_estimate(info):
    return info["list_price"] * 0.75            # worst case (grade A top): the market refuses a lot above balance


def pick_sku(category, info_by_cat, mem, median_price, explore):
    """Best-selling SKU in the category (memory), else the SKU closest to the category median price, which sits
    inside most orders' acceptable band (0.5-1.5 x a reference price)."""
    skus = info_by_cat.get(category) or []
    counts = mem.sku_counts(category)
    sold = [s for s in counts if any(x == s for x, _ in skus)]
    if sold and not explore:
        return sold[0]
    pool = [(s, i) for s, i in skus if not explore or
            (i.get("kind") == "food" and (i.get("shelf_hours") or 0) >= config.MIN_FOOD_SHELF_FOR_EXPLORE) or
            (i.get("kind") == "goods" and i["list_price"] <= config.EXPLORE_MAX_LIST)]
    if not pool:
        return None
    return min(pool, key=lambda x: abs(x[1]["list_price"] - (median_price or x[1]["list_price"])))[0]


def plan(inv, demand_rows, mem, now=None, dry_run=False, adjust=None):
    now = now or time.time()
    adjust = active_adjustments(mem, now) if adjust is None else adjust
    cat = market.catalog()
    info_by_cat = {}
    for s, i in cat.items():
        info_by_cat.setdefault(i["category"], []).append((s, i))
    units = inv.get("units") or []
    have, stock_cost, cat_cost = {}, 0, {}
    for u in units:
        if u.get("grade") != "expired":
            have[u["category"]] = have.get(u["category"], 0) + 1
            stock_cost += u["cost"]
            cat_cost[u["category"]] = cat_cost.get(u["category"], 0) + u["cost"]
    balance = inv.get("balance") or 0
    stock_max = config.stock_budget(balance)
    budget = min(balance - config.CASH_RESERVE, stock_max - stock_cost)
    explore_budget = min(budget, config.EXPLORE_SHARE * config.MAX_STOCK_COST - (mem.get("explore_spent") or 0))
    dem = mem.category_demand(now=now)
    forum = mem.forum_demand_signals()
    view = scout.category_view(mem, now=now)
    stockouts = mem.stockout_rates(now=now)
    lines, why = [], []

    # 1) evidence: categories that sell, by expected profit per hour
    needs = []
    for row in demand_rows:
        k = row["category"]
        d = dem.get(k) or {}
        rate = d.get("rate", 0) + 0.05 * forum.get(market.leaf(k), 0)
        missed = stockouts.get(k, 0.0)                    # orders that came to us and found nothing
        if rate <= 0 and missed <= 0:
            continue
        share = scout.share_estimate(1 / (1 + (d.get("shops") or 0)), view.get(k))
        gap = scout.gap_factor(view.get(k))
        exp_h = rate * share * gap + missed
        factor = (adjust.get(k) or {}).get("factor", 1.0)
        target = math.ceil(exp_h * config.HORIZON_H * factor)
        shelf = [i.get("shelf_hours") for _, i in info_by_cat.get(k, []) if i.get("shelf_hours")]
        if row.get("kind") == "food" and shelf and min(shelf) < 720:
            target = min(target, math.ceil(exp_h * min(shelf) * 0.5 * factor))
        ratio, n = mem.price_ratio(category=k)
        if ratio is not None and ratio < 0.65:
            why.append({"category": k, "skip": f"market pays {ratio:.2f} of list, no margin over cost"})
            continue
        need = target - have.get(k, 0)
        if factor != 1.0 or gap != 1.0 or missed:
            why.append({"category": k, "review_factor": factor, "gap_factor": gap, "share": round(share, 3),
                        "stockouts_per_h": round(missed, 2),
                        "pressure": (view.get(k) or {}).get("pressure"), "target": target})
        if need > 0:
            margin = (ratio or config.EV_DEFAULT_PRICE_RATIO) - config.EV_COST_RATIO
            needs.append((exp_h * row.get("median_price", 0) * margin, k, need, row, exp_h * factor, ratio))
    needs.sort(key=lambda x: -x[0])                       # expected margin per hour, not revenue
    for score, k, need, row, mu_h, ratio in needs:
        sku = pick_sku(k, info_by_cat, mem, row.get("median_price"), explore=False)
        if not sku:
            continue
        info = cat[sku]
        L = info["list_price"]
        cost = L * config.EV_COST_RATIO
        price = L * (ratio or config.EV_DEFAULT_PRICE_RATIO)
        loss = pricing.hold_loss(info.get("kind"), cost, info.get("shelf_hours"))
        ev_qty, evs = pricing.ev_quantity(have.get(k, 0), mu_h * config.HORIZON_H, price, cost, loss)
        deep = (dem.get(k) or {}).get("rate", 0) >= config.DEEP_CATEGORY_RATE
        cat_cap = (config.DEEP_CATEGORY_SHARE if deep else config.CATEGORY_MAX_SHARE) * stock_max
        room = int(max(0, cat_cap - cat_cost.get(k, 0)) // unit_cost_estimate(info))
        qty = min(20, need, ev_qty, room)
        if qty <= 0:
            why.append({"category": k, "sku": sku, "skip": "no unit with positive expected profit" if ev_qty <= 0
                        else f"category stock cost {cat_cost.get(k, 0):.0f} at the {cat_cap / stock_max:.0%} cap",
                        "rule_need": need, "ev_qty": ev_qty})
            continue
        est = unit_cost_estimate(cat[sku]) * qty
        while qty > 1 and est > budget:
            qty -= 1
            est = unit_cost_estimate(cat[sku]) * qty
        if est > budget or len(lines) >= 10:
            continue
        budget -= est
        lines.append({"sku": sku, "qty": qty})
        cat_cost[k] = cat_cost.get(k, 0) + est
        why.append({"category": k, "sku": sku, "qty": qty, "reason": "demand", "score": round(score),
                    "rule_need": need, "ev_qty": ev_qty, "ev_first_units": evs[:3], "cap_room": room, "deep": deep})

    # 2) coverage: one unit where we have nothing and there is no evidence yet, most SKUs first
    spent = 0
    for row in sorted(demand_rows, key=lambda r: -r.get("skus", 0)):
        k = row["category"]
        if len(lines) >= 10 or have.get(k) or (dem.get(k) or {}).get("n") or \
                (adjust.get(k) or {}).get("factor", 1.0) == 0:
            continue
        sku = pick_sku(k, info_by_cat, mem, row.get("median_price"), explore=True)
        if not sku:
            continue
        est = unit_cost_estimate(cat[sku])
        if est > min(budget, explore_budget - spent):
            continue
        spent += est
        budget -= est
        lines.append({"sku": sku, "qty": 1})
        why.append({"category": k, "sku": sku, "qty": 1, "reason": "coverage"})
    if spent and not dry_run:
        mem.put("explore_spent", (mem.get("explore_spent") or 0) + spent)
    return lines, why, {"balance": balance, "stock_cost": stock_cost, "stock_budget": stock_max, "units": len(units)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=str(config.MARKET_DIR))
    a = ap.parse_args()
    md = Path(a.state)
    mem = Memory()
    try:
        mem.sync_ledger()
        mem.sync_shops({my_pubkey()} - {None})
    except Exception as e:
        print(json.dumps({"event": "memory_sync_error", "error": str(e)[:200]}), flush=True)
    try:
        kick_forum(mem)
        if (market.read_json("inventory.json", {}, md) or {}).get("round", 0) >= 3:
            kick(mem, "restock_review_r3.py", "review_kick", config.RESTOCK_REVIEW_EVERY_MIN * 60)
    except Exception as e:
        print(json.dumps({"event": "forum_kick_error", "error": str(e)[:200]}), flush=True)
    try:
        ensure_org_server(mem)
    except Exception as e:
        print(json.dumps({"event": "org_server_error", "error": str(e)[:200]}), flush=True)
    try:
        out = client.call("hook", "ceo", "tick", {"state": str(md)}, mem=mem)
    except Exception as e:                                # a broken organisation must not stop the kit
        print(json.dumps({"event": "org_tick_error", "error": str(e)[:300]}), flush=True)
        out = {"buy": []}
    if time.time() - (mem.get("kpi_at") or 0) > config.KPI_EVERY_MIN * 60:
        mem.put("kpi_at", time.time())
        try:
            client.call("hook", "ceo", "kpi", {}, mem=mem)
        except Exception as e:
            print(json.dumps({"event": "kpi_error", "error": str(e)[:200]}), flush=True)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
