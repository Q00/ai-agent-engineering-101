"""Lessons: what went wrong, turned into short rules the shop manager and the buyer read before they act.
restock.py starts it every LESSONS_EVERY.

    python lessons.py

Code first collects the facts, each with an id, from our own records only: checkouts the kit refused to pay,
goods that broke without warranty, food thrown away past its date, write-tool refusals of the shop manager,
what the seller's price bandit learned, stock that does not sell. The fast model then writes at most five
rules, each citing the fact ids it rests on; a rule that cites no fact, or an id that is not in the list, is
dropped in code (the RA rule: a step counts only if its premises exist). Kept rules go to .market/lessons.json
(the newest LESSONS_KEPT, read by planner.py through the `lessons` tool and by market_buyer's shop choice) and
are appended to logs/lessons.log so the report can show where each rule came from.
"""
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
MD = HERE / ".market"
LESSONS_KEPT = 10
MIN_FACTS = 3
DAY = 86400
SYSTEM = (
    "You review a shop-and-buyer agent pair in an agent market. From the numbered facts only, write at most five "
    "short rules that would have avoided the losses or kept the wins. Each rule is for the shop manager (stock "
    "and prices) or the buyer (which shop to pick, what to accept), and cites the fact ids it rests on. No rule "
    "without a fact behind it; no rule about anything the facts do not show. Plain, specific, at most 200 "
    "characters each."
)
SCHEMA = {"type": "object", "properties": {"lessons": {"type": "array", "items": {
    "type": "object", "properties": {"rule": {"type": "string"}, "for": {"type": "string", "enum": ["manager", "buyer"]},
                                     "evidence": {"type": "array", "items": {"type": "string"}}},
    "required": ["rule", "for", "evidence"]}}}, "required": ["lessons"]}


def load(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def facts():
    """[(id, fact)] from our own records of the last day."""
    out, now = [], time.time()
    add = lambda prefix, text: out.append((f"{prefix}{sum(1 for i, _ in out if i[0] == prefix) + 1}", text))
    refusals = [r for r in load(MD / "memory_cursor.json", {}).get("refusals", []) if now - r.get("ts", 0) < DAY]
    for (shop, reason), n in Counter((r["shop"], r["reason"]) for r in refusals).most_common(5):
        add("R", f"my kit refused to pay {shop} {n} times: {reason}")
    broke = Counter(p["shop"] for p in load(MD / "purchases.json", []) if p.get("broke_at") and not p.get("covered"))
    for shop, n in broke.most_common(5):
        add("B", f"{n} goods bought from {shop} broke without warranty")
    units = load(MD / "inventory.json", {}).get("units", [])
    expired = Counter(u.get("category") for u in units if u.get("grade") == "expired")
    for cat, n in expired.most_common(5):
        add("X", f"{n} units of {cat} expired in our stock (thrown away at cost)")
    try:
        lines = (HERE / "logs" / "planner-tools.log").read_text(encoding="utf-8").splitlines()[-300:]
    except OSError:
        lines = []
    why = Counter()
    for ln in lines:
        try:                       # "refused" is logged whole; "result" is cut at 1000 characters
            row = json.loads(ln)
            refused = row.get("refused") or (json.loads(row["result"]) or {}).get("refused") or []
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        for x in refused:
            why[x.get("why", "")[:80]] += 1
    for w, n in why.most_common(5):
        add("T", f"the manager's write tools refused {n} lines: {w}")
    for cat, arms in load(MD / "price_bandit.json", {}).get("cats", {}).items():
        talks = sum(sum(v) for v in arms.values())
        if talks < 10:
            continue
        best = max(arms, key=lambda a: arms[a][0] / max(1, sum(arms[a])))
        add("P", f"{cat}: multiplier {best} closes most often ({arms[best][0]} of {sum(arms[best])} talks), "
                 f"{talks} talks in all")
    sold = Counter(s["category"] for s in load(MD / "restock_state.json", {}).get("sold_log", []) if now - s["ts"] < DAY)
    stale = Counter(u.get("category") for u in units if u.get("grade") != "expired" and now - u.get("supplied_at", now) > DAY)
    for cat, n in stale.most_common(5):
        if not sold.get(cat):
            add("S", f"{n} units of {cat} held over a day with no sale in the last day")
    return out[:30]


def main():
    os.chdir(HERE)
    (HERE / "logs").mkdir(exist_ok=True)
    out = open(HERE / "logs" / "lessons.log", "a", encoding="utf-8")
    log = lambda m: (out.write(f"{time.strftime('%m-%d %H:%M:%S')} {m}\n"), out.flush())
    fs = facts()
    if len(fs) < MIN_FACTS:
        log(f"[lessons] only {len(fs)} facts, nothing to learn yet")
        return 0
    import harness
    ids = {i for i, _ in fs}
    try:
        ans = harness.ask(SYSTEM, "Facts:\n" + "\n".join(f"{i}: {t}" for i, t in fs), SCHEMA, None, 120, 1,
                          model=harness.CHEAP, role="lessons")
    except Exception as e:
        log(f"[lessons] failed: {type(e).__name__}: {str(e)[:200]}")
        return 0
    kept, dropped = [], []
    for x in ans["lessons"][:5]:
        ok = x.get("evidence") and set(x["evidence"]) <= ids
        (kept if ok else dropped).append(dict(x, ts=int(time.time()),
                                              facts=[t for i, t in fs if i in set(x.get("evidence") or [])]))
    old = [x for x in load(MD / "lessons.json", []) if x["rule"] not in {k["rule"] for k in kept}]
    tmp = MD / "lessons.tmp"
    tmp.write_text(json.dumps((old + kept)[-LESSONS_KEPT:], ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(MD / "lessons.json")
    log(f"[lessons] {len(fs)} facts: " + "; ".join(f"{i} {t}" for i, t in fs))
    for x in kept:
        log(f"[lessons] kept ({x['for']}, {','.join(x['evidence'])}): {x['rule']}")
    for x in dropped:
        log(f"[lessons] dropped, evidence {x.get('evidence')} not in the facts: {x['rule']}")
    return 0


def rules(who):
    """The kept rules for 'manager' or 'buyer', newest first, as short lines for a prompt."""
    return [x["rule"] for x in reversed(load(MD / "lessons.json", [])) if x.get("for") == who]


if __name__ == "__main__":
    sys.exit(main())
