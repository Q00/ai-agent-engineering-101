"""Recompute observational comparison data from the frozen study; no API calls."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
PEER = ROOT / "extensions/peer_dag"
BATCH = PEER / "conditions/20260922T012131-no-token-limit-9703d2"


def calculate():
    metrics = [r for source in (BATCH / "summary.json", BATCH / "recovery_429/summary.json")
               for r in json.loads(source.read_text())["runs"]]
    final = [r for r in metrics if r["status"] == "succeeded"]
    assert len(final) == len({(r["block"], r["condition"], r["case_id"]) for r in final}) == 45
    rows = []
    for r in final:
        folder = PEER / "runs" / r["run_id"] / "artifacts"
        paths = sorted(folder.rglob("*.json"))
        documents = [json.loads(p.read_text()) for p in paths]
        assert all(d["status"] == "succeeded" for d in documents)
        root = json.loads((folder / (r["case_id"] + ".json")).read_text())["artifact"]
        rows.append({"run": r["run_id"], "case": r["case_id"], "condition": r["condition"],
                     "mode": r["root_plan"]["mode"], "documents": len(paths),
                     "root_summary_chars": len(root["summary"]),
                     "all_summary_chars": sum(len(d["artifact"]["summary"]) for d in documents),
                     "calls": r["calls"], "depth": r["max_depth"], "facts_passed": r["passed"],
                     "peak_execution_calls": r["trace"]["peak_execution_calls"],
                     "artifact_sha256": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                         for p in paths}})
    case_groups = []
    for case in sorted({r["case"] for r in rows}):
        for mode in ("execute", "delegate"):
            subset = [r for r in rows if r["case"] == case and r["mode"] == mode]
            if subset:
                case_groups.append({"case": case, "mode": mode, "n": len(subset),
                                    "facts_passed": sum(r["facts_passed"] for r in subset),
                                    **{k: round(mean(r[k] for r in subset), 2) for k in
                                       ("documents", "root_summary_chars", "all_summary_chars", "calls")}})
    with (ROOT / "results-allocation-reference.csv").open(newline="") as f:
        old = [r for r in csv.DictReader(f)
               if json.loads(r["note"]).get("experiment_id") == "870e29e83d8e340a"]
    with (ROOT / "results.csv").open(newline="") as f:
        new = [r for r in csv.DictReader(f) if r["tasks"]]
    assert len(old) == 9 and len(new) == 45
    allocation = {}
    for condition in ("baseline", "homogeneous", "overconfident"):
        allocation[condition] = {}
        for name, source in (("basic", old), ("peer_final", new)):
            subset = [r for r in source if r["condition"] == condition]
            allocation[condition][name] = {key: sum(int(r[key]) for r in subset)
                                           for key in ("tasks", "correct", "misawards", "messages")}
    return {"scope": "Observational, post-hoc comparison; final slots include 429 recovery. Not a randomized single-agent ablation.",
            "character_measure": "Python len(summary), including spaces; excludes facts/evidence and JSON serialization. Repeated content is not deduplicated.",
            "allocation": allocation,
            "depth_counts": dict(sorted(Counter(r["depth"] for r in rows).items())),
            "execution_peak_counts": dict(sorted(Counter(r["peak_execution_calls"] for r in rows).items())),
            "all_phase_peak_counts": dict(Counter(r["trace"]["peak_calls"] for r in final)),
            "mode_counts": dict(Counter(r["mode"] for r in rows)),
            "documents": sum(r["documents"] for r in rows), "root_documents": 45,
            "subtask_documents": sum(r["documents"] - 1 for r in rows),
            "max_tasks": max(r["tasks"] for r in metrics), "max_calls": max(r["calls"] for r in metrics),
            "case_groups": case_groups, "runs": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = calculate()
    path = Path(__file__).with_name("comparison.json")
    content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        assert path.read_text() == content, "comparison data differs from source artifacts"
    else:
        path.write_text(content)
    print(json.dumps({k: v for k, v in result.items() if k != "runs"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
