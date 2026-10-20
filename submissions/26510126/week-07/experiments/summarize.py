"""One row per condition from experiments/results.jsonl, as a Markdown table.

    python experiments/summarize.py [results.jsonl]
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "results.jsonl"
rows = defaultdict(list)
for line in open(path):
    r = json.loads(line)
    rows[r["condition"]].append(r)

short = lambda s: (s or "none").replace("TASK_STATE_", "").lower()
print("| condition | runs | completed / rejected / canceled | deal prices | harness offered over budget (runs) "
      "| sent over budget (runs) | guard fires | misread asks | offers per run |")
print("|---|---|---|---|---|---|---|---|---|")
for name, rs in rows.items():
    finals = [short(r["final"]) for r in rs]
    deals = sorted(int(r["deal"]) for r in rs if r["deal"] is not None)
    misreads = [r["misreads"] for r in rs if r["misreads"] is not None]
    print(f"| {name} | {len(rs)} "
          f"| {finals.count('completed')} / {finals.count('rejected')} / {finals.count('canceled')} "
          f"| {', '.join(map(str, deals)) or '-'} "
          f"| {sum(r['harness_over_budget'] for r in rs)} "
          f"| {sum(r['sent_over_budget'] for r in rs)} "
          f"| {sum(r['guards'] for r in rs)} "
          f"| {sum(misreads) if misreads else '-'} "
          f"| {sum(len(r['offers']) for r in rs) / len(rs):.1f} |")
