"""The brief as the user reads it (HARNESS.md 5, D24 draft).

    py -m outer.brief            # the newest brief and what waits for a decision
    py -m outer.brief <id>       # one brief

A brief is written by rec.brief as JSON (logs/strategy/briefs/<id>.json, committed: evidence of the process);
this renders it as Markdown next to it and keeps logs/strategy/PENDING.md listing the briefs that wait for
an answer (py -m outer.decide <id> <option|reject>).
"""
import json
import sys

from core import atomic, paths


def briefs_dir():
    return paths.root() / "logs" / "strategy" / "briefs"


def render(doc):
    lines = [f"# Brief {doc['proposal_id']}  (strategy v{doc.get('version_from')}, {doc.get('status')})", "",
             "## 1. Score", doc.get("score", ""), "", "## 2. What happened"]
    lines += [f"- {h['claim']}  _(evidence: {', '.join(h.get('evidence') or [])})_" for h in doc.get("happened", [])]
    lines += ["", "## 3. The hypothesis in force", doc.get("hypothesis", ""), "",
              "## 4. Counterparts", doc.get("counterparts") or "(nothing new)", ""]
    if doc.get("options"):
        lines.append("## 5. Options")
        for o in doc["options"]:
            lines += [f"### {o['id']}{'  (recommended)' if o['id'] == doc.get('recommendation') else ''}",
                      f"- change: `{json.dumps(o['change'], ensure_ascii=False)}`",
                      f"- targets: {o['targets']}", f"- expected: {o['expected']}", f"- risk: {o['risk']}",
                      f"- can be undone: {'yes' if o['reversible'] else 'no'}", ""]
        lines += ["## 6. Recommendation", str(doc.get("recommendation")), "",
                  f"Answer: `py -m outer.decide {doc['proposal_id']} <option id|reject>`", ""]
    a = doc.get("audit")
    if a:
        lines += ["## Audit (another vendor's model)", f"agrees: {a['agrees']}"
                  + (f", prefers {a['prefer']}" if a.get("prefer") else "")]
        lines += [f"- {p['claim']}: {p['why']}" for p in a.get("points", [])]
    if doc.get("decision"):
        lines += ["", "## Decision", json.dumps(doc["decision"], ensure_ascii=False)]
    return "\n".join(lines) + "\n"


def write_markdown(doc):
    d = briefs_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{doc['proposal_id']}.md").write_text(render(doc), encoding="utf-8")


def refresh_pending():
    """logs/strategy/PENDING.md: one line per brief whose options wait for the user."""
    rows = []
    for f in sorted((paths.memory() / "strategy" / "proposals").glob("*.json")):
        if f.name.endswith(".audit.json"):
            continue
        p = atomic.read_json(f, default=None) or {}
        if p.get("status") == "open":
            rows.append(f"- {p['proposal_id']}: options {[o['id'] for o in p['options']]}, recommended "
                        f"{p.get('recommendation')} -> `py -m outer.decide {p['proposal_id']} <option|reject>`")
    path = paths.root() / "logs" / "strategy" / "PENDING.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Waiting for a decision\n\n" + ("\n".join(rows) if rows else "(nothing)") + "\n", encoding="utf-8")
    return rows


if __name__ == "__main__":
    d = briefs_dir()
    files = sorted(d.glob("*.md")) if d.exists() else []
    if len(sys.argv) > 1:
        files = [d / f"{sys.argv[1]}.md"]
    if not files:
        print("no brief yet")
    else:
        print(files[-1].read_text(encoding="utf-8"))
    print("\n".join(refresh_pending()) or "nothing waits for a decision")
