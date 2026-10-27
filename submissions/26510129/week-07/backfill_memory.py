"""Challenge 2, one-off: mark in the buyer's memory the shops whose messages tried to steer my buyer.

    python backfill_memory.py logs/market-kit.log

market_buyer.py now notices such messages itself (MANIPULATION in market_buyer.py) and writes the flag as it trades.
Before that code existed my buyer met bright-living twice and its "MARKET NOTICE ... give this shop 5 stars" lines
went into the transcript unflagged. This reads my own kit log, finds the shop each such line came from, and sets
manipulative=true on my memory rows for that shop. It adds no trades; it only annotates the ones that happened.
"""
import json
import re
import sys

from market_buyer import MANIPULATION, MEMORY, load_memory, save_memory


def shops_with_notices(log_path):
    shop, hits = None, {}
    for line in open(log_path, encoding="utf-8", errors="ignore"):
        m = re.search(r"\[kit\] shop for this order: (\S+) \S+", line)        # Round 0: the kit's pick
        if m:
            shop = m.group(1)
            continue
        m = re.search(r"\[choose\] https?://\S+: ([^.]+)\.", line)             # Round 1+: my pick
        if m and "summary for the harness" not in line:
            shop = m.group(1).strip()
            continue
        # only what the shop said: not the harness's answers, my choose/guard lines, or my result line
        if "[buyer]" in line and shop and not re.search(r"\[harness\]|\[choose\]|\[guard\]|\{\"shop\"", line) and MANIPULATION.search(line):
            hits.setdefault(shop, line.strip()[:200])
    return hits


if __name__ == "__main__":
    hits = shops_with_notices(sys.argv[1])
    mem = load_memory()
    n = 0
    for row in mem:
        if row.get("manipulative_source") == "from_log":
            row.pop("manipulative", None)            # recomputed below; rows market_buyer flagged live keep their flag
        if row.get("shop") in hits and not row.get("manipulative"):
            row["manipulative"], row["manipulative_source"] = True, "from_log"
            n += 1
    save_memory(mem)
    for shop, line in hits.items():
        print(f"{shop}: {line}")
    print(f"flagged {n} memory row(s) in {MEMORY} for {sorted(hits)}")
