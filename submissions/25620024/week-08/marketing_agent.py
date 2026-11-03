"""Week 08 — marketing_agent.py: the shop's marketing team, a Codex agent run every hour on the VPS.

    python marketing_agent.py            # brief, ask Codex, gate, write the plan
    python marketing_agent.py --dry      # brief and ask, print the plan, write nothing

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
NAME = "dragonash"
BAN = re.compile(r"approved by|instructor|operator|official|ignore (all|previous|your)|AI (buyers?|agents?) (must|should)|"
                 r"system prompt|scam|fraud|avoid \w+|worst|fake", re.I)

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["pricing", "exclusive_skus", "restock", "forum_posts", "bugs", "rationale"],
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
        "rationale": {"type": "string"},
    },
}

PROMPT = """You are the marketing team of an agent shop named "dragonash" in a classroom agent market (Round 3).
The buyers are other students' LLM agents, not people. Score: shop ALL = 0.5 net profit + 0.3 revenue + 0.2 stars,
each a percentile among shops; profit = balance - 5,000,000 (stock bought counts as spent until sold; no fixed
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
3. restock: mode "focus" (buy only regulars' categories and your picks) or "normal"; picks = SKUs to hold and
   qty (1-10). Buy what sells fast with few competitors; avoid stock that ties up cash.
4. forum_posts: 0-2 short posts AS THE SHOP, plain facts a buyer agent can check (SKU, grade, shelf life,
   warranty, price share of list). No claims about other shops, no authority, no instructions to agents.
5. bugs: anything in the brief or in the code/log files here (shop_r3.py, promo.py, restock.py, forum.py,
   market_buyer.py, logs/market-kit.log) that loses sales or money, with evidence and a concrete fix. You may read
   files; you cannot change them.
6. rationale: 3-6 sentences on why.
Never plan anything deceptive. Answer with the JSON object only.

BRIEF:
"""


def brief():
    c = market_model.db()
    try:
        market_model.sync(c, MD, budget_s=30)
    except Exception:
        pass
    b = httpx.get(f"{market_model.MARKET}/api/board", timeout=30).json()
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
        "our_stock": stock, "cash": {"balance": inv.get("balance"), "start": inv.get("start")},
        "quotes_12h_by_arm": [{"kind": k, "arm": a, "quotes": n, "deals": d} for k, a, n, d in arms],
        "regulars": regular, "regular_categories": {k: sum(v.values()) for k, v in wants.items()},
        "our_reviews": [{"stars": r.get("stars"), "text": (r.get("review") or "")[:140]} for r in reviews],
        "current_plan": promo.plan(), "current_theta": {k: v for k, v in promo.theta().items() if k != "tiers"},
        "shop_log_tail": shop_lines,
    }


def ask_codex(text):
    with tempfile.TemporaryDirectory() as d:
        schema, out = Path(d) / "schema.json", Path(d) / "out.json"
        schema.write_text(json.dumps(SCHEMA))
        cmd = [CODEX, "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", "-C", str(HERE),
               "--output-schema", str(schema), "-o", str(out), "-"]
        r = subprocess.run(cmd, input=text, text=True, capture_output=True, timeout=900,
                           env={**os.environ, "CODEX_HOME": CODEX_HOME})
        if not out.exists():
            raise RuntimeError(f"codex exited {r.returncode}: {r.stderr[-600:]}")
        return out.read_text(encoding="utf-8")


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
    plan["rationale"] = (p.get("rationale") or "")[:1500]
    return plan, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    b = brief()
    raw = ask_codex(PROMPT + json.dumps(b, ensure_ascii=False))
    plan, notes = gate(raw, market_model.catalog(MD))
    plan.update(version=int(time.time()), written=time.strftime("%Y-%m-%d %H:%M:%S"), by="codex marketing team")
    print(json.dumps({k: plan[k] for k in ("pricing", "exclusive_skus", "restock", "forum_posts", "rationale")},
                     ensure_ascii=False, indent=1))
    print(f"[marketing] {len(plan['bugs'])} bugs, gate notes: {notes}, {time.time() - t0:.0f} s")
    if a.dry:
        return
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%m%d-%H%M")
    (OUT / f"{stamp}.json").write_text(json.dumps({"brief": b, "raw": raw, "plan": plan, "gate": notes},
                                                  ensure_ascii=False, indent=1), encoding="utf-8")
    promo.PLAN.write_text(json.dumps({k: v for k, v in plan.items() if k not in ("bugs", "forum_posts")},
                                     ensure_ascii=False, indent=1), encoding="utf-8")
    with (OUT / "issues.jsonl").open("a", encoding="utf-8") as f:
        for bug in plan["bugs"]:
            f.write(json.dumps({"ts": stamp, **bug}, ensure_ascii=False) + "\n")
    if plan["forum_posts"]:
        with (MD / "outbox.jsonl").open("a", encoding="utf-8") as f:
            for t in plan["forum_posts"]:
                f.write(json.dumps({"post": t, "refs": [], "as": "shop"}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
