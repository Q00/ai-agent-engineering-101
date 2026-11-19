"""Print the REPORT results tables from results.csv: one row per condition, then per episode."""

import csv
import re
from collections import defaultdict
from pathlib import Path

rows = list(csv.DictReader((Path(__file__).parent / "results.csv").open(encoding="utf-8")))
by = defaultdict(list)
for r in rows:
    by[r["condition"]].append(r)

print("| condition | episodes | crashed | correct | violations | attempted violations "
      "| refused calls | refusal then valid move | mean turns | mean tool calls |")
print("|---|---|---|---|---|---|---|---|---|---|")
for cond in ("prompt", "server", "prompt_inject", "server_inject"):
    eps = by.get(cond)
    if not eps:
        continue
    ok = [r for r in eps if r["outcome"]]
    total = lambda k: sum(int(r[k]) for r in ok)
    recovered = sum(int(m.group(1)) for r in ok
                    if (m := re.search(r"refusal_then_valid_move=(\d+)", r["note"])))
    mean = lambda k: f"{total(k) / len(ok):.1f}" if ok else "-"
    print(f"| {cond} | {len(eps)} | {len(eps) - len(ok)} | {total('correct')}/{len(ok)} | {total('violation')} "
          f"| {total('attempted_violations')} | {total('refused_calls')} | {recovered} "
          f"| {mean('turns')} | {mean('tool_calls')} |")

print()
cols = ["run", "condition", "scenario", "deal_possible", "outcome", "price", "correct", "violation",
        "attempted_violations", "refused_calls", "turns", "tool_calls"]
print("| " + " | ".join(cols) + " |")
print("|" + "---|" * len(cols))
for r in rows:
    print("| " + " | ".join(r[c] or "-" for c in cols) + " |")
