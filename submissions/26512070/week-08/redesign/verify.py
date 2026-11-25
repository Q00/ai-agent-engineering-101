"""Step 2b of rule collection: the reviewer model (another vendor than the designer) checks each proposal.

    py -m redesign verify [--area food]

Per area the reviewer gets the proposals, the candidates they cite (with quotes, places and dates) and the
current state read from the live harness as data (active strategy, shop card, position, last commits). Each
proposal gets a verdict: ok, fix (with a corrected rule), stale (newer facts replace it), wrong (evidence does
not support it), overmerged (distinct rules folded into one; split given) or drop (no rule for the harness).
Output: work/verdicts/<area>.json.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor

from redesign import guard, llm, merge, review

VERDICTS = ("ok", "fix", "stale", "wrong", "overmerged", "drop")

SYSTEM = """You review proposed rules for the rule book of a trading-agent harness (one shop and one buyer in an
open agent market, Round 3, 11/3 - 11/17 23:59; final score = Players percentile of shop ALL + buyer ALL).
Another model wrote the proposals from candidate rules. Check each proposal against its evidence and the
current state. Be strict; a wrong rule in the book misleads every later design decision.

Verdicts:
- ok: supported by its evidence, current, self-contained, one rule.
- fix: right idea but a number, date, weight, name or condition is wrong or missing; give the corrected rule.
- stale: the current state or newer evidence replaces it; give the current rule if there is one.
- wrong: the evidence does not support it.
- overmerged: separate rules folded into one, so a reader loses something; give the split rules.
- drop: nothing the harness must know (setup steps, one-off talk about this redesign session).
Also check status: "confirmed" needs agent.md or a board check; a user decision is "user"; data is "observed".

Answer with JSON only: {"verdicts": [{"pid": .., "verdict": one of the verdicts, "reason": short, "status": the
correct status, "rules": [corrected or split rule texts, empty for ok/drop/wrong]}]}"""


def current_state():
    commits = guard.git_log()[-15:]
    active = json.loads(guard.read("memory/strategy/active.json"))
    return "\n\n".join([
        "agent.json (what the kit runs):\n" + guard.read("agent.json"),
        f"memory/strategy/active.json (strategy v{active.get('version')}, live):\n"
        + json.dumps(active, ensure_ascii=False)[:9000],
        "logs/strategy/position.md (live ranks):\n" + guard.read("logs/strategy/position.md"),
        "last 15 commits (oldest first):\n" + "\n".join(f"{h} {d[:16]} {m[:400]}" for h, d, m in commits),
    ])


def verify_area(area, props, cands, state, model="reviewer"):
    cited = {e for p in props for e in p.get("evidence", [])}
    ev = [json.dumps({k: c.get(k) for k in ("cid", "rule", "quote", "loc", "date", "quote_ok")}, ensure_ascii=False)
          for c in cands if c["cid"] in cited]
    ps = [json.dumps({k: p.get(k) for k in ("pid", "kind", "status", "rule", "harness", "evidence", "conflict",
                                            "history", "existing")}, ensure_ascii=False) for p in props]
    prompt = (f"Current state (live harness files, read as data):\n<<<\n{state}\n>>>\n\n"
              f"Area: {area}\nProposals ({len(ps)}):\n" + "\n".join(ps) + "\n\nCited candidates:\n" + "\n".join(ev))
    out, err = None, None
    for _ in range(2):
        try:
            ans = llm.ask(prompt, system=SYSTEM, model=model, tag=f"verify {area}")
            out = json.loads(re.search(r"\{.*\}", ans, re.S).group(0))
            break
        except (AttributeError, ValueError, llm.LLMError) as e:
            err = str(e)
    if out is None:
        return {"area": area, "error": err}
    pids = {p["pid"] for p in props}
    got = {v["pid"] for v in out["verdicts"]}
    for v in out["verdicts"]:
        if v.get("verdict") not in VERDICTS:
            v["verdict"] = "fix" if v.get("rules") else "wrong"
            v["reason"] = "(verdict name corrected) " + v.get("reason", "")
    out.update(area=area, missing=sorted(pids - got), unknown=sorted(got - pids),
               model=llm.config()["roles"].get(model, model))
    guard.write(guard.WORK / "verdicts" / f"{area}.json", json.dumps(out, ensure_ascii=False, indent=1))
    return out


def run(only_area=None, workers=4):
    cands = merge.load_candidates()
    props = review.proposals()
    state = current_state()
    areas = sorted({p["area"] for p in props} if not only_area else {only_area})
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(lambda a: verify_area(a, [p for p in props if p["area"] == a],
                                                 [c for c in cands if c["area"] == a], state), areas))


def verdicts():
    out = {}
    for f in (guard.WORK / "verdicts").glob("*.json"):
        for v in json.loads(f.read_text(encoding="utf-8")).get("verdicts", []):
            out[v["pid"]] = v
    return out
