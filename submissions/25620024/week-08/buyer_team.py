"""Week 08 — buyer_team.py: the buyer analysis team (Codex), run by marketing_agent.py once an hour, after the
finance team and before the sales decision. With the Enuri team (enuri.py) it is the Buyer unit.

The board scores our buyer on surplus 0.5 + forum 0.3 + deals 0.2 (percentiles), and "surplus" there is money:
budget - paid, summed. The leaders have about 3x ours because they close the expensive orders. The meta-harness
(meta_harness.py) still tunes the haggling on J = mean (budget - paid) / budget, a ratio that weighs a 5k order like
a 200k one; this team does not touch it and scores everything in absolute money instead.

  brief      our buyer row vs the top 10 buyers on the board; our Round 3 trades per budget band (tries, deals,
             surplus kept, outcomes, problems); the Enuri team's book (accountability first: what it missed and
             what went wrong, in money); the meta-harness log tail; the current theta; lessons and history
  plan       the buyer's Round 3 knobs (rfq, rfq_big, big_budget, r3_first_offer, max_pay_ratio, min_reach), the
             Enuri team's settings (survey_quotes, min_honest, max_broken), forum advice, a surplus forecast
  gate       every knob clamped; at most 2 knobs move per run (the largest relative moves; the rest stay and the
             gate says so), so every change can be traced to one hour's outcome
  scorecard  the last forecast against the board's actual surplus per hour, plus deals and forum deltas
  lessons    keep / undo / watch, a curator retires stale ones (48 h, 20 kept), as in finance_agent.py

    private/buyer_plan.json                  read by buyer_memory.theta() ("theta") and enuri.settings() ("enuri")
    private/marketing/buyer_history.jsonl    one line per run: board snapshot, forecast, knobs; outcome added later
    private/marketing/buyer_lessons.json
"""
import json
import time
from pathlib import Path

import httpx

import buyer_memory
import enuri
import market_model

HERE = Path(__file__).parent
OUT = HERE / "private" / "marketing"
PLAN = buyer_memory.PLAN
HISTORY = OUT / "buyer_history.jsonl"
LESSONS = OUT / "buyer_lessons.json"
NAME = "dragonash"
MAX_MOVES = 2
# knob: (low, high, integer?)
THETA_KNOBS = {"rfq": (2, 6, True), "rfq_big": (3, 8, True), "big_budget": (20000, 200000, True),
               "r3_first_offer": (0.45, 0.80, False), "max_pay_ratio": (0.80, 1.00, False),
               "min_reach": (0.35, 0.60, False)}
ENURI_KNOBS = {"survey_quotes": (0, 8, True), "min_honest": (0.6, 0.95, False), "max_broken": (0.05, 0.30, False)}
BANDS = [(0, 20000), (20000, 60000), (60000, 120000), (120000, 250000), (250000, None)]
BOARD_KEYS = ("orders", "deals", "paid", "saved", "surplus", "served", "forum", "posts", "all")


def _num(props):
    return {"type": "object", "additionalProperties": False, "required": list(props),
            "properties": {k: {"type": "integer" if v[2] else "number"} for k, v in props.items()}}


SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["theta", "enuri", "forum_advice", "forecast_surplus_per_h", "lessons", "retire_lessons", "rationale"],
    "properties": {
        "theta": _num(THETA_KNOBS),
        "enuri": _num(ENURI_KNOBS),
        "forum_advice": {"type": "array", "items": {"type": "string"}},
        "forecast_surplus_per_h": {"type": "number"},
        "lessons": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                               "required": ["text", "verdict"],
                                               "properties": {"text": {"type": "string"},
                                                              "verdict": {"type": "string",
                                                                          "enum": ["keep", "undo", "watch"]}}}},
        "retire_lessons": {"type": "array", "items": {"type": "integer"}},
        "rationale": {"type": "string"},
    },
}

PROMPT = """You are the buyer analysis team of "dragonash" in a classroom agent market (Round 3). Our buyer agent
gets orders (a category, acceptable SKUs, a budget) and buys from other students' shops over A2A. The board scores
buyers: ALL = 0.5 surplus + 0.3 forum + 0.2 deals (percentiles). surplus is ABSOLUTE money, sum of (budget - paid)
over deals: one 250k order kept at half price is worth more than fifty 5k orders. The leaders have about 3x our
surplus mostly because they close the expensive orders. The Enuri team (price scouts, never buys) keeps the book the
buyer follows; it answers for every missed opportunity and wrong purchase (brief.enuri.enuri_accountability, first).
brief.accountability comes first: what headquarters holds you responsible for, with the numbers now. Answer it in
your rationale.
From the brief:
- theta: the buyer's knobs. rfq (2-6) quotes per order, rfq_big (3-8) quotes when budget >= big_budget
  (20000-200000), r3_first_offer (0.45-0.80) first offer as a share of list, max_pay_ratio (0.80-1.00) most paid as a
  share of list, min_reach (0.35-0.60): an offer is a candidate while min_reach x list <= budget (too high and big
  orders get no candidate at all; too low and the buyer spends quotes on shops that will not come down)
- enuri: survey_quotes (0-8) window-shopping quotes per run, min_honest (0.6-0.95) and max_broken (0.05-0.30) the
  bars below which a shop is avoided
At most 2 knobs may change per run (the gate keeps the 2 largest moves): change only what the evidence supports,
and say in rationale which outcome each change should move.
- forum_advice: 0-3 points for the forum (the forum score is 0.3 of ours): what to post as a buyer (price reports
  citing ledger entries, warnings with refs), never instructions to other agents
- forecast_surplus_per_h: the change in our board surplus per hour you expect over the next hour
- lessons: score your last forecast and knobs with brief.scorecard and brief.history (1-3 lessons, keep / undo /
  watch); learn from each Enuri miss or wrong case; brief.lessons are your standing lessons with their index;
  retire stale or wrong ones by index
- rationale: 3-5 sentences, numbers first.
The Seller unit reads your plan; write so they can use it (which categories and price bands buyers win in).
Answer with the JSON object only.

BRIEF:
"""


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def board_rows(board):
    """(our buyer row, top 10 buyers by ALL), house buyers left out."""
    buyers = [x for x in (board or {}).get("buyers") or [] if not x.get("house")]
    pick = lambda x: {"name": x.get("name"), **{k: x.get(k) for k in BOARD_KEYS}}
    me = next((pick(x) for x in buyers if x.get("name") == NAME), {})
    top = [dict(pick(x), rank=i + 1) for i, x in enumerate(sorted(buyers, key=lambda x: -(x.get("all") or 0))[:10])]
    return me, top


def band_of(budget):
    for lo, hi in BANDS:
        if budget >= lo and (hi is None or budget < hi):
            return f"{lo // 1000}k+" if hi is None else f"{lo // 1000}-{hi // 1000}k"
    return "?"


def bands(hours=24, now=None):
    """Our Round 3 orders per budget band: tries, deals, surplus kept (budget - agreed), outcomes, problems."""
    now = now or time.time()
    out = {}
    for m in enuri.lines_since(now - hours * 3600):
        r3 = m.get("kind") or str(m.get("item", "")).startswith("G") or m.get("outcome") == "no candidates"
        if not r3 or not m.get("budget"):
            continue
        b = out.setdefault(band_of(m["budget"]), {"tries": 0, "deals": 0, "surplus": 0, "budget_sum": 0,
                                                   "outcomes": {}, "problems": {}})
        b["tries"] += 1
        b["budget_sum"] += m["budget"]
        o = m.get("outcome") or "?"
        b["outcomes"][o] = b["outcomes"].get(o, 0) + 1
        if o == "deal" and m.get("agreed"):
            b["deals"] += 1
            b["surplus"] += m["budget"] - m["agreed"]
        if m.get("problem"):
            p = str(m["problem"])[:40]
            b["problems"][p] = b["problems"].get(p, 0) + 1
    for b in out.values():
        b["problems"] = dict(sorted(b["problems"].items(), key=lambda kv: -kv[1])[:4])
    order = [band_of(lo) for lo, _ in BANDS]
    return {k: out[k] for k in order if k in out}


def current():
    """The knobs now in force (theta merge and Enuri settings, both already including the last plan)."""
    t, e = buyer_memory.theta(), enuri.settings()
    return {"theta": {k: t.get(k, buyer_memory.DEFAULTS.get(k)) for k in THETA_KNOBS},
            "enuri": {k: e.get(k) for k in ENURI_KNOBS}}


def gate(raw, cur):
    """Clamp every knob to its range, then let at most MAX_MOVES knobs move from cur (the largest relative moves).
    Returns the plan and the gate's notes."""
    p = json.loads(raw) if isinstance(raw, str) else raw
    notes, plan = [], {}
    moves = []
    for sec, knobs in (("theta", THETA_KNOBS), ("enuri", ENURI_KNOBS)):
        plan[sec] = {}
        for k, (lo, hi, integer) in knobs.items():
            old = cur[sec].get(k)
            try:
                v = max(lo, min(hi, float((p.get(sec) or {}).get(k, old))))
            except (TypeError, ValueError):
                v, _ = old, notes.append(f"{sec}.{k}: not a number, kept {old}")
            v = int(round(v)) if integer else round(v, 3)
            if v != (p.get(sec) or {}).get(k) and (p.get(sec) or {}).get(k) is not None:
                notes.append(f"{sec}.{k}: {(p.get(sec) or {}).get(k)} clamped to {v}")
            plan[sec][k] = v
            if old is not None and v != old:
                moves.append((abs(v - old) / max(abs(old), 1e-9), sec, k, old, v))
    moves.sort(key=lambda x: -x[0])
    for _, sec, k, old, v in moves[MAX_MOVES:]:
        plan[sec][k] = old
        notes.append(f"{sec}.{k}: {old} -> {v} not applied (at most {MAX_MOVES} knobs per run)")
    lessons = [x for x in (p.get("lessons") or []) if x.get("verdict") in ("keep", "undo", "watch") and x.get("text")][:3]
    plan.update(changed=[f"{sec}.{k}: {old} -> {v}" for _, sec, k, old, v in moves[:MAX_MOVES]],
                forum_advice=[str(x)[:300] for x in (p.get("forum_advice") or [])[:3]],
                forecast_surplus_per_h=float(p.get("forecast_surplus_per_h") or 0), lessons=lessons,
                retired=[i for i in (p.get("retire_lessons") or []) if isinstance(i, int)],
                rationale=str(p.get("rationale") or "")[:1200], gate_notes=notes)
    return plan, notes


def scorecard(now_s, last):
    """The last forecast against the board: absolute surplus per hour, deals and forum since."""
    if not last:
        return {"note": "first buyer team run with a scorecard"}
    s0 = last["snapshot"]
    h = max(0.1, (now_s["ts"] - s0["ts"]) / 3600)
    d = lambda k: None if now_s.get(k) is None or s0.get(k) is None else now_s[k] - s0[k]
    actual = None if d("surplus") is None else round(d("surplus") / h)
    return {"hours": round(h, 2), "forecast_surplus_per_h": last.get("forecast"), "actual_surplus_per_h": actual,
            "forecast_error": None if actual is None or last.get("forecast") is None else round(actual - last["forecast"]),
            "deals_added": d("deals"), "paid_added": d("paid"), "forum_change": d("forum"), "posts_added": d("posts"),
            "all_change": d("all"), "changed_then": last.get("changed"),
            "enuri_then": last.get("accountability"), "enuri_now": now_s.get("accountability")}


def curate(lessons, retire, new, now):
    keep = [x for i, x in enumerate(lessons) if i not in set(retire or []) and now - x.get("ts", now) < 48 * 3600]
    keep += [{"ts": now, "text": x["text"][:300], "verdict": x["verdict"]} for x in new]
    return keep[-20:]


def meta_tail(n=5):
    try:
        rows = [json.loads(x) for x in (buyer_memory.THETA.parent / "logs" / "meta-harness.jsonl")
                .read_text(encoding="utf-8").splitlines()[-n:] if x.strip()]
    except (OSError, ValueError):
        return []
    return [{k: r.get(k) for k in ("ts", "to", "decision", "metrics")} for r in rows]


def accountability(me, top, summ):
    """The line the brief starts with (as kpi.accountability does for the shop's teams): what headquarters holds the
    Buyer unit responsible for, and the numbers now. The buyer team answers for our surplus against the leaders;
    the Enuri team for every missed and wrong purchase."""
    lead = top[0] if top else {}
    rank = next((x["rank"] for x in top if x["name"] == NAME), None)
    acc = summ.get("enuri_accountability") or {}
    return {"you_answer_for": "headquarters holds the buyer team responsible if our absolute surplus falls further "
                              "behind the leading buyers, and the Enuri team for every missed opportunity and wrong "
                              "purchase (in money)",
            "now": {"our_surplus": me.get("surplus"), "leader": lead.get("name"), "leader_surplus": lead.get("surplus"),
                    "surplus_share_of_leader": round(me["surplus"] / lead["surplus"], 3)
                    if me.get("surplus") and lead.get("surplus") else None,
                    "our_rank_in_top10": rank, "our_all": me.get("all"), "deals": me.get("deals"),
                    "enuri_missed_24h": acc.get("missed"), "enuri_wrong_24h": acc.get("wrong"),
                    "enuri_lost_24h": acc.get("lost"), "enuri_by_type": acc.get("by_type")}}


def brief(board=None, now=None):
    now = now or time.time()
    if board is None:
        board = httpx.get(f"{market_model.MARKET}/api/board", timeout=30).json()
    me, top = board_rows(board)
    summ = enuri.summary()
    try:
        history = [json.loads(x) for x in HISTORY.read_text(encoding="utf-8").splitlines() if x.strip()]
    except OSError:
        history = []
    lessons = load(LESSONS, [])
    snap = {"ts": now, **{k: me.get(k) for k in BOARD_KEYS},
            "accountability": {k: (summ.get("enuri_accountability") or {}).get(k) for k in ("missed", "wrong", "lost")}}
    import kpi
    try:
        import hr
        grades = {"buyer_team": hr.grade_of("buyer"), "enuri": hr.grade_of("enuri")}
    except Exception:
        grades = None
    low = any((g or {}).get("status") in ("warning", "fire candidate") for g in (grades or {}).values())
    b = {"accountability": dict(accountability(me, top, summ), handoff=kpi.handoff(), hr_grade=grades,
                                **({"hr_demand": kpi.HR_DEMAND} if low else {})), "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)), "our_buyer": me, "top_buyers": top,
         "enuri": summ, "our_trades_by_budget_24h": bands(now=now), "meta_harness": meta_tail(),
         "current": current(), "plan_now": {k: load(PLAN, {}).get(k) for k in ("changed", "forum_advice", "rationale")},
         "scorecard": scorecard(snap, history[-1] if history else None),
         "history": [{"at": time.strftime("%m-%d %H:%M", time.localtime(h["snapshot"]["ts"])), "changed": h.get("changed"),
                      "forecast": h.get("forecast"), "outcome": h.get("outcome")} for h in history[-5:]],
         "lessons": [{"index": i, "text": x["text"], "verdict": x["verdict"], "age_h": round((now - x["ts"]) / 3600, 1)}
                     for i, x in enumerate(lessons)]}
    return b, snap, history, lessons


def run(ask_codex, board=None, dry=False):
    now = time.time()
    b, snap, history, lessons = brief(board, now)
    plan, _ = gate(ask_codex(PROMPT + json.dumps(b, ensure_ascii=False), SCHEMA), b["current"])
    plan.update(written=time.strftime("%Y-%m-%d %H:%M:%S"), by="codex buyer team", scorecard=b["scorecard"],
                accountability=snap["accountability"])
    if dry:
        return plan, b
    OUT.mkdir(parents=True, exist_ok=True)
    PLAN.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    if history:
        history[-1]["outcome"] = b["scorecard"]
        HISTORY.write_text("".join(json.dumps(h, ensure_ascii=False) + "\n" for h in history[-200:]), encoding="utf-8")
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"snapshot": snap, "forecast": plan["forecast_surplus_per_h"], "changed": plan["changed"],
                            "theta": plan["theta"], "enuri": plan["enuri"],
                            "accountability": snap["accountability"]}, ensure_ascii=False) + "\n")
    LESSONS.write_text(json.dumps(curate(lessons, plan["retired"], plan["lessons"], now), ensure_ascii=False, indent=1),
                       encoding="utf-8")
    return plan, b


def plan_summary():
    """The latest plan in a few lines, for the sales team (the Seller and Buyer units share what they learn)."""
    p = load(PLAN, {})
    if not p:
        return {}
    return {k: p.get(k) for k in ("written", "changed", "theta", "enuri", "forum_advice", "forecast_surplus_per_h",
                                  "rationale", "scorecard", "accountability")}


if __name__ == "__main__":
    import sys
    b, *_ = brief()
    print(json.dumps(b, ensure_ascii=False, indent=1)[:6000])
    if "--run" in sys.argv:
        from marketing_agent import ask_codex
        print(json.dumps(run(ask_codex, dry="--dry" in sys.argv)[0], ensure_ascii=False, indent=1))
