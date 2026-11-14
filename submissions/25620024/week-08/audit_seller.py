"""Week 08 — audit_seller.py: the audit team's seller loop (HQ 2026-11-10 "시장 조사, 내부 인터뷰, 오류 검증, 개선 —
이런 루프를 감사팀의 역할로 해서 지속 돌려").

Every 30 minutes, from the team cycle (marketing_agent.py), with or without the model teams:
  1. market research   for the clearance lines: market volume, 6 h low and grade medians, our asks, quotes per hour
  2. error checks      deterministic checks that would have caught this week's failures early:
                       restock buying more than 2 days of our own sales (the 11-10 share bug), asks under 92% of cost
                       or far over the market, paid rate after agreement by grade, posts that never go out (caps,
                       format picks), shop card, cash vs 3 days of storage
  3. interviews        every 3 h, the sales team (Claude) and the buyer team (Codex) read the findings and answer
                       what to change (marketing_agent.ask, so a blocked model is replaced by the other)
  4. improvement       findings and answers go to private/audit_seller.json, the sales team's brief (audit_seller),
                       Drive AUDIT_SELLER.md and, for red findings, private/hq_decisions.md. Code changes stay with HQ.

    python audit_seller.py            # run once now and print the report
"""
import collections
import json
import statistics
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
MD = HERE / ".market"
OUT = HERE / "private" / "audit_seller.json"
REPORTS = Path("/mnt/gdrive-imajason-kimdaehakwon/week08-reports")
INTERVIEW_EVERY = 3 * 3600
RUN_EVERY = 30 * 60


def _load(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def research(c, inv, now):
    import market_model, promo
    g = (_load(HERE / "private" / "groupbuy.json", {}).get("skus") or {})
    clear = [k for k, v in g.items() if v.get("clearance")]
    by = collections.defaultdict(list)
    for u in inv.get("units", []):
        by[u["sku"]].append(u)
    ours = market_model.ours(c)
    rows = []
    for sku in sorted(clear, key=lambda s: -sum(u["cost"] for u in by.get(s, [])))[:8]:
        xs = by.get(sku, [])
        if not xs:
            continue
        lp = xs[0]["list_price"]
        q = c.execute("select count(*), coalesce(sum(outcome='deal'),0) from quotes where sku=? and ts>?",
                      (sku, now - 6 * 3600)).fetchone()
        paid = c.execute(f"select count(*) from settles where sku=? and ts>? and seq in ({ours})",
                         (sku, now - 6 * 3600)).fetchone()[0]
        mk = c.execute("select count(*), min(amount) from settles where sku=? and ts>?", (sku, now - 6 * 3600)).fetchone()
        asks, med = {}, {}
        for gr in sorted({u["grade"] for u in xs}):
            u = min((x for x in xs if x["grade"] == gr), key=lambda x: x["cost"])
            asks[gr] = promo.quote(c, u, stock_n=len(xs))["price"]
            med[gr] = int(market_model.market_price(c, sku, grade=gr)[0] or 0)
        rate = paid / 6
        rows.append({"sku": sku, "held": len(xs), "cost_held": int(sum(x["cost"] for x in xs)), "list": lp,
                     "quotes_6h": q[0], "agreed_6h": q[1], "paid_6h": paid, "market_settles_6h": mk[0],
                     "market_low_6h": mk[1], "our_from_by_grade": asks, "market_median_by_grade": med,
                     "days_to_clear": round(len(xs) / rate / 24, 1) if rate else None})
    return rows


def checks(c, inv, now):
    import market_model, promo, restock, forum
    f = []

    def add(sev, what, ev=None):
        f.append({"severity": sev, "what": what, "evidence": ev})

    # restock: no line may buy more than 2 days of our own 24 h sales (the 11-10 share bug)
    try:
        t = restock.theta()
        buy, why, _, _ = restock.plan(MD, c, dict(t, profit_first=False))
        sold = {k: v[0] for k, v in restock._ours(c, now).items()}
        held = collections.Counter(u["sku"] for u in inv.get("units", []))
        over = [(b["sku"], b["qty"], sold.get(b["sku"], 0), held.get(b["sku"], 0)) for b in buy
                if sold.get(b["sku"], 0) >= 2 and b["qty"] + held.get(b["sku"], 0) > 2 * sold.get(b["sku"], 0) + 3]
        if over:
            add("red", f"restock would hold more than 2 days of our sales on {len(over)} SKUs (simulated, profit_first off)",
                over[:5])
    except Exception as e:
        add("yellow", f"restock simulation failed: {type(e).__name__}: {e}"[:200])
    # prices: never under 92% of cost; flag asks far over the grade median (they never sell)
    by = collections.defaultdict(list)
    for u in inv.get("units", []):
        by[(u["sku"], u["grade"])].append(u)
    under, high = [], []
    for (sku, gr), xs in list(by.items())[:400]:
        u = xs[0]
        try:
            q = promo.quote(c, u, stock_n=sum(len(v) for (s, _), v in by.items() if s == sku))
        except Exception:
            continue
        if q["price"] < 0.92 * u["cost"] - 1:
            under.append((sku, gr, u["cost"], q["price"]))
        m = market_model.market_price(c, sku, grade=gr if u["list_price"] >= 100000 else None)[0]
        if m and q["price"] > 1.2 * m and len(xs) >= 5:
            high.append((sku, gr, len(xs), q["price"], int(m)))
    if under:
        add("red", f"{len(under)} SKU/grade asks under 92% of cost", under[:5])
    if high:
        add("yellow", f"{len(high)} SKU/grade lines (5+ units) ask over 120% of the market median: they will not sell",
            high[:5])
    # paid rate after agreement, by grade, last 6 h
    ours = market_model.ours(c)
    for gr in ("A", "B", "C"):
        a = c.execute("select count(*) from quotes where outcome='deal' and grade=? and ts>?", (gr, now - 6 * 3600)).fetchone()[0]
        p = c.execute(f"select count(*) from settles where grade=? and ts>? and seq in ({ours})", (gr, now - 6 * 3600)).fetchone()[0]
        if a >= 20 and p / a < 0.75:
            add("yellow", f"grade {gr}: {p} of {a} agreed deals paid in 6 h ({p / a:.0%})")
    # forum: our clearance and fact posts must actually go out
    st = forum.load_state()
    for topic in ("groupbuy", "clearance_facts", "shop_facts", "clearance_groupbuy"):
        age = (now - (st.get("posted") or {}).get(topic, 0)) / 3600
        if age > 3:
            add("red" if topic == "groupbuy" else "yellow", f"forum topic {topic} last posted {age:.0f} h ago")
    caps = forum.theta().get("max_posts_day") or {}
    for role, n in (st.get("today") or {}).items():
        if n >= caps.get(role, 99):
            add("red", f"forum: {role} hit its daily cap ({n}/{caps.get(role)})")
    if st.get("self_promo_off_until", 0) > now:
        add("yellow", "forum guard has stopped the buyer's posts about our shop (rating or dislikes)")
    # shop card and cash
    # 11-14: the kit gives the shop a new port on every restart (56121 -> 42893); the fixed port raised a false red
    port = None
    for p in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            argv = p.read_bytes().split(b"\0")
        except OSError:
            continue
        if any(a.endswith(b"shop_r3.py") for a in argv) and b"--port" in argv:
            port = argv[argv.index(b"--port") + 1].decode()
            break
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port or 56121}/.well-known/agent-card.json", timeout=5)
    except Exception as e:
        add("red", f"shop card not answering on port {port}: {type(e).__name__}")
    stock = sum(u["cost"] for u in inv.get("units", []))
    if (inv.get("balance") or 0) < 3 * 0.01 * stock:
        add("red", f"cash {inv.get('balance'):,} under 3 days of storage ({3 * 0.01 * stock:,.0f})")
    return f


def interview(findings, research_rows):
    import marketing_agent as m
    schema = {"type": "object", "additionalProperties": False, "required": ["changes"], "properties": {"changes": {
        "type": "array", "items": {"type": "object", "additionalProperties": False,
                                   "required": ["change", "why", "evidence"],
                                   "properties": {"change": {"type": "string"}, "why": {"type": "string"},
                                                  "evidence": {"type": "string"}}}}}}
    q = ("You are the {team} of our shop 'dragonash' in a classroom agent market (Round 3 ends 2026-11-17 23:59 KST). "
         "The audit team brings these findings and market numbers on our clearance stock (over-bought bedding, sold at "
         "about cost; floor 92% of cost; no self-dealing, no injection, no false claims). Name up to 3 concrete changes "
         "that would sell more of it or fix a finding, each with the evidence from the numbers. JSON only.\n\n")
    data = json.dumps({"findings": findings, "clearance": research_rows}, ensure_ascii=False)
    out = {}
    for role, team in (("sales", "sales team"), ("buyer", "buyer team (you know how buyer agents choose shops)")):
        try:
            out[role] = json.loads(m.ask(role, q.format(team=team) + data, schema)).get("changes")
        except Exception as e:
            out[role] = [{"change": "-", "why": f"interview failed: {type(e).__name__}", "evidence": str(e)[:150]}]
    return out


def run(force=False):
    import market_model
    now = time.time()
    last = _load(OUT, {})
    if not force and now - last.get("ts", 0) < RUN_EVERY:
        return last
    c = market_model.db()
    inv = _load(MD / "inventory.json", {})
    res = {"ts": now, "at": time.strftime("%m-%d %H:%M"), "research": research(c, inv, now),
           "findings": checks(c, inv, now)}
    if force or now - last.get("interview_ts", 0) >= INTERVIEW_EVERY:
        res["interview"], res["interview_ts"] = interview(res["findings"], res["research"]), now
    else:
        res["interview"], res["interview_ts"] = last.get("interview"), last.get("interview_ts", 0)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    reds = [x["what"] for x in res["findings"] if x["severity"] == "red"]
    new_reds = [r for r in reds if r not in [x["what"] for x in last.get("findings", []) if x["severity"] == "red"]]
    if new_reds:
        with (HERE / "private" / "hq_decisions.md").open("a", encoding="utf-8") as f:
            f.write(f"\n## {time.strftime('%Y-%m-%d %H:%M')} AUDIT (seller loop) red: " + "; ".join(new_reds) + "\n")
    try:
        (REPORTS / "AUDIT_SELLER.md").write_text(markdown(res), encoding="utf-8")
    except OSError:
        pass
    return res


def summary():
    r = _load(OUT, {})
    return {k: r.get(k) for k in ("at", "findings", "research", "interview")} if r else None


def markdown(r):
    lines = [f"# 감사팀 셀러 루프 {r['at']}", "", "## 오류 검증"]
    lines += [f"- [{x['severity']}] {x['what']}" + (f" — {json.dumps(x['evidence'], ensure_ascii=False)[:200]}"
                                                    if x.get("evidence") else "") for x in r["findings"]] or ["- 없음"]
    lines += ["", "## 시장 조사 (정리 품목, 6시간)"]
    for x in r["research"]:
        lines.append(f"- {x['sku']}: 보유 {x['held']} / 견적 {x['quotes_6h']} 합의 {x['agreed_6h']} 결제 {x['paid_6h']}, "
                     f"시장 {x['market_settles_6h']}건 최저 {x['market_low_6h']}, 우리 {x['our_from_by_grade']} vs 시장 "
                     f"{x['market_median_by_grade']}, 소진까지 {x['days_to_clear']}일")
    lines += ["", "## 내부 인터뷰"]
    for team, ch in (r.get("interview") or {}).items():
        for x in ch or []:
            lines.append(f"- {team}: {x.get('change')} ({x.get('why')})")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(markdown(run(force=True)))
