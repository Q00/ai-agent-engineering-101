"""Check the metric counting against a deterministic stub before spending
live calls on it.

The expected numbers below are derived by hand from `fake_provider`:

  baseline       A bids on 1,2; B on 3,4; C on 5; C returns prose on 6, and
                 nobody else is eligible, so 6 is unassigned
  homogeneous    all three bid 90 on everything except 6 (C prose), the tie
                 goes to whoever answered first, and the team is polled in the
                 order A, B, C, so A takes all six
  overconfident  C bids 96 everywhere it parses, so it outbids A and B on
                 1-4, wins 5 on its own, and 6 is unassigned again

Run: python test_counting.py
"""
import json
from pathlib import Path

import chat
from contractor import build_team
from fake_provider import make_fake_ask
from manager import run_round

EXPECTED = {
    "baseline":      dict(correct=5, misawards=0, unassigned=1, messages=28, parse_fails=1),
    "homogeneous":   dict(correct=2, misawards=4, unassigned=0, messages=41, parse_fails=1),
    "overconfident": dict(correct=1, misawards=4, unassigned=1, messages=32, parse_fails=1),
}


def main():
    tasks = json.loads((Path(__file__).resolve().parent / "tasks.json")
                       .read_text(encoding="utf-8"))
    ask = make_fake_ask()
    bad = 0
    for condition, want in EXPECTED.items():
        r = run_round(tasks, build_team(condition), chat.Meter(), ask,
                      condition, 0, log=lambda *_: None)
        got = dict(correct=r.correct, misawards=r.misawards,
                   unassigned=r.unassigned, messages=r.messages,
                   parse_fails=r.parse_fails)
        mark = "ok  " if got == want else "FAIL"
        bad += got != want
        print(f"{mark} {condition:14} {got}")
        if got != want:
            print(f"     expected {want}")
        # every task is accounted for exactly once
        assert r.correct + r.misawards + r.unassigned == r.tasks
        # one record per contractor per task, no matter how the reply parsed
        assert len(r.records) == r.tasks * 3
    print("counting verified" if not bad else f"{bad} condition(s) off")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
