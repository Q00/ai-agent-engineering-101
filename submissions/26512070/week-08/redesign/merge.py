"""Step 2 of rule collection: the designer model merges the candidates of each area into proposed rules.

    py -m redesign merge [--area food]

Per area: duplicates become one rule with all their candidate ids as evidence; sources that disagree are
written as a conflict (with dates, so the newer one can win); every candidate is either evidence or dropped
with a reason, and the check below lists any the model left out. Output: work/proposals/<area>.json.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor

from redesign import extract, guard, llm, rules

SYSTEM = """You turn candidate rules into the rule book of a trading-agent harness that is being redesigned.
The harness runs one shop (seller) and one buyer in an open agent market, Round 3 (11/3 - 11/17 23:59); the final
score is the Players percentile (shop ALL + buyer ALL). Seller and buyer run independently.

You get the existing rule book and every candidate of one area. Produce the rules of that area:
- Merge candidates that say the same thing into one rule; list all their cids in "evidence".
- If sources disagree (a number, a weight, a rule that changed), write ONE rule with the newest/authoritative
  value and describe the disagreement in "conflict" with each side's source and date. The market's agent.md is
  the authority on market rules; for user decisions the newest date wins.
- status: "confirmed" only if agent.md states it or a check against the market board is cited; "user" for what the
  user decided; "observed" for what data showed (keep numbers, window, date); "question" for open points.
- "existing": if the rule matches an existing rule-book id give {"id": .., "relation": "same"|"refines"|"contradicts"}, else null.
- Drop a candidate (with a short reason) only if it is setup instructions for joining, pure implementation detail
  with no behaviour, or wrong. Superseded rules are not dropped: they go into "conflict" or "history".
- "history": short notes of how the rule changed over time (version, date), or "".
- rule and harness are self-contained English: numbers, dates, no "this".

Answer with JSON only:
{"rules": [{"kind": .., "status": .., "rule": .., "harness": .., "evidence": [cid, ..], "conflict": "", "history": "",
"existing": null}], "dropped": [{"cid": .., "reason": ..}]}"""


def _commit_dates():
    return [(h, d, m) for h, d, m in guard.git_log()]


def _date(c, commits):
    if c["source"] != "git log":
        return ""
    q = extract._norm(c.get("quote", ""))
    for h, d, m in commits:
        if q and q in extract._norm(m):
            return f"{d[:16]} {h}"
    return ""


def load_candidates():
    cands = [json.loads(l) for l in (guard.WORK / "candidates.jsonl").read_text(encoding="utf-8").splitlines() if l]
    commits = _commit_dates()
    for c in cands:
        c["date"] = _date(c, commits)
    return cands


def merge_area(area, cands, book, model="designer"):
    path = guard.WORK / "proposals" / f"{area}.json"
    lines = [json.dumps({k: c.get(k) for k in ("cid", "kind", "rule", "quote", "harness", "loc", "date", "quote_ok")},
                        ensure_ascii=False) for c in cands]
    prompt = (f"Existing rule book:\n{json.dumps(book, ensure_ascii=False)}\n\n"
              f"Area: {area}\nCandidates ({len(cands)}), one JSON per line:\n" + "\n".join(lines))
    out, err = None, None
    for _ in range(2):
        try:
            ans = llm.ask(prompt, system=SYSTEM, model=model, tag=f"merge {area}")
            out = json.loads(re.search(r"\{.*\}", ans, re.S).group(0))
            break
        except (AttributeError, ValueError, llm.LLMError) as e:
            err = str(e)
    if out is None:
        return {"area": area, "error": err}
    ids = {c["cid"] for c in cands}
    used = {e for r in out["rules"] for e in r.get("evidence", [])} | {d["cid"] for d in out.get("dropped", [])}
    for i, r in enumerate(out["rules"]):
        r["pid"] = f"{area}#{i + 1}"
        r["area"] = area
        srcs = sorted({c["source"] for c in cands if c["cid"] in r.get("evidence", [])})
        r["sources"] = srcs
        r["flags"] = []
        if r.get("status") == "confirmed" and "agent.md" not in srcs:
            r["flags"].append("confirmed without agent.md evidence")
        if any(not c["quote_ok"] for c in cands if c["cid"] in r.get("evidence", [])) and len(r.get("evidence", [])) == 1:
            r["flags"].append("only evidence has a quote not found in its source")
    out.update(area=area, candidates=len(cands), missing=sorted(ids - used), unknown=sorted(used - ids),
               model=llm.config()["roles"].get(model, model))
    guard.write(path, json.dumps(out, ensure_ascii=False, indent=1))
    return out


def run(only_area=None, workers=4):
    cands = load_candidates()
    book = [{k: r[k] for k in ("id", "area", "status", "rule")} for r in rules.load()]
    areas = sorted({c["area"] for c in cands} if not only_area else {only_area})
    by = {a: [c for c in cands if c["area"] == a] for a in areas}
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(lambda a: merge_area(a, by[a], book), areas))
