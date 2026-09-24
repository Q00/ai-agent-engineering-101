"""results.csv -> the two tables REPORT.md part 2 asks for, as markdown.

The per-condition table has the same columns as the reference run's table in
the lecture notes, so the two can be read side by side. Crashed episodes are
kept in the per-episode table and left out of the means, with a count printed
underneath: dropping them silently would flatter the run.

  python summarize.py            > paste the output into REPORT.md
"""
import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONDITIONS = ("free", "tagged", "structured")
OUTCOMES = ("deal", "no_deal", "open")


def load(results):
    with results.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if any((v or "").strip() for v in r.values())]


def num(row, key):
    v = (row.get(key) or "").strip()
    return int(v) if v.isdigit() else None


def per_condition(rows):
    out = ["| condition | correct / n | deal, no_deal, open | violation | mean turns "
           "| format errors | reader calls |",
           "|---|---|---|---|---|---|---|"]
    for c in CONDITIONS:
        got = [r for r in rows if r["condition"] == c]
        live = [r for r in got if num(r, "turns") is not None]
        counts = [sum(1 for r in live if r["outcome"] == o) for o in OUTCOMES]
        correct = sum(num(r, "correct") or 0 for r in live)
        violation = sum(num(r, "violation") or 0 for r in live)
        fmt = sum(num(r, "format_errors") or 0 for r in live)
        reader = sum(num(r, "reader_calls") or 0 for r in live)
        turns = [num(r, "turns") for r in live]
        mean = f"{sum(turns) / len(turns):.1f}" if turns else "-"
        out.append(f"| `{c}` | {correct} / {len(got)} | {', '.join(str(n) for n in counts)} "
                   f"| {violation} | {mean} | {fmt} | {reader} |")
    return "\n".join(out)


def per_episode(rows):
    cols = ["run", "condition", "scenario", "deal_possible", "outcome", "price",
            "correct", "violation", "turns", "format_errors", "reader_calls", "note"]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in sorted(rows, key=lambda r: (CONDITIONS.index(r["condition"])
                                         if r["condition"] in CONDITIONS else 9,
                                         r["run"], r["scenario"])):
        cells = [(r.get(c) or "").strip().replace("|", "\\|") for c in cols]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def main():
    results = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "results.csv"
    if not results.is_file():
        print(f"{results} not found -- run the lab first")
        return 1
    rows = load(results)
    crashed = [r for r in rows if num(r, "turns") is None]

    print("### Per condition\n")
    print(per_condition(rows))
    print()
    if crashed:
        print(f"{len(crashed)} crashed episode(s) are in the table below and out of the "
              f"means above.\n")
    print("### Per episode\n")
    print(per_episode(rows))
    print()
    print(f"{len(rows)} episode(s) total, {len(rows) - len(crashed)} completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
