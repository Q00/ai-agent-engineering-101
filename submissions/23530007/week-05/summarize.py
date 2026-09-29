"""Print the REPORT result tables from results.csv (markdown). Re-run after every new batch.

  python summarize.py
"""
import csv
from pathlib import Path

ORDER = ("prompt", "server", "prompt_inject", "server_inject")
rows = list(csv.DictReader((Path(__file__).resolve().parent / "results.csv").open(encoding="utf-8")))
ok = [r for r in rows if r["outcome"]]          # crashed rows have no outcome

print("| condition | episodes | correct | deal, no_deal, open | violation | attempted | refused "
      "| mean turns | mean tool_calls |")
print("|---|---|---|---|---|---|---|---|---|")
for c in ORDER:
    rs = [r for r in ok if r["condition"] == c]
    if not rs:
        continue
    n = len(rs)
    tot = lambda k: sum(int(r[k]) for r in rs)
    cnt = lambda o: sum(r["outcome"] == o for r in rs)
    print(f"| `{c}` | {n} | {tot('correct')}/{n} | {cnt('deal')}, {cnt('no_deal')}, {cnt('open')} "
          f"| {tot('violation')} | {tot('attempted_violations')} | {tot('refused_calls')} "
          f"| {tot('turns') / n:.1f} | {tot('tool_calls') / n:.1f} |")
crashed = len(rows) - len(ok)
print(f"\ncrashed rows: {crashed}")

print("\n| run | condition | scenario | deal_possible | outcome | price | correct | violation "
      "| attempted | refused | turns | tool_calls |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|")
for r in ok:
    print(f"| {r['run']} | {r['condition']} | {r['scenario']} | {r['deal_possible']} | {r['outcome']} "
          f"| {r['price'] or '—'} | {r['correct']} | {r['violation']} | {r['attempted_violations']} "
          f"| {r['refused_calls']} | {r['turns']} | {r['tool_calls']} |")
