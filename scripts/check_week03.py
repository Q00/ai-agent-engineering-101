#!/usr/bin/env python3
"""Structural checks for the week-03 submission. CI runs exactly this.

Usage: python scripts/check_week03.py submissions/<student-id>/week-03

There is no starter this week, so the checks are a data contract, not a file
layout: your code can be organised any way you like, but the tasks file, the
results table, the logs, and the report must have the shape below.
"""
import ast
import csv
import json
import sys
from pathlib import Path

CONDITIONS = ("baseline", "homogeneous", "overconfident")
HEADER = ["run", "condition", "tasks", "correct", "messages", "unassigned", "misawards", "note"]
MIN_TASKS = 5
MIN_RUNS_PER_CONDITION = 3


def fail(msg: str):
    print(f"FAIL  {msg}")
    fail.count += 1


fail.count = 0


def ok(msg: str):
    print(f"ok    {msg}")


def check_python(sub: Path):
    files = [p for p in sub.rglob("*.py") if "__pycache__" not in p.parts]
    if not files:
        fail("no .py file found (the contract net has to be code you ran)")
        return
    for p in files:
        try:
            ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as e:
            fail(f"{p.relative_to(sub)} has a syntax error: {e}")
            return
    ok(f"{len(files)} .py file(s) parse")


def check_tasks(sub: Path):
    tf = sub / "tasks.json"
    if not tf.is_file():
        fail("tasks.json is missing (the task set with a gold contractor per task)")
        return
    try:
        tasks = json.loads(tf.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail(f"tasks.json is not valid JSON: {e}")
        return
    if not isinstance(tasks, list) or len(tasks) < MIN_TASKS:
        fail(f"tasks.json must be a list of at least {MIN_TASKS} tasks")
        return
    bad = [i for i, t in enumerate(tasks)
           if not isinstance(t, dict) or not all(k in t for k in ("id", "desc", "gold"))]
    if bad:
        fail(f"tasks.json entries {bad} lack id, desc, or gold")
    else:
        golds = {t["gold"] for t in tasks}
        if len(golds) < 2:
            fail("tasks.json gives every task the same gold contractor; allocation quality is then trivial")
        else:
            ok(f"tasks.json has {len(tasks)} tasks across {len(golds)} gold contractors")


def check_results(sub: Path):
    res = sub / "results.csv"
    if not res.is_file():
        fail("results.csv is missing")
        return
    with res.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0] != HEADER:
        fail(f"results.csv header must be exactly {','.join(HEADER)}")
        return
    data = [r for r in rows[1:] if any(c.strip() for c in r)]
    per = {c: 0 for c in CONDITIONS}
    bad = 0
    for r in data:
        if len(r) != len(HEADER):
            bad += 1
            continue
        _, condition, tasks, correct, messages, unassigned, misawards, _ = r
        if condition not in per:
            bad += 1
            continue
        per[condition] += 1
        for v in (tasks, correct, messages, unassigned, misawards):
            if v.strip() and not v.strip().isdigit():
                bad += 1
                break
    if bad:
        fail(f"results.csv has {bad} malformed row(s): condition must be one of "
             f"{'|'.join(CONDITIONS)}, counts must be integers (blank only for a crashed run)")
    for c, n in per.items():
        if n < MIN_RUNS_PER_CONDITION:
            fail(f"results.csv has {n} run(s) for {c}; at least {MIN_RUNS_PER_CONDITION} required")
        else:
            ok(f"results.csv has {n} run(s) for {c}")


def check_logs(sub: Path):
    logs = sub / "logs"
    files = [p for p in logs.iterdir() if p.is_file()] if logs.is_dir() else []
    need = len(CONDITIONS) * MIN_RUNS_PER_CONDITION
    if len(files) < need:
        fail(f"logs/ has {len(files)} file(s); one capture per run, at least {need}")
    else:
        ok(f"logs/ contains {len(files)} file(s)")


def check_report(sub: Path):
    rep = sub / "REPORT.md"
    if not rep.is_file():
        fail("REPORT.md is missing (setup, results, comparison with Smith 1980, interpretation)")
        return
    text = rep.read_text(encoding="utf-8")
    if len(text.strip()) < 600:
        fail("REPORT.md looks empty; write the four parts")
        return
    if not any(line.lstrip().startswith("|") for line in text.splitlines()):
        fail("REPORT.md has no markdown table (results table and the Smith 1980 comparison table)")
        return
    ok("REPORT.md present with a table")


def check_keys(sub: Path):
    for p in sub.rglob("*"):
        if p.is_file() and p.stat().st_size < 1_000_000:
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for marker in ("sk-ant-", "sk-or-v1-", "sk-proj-"):
                if marker in text:
                    fail(f"{p} appears to contain an API key ({marker}...) — remove it and rotate the key")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    sub = Path(sys.argv[1])
    if not sub.is_dir():
        fail(f"{sub} is not a directory")
        return 1

    check_python(sub)
    check_tasks(sub)
    check_results(sub)
    check_logs(sub)
    check_report(sub)
    check_keys(sub)

    if fail.count:
        print(f"\n{fail.count} check(s) failed")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
