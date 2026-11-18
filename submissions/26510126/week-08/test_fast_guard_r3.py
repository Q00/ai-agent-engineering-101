"""python test_fast_guard_r3.py: the fast guard reverts a number only when both our value and count shares fall."""
import json
import tempfile
import time
from pathlib import Path

import meta_r3
import metrics_r3

d = Path(tempfile.mkdtemp())
meta_r3.NUMBER_TRIALS, meta_r3.STRATEGY, meta_r3.CODE_CHANGES = d / "trials.json", d / "strategy.json", d / "changes.jsonl"
metrics_r3.outage_secs = lambda *a, **k: 0
now = time.time()
results = []


def run(before, after):
    meta_r3.write_json(meta_r3.NUMBER_TRIALS, [{"t": now - 3700, "changes": {"stuck_hours": [6, 5]}, "reason": "x"}])
    meta_r3.write_json(meta_r3.STRATEGY, {"stuck_hours": 5})
    metrics_r3.market_share = lambda t0, t1: before if t1 <= now - 3700 + 1 else after
    out = meta_r3.fast_guard(now)
    return out, json.loads(meta_r3.STRATEGY.read_text())["stuck_hours"]


out, v = run({"ours": 220, "share_n": 0.0361, "share_value": 0.0464}, {"ours": 168, "share_n": 0.0485, "share_value": 0.0244})
results.append(("value share halved but count share up (a stock-mix hour): kept", not out and v == 5))
out, v = run({"ours": 220, "share_n": 0.0361, "share_value": 0.0464}, {"ours": 60, "share_n": 0.0150, "share_value": 0.0180})
results.append(("both shares collapse: reverted", bool(out) and v == 6))
for name, ok in results:
    print("PASS" if ok else "FAIL", name)
print(f"{sum(ok for _, ok in results)}/{len(results)} passed")
raise SystemExit(0 if all(ok for _, ok in results) else 1)
