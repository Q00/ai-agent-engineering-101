"""Week 08 — kpi.py: what headquarters holds each team accountable for (owner, 2026-11-04). Every team brief carries
its own line from here first, and the team must answer it.

  scm        hits out of stock: SKUs we sold 2+ times in 6 h that have no unit left
  finance    cash and profit, and how far they are from the fatal line (cash < 45% of start, loss > 15%)
  marketing  sell-through: units sold in 6 h / (sold + in stock), and stock older than 12 h
  sales      the shop's rank, ALL and the Players rank, and VIP service (VIP quotes in 6 h)
"""
import json
import time
from pathlib import Path

import market_model

HERE = Path(__file__).parent
MD = HERE / ".market"
BOARD = "https://market.ourolabs.ai/api/board"
NAME, OWNER = "dragonash", "dragonash22"
WHO = {"scm": "the sales team has no stock of a hit to sell", "finance": "the sales team's finances get worse",
       "marketing": "stock stops selling (sell-through falls)", "sales": "the shop's rank falls"}


def _inv():
    try:
        return json.loads((MD / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def stockouts(c):
    import finance_agent
    hits = finance_agent.proven_sellers(c)
    return {"hits": len(hits), "out_of_stock": [h for h in hits if not h["units_left"]]}


def sell_through(c, hours=6, old_h=12):
    inv, now = _inv(), time.time()
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    sold = c.execute(f"select count(*) from settles where ts>? and seq in ({market_model.ours(c)})",
                     (now - hours * 3600,)).fetchone()[0]
    old = [u for u in units if now - (u.get("supplied_at") or now) > old_h * 3600]
    return {"sold_6h": sold, "in_stock": len(units), "sell_through_6h": round(sold / (sold + len(units)), 3) if sold + len(units) else None,
            "older_than_12h": len(old), "older_cost": round(sum(u.get("cost") or 0 for u in old)),
            "expired_units": sum(1 for u in inv.get("units", []) if u.get("grade") == "expired")}


def health(shop):
    inv = _inv()
    start, bal, profit = inv.get("start") or 5_000_000, inv.get("balance"), (shop or {}).get("profit")
    return {"balance": bal, "profit": profit, "cash_above_fatal": None if bal is None else round(bal - 0.45 * start),
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
        data = {"scm": lambda: stockouts(c), "finance": lambda: health(shop),
                "marketing": lambda: sell_through(c), "sales": lambda: {**rank(board), **vip()}}[team]()
    except Exception as e:
        data = {"error": f"{type(e).__name__}: {e}"}
    out = {"you_answer_for": f"headquarters holds you responsible if {WHO[team]}", "now": data}
    try:
        import hr
        g = hr.grade_of({"sales": "sales", "scm": "scm", "marketing": "marketing", "finance": "finance"}[team])
        if g:
            out["hr_grade"] = g        # the HR team's score, KPIs, rewards and the levers you may change per run
    except Exception:
        pass
    h = handoff()
    if h:
        out["handoff"] = h          # read it before anything else: what the team before you learned and got wrong
    return out


if __name__ == "__main__":
    for t in WHO:
        print(t, json.dumps(accountability(t), ensure_ascii=False))
