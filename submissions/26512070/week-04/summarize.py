"""Print the REPORT part-2 tables from results.csv as markdown.

    python summarize.py            # real runs only (r1..r3)
    python summarize.py --all      # include the r0 trial runs

Per condition: episodes, correct, violations, deals, mean turns, format
errors, reader calls. Then the same split by deal_possible, then every episode.
Crashed rows (blank outcome) are counted as episodes and listed, never dropped.
"""
import argparse
import csv
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONDITIONS = ("free", "tagged", "structured")


def load(include_trial):
    with (HERE / "results.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if include_trial or not r["run"].endswith("-r0")]


def num(r, k):
    return int(r[k]) if r[k].strip() else 0


def summary(rows, label):
    print(f"| {label} | episodes | correct | violations | deal / no_deal / open | "
          f"crashed | mean turns | format errors | reader calls |")
    print("|---|---|---|---|---|---|---|---|---|")
    for c in CONDITIONS:
        rs = [r for r in rows if r["condition"] == c]
        if not rs:
            continue
        ok = [r for r in rs if r["outcome"]]
        out = {o: sum(r["outcome"] == o for r in ok) for o in ("deal", "no_deal", "open")}
        turns = sum(num(r, "turns") for r in ok) / len(ok) if ok else 0
        print(f"| {c} | {len(rs)} | {sum(num(r, 'correct') for r in rs)}/{len(rs)} | "
              f"{sum(num(r, 'violation') for r in rs)} | "
              f"{out['deal']} / {out['no_deal']} / {out['open']} | {len(rs) - len(ok)} | "
              f"{turns:.1f} | {sum(num(r, 'format_errors') for r in rs)} | "
              f"{sum(num(r, 'reader_calls') for r in rs)} |")
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="include r0 trial runs")
    rows = load(ap.parse_args().all)

    summary(rows, "condition")
    for possible, name in (("1", "deal possible"), ("0", "deal impossible")):
        print(f"**{name}**\n")
        summary([r for r in rows if r["deal_possible"] == possible], "condition")

    by = defaultdict(list)
    for r in rows:
        by[(r["condition"], r["scenario"])].append(r)
    print("| run | condition | scenario | deal_possible | outcome | price | correct | "
          "violation | turns | format_errors | reader_calls | note |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        note = r["note"].replace("|", "/")
        print(f"| {r['run']} | {r['condition']} | {r['scenario']} | {r['deal_possible']} | "
              f"{r['outcome'] or '(crash)'} | {r['price']} | {r['correct']} | {r['violation']} | "
              f"{r['turns']} | {r['format_errors']} | {r['reader_calls']} | {note} |")


if __name__ == "__main__":
    main()
