"""Print descriptive summaries for the original and two Week 04 follow-ups."""

import csv
from collections import Counter
from pathlib import Path
from statistics import mean

BASE = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
CONDITIONS = ("free", "tagged", "structured")


def load(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def totals(rows):
    valid = [r for r in rows if r["outcome"]]
    outcomes = Counter(r["outcome"] for r in valid)
    return (f"{sum(int(r['correct']) for r in valid)}/{len(valid)}",
            f"{outcomes['deal']}/{outcomes['no_deal']}/{outcomes['open']}",
            sum(int(r["violation"]) for r in valid),
            f"{mean(int(r['turns']) for r in valid):.2f}" if valid else "n/a",
            sum(int(r["format_errors"]) for r in valid),
            sum(int(r["reader_calls"]) for r in valid),
            len(rows) - len(valid))


def summarize(name, rows):
    print(f"\n## {name} ({len(rows)} rows)")
    print("| condition | correct | deal/no_deal/open | violation | mean turns | "
          "format errors | reader calls | crashes |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for condition in CONDITIONS:
        values = totals([r for r in rows if r["condition"] == condition])
        print(f"| {condition} | " + " | ".join(map(str, values)) + " |")
    print("\n| condition | scenario | correct | deal/no_deal/open |")
    print("|---|---|---:|---:|")
    for condition in CONDITIONS:
        for scenario in ("bike", "textbook", "keyboard", "laptop"):
            subset = [r for r in rows if r["condition"] == condition and
                      r["scenario"] == scenario and r["outcome"]]
            counts = Counter(r["outcome"] for r in subset)
            print(f"| {condition} | {scenario} | "
                  f"{sum(int(r['correct']) for r in subset)}/{len(subset)} | "
                  f"{counts['deal']}/{counts['no_deal']}/{counts['open']} |")


def main():
    original = load(BASE / "results.csv")
    replication = load(HERE / "replication.csv")
    termination = load(HERE / "termination.csv")
    for name, rows in (("original", original), ("replication", replication),
                       ("combined unchanged prompt", original + replication),
                       ("common deadline policy", termination)):
        summarize(name, rows)


if __name__ == "__main__":
    main()
