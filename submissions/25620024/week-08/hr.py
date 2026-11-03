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


def measure(board):
    c = market_model.db()
    inv = _load(MD / "inventory.json", {})
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    stock = {}
    for u in units:
        stock[u["sku"]] = stock.get(u["sku"], 0) + 1
    since = time.time() - 6 * 3600
    ours = [a for (a,) in c.execute(f"select amount from settles where ts>? and seq in ({market_model.ours(c)})", (since,))]
    rank = kpi.rank(board)
    hits = market_hits(c)
    top5 = [s for s, _ in hits[:5]]
    proven = kpi.stockouts(c)
    st = kpi.sell_through(c)
    stock_cost = sum(u.get("cost") or 0 for u in units)
    h = kpi.health({"profit": rank.get("profit")})
    me_s = next((s for s in board.get("shops", []) if s.get("name") == kpi.NAME), {})
    buyers = sorted(board.get("buyers", []), key=lambda b: -(b.get("all") or 0))
    me_b = next((b for b in buyers if b.get("name") == kpi.NAME), {})
    b_rank = next((i + 1 for i, b in enumerate(buyers) if b.get("name") == kpi.NAME), None)
    lead = max((b.get("surplus") or 0 for b in buyers), default=0)
    book = _load(HERE / "private" / "enuri_book.json", {})
    acc = book.get("enuri_accountability") or {}
    last = _last()
    profit_h = None
    if last and last.get("profit") is not None and me_s.get("profit") is not None:
        hrs = max(0.1, (time.time() - last["ts"]) / 3600)
        profit_h = round((me_s["profit"] - last["profit"]) / hrs)
    return {
        "sales": [kpi_row("shop rank", rank.get("shop_rank"), 10, higher=False, near=0.5),
                  kpi_row("shop ALL", me_s.get("all"), 0.75, near=0.1),
                  kpi_row("avg sale 6h", round(statistics.mean(ours)) if ours else None, 60000, unit="원"),
                  kpi_row("VIP quotes (recent)", kpi.vip().get("vip_quotes_recent"), 10, near=0.5)],
        "scm": [kpi_row("market top-5 hits in stock", sum(1 for s in top5 if stock.get(s)), 3, near=0.34),
                kpi_row("our hits out of stock", len(proven["out_of_stock"]), 0, higher=False, near=1.0)],
        "marketing": [kpi_row("sell-through 6h", st.get("sell_through_6h"), 0.6, near=0.2),
                      kpi_row("stock older than 12h (share of cost)",
                              round(st["older_cost"] / stock_cost, 3) if stock_cost else 0, 0.2, higher=False, near=0.5)],
        "finance": [kpi_row("cash above the fatal line", h.get("cash_above_fatal"), 0, near=0),
                    kpi_row("profit per hour since last HR run", profit_h, 0, near=0),
                    kpi_row("profit", me_s.get("profit"), 1_000_000, near=0.5, unit="원")],
        "buyer": [kpi_row("buyer rank", b_rank, 10, higher=False, near=0.5),
                  kpi_row("surplus share of the leader", round((me_b.get("surplus") or 0) / lead, 3) if lead else None,
                          0.5, near=0.3)],
        "enuri": [kpi_row("money lost to missed/wrong buys (24h)", acc.get("lost"), 300000, higher=False, near=0.5,
                          unit="원"),
                  kpi_row("wrong buys (24h)", acc.get("wrong"), 30, higher=False, near=0.5)],
        "_board": {"shop_rank": rank.get("shop_rank"), "players_rank": rank.get("players_rank"),
                   "buyer_rank": b_rank, "profit": me_s.get("profit"), "revenue": me_s.get("revenue"),
                   "market_hits": hits[:5], "our_stock_of_hits": {s: stock.get(s, 0) for s in top5}},
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
                      (round(old * 1.1, 3) if old else old)
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
    """Good three runs in a row: a commendation (a raise every third one). Fire candidate: a demotion, once a day."""
    out = []
    for t, v in teams.items():
        past = [x["teams"].get(t, {}).get("status") for x in history[-2:]]
        mine = [x for x in rewards()["log"] if x["team"] == t]
        if v["status"] == "good" and len(past) == 2 and all(p == "good" for p in past):
            if not mine or time.time() - mine[-1]["ts"] > 3 * 3600:
                n = sum(x["kind"] == "commendation" for x in mine) + 1
                reward(t, "raise" if n % 3 == 0 else "commendation", f"good three runs in a row (score {v['score']})")
                out.append(f"{NAMES[t]}: {'연봉 인상' if n % 3 == 0 else '표창'}")
        if v["status"] == "fire candidate" and not any(x["kind"] == "demotion" and time.time() - x["ts"] < 24 * 3600
                                                      for x in mine):
            reward(t, "demotion", f"fire candidate (score {v['score']})")
            out.append(f"{NAMES[t]}: 강등(자율권 축소)")
    return out


def report(teams, board, history, raised=(), given=()):
    icon = {"good": "🟢", "warning": "🟡", "fire candidate": "🔴", None: "⚪"}
    lines = [f"# 인사팀 보고 {time.strftime('%m-%d %H:%M')}", "",
             f"가게 {board['shop_rank']}위 · Players {board['players_rank']}위 · 바이어 {board['buyer_rank']}위 · "
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
    lines += ["", "## 보상·챌린지"]
    lines += [f"- {NAMES[t]}: 자율권 레버 {levers(t)}개" + "".join(
        f" · {x['kind']} ({x['reason'][:60]})" for x in [y for y in rewards()["log"] if y["team"] == t][-2:])
        for t in TEAMS]
    lines += [f"- 이번 보상: {', '.join(given)}" if given else "- 이번 보상: 없음",
              f"- 목표 상향(챌린지): {'; '.join(raised)}" if raised else "- 목표 상향(챌린지): 없음"]
    lines += ["", f"시장 고가 히트 top5 (24h 판매): {board['market_hits']}", f"우리 재고: {board['our_stock_of_hits']}"]
    return "\n".join(x for x in lines if x is not None)


def run(board=None):
    if board is None:
        import httpx
        board = httpx.get(kpi.BOARD, timeout=30).json()
    history = _history()
    m = measure(board)
    teams = grade(m, history)
    raised, given = challenge(teams, history), auto_rewards(teams, history)
    md = report(teams, m["_board"], history, raised, given)
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


def grade_of(team):
    """A team's latest HR grade for its brief: score, status, KPIs, its rewards and how many levers it may change."""
    g = (_load(OUT / "grades.json", {}) or {}).get(team)
    if g is None:
        return None
    return dict(g, levers_per_run=levers(team), rewards=[x for x in rewards()["log"] if x["team"] == team][-3:])


if __name__ == "__main__":
    print(run()[1])
