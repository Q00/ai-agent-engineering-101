"""Week 08 — kpi.py: what headquarters holds each team accountable for (owner, 2026-11-04). Every team brief carries
its own line from here first, and the team must answer it.

Each team answers for one component of the midterm's own scoring (Players = shop ALL + buyer ALL, percentiles at the
end of the round; hr.py grades them); the numbers below are what it acts on.

  scm        revenue percentile (shop ALL x0.3): hits out of stock, the market's top sellers and our units of each
  finance    profit percentile (shop ALL x0.5): cash and profit against the fatal line (cash < 10% of start, loss > 15%)
  marketing  stars percentile (shop ALL x0.2): sell-through and stock older than 12 h
  sales      shop ALL percentile: the shop's rank, ALL and the Players rank, and VIP service
"""
import json
import time
from pathlib import Path

import market_model

HERE = Path(__file__).parent
MD = HERE / ".market"
BOARD = "https://market.ourolabs.ai/api/board"
NAME, OWNER = "dragonash", "dragonash22"
WHO = {"scm": "our revenue percentile falls (shop ALL x0.3): no stock of what sells means no revenue",
       "finance": "our Book percentile falls (shop ALL x0.5): balance - start + unsold stock at cost (patch 3.4); detect and report risks to sales, do not block",
       "marketing": "our stars percentile falls (shop ALL x0.2): reviews of paid deals",
       "sales": "our shop ALL percentile falls (half of the Players score that is 30% of the midterm)"}


def _inv():
    try:
        return json.loads((MD / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def stockouts(c):
    """Our own hits out of stock, and the market's top high-ticket sellers (all shops, ledger) with our units of each:
    HQ 11-04 holds SCM to keep at least 3 of the market's top 5 in stock."""
    import finance_agent
    import hr
    hits = finance_agent.proven_sellers(c)
    inv = _inv()
    have = {}
    for u in inv.get("units", []):
        if u.get("grade") != "expired":
            have[u["sku"]] = have.get(u["sku"], 0) + 1
    cat = market_model.catalog(MD)
    market = [{"sku": s, "sold_24h": n, "name": ((cat.get(s) or {}).get("name") or "")[:30],
               "list_price": (cat.get(s) or {}).get("list_price"), "our_units": have.get(s, 0)}
              for s, n in hr.market_hits(c)]
    return {"hits": len(hits), "out_of_stock": [h for h in hits if not h["units_left"]],
            "market_top_hits": market, "market_top5_in_stock": sum(1 for m in market[:5] if m["our_units"]),
            "target": "at least 3 of the market's top 5 in stock, 2+ units each"}


def sell_through(c, hours=6, old_h=12):
    inv, now = _inv(), time.time()
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    sold = c.execute(f"select count(*) from settles where ts>? and seq in ({market_model.ours(c)})",
                     (now - hours * 3600,)).fetchone()[0]
    old = [u for u in units if now - (u.get("supplied_at") or now) > old_h * 3600]
    return {"sold_6h": sold, "in_stock": len(units), "sell_through_6h": round(sold / (sold + len(units)), 3) if sold + len(units) else None,
            "older_than_12h": len(old), "older_cost": round(sum(u.get("cost") or 0 for u in old)),
            "expired_units": sum(1 for u in inv.get("units", []) if u.get("grade") == "expired")}


def cash_flow(c, hours=3):
    """Net cash flow per hour, newest last: cash in (our settles) minus cash out (our supply), and what the shop is
    worth (balance + stock at cost). HQ 11-04: the cash flow has to keep improving."""
    now, out = time.time(), []
    for h in range(hours, 0, -1):
        a, b = now - h * 3600, now - (h - 1) * 3600
        cin = c.execute(f"select coalesce(sum(amount),0) from settles where ts between ? and ? and seq in ({market_model.ours(c)})",
                        (a, b)).fetchone()[0]
        cout = c.execute("select coalesce(sum(total),0) from supplies where shop=? and ts between ? and ?",
                         (NAME, a, b)).fetchone()[0]
        out.append({"hours_ago": h, "in": round(cin), "out": round(cout), "net": round(cin - cout)})
    inv = _inv()
    stock = sum(u.get("cost") or 0 for u in inv.get("units", []) if u.get("grade") != "expired")
    return {"by_hour": out, "balance": inv.get("balance"), "stock_at_cost": round(stock),
            "worth": round((inv.get("balance") or 0) + stock)}


def health(shop):
    inv = _inv()
    start, bal, profit = inv.get("start") or 5_000_000, inv.get("balance"), (shop or {}).get("profit")
    return {"balance": bal, "profit": profit, "cash_above_fatal": None if bal is None else round(bal - 0.10 * start),
            "loss_room": None if profit is None else round(profit + 0.15 * start)}


def rank(board=None):
    if board is None:
        import httpx
        board = httpx.get(BOARD, timeout=30).json()
    shops = sorted(board.get("shops", []), key=lambda s: -(s.get("all") or 0))
    players = sorted(board.get("players", []), key=lambda p: -(p.get("total") or 0))
    me = next(((i + 1, s) for i, s in enumerate(shops) if s.get("name") == NAME), (None, {}))
    pl = next(((i + 1, p) for i, p in enumerate(players) if (p.get("owner") or {}).get("login") == OWNER), (None, {}))
    return {"shop_rank": me[0], "shops": len(shops), "shop_all": me[1].get("all"), "deals": me[1].get("deals"),
            "revenue": me[1].get("revenue"), "players_rank": pl[0], "players": len(players), "players_total": pl[1].get("total")}


def vip(hours=6):
    """VIP service: quotes where the clerk recognised a regular, from the shop log."""
    since, n, who = time.strftime("%H:%M:%S", time.localtime(time.time() - hours * 3600)), 0, set()
    try:
        for line in (HERE / "logs" / "market-kit.log").read_text(encoding="utf-8", errors="replace").splitlines()[-20000:]:
            if " VIP " in line and "cost" in line:
                n += 1
                who.add(line.rsplit(" VIP ", 1)[1].split()[0])
    except OSError:
        pass
    return {"vip_quotes_recent": n, "vip_buyers": sorted(who)[:20], "regulars": len(market_model.regulars(market_model.db())[0])}


HR_DEMAND = ("HR: your grade is below standard. Start your rationale with 'HR:' and, for each KPI marked missed in "
             "hr_grade.kpis, say in one line why it was missed and what you change this run to meet it.")


def handoff():
    """The handover note headquarters wrote when it replaced the teams (private/handoff.md), or None."""
    try:
        return (HERE / "private" / "handoff.md").read_text(encoding="utf-8")[:6000]
    except OSError:
        return None


def accountability(team, c=None, shop=None, board=None):
    """The line a team's brief starts with: what it answers for, and the numbers now."""
    c = c or market_model.db()
    try:
        data = {"scm": lambda: stockouts(c), "finance": lambda: {**health(shop), "cash_flow": cash_flow(c)},
                "marketing": lambda: sell_through(c), "sales": lambda: {**rank(board), **vip()}}[team]()
    except Exception as e:
        data = {"error": f"{type(e).__name__}: {e}"}
    out = {"you_answer_for": f"headquarters holds you responsible if {WHO[team]}", "now": data}
    try:
        import hr
        g = hr.grade_of({"sales": "sales", "scm": "scm", "marketing": "marketing", "finance": "finance"}[team])
        if g:
            out["hr_grade"] = g        # the HR team's score, KPIs, rewards and the levers you may change per run
        if team == "sales":
            import qa
            out["internal_audit"] = qa.summary()         # the review team's audit: what is broken right now
        if team == "sales" and hr.contest_for("sales"):
            out["private_from_hq"] = hr.contest_for("sales")   # the unit heads' contest: for the sales team only
            if g.get("status") in ("warning", "fire candidate"):
                out["hr_demand"] = HR_DEMAND
    except Exception:
        pass
    try:
        import leaders_meeting
        lp = leaders_meeting.summary()
        if lp:
            out["leaders_plan"] = lp    # the unit heads' joint plan: do your part of it
    except Exception:
        pass
    h = handoff()
    if h:
        out["handoff"] = h          # read it before anything else: what the team before you learned and got wrong
    ag = agenda()
    if ag:
        out["hq_agenda"] = ag       # HQ 11-14: the contingency meeting's agenda -- answer it from your own role
    return out


AGENDA = Path(__file__).parent / "private" / "emergency_agenda.json"


def agenda(max_age_h=6):
    """HQ's contingency-meeting agenda while fresh ({"ts", "text"} written by the TF), else None."""
    try:
        a = json.loads(AGENDA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return a.get("text") if time.time() - (a.get("ts") or 0) < max_age_h * 3600 else None


if __name__ == "__main__":
    for t in WHO:
        print(t, json.dumps(accountability(t), ensure_ascii=False))
