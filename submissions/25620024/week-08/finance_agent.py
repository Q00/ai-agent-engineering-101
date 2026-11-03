"""Week 08 — finance_agent.py: the finance team (Codex), the check on the sales team. Run by marketing_agent.py
once an hour, before the sales team decides.

The sales team (Claude) is the brain, but it must take the finance team's advice: it reads it every run and has
to follow it or say why not. The finance team also sets binding limits that the code enforces whatever the sales
plan says: minimum margin, spend per supply window, cash floor, warranty hours, how far follow pricing may go
under the market median. It is a different model from the sales team on purpose: an independent check.

Self-improvement (run -> compare -> memory -> revise): every run it forecasts profit per hour for the next hour;
the next run scores that forecast against what happened, writes lessons (keep / undo / watch) and a curator
retires stale ones (48 h, 20 kept).
"""
import json
import time
from pathlib import Path

import market_model
import promo

HERE = Path(__file__).parent
MD = HERE / ".market"
OUT = HERE / "private" / "marketing"
LIMITS = HERE / "private" / "finance_limits.json"
HISTORY = OUT / "finance_history.jsonl"
LESSONS = OUT / "finance_lessons.json"

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "advice", "limits", "forecast_profit_per_h", "lessons", "retire_lessons", "rationale"],
    "properties": {
        "verdict": {"type": "string", "enum": ["ok", "caution", "stop"]},
        "advice": {"type": "array", "items": {"type": "string"}},
        "limits": {"type": "object", "additionalProperties": False,
                   "required": ["min_margin", "window_spend", "cash_floor", "max_warranty_h", "max_undercut"],
                   "properties": {"min_margin": {"type": "number"}, "window_spend": {"type": "number"},
                                  "cash_floor": {"type": "number"}, "max_warranty_h": {"type": "integer"},
                                  "max_undercut": {"type": "number"}}},
        "forecast_profit_per_h": {"type": "number"},
        "lessons": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                               "required": ["text", "verdict"],
                                               "properties": {"text": {"type": "string"},
                                                              "verdict": {"type": "string",
                                                                          "enum": ["keep", "undo", "watch"]}}}},
        "retire_lessons": {"type": "array", "items": {"type": "integer"}},
        "rationale": {"type": "string"},
    },
}

PROMPT = """You are the finance team of an agent shop named "dragonash" in a classroom agent market (Round 3).
The sales team (Claude) is the brain and decides prices, promotions and what to stock; you are its independent
check. You ADVISE: the headquarters wants the sales team to decide, and you may block it only when a risk is
fatal (cash about to fall under 45% of the start, or losses over 15% of it); short of that a "stop" is read as
"caution", window_spend keeps at least 0.03 and cash_floor is capped at 0.50. Score: shop ALL = 0.5 net profit + 0.3 revenue + 0.2 stars (percentiles); profit = balance - 5,000,000 at the
end of the round (stock still unsold then is lost money; bankruptcy aid is subtracted). From the brief:
- verdict: ok, caution (advice must be followed) or stop (no new stock spend this hour)
- advice: 2-5 concrete points for the sales team, with numbers (margins too thin, cash tied in slow stock, a
  promotion that costs more than it brings, warranty or guarantee exposure, ...)
- limits (binding; the code enforces them over the sales plan): min_margin over cost (0.05-0.60),
  window_spend as a share of the 5,000,000 start per supply window (0.0-0.25), cash_floor as a share of the start
  (0.30-0.80), max_warranty_h (0, 3 or 6), max_undercut under the market median for follow pricing (0.0-0.05)
- forecast_profit_per_h: your forecast of the change in profit per hour over the next hour
- lessons: score your last forecast and limits with brief.scorecard and brief.history (1-3 lessons, keep / undo /
  watch); brief.lessons are your standing lessons with their index; retire stale or wrong ones by index
- rationale: 3-5 sentences.
brief.hq is the headquarters' latest directive (the owner; above every team): follow it within your role and say in
advice how. brief.proven_sellers are SKUs the ledger shows we sold lately, with margin and units left: buying back
a proven seller that is sold out is not the slow stock you guard against; set window_spend so it can be restocked
unless cash truly cannot carry it, and say which of them you would cut and why.
brief.accountability comes first: what headquarters holds you responsible for, with the numbers now. Answer
it in your rationale (what you do about it this run).
Answer with the JSON object only.

BRIEF:
"""


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def snapshot(c, our_shop):
    inv = load(MD / "inventory.json", {})
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    since = time.time() - 6 * 3600
    sales = c.execute(f"""select s.amount, su.cost, s.sku from settles s join settle_units x on x.seq = s.seq
                          join supply_units su on su.unit = x.unit where s.ts > ? and s.seq in ({market_model.ours(c)})""",
                      (since,)).fetchall()
    cat = market_model.catalog(MD)
    rev = sum(a for a, _, _ in sales)
    margin = sum(a - (co or 0) for a, co, _ in sales)
    goods_rev = sum(a for a, _, sku in sales if (cat.get(sku) or {}).get("kind") == "goods")
    return {"ts": time.time(), "profit": our_shop.get("profit"), "revenue": our_shop.get("revenue"),
            "deals": our_shop.get("deals"), "stars": our_shop.get("stars"), "refunds": our_shop.get("refunds"),
            "broken": our_shop.get("broken"), "balance": inv.get("balance"), "start": inv.get("start"),
            "stock_units": len(units), "stock_cost": round(sum(u.get("cost") or 0 for u in units)),
            "expired_units": sum(1 for u in inv.get("units", []) if u.get("grade") == "expired"),
            "storage_per_day": round(0.01 * sum(u.get("cost") or 0 for u in units
                                                if (cat.get(u["sku"]) or {}).get("kind") == "goods")),
            "sales_6h": {"n": len(sales), "revenue": rev, "gross_margin": round(margin),
                         "margin_share": round(margin / rev, 3) if rev else None,
                         "goods_revenue_under_warranty_risk": goods_rev},
            "guarantee_credits_6h": c.execute("select count(*), coalesce(sum(amount), 0) from credits_used where ts>?",
                                              (since,)).fetchone()}


def proven_sellers(c, hours=6, min_n=2):
    """SKUs we sold at least min_n times lately: deals, mean price, mean cost, margin, units still in stock."""
    inv = load(MD / "inventory.json", {})
    left = {}
    for u in inv.get("units", []):
        if u.get("grade") != "expired":
            left[u["sku"]] = left.get(u["sku"], 0) + 1
    rows = c.execute(f"""select s.sku, count(*), avg(s.amount), avg(su.cost) from settles s
                          join settle_units x on x.seq = s.seq join supply_units su on su.unit = x.unit
                          where s.ts > ? and s.seq in ({market_model.ours(c)}) group by s.sku having count(*) >= ?
                          order by count(*) * avg(s.amount) desc""", (time.time() - hours * 3600, min_n)).fetchall()
    return [{"sku": k, "sold": n, "mean_price": round(p), "mean_cost": round(co or 0),
             "margin": round(p / co - 1, 3) if co else None, "units_left": left.get(k, 0)} for k, n, p, co in rows][:20]


def hq_directive():
    """The last numbered headquarters directive in private/owner_directives.md."""
    try:
        lines = (HERE / "private" / "owner_directives.md").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    hq = [x for x in lines if x[:3].rstrip(".").strip().isdigit()]
    return hq[-1] if hq else None


FATAL_CASH, FATAL_LOSS, MIN_SPEND, MAX_FLOOR = 0.45, 0.15, 0.03, 0.50
MAX_SPEND = 0.25           # HQ 2026-11-04: up to 1,250,000 per window for a high-ticket assortment


def fatal(snap):
    """The only cases the finance team may block in: cash near the bankruptcy line, or a loss spiral."""
    start = snap.get("start") or 5_000_000
    bal, profit = snap.get("balance"), snap.get("profit")
    return bool((bal is not None and bal < FATAL_CASH * start) or (profit is not None and profit < -FATAL_LOSS * start))


def gate(raw, snap=None):
    p = json.loads(raw)
    clamp = lambda x, lo, hi: max(lo, min(hi, float(x)))
    lim = p.get("limits") or {}
    w = int(lim.get("max_warranty_h", 6))
    limits = {"min_margin": round(clamp(lim.get("min_margin", 0.2), 0.05, 0.60), 3),
              "window_spend": round(clamp(lim.get("window_spend", 0.04), 0.0, MAX_SPEND), 3),
              "cash_floor": round(clamp(lim.get("cash_floor", 0.5), 0.30, 0.80), 3),
              "max_warranty_h": w if w in (0, 3, 6) else 0,
              "max_undercut": round(clamp(lim.get("max_undercut", 0.03), 0.0, 0.05), 3)}
    verdict = p.get("verdict") if p.get("verdict") in ("ok", "caution", "stop") else "caution"
    if snap is not None and not fatal(snap):         # advise, do not block: HQ directive 2026-11-04
        if verdict == "stop":
            verdict = "caution"
        limits["window_spend"] = max(limits["window_spend"], MIN_SPEND)
        limits["cash_floor"] = min(limits["cash_floor"], MAX_FLOOR)
    if verdict == "stop":
        limits["window_spend"] = 0.0
    lessons = [x for x in (p.get("lessons") or []) if x.get("verdict") in ("keep", "undo", "watch") and x.get("text")][:3]
    return {"verdict": verdict, "advice": [str(x)[:300] for x in (p.get("advice") or [])[:5]], "limits": limits,
            "forecast_profit_per_h": float(p.get("forecast_profit_per_h") or 0), "lessons": lessons,
            "retired": [i for i in (p.get("retire_lessons") or []) if isinstance(i, int)],
            "rationale": (p.get("rationale") or "")[:1200]}


def scorecard(now_s, last):
    if not last:
        return {"note": "first finance run with a scorecard"}
    s0, h = last["snapshot"], max(0.1, (now_s["ts"] - last["snapshot"]["ts"]) / 3600)
    actual = None if now_s.get("profit") is None or s0.get("profit") is None else round((now_s["profit"] - s0["profit"]) / h)
    return {"hours": round(h, 2), "forecast_profit_per_h": last.get("forecast"), "actual_profit_per_h": actual,
            "forecast_error": None if actual is None or last.get("forecast") is None else round(actual - last["forecast"]),
            "limits_then": last.get("limits"), "verdict_then": last.get("verdict"),
            "stock_cost_change": (now_s.get("stock_cost") or 0) - (s0.get("stock_cost") or 0),
            "refunds_added": (now_s.get("refunds") or 0) - (s0.get("refunds") or 0)}


def curate(lessons, retire, new, now):
    keep = [x for i, x in enumerate(lessons) if i not in set(retire or []) and now - x.get("ts", now) < 48 * 3600]
    keep += [{"ts": now, "text": x["text"][:300], "verdict": x["verdict"]} for x in new]
    return keep[-20:]


def run(ask_codex, our_shop, dry=False):
    now = time.time()
    c = market_model.db()
    snap = snapshot(c, our_shop)
    try:
        history = [json.loads(x) for x in HISTORY.read_text(encoding="utf-8").splitlines() if x.strip()]
    except OSError:
        history = []
    lessons = load(LESSONS, [])
    try:
        import scm_agent
        scm = scm_agent.scm_plan()
    except Exception:
        scm = {}
    import kpi
    brief = {"accountability": kpi.accountability("finance", c, our_shop), "time": time.strftime("%Y-%m-%d %H:%M"),
             "snapshot": snap, "hq": hq_directive(),
             "proven_sellers": proven_sellers(c),
             "sales_plan": {k: promo.plan().get(k) for k in ("pricing", "exclusive_skus", "follow_skus", "restock")},
             "scm_plan": {k: scm.get(k) for k in ("orders", "clearance", "alerts")},
             "current_limits": load(LIMITS, {}), "scorecard": scorecard(snap, history[-1] if history else None),
             "history": [{"at": time.strftime("%m-%d %H:%M", time.localtime(h["snapshot"]["ts"])), "verdict": h.get("verdict"),
                          "forecast": h.get("forecast"), "outcome": h.get("outcome")} for h in history[-5:]],
             "lessons": [{"index": i, **{k: x[k] for k in ("text", "verdict")}, "age_h": round((now - x["ts"]) / 3600, 1)}
                         for i, x in enumerate(lessons)]}
    out = gate(ask_codex(PROMPT + json.dumps(brief, ensure_ascii=False), SCHEMA), snap)
    out.update(written=time.strftime("%Y-%m-%d %H:%M:%S"), by="codex finance team")
    if dry:
        return out, brief
    LIMITS.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    if history:
        history[-1]["outcome"] = brief["scorecard"]
        HISTORY.write_text("".join(json.dumps(h, ensure_ascii=False) + "\n" for h in history[-200:]), encoding="utf-8")
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"snapshot": snap, "forecast": out["forecast_profit_per_h"], "limits": out["limits"],
                            "verdict": out["verdict"]}, ensure_ascii=False) + "\n")
    LESSONS.write_text(json.dumps(curate(lessons, out["retired"], out["lessons"], now), ensure_ascii=False, indent=1),
                       encoding="utf-8")
    return out, brief


def limits():
    return (load(LIMITS, {}) or {}).get("limits") or {}
