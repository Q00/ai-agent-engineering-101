"""Replay the recorded planner replies through both plan parsers.

Offline and deterministic: it reads the raw reply that each failed plan_exec run
already wrote to its log and asks what each parser would make of it. No API call
and no new result row, so this is a prediction about the tolerant condition and
never a substitute for running it.

python replay_plan_failures.py
"""
import ast
import re
from pathlib import Path

from harness_plan_execute import iter_json_arrays, parse_plan

ROOT = Path(__file__).resolve().parent
RAW = re.compile(r"^\[plan\] not valid JSON: (.+)$", flags=re.M)


def recorded_failures(logs=ROOT / "logs"):
    """Yield (log name, raw planner reply) for every recorded plan parse failure."""
    for path in sorted(logs.glob("plan_exec-*.txt")):
        found = RAW.search(path.read_text(encoding="utf-8"))
        if found:
            # The log stores the reply as a Python repr on one line.
            yield path.name, ast.literal_eval(found.group(1).strip())


def main():
    rows = list(recorded_failures())
    if not rows:
        print("no recorded plan parse failures found in logs/")
        return 0
    for name, raw in rows:
        arrays = list(iter_json_arrays(raw))
        strict = parse_plan(raw)
        tolerant = parse_plan(raw, tolerant=True)
        print(f"{name}: {len(raw)} chars, {len(arrays)} balanced array(s)")
        print(f"  strict   -> {strict!r}")
        print(f"  tolerant -> {tolerant!r}")
        print(f"  usable   -> {'no plan' if tolerant is None else 'see the steps above'}")
        print()
    salvaged = sum(parse_plan(raw, tolerant=True) is not None for _, raw in rows)
    print(f"strict parsed 0/{len(rows)}; tolerant parsed {salvaged}/{len(rows)}.")
    print("Whether a salvaged plan is the plan the model meant is a separate")
    print("question, and the steps printed above are the evidence for it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
