"""Aggregate results.csv into the markdown tables REPORT.md needs.

Usage: python summarize.py

Per condition: episodes, correct, violations, attempted violations, refused
calls, deal / no_deal / open, mean turns, mean tool calls, and how many
refusals were followed by an accepted move in the same host run (from the
note). Then every episode row, in run order. Crashed episodes (blank
outcome) are listed but excluded from the means.
"""
import csv
import re
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORDER = ("prompt_inject", "server_inject", "prompt", "server")


def load():
    with (HERE / "results.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def note_int(note: str, key: str) -> int:
    m = re.search(rf"{key}=(\d+)", note)
    return int(m.group(1)) if m else 0


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
        for k in ("correct", "violation", "attempted_violations", "refused_calls", "turns", "tool_calls"):
            a[k] += int(r[k])
        a["refused_then_valid"] += note_int(r["note"], "refused_then_valid")
        a["passes"] += note_int(r["note"], "passes")
        a["notice"] += note_int(r["note"], "buyer_texts_mentioning_notice")
    print("| condition | episodes | correct | violations | attempted | refused | refused then valid | deal / no_deal / open | mean turns | mean tool calls | passes |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for c in ORDER:
        a = agg[c]
        if not a["episodes"]:
            continue
        crashed = f" ({a['crashed']} crashed)" if a["crashed"] else ""
        d = a["done"] or 1
        print(f"| {c} | {a['episodes']}{crashed} | {a['correct']}/{a['done']} | {a['violation']} | "
              f"{a['attempted_violations']} | {a['refused_calls']} | {a['refused_then_valid']} | "
              f"{a['deal']} / {a['no_deal']} / {a['open']} | {a['turns'] / d:.1f} | {a['tool_calls'] / d:.1f} | {a['passes']} |")


def per_episode(rows):
    print("\n| run | condition | scenario | deal_possible | outcome | price | correct | violation | attempted | refused | turns | tool_calls | note |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: (ORDER.index(r["condition"]), int(r["run"]), r["scenario"])):
        note = r["note"].replace("|", "/")
        print(f"| {r['run']} | {r['condition']} | {r['scenario']} | {r['deal_possible']} | {r['outcome']} | "
              f"{r['price']} | {r['correct']} | {r['violation']} | {r['attempted_violations']} | "
              f"{r['refused_calls']} | {r['turns']} | {r['tool_calls']} | {note} |")


if __name__ == "__main__":
    rows = load()
    per_condition(rows)
    per_episode(rows)
