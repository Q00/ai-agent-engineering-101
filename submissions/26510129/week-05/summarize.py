"""results.csv folded to one row per condition, plus the per-episode table. REPORT.md copies this.

Usage: python summarize.py [results.csv]
"""
import csv
import sys
from pathlib import Path

CONDITIONS = ("prompt", "server", "prompt_inject", "server_inject")
COLS = ["run", "condition", "scenario", "deal_possible", "outcome", "price", "correct", "violation",
        "attempted_violations", "refused_calls", "turns", "tool_calls", "note"]


def n(r, k):
    return int(r[k]) if r[k].strip() else 0


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("results.csv")
    if not path.is_file():
        print(f"{path} not found; run the episodes first")
        return 1
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    print("| condition | episodes | correct | violations | attempted violations | refused calls "
          "| refused then valid, same turn | mean turns | mean tool calls | deal / no_deal / open / crashed |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for c in CONDITIONS:
        rs = [r for r in rows if r["condition"] == c]
        if not rs:
            continue
        live = [r for r in rs if r["outcome"]]
        outs = [sum(1 for r in live if r["outcome"] == o) for o in ("deal", "no_deal", "open")]
        rtv = sum(int(p.split("=")[1]) for r in live for p in r["note"].split("; ")
                  if p.startswith("refused_then_valid="))
        mean = lambda k: sum(n(r, k) for r in live) / len(live) if live else 0
        print(f"| {c} | {len(rs)} | {sum(n(r, 'correct') for r in live)} | "
              f"{sum(n(r, 'violation') for r in live)} | {sum(n(r, 'attempted_violations') for r in live)} | "
              f"{sum(n(r, 'refused_calls') for r in live)} | {rtv} | {mean('turns'):.1f} | "
              f"{mean('tool_calls'):.1f} | {' / '.join(map(str, outs))} / {len(rs) - len(live)} |")

    print("\n| " + " | ".join(COLS) + " |")
    print("|" + "---|" * len(COLS))
    for r in rows:
        print("| " + " | ".join(r[c] for c in COLS) + " |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
