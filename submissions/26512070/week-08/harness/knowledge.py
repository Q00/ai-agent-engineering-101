"""Knowledge and wisdom with their grade (design 2, R0, R14): knowledge.get / knowledge.write / wisdom.get.

Every item says what it claims, about what, how certain (stated / observed / hypothesis), where it came from, by
which method over which sample, when it was made and until when it holds, and what it rests on (links). Items are
appended to memory/harness/knowledge.jsonl and never edited; the newest line of an id is the item.

The grade is set by code, never by the model that made the claim (R14):
- stated: the source is a document or code (market rules, agent.md, our code, a user decision) - named in `source`;
- observed: the source is data and the method recomputes it (sample with n > 0);
- hypothesis: everything else, including any "doing A makes B" claim without an experiment.
Wisdom (layer "wisdom") is written only through the approval path (harness.wisdom), never here.
"""
import json
import time

from harness import out_dir

GRADES = ("stated", "observed", "hypothesis")
STATED_SOURCES = ("market:", "code:", "user:")          # rules page / agent.md, our code, a user decision
EXPIRY = {"observed": 24 * 3600, "stated": None, "hypothesis": None}   # D-7: hypotheses end with their experiment
REQUIRED = ("id", "about", "claim", "source", "method")


def path():
    return out_dir() / "knowledge.jsonl"


def grade(item):
    """The grade by rule (R14): from the source and the sample, not from the claim's author."""
    src = str(item.get("source") or "")
    if item.get("causal"):                       # "doing A makes B" is a hypothesis until an experiment shows it (2-1)
        return "hypothesis"
    if src.startswith(STATED_SOURCES):
        return "stated"
    sample = item.get("sample") or {}
    if src.startswith("data:") and item.get("method") and (sample.get("n") or 0) > 0 and not item.get("causal"):
        return "observed"
    return "hypothesis"


def write(item, now=None):
    """knowledge.write: append one knowledge item. Returns it with grade, made and expires filled in."""
    missing = [k for k in REQUIRED if not item.get(k)]
    if missing:
        raise ValueError(f"knowledge item without {missing}")
    if item.get("layer") == "wisdom":
        raise ValueError("wisdom is written through harness.wisdom (the approval path), not here")
    now = now or time.time()
    g = grade(item)
    life = item.get("expires_in", EXPIRY[g])
    out = {"layer": "knowledge", **{k: v for k, v in item.items() if k != "expires_in"}, "grade": g, "made": now,
           "expires": now + life if life else None, "links": item.get("links") or []}
    with open(path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n")
    return out


def write_many(items, now=None):
    return [write(i, now) for i in items]


def _all():
    p = path()
    if not p.exists():
        return {}
    cur = {}
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                it = json.loads(line)
            except ValueError:
                continue
            cur[it["id"]] = it
    return cur


def get(about=None, prefix=None, grade_=None, now=None, include_expired=False):
    """knowledge.get: the current items about something (exact) or under a prefix, optionally of one grade."""
    now = now or time.time()
    out = []
    for it in _all().values():
        if about and it["about"] != about:
            continue
        if prefix and not str(it["about"]).startswith(prefix):
            continue
        if grade_ and it["grade"] != grade_:
            continue
        if not include_expired and it.get("expires") and it["expires"] < now:
            continue
        out.append(it)
    return sorted(out, key=lambda i: -i["made"])


def flag_review(source_prefix, why, now=None):
    """R1: when a source changes (a market rule), mark every item resting on it for review (a new line per item)."""
    now = now or time.time()
    hit = []
    for it in _all().values():
        if str(it.get("source", "")).startswith(source_prefix) or any(
                str(l).startswith(source_prefix) for l in it.get("links") or []):
            it = dict(it, review={"since": now, "why": why})
            with open(path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(it, ensure_ascii=False, separators=(",", ":")) + "\n")
            hit.append(it["id"])
    return hit
