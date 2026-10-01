"""Read-only trace analysis for the current-code report; never calls a model."""
import argparse
from collections import Counter
import json
from pathlib import Path

BATCH = Path(__file__).resolve().parent
PEER = BATCH.parent.parent
WEEK = PEER.parent.parent
CONDITIONS = ("baseline", "homogeneous", "overconfident")


def collect(folder, gold):
    summary = json.loads((folder / "summary.json").read_text())
    runs = []
    for metric in summary["runs"]:
        trace = PEER / "logs" / (metric["run_id"] + ".jsonl")
        rows = [(line, json.loads(text)) for line, text in
                enumerate(trace.read_text().splitlines(), 1)]
        awards = [(line, row) for line, row in rows if row["event"] == "award"]
        roots = [(line, row) for line, row in awards if row["task_id"] == metric["case_id"]]
        assert len(roots) <= 1
        root = roots[0][1] if roots else None
        assert (root["worker"] if root else None) == metric["root_award"]
        transfers = []
        for line, award in awards:
            if award["task_id"] == metric["case_id"]:
                continue
            calls = [(n, row) for n, row in rows
                     if row.get("task_id") == award["task_id"]
                     and row.get("worker") == award["worker"]
                     and row.get("phase") in ("execute", "synthesize")
                     and row["event"] in ("call_start", "call_end")]
            ends = [(n, row) for n, row in rows if row["event"] == "task_end"
                    and row["task_id"] == award["task_id"]]
            assert len(ends) <= 1
            completed = bool(ends and ends[0][1]["outcome"]["status"] == "succeeded")
            if completed:
                assert {row["event"] for _, row in calls} == {"call_start", "call_end"}
                assert ends[0][1]["outcome"]["worker"] == award["worker"]
            transfers.append({
                "task_id": award["task_id"], "requester": award["requester"],
                "worker": award["worker"], "mode": award["plan"]["mode"],
                "different_worker": award["requester"] != award["worker"],
                "award_line": line, "completed": completed,
                "task_end_line": ends[0][0] if ends else None,
                "execution_call_lines": [n for n, _ in calls],
            })
        runs.append({
            "run_id": metric["run_id"], "condition": metric["condition"],
            "case_id": metric["case_id"], "block": metric["block"],
            "trace": str(trace.relative_to(WEEK)), "gold": gold[metric["case_id"]],
            "root_worker": root["worker"] if root else None,
            "root_award_line": roots[0][0] if roots else None,
            "root_mode": root["plan"]["mode"] if root else None,
            "root_gold_match": root["worker"] == gold[metric["case_id"]] if root else None,
            "status": metric["status"], "facts_passed": metric["passed"],
            "subtask_assignments": transfers,
        })
    groups = {}
    for condition in CONDITIONS:
        counters = Counter(dict.fromkeys([
            "runs", "root_gold_matches", "root_gold_mismatches", "root_unawarded",
            "delegate_roots", "cross_worker_assignment_runs", "cross_worker_completed_runs",
            "facts_passed", "mismatch_but_facts_passed",
            "mismatch_with_completed_cross_worker_and_facts_passed",
        ], 0))
        for run in runs:
            if run["condition"] != condition:
                continue
            different = [t for t in run["subtask_assignments"] if t["different_worker"]]
            completed = any(t["completed"] for t in different)
            counters["runs"] += 1
            counters["root_gold_matches"] += run["root_gold_match"] is True
            counters["root_gold_mismatches"] += run["root_gold_match"] is False
            counters["root_unawarded"] += run["root_gold_match"] is None
            counters["delegate_roots"] += run["root_mode"] == "delegate"
            counters["cross_worker_assignment_runs"] += bool(different)
            counters["cross_worker_completed_runs"] += completed
            counters["facts_passed"] += run["facts_passed"]
            counters["mismatch_but_facts_passed"] += run["root_gold_match"] is False and run["facts_passed"]
            counters["mismatch_with_completed_cross_worker_and_facts_passed"] += (
                run["root_gold_match"] is False and completed and run["facts_passed"])
        assert (counters["root_gold_matches"] + counters["root_gold_mismatches"]
                + counters["root_unawarded"]) == counters["runs"]
        groups[condition] = dict(counters)
    return {"conditions": groups, "runs": runs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Compare saved evidence without writing")
    args = parser.parse_args()
    gold = {task["id"]: task["gold"] for task in json.loads((WEEK / "tasks.json").read_text())}
    result = {
        "scope": "Current peer DAG only. Primary 45 and selected recovery 16 remain separate.",
        "definitions": {
            "root_gold_match": "Initial assignee equals predeclared responsibility ID; not output quality.",
            "cross_worker_completed_runs": "At least one non-root task awarded to a worker different from its requester, with execute/synthesize call start/end and succeeded task_end.",
            "facts_passed": "Existing expected.json checks; not proof of complete document quality.",
            "causality": "Observational records do not establish that delegation caused a pass.",
        },
        "primary": collect(BATCH, gold),
        "recovery": collect(BATCH / "recovery_429", gold),
    }
    assert len(result["primary"]["runs"]) == 45
    assert len(result["recovery"]["runs"]) == 16
    target = BATCH / "CURRENT_CODE_EVIDENCE.json"
    if args.check:
        assert json.loads(target.read_text()) == result
    else:
        with target.open("x") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    print(json.dumps({"passed": True, "check_only": args.check,
                      **{name: result[name]["conditions"] for name in ("primary", "recovery")}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
