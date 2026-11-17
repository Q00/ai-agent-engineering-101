"""One screen of what the agent knows right now, for the person who runs it.

    python status.py            (from the agent folder; reads .market/, changes nothing, calls no model)

Why: from 11-15 21:48 to 11-17 19:10 the model's login was expired and the answer was in files nobody opened.
The alerts, the rule in force, the memory sizes and today's numbers are one command away.
"""
import json
import time
from pathlib import Path

import objective
import playbook
import profiles
import rulebook
import strategy_params

STATE = Path(".market")


def _json(name, default=None):
    try:
        return json.loads((STATE / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def today(rows, formula, day=None):
    """Today's orders, deals, fill and mean score from the hot order log."""
    day = day or time.strftime("%Y-%m-%d")
    rows = [r for r in rows if str(r.get("at", "")).startswith(day)]
    orders = {r.get("order") for r in rows if r.get("order")}
    won = {r.get("order") for r in rows if r.get("order") and r.get("outcome") == "deal"}
    scores = [s for r in rows if (s := objective.outcome_score(r, formula)) is not None]
    return {"orders": len(orders), "deals": len(won), "fill": round(len(won) / len(orders), 2) if orders else None,
            "score": round(sum(scores) / len(scores), 3) if scores else None}


def lines():
    book, prof = rulebook.load(STATE), profiles.load(STATE)
    out = [f"rule in force      {book['formula']}"
           + (f"   (the rules page changed on {book['changed_at']})" if book.get("changed_at") else "")
           + ("   UNKNOWN FORMULA ON THE PAGE: the last known one is in use" if book.get("named_formula") == "unknown" else "")]
    checked = book.get("checked_at")
    out.append("rules page checked " + (time.strftime("%m-%d %H:%M", time.localtime(checked)) if checked else "never"))
    out.append(f"my running score   {prof.get('saved')}   over {prof.get('deals_scored')} remembered deals")
    out.append(f"order log          {prof.get('hot_rows')} rows of the last {prof.get('hot_hours')} h, "
               f"{prof.get('archived_rows')} archived, {len(prof.get('shops') or {})} shop profiles")
    out.append(f"today              {today(_json('buyer_memory.json', []), book['formula'])}")
    params = strategy_params.load()
    out.append("knobs              " + ", ".join(f"{k}={v}" for k, v in params.items()))
    history = playbook.read_jsonl(STATE / "tune_history.jsonl")
    if history:
        last = history[-1]
        out.append(f"last change        {last.get('at')} by {last.get('model')}: {last.get('changes') or 'none'}"
                   + ("" if last.get("answered", True) else "   THE MODEL DID NOT ANSWER"))
    lessons = playbook.load(STATE)
    alerts = [e for e in lessons if e.get("source") == "alert"]
    out.append(f"lessons            {len(lessons)} recorded, {len(alerts)} of them alerts")
    for e in alerts[-3:]:
        out.append(f"  ALERT {e.get('at')}: {e.get('lesson')} ({str(e.get('evidence', ''))[:110]})")
    return out


if __name__ == "__main__":
    print("\n".join(lines()))
