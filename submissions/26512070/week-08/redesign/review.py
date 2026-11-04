"""Step 3 of rule collection: the user reads the proposals and decides; only then a rule enters rules.json.

    py -m redesign proposals                    # write work/proposals.md (conflicts and flags first)
    py -m redesign approve food#1 food#2 ...    # or an area name, which approves all its open proposals
    py -m redesign reject food#3 "reason"

approve: a proposal that matches an existing rule ("same") only adds its evidence; "refines" or "contradicts"
replaces the existing rule's text and keeps the old text in history; anything else becomes a new rule.
Every decision is a line in work/review.jsonl.
"""
import json
import time

from redesign import guard, rules

PROPOSALS = guard.WORK / "proposals"
LOG = guard.WORK / "review.jsonl"


def _decided():
    if not LOG.exists():
        return {}
    out = {}
    for l in LOG.read_text(encoding="utf-8").splitlines():
        if l.strip():
            d = json.loads(l)
            out[d["pid"]] = d
    return out


def proposals():
    out = []
    for f in sorted(PROPOSALS.glob("*.json")):
        out += json.loads(f.read_text(encoding="utf-8")).get("rules", [])
    return out


def markdown():
    done = _decided()
    ps = proposals()
    open_ = [p for p in ps if p["pid"] not in done]
    lines = [f"# 규칙 제안 검토 ({time.strftime('%Y-%m-%d %H:%M')})", "",
             f"제안 {len(ps)}개, 결정 {len(ps) - len(open_)}개, 남은 것 {len(open_)}개. "
             "충돌이나 경고가 있는 제안을 영역마다 먼저 보인다.", "",
             "승인: `py -m redesign approve <pid ...|영역>` · 거절: `py -m redesign reject <pid> \"이유\"`", ""]
    for area in dict.fromkeys(p["area"] for p in open_):
        lines += [f"## {area}", ""]
        items = sorted((p for p in open_ if p["area"] == area), key=lambda p: (not (p.get("conflict") or p["flags"]),))
        for p in items:
            ex = p.get("existing")
            tag = f" · 기존 {ex['id']} ({ex['relation']})" if ex else " · 새 규칙"
            lines.append(f"### {p['pid']} [{p['status']}, {p['kind']}]{tag}")
            lines.append("")
            lines.append(p["rule"])
            lines.append("")
            if p.get("harness"):
                lines.append(f"- 하네스: {p['harness']}")
            if p.get("conflict"):
                lines.append(f"- **충돌:** {p['conflict']}")
            if p.get("history"):
                lines.append(f"- 이력: {p['history']}")
            if p["flags"]:
                lines.append(f"- **경고:** {'; '.join(p['flags'])}")
            lines.append(f"- 출처: {', '.join(p['sources'])} (후보 {len(p.get('evidence', []))}개)")
            lines.append("")
    return guard.write("proposals.md", "\n".join(lines))


def _book():
    return json.loads(guard.RULE_BOOK.read_text(encoding="utf-8"))


def _save(book):
    problems = rules.check(book["rules"])
    if problems:
        raise ValueError("; ".join(problems))
    guard.write(guard.RULE_BOOK, json.dumps(book, ensure_ascii=False, indent=1) + "\n")


def _new_id(book, area):
    n = sum(r["id"].startswith(area + "-") for r in book["rules"]) + 1
    while any(r["id"] == f"{area}-{n}" for r in book["rules"]):
        n += 1
    return f"{area}-{n}"


def approve(targets):
    ps = {p["pid"]: p for p in proposals()}
    done = _decided()
    picked = [pid for t in targets for pid in ([t] if t in ps else
              [q for q in ps if ps[q]["area"] == t and q not in done])]
    book = _book()
    byid = {r["id"]: r for r in book["rules"]}
    result = []
    for pid in picked:
        if pid not in ps:
            raise KeyError(f"no proposal {pid}")
        p = ps[pid]
        src = f"{', '.join(p['sources'])} (redesign {pid}, {len(p.get('evidence', []))} candidates)"
        ex = p.get("existing") or {}
        if ex.get("id") in byid and ex.get("relation") == "same":
            r = byid[ex["id"]]
            r["source"] += f"; {src}"
            action = f"evidence -> {r['id']}"
        elif ex.get("id") in byid:
            r = byid[ex["id"]]
            r["history"] = (r.get("history", "") + f" [{time.strftime('%m-%d')}] was: {r['rule']}").strip()
            r.update(rule=p["rule"], harness=p.get("harness", ""), status=p["status"], source=f"{r['source']}; {src}")
            action = f"replaces {r['id']}"
        else:
            r = {"id": _new_id(book, p["area"]), "area": p["area"], "status": p["status"], "rule": p["rule"],
                 "source": src, "harness": p.get("harness", "")}
            book["rules"].append(r)
            byid[r["id"]] = r
            action = f"new {r['id']}"
        if p.get("conflict"):
            r["conflict"] = p["conflict"]
        guard.append(LOG, json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "pid": pid, "decision": "approve",
                                      "action": action, "evidence": p.get("evidence", [])}, ensure_ascii=False))
        result.append((pid, action))
    _save(book)
    return result


def reject(pid, reason):
    if pid not in {p["pid"] for p in proposals()}:
        raise KeyError(f"no proposal {pid}")
    guard.append(LOG, json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "pid": pid, "decision": "reject",
                                  "reason": reason}, ensure_ascii=False))
