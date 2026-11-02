"""Insights: what the integrated analysis has learned from the numbers, kept from run to run.

An insight has three short parts: what was found ("grade B bedding sells at about 60% of list"),
the numbers behind it, and how later decisions should use it ("open grade B bedding at 65%, not 70%"). The integrated analysis keeps them every 12 hours
in both phases (an insight changes no rule): it adds at most NEW_MAX new ones and marks the ones
the new numbers confirm or refute. The code holds the rest:

    a new insight needs a known role, a finding and a use of at most TEXT_MAX characters each, and
    evidence with a number in it; a repeat of an active insight is refused
    testing -> confirmed after CONFIRM confirmations, when they outnumber the refutations
    retired when the refutations outnumber the confirmations by RETIRE, and beyond ACTIVE_MAX
    active insights the weakest is retired; retired insights stay in the file with the reason

Every briefing shows the active ones in part 2 (context.py). Out: analysis/insights.json.
"""
import re

import common

NEW_MAX = 2
TEXT_MAX = 200
CONFIRM = 2
RETIRE = 2
ACTIVE_MAX = 20
ROLES = ("seller", "buyer", "both")
TOPICS = ("price", "stock", "shops", "demand", "quality", "other")


def path():
    return common.MARKET_DIR / "analysis" / "insights.json"


def load() -> list:
    return common.read_json(path(), []) or []


def active(rows: list) -> list:
    return [x for x in rows if x.get("status") in ("testing", "confirmed")]


def score(x: dict) -> int:
    return x.get("confirmed", 0) - x.get("refuted", 0)


def _norm(text) -> str:
    return re.sub(r"\W+", " ", str(text)).strip().lower()


def _retire(x: dict, why: str, now: float, log: dict) -> None:
    x.update(status="retired", retired_why=why, retired_at=int(now))
    log["retired"].append(x["id"])


def _refuse(new, seen: set, added: int) -> str | None:
    if not isinstance(new, dict):
        return "not an object"
    if added >= NEW_MAX:
        return f"only {NEW_MAX} new insights per decision"
    if new.get("role") not in ROLES:
        return "role must be seller, buyer or both"
    text = str(new.get("text") or "").strip()
    if not text or len(text) > TEXT_MAX:
        return f"text must be 1 to {TEXT_MAX} characters"
    if not re.search(r"\d", str(new.get("evidence") or "")):
        return "evidence has no number"
    if not 1 <= len(str(new.get("use") or "").strip()) <= TEXT_MAX:
        return f"use must be 1 to {TEXT_MAX} characters"
    if _norm(text) in seen:
        return "repeats an active insight"
    return None


def update(got: dict, now: float) -> dict:
    """Apply the integrated analysis's {"confirm": [id], "refute": [id], "new": [...]}; returns what was done."""
    rows = load()
    log = {"added": [], "confirmed": [], "refuted": [], "retired": [], "refused": []}
    by_id = {x["id"]: x for x in active(rows)}
    yes, no = set(got.get("confirm") or []), set(got.get("refute") or [])
    for i in sorted(yes | no, key=str):
        x = by_id.get(i)
        if x is None or (i in yes and i in no):                 # unknown, retired, or marked both ways
            log["refused"].append({"id": i, "why": "no active insight with this id" if x is None else "both confirmed and refuted"})
            continue
        key = "confirmed" if i in yes else "refuted"
        x[key] = x.get(key, 0) + 1
        x["checked"] = int(now)
        log[key].append(i)
        if -score(x) >= RETIRE:
            _retire(x, f"refuted {x['refuted']} times, confirmed {x.get('confirmed', 0)}", now, log)
        elif x.get("confirmed", 0) >= CONFIRM and score(x) > 0:
            x["status"] = "confirmed"
    n = max((int(x["id"][1:]) for x in rows if re.fullmatch(r"I\d+", str(x.get("id")))), default=0)
    seen = {_norm(x.get("text")) for x in active(rows)}
    for new in got.get("new") or []:
        why = _refuse(new, seen, len(log["added"]))
        if why:
            log["refused"].append({"text": str((new or {}).get("text") if isinstance(new, dict) else new)[:80], "why": why})
            continue
        n += 1
        text = str(new["text"]).strip()
        rows.append({"id": f"I{n}", "role": new["role"], "topic": new.get("topic") if new.get("topic") in TOPICS else "other",
                     "text": text, "evidence": str(new["evidence"])[:TEXT_MAX], "use": str(new["use"]).strip(), "created": int(now), "checked": int(now),
                     "confirmed": 0, "refuted": 0, "status": "testing"})
        seen.add(_norm(text))
        log["added"].append(f"I{n}")
    live = active(rows)
    for x in sorted(live, key=lambda x: (score(x), x.get("checked", 0)))[:max(0, len(live) - ACTIVE_MAX)]:
        _retire(x, f"more than {ACTIVE_MAX} active insights; this was the weakest", now, log)
    common.write_json(path(), rows)
    return log
