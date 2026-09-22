"""results.csv를 조건별 한 줄로 접는다. REPORT.md의 결과표는 이 출력을 옮긴 것이다.

사용법: python3 summarize.py [results.csv]
"""
import csv
import sys
from pathlib import Path

CONDITIONS = ("free", "tagged", "structured")
OUTCOMES = ("deal", "no_deal", "open")


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("results.csv")
    if not path.is_file():
        print(f"{path} not found; run the episodes first")
        return 1
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    print(f"{'condition':11} {'episodes':>8} {'correct':>8} {'violation':>9} "
          f"{'mean turns':>10} {'fmt err':>8} {'reader':>7}  outcomes (deal/no_deal/open/crashed)")
    for c in CONDITIONS:
        rs = [r for r in rows if r["condition"] == c]
        live = [r for r in rs if r["outcome"]]
        crashed = len(rs) - len(live)
        counts = {o: sum(1 for r in live if r["outcome"] == o) for o in OUTCOMES}
        turns = [int(r["turns"]) for r in live if r["turns"]]
        print(f"{c:11} {len(rs):>8} {sum(int(r['correct'] or 0) for r in live):>8} "
              f"{sum(int(r['violation'] or 0) for r in live):>9} "
              f"{(sum(turns) / len(turns) if turns else 0):>10.1f} "
              f"{sum(int(r['format_errors'] or 0) for r in live):>8} "
              f"{sum(int(r['reader_calls'] or 0) for r in live):>7}  "
              f"{counts['deal']}/{counts['no_deal']}/{counts['open']}/{crashed}")

    print("\nper-episode rows for REPORT.md part 2:")
    cols = ["run", "condition", "scenario", "deal_possible", "outcome", "price", "correct",
            "violation", "turns", "format_errors", "reader_calls", "note"]
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for r in rows:
        print("| " + " | ".join((r[c] or "") for c in cols) + " |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
