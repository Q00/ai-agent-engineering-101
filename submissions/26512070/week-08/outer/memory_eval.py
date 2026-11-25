"""Does memory.search find the right record? A question set scored by ability (LongMemEval's five, Wu et al. 2024).

    py -m outer.memory_eval memory_eval/questions.json

Each question: {"q": "...", "ability": "extraction|multi_slice|temporal|update|abstention",
                "filters": {...memory.search filters...}, "expect": ["pointer", ...] or [] for abstention}.
A question is right when every expected pointer is in the top `k` (default 5); an abstention question is right
when the search found nothing. The report gives right/total per ability; it goes in REPORT.md (context).
"""
import json
import sys

from tools import search  # noqa: F401  (registers memory.search)
from tools.registry import Ctx, call


def run(questions, k=5):
    ctx, by, rows = Ctx(caller="memory-eval"), {}, []
    for q in questions:
        r = call(ctx, "memory.search", dict(q.get("filters") or {}, query=q["q"], limit=k))
        res = r.get("result") or {}
        got = [x["pointer"] for x in res.get("records", [])]
        ok = (res.get("found") == 0) if not q["expect"] else all(p in got for p in q["expect"])
        a = by.setdefault(q["ability"], [0, 0])
        a[0] += ok
        a[1] += 1
        rows.append({"q": q["q"], "ability": q["ability"], "ok": ok, "got": got[:k]})
    return {"by_ability": {k: {"right": v[0], "total": v[1]} for k, v in by.items()},
            "right": sum(v[0] for v in by.values()), "total": len(questions), "questions": rows}


if __name__ == "__main__":
    qs = json.loads(open(sys.argv[1], encoding="utf-8").read())
    out = run(qs)
    print(json.dumps({k: out[k] for k in ("by_ability", "right", "total")}, ensure_ascii=False, indent=1))
    for r in out["questions"]:
        if not r["ok"]:
            print("MISS", r["ability"], r["q"], r["got"])
