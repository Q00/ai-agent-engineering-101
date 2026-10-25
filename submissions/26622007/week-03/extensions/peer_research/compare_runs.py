"""Recompute memory ablation evidence from completed, immutable run artifacts."""
import argparse
import json
from pathlib import Path

from common import ROOT
from audit import audit as trace_audit


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def compare(seed_dir, warm_dir, disabled_dir):
    dirs = {"seed": seed_dir, "warm": warm_dir, "disabled": disabled_dir}
    results = {name: read(path / "result.json") for name,path in dirs.items()}
    settings = {name: read(path / "settings.json") for name,path in dirs.items()}
    # Recorded absolute paths describe the original host; resolve evidence from this checkout for replay.
    logs = {name: ROOT / "logs" / (r["run_id"] + ".jsonl") for name,r in results.items()}
    events = {name: [json.loads(line) for line in path.read_text().splitlines()] for name,path in logs.items()}
    traces = {name: trace_audit(path) for name,path in logs.items()}
    before = read(warm_dir / "memory-before.json")
    owners = {row["id"]: row["owner"] for rows in before.values() for row in rows}
    seed_ids = {row["id"] for row in read(seed_dir / "memory-written.json")}
    transfers = sorted({(owners[ident], row["worker"], ident)
                        for row in events["warm"] if row["event"] == "memory_delivery"
                        for ident in row["handoff_ids"] if ident in owners and owners[ident] != row["worker"]})
    expected = read(ROOT / "expected.json")
    private_values = [expected["internal_decision_id"], expected["backup_owner"]]
    no_memory_requests = [json.dumps(row["payload"], ensure_ascii=False) for row in events["disabled"] if row["event"] == "http_request"]
    warm, disabled = results["warm"], results["disabled"]
    checks = {
        "fresh_processes": len({r["process_id"] for r in results.values()}) == 3,
        "same_followup_case": settings["warm"]["case_sha"] == settings["disabled"]["case_sha"],
        "same_followup_config": settings["warm"]["config"] == settings["disabled"]["config"],
        "same_followup_code": settings["warm"]["sources"] == settings["disabled"]["sources"],
        "same_requester": settings["warm"]["requester"] == settings["disabled"]["requester"],
        "seed_memory_persisted": bool(seed_ids) and seed_ids <= set(owners),
        "warm_recalled_prior_ids": bool(seed_ids & set(warm["recalled_ids"])),
        "cross_worker_memory_delivered": bool(transfers),
        "warm_all_acceptance_checks": warm["evaluation"]["passed"],
        "disabled_all_acceptance_checks": disabled["evaluation"]["passed"],
        "disabled_received_no_memory": not any(disabled["memory_before"].values()) and not any(disabled["delivered_ids"].values()),
        "disabled_wrote_no_memory": not any(disabled["memory_written"].values()),
        "no_private_answer_in_disabled_requests": bool(no_memory_requests) and all(value not in body for body in no_memory_requests for value in private_values),
        "all_dependency_and_resource_traces_valid": all(t["passed"] for t in traces.values()),
    }
    return {"passed": all(checks.values()), "checks": checks,
            "runs": {name: {"id": r["run_id"], "status": r["status"], "acceptance_passed": r["evaluation"]["passed"],
                            "failed_acceptance_checks": [k for k,v in r["evaluation"]["checks"].items() if not v],
                            "calls": r["calls"], "reported_search_requests": r["reported_search_requests"],
                            "verified_cited_urls": len(r["evaluation"]["cited_verified_urls"]),
                            "memory_before": r["memory_before"], "memory_written": r["memory_written"],
                            "peak_execution_calls": traces[name]["peak_execution_calls"], "process_id": r["process_id"]}
                     for name,r in results.items()},
            "cross_worker_transfers": [{"from": a, "to": b, "memory_id": ident} for a,b,ident in transfers],
            "trace_audits": traces,
            "limits": "One matched follow-up pair is a functional memory test, not a reliability or latency benchmark. Seed limitations are preserved."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("seed", "warm", "disabled"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    dirs = {name: ROOT / "runs" / getattr(args, name) for name in ("seed", "warm", "disabled")}
    result = compare(**{name + "_dir": path for name,path in dirs.items()})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
