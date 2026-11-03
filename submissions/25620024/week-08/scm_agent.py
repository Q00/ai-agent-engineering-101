"""Week 08 — scm_agent.py: the supply chain team (Codex), run by marketing_agent.py once an hour.

It owns stock: what to order in the next supply windows, what to clear before it decays, what to watch.
Like the other teams it learns from its own outcomes (Hermes: run -> compare -> memory -> revise):
  scorecard   since its last plan: units bought and sold, sell-through, units expired, cash, storage per day
  lessons     keep / undo / watch, fed back into every later SCM brief; a curator retires stale ones (48 h, 20 kept)
  plan        private/scm_plan.json: orders (restock.py buys them) and clearance markdowns (promo.py applies them);
              the shop's guards still hold: cash floor, spend per window, never below cost x (1 + clear floor)
"""
import json
import time
from pathlib import Path

import market_model
import promo

HERE = Path(__file__).parent
MD = HERE / ".market"
OUT = HERE / "private" / "marketing"
PLAN = HERE / "private" / "scm_plan.json"
HISTORY = OUT / "scm_history.jsonl"
LESSONS = OUT / "scm_lessons.json"

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["orders", "clearance", "alerts", "lessons", "retire_lessons", "rationale"],
    "properties": {
        "orders": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                              "required": ["sku", "qty", "why"],
                                              "properties": {"sku": {"type": "string"}, "qty": {"type": "integer"},
                                                             "why": {"type": "string"}}}},
        "clearance": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                 "required": ["sku", "markdown", "why"],
                                                 "properties": {"sku": {"type": "string"},
                                                                "markdown": {"type": "number"},
                                                                "why": {"type": "string"}}}},
        "alerts": {"type": "array", "items": {"type": "string"}},
        "lessons": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                               "required": ["text", "verdict"],
                                               "properties": {"text": {"type": "string"},
                                                              "verdict": {"type": "string",
                                                                          "enum": ["keep", "undo", "watch"]}}}},
        "retire_lessons": {"type": "array", "items": {"type": "integer"}},
        "rationale": {"type": "string"},
    },
}

PROMPT = """You are the supply chain (SCM) team of an agent shop named "dragonash" in a classroom agent market.
Profit = balance - 5,000,000: stock bought counts as spent until it sells; food spoils (cost lost) once past its
shelf life and its grade drops while it waits; goods cost 1% of their cost per day in storage. Supply opens for
3 minutes every 20 (the shop buys at most ~10 lines per window, up to 20 units per line). The sales team (Claude) is
the brain of the shop: you work for it, to maximise its sales and profit; it may veto your orders. Do its requests
(brief.sales_requests) first. You own stock. From the brief:
- orders: what to hold for the next hour (sku, qty 1-10): fast sellers that will sell before they decay, the sales
  team's picks and regulars' categories if they sell; never more than about 6-12 h of our own sales per SKU,
  and keep cash for high-ticket picks
- clearance: SKUs to sell off before they decay or because they sit too long: markdown 0.0-0.30 (the shop never
  goes below cost x 1.10 anyway)
- alerts: what the owner should know (spoilage soon, dead stock, cash too low)
- lessons: judge your last plan with brief.scorecard and brief.history (1-3 lessons, verdict keep / undo /
  watch); brief.lessons are your standing lessons with their index: follow them unless the scorecard contradicts
  them; list stale or wrong ones in retire_lessons. Change at most 3 orders/markdowns against brief.current_plan
  per run so each outcome can be traced.
- rationale: 3-5 sentences.
Answer with the JSON object only.

BRIEF:
"""


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def stock_view(c, now):
    inv = load(MD / "inventory.json", {})
    cat = market_model.catalog(MD)
    rates = market_model.our_sales(c, hours=12)
    by = {}
    for u in inv.get("units", []):
        s = by.setdefault(u["sku"], {"units": 0, "expired": 0, "cost": 0.0, "grades": "", "age_h": 0,
                                     "min_shelf_h": None})
        if u.get("grade") == "expired":
            s["expired"] += 1
            continue
        s["units"] += 1
        s["cost"] += u.get("cost") or 0
        s["grades"] += u.get("grade") or "?"
        s["age_h"] = max(s["age_h"], round((now - (u.get("supplied_at") or now)) / 3600, 1))
        if u.get("expires_at"):
            left = round((u["expires_at"] - now) / 3600, 1)
            s["min_shelf_h"] = left if s["min_shelf_h"] is None else min(s["min_shelf_h"], left)
    out = {}
    comp = market_model.competitor_stock(c)
    for sku, s in by.items():
        r = rates.get(sku, 0)
        meta = cat.get(sku) or {}
        cover = round(s["units"] / r, 1) if r else None
        spoil = None
        if meta.get("kind") == "food" and s["min_shelf_h"] is not None:
            spoil = max(0, round(s["units"] - r * s["min_shelf_h"], 1))
        out[sku] = {"name": (meta.get("name") or "")[:36], "kind": meta.get("kind"), "list": meta.get("list_price"),
                    "units": s["units"], "expired_units": s["expired"], "avg_cost": round(s["cost"] / s["units"]) if s["units"] else None,
                    "grades": "".join(sorted(s["grades"])), "oldest_h": s["age_h"], "min_shelf_h": s["min_shelf_h"],
                    "sold_per_h_12h": round(r, 2), "cover_h": cover, "spoil_risk_units": spoil,
                    "storage_per_day": round(0.01 * s["cost"]) if meta.get("kind") == "goods" else 0,
                    "market_sold_24h": c.execute("select count(*) from settles where sku=? and ts>?",
                                                 (sku, now - 86400)).fetchone()[0],
                    "competitor_units": comp.get(sku, 0)}
    return inv, out


def measure(c, inv, stock, since):
    bought = c.execute("select coalesce(sum(qty), 0), coalesce(sum(total), 0) from supplies where shop=? and ts>?",
                       (market_model.SHOP_NAME, since)).fetchone()
    sold = c.execute(f"select count(*), coalesce(sum(amount), 0) from settles where ts>? and seq in ({market_model.ours(c)})",
                     (since,)).fetchone()
    return {"ts": time.time(), "balance": inv.get("balance"), "units_in_stock": sum(s["units"] for s in stock.values()),
            "expired_units": sum(s["expired_units"] for s in stock.values()),
            "stock_cost": sum((s["avg_cost"] or 0) * s["units"] for s in stock.values()),
            "bought_units": bought[0], "bought_cost": bought[1], "sold_units": sold[0], "sold_revenue": sold[1]}


def scorecard(now_m, last):
    if not last:
        return {"note": "first SCM plan with a scorecard"}
    m0 = last["metrics"]
    h = max(0.1, (now_m["ts"] - m0["ts"]) / 3600)
    sold, bought = now_m["sold_units"], now_m["bought_units"]
    return {"hours": round(h, 2), "last_changes": last.get("changes"), "bought_units": bought,
            "bought_cost": now_m["bought_cost"], "sold_units": sold, "sold_revenue": now_m["sold_revenue"],
            "sell_through": round(sold / bought, 2) if bought else None,
            "expired_units_added": now_m["expired_units"] - m0.get("expired_units", 0),
            "stock_cost_change": now_m["stock_cost"] - m0.get("stock_cost", 0),
            "balance_change_per_h": round((now_m["balance"] - (m0.get("balance") or now_m["balance"])) / h)
            if now_m.get("balance") is not None else None}


def gate(raw, cat):
    p, notes = json.loads(raw), []
    clamp = lambda x, lo, hi: max(lo, min(hi, float(x)))
    orders = [{"sku": x["sku"], "qty": int(clamp(x.get("qty", 1), 1, 10))} for x in (p.get("orders") or [])[:10]
              if x.get("sku") in cat]
    clear = {x["sku"]: round(clamp(x.get("markdown", 0.1), 0.0, 0.30), 3) for x in (p.get("clearance") or [])[:20]
             if x.get("sku") in cat}
    dropped = len(p.get("orders") or []) - len(orders)
    if dropped:
        notes.append(f"{dropped} orders dropped (unknown SKU or over 10)")
    lessons = [x for x in (p.get("lessons") or []) if x.get("verdict") in ("keep", "undo", "watch") and x.get("text")][:3]
    retire = [i for i in (p.get("retire_lessons") or []) if isinstance(i, int)]
    return {"orders": orders, "clearance": clear, "alerts": (p.get("alerts") or [])[:8],
            "lessons": lessons, "retired": retire, "rationale": (p.get("rationale") or "")[:1200]}, notes


def changes(old, new):
    out = []
    a, b = {x["sku"]: x["qty"] for x in old.get("orders") or []}, {x["sku"]: x["qty"] for x in new.get("orders") or []}
    out += [f"order {k}: {a.get(k)} -> {b.get(k)}" for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
    ca, cb = old.get("clearance") or {}, new.get("clearance") or {}
    out += [f"clear {k}: {ca.get(k)} -> {cb.get(k)}" for k in sorted(set(ca) | set(cb)) if ca.get(k) != cb.get(k)]
    return out


def curate(lessons, retire, new, now):
    keep = [x for i, x in enumerate(lessons) if i not in set(retire or []) and now - x.get("ts", now) < 48 * 3600]
    keep += [{"ts": now, "text": x["text"][:300], "verdict": x["verdict"]} for x in new]
    return keep[-20:]


def run(ask_codex, dry=False):
    """One SCM cycle. ask_codex(prompt, schema) -> raw JSON text (marketing_agent passes its Codex runner)."""
    now = time.time()
    c = market_model.db()
    inv, stock = stock_view(c, now)
    try:
        history = [json.loads(x) for x in HISTORY.read_text(encoding="utf-8").splitlines() if x.strip()]
    except OSError:
        history = []
    now_m = measure(c, inv, stock, history[-1]["metrics"]["ts"] if history else now - 3600)
    lessons = load(LESSONS, [])
    current = load(PLAN, {})
    brief = {"time": time.strftime("%Y-%m-%d %H:%M"), "cash": {"balance": inv.get("balance"), "start": inv.get("start"),
             "restock_window": inv.get("restock")}, "stock": stock,
             "sales_team_picks": (promo.plan().get("restock") or {}).get("skus"),
             "follow_skus": promo.plan().get("follow_skus"), "exclusive_skus": promo.plan().get("exclusive_skus"),
             "regular_categories": {k: sum(v.values()) for k, v in market_model.regulars(c)[1].items()},
             "hot_skus_24h": [{"sku": s, "sold": n, "competitor_units": cu} for s, n, cu in market_model.hot_skus(c, top=15)],
             "scorecard": scorecard(now_m, history[-1] if history else None),
             "history": [{"at": time.strftime("%m-%d %H:%M", time.localtime(h["metrics"]["ts"])),
                          "changes": h.get("changes"), "outcome": h.get("outcome")} for h in history[-5:]],
             "lessons": [{"index": i, **{k: x[k] for k in ("text", "verdict")}, "age_h": round((now - x["ts"]) / 3600, 1)}
                         for i, x in enumerate(lessons)],
             "current_plan": current,
             "sales_requests": load(OUT / "requests.json", {}).get("scm") or []}
    raw = ask_codex(PROMPT + json.dumps(brief, ensure_ascii=False), SCHEMA)
    plan, notes = gate(raw, market_model.catalog(MD))
    moved = changes(current, plan)
    if len(moved) > 3:
        notes.append(f"{len(moved)} changes (rule: at most 3): {moved[:6]}")
    plan.update(version=int(now), written=time.strftime("%Y-%m-%d %H:%M:%S"), by="codex scm team", gate=notes)
    if dry:
        return plan, brief
    PLAN.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    if history:
        history[-1]["outcome"] = brief["scorecard"]
        HISTORY.write_text("".join(json.dumps(h, ensure_ascii=False) + "\n" for h in history[-200:]), encoding="utf-8")
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"metrics": now_m, "changes": moved, "version": plan["version"]}, ensure_ascii=False) + "\n")
    LESSONS.write_text(json.dumps(curate(lessons, plan["retired"], plan["lessons"], now), ensure_ascii=False, indent=1),
                       encoding="utf-8")
    return plan, brief


def scm_plan():
    return load(PLAN, {})
