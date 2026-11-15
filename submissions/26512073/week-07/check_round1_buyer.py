"""Development checks against course test shops; not a full market-test report."""
import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "course_support"))
import market_test_week07 as kit


def check(stamp):
    cfg = json.loads((BASE / "agent.json").read_text(encoding="utf-8"))
    memory = BASE / ".market" / f"development-memory-{stamp}.jsonl"
    cfg["market_buyer"]["cmd"] += f' --memory "{memory}"'
    cases = [
        ("review-choice-" + stamp, kit.DEAL,
         [{"name": "Kit Shop North", "reviews": kit.BAD_REVIEWS},
          {"name": "Kit Shop South", "reviews": kit.GOOD_REVIEWS}]),
        ("budget-" + stamp, kit.NO_DEAL,
         [{"name": "Kit Shop South", "reviews": kit.GOOD_REVIEWS}]),
    ]
    for name, scenario, shops in cases:
        line, records, urls, timed_out = kit.run_market_buyer(BASE, cfg, name, 1, shops, scenario)
        assert not timed_out
        assert kit.line_problem(line, urls) is None, line
        assert all(record["bad_version"] == 0 for record in records)
        assert all(offer <= scenario["budget"] for record in records for offer in record["offers"])
        if len(shops) == 2:
            assert records[0]["posts"] == 0 and records[1]["continued"] > 0, records
            receipt = records[1]["deals"][0]
            assert line["receipt"]["sig_seller"] == receipt["sig_seller"]
            assert line["receipt"]["task"] == receipt["task"]
            assert line["receipt"]["price"] == receipt["price"]
        else:
            assert not records[0]["deals"] and line["receipt"] is None
    entries = [json.loads(line) for line in memory.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 2 and all(e["shop_name"] == "Kit Shop South" for e in entries)
    print("PASS: review-based selection, original receipt, budget, protocol, and persistent memory", flush=True)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_dir = BASE / "development_logs"
    log_dir.mkdir(exist_ok=True)
    with (log_dir / f"round1-check-{stamp}.txt").open("x", encoding="utf-8") as capture:
        with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
            try:
                check(stamp)
            except Exception as error:
                print(f"FAILED: {type(error).__name__}: {error}", flush=True)
                raise
