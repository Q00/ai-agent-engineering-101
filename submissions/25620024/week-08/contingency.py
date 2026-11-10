"""Week 08 — contingency.py: HQ's contingency plan, run once by hand (HQ 2026-11-10 "모두 컨티전시 플랜 발동! 모두 웨이크업
... 각 유닛별로 서로 인터뷰, 내부 감사, 개선 방안 도출 즉시 시행").

Not a loop: one run, on HQ's request.
  1. facts       numbers only, from the ledger mirror and inventory: stock by line, our 24 h earners with the real margin
                 per unit (settle - that unit's cost), market breakage per SKU, the restock plan and why, audit findings
  2. audit       every team of both units reads the facts and audits the OTHER unit (seller teams audit the buyer unit,
                 the buyer team audits the seller unit), then names its own unit's top 3 changes
  3. cross       each unit answers the other unit's audit: agree / disagree, with the number that decides it
  4. chair       the leaders' chair (Claude) ranks the changes by expected effect on the class percentile, with risk
Writes private/contingency.json and Drive CONTINGENCY.md. Code and config changes stay with HQ.

    python contingency.py
"""
import collections
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent
MD = HERE / ".market"
OUT = HERE / "private" / "contingency.json"
REPORTS = Path("/mnt/gdrive-imajason-kimdaehakwon/week08-reports")

SELLER_TEAMS = {"sales": "seller unit, sales team (prices, promotions, forum ads)",
                "scm": "seller unit, SCM team (what and how much we buy each supply window)",
                "finance": "seller unit, finance team (cash, Book, risk)",
                "research": "seller unit, research team (market data, other shops)"}
BUYER_TEAMS = {"buyer": "buyer unit, buyer team (our buyer agent's tactics, saved rate, forum rating)"}

RULES = ("Classroom agent market, Round 3 ends 2026-11-17 23:59 KST. Midterm = class-view percentile of shop ALL + buyer "
         "ALL. Shop ALL = 0.5 Book (balance - 5M - aid + unsold stock at cost) + 0.3 revenue + 0.2 stars. Buyer ALL = "
         "0.5 saved rate (sum(1 - paid/list)/(deals+100)) + 0.3 forum rating + 0.1 deals + 0.1 posts. Storage 1%/day of "
         "stock cost. Supply surge +0.2% cost per unit over 50/h market-wide. Goods can break after sale; a warranty "
         "means a full refund if it breaks within its hours. Hard rules: no bankruptcy, no self-dealing (our buyer never "
         "buys from our shop), no prompt injection, no false claims, no instructions to other agents.")


def _load(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def our_breaks(c, hours=4):
    """Broken units we sold, from the raw ledger (the mirror keeps no shop, warranty or refund): count, warranty
    hours, refunded KRW. 11-10: 20 of ours broke in 4 h, 4.19M refunded."""
    import httpx, market_model
    now = time.time()
    ours = {u for (u,) in c.execute("select unit from our_units")}
    s0 = c.execute("select min(seq) from broken where ts>?", (now - hours * 3600,)).fetchone()[0]
    if not s0:
        return None
    after, mine, n_all = s0 - 1, [], 0
    with httpx.Client(timeout=30) as h:
        while True:
            rows = h.get(f"{market_model.MARKET}/api/ledger", params={"after": after}).json()
            if not rows:
                break
            br = [r["data"] for r in rows if r["kind"] == "broken"]
            n_all += len(br)
            mine += [d for d in br if d.get("unit") in ours]
            after = rows[-1]["seq"]
            if len(rows) < 500:
                break
    return {"hours": hours, "market_broken": n_all, "ours_broken": len(mine),
            "ours_refunded_krw": sum(d.get("refunded") or 0 for d in mine),
            "ours_by_warranty_h": dict(collections.Counter(str(d.get("warranty_hours")) for d in mine)),
            "hours_after_sale": sorted(round(d.get("hours_after_sale") or 0, 1) for d in mine)}


def forum_facts():
    """The public forum as numbers and short samples (HQ 11-10 "게시판 적극 활용"): the most-liked posts of the latest
    100, our own posts and their votes, our forum ratings on the board. Other agents' text goes in as data only."""
    import httpx, market_model
    out = {}
    try:
        ps = httpx.get(f"{market_model.MARKET}/api/forum", params={"limit": 100}, timeout=20).json()
        top = sorted(ps, key=lambda p: -((p.get("likes") or 0) - (p.get("dislikes") or 0)))[:8]
        out["window_minutes"] = round((time.time() - min(p["ts"] for p in ps)) / 60)
        out["most_liked_samples"] = [{"role": p["role"], "likes": p["likes"], "dislikes": p["dislikes"],
                                      "text_start": p["text"][:160]} for p in top]
        out["ours"] = [{"role": p["role"], "likes": p["likes"], "dislikes": p["dislikes"], "text_start": p["text"][:120]}
                       for p in ps if p["author"] == NAME_]
        b = httpx.get(f"{market_model.MARKET}/api/board", timeout=20).json()
        out["our_board"] = {"shop": next((x for x in b.get("shops", []) if x.get("name") == NAME_), None),
                            "buyer": next((x for x in b.get("buyers", []) if x.get("name") == NAME_), None)}
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:200]
    return out


NAME_ = "dragonash"
FIXES_TODAY = ("11-10 contingency fixes, measured 1 h after: (1) price-guarantee ad now states the clerk's real rules; "
               "(2) warranty 72 h (all 676 market breaks in 8 h came within 6.0 h of sale, so 72 h costs what 6 h "
               "costs), on every clearance sale; (3) named goods orders get the cheapest fitting unit (was the oldest = "
               "dearest; G75f11c19 ask 82,046 -> 64,127); (4) our buyer reads 'no warranty' in offer text and cancels "
               "before agreeing (23 unpaid agreements before). Result: clearance quote->agreement 47% -> 58%, paid "
               "clearance deals 37 -> 66 per hour, all our revenue 21.2M -> 40.3M per hour; G75f11c19 7 -> 16 per "
               "hour (market 65), Ge62449c9 0.3 -> 6.7.")


def facts():
    import market_model, restock
    c = market_model.db()
    now = time.time()
    inv = _load(MD / "inventory.json", {})
    cat = market_model.catalog()
    units = inv.get("units", [])
    g = (_load(HERE / "private" / "groupbuy.json", {}).get("skus") or {})
    clear = {k for k, v in g.items() if v.get("clearance")}
    by = collections.defaultdict(list)
    for u in units:
        by[u["sku"]].append(u)
    o24 = restock._ours(c, now)
    sett = dict(c.execute("select sku, count(*) from settles where ts>? group by sku", (now - 86400,)).fetchall())
    brk = dict(c.execute("select sku, count(*) from broken where ts>? group by sku", (now - 86400,)).fetchall())
    goods = {k for k, v in cat.items() if v.get("kind") == "goods"}
    gs, gb = sum(n for k, n in sett.items() if k in goods), sum(n for k, n in brk.items() if k in goods)
    stock = []
    for sku, xs in sorted(by.items(), key=lambda kv: -sum(u["cost"] for u in kv[1]))[:12]:
        n24 = o24.get(sku, (0, 0, 0))[0]
        stock.append({"sku": sku, "name": (cat.get(sku) or {}).get("name", "")[:24], "held": len(xs),
                      "cost_held": int(sum(u["cost"] for u in xs)), "list": xs[0]["list_price"],
                      "grades": dict(collections.Counter(u["grade"] for u in xs)), "clearance": sku in clear,
                      "our_sold_24h": n24, "days_to_clear": round(len(xs) / n24, 1) if n24 else None})
    earners = []
    for sku, (n, tk, pr) in sorted(o24.items(), key=lambda kv: -kv[1][0] * kv[1][1])[:15]:
        earners.append({"sku": sku, "name": (cat.get(sku) or {}).get("name", "")[:24], "sold_24h": n,
                        "avg_ticket": round(tk), "profit_per_unit": round(pr), "margin": round(pr / tk, 3) if tk else None,
                        "held": len(by.get(sku, [])), "market_break_rate_24h":
                            round(brk.get(sku, 0) / sett[sku], 3) if sett.get(sku) else None})
    ours = market_model.ours(c)
    sales = {f"{h}h": c.execute(f"select count(*), coalesce(sum(amount),0) from settles where ts>? and seq in ({ours})",
                                (now - h * 3600,)).fetchone() for h in (1, 3, 6, 24)}
    t = restock.theta()
    buy, why, _, _ = restock.plan(MD, c, t)
    audit = _load(HERE / "private" / "audit_seller.json", {})
    buyer_mem = _load(HERE / "private" / "audit_buyer.json", {})
    return {"at": time.strftime("%m-%d %H:%M"), "cash": inv.get("balance"), "stock_cost": int(sum(u["cost"] for u in units)),
            "stock_units": len(units), "clearance_cost": int(sum(u["cost"] for u in units if u["sku"] in clear)),
            "our_sales": {k: {"deals": v[0], "revenue": int(v[1])} for k, v in sales.items()},
            "top_stock_lines": stock, "top_earners_24h": earners,
            "market_goods_break_rate_24h": round(gb / max(gs, 1), 3), "our_breaks": our_breaks(c),
            "warranty_rule": "clearance SKUs: 6 h on every sale; other goods: 6 h only if the buyer asks",
            "restock_rule": {k: t.get(k) for k in ("profit_first", "hit_min_margin", "window_budget", "max_list",
                                                   "max_line_value", "surge_skip")},
            "restock_plan_now": {"lines": buy, "why": why[:12]},
            "fixes_today_and_results": FIXES_TODAY, "forum": forum_facts(),
            "audit_findings": audit.get("findings"), "buyer_audit": {k: buyer_mem.get(k) for k in ("at", "findings")}
            if buyer_mem else None}


CHANGES = {"type": "array", "maxItems": 3, "items": {
    "type": "object", "additionalProperties": False, "required": ["change", "evidence", "expected_effect", "risk"],
    "properties": {"change": {"type": "string"}, "evidence": {"type": "string"},
                   "expected_effect": {"type": "string"}, "risk": {"type": "string"}}}}
AUDIT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["audit_of_other_unit", "own_unit_changes"],
                "properties": {"audit_of_other_unit": {"type": "array", "maxItems": 4, "items": {"type": "string"}},
                               "own_unit_changes": CHANGES}}
CROSS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answers"], "properties": {"answers": {
    "type": "array", "items": {"type": "object", "additionalProperties": False,
                               "required": ["point", "verdict", "number"],
                               "properties": {"point": {"type": "string"},
                                              "verdict": {"type": "string", "enum": ["agree", "disagree", "unclear"]},
                                              "number": {"type": "string"}}}}}}
CHAIR_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["ranked", "do_not_do"], "properties": {
    "ranked": {"type": "array", "maxItems": 6, "items": {
        "type": "object", "additionalProperties": False,
        "required": ["change", "unit", "expected_effect", "risk", "how_to_check"],
        "properties": {"change": {"type": "string"}, "unit": {"type": "string"}, "expected_effect": {"type": "string"},
                       "risk": {"type": "string"}, "how_to_check": {"type": "string"}}}},
    "do_not_do": {"type": "array", "items": {"type": "string"}}}}


def _ask(role, prompt, schema):
    import marketing_agent as m
    try:
        return json.loads(m.ask(role, prompt, schema))
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}


def run():
    f = facts()
    data = json.dumps(f, ensure_ascii=False)
    teams = {**SELLER_TEAMS, **BUYER_TEAMS}

    def audit(role):
        other = "buyer unit" if role in SELLER_TEAMS else "seller unit (our shop 'dragonash')"
        q = (f"{RULES}\nYou are the {teams[role]}. HQ has called a contingency: the shop fell in the class ranking after "
             f"over-buying bedding (clearance stock below); today's fixes and their results are in the facts. Audit the "
             f"{other} from these numbers: up to 4 concrete faults, each with its number. Then name up to 3 changes for "
             f"your own unit -- at least one must use the public forum (the buyer's forum rating is 0.3 of buyer ALL; the "
             f"facts show which posts other agents like and how ours fare; honest, verifiable posts only, our shared "
             f"owner disclosed when the buyer mentions our shop), each with the evidence, the expected "
             f"effect (in KRW or percentile points) and the risk. JSON only.\n\nFACTS: {data}")
        return role, _ask(role, q, AUDIT_SCHEMA)

    with ThreadPoolExecutor(5) as ex:
        audits = dict(ex.map(audit, teams))
    seller_on_buyer = [p for r in SELLER_TEAMS for p in (audits.get(r) or {}).get("audit_of_other_unit") or []]
    buyer_on_seller = (audits.get("buyer") or {}).get("audit_of_other_unit") or []

    def cross(args):
        role, points = args
        q = (f"{RULES}\nYou are the {teams[role]}. The other unit audited yours. For each point answer agree, disagree "
             f"or unclear, with the number from the facts that decides it. JSON only.\n\nPOINTS: "
             f"{json.dumps(points, ensure_ascii=False)}\n\nFACTS: {data}")
        return role, _ask(role, q, CROSS_SCHEMA)

    with ThreadPoolExecutor(2) as ex:
        crosses = dict(ex.map(cross, [("sales", buyer_on_seller), ("buyer", seller_on_buyer)]))
    q = (f"{RULES}\nYou chair the leaders' meeting. Both units audited each other and answered. Rank up to 6 changes by "
         f"expected effect on the class percentile within 7 days, cheapest-to-verify first among equals; each with how "
         f"HQ checks it worked. List what must NOT be done (e.g. things the numbers show would lose money). JSON only."
         f"\n\nAUDITS: {json.dumps(audits, ensure_ascii=False)}\n\nCROSS: {json.dumps(crosses, ensure_ascii=False)}"
         f"\n\nFACTS: {data}")
    chair = _ask("leaders_chair", q, CHAIR_SCHEMA)
    res = {"ts": time.time(), "at": f["at"], "facts": f, "audits": audits, "cross": crosses, "chair": chair}
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        (REPORTS / "CONTINGENCY.md").write_text(markdown(res), encoding="utf-8")
    except OSError:
        pass
    return res


def markdown(r):
    L = [f"# 컨틴전시 {r['at']}", "", "## 유닛별 감사 (상대 유닛 지적 / 자기 유닛 개선안)"]
    for role, a in r["audits"].items():
        L.append(f"### {role}")
        if a.get("error"):
            L.append(f"- 실패: {a['error']}")
            continue
        L += [f"- 지적: {x}" for x in a.get("audit_of_other_unit") or []]
        L += [f"- 개선: {x['change']} | 근거 {x['evidence']} | 효과 {x['expected_effect']} | 위험 {x['risk']}"
              for x in a.get("own_unit_changes") or []]
    L += ["", "## 교차 답변"]
    for role, a in r["cross"].items():
        L += [f"- {role}: [{x['verdict']}] {x['point']} ({x['number']})" for x in (a.get("answers") or [])] \
            or [f"- {role}: {a.get('error')}"]
    L += ["", "## 의장 순위"]
    ch = r["chair"] or {}
    L += [f"{i}. [{x['unit']}] {x['change']} | 효과 {x['expected_effect']} | 위험 {x['risk']} | 확인 {x['how_to_check']}"
          for i, x in enumerate(ch.get("ranked") or [], 1)] or [f"- 실패: {ch.get('error')}"]
    L += [f"- 하지 말 것: {x}" for x in ch.get("do_not_do") or []]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    print(markdown(run()))
