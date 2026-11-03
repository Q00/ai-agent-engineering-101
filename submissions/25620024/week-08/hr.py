"""Week 08 — hr.py: the HR team (owner, 2026-11-04). Code, not a model: every team gets the same rules, and the
grading costs no tokens. Run by marketing_agent.py on every team run, and by hand: python hr.py

For each team a few KPIs, each with a target. A KPI scores 100 when met, 50 when within the "near" band, 0 when
missed; the team's score is the mean. Status: good (>= 70), warning (40-69), fire candidate (< 40, or warning
three runs in a row). History is kept, so the report shows the trend, and each team's brief carries its own grade.

    private/hr/history.jsonl   one line per run: every team's KPIs and score
    private/hr/latest.md       the report to headquarters (also copied to Drive week08-reports/hr.md)
    private/hr/targets.json    the challenge: a KPI met three runs in a row gets a 10% harder target
    private/hr/rewards.json    commendations and raises. A raise is autonomy: the team may change one more lever per
                               run (levers = 2 + raises - demotions, 1-4); a fire candidate loses one (demotion)
"""
import json
import statistics
import time
from pathlib import Path

import kpi
import market_model

HERE = Path(__file__).parent
MD = HERE / ".market"
OUT = HERE / "private" / "hr"
HISTORY = OUT / "history.jsonl"
REPORTS = Path("/mnt/gdrive-imajason-kimdaehakwon/week08-reports")
TEAMS = ("sales", "scm", "marketing", "finance", "buyer", "enuri")
NAMES = {"sales": "세일즈팀", "scm": "SCM팀", "marketing": "마케팅팀", "finance": "재경팀", "buyer": "바이어팀",
         "enuri": "에누리팀"}


def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


PROBATION_H = 2            # hours after the handoff (new teams) with no demotion
TARGETS = OUT / "targets.json"
REWARDS = OUT / "rewards.json"


def kpi_row(name, value, target, higher=True, near=0.2, unit=""):
    """One KPI: met (100) / near (50, within `near` of the target) / missed (0). None value scores nothing.
    The target is the challenge level from targets.json when HR has raised it."""
    target = (_load(TARGETS, {}) or {}).get(name, target)
    if value is None:
        return {"kpi": name, "value": None, "target": target, "score": None, "unit": unit}
    if higher:
        s = 100 if value >= target else 50 if value >= target * (1 - near) else 0
    else:
        s = 100 if value <= target else 50 if value <= target * (1 + near) else 0
    return {"kpi": name, "value": value, "target": target, "higher": higher, "score": s, "unit": unit}


def market_hits(c, top=10, hours=24, min_price=80000):
    """The market's best-selling high-ticket SKUs on the ledger (all shops): [(sku, sales)]."""
    return c.execute("""select sku, count(*) from settles where ts>? and amount>=? group by sku
                        order by count(*) desc limit ?""", (time.time() - hours * 3600, min_price, top)).fetchall()


def pct(rows, key, me):
    """The board's percentile of one scored component: share of the field below us (ties count half)."""
    vals = [r.get(key) for r in rows if r.get(key) is not None]
    mine = (me or {}).get(key)
    if mine is None or not vals:
        return None
    return round((sum(v < mine for v in vals) + 0.5 * (sum(v == mine for v in vals) - 1)) / max(1, len(vals) - 1), 3)


TARGET = 0.8      # every scored component in the top 20% of the field


def measure(board):
    """KPIs are the midterm's own scoring (week-08 / agent.md): Players = shop ALL + buyer ALL; shop ALL = 0.5 profit
    + 0.3 revenue + 0.2 stars, buyer ALL = 0.5 surplus + 0.3 forum votes + 0.1 deals + 0.1 posts, each a percentile
    among shops with a deal (buyers with a deal), ranked at the end of the round. Each component has one owner team;
    the drivers (ticket size, hits in stock, sell-through, Enuri's losses) are diagnostics, not scored."""
    c = market_model.db()
    shops = [x for x in board.get("shops", []) if x.get("deals")]
    buyers = [x for x in board.get("buyers", []) if x.get("deals")]
    me_s = next((x for x in shops if x.get("name") == kpi.NAME), {})
    me_b = next((x for x in buyers if x.get("name") == kpi.NAME), {})
    players = board.get("players", [])
    me_p = next((x for x in players if (x.get("owner") or {}).get("login") == kpi.OWNER), {})
    k = lambda name, v: kpi_row(name, v, TARGET, near=0.25)
    rank = kpi.rank(board)
    # diagnostics: what drives the scored numbers
    inv = _load(MD / "inventory.json", {})
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    stock = {}
    for u in units:
        stock[u["sku"]] = stock.get(u["sku"], 0) + 1
    ours = [a for (a,) in c.execute(f"select amount from settles where ts>? and seq in ({market_model.ours(c)})",
                                    (time.time() - 6 * 3600,))]
    rev_1h = c.execute(f"select coalesce(sum(amount), 0) from settles where ts>? and seq in ({market_model.ours(c)})",
                       (time.time() - 3600,)).fetchone()[0]
    hits = market_hits(c)
    top5 = [x for x, _ in hits[:5]]
    st = kpi.sell_through(c)
    acc = (_load(HERE / "private" / "enuri_book.json", {}) or {}).get("enuri_accountability") or {}
    return {
        "sales": [k("shop ALL percentile (Players: shop half)", pct(shops, "all", me_s))],
        "finance": [k("profit percentile (shop ALL x0.5)", pct(shops, "profit", me_s))],
        "scm": [k("revenue percentile (shop ALL x0.3)", pct(shops, "revenue", me_s))],
        "marketing": [k("stars percentile (shop ALL x0.2)", pct(shops, "stars", me_s))],
        "buyer": [k("surplus percentile (buyer ALL x0.5)", pct(buyers, "surplus", me_b)),
                  k("deals percentile (buyer ALL x0.1)", pct(buyers, "deals", me_b))],
        "enuri": [k("forum votes percentile (buyer ALL x0.3)", pct(buyers, "forum", me_b)),
                  k("posts percentile (buyer ALL x0.1)", pct(buyers, "posts", me_b))],
        "_board": {"shop_rank": rank.get("shop_rank"), "players_rank": rank.get("players_rank"),
                   "players_percentile": pct(players, "total", me_p),
                   "buyer_rank": next((i + 1 for i, b in enumerate(sorted(buyers, key=lambda b: -(b.get("all") or 0)))
                                       if b.get("name") == kpi.NAME), None),
                   "profit": me_s.get("profit"), "revenue": me_s.get("revenue"),
                   "market_hits": hits[:5], "our_stock_of_hits": {x: stock.get(x, 0) for x in top5}},
        "_diag": {"avg sale 6h": round(statistics.mean(ours)) if ours else None, "revenue last 1h": rev_1h,
                  "market top-5 hits in stock": sum(1 for x in top5 if stock.get(x)),
                  "sell-through 6h": st.get("sell_through_6h"), "Enuri lost 24h": acc.get("lost"),
                  "Enuri wrong buys 24h": acc.get("wrong"), "VIP quotes": kpi.vip().get("vip_quotes_recent")},
    }


def _history():
    try:
        return [json.loads(x) for x in HISTORY.read_text(encoding="utf-8").splitlines() if x.strip()]
    except OSError:
        return []


def _last():
    h = _history()
    return h[-1] if h else None


def grade(m, history):
    out = {}
    for t in TEAMS:
        scores = [r["score"] for r in m[t] if r["score"] is not None]
        s = round(statistics.mean(scores)) if scores else None
        prev = [x["teams"][t]["status"] for x in history[-2:] if t in x.get("teams", {})]
        status = None if s is None else "good" if s >= 70 else "warning" if s >= 40 else "fire candidate"
        if status == "warning" and len(prev) == 2 and all(p in ("warning", "fire candidate") for p in prev):
            status = "fire candidate"
        out[t] = {"score": s, "status": status, "kpis": m[t]}
    return out


def challenge(teams, history):
    """A KPI met in this run and the two before gets a 10% harder target (ranks: one place better)."""
    tg, raised = _load(TARGETS, {}) or {}, []
    for t, v in teams.items():
        for r in v["kpis"]:
            if r["score"] != 100:
                continue
            past = [x.get("met", {}).get(r["kpi"]) for x in history[-2:]]
            if len(past) == 2 and all(past):
                old = r["target"]
                new = (old - 1 if "rank" in r["kpi"] else old * 0.9) if not r.get("higher", True) else \
                      (min(0.99, round(old * 1.1, 3)) if old else old)
                if new != old and not ("rank" in r["kpi"] and new < 1):
                    tg[r["kpi"]] = new
                    raised.append(f"{NAMES[t]} {r['kpi']}: {old} -> {new}")
    OUT.mkdir(parents=True, exist_ok=True)
    TARGETS.write_text(json.dumps(tg, ensure_ascii=False, indent=1), encoding="utf-8")
    return raised


def rewards():
    return _load(REWARDS, {"log": []})


def reward(team, kind, reason, by="HR"):
    """kind: commendation | raise | demotion. Raises and demotions change the team's autonomy (levers per run)."""
    r = rewards()
    r["log"].append({"ts": time.time(), "team": team, "kind": kind, "reason": reason, "by": by})
    OUT.mkdir(parents=True, exist_ok=True)
    REWARDS.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")


def levers(team):
    """How many levers the team may change per run: 2 + raises - demotions, kept within 1-4."""
    log = [x for x in rewards()["log"] if x["team"] == team]
    return max(1, min(4, 2 + sum(x["kind"] == "raise" for x in log) - sum(x["kind"] == "demotion" for x in log)))


def auto_rewards(teams, history):
    """Good three runs in a row: a commendation (a raise every third one). Fire candidate three runs in a row, after
    the probation: a demotion, at most once a day."""
    out = []
    for t, v in teams.items():
        past = [x["teams"].get(t, {}).get("status") for x in history[-2:]]
        mine = [x for x in rewards()["log"] if x["team"] == t]
        if v["status"] == "good" and len(past) == 2 and all(p == "good" for p in past):
            if not mine or time.time() - mine[-1]["ts"] > 3 * 3600:
                n = sum(x["kind"] == "commendation" for x in mine) + 1
                reward(t, "raise" if n % 3 == 0 else "commendation", f"good three runs in a row (score {v['score']})")
                out.append(f"{NAMES[t]}: {'연봉 인상' if n % 3 == 0 else '표창'}")
        hired = (HERE / "private" / "handoff.md").stat().st_mtime if (HERE / "private" / "handoff.md").exists() else 0
        if time.time() - hired < PROBATION_H * 3600:  # new teams get a probation period: graded, not demoted
            continue
        past3 = [x["teams"].get(t, {}).get("status") for x in history[-2:]] + [v["status"]]
        if len(past3) == 3 and all(p == "fire candidate" for p in past3) and not any(
                x["kind"] == "demotion" and time.time() - x["ts"] < 24 * 3600 for x in mine):
            reward(t, "demotion", f"fire candidate (score {v['score']})")
            out.append(f"{NAMES[t]}: 강등(자율권 축소)")
    return out


def report(teams, board, history, raised=(), given=(), diag=None):
    icon = {"good": "🟢", "warning": "🟡", "fire candidate": "🔴", None: "⚪"}
    lines = [f"# 인사팀 보고 {time.strftime('%m-%d %H:%M')}", "",
             f"Players {board['players_rank']}위 (백분위 {board.get('players_percentile')}, 중간평가 30%) · 가게 {board['shop_rank']}위 · 바이어 {board['buyer_rank']}위 · "
             f"이익 {board['profit']:,} · 매출 {board['revenue']:,}" if board.get("profit") is not None else "", "",
             "| 팀 | 점수 | 상태 | 추세 |", "|---|---|---|---|"]
    for t in TEAMS:
        prev = [x["teams"][t]["score"] for x in history[-3:] if x.get("teams", {}).get(t, {}).get("score") is not None]
        trend = " → ".join(str(p) for p in prev + [teams[t]["score"]]) if prev else "첫 평가"
        lines.append(f"| {NAMES[t]} | {teams[t]['score']} | {icon[teams[t]['status']]} {teams[t]['status'] or '-'} | {trend} |")
    lines += ["", "## KPI 상세"]
    for t in TEAMS:
        lines.append(f"- **{NAMES[t]}**: " + "; ".join(
            f"{r['kpi']} {r['value'] if r['value'] is not None else '-'} (목표 {'≥' if r.get('higher', True) else '≤'}"
            f"{r['target']}) {'✅' if r['score'] == 100 else '⚠️' if r['score'] == 50 else '❌' if r['score'] == 0 else '·'}"
            for r in teams[t]["kpis"]))
    lines += ["", "## 사유서 (KPI 미달 팀이 마지막 회의에 쓴 것)"]
    for t, why in explanations().items():
        if teams.get(t, {}).get("status") in ("warning", "fire candidate"):
            lines.append(f"- **{NAMES[t]}**: {why or '(아직 사유서 없음: 다음 회의부터 작성)'}")
    lines += ["", "## 보상·챌린지"]
    lines += [f"- {NAMES[t]}: 자율권 레버 {levers(t)}개" + "".join(
        f" · {x['kind']} ({x['reason'][:60]})" for x in [y for y in rewards()["log"] if y["team"] == t][-2:])
        for t in TEAMS]
    lines += [f"- 이번 보상: {', '.join(given)}" if given else "- 이번 보상: 없음",
              f"- 목표 상향(챌린지): {'; '.join(raised)}" if raised else "- 목표 상향(챌린지): 없음"]
    lines += ["", f"진단 지표 (점수 아님): {diag}", f"시장 고가 히트 top5 (24h 판매): {board['market_hits']}",
              f"우리 재고: {board['our_stock_of_hits']}"]
    return "\n".join(x for x in lines if x is not None)


def run(board=None):
    if board is None:
        import httpx
        board = httpx.get(kpi.BOARD, timeout=30).json()
    history = _history()
    m = measure(board)
    teams = grade(m, history)
    raised, given = challenge(teams, history), auto_rewards(teams, history)
    md = report(teams, m["_board"], history, raised, given, m.get("_diag"))
    OUT.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), "profit": m["_board"].get("profit"),
                            "teams": {t: {"score": v["score"], "status": v["status"]} for t, v in teams.items()},
                            "met": {r["kpi"]: r["score"] == 100 for v in teams.values() for r in v["kpis"]}},
                           ensure_ascii=False) + "\n")
    (OUT / "latest.md").write_text(md, encoding="utf-8")
    (OUT / "grades.json").write_text(json.dumps(teams, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        (REPORTS / "hr.md").write_text(md, encoding="utf-8")
    except OSError:
        pass
    return teams, md


def explanations():
    """What each team last wrote after 'HR:' in its rationale (the plan files the teams write)."""
    src = {"sales": "marketing_plan.json", "scm": "scm_plan.json", "finance": "finance_limits.json",
           "buyer": "buyer_plan.json", "marketing": "marketing_plan.json", "enuri": "buyer_plan.json"}
    out = {}
    for t, f in src.items():
        r = str((_load(HERE / "private" / f, {}) or {}).get("rationale") or "")
        i = r.find("HR:")
        out[t] = r[i:i + 500] if i >= 0 else None
    return out


def grade_of(team):
    """A team's latest HR grade for its brief: score, status, KPIs, its rewards and how many levers it may change."""
    g = (_load(OUT / "grades.json", {}) or {}).get(team)
    if g is None:
        return None
    return dict(g, levers_per_run=levers(team), rewards=[x for x in rewards()["log"] if x["team"] == team][-3:])


if __name__ == "__main__":
    print(run()[1])
