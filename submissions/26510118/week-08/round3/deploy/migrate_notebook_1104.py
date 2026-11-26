"""One-time (11/4 evening): bring the buyer notebook to the rule that a failure counts for a day.

Until 11/4 an outcome in buyer/notebook.json carried no time, and a turn-away ("none of that in
stock") was recorded as a failure ("refused"). buyer_memory now forgets a failure after a day and
counts turn-aways apart. This gives each past outcome its time from the records, drops the
"refused" outcomes, and fills in the last day's turn-aways and agreed deals per shop.

    MARKET_DIR=.market python deploy/migrate_notebook_1104.py            # show what would change
    MARKET_DIR=.market python deploy/migrate_notebook_1104.py --apply    # write it (old file kept as .bak-1104)

Times come from: settled = buyer/purchases.jsonl settled_at; unpaid = records/unpaid_buys.jsonl, or
an order whose receipt was unsigned; error = an order that broke off. A shop's outcomes are matched
from the newest back, each to the newest record of its kind not used yet; one with no record keeps
no time (it counts until newer outcomes push it out). Running it again only fills times still missing.
"""
import argparse
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import buyer_memory as memory  # noqa: E402
import common                  # noqa: E402


def from_records():
    """({shop: {kind: [ts, newest first]}}, {shop: [turn-away ts]}, {shop: [agreed ts]})."""
    ev = defaultdict(lambda: defaultdict(list))
    turned, agreed = defaultdict(list), defaultdict(list)
    for p in common.read_jsonl(memory.folder() / "purchases.jsonl"):
        if p.get("shop") and p.get("settled_at"):
            ev[p["shop"]]["settled"].append(p["settled_at"])
    for u in common.records("unpaid_buys"):
        if u.get("shop"):
            ev[u["shop"]]["unpaid"].append(u["ts"])
    for o in common.records("orders"):
        s, left = o.get("shop"), o.get("left") or ""
        if not s:
            continue
        if o.get("error"):
            ev[s]["error"].append(o["ts"])
        elif "not signed" in left:
            ev[s]["unpaid"].append(o["ts"])
        elif o.get("deal"):
            agreed[s].append(o["ts"])
        elif not o.get("offers") and (not left or "turned us away" in left):
            turned[s].append(o["ts"])
    for kinds in ev.values():
        for k in kinds:
            kinds[k].sort(reverse=True)
    return ev, turned, agreed


def migrate(nb: dict, now: float) -> list:
    ev, turned, agreed = from_records()
    rows = []
    for name, e in nb.items():
        was_locked = len(e.get("recent") or []) >= 3 and all(x != "settled" for x in e["recent"][-3:])
        used, out = defaultdict(int), []
        for w, t in reversed([(w, t) for w, t in zip(e.get("recent") or [], memory.times(e)) if w != "refused"]):
            have = (ev.get(name) or {}).get(w) or []
            if t is None and used[w] < len(have):
                t = int(have[used[w]])
            used[w] += 1
            out.append((w, t))
        out.reverse()
        e["recent"], e["recent_at"] = [w for w, _ in out], [t for _, t in out]
        if not e.get("turned"):
            e["turned"] = [int(t) for t in turned.get(name, []) if now - t < memory.EXPIRE]
        if not e.get("agreed_at"):
            e["agreed_at"] = [int(t) for t in agreed.get(name, []) if now - t < memory.EXPIRE]
        e.setdefault("refused", 0)
        fails = [t for w, t in out if w != "settled"]
        tail = out[-3:]
        unlock = (min(t for _, t in tail) + memory.EXPIRE
                  if len(tail) == 3 and all(w != "settled" and t for w, t in tail) else None)
        rows.append({"shop": name, "was_locked": was_locked, "locked": memory.failed_in_a_row(nb, name, now=now),
                     "turns_away": memory.turns_away(nb, name, now), "turned_agreed": memory.turned_counts(nb, name, now),
                     "untimed": sum(1 for _, t in out if t is None), "failures": len(fails),
                     "unlock_at": unlock if memory.failed_in_a_row(nb, name, now=now) else None})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    now = time.time()
    nb = memory.load()
    rows = migrate(nb, now)
    kst = lambda t: time.strftime("%m/%d %H:%M", time.gmtime(t + 9 * 3600)) if t else "-"
    for r in sorted(rows, key=lambda r: (not r["was_locked"], r["shop"])):
        print(f"{r['shop']:24} locked {str(r['was_locked']):5} -> {str(r['locked']):5}  turns away {str(r['turns_away']):5} "
              f"{r['turned_agreed']}  untimed {r['untimed']}  unlocks {kst(r['unlock_at'])}")
    print(f"shops {len(rows)}: locked before {sum(r['was_locked'] for r in rows)}, after: failed 3 in a row "
          f"{sum(r['locked'] for r in rows)}, turn us away on most tries {sum(r['turns_away'] for r in rows)}, "
          f"usable {sum(not r['locked'] and not r['turns_away'] for r in rows)}; outcomes with no time {sum(r['untimed'] for r in rows)}")
    if a.apply:
        path = memory.folder() / "notebook.json"
        if not path.with_name("notebook.json.bak-1104").exists():
            shutil.copy(path, path.with_name("notebook.json.bak-1104"))
        memory.save(nb)
        print("written:", path)


if __name__ == "__main__":
    main()
