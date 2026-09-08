#!/usr/bin/env python3
"""Structural checks for the week-02 submission. CI runs exactly this.

Usage: python scripts/check_week02.py submissions/<student-id>/week-02
"""
import ast
import csv
import sys
from pathlib import Path

HARNESSES = ("harness_react.py", "harness_plan_execute.py")
HEADER = ["run", "harness", "success", "tokens", "iters", "interventions", "note"]
MIN_RUNS_PER_HARNESS = 3


def fail(msg: str):
    print(f"FAIL  {msg}")
    fail.count += 1


fail.count = 0


def ok(msg: str):
    print(f"ok    {msg}")


def imports_tools_shared(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "tools_shared":
            return True
        if isinstance(node, ast.Import) and any(a.name == "tools_shared" for a in node.names):
            return True
    return False


def check_python(sub: Path):
    shared = sub / "tools_shared.py"
    if not shared.is_file():
        fail("tools_shared.py is missing (both harnesses must share one tools module)")
    else:
        try:
            ast.parse(shared.read_text(encoding="utf-8"))
            ok("tools_shared.py parses")
        except SyntaxError as e:
            fail(f"tools_shared.py has a syntax error: {e}")

    for name in HARNESSES:
        p = sub / name
        if not p.is_file():
            fail(f"{name} is missing")
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as e:
            fail(f"{name} has a syntax error: {e}")
            continue
        if imports_tools_shared(tree):
            ok(f"{name} parses and imports tools_shared")
        else:
            fail(f"{name} does not import tools_shared (same tools for both harnesses)")


def check_task(sub: Path):
    task = sub / "TASK.md"
    if not task.is_file():
        fail("TASK.md is missing (task, success criterion, expected answer)")
        return
    text = task.read_text(encoding="utf-8")
    missing = [k for k in ("task:", "expected:") if k not in text]
    if missing:
        fail(f"TASK.md lacks {', '.join(missing)} line(s)")
    else:
        ok("TASK.md has task: and expected: lines")


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
    per = {"react": 0, "plan_exec": 0}
    bad = 0
    for r in data:
        if len(r) != len(HEADER):
            bad += 1
            continue
        _, harness, success, tokens, iters, interventions, _ = r
        if harness not in per or success not in ("O", "X"):
            bad += 1
            continue
        per[harness] += 1
        for v in (tokens, iters, interventions):
            if v.strip() and not v.strip().isdigit():
                bad += 1
                break
    if bad:
        fail(f"results.csv has {bad} malformed row(s): harness react|plan_exec, success O|X, integer counts")
    for h, n in per.items():
        if n < MIN_RUNS_PER_HARNESS:
            fail(f"results.csv has {n} run(s) for {h}; at least {MIN_RUNS_PER_HARNESS} required")
        else:
            ok(f"results.csv has {n} run(s) for {h}")


def check_logs(sub: Path):
    logs = sub / "logs"
    files = [p for p in logs.iterdir() if p.is_file()] if logs.is_dir() else []
    if len(files) < 2 * MIN_RUNS_PER_HARNESS:
        fail(f"logs/ has {len(files)} file(s); one capture per run, at least {2 * MIN_RUNS_PER_HARNESS}")
    else:
        ok(f"logs/ contains {len(files)} file(s)")


def check_report(sub: Path):
    rep = sub / "REPORT.md"
    if not rep.is_file():
        fail("REPORT.md is missing (variant definition, measurements, one paragraph of interpretation)")
    elif len(rep.read_text(encoding="utf-8").strip()) < 300:
        fail("REPORT.md looks empty; write the three parts")
    else:
        ok("REPORT.md present")


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
    check_task(sub)
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
