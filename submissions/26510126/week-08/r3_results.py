"""REPORT section 3 numbers, quoted from the logs: settled deals at our shop and by our buyer, refusals, and the
board's Players rank at the time of the run.

    python r3_results.py             # prints Markdown for REPORT.md section 3
    python r3_results.py --no-board  # logs only (offline)

Read-only: logs/market-kit.log (and .log.1 after the kit rotates it), .market/payments.jsonl, the market's /api/board.
"""
import argparse
import collections
import json
import re
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = [HERE / "logs" / "market-kit.log.1", HERE / "logs" / "market-kit.log"]
US = "thisjonghoon"
PATTERNS = {
    "shop settled": re.compile(r"\[acp\] settled "),
    "buyer paid": re.compile(r"\[acp(-tools)?\] paid "),
    "shop settle refused": re.compile(r"\[acp\] settle refused"),
    "shop checkout refused": re.compile(r"\[acp\] checkout refused"),
    "buyer not paying (accept policy)": re.compile(r"\[acp(-tools)?\] not paying: "),
    "buyer payment failed": re.compile(r"\[acp(-tools)?\] payment (not completed|failed)"),
    "supply": re.compile(r"\[supply\]"),
    "forum": re.compile(r"\[forum\]"),
}


def lines():
    """The whole round: the archived kit logs (archive_kit_log.sh cuts the live log and gzips it), then the live ones."""
    import gzip
    firsts = set()
    for p in sorted((HERE / "logs" / "archive").glob("market-kit-until-*.log.gz")):
        with gzip.open(p, "rt", encoding="utf-8", errors="replace") as f:
            for i, l in enumerate(f):
                if i == 0:
                    firsts.add(l.rstrip("\n"))
                yield l.rstrip("\n")
    for p in LOGS:
        if p.exists():
            # 11/18 fresh-reader review: .log.1 can be the same text as the last archive, and only settles were deduplicated,
            # so refusals, supply and forum lines counted twice; skip .log.1 when an archive starts with its first line
            if p.name.endswith(".log.1"):
                with p.open(encoding="utf-8", errors="replace") as f:
                    if f.readline().rstrip("\n") in firsts:
                        continue
            yield from p.read_text(encoding="utf-8", errors="replace").splitlines()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-board", action="store_true")
    a = ap.parse_args()
    count, example = collections.Counter(), {}
    reasons = collections.Counter()
    seen = set()            # the archives and .log.1 overlap: a settle counts once per ledger entry (11/9: 13,036 lines, 11,542 entries)
    for line in lines():
        for k, rx in PATTERNS.items():
            if rx.search(line):
                ref = re.search(r"ledger #(\d+)", line) if k in ("shop settled", "buyer paid") else None
                if ref:
                    if (k, ref.group(1)) in seen:
                        continue
                    seen.add((k, ref.group(1)))
                count[k] += 1
                example.setdefault(k, line.strip()[:220])
                if k in ("shop settle refused", "shop checkout refused", "buyer not paying (accept policy)", "buyer payment failed"):
                    reasons[(k, re.sub(r"\d+", "N", re.sub(r"\b(u|o)-[0-9a-f]+", r"\1-…", line.split("]", 2)[-1].strip()))[:120])] += 1
    print(f"Counted {time.strftime('%Y-%m-%d %H:%M')} from `logs/archive/market-kit-until-*.log.gz` and `logs/market-kit.log`.\n")
    print("| Line in the kit log | Count | First example |\n|---|---|---|")
    for k in PATTERNS:
        print(f"| {k} | {count[k]} | `{example.get(k, '').replace('|', '/')}` |")
    if reasons:
        print("\nRefusal reasons (numbers masked as N):\n")
        for (k, r), n in reasons.most_common(10):
            print(f"- {k}, {n}x: `{r.replace('|', '/')}`")
    pays = []
    try:
        pays = [json.loads(l) for l in (HERE / ".market" / "payments.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, ValueError):
        pass
    if pays:
        why = collections.Counter((p.get("reason") or "")[:100] for p in pays if not p.get("paid"))
        print(f"\n`payments.jsonl`: {len(pays)} agreed deals, {sum(bool(p.get('paid')) for p in pays)} paid; "
              f"not paid because: {dict(why.most_common(5))}")
    if a.no_board:
        return
    import httpx
    b = httpx.get("https://market.ourolabs.ai/api/board", timeout=20).json()
    print(f"\nBoard at {time.strftime('%Y-%m-%d %H:%M')} (round {b.get('current')}):\n")
    for kind in ("players", "shops", "buyers"):
        rows = b.get(kind) or []
        # rank by the board's own score, not the order the API lists them in (11/9: "#13 of 84" was the list order)
        rows = sorted(rows, key=lambda r: -((r.get("total") if kind == "players" else r.get("all")) or 0))
        if kind == "players":
            st = sorted([r for r in rows if r.get("student")], key=lambda r: -(r.get("student_total") or 0))
            j = next((j for j, r in enumerate(st) if r.get("player") == US), None)
            if j is not None:
                print(f"- graded: #{j + 1} of {len(st)} students (student_pct {st[j].get('student_pct')}, "
                      f"student_total {st[j].get('student_total')})")
        i = next((i for i, r in enumerate(rows) if US in (r.get("name"), r.get("player"))
                  or (r.get("owner") or {}).get("login") == US), None)
        if i is not None:
            nums = {k: v for k, v in rows[i].items() if isinstance(v, (int, float)) and not isinstance(v, bool) and k != "joined"}
            print(f"- {kind}: #{i + 1} of {len(rows)} (top {100 * (i + 1) / len(rows):.0f}%) {json.dumps(nums, ensure_ascii=False)}")
        elif rows:
            print(f"- {kind}: not listed among {len(rows)}")


if __name__ == "__main__":
    main()
