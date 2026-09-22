"""Read results.csv back and print the report's tables.

The report has to state the same numbers the CSV holds, and typing them out
by hand is how the two stop agreeing. Everything in REPORT.md's results
section comes from here.

  python analyze.py                 # results.csv
  python analyze.py results_pilot_openrouter.csv

Dead episodes are carried through rather than filtered out. A condition that
crashed nine times and closed three deals is not the same as one that closed
three deals, and a summary that silently drops the blanks cannot tell them
apart, so the episode count is printed next to every row.
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONDITIONS = ("free", "tagged", "structured")


def load(path: Path) -> list:
    with path.open(encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if any(v.strip() for v in r.values())]


def num(value, default=0):
    value = (value or "").strip()
    return int(value) if value.isdigit() else default


def summary(rows: list) -> str:
    by = defaultdict(list)
    for r in rows:
        by[r["condition"]].append(r)

    out = ["| condition | episodes | dead | correct | violation | mean turns | "
           "format errors | reader calls |",
           "|---|---|---|---|---|---|---|---|"]
    for c in CONDITIONS:
        rs = by.get(c, [])
        if not rs:
            continue
        live = [r for r in rs if r["outcome"].strip()]
        dead = len(rs) - len(live)
        turns = [num(r["turns"]) for r in live]
        out.append(
            f"| {c} | {len(rs)} | {dead} | "
            f"{sum(num(r['correct']) for r in live)}/{len(live)} | "
            f"{sum(num(r['violation']) for r in live)} | "
            f"{(sum(turns) / len(turns) if turns else 0):.1f} | "
            f"{sum(num(r['format_errors']) for r in live)} | "
            f"{sum(num(r['reader_calls']) for r in live)} |")
    return "\n".join(out)


def outcomes(rows: list) -> str:
    by = defaultdict(lambda: defaultdict(int))
    for r in rows:
        by[r["condition"]][r["outcome"].strip() or "crashed"] += 1
    out = ["| condition | deal | no_deal | open | crashed |", "|---|---|---|---|---|"]
    for c in CONDITIONS:
        if c not in by:
            continue
        d = by[c]
        out.append(f"| {c} | {d['deal']} | {d['no_deal']} | {d['open']} | {d['crashed']} |")
    return "\n".join(out)


def by_possibility(rows: list) -> str:
    """The headline `correct` hides which half of the scenario set earned it.

    A condition that walks away from everything scores every impossible
    scenario and no possible one. Splitting the column is the only way to
    see that, and the reference run in the lecture is exactly that shape.
    """
    by = defaultdict(lambda: [0, 0, 0, 0])
    for r in rows:
        if not r["outcome"].strip():
            continue
        i = 0 if r["deal_possible"] == "1" else 2
        by[r["condition"]][i] += num(r["correct"])
        by[r["condition"]][i + 1] += 1
    out = ["| condition | correct where a deal was possible | "
           "correct where it was not |", "|---|---|---|"]
    for c in CONDITIONS:
        if c not in by:
            continue
        a, an, b, bn = by[c]
        out.append(f"| {c} | {a}/{an} | {b}/{bn} |")
    return "\n".join(out)


def episodes(rows: list) -> str:
    cols = ["run", "condition", "scenario", "deal_possible", "outcome", "price",
            "correct", "violation", "turns", "format_errors", "reader_calls", "note"]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in sorted(rows, key=lambda r: (r["condition"], r["run"], r["scenario"])):
        cells = [(r.get(c) or "").replace("|", "\\|") for c in cols]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "results.csv"
    if not path.is_absolute():
        path = HERE / path
    if not path.is_file():
        print(f"{path} not found")
        return 1
    rows = load(path)
    print(f"# {path.name}: {len(rows)} rows\n")
    print("## Per condition\n")
    print(summary(rows))
    print("\n## How episodes ended\n")
    print(outcomes(rows))
    print("\n## Where the correct answers came from\n")
    print(by_possibility(rows))
    print("\n## Every episode\n")
    print(episodes(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
