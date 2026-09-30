"""Aggregate results.csv into the two markdown tables REPORT.md needs.

Usage: python summarize.py            # prints both tables

Per condition: episodes, correct, violations, deals, no_deals, open, mean
turns, format errors, reader calls. Then every episode row, in run order.
Crashed episodes (blank outcome) are listed but excluded from the means.
"""
import csv
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORDER = ("free", "tagged", "structured")


def load():
    with (HERE / "results.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def per_condition(rows):
    agg = defaultdict(lambda: defaultdict(int))
    for r in rows:
        a = agg[r["condition"]]
        a["episodes"] += 1
        if not r["outcome"]:
            a["crashed"] += 1
            continue
        a["done"] += 1
        a[r["outcome"]] += 1
        for k in ("correct", "violation", "turns", "format_errors", "reader_calls"):
            a[k] += int(r[k])
    print("| condition | episodes | correct | violations | deal / no_deal / open | mean turns | format errors | reader calls |")
    print("|---|---|---|---|---|---|---|---|")
    for c in ORDER:
        a = agg[c]
        if not a["episodes"]:
            continue
        crashed = f" ({a['crashed']} crashed)" if a["crashed"] else ""
        mean = a["turns"] / a["done"] if a["done"] else 0
        print(f"| {c} | {a['episodes']}{crashed} | {a['correct']}/{a['done']} | {a['violation']} | "
              f"{a['deal']} / {a['no_deal']} / {a['open']} | {mean:.1f} | {a['format_errors']} | {a['reader_calls']} |")


def per_episode(rows):
    print("\n| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: (ORDER.index(r["condition"]), int(r["run"]), r["scenario"])):
        note = r["note"].replace("|", "/")
        print(f"| {r['run']} | {r['condition']} | {r['scenario']} | {r['deal_possible']} | {r['outcome']} | "
              f"{r['price']} | {r['correct']} | {r['violation']} | {r['turns']} | {r['format_errors']} | "
              f"{r['reader_calls']} | {note} |")


if __name__ == "__main__":
    rows = load()
    per_condition(rows)
    per_episode(rows)
