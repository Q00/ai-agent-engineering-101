"""Export the recorded peer study to the course CSV contract; no API calls.

An actual CLI invocation (one case) is one CSV row. Five cases form a reporting
block, never a fabricated execution. Historical CSV and raw logs are immutable.
"""
import argparse
from collections import Counter
import csv
import hashlib
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PEER = ROOT / "extensions/peer_dag"
BATCH_ID = "20260922T012131-no-token-limit-9703d2"
BATCH = PEER / "conditions" / BATCH_ID
OUTPUT = ROOT / "submission"
HEADER = ["run", "condition", "tasks", "correct", "messages", "unassigned", "misawards", "note"]
COUNTS = HEADER[2:7]
CONDITIONS = ("baseline", "homogeneous", "overconfident")
START, END = "<!-- submission-results:start -->", "<!-- submission-results:end -->"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def relative(path):
    return path.relative_to(ROOT).as_posix()


def jdump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def csv_bytes(rows):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=HEADER, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode()


def project(metric, numbered, gold, cohort):
    """Count protocol events, separating root allocation from recursive cost."""
    case = metric["case_id"]
    rows = [row for _, row in numbered]
    roots = [(n, row) for n, row in numbered
             if row["event"] == "award" and row["task_id"] == case]
    require(len(roots) <= 1, "multiple root awards")
    winner = roots[0][1]["worker"] if roots else None
    require(winner == metric["root_award"], "root award differs from study summary")
    end = [row for row in rows if row["event"] == "task_end" and row["task_id"] == case]
    require(len(end) == 1 and end[0]["outcome"]["status"] == metric["status"],
            "root status differs from recorded task_end")
    counts = Counter(announcements=0, bids=0, awards=0)
    root_counts = Counter(counts)
    event_lines = {key: [] for key in counts}
    for number, row in numbered:
        kind = None
        if row["event"] == "call_start" and row.get("phase") == "propose":
            kind = "announcements"
        elif row["event"] == "proposal" and row["proposal"]["bid"] is True:
            kind = "bids"
        elif row["event"] == "award":
            kind = "awards"
        if kind:
            counts[kind] += 1
            event_lines[kind].append(number)
            if row["task_id"] == case:
                root_counts[kind] += 1
    call_count = sum(row["event"] == "call_start" for row in rows)
    require(call_count == metric["calls"], "model call count differs from study summary")
    completed = metric["status"] == "succeeded"
    if completed:
        require(winner is not None, "successful execution lacks a root award")
    else:
        # This exporter is explicitly for this frozen study, whose interruptions
        # were HTTP 429 exhaustion. Do not silently classify a different failure.
        require(metric["http_429"] > 0 and metric["error"], "unexpected failure: review export policy")
    observed = {"tasks": 1, "correct": int(winner == gold) if winner else 0,
                "messages": sum(counts.values()), "unassigned": int(winner is None),
                "misawards": int(winner is not None and winner != gold)}
    note = {"cohort": cohort, "case": case, "block": metric["block"],
            "status": metric["status"], "gold": gold, "root_award": winner,
            "facts_passed": metric["passed"], "calls": call_count,
            "http_requests": metric["http_requests"], "http_429": metric["http_429"],
            "messages_scope": "all_recursive_negotiations", "message_parts": dict(counts),
            "root_messages": sum(root_counts.values()),
            "proposal_rejections": metric["proposal_rejections"],
            "console": f"logs/{metric['run_id']}.console.log",
            "trace": f"logs/{metric['run_id']}.jsonl"}
    if not completed:
        partial = dict(observed)
        partial["root_unawarded"] = partial.pop("unassigned")
        note.update(error=metric["error"], counts_omitted_due_to_error=True,
                    observed_partial=partial)
    result = {"run": metric["run_id"], "condition": metric["condition"],
              **{key: observed[key] if completed else "" for key in COUNTS}, "note": jdump(note)}
    evidence = {"run": metric["run_id"], "cohort": cohort, "case": case,
                "condition": metric["condition"], "block": metric["block"],
                "root_award_line": roots[0][0] if roots else None,
                "message_event_lines": event_lines, "observed": observed if completed else partial,
                "counts_published": completed, "status": metric["status"]}
    return result, evidence


def collect(folder, cohort, gold):
    summary = json.loads((folder / "summary.json").read_text())
    rows, evidence, copies = [], [], []
    for metric in summary["runs"]:
        trace = PEER / "logs" / (metric["run_id"] + ".jsonl")
        trace_bytes = trace.read_bytes()
        numbered = [(n, json.loads(line)) for n, line in
                    enumerate(trace_bytes.decode().splitlines(), 1)]
        row, item = project(metric, numbered, gold[metric["case_id"]], cohort)
        console = folder / (metric["run_id"] + ".console.log")
        console_bytes = console.read_bytes()
        destination = ROOT / "logs" / console.name
        trace_destination = ROOT / "logs" / trace.name
        # The console contains the command and final summary; full protocol
        # events were written to JSONL. Preserve both originals byte for byte.
        copies.extend(((destination, console_bytes), (trace_destination, trace_bytes)))
        item.update(trace=relative(trace), trace_sha256=digest(trace_bytes),
                    submission_trace=relative(trace_destination),
                    source_console=relative(console), console=relative(destination),
                    console_sha256=digest(console_bytes))
        rows.append(row)
        evidence.append(item)
    return rows, evidence, copies


def groups(rows):
    output = []
    for block in (1, 2, 3):
        for condition in CONDITIONS:
            subset = [r for r in rows if r["condition"] == condition
                      and json.loads(r["note"])["block"] == block]
            completed = [r for r in subset if r["tasks"] != ""]
            output.append({"block": block, "condition": condition, "attempts": len(subset),
                           "slots": len({json.loads(r["note"])["case"] for r in subset}),
                           "completed": len(completed), "failed": len(subset) - len(completed),
                           **{k: sum(int(r[k]) for r in completed) for k in COUNTS}})
    return output


def block_table(rows):
    lines = ["|회차|조건|완료/작업|정답|오배정|미배정|메시지|429 복구|",
             "|---:|---|---:|---:|---:|---:|---:|---:|"]
    for g in groups(rows):
        lines.append(f"|{g['block']}|{g['condition']}|{g['completed']}/{g['slots']}|"
                     f"{g['correct']}|{g['misawards']}|{g['unassigned']}|{g['messages']}|{g['failed']}|")
    return "\n".join(lines)


def individual_table(rows):
    lines = ["# 제출 CSV의 실행별 결과", "",
             "`results.csv`의 61개 실제 시도(본 실험 45 + 429 복구 16)를 그대로 표시한다. 완료 행은 중복 없이 원래 45개 슬롯에 대응한다. —는 0이 아닌 오류 중단으로 인한 공란이다.", "",
             "|회차|조건|작업|구분|상태|tasks|correct|messages|unassigned|misawards|원본 기록|",
             "|---:|---|---|---|---|---:|---:|---:|---:|---:|---|"]
    for row in rows:
        note = json.loads(row["note"])
        values = "|".join(str(row[k]) if row[k] != "" else "—" for k in COUNTS)
        lines.append(f"|{note['block']}|{row['condition']}|{note['case']}|{note['cohort']}|{note['status']}|{values}|"
                     f"[콘솔](../{note['console']}) · [전체 협의](../{note['trace']})|")
    return "\n".join(lines) + "\n"


def artifacts():
    gold = {t["id"]: t["gold"] for t in json.loads((ROOT / "tasks.json").read_text())}
    primary, primary_evidence, copies = collect(BATCH, "primary", gold)
    recovery, recovery_evidence, recovery_copies = collect(BATCH / "recovery_429", "recovery", gold)
    require(len(primary) == 45 and len(recovery) == 16, "wrong study sizes")
    expected = {(block, condition, case) for block in (1, 2, 3)
                for condition in CONDITIONS for case in gold}
    actual = [(e["block"], e["condition"], e["case"]) for e in primary_evidence]
    require(len(set(actual)) == len(actual) and set(actual) == expected, "incomplete/duplicate schedule")
    failures = {(e["block"], e["condition"], e["case"]) for e in primary_evidence if not e["counts_published"]}
    recovered = {(e["block"], e["condition"], e["case"]) for e in recovery_evidence}
    require(failures == recovered, "recovery does not match primary failures")
    require(len({r["run"] for r in primary + recovery}) == 61, "reused execution ID")
    for group in groups(primary):
        require(group["attempts"] == 5, "each condition/block must contain the same five cases")
    combined = primary + recovery
    completed_slots = [(json.loads(r["note"])["block"], r["condition"], json.loads(r["note"])["case"])
                       for r in combined if r["tasks"] != ""]
    require(len(completed_slots) == len(set(completed_slots)) == 45 and set(completed_slots) == expected,
            "final results must contain exactly one completed attempt for every original slot")
    evidence = {"batch_id": BATCH_ID, "source_summary_sha256": digest((BATCH / "summary.json").read_bytes()),
                "csv_sha256": digest(csv_bytes(combined)),
                "definitions": {"run": "one recorded CLI invocation of one case; no fabricated group run IDs",
                                "tasks": "root cases (1 per completed invocation), not recursive subtasks",
                                "allocation": "root award compared with precommitted gold",
                                "messages": "all depths: propose call_start + valid bid=true proposal + award",
                                "excluded_messages": "review, execution, synthesis, refusals, invalid proposals and HTTP retries",
                                "failure": "all five count columns blank; partial observations retained only in note/evidence",
                                "report_blocks": "CSV completed-row sums by original block and condition; one result per original slot",
                                "recovery": "user-requested final-slot estimate includes 429 recovery; original failed attempts remain blank rows"},
                "groups": groups(combined), "primary": primary_evidence, "recovery": recovery_evidence}
    outputs = {ROOT / "results.csv": csv_bytes(combined),
               OUTPUT / "results-primary.csv": csv_bytes(primary),
               OUTPUT / "results-recovery.csv": csv_bytes(recovery),
               OUTPUT / "RESULTS_BY_RUN.md": individual_table(combined).encode(),
               OUTPUT / "BLOCK_RESULTS.md": (block_table(combined) + "\n").encode(),
               OUTPUT / "evidence.json": (json.dumps(evidence, ensure_ascii=False, indent=2) + "\n").encode()}
    return outputs, copies + recovery_copies, combined


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    outputs, copies, rows = artifacts()
    archive = ROOT / "results-allocation-reference.csv"
    require(archive.is_file(), "archive the previous allocation CSV before exporting")
    for destination, content in copies:
        if destination.exists():
            require(destination.read_bytes() == content, f"refuse to overwrite original log: {destination.name}")
        elif args.write:
            with destination.open("xb") as stream:
                stream.write(content)
        else:
            raise ValueError(f"missing original log copy: {destination.name}")
    for destination, content in outputs.items():
        if args.write:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        else:
            require(destination.read_bytes() == content, f"derived artifact differs: {relative(destination)}")
    report = ROOT / "REPORT.md"
    text = report.read_text()
    require(text.count(START) == text.count(END) == 1, "report needs one generated result-table block")
    before, remainder = text.split(START)
    _, after = remainder.split(END)
    expected = before + START + "\n" + block_table(rows) + "\n" + END + after
    if args.write:
        report.write_text(expected)
    else:
        require(text == expected, "report table differs from the submission CSV")
    print(json.dumps({"passed": True, "mode": "write" if args.write else "check", "csv_rows": len(rows),
                      "completed_rows": sum(r["tasks"] != "" for r in rows),
                      "error_rows_with_blank_counts": sum(r["tasks"] == "" for r in rows),
                      "byte_identical_console_copies": sum(p.name.endswith(".console.log") for p, _ in copies),
                      "byte_identical_trace_copies": sum(p.suffix == ".jsonl" for p, _ in copies),
                      "report_groups": len(groups(rows))}))


if __name__ == "__main__":
    main()
