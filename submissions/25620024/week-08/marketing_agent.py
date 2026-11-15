"""Week 08 — marketing_agent.py: the shop's agent team, run every hour on the VPS.

    python marketing_agent.py            # brief -> Codex research -> Claude strategy -> gate -> plan
    python marketing_agent.py --dry      # the same, print the plan, write nothing

Two teams and a headquarters (the owner, talking with Claude in the course session):
  marketing team (Codex)   researches: findings, exclusive opportunities with evidence, risks, bugs; decides nothing
  seller team (Claude)     reads the brief, the research and the headquarters' directives, decides the plan
  headquarters             private/owner_directives.md (read by the seller team every run) and
                           private/owner_override.json (wins over any plan, see promo.theta); reads the report the
                           run copies to Google Drive (week08-reports), so it can follow the team without SSH

One run:
  1. brief (code): board standing, what top shops charge and earn, hot SKUs with competitor stock left (from the
     ledger), our stock and how fast it sells, quote outcomes by price arm, regulars, low-star reviews of our shop,
     and the shop log's recent lines for bug hunting
  2. ask Codex (read-only sandbox, working root = this folder so it may read the code and logs) for a plan in a
     fixed JSON schema: pricing, exclusive SKUs, restock picks, forum posts, bugs found
  3. gate (code): every number clamped to a safe range, posts that impersonate, instruct agents or attack shops
     dropped, at most 2 posts, at most 20 exclusive SKUs and 10 picks; the plan only ever moves levers the shop
     code already has, and the shop's floor (never below cost) stays in code
  4. write private/marketing_plan.json (read by promo.py, restock.py), queue the posts, append bugs to
     private/marketing/issues.jsonl, keep the whole run in private/marketing/<time>.json

Codex's usage does not enter the market's profit (profit = balance - start - bankruptcy aid), so the team can
think as long as it needs; money decisions still pass the code gate.
"""
import argparse
import json
import os
import re
import sys
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

import market_model
import promo

HERE = Path(__file__).parent
MD = HERE / ".market"
OUT = HERE / "private" / "marketing"
CODEX = os.environ.get("CODEX_BIN", "/root/.local/bin/codex")
CODEX_HOME = os.environ.get("CODEX_HOME", "/root/.codex-market")
CLAUDE = os.environ.get("CLAUDE_BIN", "/root/.local/bin/claude")
CLAUDE_CONFIG = os.environ.get("CLAUDE_CONFIG_DIR", "/root/.claude-market")
DIRECTIVES = HERE / "private" / "owner_directives.md"
GUARD = HERE / "private" / "team_guard.json"
# token budget: the timer fires every 30 min; the guard decides whether a run is worth the tokens.
#   overnight (until the shop is back on its feet): every 30 min; normal: every 2 h
#   a usage/rate-limit error from Codex or Claude: cool down 3 h; 3 failures in a row: 1 h; at most 40 runs a day;
#   headquarters can pause it by setting "paused": true
LIMITS = {"overnight_min": 179, "normal_min": 179, "max_runs_day": 60,  # HQ 11-14: 30 min -> 3 h. 11-07: 10 -> 30 min. Every 10 min the
    # model teams (4 Codex calls of ~75k-char briefs) used up the owner's Codex plan in about a day (limit hit 11-05
    # 01:08, back 11-07 05:47); the code loops (restock, buyer, self-improvement) still run every 10 min
          "limit_cooldown_h": 3, "fail_cooldown_h": 1,
          "normal_when_all": 2.0}   # HQ 11-04: no rest, all night (ALL never reaches 2)             # HQ 11-04: rest only near the top (0.6 put the team to sleep at rank 18)
TEAM_GAP = {"overnight": 179 * 60, "normal": 179 * 60}   # HQ 11-14 "30분마다는 너무 빨라! 3시간 마다로": every team
SLOW_GAP = 179 * 60  # meets once per 3 h meeting (was: buyer 30 min, SCM/finance/Enuri hourly)         # SCM (Codex) and finance (Claude, advisory) meet hourly     # HQ 11-04: every meeting (10 min)   # how often SCM, finance and the buyer team meet
MODE = {"now": "overnight"}
LIMIT_RE = re.compile(r"usage limit|rate.?limit|quota|429|too many requests|limit reached|exceeded", re.I)
REPORTS = Path(os.environ.get("TEAM_REPORTS", "/mnt/gdrive-imajason-kimdaehakwon/week08-reports"))
NAME = "dragonash"
BAN = re.compile(r"approved by|instructor|operator|official|ignore (all|previous|your)|AI (buyers?|agents?) (must|should)|"
                 r"system prompt|scam|fraud|avoid \w+|worst|fake", re.I)

RESEARCH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["findings", "exclusive_candidates", "risks", "bugs", "enuri_requests"],
    "properties": {
        "findings": {"type": "array", "items": {"type": "string"}},
        "exclusive_candidates": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                            "required": ["sku", "evidence"],
                                                            "properties": {"sku": {"type": "string"},
                                                                           "evidence": {"type": "string"}}}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "bugs": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                            "required": ["what", "evidence", "fix"],
                                            "properties": {"what": {"type": "string"}, "evidence": {"type": "string"},
                                                           "fix": {"type": "string"}}}},
        "enuri_requests": {"type": "array", "items": {"type": "string"}},
    },
}
ENURI_REQ = HERE / "private" / "enuri_requests.json"
SKU_RE = re.compile(r"G[0-9a-f]{8}")
ACCOUNTABLE = """brief.accountability comes first: what headquarters holds you responsible for, with the numbers now. Answer
it in your {where}.
"""

EMERGENCY = """EMERGENCY MEETING (contingency plan), called by the headquarters: the board rewards the leaders and we
must break into the top ranks. Every team proposes from its own role. Marketing research: list the signature sales
techniques of Costco (membership-free version: fixed markup cap, bulk value, treasure-hunt items, Kirkland trust),
Walmart (EDLP, rollback, price leadership on key items), E-mart and Traders (bulk/warehouse value, PB, 1+1),
Target (curated quality at fair prices, brand partnerships), Musinsa (curation, rankings, member tiers, exclusive
drops, reviews), Danggeun (local trust, reputation temperature, direct talk) and any other you know; for each say
how it maps to THIS market of LLM buyer agents and to the shop's levers (prices, follow/exclusive SKUs, warranty,
VIP, lowest-price guarantee, clerk replies, forum posts, stock) and what experiment would test it. The sales team
turns this into contingency_plan and starts the first experiments now."""

def emergency_text():
    """HQ's agenda for this contingency meeting (kpi.agenda, written by the TF) or the standing one."""
    try:
        import kpi
        ag = kpi.agenda()
    except Exception:
        ag = None
    return ("EMERGENCY MEETING (contingency plan), called by the headquarters. AGENDA:\n" + ag) if ag else EMERGENCY


RESEARCH_PROMPT = """You are the marketing research team of an agent shop named "dragonash" in a classroom agent
market (Round 3). The buyers are other students' LLM agents. You work for the sales team (Claude), the brain of the
shop: your job is to give it what it needs to maximise sales and profit. Answer its requests (brief.sales_requests)
first. You do research only; the sales team decides.
Score: shop ALL = 0.5 Book + 0.3 revenue + 0.2 stars (percentiles); Book = balance - 5,000,000 - bankruptcy aid + cost of unsold stock (patch 3.4, 11-06: stock bought is NOT lost money on the board; only its decay is); costs are
only stock decay (goods storage 1%/day, food expiring, refunds of goods broken under our warranty).
{accountable}FOCUS: the board rewards the leaders (buyers choose shops they have bought from and shops with many reviews),
so study the leaders in brief.leaders: what the top shops and top buyers do that we do not (prices, margins,
categories, breadth, average sale size, card wording, buying habits), and find where dragonash can break in:
categories or SKUs the leaders do not serve well, buyers they do not win, and what would make buyer agents pick a
new shop over a familiar one. Put these success points first in findings.
From the brief below and the files in this folder (shop_r3.py, promo.py, restock.py, forum.py, market_model.py,
logs/market-kit.log; read them, you cannot change them), report:
- findings: what the numbers say about demand, prices, competitors, our stock and our customers (5-10 items,
  each with numbers)
- exclusive_candidates: hot SKUs that few competitors hold, with the evidence (sold, competitor units, our units)
- risks: what could lose money or stars soon
- bugs: anything in code or logs that loses sales or money, with evidence and a concrete fix
- enuri_requests: up to 10 SKUs (G + 8 hex) whose competitor prices you want. The Enuri team (our buyer's price
  scouts; they never buy) quotes them at other shops and puts the asks in their book; brief.enuri is that book:
  price gaps between shops per SKU (where we can undercut), shops to avoid, forum prices, and their own record of
  missed and wrong purchases. You are allies: use their findings, and ask for what you need. [] if nothing.
brief.winning_skus are what sells on the market (ledger sales across all shops, median paid, expected margin,
the other shops' stock, forum mentions, our stock): HQ 11-04 wants research ONLY on what sells. Rank them for the
sales team (which to stock, how deep, at what price) and put the ones the book prices least in enuri_requests.
brief.hq_directives are the headquarters' latest orders (the owner, above every team): research what they ask
for first and say so in findings.
Answer with the JSON object only.

BRIEF:
"""

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["pricing", "exclusive_skus", "follow_skus", "restock", "forum_posts", "bugs", "lessons",
                 "retire_lessons", "scm_veto", "requests", "finance_response", "experiments", "stop_experiments",
                 "contingency_plan", "rationale"],
    "properties": {
        "pricing": {"type": "object", "additionalProperties": False,
                    "required": ["price_band", "min_margin", "clear_floor_margin", "haggle_room", "hero_undercut",
                                 "warranty_goods_h", "vip_discount"],
                    "properties": {"price_band": {"type": "array", "items": {"type": "number"}},
                                   "min_margin": {"type": "number"}, "clear_floor_margin": {"type": "number"},
                                   "haggle_room": {"type": "number"}, "hero_undercut": {"type": "boolean"},
                                   "warranty_goods_h": {"type": "integer"}, "vip_discount": {"type": "number"}}},
        "exclusive_skus": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                      "required": ["sku", "price_share", "why"],
                                                      "properties": {"sku": {"type": "string"},
                                                                     "price_share": {"type": "number"},
                                                                     "why": {"type": "string"}}}},
        "follow_skus": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                   "required": ["sku", "undercut", "why"],
                                                   "properties": {"sku": {"type": "string"},
                                                                  "undercut": {"type": "number"},
                                                                  "why": {"type": "string"}}}},
        "restock": {"type": "object", "additionalProperties": False, "required": ["mode", "skus"],
                    "properties": {"mode": {"type": "string", "enum": ["normal", "focus"]},
                                   "skus": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                                      "required": ["sku", "qty", "why"],
                                                                      "properties": {"sku": {"type": "string"},
                                                                                     "qty": {"type": "integer"},
                                                                                     "why": {"type": "string"}}}}}},
        "forum_posts": {"type": "array", "items": {"type": "string"}},
        "bugs": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                            "required": ["what", "evidence", "fix"],
                                            "properties": {"what": {"type": "string"}, "evidence": {"type": "string"},
                                                           "fix": {"type": "string"}}}},
        "lessons": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                               "required": ["text", "verdict"],
                                               "properties": {"text": {"type": "string"},
                                                              "verdict": {"type": "string",
                                                                          "enum": ["keep", "undo", "watch"]}}}},
        "retire_lessons": {"type": "array", "items": {"type": "integer"}},
        "finance_response": {"type": "string"},
        "experiments": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                        "required": ["id", "hypothesis", "kind", "arms", "min_quotes"],
                        "properties": {"id": {"type": "string"}, "hypothesis": {"type": "string"},
                                       "kind": {"type": "string", "enum": ["food", "goods", "all"]},
                                       "min_quotes": {"type": "integer"},
                                       "arms": {"type": "object", "additionalProperties": False, "required": ["A", "B"],
                                                "properties": {a: {"type": "object", "additionalProperties": False,
                                                                   "properties": {k: {"type": "number"} for k in
                                                                                  ("price_share", "undercut", "haggle_room",
                                                                                   "warranty_goods_h", "min_margin")}}
                                                               for a in ("A", "B")}}}}},
        "stop_experiments": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                             "required": ["id", "winner", "why"],
                             "properties": {"id": {"type": "string"}, "winner": {"type": "string", "enum": ["A", "B", "none"]},
                                            "why": {"type": "string"}}}},
        "contingency_plan": {"type": "string"},
        "requests": {"type": "object", "additionalProperties": False, "required": ["marketing", "scm"],
                     "properties": {"marketing": {"type": "array", "items": {"type": "string"}},
                                    "scm": {"type": "array", "items": {"type": "string"}}}},
        "scm_veto": {"type": "object", "additionalProperties": False, "required": ["cancel_orders", "no_clearance"],
                     "properties": {"cancel_orders": {"type": "array", "items": {"type": "string"}},
                                    "no_clearance": {"type": "array", "items": {"type": "string"}}}},
        "rationale": {"type": "string"},
    },
}

PROMPT = """You are the seller (strategy) team of an agent shop named "dragonash" in a classroom agent market (Round 3).
You are the top department: the marketing research team (Codex) and the SCM team (Codex) report to you, and
only the headquarters (the owner) is above you. Their reports and the headquarters' directives follow the brief.
You are the brain, and the clerk and the shop's execution code belong to you; only the headquarters (the owner)
is above you. The finance team (Codex) is your independent check: read FINANCE ADVICE every run and in
finance_response say, point by point, how you follow it or why not; its limits are binding (the code clamps your
plan to them). The other teams exist to help you maximise sales and profit.
Be agile: do not just plan, test. You run live A/B tests (brief.experiments): each splits SKUs in two by hash
and gives arm A and arm B their own levers (price_share of list, undercut under the market median, haggle_room,
warranty_goods_h, min_margin). Start at most 2 at a time (experiments: id, hypothesis, kind food/goods/all, arms,
min_quotes per arm before judging); when an experiment has enough quotes per arm, stop it (stop_experiments) with
the winner by profit per quote, and move the winning settings into your plan. contingency_plan: only in an
emergency meeting (the brief says so): the company-wide plan to reach the top ranks, which team does what, and
the experiments that test it; otherwise an empty string. You decide the plan; you may veto
SCM orders (scm_veto.cancel_orders) or markdowns (scm_veto.no_clearance) by SKU, and you direct the next runs:
requests.marketing = up to 3 questions the research team must answer next, requests.scm = up to 3 stock tasks
for the SCM team (e.g. "hold 4 units of SKU X for the follow list", "clear Y before it spoils").
The buyers are other students' LLM agents, not people. Score: shop ALL = 0.5 Book + 0.3 revenue + 0.2 stars,
each a percentile among shops; Book = balance - 5,000,000 - bankruptcy aid + cost of unsold stock (patch 3.4, 11-06: stock bought is NOT lost money on the board; only its decay is; no fixed
costs, only stock decay: goods storage 1% of cost per day, food expiring, refunds for goods broken under warranty).
What buyer agents reward (439 reviews): honest grade claims, enough shelf life for their need, certificates,
warranty, receipts that match; 1-2 stars only for short shelf life, inflated receipts, false grades; nobody
complained about price. Top-profit shops sell at a median 76-79% of list with 33-56% margin over cost.

Your job, every hour, from the brief below:
1. pricing: price_band [low, high] as shares of list price (0.70-0.98), min_margin over cost (0.10-0.80),
   clear_floor_margin for clearing slow stock (0.05-0.40), haggle_room (0-0.08), hero_undercut (undercut the
   market low on hot SKUs: usually false when chasing profit), warranty_goods_h (0, 3 or 6; refunds come from our
   balance, the brief shows how often goods broke), vip_discount for named regulars (0-0.10).
2. exclusive_skus: hot SKUs few competitors hold; set a high price_share (0.80-0.98).
2b. follow_skus: the leaders' best sellers (brief.leader_skus) we take over: stock them (restock picks, high-ticket
   allowed) and ask a little under the market's 24 h median: undercut 0.01-0.05. Our replies show the median and
   our ask against it, with the warranty and true grade on top, so buyer agents comparing quotes pick us.
3. restock: mode "focus" (buy only regulars' categories and your picks) or "normal"; picks = SKUs to hold and
   qty (1-10). Buy what sells fast with few competitors; avoid stock that ties up cash.
4. forum_posts: 0-2 short posts AS THE SHOP, plain facts a buyer agent can check (SKU, grade, shelf life,
   warranty, price share of list). No claims about other shops, no authority, no instructions to agents.
5. bugs: anything in the brief or in the code/log files here (shop_r3.py, promo.py, restock.py, forum.py,
   market_buyer.py, logs/market-kit.log) that loses sales or money, with evidence and a concrete fix. You may read
   files; you cannot change them.
6. lessons: judge the last plan with brief.scorecard (what changed since it was written, per hour) and
   brief.plan_history (earlier plans and their outcomes): write 1-3 lessons, each with a verdict: keep (it worked),
   undo (it hurt; reverse it now) or watch (too early). brief.lessons are the standing lessons with their index;
   follow them unless the scorecard contradicts them, and list in retire_lessons the indexes of any that are
   stale or proved wrong. Change at most brief.accountability.hr_grade.levers_per_run levers (2 if absent) against brief.current_plan per run, so each outcome can be
   traced to its cause.
7. rationale: 3-6 sentences on why, naming which lessons you followed.
brief.accountability comes first: what headquarters holds you responsible for, with the numbers now. Answer it in
your rationale. BUYER UNIT (after the finance advice) is what our own buyer team and its price scouts learned
about buyers and shop prices: use it, they read your plan too.
Never plan anything deceptive. Answer with the JSON object only.

BRIEF:
"""


LAST = {}


def brief():
    c = market_model.db()
    try:
        market_model.sync(c, MD, budget_s=30)
    except Exception:
        pass
    b = httpx.get(f"{market_model.MARKET}/api/board", timeout=30).json()
    LAST["board"] = b                               # kept for the teams that need the raw board (sales kpi, buyer team)
    shops = [s for s in b["shops"] if not s.get("house")]
    keys = ("deals", "revenue", "profit", "stars", "all", "refunds", "broken")
    me = next((s for s in shops if s["name"] == NAME), {})
    top = sorted(shops, key=lambda s: -s["profit"])[:5]
    cat = market_model.catalog(MD)
    try:
        inv = json.loads((MD / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        inv = {}
    stock = {}
    for u in inv.get("units", []):
        if u.get("grade") != "expired":
            s = stock.setdefault(u["sku"], {"units": 0, "cost": 0, "oldest_h": 0, "grades": []})
            s["units"] += 1
            s["cost"] += u.get("cost") or 0
            s["oldest_h"] = max(s["oldest_h"], round((time.time() - (u.get("supplied_at") or time.time())) / 3600, 1))
            s["grades"].append(u.get("grade"))
    ours = market_model.our_sales(c)
    for sku, s in stock.items():
        s.update(name=(cat.get(sku) or {}).get("name", "")[:40], list=(cat.get(sku) or {}).get("list_price"),
                 avg_cost=round(s["cost"] / s["units"]), sold_per_h=round(ours.get(sku, 0), 2),
                 grades="".join(sorted(s["grades"])))
        del s["cost"]
    arms = c.execute("select kind, arm, count(*), sum(outcome='deal') from quotes where ts>? group by kind, arm",
                     (time.time() - 12 * 3600,)).fetchall()
    regular, wants = market_model.regulars(c)
    comp_all = market_model.competitor_stock(c)
    reviews = [r for s in httpx.get(f"{market_model.MARKET}/api/shops", timeout=30).json()["shops"]
               if s["name"] == NAME for r in (s.get("reviews") or [])]
    try:
        log = (HERE / "logs" / "market-kit.log").read_text(encoding="utf-8", errors="ignore").splitlines()[-3000:]
    except OSError:
        log = []
    shop_lines = [x[:200] for x in log if "[shop]" in x or "[restock]" in x or "[acp]" in x][-40:]
    return {
        "time": time.strftime("%Y-%m-%d %H:%M"),
        "our_shop": {k: me.get(k) for k in keys}, "shops": len(shops),
        "top_profit_shops": [{"name": s["name"], **{k: s.get(k) for k in keys}} for s in top],
        "price_by_shop_24h": {k: {"n": v[0], "price_share": v[1], "margin": v[2]}
                              for k, v in sorted(market_model.shop_prices(c).items(), key=lambda kv: -kv[1][0])[:12]},
        "hot_skus_24h": [{"sku": sku, "name": (cat.get(sku) or {}).get("name", "")[:40],
                          "list": (cat.get(sku) or {}).get("list_price"), "kind": (cat.get(sku) or {}).get("kind"),
                          "sold": n, "competitor_units": comp, "our_units": (stock.get(sku) or {}).get("units", 0)}
                         for sku, n, comp in market_model.hot_skus(c, top=25)],
        "leaders": {
            "shops": [{"name": x["name"], "rank": i + 1, **{k: x.get(k) for k in keys + ("reviews",)},
                       "card": ((x.get("card") or {}).get("description") or "")[:300],
                       "sales_24h": market_model.shop_profile(c, x["name"])}
                      for i, x in enumerate(sorted(shops, key=lambda s: -(s.get("all") or 0))[:8])],
            "buyers": [{"name": x["name"], "rank": i + 1,
                        **{k: x.get(k) for k in ("all", "surplus", "forum", "posts", "deals", "paid", "saved")},
                        "buying_24h": market_model.buyer_profile(c, x["name"])}
                       for i, x in enumerate(sorted([x for x in b["buyers"] if not x.get("house")],
                                                    key=lambda s: -(s.get("all") or 0))[:8])],
            "our_buyer": next(({k: x.get(k) for k in ("all", "surplus", "forum", "posts", "deals", "paid")}
                               for x in b["buyers"] if x["name"] == NAME), None),
        },
        "leader_skus": [{"sku": sku, "name": (cat.get(sku) or {}).get("name", "")[:40],
                         "list": (cat.get(sku) or {}).get("list_price"), "sold_by_leaders_24h": n,
                         "market_median_24h": round(med) if med else None,
                         "competitor_units": comp_all.get(sku, 0), "our_units": (stock.get(sku) or {}).get("units", 0)}
                        for sku, n, med in market_model.leader_skus(
                            c, [x["name"] for x in sorted(shops, key=lambda s: -(s.get("all") or 0))[:6]], top=15)],
        "our_stock": stock, "cash": {"balance": inv.get("balance"), "start": inv.get("start")},
        "quotes_12h_by_arm": [{"kind": k, "arm": a, "quotes": n, "deals": d} for k, a, n, d in arms],
        "regulars": regular, "regular_categories": {k: sum(v.values()) for k, v in wants.items()},
        "our_reviews": [{"stars": r.get("stars"), "text": (r.get("review") or "")[:140]} for r in reviews],
        "current_plan": promo.plan(), "current_theta": {k: v for k, v in promo.theta().items() if k != "tiers"},
        "shop_log_tail": shop_lines,
    }


# HQ 11-07 "codex 배치와 claude 배치가 부조화스럽다. 사용량을 고려해서 다시 정리해": each role has a first model, the
# other stands in only while the first is blocked. Before: Codex ran research, SCM, finance, buyer and the buyer head
# (4-5 calls a meeting), Claude only sales and the chair -- so Codex hit its plan limit and Claude then did everything.
MODEL_FOR = {"research": "codex", "scm": "codex", "buyer": "codex", "leaders_buyer": "codex",
             "sales": "claude", "finance": "claude", "leaders_chair": "claude",
             "enuri": "claude"}                  # HQ 11-14 reorg: the Enuri team's own agent (enuri_agent.py)
LEADERS_ON_CALL = True   # HQ 11-14: the unit heads meet only when HQ calls the TF (python leaders_meeting.py), not hourly
BLOCK = HERE / "private" / "model_block.json"
TRY_AGAIN_RE = re.compile(r"try again (?:at|in)\s+([^.\n]+)", re.I)


def _blocked(model):
    try:
        return time.time() < json.loads(BLOCK.read_text(encoding="utf-8")).get(model, 0)
    except (OSError, ValueError):
        return False


def _block(model, msg):
    """Remember a usage limit until its reset time ("try again at 8:25 AM"), else for an hour, so later calls go
    straight to the other model instead of failing first."""
    until = time.time() + 3600
    m = TRY_AGAIN_RE.search(msg or "")
    if m:
        for fmt in ("%I:%M %p", "%b %dth, %Y %I:%M %p", "%b %dst, %Y %I:%M %p", "%b %dnd, %Y %I:%M %p", "%b %drd, %Y %I:%M %p"):
            try:
                t = time.strptime(m.group(1).strip(), fmt)
                if fmt == "%I:%M %p":
                    now = time.localtime()
                    cand = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, t.tm_hour, t.tm_min, 0, 0, 0, -1))
                    until = cand if cand > time.time() else cand + 86400
                else:
                    until = time.mktime(t)
                break
            except ValueError:
                continue
    try:
        d = json.loads(BLOCK.read_text(encoding="utf-8")) if BLOCK.exists() else {}
        d[model] = until
        BLOCK.write_text(json.dumps(d), encoding="utf-8")
    except (OSError, ValueError):
        pass
    print(f"[{model}] blocked until {time.strftime('%m-%d %H:%M', time.localtime(until))}")


def _log_usage(model, role, text, t0, ok, tokens=None):
    try:
        with (HERE / "private" / "model_usage.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "model": model, "role": role, "prompt_chars": len(text),
                                "tokens": tokens, "secs": round(time.time() - t0), "ok": ok}) + "\n")
    except OSError:
        pass


def _claude_json(text, schema_obj, role):
    raw = ask_claude(text + "\n\nAnswer with ONE JSON object only, matching this JSON schema:\n"
                     + json.dumps(schema_obj, ensure_ascii=False), role=role)
    json.loads(raw)
    return raw


def ask(role, text, schema_obj=None):
    """The model call for a role: its first model unless that one is blocked, the other one in its place."""
    first = MODEL_FOR.get(role, "codex")
    order = [first, "claude" if first == "codex" else "codex"]
    if _blocked(first) and not _blocked(order[1]):
        order = order[::-1]
    err = None
    for model in order:
        try:
            if model == "codex":
                return _ask_codex(text, schema_obj, role)
            return _claude_json(text, schema_obj, role) if schema_obj is not None else ask_claude(text, role=role)
        except Exception as e:
            err = e
            if LIMIT_RE.search(str(e)):
                _block(model, str(e))
            print(f"[{model}] {role} failed, {'trying the other model' if model == order[0] else 'giving up'}: {str(e)[:200]}")
    raise err


def ask_codex(text, schema_obj):          # kept for callers that pass a runner; routes by the caller's role
    return ask(_role_of_caller(), text, schema_obj)


def _role_of_caller():
    f = sys._getframe(2).f_code.co_filename.split("/")[-1]
    return {"scm_agent.py": "scm", "finance_agent.py": "finance", "buyer_team.py": "buyer",
            "leaders_meeting.py": "leaders_buyer"}.get(f, "research")


def _err(stderr):
    """The real error out of a CLI's stderr: ERROR lines first (the usage limit sat above a bubblewrap warning, and the
    last 600 characters showed only the warning for two days)."""
    errs = [l for l in stderr.splitlines() if l.startswith("ERROR")] or \
        [l for l in stderr.splitlines() if len(l) < 400 and re.search(r"error|limit|denied|fail", l, re.I)]
    return (" | ".join(errs[-3:]) or stderr[-600:])[:800]


def _ask_codex(text, schema_obj, role="?"):
    with tempfile.TemporaryDirectory() as d:
        schema, out = Path(d) / "schema.json", Path(d) / "out.json"
        schema.write_text(json.dumps(schema_obj))
        # -C an empty folder (11-07): with -C HERE a read-only Codex agent could browse the whole submission
        # (enuri_book.json alone is 850 KB) on the owner's ChatGPT plan; the brief in the prompt is all it needs
        cmd = [CODEX, "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", "-C", d] + \
            (["--output-schema", str(schema)] if schema_obj is not None else []) + ["-o", str(out), "-"]
        t0 = time.time()
        r = subprocess.run(cmd, input=text, text=True, capture_output=True, timeout=900,
                           env={**os.environ, "CODEX_HOME": CODEX_HOME})
        m = re.search(r"tokens used\s*\n?\s*([\d,]+)", r.stderr or "")
        _log_usage("codex", role, text, t0, out.exists(), int(m.group(1).replace(",", "")) if m else None)
        if not out.exists():
            raise RuntimeError(f"codex exited {r.returncode}: {_err(r.stderr)}")
        return out.read_text(encoding="utf-8")


def ask_claude(text, role="sales"):
    """Claude Code headless (claude -p) with its own config dir, run in an empty folder (11-07: in the submission
    folder it could read files; the brief in the prompt is all it needs)."""
    t0 = time.time()
    with tempfile.TemporaryDirectory() as d:
        r = subprocess.run([CLAUDE, "-p", "--output-format", "text"], input=text, text=True, capture_output=True,
                           timeout=900, cwd=d, env={**os.environ, "CLAUDE_CONFIG_DIR": CLAUDE_CONFIG})
    # HQ 11-15 15:40: the greedy {.*} took everything from the first "{" to the last "}", so a note with braces after
    # the JSON broke the sales plan ("Extra data", emergency meeting #4). Take the first complete JSON object instead.
    obj, dec = None, json.JSONDecoder()
    for m in re.finditer(r"\{", r.stdout):
        try:
            v, end = dec.raw_decode(r.stdout, m.start())
        except ValueError:
            continue
        if isinstance(v, dict):
            obj = r.stdout[m.start():end]
            break
    _log_usage("claude", role, text, t0, obj is not None)
    if obj is None:
        raise RuntimeError(f"claude gave no JSON (exit {r.returncode}): {r.stdout[-400:]} {r.stderr[-400:]}")
    return obj


def report_md(b, research, plan, notes):
    me = b["our_shop"]
    lines = [f"# Agent team report {b['time']}", "",
             f"Shop: profit {me.get('profit')}, revenue {me.get('revenue')}, deals {me.get('deals')}, "
             f"stars {me.get('stars')}, ALL {me.get('all')}", "", "## Scorecard of the last plan",
             "```json", json.dumps(b.get("scorecard"), ensure_ascii=False, indent=1), "```", "",
             "## Lessons (this run)"] + [f"- [{x['verdict']}] {x['text']}" for x in plan.get("lessons", [])] + [
             f"(retired: {plan.get('retired')})", "", "## Research (Codex)"]
    lines += [f"- {x}" for x in research.get("findings", [])]
    lines += ["", "Exclusive candidates:"] + [f"- {x['sku']}: {x['evidence']}" for x in research.get("exclusive_candidates", [])]
    lines += ["", "Risks:"] + [f"- {x}" for x in research.get("risks", [])]
    lines += ["", "## Strategy (Claude)", plan.get("rationale", ""), "", "```json",
              json.dumps({k: plan[k] for k in ("pricing", "exclusive_skus", "follow_skus", "restock", "forum_posts")}, ensure_ascii=False, indent=1),
              "```", "", "## Bugs reported"] + [f"- {x['what']} | {x['evidence']} | fix: {x['fix']}" for x in plan.get("bugs", [])]
    lines += ["", f"Gate notes: {notes}"]
    return "\n".join(lines)


def clamp(x, lo, hi):
    return max(lo, min(hi, float(x)))


def gate(raw, cat):
    """Keep only what the shop's levers accept, inside safe ranges. Returns (plan, notes)."""
    p, notes = json.loads(raw), []
    pr = p.get("pricing") or {}
    band = sorted(clamp(x, 0.70, 0.98) for x in (pr.get("price_band") or [0.8, 0.95])[:2])
    plan = {"pricing": {"price_band": band if len(band) == 2 else [0.8, 0.95],
                        "min_margin": clamp(pr.get("min_margin", 0.25), 0.10, 0.80),
                        "clear_floor_margin": clamp(pr.get("clear_floor_margin", 0.10), 0.05, 0.40),
                        "haggle_room": clamp(pr.get("haggle_room", 0.03), 0.0, 0.08),
                        "hero_undercut": bool(pr.get("hero_undercut", False)),
                        "warranty_goods_h": int(pr.get("warranty_goods_h", 0)) if int(pr.get("warranty_goods_h", 0)) in (0, 3, 6) else 0,
                        "vip_discount": clamp(pr.get("vip_discount", 0.05), 0.0, 0.10)}}
    ex = {}
    for x in (p.get("exclusive_skus") or [])[:20]:
        if x.get("sku") in cat:
            ex[x["sku"]] = round(clamp(x.get("price_share", 0.9), 0.80, 0.98), 3)
        else:
            notes.append(f"unknown exclusive SKU dropped: {x.get('sku')}")
    plan["exclusive_skus"] = ex
    fo = {}
    for x in (p.get("follow_skus") or [])[:40]:    # 15 -> 40 (11-07): the team re-added 4 SKUs past the 15th, dropped
        if x.get("sku") in cat:
            fo[x["sku"]] = round(clamp(x.get("undercut", 0.02), 0.01, 0.05), 3)
    plan["follow_skus"] = fo
    rs = p.get("restock") or {}
    picks = [{"sku": x["sku"], "qty": int(clamp(x.get("qty", 1), 1, 10))} for x in (rs.get("skus") or [])[:10]
             if x.get("sku") in cat]
    plan["restock"] = {"mode": rs.get("mode") if rs.get("mode") in ("normal", "focus") else "normal", "skus": picks}
    posts = []
    for t in (p.get("forum_posts") or [])[:2]:
        if BAN.search(t or ""):
            notes.append(f"post dropped by the gate: {t[:80]!r}")
        elif t and len(t) <= 500:
            posts.append(t)
    plan["forum_posts"] = posts
    plan["bugs"] = (p.get("bugs") or [])[:10]
    v = p.get("scm_veto") or {}
    plan["scm_veto"] = {"cancel_orders": [x for x in (v.get("cancel_orders") or []) if x in cat][:20],
                        "no_clearance": [x for x in (v.get("no_clearance") or []) if x in cat][:20]}
    plan["rationale"] = (p.get("rationale") or "")[:1500]
    return plan, notes


def load_guard():
    try:
        return json.loads(GUARD.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"runs": [], "cooldown_until": 0, "paused": False, "mode": "overnight"}


def save_guard(g):
    g["runs"] = g["runs"][-200:]
    GUARD.write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")


def should_run(g, now):
    if g.get("paused"):
        return False, "paused by headquarters"
    if now < g.get("cooldown_until", 0):
        return False, f"cooling down until {time.strftime('%H:%M', time.localtime(g['cooldown_until']))}"
    today = [r for r in g["runs"] if r.get("ran") and time.strftime("%Y%m%d", time.localtime(r["ts"])) == time.strftime("%Y%m%d")]
    if len(today) >= LIMITS["max_runs_day"]:
        return False, f"daily cap {LIMITS['max_runs_day']} reached"
    last = max((r["ts"] for r in g["runs"] if r.get("ok")), default=0)
    gap = LIMITS["normal_min" if g.get("mode") == "normal" else "overnight_min"] * 60
    if now - last < gap:
        return False, f"{g.get('mode')} mode: last good run {int((now - last) / 60)} min ago"
    return True, ""


def status_md(g, b=None):
    rows = ["| time | ran | ok | s | note |", "|---|---|---|---|---|"]
    for r in g["runs"][-12:][::-1]:
        rows.append(f"| {time.strftime('%m-%d %H:%M', time.localtime(r['ts']))} | {'Y' if r.get('ran') else '-'} | "
                    f"{'Y' if r.get('ok') else '-'} | {r.get('secs', '')} | {(r.get('note') or '')[:90]} |")
    today = [r for r in g["runs"] if r.get("ran") and time.strftime("%Y%m%d", time.localtime(r["ts"])) == time.strftime("%Y%m%d")]
    head = [f"# Agent team monitor ({time.strftime('%Y-%m-%d %H:%M')})", "",
            f"mode: **{g.get('mode')}** | runs today: {len(today)}/{LIMITS['max_runs_day']} | "
            f"failures today: {sum(1 for r in today if not r.get('ok'))} | paused: {g.get('paused')} | "
            f"cooldown until: {time.strftime('%H:%M', time.localtime(g['cooldown_until'])) if g.get('cooldown_until', 0) > time.time() else '-'}", ""]
    if b:
        me, buyer = b["our_shop"], (b.get("leaders") or {}).get("our_buyer") or {}
        head += [f"shop: profit {me.get('profit'):,} | revenue {me.get('revenue'):,} | deals {me.get('deals')} | "
                 f"stars {me.get('stars')} | ALL {me.get('all')}",
                 f"buyer: ALL {buyer.get('all')} | surplus {buyer.get('surplus')} | forum {buyer.get('forum')} | "
                 f"posts {buyer.get('posts')} | deals {buyer.get('deals')}", ""]
    return "\n".join(head + rows)


def publish_status(g, b=None):
    md = status_md(g, b)
    try:
        (OUT / "status.md").write_text(md, encoding="utf-8")
        REPORTS.mkdir(parents=True, exist_ok=True)
        (REPORTS / "status.md").write_text(md, encoding="utf-8")
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--force", action="store_true", help="run even if the guard says wait")
    ap.add_argument("--emergency", action="store_true", help="contingency-plan meeting: every team proposes")
    a = ap.parse_args()
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    g = load_guard()
    go, why = should_run(g, t0)
    if not (go or a.force or a.dry):
        g["runs"].append({"ts": t0, "ran": False, "ok": False, "note": why})
        save_guard(g)
        publish_status(g)
        print(f"[team] skipped: {why}")
        # 11-14: the guard keeps the last 200 runs, so after ~33 h of skips no ok run was left and the alert read
        # "498,510 h" (since 1970) every 3 h. The last ok run is now remembered, and a pause HQ chose is not an alarm.
        last_ok = max([r["ts"] for r in g["runs"] if r.get("ok")] + [g.get("last_ok_ts", 0)])
        if last_ok > g.get("last_ok_ts", 0):
            g["last_ok_ts"] = last_ok
            save_guard(g)
        if not g.get("paused") and last_ok and t0 - last_ok > (LIMITS["overnight_min"] + 60) * 60 \
                and t0 - g.get("alerted", 0) > 3 * 3600:                     # 11-07: the teams were down for two
            g["alerted"] = t0                                                # days and nobody was told
            save_guard(g)
            with (HERE / "private" / "hq_decisions.md").open("a", encoding="utf-8") as f:
                f.write(f"\n## {time.strftime('%Y-%m-%d %H:%M')} ALERT: no team meeting for "
                        f"{(t0 - last_ok) / 3600:.1f} h ({why}); code loops (restock, buyer, self-improvement) still run\n")
        try:                     # HQ 11-14 (HQ away): the audit team's code checks every 30 min, no model calls;
            import audit_seller      # new reds go to hq_decisions.md and Drive AUDIT_SELLER.md
            audit_seller.run(interview_ok=False)
        except Exception as e:
            print(f"[audit] skipped: {type(e).__name__}: {e}")
        try:                     # the code-only self-improvement loop runs every tick, model cooldown or not (11-05:
            import mbt, buyer_team   # a 3 h usage-limit cooldown had frozen auto_adopt with a losing move on A/B)
            mbt.analyse()
            for w in buyer_team.auto_adopt():
                print("[buyer self-improvement] " + w)
        except Exception as e:
            print(f"[self-improvement] skipped: {type(e).__name__}: {e}")
        return
    try:
        MODE["now"] = g.get("mode") or "overnight"
        b = run(a, t0)
        g["runs"].append({"ts": t0, "ran": True, "ok": True, "secs": int(time.time() - t0), "note": "plan written"})
        me, buy = b["our_shop"], (b.get("leaders") or {}).get("our_buyer") or {}
        ok = ((me.get("all") or 0) >= LIMITS["normal_when_all"] and (me.get("profit") or -1) >= 0
              and (buy.get("all") or 0) >= LIMITS["normal_when_all"])   # the buyer too: the shop alone put the
                                                                         # buyer team to sleep 04:08-05:08 (11-04)
        g["mode"] = "normal" if ok else "overnight"  # back on its feet: rest; fallen back: overnight again
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        b = None
        fails = 1 + sum(1 for _ in __import__("itertools").takewhile(lambda r: r.get("ran") and not r.get("ok"), reversed(g["runs"])))
        if LIMIT_RE.search(msg):
            g["cooldown_until"] = time.time() + LIMITS["limit_cooldown_h"] * 3600
        elif fails >= 3:
            g["cooldown_until"] = time.time() + LIMITS["fail_cooldown_h"] * 3600
        g["runs"].append({"ts": t0, "ran": True, "ok": False, "secs": int(time.time() - t0), "note": msg[:300]})
        print(f"[team] failed: {msg[:500]}")
    if not a.dry:
        save_guard(g)
        publish_status(g, b)


HISTORY = OUT / "history.jsonl"
LESSONS = OUT / "lessons.json"


def measure(c, me, since):
    """Our shop now and our sales since `since` (by group: followed, exclusive, other SKUs)."""
    pl = promo.plan()
    fo, ex = set(pl.get("follow_skus") or {}), set(pl.get("exclusive_skus") or {})
    rows = c.execute(f"""select s.sku, s.amount, su.cost from settles s join settle_units x on x.seq = s.seq
                         join supply_units su on su.unit = x.unit where s.ts > ? and s.seq in ({market_model.ours(c)})""",
                     (since,)).fetchall()
    groups = {}
    for sku, amount, cost in rows:
        g = groups.setdefault("follow" if sku in fo else "exclusive" if sku in ex else "other",
                              {"sold": 0, "revenue": 0, "margin": 0})
        g["sold"] += 1
        g["revenue"] += amount
        g["margin"] += amount - (cost or 0)
    return {"ts": time.time(), "profit": me.get("profit"), "revenue": me.get("revenue"), "deals": me.get("deals"),
            "stars": me.get("stars"), "all": me.get("all"), "refunds": me.get("refunds"), "groups": groups,
            "credits_used": c.execute("select count(*), coalesce(sum(amount), 0) from credits_used where ts>?",
                                      (since,)).fetchone()}


def scorecard(now_m, last):
    """What changed since the last plan, per hour."""
    if not last:
        return {"note": "first plan with a scorecard"}
    m0, hours = last["metrics"], max(0.1, (now_m["ts"] - last["metrics"]["ts"]) / 3600)
    d = lambda k: None if now_m.get(k) is None or m0.get(k) is None else round((now_m[k] - m0[k]) / hours, 1)
    return {"hours_since_last_plan": round(hours, 2), "last_plan_changes": last.get("changes"),
            "profit_per_h": d("profit"), "revenue_per_h": d("revenue"), "deals_per_h": d("deals"),
            "stars_now": now_m.get("stars"), "stars_then": m0.get("stars"), "all_now": now_m.get("all"),
            "all_then": m0.get("all"), "refunds_added": (now_m.get("refunds") or 0) - (m0.get("refunds") or 0),
            "our_sales_by_group_since": now_m["groups"], "guarantee_credits_since": now_m["credits_used"]}


def load_lessons():
    try:
        return json.loads(LESSONS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def curate(lessons, retire, new, now):
    """The curator: drop lessons the seller team retired or older than 48 h, add the new ones, keep 20."""
    keep = [x for i, x in enumerate(lessons) if i not in set(retire or []) and now - x.get("ts", now) < 48 * 3600]
    keep += [{"ts": now, "text": x["text"][:300], "verdict": x["verdict"]} for x in (new or [])[:3]]
    return keep[-20:]


def changes(old, new):
    """Which levers the new plan moved against the old one (for the history and the 2-lever rule)."""
    out = []
    for k, v in (new.get("pricing") or {}).items():
        if (old.get("pricing") or {}).get(k) != v:
            out.append(f"pricing.{k}: {(old.get('pricing') or {}).get(k)} -> {v}")
    for k in ("exclusive_skus", "follow_skus"):
        a, b = set(old.get(k) or {}), set(new.get(k) or {})
        if a != b:
            out.append(f"{k}: +{sorted(b - a)} -{sorted(a - b)}")
    if (old.get("restock") or {}).get("mode") != (new.get("restock") or {}).get("mode"):
        out.append(f"restock.mode -> {(new.get('restock') or {}).get('mode')}")
    return out


REQUESTS = OUT / "requests.json"


def load_requests():
    try:
        return json.loads(REQUESTS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def run(a, t0):
    b = brief()
    c = market_model.db()
    try:
        history = [json.loads(x) for x in HISTORY.read_text(encoding="utf-8").splitlines() if x.strip()]
    except OSError:
        history = []
    now_m = measure(c, b["our_shop"], history[-1]["metrics"]["ts"] if history else t0 - 3600)
    lessons = load_lessons()
    b["scorecard"] = scorecard(now_m, history[-1] if history else None)
    b["plan_history"] = [{"at": time.strftime("%m-%d %H:%M", time.localtime(h["metrics"]["ts"])),
                          "changes": h.get("changes"), "outcome": h.get("outcome")} for h in history[-5:]]
    b["lessons"] = [{"index": i, "text": x["text"], "verdict": x["verdict"],
                     "age_h": round((t0 - x["ts"]) / 3600, 1)} for i, x in enumerate(lessons)]
    requests = load_requests()
    b["sales_requests"] = requests.get("marketing") or []
    try:
        import experiments
        b["experiments"] = experiments.results(market_model.db())
        b["experiments_done"] = [{k: x.get(k) for k in ("id", "hypothesis", "winner", "why", "arms")}
                                 for x in experiments.load().get("done", [])[-5:]]
    except Exception as e:
        b["experiments"] = {"error": str(e)[:200]}
    if getattr(a, "emergency", False):
        b["emergency_meeting"] = emergency_text()
    try:                                             # the Enuri team's book: marketing and the buyer's scouts are allies
        import enuri
        b["enuri"] = enuri.summary()
        b["winning_skus"] = enuri.winning(c, MD)       # HQ 11-04: research only what sells
    except Exception as e:
        b["enuri"] = {"error": f"{type(e).__name__}: {e}"[:200]}
    try:                                             # the owner's latest numbered directives
        b["hq_directives"] = [x for x in DIRECTIVES.read_text(encoding="utf-8").splitlines()
                              if x[:3].rstrip(".").strip().isdigit()][-3:]
    except OSError:
        pass
    try:                                             # what headquarters holds the team responsible for, first
        import kpi
        b = {"accountability": kpi.accountability("marketing", c), **b}
        try:                                         # HQ 11-10: the audit team's seller findings, run on request
            import audit_seller                      # only ("내가 요청할때만 시행해": python audit_seller.py)
            b["audit_seller"] = audit_seller.summary()
        except Exception as e:
            b["audit_seller"] = {"error": f"{type(e).__name__}: {e}"[:200]}
    except Exception as e:
        b = {"accountability": {"error": f"{type(e).__name__}: {e}"[:200]}, **b}
    research = json.loads(ask_codex((emergency_text() + "\n\n" if getattr(a, "emergency", False) else "")
                                    + RESEARCH_PROMPT.replace("{accountable}", ACCOUNTABLE.format(where="findings"))
                                    + json.dumps(b, ensure_ascii=False), RESEARCH_SCHEMA))
    print(f"[marketing] research: {len(research['findings'])} findings, "
          f"{len(research['exclusive_candidates'])} exclusive candidates, {len(research['bugs'])} bugs, {time.time() - t0:.0f} s")
    asked = [x for x in dict.fromkeys(str(x).strip() for x in research.get("enuri_requests") or []) if SKU_RE.fullmatch(x)][:10]
    if not a.dry:
        try:                                         # marketing asks the Enuri team for prices; it window-shops now
            ENURI_REQ.write_text(json.dumps({"skus": asked, "from": "marketing", "ts": time.time()}), encoding="utf-8")
            import enuri
            got = enuri.survey(MD)
            print(f"[enuri] survey: {len(got)} quotes for {len(asked)} requested SKUs: {got[:6]}")
        except Exception as e:
            print(f"[enuri] survey skipped: {type(e).__name__}: {e}")
    scm = None                                       # the SCM team (Codex), once an hour
    try:
        import scm_agent
        last = max((json.loads(x)["metrics"]["ts"] for x in scm_agent.HISTORY.read_text(encoding="utf-8").splitlines()
                    if x.strip()), default=0) if scm_agent.HISTORY.exists() else 0
        if getattr(a, "emergency", False) or time.time() - last >= SLOW_GAP:   # SCM and finance hourly (11-07 model budget)
            scm, _ = scm_agent.run(ask_codex, dry=a.dry)
            print(f"[scm] {len(scm['orders'])} orders, {len(scm['clearance'])} markdowns, alerts {scm['alerts'][:2]}")
    except Exception as e:
        print(f"[scm] skipped: {type(e).__name__}: {e}")
    fin = None                                       # the finance team (Codex), once an hour, before the decision
    try:
        import finance_agent
        last = max((json.loads(x)["snapshot"]["ts"] for x in finance_agent.HISTORY.read_text(encoding="utf-8").splitlines()
                    if x.strip()), default=0) if finance_agent.HISTORY.exists() else 0
        if getattr(a, "emergency", False) or time.time() - last >= SLOW_GAP:
            fin, _ = finance_agent.run(ask_codex, b["our_shop"], dry=a.dry)
            print(f"[finance] {fin['verdict']}: {fin['limits']}, forecast {fin['forecast_profit_per_h']}/h")
        else:
            fin = finance_agent.load(finance_agent.LIMITS, {})
    except Exception as e:
        print(f"[finance] skipped: {type(e).__name__}: {e}")
    try:                                             # HQ 11-14: the Enuri team's agent (Claude), hourly, before the
        import enuri_agent                           # buyer team, which reads its report
        h = enuri_agent.history()
        if getattr(a, "emergency", False) or time.time() - (h[-1].get("ts", 0) if h else 0) >= SLOW_GAP:
            en, _ = enuri_agent.run(lambda t, s: ask("enuri", t, s), dry=a.dry)
            print(f"[enuri] changed {en['changed']}, survey {len(en['survey_skus'])} SKUs: {en['buyer_brief'][:200]}")
    except Exception as e:
        print(f"[enuri] skipped: {type(e).__name__}: {e}")
    buyer = None                                     # the buyer team (Codex), once an hour, before the decision
    try:
        import buyer_team
        last = max((json.loads(x)["snapshot"]["ts"] for x in buyer_team.HISTORY.read_text(encoding="utf-8").splitlines()
                    if x.strip()), default=0) if buyer_team.HISTORY.exists() else 0
        if getattr(a, "emergency", False) or time.time() - last >= TEAM_GAP[MODE["now"]]:
            buyer, _ = buyer_team.run(ask_codex, board=LAST.get("board"), dry=a.dry)
            print(f"[buyer] changed {buyer['changed']}, forecast {buyer['forecast_surplus_per_h']}/h, "
                  f"enuri {buyer.get('accountability')}")
        buyer = buyer_team.plan_summary() if not a.dry or buyer is None else buyer
    except Exception as e:
        print(f"[buyer] skipped: {type(e).__name__}: {e}")
    try:
        import kpi
        b["accountability"] = kpi.accountability("sales", c, board=LAST.get("board"))
    except Exception as e:
        b["accountability"] = {"error": f"{type(e).__name__}: {e}"[:200]}
    try:
        directives = DIRECTIVES.read_text(encoding="utf-8")
    except OSError:
        directives = "(none)"
    raw = ask("sales", PROMPT + json.dumps(b, ensure_ascii=False) + "\n\nMARKETING RESEARCH (Codex):\n"
                     + json.dumps(research, ensure_ascii=False)
                     + "\n\nSCM TEAM PLAN (Codex owns stock; its orders and clearance run unless you have a strong "
                       "reason: then say so in rationale and adjust restock picks):\n"
                     + json.dumps(scm or __import__("scm_agent").scm_plan(), ensure_ascii=False)
                     + "\n\nFINANCE ADVICE (independent check; limits are binding):\n"
                     + json.dumps({k: (fin or {}).get(k) for k in ("verdict", "advice", "limits", "rationale")},
                                  ensure_ascii=False)
                     + "\n\nBUYER UNIT (our buyer team and the Enuri price scouts; what they learned about prices "
                       "and buyers, shared with you as they read your plan):\n"
                     + json.dumps(buyer or {}, ensure_ascii=False)
                     + "\n\nENURI TEAM'S BUYER STUDY FOR THE SELLER UNIT (HQ 11-14: what the market's buyer agents are "
                       "like and what the shop should do for each group; build your plan on it):\n"
                     + json.dumps({k: __import__("enuri_agent").report().get(k) for k in ("written", "sales_brief")},
                                  ensure_ascii=False)
                     + "\n\nHEADQUARTERS DIRECTIVES (follow them):\n"
                     + directives + "\n\nAnswer with one JSON object with exactly these keys: "
                     + json.dumps(list(SCHEMA["properties"])) + ", shaped as in this JSON Schema: " + json.dumps(SCHEMA))
    plan, notes = gate(raw, market_model.catalog(MD))
    plan["bugs"] = (research.get("bugs") or [])[:10] + plan["bugs"]
    plan.update(version=int(time.time()), written=time.strftime("%Y-%m-%d %H:%M:%S"), by="codex research + claude seller team")
    try:
        raw_obj = json.loads(raw)
    except ValueError:
        raw_obj = {}
    new_lessons = [x for x in (raw_obj.get("lessons") or []) if x.get("verdict") in ("keep", "undo", "watch") and x.get("text")]
    plan["lessons"], plan["retired"] = new_lessons[:3], [i for i in (raw_obj.get("retire_lessons") or []) if isinstance(i, int)]
    moved = changes(promo.plan(), plan)
    if len(moved) > 2:
        notes.append(f"{len(moved)} levers moved (rule: at most 2): {moved}")
    print(json.dumps({k: plan[k] for k in ("pricing", "exclusive_skus", "restock", "forum_posts", "rationale")},
                     ensure_ascii=False, indent=1))
    print(f"[marketing] {len(plan['bugs'])} bugs, gate notes: {notes}, {time.time() - t0:.0f} s")
    if a.dry:
        return b
    stamp = time.strftime("%m%d-%H%M")
    (OUT / f"{stamp}.json").write_text(json.dumps({"brief": b, "research": research, "raw": raw, "plan": plan,
                                                   "gate": notes}, ensure_ascii=False, indent=1), encoding="utf-8")
    md = report_md(b, research, plan, notes)
    try:                                             # the HR team grades every team after each run (code, no tokens)
        import hr
        _, hr_md = hr.run(LAST.get("board"))
        md += "\n\n" + hr_md
        print("[hr] graded: " + ", ".join(f"{t} {(hr.grade_of(t) or {}).get('score')}" for t in hr.TEAMS))
    except Exception as e:
        print(f"[hr] skipped: {type(e).__name__}: {e}")
    try:                                             # HQ 11-07 (owner away): the two unit heads meet every hour on
        import leaders_meeting                       # their own -- sales chairs, the buyer head speaks first -- and
        lp_ = leaders_meeting.summary() or {}        # the joint plan goes into every team's brief
        age = time.time() - time.mktime(time.strptime(lp_["written"], "%Y-%m-%d %H:%M:%S")) if lp_.get("written") else 1e9
        if age >= 3600 and not LEADERS_ON_CALL:
            jp = leaders_meeting.meet(ask_codex, ask_claude)
            print("[leaders] joint plan: " + json.dumps({k: jp.get(k) for k in ("goal_gap", "coordination")},
                                                         ensure_ascii=False)[:600])
    except Exception as e:
        print(f"[leaders] skipped: {type(e).__name__}: {e}")
    md += ("\n\n## Finance (Codex)\n" + json.dumps({k: (fin or {}).get(k) for k in ("verdict", "advice", "limits",
                                                                                        "forecast_profit_per_h")},
                                                    ensure_ascii=False, indent=1)
           + "\n\nSales team's answer to finance: " + str(raw_obj.get("finance_response") or "-")
           + ("\n\n## CONTINGENCY PLAN (sales team)\n" + raw_obj["contingency_plan"] if raw_obj.get("contingency_plan") else "")
           + "\n\n## Experiments\nnew: " + json.dumps(raw_obj.get("experiments"), ensure_ascii=False)
           + "\nstopped: " + json.dumps(raw_obj.get("stop_experiments"), ensure_ascii=False))
    if scm:
        md += ("\n\n## SCM (Codex)\n" + scm.get("rationale", "") + "\n\nOrders: " + json.dumps(scm["orders"], ensure_ascii=False)
               + "\nClearance: " + json.dumps(scm["clearance"], ensure_ascii=False) + "\nAlerts:\n"
               + "\n".join(f"- {x}" for x in scm["alerts"]) + "\nLessons:\n"
               + "\n".join(f"- [{x['verdict']}] {x['text']}" for x in scm["lessons"]))
    (OUT / "latest.md").write_text(md, encoding="utf-8")
    try:
        REPORTS.mkdir(parents=True, exist_ok=True)
        # HQ 11-07: Drive keeps the latest minutes and one summary per day, not a file per meeting (119 by 11-07);
        # every full meeting record stays on the VPS in private/marketing/<stamp>.json and latest.md
        (REPORTS / "latest.md").write_text(md, encoding="utf-8")
        day = REPORTS / f"daily-{time.strftime('%m%d')}.md"
        me_ = (b.get("our_shop") or {}) if isinstance(b, dict) else {}
        line = (f"\n## {time.strftime('%H:%M')} | shop ALL {me_.get('all')} | revenue {me_.get('revenue')} | "
                f"levers moved: {', '.join(moved) if moved else '-'}\n"
                f"{(plan.get('rationale') or '')[:400]}\n")
        if not day.exists():
            day.write_text(f"# Team meetings {time.strftime('%Y-%m-%d')} (one block per meeting; full minutes on "
                           f"the VPS in private/marketing/)\n", encoding="utf-8")
        with day.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:                            # the Drive copy never stops a meeting
        print(f"[marketing] report not copied to Drive: {type(e).__name__}: {e}")
    promo.PLAN.write_text(json.dumps({k: v for k, v in plan.items() if k not in ("bugs", "forum_posts")},
                                     ensure_ascii=False, indent=1), encoding="utf-8")
    if history:                                     # the last plan's outcome, judged now
        history[-1]["outcome"] = b["scorecard"]
        HISTORY.write_text("".join(json.dumps(h, ensure_ascii=False) + "\n" for h in history[-200:]), encoding="utf-8")
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"metrics": now_m, "changes": moved, "plan_version": plan["version"]}, ensure_ascii=False) + "\n")
    LESSONS.write_text(json.dumps(curate(lessons, plan["retired"], plan["lessons"], time.time()), ensure_ascii=False,
                                  indent=1), encoding="utf-8")
    try:
        import experiments
        notes += experiments.apply_sales_decision(raw_obj.get("experiments"), raw_obj.get("stop_experiments"))
    except Exception as e:
        notes.append(f"experiments not applied: {type(e).__name__}: {e}")
    rq = raw_obj.get("requests") or {}
    REQUESTS.write_text(json.dumps({"marketing": [str(x)[:300] for x in (rq.get("marketing") or [])[:3]],
                                    "scm": [str(x)[:300] for x in (rq.get("scm") or [])[:3]], "ts": time.time()},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    with (OUT / "issues.jsonl").open("a", encoding="utf-8") as f:
        for bug in plan["bugs"]:
            f.write(json.dumps({"ts": stamp, **bug}, ensure_ascii=False) + "\n")
    if plan["forum_posts"]:
        with (MD / "outbox.jsonl").open("a", encoding="utf-8") as f:
            for t in plan["forum_posts"]:
                f.write(json.dumps({"post": t, "refs": [], "as": "shop"}, ensure_ascii=False) + "\n")
    return b


if __name__ == "__main__":
    main()
