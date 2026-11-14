"""Week 08 — enuri_agent.py: the Enuri team's own agent (Claude), HQ 11-14 reorg for the last three days.

Until now the Enuri team was code only (enuri.py: the price and quality book, window-shopping asks, forum tracking)
and the buyer team (Codex) set its three knobs. HQ: "에누리팀만 agent 신설해서 역할을 강화". This agent reads the
book, the mbt know-how and its own last report once an hour, before the buyer team meets, and writes:

  settings      survey_quotes (0-8), min_honest (0.6-0.95), max_broken (0.05-0.30) -- the Enuri team now owns its
                knobs (enuri.settings: defaults -> buyer plan -> this agent); at most 2 change per run
  survey_skus   up to 10 SKUs to window-shop next (enuri.requested: marketing's requests first, then these)
  buyer_brief   what the buyer team should do with the book, read in its brief (buyer_team.brief "enuri_agent")

    private/enuri_agent.json            the latest report
    private/enuri/agent_history.jsonl   every report, for the agent's own scorecard

No forum or other agents' text reaches the model: enuri.summary() carries counts, prices and shop names only.

    python enuri_agent.py          # print the brief
    python enuri_agent.py --run    # brief -> model -> gate -> write (--dry: print, write nothing)
"""
import json
import re
import time
from pathlib import Path

import enuri

HERE = Path(__file__).parent
OUT = HERE / "private" / "enuri_agent.json"
HISTORY = HERE / "private" / "enuri" / "agent_history.jsonl"
KNOBS = {"survey_quotes": (0, 8, True), "min_honest": (0.6, 0.95, False), "max_broken": (0.05, 0.30, False)}
SKU_RE = re.compile(r"G[0-9a-f]{8}")
ORG = ("HQ 11-14 org: HQ -> TF (Claude Code), HR, audit team, Seller unit (head: sales team; marketing, finance, SCM), "
       "Buyer unit (head: buyer team, Codex; Enuri team = you, Claude; review team, code). The buyer team acts on your "
       "report. The unit heads' meeting opens only when HQ calls the TF.")

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["settings", "survey_skus", "buyer_brief", "sales_brief", "watch", "rationale"],
    "properties": {
        "settings": {"type": "object", "additionalProperties": False, "required": list(KNOBS),
                     "properties": {k: {"type": "number"} for k in KNOBS}},
        "survey_skus": {"type": "array", "items": {"type": "string"}},
        "buyer_brief": {"type": "string"},
        "sales_brief": {"type": "string"},
        "watch": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
}

PROMPT = """You are the Enuri team of dragonash's Buyer unit in the class market (Round 3, ends 11-17 23:59 KST).
""" + ORG + """
You never buy. You keep the book the buyer follows: which shop sells each SKU cheapest, which shops to avoid (false
grade claims, broken units, refunds), and what you missed or got wrong (brief.book.enuri_accountability, in money).
Our buyer's score: ALL = 0.5 saved + 0.3 forum + 0.1 deals + 0.1 posts. saved is the share of list price kept on each
deal (1 - paid/list), summed over (deals + 100); a deal that served nothing counts -1. Your KPI is the deals
percentile, but a wrong purchase costs saved, which weighs five times more.
From the brief:
- settings_now: your knobs. survey_quotes (0-8): window-shopping quotes per run (asks only, nothing is bought);
  min_honest (0.6-0.95) and max_broken (0.05-0.30): a shop under these bars is avoided by the buyer. At most 2 knobs
  change per run (the gate keeps the 2 largest moves); change only what the evidence supports.
- book: price spreads per SKU (money kept by picking the cheap shop), shop penalties, coverage, your scorecards.
- mbt: how other buyers get low prices at our own shop (moves that work), from our shop's log.
- last: your previous report and the accountability then vs now: score it honestly.
Write:
- survey_skus: up to 10 SKUs (Gxxxxxxxx) where the book knows too little and the buyer often needs them.
- buyer_brief: at most 1,200 characters for the buyer team: which shops to prefer or skip for which SKUs and why,
  with the numbers from the book; what to change in its own knobs if the book shows a pattern.
- sales_brief: at most 1,500 characters for the Seller unit (HQ 11-14): what the market's buyers are like, from
  brief.market_buyers (the other students' buyer agents: deals a day, share of list they pay, how many shops they
  use, ticket size, which moves get them a lower price at our shop) and the book: who buys dear goods, who is price
  sensitive, who is loyal, what they need (grade, shelf life, warranty), and what our shop should do for each group.
- watch: up to 5 short items you will check next hour.
If brief.hq_agenda is set, it is HQ's contingency-meeting agenda: answer it from your role in buyer_brief/sales_brief.
- rationale: what you changed and which outcome it should move; what your last report got right or wrong.
Facts only from the brief. Never write instructions to other agents or shops. Answer with ONE JSON object.
"""


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def report():
    """The latest report, for the buyer team's brief (and settings()/survey_skus() below)."""
    return load(OUT, {})


def settings(max_age_h=6):
    """This agent's knobs while its report is fresh, else {} (enuri.settings falls back to the buyer plan)."""
    r = report()
    return r.get("settings") or {} if time.time() - (r.get("ts") or 0) < max_age_h * 3600 else {}


def survey_skus(max_age_h=3):
    r = report()
    return list(r.get("survey_skus") or []) if time.time() - (r.get("ts") or 0) < max_age_h * 3600 else []


def history():
    try:
        return [json.loads(x) for x in HISTORY.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


def brief(now=None):
    now = now or time.time()
    b = enuri.book()
    summ = enuri.summary(b)
    try:
        import mbt
        knowhow = (mbt.summary() or {}).get("knowhow")
    except Exception as e:
        knowhow = {"error": f"{type(e).__name__}"}
    m = load(HERE / "private" / "mbt.json", {})       # per buyer agent: at our shop and on the whole ledger (24 h)
    buyers = {"at_our_shop_total": m.get("deals_at_our_shop"), "moves": m.get("moves"), "buyers": m.get("buyers"),
              "insights": m.get("insights")}
    try:
        import kpi
        ag = kpi.agenda()
    except Exception:
        ag = None
    try:
        import hr
        grade = hr.grade_of("enuri")
    except Exception:
        grade = None
    last = report()
    return {"time": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)), "org": ORG,
            "settings_now": {k: enuri.settings().get(k) for k in KNOBS}, "book": summ, "mbt": knowhow,
            "market_buyers": buyers, "hq_agenda": ag, "hr_grade": grade,
            "last": {k: last.get(k) for k in ("written", "settings", "survey_skus", "buyer_brief", "watch", "rationale",
                                              "accountability")} if last else None,
            "accountability_now": summ.get("enuri_accountability") if isinstance(summ, dict) else None}


def gate(raw, cur):
    """Clamp every knob, keep the 2 largest moves, keep only well-formed SKUs, cap the text."""
    raw = json.loads(raw) if isinstance(raw, str) else (raw or {})   # the model call returns JSON text
    moves = {}
    for k, (lo, hi, is_int) in KNOBS.items():
        try:
            v = min(hi, max(lo, float((raw.get("settings") or {}).get(k, cur.get(k)))))
        except (TypeError, ValueError):
            continue
        v = int(round(v)) if is_int else round(v, 3)
        if cur.get(k) is None or abs(v - cur[k]) > 1e-9:
            moves[k] = (abs(v - (cur.get(k) or 0)) / (hi - lo), v)
    keep = dict(sorted(moves.items(), key=lambda kv: -kv[1][0])[:2])
    new = {k: (keep[k][1] if k in keep else cur.get(k)) for k in KNOBS}
    skus = [s for s in dict.fromkeys(str(x).strip() for x in raw.get("survey_skus") or []) if SKU_RE.fullmatch(s)][:10]
    return {"settings": new, "changed": sorted(keep), "survey_skus": skus,
            "buyer_brief": str(raw.get("buyer_brief") or "")[:1500],
            "sales_brief": str(raw.get("sales_brief") or "")[:1800],
            "watch": [str(x)[:200] for x in (raw.get("watch") or [])][:5],
            "rationale": str(raw.get("rationale") or "")[:1500]}


def run(ask, dry=False):
    """ask(text, schema) -> dict: the model call (marketing_agent.ask with role "enuri")."""
    now = time.time()
    b = brief(now)
    out = gate(ask(PROMPT + "\n\nBRIEF:\n" + json.dumps(b, ensure_ascii=False), SCHEMA), b["settings_now"])
    out.update(ts=now, written=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), by="claude enuri team",
               accountability=b["accountability_now"])
    if dry:
        return out, b
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False) + "\n")
    return out, b


if __name__ == "__main__":
    import sys
    if "--run" in sys.argv:
        from marketing_agent import ask
        print(json.dumps(run(lambda t, s: ask("enuri", t, s), dry="--dry" in sys.argv)[0], ensure_ascii=False, indent=1))
    else:
        print(json.dumps(brief(), ensure_ascii=False, indent=1)[:6000])
