"""Print the REPORT.md part-2 tables from results.csv. Run: python summarize.py"""

import csv
from pathlib import Path

import negotiation as ng

HERE = Path(__file__).parent


def main():
    with (HERE / "results.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    print("| condition | episodes | crashed | correct | violations | deal / no_deal / open "
          "| mean turns | format errors | reader calls |")
    print("|---|---|---|---|---|---|---|---|---|")
    for c in ng.CONDITIONS:
        rs = [r for r in rows if r["condition"] == c]
        ok = [r for r in rs if r["outcome"]]
        n = len(ok)
        out = {o: sum(r["outcome"] == o for r in ok) for o in ("deal", "no_deal", "open")}
        s = lambda k: sum(int(r[k]) for r in ok)
        mean_turns = f"{s('turns') / n:.1f}" if n else "-"
        print(f"| `{c}` | {len(rs)} | {len(rs) - n} | {s('correct')}/{n} | {s('violation')} | "
              f"{out['deal']} / {out['no_deal']} / {out['open']} | {mean_turns} | "
              f"{s('format_errors')} | {s('reader_calls')} |")

    print()
    print("| " + " | ".join(rows[0].keys()) + " |" if rows else "")
    print("|" + "---|" * len(rows[0]) if rows else "")
    for r in rows:
        print("| " + " | ".join(v.replace("|", "\\|") for v in r.values()) + " |")


if __name__ == "__main__":
    main()
