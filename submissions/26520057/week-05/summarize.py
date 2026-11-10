"""Print the REPORT tables from results.csv: per-condition summary and the per-episode table."""
import csv
import re
from collections import defaultdict

CONDS = ["prompt", "server", "prompt_inject", "server_inject"]
rows = list(csv.DictReader(open("results.csv")))
by = defaultdict(list)
for r in rows:
    by[r["condition"]].append(r)


def note_int(r, key):
    m = re.search(rf"{key}=(\d+)", r["note"])
    return int(m.group(1)) if m else 0


print("| condition | episodes | correct | deal / no_deal / open | violation | attempted | refused "
      "| limit refusals | refused→valid same turn | mean turns | mean tool calls |")
print("|---|---|---|---|---|---|---|---|---|---|---|")
for c in CONDS:
    rs = [r for r in by[c] if r["outcome"]]
    if not by[c]:
        continue
    s = lambda k: sum(int(r[k]) for r in rs)  # noqa: E731
    oc = [sum(r["outcome"] == o for r in rs) for o in ("deal", "no_deal", "open")]
    crashed = len(by[c]) - len(rs)
    print(f"| {c} | {len(by[c])}{f' ({crashed} crashed)' if crashed else ''} | {s('correct')}/{len(rs)} "
          f"| {oc[0]} / {oc[1]} / {oc[2]} | {s('violation')} | {s('attempted_violations')} "
          f"| {s('refused_calls')} | {sum(note_int(r, 'limit_refusals') for r in rs)} "
          f"| {sum(note_int(r, 'refused_then_valid') for r in rs)} "
          f"| {s('turns') / len(rs):.1f} | {s('tool_calls') / len(rs):.1f} |")

print()
print("| run | condition | scenario | possible | outcome | price | correct | violation | attempted | refused | turns | tool calls |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|")
for r in rows:
    print("| " + " | ".join(r[k] for k in ("run", "condition", "scenario", "deal_possible", "outcome", "price",
                                          "correct", "violation", "attempted_violations", "refused_calls",
                                          "turns", "tool_calls")) + " |")
