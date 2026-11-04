"""Background ReAct review of the restock plan (started by restock_r3.py every RESTOCK_REVIEW_EVERY_MIN).

The rule-based plan sizes stock by demand rate x share x horizon. It cannot tell, for example, that a category
it keeps buying is not selling for *us* (sell-through), that food on hand will expire before it sells, or that
goods sit in storage at 1% of cost per day. The ReAct agent looks at those through read-only tools and proposes
a factor per category; this script clamps it to [ADJUST_MIN, ADJUST_MAX], keeps only categories in demand.json
and stores it for ADJUST_TTL_H. It never buys, prices or sells anything itself.

    python3 restock_review_r3.py
"""
import json
import time

from r3lib import config, market, oracle, primer, react, scout
from r3lib.memory import Memory
from restock_r3 import active_adjustments, plan

GOAL = (
    "You review a shop's restock plan in an agent marketplace. The plan is computed by rules from market-wide "
    "demand. Unsold food is thrown away at cost; goods cost 1% of their cost per day in storage; stock that never "
    "sells is a loss in net profit, the shop's main score. Find categories where the rules are likely wrong for "
    "OUR shop: low sell-through of what we requested, stock that will expire before it sells, goods held for days, "
    "or strong forum demand the ledger does not show yet. Propose a factor per such category: 0 stops buying it, "
    "0.5 halves the target, 1.3 raises it. Leave every other category out (factor 1). Prefer few, well-supported "
    "changes; give the observation behind each reason.")
FINAL = '{"adjustments": [{"category": "<full category from the plan>", "factor": 0.0-1.5, "reason": "..."}]}'


def tools(mem, inv, demand):
    def plan_preview():
        lines, why, state = plan(inv, demand, mem, dry_run=True, adjust={})
        return {"buy": lines, "why": why, "state": state}

    def sell_through(hours=24):
        return mem.sell_through(hours=max(1, min(72, int(hours))))

    def stock_on_hand():
        now, out = time.time(), {}
        for u in inv.get("units") or []:
            d = out.setdefault(u.get("category"), {"units": 0, "cost": 0, "min_hours_to_expiry": None,
                                                   "max_days_held": 0})
            d["units"] += 1
            d["cost"] += u.get("cost") or 0
            if u.get("expires_at"):
                h = round((u["expires_at"] - now) / 3600, 1)
                d["min_hours_to_expiry"] = h if d["min_hours_to_expiry"] is None else min(h, d["min_hours_to_expiry"])
            if u.get("kind") == "goods" and u.get("supplied_at"):
                d["max_days_held"] = max(d["max_days_held"], round((now - u["supplied_at"]) / 86400, 2))
        return out

    def category_evidence(category):
        d = mem.category_demand().get(category) or {}
        ratio, n = mem.price_ratio(category=category)
        return {"settles_per_hour": round(d.get("rate", 0), 3), "settles_48h": d.get("n", 0),
                "competitor_shops_with_stock": d.get("shops", 0), "price_to_list": ratio, "price_evidence": n,
                "forum_wanted": mem.forum_demand_signals().get(market.leaf(category), 0)}

    return [react.Tool("plan_preview", "what the rules would buy now, with reasons, before any review", plan_preview),
            react.Tool("sell_through", "per category: units our restock requested vs units our shop sold",
                       sell_through, {"hours": "int <= 72"}),
            react.Tool("stock_on_hand", "per category: units, cost, hours until the first food expires, days goods "
                       "have been held", stock_on_hand),
            primer.tool(), react.recall_tool(mem), scout.tool(mem), oracle.tool(mem),
            react.Tool("category_evidence", "market demand, competition, price/list and forum signals for one category",
                       category_evidence, {"category": "full category string"})]


def clamp(final, categories):
    items = {}
    for a in (final or {}).get("adjustments") or []:
        if not isinstance(a, dict) or a.get("category") not in categories:
            continue
        try:
            f = float(a.get("factor"))
        except (TypeError, ValueError):
            continue
        f = round(min(config.ADJUST_MAX, max(config.ADJUST_MIN, f)), 2)
        if f != 1.0:
            items[a["category"]] = {"factor": f, "reason": str(a.get("reason") or "")[:300]}
        if len(items) >= 10:
            break
    return items


def main(run=react.run):
    mem = Memory()
    inv = market.read_json("inventory.json", {}) or {}
    demand = market.read_json("demand.json", []) or []
    if (inv.get("round") or 0) < 3 or not demand:
        print(json.dumps({"event": "review_skip", "why": "not round 3 or no demand.json"}), flush=True)
        return
    final, trace = run(GOAL, tools(mem, inv, demand), FINAL, purpose="restock_review",
                       context={"current_adjustments": active_adjustments(mem)},
                       max_steps=config.REACT_MAX_STEPS, budget_s=config.REACT_BUDGET_S,
                       prefetch=("plan_preview", "sell_through", "stock_on_hand"))
    if final is None:                               # keep the old factors until they expire
        print(json.dumps({"event": "review_failed", "steps": len(trace)}), flush=True)
        return
    items = clamp(final, {r.get("category") for r in demand})
    mem.put("restock_adjust", {"ts": time.time(), "items": items})
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.LOG_DIR / "restock-review.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": round(time.time()), "proposed": final, "applied": items, "steps": len(trace)},
                           ensure_ascii=False) + "\n")
    print(json.dumps({"event": "review_done", "applied": items}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
