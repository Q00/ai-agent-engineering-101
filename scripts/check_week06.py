#!/usr/bin/env python3
"""Structural checks for the week-06 submission. CI runs exactly this.

Usage: python scripts/check_week06.py submissions/<student-id>/week-06

There is no starter this week, so the checks are a data contract, not a file
layout: your sellers, buyer, and runner can be organised any way you like, but
the saved Agent Cards, the scenario file, the results table, the logs, and the
report must have the shape below.
"""
import ast
import csv
import json
import sys
from collections import Counter
from pathlib import Path

CONDITIONS = ("plain", "inflated")                 # both required
CHOSEN = ("cheap", "pricey")
OUTCOMES = ("deal", "rejected", "no_deal", "no_choice")
ORDERS = ("cheap/pricey", "pricey/cheap")
HEADER = ["run", "condition", "scenario", "item", "budget", "card_order", "chosen", "outcome",
          "price", "violation", "attempted_violations", "turns", "llm_calls", "note"]
MIN_SCENARIOS = 4
MIN_REPEATS = 3
MIN_CARDS = 3
MIN_TABLE_ROWS = 8


def fail(msg: str):
    print(f"FAIL  {msg}")
    fail.count += 1


fail.count = 0


def ok(msg: str):
    print(f"ok    {msg}")


def check_python(sub: Path):
    files = [p for p in sub.rglob("*.py") if "__pycache__" not in p.parts]
    if not files:
        fail("no .py file found (the sellers and the runner have to be code you ran)")
        return
    for p in files:
        try:
            ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as e:
            fail(f"{p.relative_to(sub)} has a syntax error: {e}")
            return
    ok(f"{len(files)} .py file(s) parse")


def check_cards(sub: Path):
    cards = sorted((sub / "cards").glob("*.json")) if (sub / "cards").is_dir() else []
    if len(cards) < MIN_CARDS:
        fail(f"cards/ has {len(cards)} JSON file(s); save the Agent Card each seller served, "
             f"at least {MIN_CARDS} (cheap, pricey plain, pricey inflated)")
        return
    bad = []
    for p in cards:
        try:
            c = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            bad.append(p.name)
            continue
        ifaces = c.get("supportedInterfaces")
        skills = c.get("skills")
        if not (isinstance(c.get("name"), str) and isinstance(c.get("description"), str)
                and isinstance(ifaces, list) and ifaces
                and all(isinstance(i, dict) and all(k in i for k in ("url", "protocolBinding", "protocolVersion"))
                        for i in ifaces)
                and isinstance(skills, list) and skills
                and all(isinstance(s, dict) and all(k in s for k in ("id", "name", "description")) for s in skills)):
            bad.append(p.name)
    if bad:
        fail(f"cards/{bad} are not A2A v1.0 Agent Cards (name, description, supportedInterfaces with "
             f"url/protocolBinding/protocolVersion, skills with id/name/description)")
    else:
        ok(f"cards/ has {len(cards)} Agent Card(s)")


def check_scenarios(sub: Path) -> set:
    sf = sub / "scenarios.json"
    if not sf.is_file():
        fail("scenarios.json is missing")
        return set()
    try:
        scenarios = json.loads(sf.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail(f"scenarios.json is not valid JSON: {e}")
        return set()
    keys = ("id", "item", "budget", "cheap_reserve", "pricey_reserve")
    if not isinstance(scenarios, list) or len(scenarios) < MIN_SCENARIOS:
        fail(f"scenarios.json must be a list of at least {MIN_SCENARIOS} scenarios")
        return set()
    bad = [i for i, s in enumerate(scenarios)
           if not isinstance(s, dict) or not all(k in s for k in keys)
           or not all(isinstance(s.get(k), int) for k in keys[2:])]
    if bad:
        fail(f"scenarios.json entries {bad} lack {', '.join(keys)} (the last three integers)")
        return set()
    only_cheap = [s for s in scenarios if s["cheap_reserve"] <= s["budget"] < s["pricey_reserve"]]
    both = [s for s in scenarios if s["pricey_reserve"] <= s["budget"] and s["cheap_reserve"] <= s["budget"]]
    if not only_cheap or not both:
        fail("scenarios.json needs at least one scenario only the cheap shop can serve "
             "(cheap_reserve <= budget < pricey_reserve) and one both shops can serve")
    else:
        ok(f"scenarios.json has {len(scenarios)} scenarios ({len(only_cheap)} cheap-only, {len(both)} both)")
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
        d = dict(zip(HEADER, (c.strip() for c in r)))
        ints = [d[k] for k in ("budget", "price", "violation", "attempted_violations", "turns", "llm_calls")]
        if (d["condition"] not in CONDITIONS or d["card_order"] not in ORDERS
                or (d["chosen"] and d["chosen"] not in CHOSEN)
                or (d["outcome"] and d["outcome"] not in OUTCOMES)
                or (ids and d["scenario"] not in ids)
                or any(v and not v.isdigit() for v in ints)
                or (d["outcome"] == "deal" and not d["price"])):
            bad += 1
            continue
        per[(d["condition"], d["scenario"])] += 1
        runs.add((d["condition"], d["run"]))
    if bad:
        fail(f"results.csv has {bad} malformed row(s): condition one of {'|'.join(CONDITIONS)}, "
             f"card_order one of {'|'.join(ORDERS)}, chosen one of {'|'.join(CHOSEN)}, "
             f"outcome one of {'|'.join(OUTCOMES)}, scenario an id from scenarios.json, "
             f"numbers as integers (blank only for a crashed episode), a price for every deal")
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
        return
    text = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in files)
    if "TASK_STATE_INPUT_REQUIRED" not in text:
        fail("logs/ never shows TASK_STATE_INPUT_REQUIRED; log every A2A request and the task state it returned")
        return
    ok(f"logs/ contains {len(files)} file(s) with A2A task states")


def check_report(sub: Path):
    rep = sub / "REPORT.md"
    if not rep.is_file():
        fail("REPORT.md is missing (setup, results, FIPA-ACL and A2A table, interpretation)")
        return
    text = rep.read_text(encoding="utf-8")
    if len(text.strip()) < 600:
        fail("REPORT.md looks empty; write the four parts")
        return
    longest, run = 0, 0
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("|"):
            if not set(s) <= set("|-: "):
                run += 1
                longest = max(longest, run)
        else:
            run = 0
    if longest - 1 < MIN_TABLE_ROWS:
        fail(f"REPORT.md needs a table with at least {MIN_TABLE_ROWS} rows (the FIPA-ACL and A2A comparison)")
        return
    ok(f"REPORT.md present, longest table has {longest - 1} row(s)")


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
    check_cards(sub)
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
