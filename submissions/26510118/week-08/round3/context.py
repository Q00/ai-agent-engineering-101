"""The briefing an analysis model reads: five parts, assembled by code right before each call.

    1 state     the business now: phase, balance, stock, 24 h sales and purchases, the rules in force
    2 lessons   what the judge has learned so far (analysis/lessons.json), best confirmed first
    3 reports   the role's last two reports and what became of their proposals (the judge: the
                latest report of each role)
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

LIMITS = {"state": 800, "lessons": 1200, "reports": 1000, "memos": 800, "numbers": 2000}
TITLES = {"state": "1 The business now", "lessons": "2 Lessons learned so far (kept by the judge)",
          "reports": "3 Last reports and what became of their proposals", "memos": "4 Memos from the other analyses",
          "numbers": "5 New numbers"}
LESSONS = 10
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


def lessons(role: str) -> list:
    rows = [x for x in common.read_json(folder() / "lessons.json", []) or []
            if x.get("status", "active") == "active" and (role == "judge" or x.get("role") in (role, "both"))]
    rows.sort(key=lambda x: (x.get("refuted", 0) - x.get("confirmed", 0), -(x.get("updated") or 0)))
    return [f"- [{x.get('role')}] {x.get('text')} (confirmed {x.get('confirmed', 0)}, refuted {x.get('refuted', 0)})"
            for x in rows[:LESSONS]] + ([f"({len(rows) - LESSONS} weaker lesson(s) left out)"] if len(rows) > LESSONS else [])


def fate(p: dict, changes: list) -> str:
    """What the judge's next decision did with a proposal, and the 12 hours after it if it was applied."""
    if not p.get("valid"):
        return f"refused by code ({p.get('why_not')})"
    later = [c for c in changes if c.get("at", 0) >= p.get("ts", 0)]
    if not later:
        return "waiting for the judge"
    done = next((x for x in later[0].get("changes") or [] if x.get("proposal") == p.get("id")), None)
    if not done:
        return "not adopted"
    to = compact(done.get("to"))
    if not later[0].get("applied"):
        return f"adopted as {to}, recorded only (exploration phase)"
    if len(later) < 2:
        return f"adopted and applied as {to}; result not in yet"
    m0, m1 = later[0].get("metrics") or {}, later[1].get("metrics") or {}
    return (f"adopted and applied as {to}; next 12 h: shop margin {m0.get('seller_margin')} -> {m1.get('seller_margin')}, "
            f"buyer surplus {m0.get('buyer_surplus')} -> {m1.get('buyer_surplus')}")


def reports(role: str) -> list:
    rows = common.read_jsonl(folder() / "reports.jsonl")
    if role == "judge":
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
            and m.get("from") != role and (role == "judge" or m.get("to") in (role, "all"))]
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
    """The briefing for 'seller', 'buyer' or 'judge'; numbers: part 5's named blocks, made by the caller."""
    state = business(now)
    moved = change(state, now)
    blocks = [f"{k}: {compact(v)}" for k, v in numbers.items()]
    if moved and len(moved) > 1:                                # something besides hours_ago moved
        blocks.insert(1, f"change since {moved.pop('hours_ago')} h ago [then, now]: {compact(moved)}")
    parts = {"state": [compact({k: v for k, v in state.items() if k != "at"})], "lessons": lessons(role),
             "reports": reports(role), "memos": memos(role, now), "numbers": blocks}
    text = "\n\n".join(f"[{TITLES[k]}]\n{fit(v, LIMITS[k])}" for k, v in parts.items())
    folder().mkdir(parents=True, exist_ok=True)
    (folder() / f"last_context_{role}.txt").write_text(text, encoding="utf-8")
    with open(folder() / "contexts.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": int(now), "role": role, "chars": len(text), "text": text}, ensure_ascii=False) + "\n")
    return text
