"""Week 08 — leaders_meeting.py: the unit leaders meet (HQ, 2026-11-04). Only the two unit heads; every other team
keeps working on its own cycle.

  Buyer unit head   the buyer team (Codex): states where the buyer stands and what it will do and needs
  Seller unit head  the sales team (Claude), in the chair: hears the buyer head and writes one joint plan

Goal set by HQ: the top 3 by tomorrow (Players = shop ALL + buyer ALL, percentiles at the end of the round). The
joint plan goes in private/leaders_plan.json and in every team's brief (kpi.accountability), items that need the
owner's approval are listed for HQ instead of being done.

    python leaders_meeting.py          # meet now
"""
import json
import time
from pathlib import Path

import enuri
import hr
import kpi
import market_model

HERE = Path(__file__).parent
OUT = HERE / "private" / "leaders_plan.json"
GOAL = "Reach the top 3 of the Players table by tomorrow (11-04 KST) and stay there to the end of the round."

BUYER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["where_we_stand", "actions", "needs_from_seller_unit", "needs_hq_approval"],
    "properties": {"where_we_stand": {"type": "string"},
                   "actions": {"type": "array", "items": {"type": "string"}},
                   "needs_from_seller_unit": {"type": "array", "items": {"type": "string"}},
                   "needs_hq_approval": {"type": "array", "items": {"type": "string"}}},
}

BUYER_PROMPT = """You are the head of the Buyer unit (buyer team) of "dragonash" in a classroom agent market, at a
meeting of the two unit heads called by the headquarters. Goal: {goal}
Buyer ALL = 0.5 surplus + 0.3 forum votes + 0.1 deals + 0.1 posts (percentiles among buyers with a deal). From the brief:
where we stand against the leaders (with numbers), the 3-5 actions the Buyer unit takes tonight within its own
levers (buyer theta, Enuri, forum posts), what it needs from the Seller unit, and anything that needs the owner's
approval (those wait until tomorrow). Measured facts only; say when something is not yet measured.
Answer with the JSON object only.

BRIEF:
"""

PLAN_KEYS = ["goal_gap", "seller_actions", "buyer_actions", "coordination", "needs_hq_approval", "risks", "check_at"]

SALES_PROMPT = """You chair a meeting of the two unit heads of "dragonash" in a classroom agent market, called by the
headquarters (the owner). You head the Seller unit (sales team, the brain of the shop). Goal: {goal}
Players = shop ALL + buyer ALL; shop ALL = 0.5 profit + 0.3 revenue + 0.2 stars; buyer ALL = 0.5 surplus + 0.3 forum
votes + 0.1 deals + 0.1 posts; each a percentile, ranked at the end of the round (11-17 23:59 KST).
The Buyer unit head has spoken (BUYER UNIT HEAD below). Write ONE joint plan for tonight:
- goal_gap: how far each Players component is from a top-3 total, with numbers from the brief
- seller_actions / buyer_actions: 3-5 concrete actions each, within the teams' own levers, in priority order
- coordination: what each unit gives the other (e.g. demand gaps the buyer sees -> stock the shop buys)
- needs_hq_approval: anything that needs the owner (it waits until tomorrow; do not plan around it tonight)
- risks: what could cost rank tonight (bankruptcy is forbidden; unsold stock at the end is a full loss)
- check_at: when and by which numbers the heads will know the plan works
HQ rules stand: HQ direct orders, buy only what sells, never bankrupt, honest grades, no instructions to other agents.
Answer with one JSON object with exactly these keys: {keys}. Values are strings or arrays of strings.

BRIEF:
"""


def brief():
    import httpx
    board = httpx.get(kpi.BOARD, timeout=30).json()
    c = market_model.db()
    players = sorted(board.get("players", []), key=lambda p: -(p.get("total") or 0))
    top = [{"rank": i + 1, "player": p.get("player"), "shop_all": (p.get("shop") or {}).get("all"),
            "buyer_all": (p.get("buyer") or {}).get("all"), "total": p.get("total")} for i, p in enumerate(players[:5])]
    me = next(({"rank": i + 1, "shop_all": (p.get("shop") or {}).get("all"), "buyer_all": (p.get("buyer") or {}).get("all"),
                "total": p.get("total")} for i, p in enumerate(players) if (p.get("owner") or {}).get("login") == kpi.OWNER), None)
    teams, _ = hr.run(board)
    return {"time": time.strftime("%Y-%m-%d %H:%M"), "goal": GOAL, "players_top5": top, "us": me,
            "hr": {t: {"score": v["score"], "status": v["status"],
                       "kpis": [{k: r[k] for k in ("kpi", "value", "target")} for r in v["kpis"]]} for t, v in teams.items()},
            "sales_kpi": kpi.accountability("sales", c, board=board).get("now"),
            "winning_skus": enuri.winning(c)[:10], "enuri": enuri.summary(),
            "demand_gaps": market_model.gap_demand(c)[:8],
            "internal_audit": __import__("qa").audit(board),            # the review team's findings (11-04)
            "buyer_diagnosis": __import__("buyer_team").diagnosis(board),
            "review_team_experiment": __import__("review_team").report(),
            "hq_decisions": (HERE / "private" / "hq_decisions.md").read_text(encoding="utf-8")[:4000]}


def meet(ask_codex, ask_claude, goal=None):
    global GOAL
    GOAL = goal or GOAL
    b = brief()
    buyer = json.loads(ask_codex(BUYER_PROMPT.format(goal=GOAL) + json.dumps(b, ensure_ascii=False), BUYER_SCHEMA))
    raw = ask_claude(SALES_PROMPT.format(goal=GOAL, keys=", ".join(PLAN_KEYS)) + json.dumps(b, ensure_ascii=False)
                     + "\n\nBUYER UNIT HEAD:\n" + json.dumps(buyer, ensure_ascii=False))
    p = json.loads(raw)
    plan = {k: p.get(k) for k in PLAN_KEYS}
    plan.update(written=time.strftime("%Y-%m-%d %H:%M:%S"), buyer_head=buyer, players=b.get("us"))
    OUT.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    return plan


def summary():
    """The joint plan for the teams' briefs, or None."""
    try:
        p = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {k: p.get(k) for k in ("written", "goal_gap", "seller_actions", "buyer_actions", "coordination", "check_at")}


if __name__ == "__main__":
    import sys
    import marketing_agent
    goal = sys.argv[sys.argv.index("--goal") + 1] if "--goal" in sys.argv else None
    print(json.dumps(meet(marketing_agent.ask_codex, marketing_agent.ask_claude, goal), ensure_ascii=False, indent=1))
