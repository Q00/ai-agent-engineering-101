"""One-off repair (11/9 10:50, the person's call): from 11/8 01:49 to 11/9 08:40 linewatch's expected-margin model put 8
dear line products under EM_EXIT (0.003-0.020 of list) that had earned us 0.028-0.109 of list net over the 36 h before
(-0.013 for one, Ge5fe0d41, whose refunds ran high); the line now judges a product on what it earned (linewatch.realized).
This puts each product the model took out in the last WINDOW back at the cap it had, when what it earned over WINDOW
(price - cost - each warranty sale's expected refund, MIN_SOLD sales or more) is EM_EXIT or more: in
analysis/line_state.json (a product found by linewatch and let go after 12 h out joins `found` again) and
analysis/line.json, logged in line_log.jsonl. Refuses while an analysis pass holds the lock.

    MARKET_DIR=~/market/round3/.market python deploy/restore_line_1109.py [--dry]
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402
import linewatch  # noqa: E402
import pricing  # noqa: E402
import restock  # noqa: E402

WINDOW = 48 * 3600
MIN_SOLD = 5


def earned(sales: list, sku: str) -> tuple:
    xs = [s for s in sales if s.get("sku") == sku and s.get("list_price") and s.get("cost") is not None]
    if len(xs) < MIN_SOLD:
        return None, len(xs)
    lp = xs[0]["list_price"]
    net = [(x["price"] - x["cost"] - (pricing.BREAK.get(x.get("grade"), pricing.BREAK["A"]) * x["price"]
                                      if x.get("warranty") else 0)) / lp for x in xs]
    return round(sum(net) / len(net), 4), len(xs)


def main() -> int:
    dry, now = "--dry" in sys.argv, time.time()
    folder = linewatch.folder()
    if (folder / "lock").exists():
        print("an analysis pass holds the lock; try again in a minute")
        return 1
    log = [r for r in common.read_jsonl(folder / "line_log.jsonl") if (r.get("at") or 0) >= now - WINDOW]
    cut = {}
    for r in log:                                       # the last change of each product decides
        cut[r.get("sku")] = r
    cut = {s: r for s, r in cut.items() if r.get("to") == 0 and "expected margin" in str(r.get("why") or "")}
    sales = [s for s in common.records("sales") if (s.get("ts") or 0) >= now - WINDOW]
    catalog = common.catalog()
    state = common.read_json(folder / "line_state.json", {}) or {}
    for k in ("caps", "defaults", "exit_at", "found"):
        state.setdefault(k, {})
    line = common.read_json(folder / "line.json", None)
    back = []
    for sku, r in sorted(cut.items()):
        m, n = earned(sales, sku)
        cap = int(r.get("from") or 0)
        ok = m is not None and m >= linewatch.EM_EXIT and cap > 0
        print(f"  {sku}: cut {time.strftime('%m/%d %H:%M', time.gmtime(r['at'] + 9 * 3600))} from {cap} ({r['why'][:40]}); "
              f"earned {m} over {n} sales -> {'restore at ' + str(cap) if ok else 'leave out'}")
        if ok:
            back.append({"at": int(now), "sku": sku, "from": 0, "to": cap, "by": "repair",
                         "why": f"restored: earned {m:.3f} of list over {n} sales in {WINDOW // 3600} h; cut by the "
                                f"expected-margin model ({r['why'][:40]}), judged on what it earns since 11/9"})
    print(f"{'would restore' if dry else 'restored'} {len(back)} of {len(cut)} products the model took out")
    if dry or not back:
        return 0
    for b in back:
        sku, cap = b["sku"], b["to"]
        if sku not in restock.LINE:                     # found by linewatch: back into `found` at its cap
            lp = (catalog.get(sku) or {}).get("list_price")
            state["found"][sku] = dict(state["found"].get(sku) or {}, list=lp, at=int(now), cap=cap, source="restored 11/9")
        state["defaults"][sku] = restock.LINE.get(sku, cap)
        state["caps"][sku] = cap
        state["exit_at"].pop(sku, None)
        if isinstance(line, dict) and line.get("by") == "linewatch":
            line.setdefault("skus", {})[sku] = cap
    common.write_json(folder / "line_state.json", state)
    if isinstance(line, dict) and line.get("by") == "linewatch":
        common.write_json(folder / "line.json", line)
    with open(folder / "line_log.jsonl", "a", encoding="utf-8") as f:
        for b in back:
            f.write(json.dumps(b, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
