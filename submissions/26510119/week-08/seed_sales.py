"""Fill sales_log.jsonl from the shop lines of earlier market-kit logs (Round 0-2), once, before Round 3.

    python seed_sales.py ../week-07/logs/market-kit.log logs/2026-10-27-class-kit-v3-round2.log

A negotiation is keyed by the 8-character task id printed in the log, so overlapping logs count once.
"""
import json
import re
import sys

from shop_memory import SALES

LINE = re.compile(r"\[seller\] \[([0-9a-f]{8})\] (buyer: '(.*)'|ask (\d+)|accept (\d+)|reject)")


def main(paths):
    neg = {}   # task -> {"item", "ask", "offers", "result", "price", "source"}
    for path in paths:
        for line in open(path, encoding="utf-8", errors="ignore"):
            m = LINE.search(line)
            if not m:
                continue
            tid, said, ask, accept = m[1], m[3], m[4], m[5]
            n = neg.setdefault(tid, {"item": None, "ask": None, "offers": [], "result": None, "price": None,
                                     "source": path})
            if said is not None:
                item = re.search(r"buy an? (\w+)", said)
                if item:
                    n["item"] = n["item"] or item[1]
                elif said.strip().isdigit() and n["result"] is None:
                    n["offers"].append(int(said))
            elif ask:
                n["ask"] = n["ask"] or int(ask)
            elif accept:
                n["result"], n["price"] = "deal", int(accept)
            elif n["result"] is None:
                n["result"] = "reject"
    rows = []
    for tid, n in neg.items():
        if not (n["item"] and n["ask"]):
            continue
        rows.append({"time": None, "seeded_from": n["source"], "kind": "open", "task": tid, "item": n["item"],
                     "ask": n["ask"]})
        if n["result"]:
            rows.append({"time": None, "seeded_from": n["source"], "kind": "end", "task": tid, "item": n["item"],
                         "result": n["result"], "price": n["price"], "offers": n["offers"]})
    with open(SALES, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    opens = sum(r["kind"] == "open" for r in rows)
    deals = sum(r["kind"] == "end" and r["result"] == "deal" for r in rows)
    print(f"seeded {opens} negotiations ({deals} deals) into {SALES.name}")


if __name__ == "__main__":
    main(sys.argv[1:])
