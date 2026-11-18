"""Settlement and payment-refusal lines from the kit console logs (logs/kit-console*.txt), dated, up to the Round 3
close (11/17 23:59) -> logs/kit-settle-extract.txt (week-08 midterm, REPORT.md (3)). The console has only HH:MM:SS:
the date starts from the file name and moves one day at each wrap past midnight.
    python tools/settle_extract.py [OUT]"""
import os
import re
import sys
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = [("kit-console.txt", date(2026, 11, 5)), ("kit-console-1105c.txt", date(2026, 11, 5)),
         ("kit-console-1105-2318.txt", date(2026, 11, 5)), ("kit-console-1108-1148.txt", date(2026, 11, 8)),
         ("kit-console-1108-1304.txt", date(2026, 11, 8)), ("kit-console-1110-1511.txt", date(2026, 11, 10))]
PAT = re.compile(r"\[acp\] (settled|paid)|not paying|checkout refused")
END = datetime(2026, 11, 17, 23, 59, 59)


def main(out=None):
    out = out or os.path.join(HERE, "logs", "kit-settle-extract.txt")
    n = 0
    with open(out, "w", encoding="utf-8") as w:
        w.write("# Settlement and payment-refusal lines from logs/kit-console-*.txt (the kit's console, the same lines\n"
                "# as logs/market-kit.log before its rotation), up to 11/17 23:59. The date in front is inferred: the\n"
                "# console has only HH:MM:SS.\n")
        for name, d in FILES:
            p = os.path.join(HERE, "logs", name)
            if not os.path.exists(p):
                continue
            w.write(f"## {name}\n")
            prev = None
            with open(p, encoding="utf-8", errors="replace") as f:
                for ln in f:
                    m = re.match(r"(\d\d):(\d\d):(\d\d) ", ln)
                    if not m:
                        continue
                    sec = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
                    if prev is not None and sec + 3600 < prev:
                        d += timedelta(days=1)
                    prev = sec
                    if datetime(d.year, d.month, d.day) + timedelta(seconds=sec) > END:
                        continue
                    if PAT.search(ln):
                        w.write(f"{d:%m-%d} {ln.rstrip()}\n")
                        n += 1
    print(f"{n} lines, {os.path.getsize(out):,} bytes -> {out}")
    return n


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
