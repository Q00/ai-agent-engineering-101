"""Print the REPORT result tables from results.csv (markdown). Re-run after every new batch.

  python summarize.py
"""
import csv
import re
from pathlib import Path

ORDER = ("prompt", "server", "prompt_inject", "server_inject")
rows = list(csv.DictReader((Path(__file__).resolve().parent / "results.csv").open(encoding="utf-8")))
ok = [r for r in rows if r["outcome"]]          # crashed rows have no outcome
for r in ok:
    r["model"] = re.search(r"model=(\S+)", r["note"]).group(1)
models = sorted({r["model"] for r in ok})

print("| condition | model | episodes | correct | deal, no_deal, open | violation | attempted | refused "
      "| mean turns | mean tool_calls |")
print("|---|---|---|---|---|---|---|---|---|---|")
for c, m in [(c, m) for c in ORDER for m in models]:
    rs = [r for r in ok if r["condition"] == c and r["model"] == m]
    if not rs:
        continue
    n = len(rs)
    tot = lambda k: sum(int(r[k]) for r in rs)
    cnt = lambda o: sum(r["outcome"] == o for r in rs)
    print(f"| `{c}` | {m} | {n} | {tot('correct')}/{n} | {cnt('deal')}, {cnt('no_deal')}, {cnt('open')} "
          f"| {tot('violation')} | {tot('attempted_violations')} | {tot('refused_calls')} "
          f"| {tot('turns') / n:.1f} | {tot('tool_calls') / n:.1f} |")
crashed = len(rows) - len(ok)
print(f"\ncrashed rows kept as a record: {crashed}")

print("\n| run | condition | model | scenario | deal_possible | outcome | price | correct | violation "
      "| attempted | refused | turns | tool_calls |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for r in sorted(ok, key=lambda r: (ORDER.index(r["condition"]), r["model"], int(r["run"]), int(r["scenario"]))):
    print(f"| {r['run']} | {r['condition']} | {r['model']} | {r['scenario']} | {r['deal_possible']} | {r['outcome']} "
          f"| {r['price'] or '—'} | {r['correct']} | {r['violation']} | {r['attempted_violations']} "
          f"| {r['refused_calls']} | {r['turns']} | {r['tool_calls']} |")
