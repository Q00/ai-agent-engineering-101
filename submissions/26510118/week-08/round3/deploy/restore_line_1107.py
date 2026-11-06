"""One-off repair (11/7 02:10, the user's call): until today a line product whose market median fell 3% of list under
the baseline taken when it joined lost a unit of its cap, and at 6% left the line (linewatch.MARKET_CUTS, now off).
In the 12 h to 02:00 it made 71 cuts on 11 products that sold 467 units for 69.4M of margin; 7 of them were left at a
cap of 0-1, and a product at 0 cannot sell its way back up. This puts each product cut so in the last WINDOW back at the
highest cap it had before those cuts, in analysis/line_state.json and analysis/line.json, and logs it in
line_log.jsonl. A product the expected margin no longer carries leaves again at the next linewatch run (EM_EXIT).
Refuses while an analysis pass holds the lock.

    MARKET_DIR=~/market/round3/.market python deploy/restore_line_1107.py [--dry]
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402
import linewatch  # noqa: E402

WINDOW = 24 * 3600


def main() -> int:
    dry, now = "--dry" in sys.argv, time.time()
    folder = linewatch.folder()
    if (folder / "lock").exists():
        print("an analysis pass holds the lock; try again in a minute")
        return 1
    log = [r for r in common.read_jsonl(folder / "line_log.jsonl") if (r.get("at") or 0) >= now - WINDOW]
    target = {}
    for r in log:
        if "under the baseline" in str(r.get("why") or ""):
            target[r["sku"]] = max(target.get(r["sku"], 0), int(r.get("from") or 0))
    state = common.read_json(folder / "line_state.json", {}) or {}
    caps = state.setdefault("caps", {})
    line = common.read_json(folder / "line.json", None)
    back = []
    for sku, cap in sorted(target.items()):
        now_cap = caps.get(sku, 0)
        if cap > now_cap:
            back.append({"at": int(now), "sku": sku, "from": now_cap, "to": cap,
                         "why": "restored: cut for a market drop, a rule off since 11/7 02:10", "by": "repair"})
    print(f"{'would restore' if dry else 'restored'} {len(back)} of {len(target)} products cut for a market drop:")
    for b in back:
        print(f"  {b['sku']}: cap {b['from']} -> {b['to']}")
    if dry or not back:
        return 0
    for b in back:
        caps[b["sku"]] = b["to"]
        (state.get("exit_at") or {}).pop(b["sku"], None)
        if isinstance(line, dict) and line.get("by") == "linewatch":
            line.setdefault("skus", {})[b["sku"]] = b["to"]
    common.write_json(folder / "line_state.json", state)
    if isinstance(line, dict) and line.get("by") == "linewatch":
        common.write_json(folder / "line.json", line)
    with open(folder / "line_log.jsonl", "a", encoding="utf-8") as f:
        for b in back:
            f.write(json.dumps(b, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
