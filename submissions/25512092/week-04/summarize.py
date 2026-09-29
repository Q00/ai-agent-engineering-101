"""results.csv 를 읽어 REPORT.md 에 붙일 표를 만든다.

    python summarize.py                    # results.csv
    python summarize.py results_liar.csv   # 페르소나 run
"""

import csv
import sys
from collections import defaultdict

path = sys.argv[1] if len(sys.argv) > 1 else "results.csv"
rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))

by = defaultdict(list)
for r in rows:
    by[r["condition"]].append(r)

print(f"## 조건별 요약 ({path})\n")
print("| condition | correct / n | deal, no_deal, open | violation | mean turns | format errors | reader calls |")
print("|---|---|---|---|---|---|---|")
for cond in ["free", "tagged", "structured"]:
    rs = by.get(cond)
    if not rs:
        continue
    n = len(rs)
    correct = sum(int(r["correct"]) for r in rs)
    viol = sum(int(r["violation"]) for r in rs)
    fe = sum(int(r["format_errors"]) for r in rs)
    rc = sum(int(r["reader_calls"]) for r in rs)
    turns = sum(int(r["turns"]) for r in rs) / n
    oc = {k: sum(1 for r in rs if r["outcome"] == k)
          for k in ("deal", "no_deal", "open")}
    print(f"| {cond} | {correct} / {n} | {oc['deal']}, {oc['no_deal']}, {oc['open']} "
          f"| {viol} | {turns:.1f} | {fe} | {rc} |")

print("\n## 에피소드 전체\n")
head = ["run", "condition", "scenario", "deal_possible", "outcome", "price",
        "correct", "violation", "turns", "format_errors", "reader_calls", "note"]
print("| " + " | ".join(head) + " |")
print("|" + "---|" * len(head))
for r in rows:
    print("| " + " | ".join(str(r.get(k, "")).replace("|", "\\|") for k in head) + " |")