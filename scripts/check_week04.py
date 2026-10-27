#!/usr/bin/env python3
"""Structural checks for the week-04 submission. CI runs exactly this.

Usage: python scripts/check_week04.py submissions/<student-id>/week-04

There is no starter this week, so the checks are a data contract, not a file
layout: your code can be organised any way you like, but the scenario file,
the results table, the logs, and the report must have the shape below.
"""
import ast
import csv
import json
import sys
from collections import Counter
from pathlib import Path

CONDITIONS = ("free", "tagged", "structured")
OUTCOMES = ("deal", "no_deal", "open")
HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price", "correct",
          "violation", "turns", "format_errors", "reader_calls", "note"]
MIN_SCENARIOS = 4
MIN_REPEATS = 3


def fail(msg: str):
    print(f"FAIL  {msg}")
    fail.count += 1


fail.count = 0


def ok(msg: str):
    print(f"ok    {msg}")


def check_python(sub: Path):
    files = [p for p in sub.rglob("*.py") if "__pycache__" not in p.parts]
    if not files:
        fail("no .py file found (the negotiation has to be code you ran)")
        return
    for p in files:
        try:
            ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as e:
            fail(f"{p.relative_to(sub)} has a syntax error: {e}")
            return
    ok(f"{len(files)} .py file(s) parse")


def check_scenarios(sub: Path) -> set:
    sf = sub / "scenarios.json"
    if not sf.is_file():
        fail("scenarios.json is missing (the scenario set with reserve and budget per item)")
        return set()
    try:
        scenarios = json.loads(sf.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail(f"scenarios.json is not valid JSON: {e}")
        return set()
    if not isinstance(scenarios, list) or len(scenarios) < MIN_SCENARIOS:
        fail(f"scenarios.json must be a list of at least {MIN_SCENARIOS} scenarios")
        return set()
    bad = [i for i, s in enumerate(scenarios)
           if not isinstance(s, dict) or not all(k in s for k in ("id", "item", "reserve", "budget"))
           or not isinstance(s.get("reserve"), int) or not isinstance(s.get("budget"), int)]
    if bad:
        fail(f"scenarios.json entries {bad} lack id, item, or integer reserve and budget")
        return set()
    possible = [s for s in scenarios if s["reserve"] <= s["budget"]]
    if not possible or len(possible) == len(scenarios):
        fail("scenarios.json needs at least one scenario where a deal is possible "
             "(reserve <= budget) and at least one where it is not")
    else:
        ok(f"scenarios.json has {len(scenarios)} scenarios, {len(possible)} with a deal possible")
    return {str(s["id"]) for s in scenarios}


def check_results(sub: Path, ids: set):
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
    per = Counter()
    runs = set()
    bad = 0
    for r in data:
        if len(r) != len(HEADER):
            bad += 1
            continue
        run, condition, scenario, deal_possible, outcome, price, *counts, _ = r
        counts = [deal_possible] + counts
        if condition not in CONDITIONS or (outcome.strip() and outcome not in OUTCOMES):
            bad += 1
            continue
        if ids and scenario.strip() not in ids:
            bad += 1
            continue
        if any(v.strip() and not v.strip().isdigit() for v in counts + [price]):
            bad += 1
            continue
        per[(condition, scenario.strip())] += 1
        runs.add(run.strip())
    if bad:
        fail(f"results.csv has {bad} malformed row(s): condition must be one of {'|'.join(CONDITIONS)}, "
             f"outcome one of {'|'.join(OUTCOMES)}, scenario an id from scenarios.json, "
             f"counts integers (blank only for a crashed episode)")
    for c in CONDITIONS:
        short = [s for s in sorted(ids) if per[(c, s)] < MIN_REPEATS]
        n = sum(v for (cc, _), v in per.items() if cc == c)
        if not ids:
            fail(f"cannot check repeats for {c}: scenarios.json is missing or invalid")
        elif short:
            fail(f"results.csv: condition {c} has fewer than {MIN_REPEATS} episodes for scenario(s) {short}")
        else:
            ok(f"results.csv has {n} episode(s) for {c}, {MIN_REPEATS}+ per scenario")
    check_results.runs = len(runs)


check_results.runs = 0


def check_logs(sub: Path):
    logs = sub / "logs"
    files = [p for p in logs.iterdir() if p.is_file()] if logs.is_dir() else []
    need = max(len(CONDITIONS) * MIN_REPEATS, check_results.runs)
    if len(files) < need:
        fail(f"logs/ has {len(files)} file(s); one capture per run, at least {need}")
    else:
        ok(f"logs/ contains {len(files)} file(s)")


def check_report(sub: Path):
    rep = sub / "REPORT.md"
    if not rep.is_file():
        fail("REPORT.md is missing (setup, results, comparison with FIPA-ACL, interpretation)")
        return
    text = rep.read_text(encoding="utf-8")
    if len(text.strip()) < 600:
        fail("REPORT.md looks empty; write the four parts")
        return
    if not any(line.lstrip().startswith("|") for line in text.splitlines()):
        fail("REPORT.md has no markdown table (results table and the FIPA-ACL comparison table)")
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
    ids = check_scenarios(sub)
    check_results(sub, ids)
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
