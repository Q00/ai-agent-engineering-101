"""The briefing an analysis model reads: five parts, assembled by code right before each call.

    1 state     the business now: phase, balance, stock, 24 h sales and purchases, the rules in force
    2 insights  what the integrated analysis has learned so far (insights.py), best confirmed first
    3 reports   the role's last two reports and what became of their proposals (the integrated
                analysis: the latest report of each role)
    4 memos     what the other analyses left for this role in the last 12 hours (analysis/memos.jsonl)
    5 numbers   the new numbers: our own records and the whole market, and the change since about
                3 hours ago (analysis/business.jsonl, a snapshot the runner writes every hour)

Small and always needed goes in here; large and needed in parts stays behind the tools. Every
part has a size limit, and what does not fit is left out, oldest or weakest first, with a line
saying so: a model sees the same shape every time. Each briefing is kept (analysis/contexts.jsonl,
and analysis/last_context_<role>.txt to read by hand).
"""
import json
from datetime import datetime

import common

LIMITS = {"state": 800, "insights": 2400, "reports": 1000, "memos": 800, "numbers": 2000}
INTEGRATED_LIMITS = {"insights": 4800,          # it marks every active insight, so it sees all 20
                     "numbers": 4400}           # and since 11/4 night the scoreboard, the line, the notes and the tries
TITLES = {"state": "1 The business now", "insights": "2 Insights so far (kept by the integrated analysis)",
          "reports": "3 Last reports and what became of their proposals", "memos": "4 Memos from the other analyses",
          "numbers": "5 New numbers"}
INSIGHTS = 10            # a role analysis sees the best 8 and the 2 newest still under test
NEWEST = 2
REPORTS = 2
MEMOS = 5
MEMO_HOURS = 12
COMPARE = 3 * 3600
NONE = "(none yet)"


def folder():
    return common.MARKET_DIR / "analysis"


def stamp(ts) -> str:
    return datetime.fromtimestamp(ts or 0, common.KST).strftime("%m/%d %H:%M")


def compact(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _since(rows: list, now: float, seconds: float = 86400) -> list:
    return [r for r in rows if (r.get("ts") or r.get("settled_at") or 0) >= now - seconds]


def business(now: float) -> dict:
    """Part 1, and the hourly snapshot: the whole business in a few numbers."""
    inv = common.market_file("inventory.json", {}) or {}
    units = inv.get("units") or []
    sales = _since(common.records("sales"), now)
    bought = _since(common.read_jsonl(common.MARKET_DIR / "buyer" / "purchases.jsonl"), now)
    return {"at": int(now), "phase": common.strategy()["phase"], "balance": inv.get("balance"),
            "stock_units": len(units), "stock_at_cost": int(sum(u.get("cost", 0) for u in units)),
            "shop_sales_24h": len(sales), "shop_revenue_24h": sum(s.get("price") or 0 for s in sales),
            "shop_margin_24h": sum((s.get("price") or 0) - (s.get("cost") or 0) for s in sales),
            "buyer_orders_24h": len(_since(common.records("orders"), now)), "buyer_deals_24h": len(bought),
            "buyer_surplus_24h": sum((b.get("list_price") or 0) - (b.get("price") or 0) for b in bought),
            "rules": common.rules()}


def snapshot(now: float) -> None:
    folder().mkdir(parents=True, exist_ok=True)
    with open(folder() / "business.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(business(now), ensure_ascii=False) + "\n")


def change(state: dict, now: float) -> dict | None:
    """(then, now) for each number that moved since the newest snapshot at least 2.5 hours old."""
    then = next((s for s in reversed(common.read_jsonl(folder() / "business.jsonl"))
                 if s.get("at", 0) <= now - COMPARE + 1800), None)
    if not then:
        return None
    moved = {k: [then[k], v] for k, v in state.items() if k != "at" and isinstance(v, (int, float))
             and isinstance(then.get(k), (int, float)) and then[k] != v}
    return {"hours_ago": round((now - then["at"]) / 3600, 1), **moved}


def insights(role: str) -> list:
    """Active insights, best first, with the id the integrated analysis marks them by. It sees them all; a
    role analysis sees its own and the shared ones, the best 8 and the 2 newest still under test (a new
    insight scores 0, so by score alone it would never be shown, checked or used)."""
    rows = [x for x in common.read_json(folder() / "insights.json", []) or []
            if x.get("status") in ("testing", "confirmed") and (role == "integrated" or x.get("role") in (role, "both"))]
    rows.sort(key=lambda x: (x.get("refuted", 0) - x.get("confirmed", 0), -(x.get("checked") or 0)))
    shown = rows
    if role != "integrated" and len(rows) > INSIGHTS:
        best, rest = rows[:INSIGHTS - NEWEST], rows[INSIGHTS - NEWEST:]
        new = sorted((x for x in rest if x.get("status") == "testing"), key=lambda x: -(x.get("created") or 0))[:NEWEST]
        shown = best + [x for x in rest if x not in new][:NEWEST - len(new)] + new
    return [f"- {x.get('id')} [{x.get('role')}, {x.get('topic')}, {x.get('status')}: confirmed {x.get('confirmed', 0)}, "
            f"refuted {x.get('refuted', 0)}] {x.get('text')} | evidence: {x.get('evidence')} | use: {x.get('use')}"
            for x in shown] + ([f"({len(rows) - len(shown)} weaker insight(s) left out)"] if len(rows) > len(shown) else [])


def fate(p: dict, changes: list) -> str:
    """What the integrated analysis's next decision did with a proposal, and the period after it if it was applied."""
    if not p.get("valid"):
        return f"refused by code ({p.get('why_not')})"
    later = [c for c in changes if c.get("at", 0) >= p.get("ts", 0)]
    if not later:
        return "waiting for the integrated analysis"
    done = next((x for x in later[0].get("changes") or [] if x.get("proposal") == p.get("id")), None)
    if not done:
        return "not adopted"
    to = compact(done.get("to"))
    if not later[0].get("applied"):
        return f"adopted as {to}, recorded only (exploration phase)"
    if len(later) < 2:
        return f"adopted and applied as {to}; result not in yet"
    m0, m1 = later[0].get("metrics") or {}, later[1].get("metrics") or {}
    return (f"adopted and applied as {to}; the period after: shop margin {m0.get('seller_margin')} -> {m1.get('seller_margin')}, "
            f"buyer surplus {m0.get('buyer_surplus')} -> {m1.get('buyer_surplus')}")


def reports(role: str) -> list:
    rows = common.read_jsonl(folder() / "reports.jsonl")
    if role == "integrated":
        picked = [r for r in (next((x for x in reversed(rows) if x.get("role") == k), None) for k in ("seller", "buyer")) if r]
    else:
        picked = [r for r in rows if r.get("role") == role][-REPORTS:][::-1]          # newest first
    changes = common.read_jsonl(folder() / "changes.jsonl")
    out = []
    for r in picked:
        lines = [f"- {stamp(r.get('at'))} {r.get('role')}: {str(r.get('report'))[:200]}"]
        lines += [f"  {p.get('field')} = {compact(p.get('value'))}: {fate(p, changes)}" for p in r.get("proposals") or []]
        out.append("\n".join(lines))
    return out


def memos(role: str, now: float) -> list:
    rows = [m for m in common.read_jsonl(folder() / "memos.jsonl") if (m.get("ts") or 0) >= now - MEMO_HOURS * 3600
            and m.get("from") != role and (role == "integrated" or m.get("to") in (role, "all"))]
    return [f"- {stamp(m.get('ts'))} from {m.get('from')} ({m.get('act')}): {m.get('text')}" for m in rows[::-1][:MEMOS]]


def fit(items: list, limit: int) -> str:
    """Whole items in order while they fit; then a line saying how many were left out."""
    if not items:
        return NONE
    out, used = [], 0
    for i, s in enumerate(items):
        if used + len(s) + 1 > limit - 40:
            if not out:                                          # one item longer than the part: cut it
                out.append(s[:limit - 80] + " ...(cut)")
                i += 1
            if i < len(items):
                out.append(f"({len(items) - i} more left out)")
            break
        out.append(s)
        used += len(s) + 1
    return "\n".join(out)


def form(role: str, now: float, numbers: dict) -> str:
    """The briefing for 'seller', 'buyer' or 'integrated'; numbers: part 5's named blocks, made by the caller."""
    state = business(now)
    moved = change(state, now)
    blocks = [f"{k}: {compact(v)}" for k, v in numbers.items()]
    if moved and len(moved) > 1:                                # something besides hours_ago moved
        blocks.insert(1, f"change since {moved.pop('hours_ago')} h ago [then, now]: {compact(moved)}")
    parts = {"state": [compact({k: v for k, v in state.items() if k != "at"})], "insights": insights(role),
             "reports": reports(role), "memos": memos(role, now), "numbers": blocks}
    limits = dict(LIMITS, **(INTEGRATED_LIMITS if role == "integrated" else {}))
    text = "\n\n".join(f"[{TITLES[k]}]\n{fit(v, limits[k])}" for k, v in parts.items())
    folder().mkdir(parents=True, exist_ok=True)
    (folder() / f"last_context_{role}.txt").write_text(text, encoding="utf-8")
    with open(folder() / "contexts.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": int(now), "role": role, "chars": len(text), "text": text}, ensure_ascii=False) + "\n")
    return text
