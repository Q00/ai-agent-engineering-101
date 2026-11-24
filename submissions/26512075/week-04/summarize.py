from __future__ import annotations
import csv


from collections import defaultdict
from pathlib import Path

from typeset import CSV_FIELDS


def summarize(results_path: Path) -> str:
    rows = list(csv.DictReader(results_path.open(encoding="utf-8", newline="")))
    by: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by[row["condition"]].append(row)
    lines = [
        "| condition | correct | deal, no_deal, open | violation | mean turns | format_errors | reader_calls |",
        "|---|---:|---|---:|---:|---:|---:|",
    ]
    for cond in ("free", "tagged", "structured"):
        chunk = by.get(cond, [])
        n = len(chunk)
        if n == 0:
            continue
        correct = sum(int(r["correct"]) for r in chunk)
        viol = sum(int(r["violation"]) for r in chunk)
        ferr = sum(int(r["format_errors"]) for r in chunk)
        rcall = sum(int(r["reader_calls"]) for r in chunk)
        turns = sum(int(r["turns"]) for r in chunk) / n
        deals = sum(r["outcome"] == "deal" for r in chunk)
        nodeal = sum(r["outcome"] == "no_deal" for r in chunk)
        opened = sum(r["outcome"] == "open" for r in chunk)
        lines.append(
            f"| {cond} | {correct} / {n} | {deals}, {nodeal}, {opened} | {viol} | {turns:.1f} | {ferr} | {rcall} |"
        )
    return "\n".join(lines)


def markdown_episodes(results_path: Path) -> str:
    rows = list(csv.DictReader(results_path.open(encoding="utf-8", newline="")))
    header = "| " + " | ".join(CSV_FIELDS) + " |"
    sep = "|" + "|".join(["---"] * len(CSV_FIELDS)) + "|"
    lines = [header, sep]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(k, "")) for k in CSV_FIELDS) + " |")
    return "\n".join(lines)


