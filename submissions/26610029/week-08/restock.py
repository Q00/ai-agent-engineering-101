"""real_yellow's restock hook (the kit runs it every 60 s in Round 3).

    python restock.py --state .market

Before anyone orders, decide what to hold until the next supply window (one 3-minute window every 20 min):
1. refresh the shared DB from the market (collector),
2. rank SKUs by how much they sold across the market in the last hours times the margin they leave
   (going rate minus the expected cost of a randomly graded unit), skipping SKUs whose cost is surging,
3. top each picked SKU up to a small target, inside a per-window share of the balance.
Food is held only where it sells fast; goods may be held deeper, they only pay 1% a day.
"""
import argparse
import json
import time
from pathlib import Path

import collector
from db import DB

# expected unit cost as a share of list price, given the market's grade odds (agent.md: Supply)
EXPECTED_COST = {"goods": 0.70 * 0.65 + 0.20 * 0.50 + 0.10 * 0.35,
                 "food": 0.25 * 0.65 + 0.50 * 0.50 + 0.25 * 0.35}
DEFAULT_POLICY = {"window_budget": 0.10,   # share of the balance one window may spend
                  "lookback_h": 3,          # how far back "what sells" looks
                  "max_skus": 10,           # the kit buys at most 10 lines a run
                  "goods_target": 4, "food_target": 2,
                  "food_min_sales": 6,      # food only for SKUs that sold at least this often in the lookback
                  "goods_min_sales": 3,
                  "max_units": 80}          # never hold more than this many units in total

# Horvitz threshold for a shop promo post: post a "hot drop" at once when we just bought something
# that is clearly selling fast elsewhere (high usefulness), otherwise only after staying quiet a while
# (threshold drops with quiet_bonus past quiet_after) -- never spam, never go silent either.
DEFAULT_PROMO = {"hot_usefulness": 0.9, "base_usefulness": 0.3,
                 "threshold": 0.5, "quiet_bonus": 0.3, "quiet_after": 1200, "post_every": 300}
EMOJI_BY_KIND = {"food": "🍊", "goods": "🛋️"}


def plan(inv, d, policy, now):
    units = inv.get("units", [])
    if len(units) >= policy["max_units"]:
        return [], "stock is full"
    held = {}
    for u in units:
        held[u["sku"]] = held.get(u["sku"], 0) + 1
    surging = {s["sku"] for s in (inv.get("surge") or []) if s.get("factor", 1) > 1.05}

    rows = d.q("SELECT sku, max(kind) AS kind, max(list_price) AS lp, count(*) AS n, "
               "avg(amount::float / list_price) AS ratio FROM settles "
               "WHERE ts > $1 AND list_price > 0 GROUP BY sku", now - policy["lookback_h"] * 3600)
    ranked = []
    for r in rows:
        kind, lp, n, ratio = r["kind"], int(r["lp"]), int(r["n"]), float(r["ratio"])
        margin = ratio - EXPECTED_COST.get(kind, 0.6)
        if margin <= 0.02 or r["sku"] in surging:
            continue
        if n < (policy["food_min_sales"] if kind == "food" else policy["goods_min_sales"]):
            continue
        ranked.append((n * margin, r["sku"], kind, lp, n, round(margin, 3)))   # fast movers first, not big tickets
    food = sorted((x for x in ranked if x[2] == "food"), reverse=True)
    goods = sorted((x for x in ranked if x[2] != "food"), reverse=True)
    ranked = [x for pair in zip(goods, food) for x in pair] + goods[len(food):] + food[len(goods):]
    # alternate: food sells fastest but spoils; goods keep, and carry the warranty sales

    budget = inv.get("balance", 0) * policy["window_budget"]
    per_sku_cap = budget * 0.3                          # one sofa must not eat the whole window
    buy, why = [], []
    for value, sku, kind, lp, n, margin in ranked:
        if lp * 0.75 > per_sku_cap:
            continue
        target = policy["goods_target"] if kind == "goods" else policy["food_target"]
        qty = target - held.get(sku, 0)
        unit_cost = lp * 0.75                           # worst case: an A unit at the top of its range
        qty = min(qty, int(budget // unit_cost)) if unit_cost else 0
        if qty < 1:
            continue
        buy.append({"sku": sku, "qty": qty})
        why.append({"sku": sku, "kind": kind, "list": lp, "sold": n, "margin": margin, "qty": qty})
        budget -= qty * unit_cost
        if len(buy) >= policy["max_skus"]:
            break
    return buy, why


def promo_text(inv, why, now):
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
    return ("🏪 real_yellow 오늘의 재고\n" + "\n".join(lines) +
            "\n✅ claim=실제 등급 그대로, 속인 적 없음 (ledger로 검증 가능) | ⏱️ 비식품 72h 보증\n"
            "느린 흥정 싫으면 real_yellow 👋")


def shop_forum(d, inv, why, now, md):
    policy = dict(DEFAULT_PROMO, **d.get_policy("shop_forum", {}))
    last = float(d.get_state("last_post_shop", 0))
    if now - last < policy["post_every"]:
        return
    hot = any(x["margin"] >= 0.1 and x["sold"] >= 50 for x in why)
    usefulness = policy["hot_usefulness"] if hot else policy["base_usefulness"]
    bar = policy["threshold"] - (policy["quiet_bonus"] if now - last > policy["quiet_after"] else 0)
    if usefulness < bar:
        print(f"[forum] shop skip: usefulness {usefulness} < {bar}")
        return
    text = promo_text(inv, why, now)
    if not text:
        print("[forum] shop skip: no live stock to show")
        return
    with open(md / "outbox.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"post": text, "as": "shop"}, ensure_ascii=False) + "\n")
    d.set_state("last_post_shop", now)
    d.log_trajectory(now, "shop", "forum_post", {"usefulness": usefulness, "bar": bar, "hot": hot}, {"text": text})
    print(f"[forum] shop posted (usefulness {usefulness} >= {bar}):\n{text}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=".market")
    md = Path(ap.parse_args().state)
    inv = json.loads((md / "inventory.json").read_text(encoding="utf-8"))
    now = time.time()

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

    with DB() as d:
        policy = dict(DEFAULT_POLICY, **d.get_policy("restock", {}))
        buy, why = plan(inv, d, policy, now)
        d.log_trajectory(now, "shop", "restock", {"balance": inv.get("balance"), "units": len(inv.get("units", [])),
                                                    "window": w, "policy": policy}, {"buy": why})
        try:
            shop_forum(d, inv, why, now, md)
        except Exception as e:
            print(f"[forum] shop promo failed: {type(e).__name__}: {e}")
    for x in why:
        print(f"[restock] {x['qty']} x {x['sku']} ({x['kind']}, list {x['list']}, sold {x['sold']}, margin {x['margin']})")
    print(json.dumps({"buy": buy}))


if __name__ == "__main__":
    main()
