"""One-off repair (11/9 11:30, the person's call): from 11/8 01:49 to 11/9 10:43 linewatch's expected-margin model put
dear line products under EM_EXIT (then 0.02 of list). Restock's limit is lines (10 a run), not money, and a line earned
100.6k net on dear goods, 9.8k on other goods and 1.2k on food over 48 h, so a dear product earning 0.01-0.02 of list
still beats the food that took its lines; EM_EXIT is now 0.01 and a product is judged on what it earned
(linewatch.margins). This puts each product the model took out in the last WINDOW back at the cap it had, when
linewatch's own judgment now gives it EM_EXIT or more (so the next pass keeps it): in analysis/line_state.json (a
product found by linewatch and let go after 12 h out joins `found` again) and analysis/line.json, logged in
line_log.jsonl. Refuses while an analysis pass holds the lock. (A first draft, 11/9 10:50, judged on 48 h with 5 sales
and was not run: a first count of these products' margins, 0.028-0.109, had left out most refunds; they earned
0.011-0.029.)

    MARKET_DIR=~/market/round3/.market python deploy/restore_line_1109.py [--dry]
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import breakage  # noqa: E402
import common  # noqa: E402
import linewatch  # noqa: E402
import pricing  # noqa: E402
import restock  # noqa: E402

WINDOW = 48 * 3600


def main() -> int:
    dry, now = "--dry" in sys.argv, time.time()
    folder = linewatch.folder()
    if (folder / "lock").exists():
        print("an analysis pass holds the lock; try again in a minute")
        return 1
    pricing.BREAK.update(breakage.rates(common.read_json(breakage.path(), None), "market", now))
    summary = common.read_json(common.MARKET_DIR / "market_summary.json", {}) or {}
    inv = common.market_file("inventory.json", {}) or {}
    surge = {s.get("sku"): float(s.get("factor") or 1.0) for s in inv.get("surge") or [] if isinstance(s, dict)}
    margin, real, cal = linewatch.margins(now, summary, surge)
    print(f"products judged on what they earned: {len(real)}; the model's error {cal:+.4f}")
    log = [r for r in common.read_jsonl(folder / "line_log.jsonl") if (r.get("at") or 0) >= now - WINDOW]
    last = {}
    for r in log:                                       # the last change of each product decides
        last[r.get("sku")] = r
    cut = {s: r for s, r in last.items() if r.get("to") == 0 and "expected margin" in str(r.get("why") or "")}
    catalog = common.catalog()
    state = common.read_json(folder / "line_state.json", {}) or {}
    for k in ("caps", "defaults", "exit_at", "found"):
        state.setdefault(k, {})
    line = common.read_json(folder / "line.json", None)
    back = []
    for sku, r in sorted(cut.items()):
        m, cap = margin(sku), int(r.get("from") or 0)
        how = f"earned {m:.4f} over {real[sku][1]} sales in 36 h" if sku in real else f"model {m}"
        ok = m is not None and m >= linewatch.EM_EXIT and cap > 0
        print(f"  {sku}: cut {time.strftime('%m/%d %H:%M', time.gmtime(r['at'] + 9 * 3600))} from {cap}; {how} -> "
              f"{'restore at ' + str(cap) if ok else 'leave out'}")
        if ok:
            back.append({"at": int(now), "sku": sku, "from": 0, "to": cap, "by": "repair",
                         "why": f"restored: {how} (EM_EXIT now {linewatch.EM_EXIT}); cut by the expected-margin model "
                                f"({str(r.get('why'))[:40]})"})
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
