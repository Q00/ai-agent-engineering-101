"""Dump the Round 3 ledger's settle and result entries to reports/cache/ledger_r3.jsonl (resumable).

    python tools/ledger_dump.py

The ledger API has no filters, so this reads it 500 entries at a time from the first Round 3 entry. Only
settle and result entries are kept, without hashes. A second run continues after the last seq in the file.
"""
import json
import os

import httpx

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "reports", "cache", "ledger_r3.jsonl")
MARKET = "https://market.ourolabs.ai"


def page(c, after):
    for _ in range(5):
        try:
            return c.get(f"{MARKET}/api/ledger", params={"after": after}).json()
        except Exception:
            continue
    raise RuntimeError(f"ledger after {after} failed 5 times")


def round3_start(c):
    """Binary search for the first seq whose entry is in Round 3."""
    top = page(c, -1)[-1]["seq"]
    lo, hi = 0, top
    while hi - lo > 500:
        mid = (lo + hi) // 2
        p = page(c, mid)
        if p and p[0].get("round", 0) >= 3:
            hi = mid
        else:
            lo = mid
    return lo


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    c = httpx.Client(timeout=30)
    after = None
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            for line in f:
                after = json.loads(line)["seq"]
    if after is None:
        after = round3_start(c)
        print(f"round 3 starts after seq {after}", flush=True)
    n = 0
    with open(OUT, "a", encoding="utf-8") as f:
        while True:
            p = page(c, after)
            if not p:
                break
            for e in p:
                if e["kind"] in ("settle", "result") and e.get("round") == 3:
                    f.write(json.dumps({"seq": e["seq"], "ts": e["ts"], "kind": e["kind"], "data": e["data"]},
                                       ensure_ascii=False) + "\n")
                    n += 1
            after = p[-1]["seq"]
            if len(p) < 500:
                break
            if n and n % 20000 < 300:
                print(f"seq {after}, {n} kept", flush=True)
    print(f"done at seq {after}, {n} entries kept -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
