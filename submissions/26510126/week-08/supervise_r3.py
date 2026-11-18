"""The supervisor's way to change a shop number: python supervise_r3.py ask_frac=0.7 [key=value ...] -- "why"

Until 11/9 the supervisor edited strategy_r3.json by hand and logged a row, so its own changes escaped the 2 h verdict
and the fast guard every other number change gets. This writes the new values and queues them the same way the team's
changes are queued (meta_r3.number_record), with source "supervisor" in the ledger.
"""
import json
import sys
import time

import meta_r3


def main(argv):
    if "--" not in argv:
        sys.exit(__doc__)
    i = argv.index("--")
    pairs, why = argv[:i], " ".join(argv[i + 1:])
    s = meta_r3.read_json(meta_r3.STRATEGY, {})
    busy = meta_r3.shop_busy()
    if busy:   # the supervisor overlapped the slot twice on 11/9 (13:17, 13:40) and left both verdicts MIXED
        print(f"warning: the shop change slot is busy ({busy}); this change will be judged MIXED. Go on only for safety.")
    changed = {}
    for pair in pairs:
        k, v = pair.split("=", 1)
        if k not in meta_r3.BOUNDS:
            sys.exit(f"{k} is not a shop number")
        lo, hi, _ = meta_r3.BOUNDS[k]
        v = min(max(float(v), lo), hi)
        changed[k] = (s.get(k), v)
        s[k] = v
    meta_r3.write_json(meta_r3.STRATEGY, s)
    meta_r3.number_record(changed, f"supervisor: {why}")
    with meta_r3.CODE_CHANGES.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.time(), "commit": "-", "source": "supervisor",
                            "change": ", ".join(f"{k} {a} -> {b}" for k, (a, b) in changed.items()),
                            "evidence": why, "metric": "board_pts_h"}, ensure_ascii=False) + "\n")
    print(changed)


if __name__ == "__main__":
    main(sys.argv[1:])
