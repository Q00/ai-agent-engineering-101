"""The rule book (rules.json): what the redesigned harness must carry, each rule with its source.

    py -m redesign rules        # check rules.json and write work/rules.md
"""
import json

from redesign import guard

FILE = guard.OWN / "rules.json"
STATUS = ("confirmed", "user", "observed", "question")
FIELDS = ("id", "area", "status", "rule", "source", "harness")


def load():
    rules = json.loads(FILE.read_text(encoding="utf-8"))["rules"]
    problems = check(rules)
    if problems:
        raise ValueError("rules.json: " + "; ".join(problems))
    return rules


def check(rules):
    problems, seen = [], set()
    for r in rules:
        rid = r.get("id", "?")
        missing = [f for f in FIELDS if f not in r]
        if missing:
            problems.append(f"{rid} lacks {missing}")
        if rid in seen:
            problems.append(f"{rid} twice")
        seen.add(rid)
        if r.get("status") not in STATUS:
            problems.append(f"{rid} status {r.get('status')!r}")
        if not str(r.get("source", "")).strip():
            problems.append(f"{rid} has no source")
    return problems


def markdown(rules):
    out = ["# Rule book", ""]
    for area in dict.fromkeys(r["area"] for r in rules):
        out += [f"## {area}", "", "| id | status | rule | source | harness must |", "|---|---|---|---|---|"]
        for r in (r for r in rules if r["area"] == area):
            cells = [r["id"], r["status"], r["rule"], r["source"], r["harness"]]
            out.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
        out.append("")
    return "\n".join(out)


def render():
    rules = load()
    return guard.write("rules.md", markdown(rules)), rules
