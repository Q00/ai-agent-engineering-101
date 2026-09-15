"""Offline checks. No API key, no network, no tokens.

Everything here was run by hand before the graded runs; this file is that
work made rerunnable. Half the grade is reproducibility, and a verification
step that exists only as a claim in a commit message is not reproducible.

    python test_offline.py

Exits 0 if every case passes, 1 otherwise, and prints one line per case.
The model is never called: suites B and C substitute a fake bid function, so
the manager loop and the results writer are exercised on known inputs.
"""

import csv
import os
import shutil
import sys
import tempfile

import contractor
import manager
import run
from contractor import build_team, parse_bid
from model import Meter

PASS, FAIL = [], []


def check(name, got, want):
    if got == want:
        PASS.append(name)
        print(f"ok    {name}")
    else:
        FAIL.append(name)
        print(f"FAIL  {name}\n        got  {got!r}\n        want {want!r}")


# ------------------------------------------------------------------ suite A
# parse_bid decides what counts as a bid and what counts as a failure. The
# lab's numbers depend on that line, so each path is pinned by a case.

PARSE_CASES = [
    ("clean object",
     '{"bid": true, "confidence": 92, "reason": "arithmetic"}', "ok", True, 92.0),
    ("fenced object",
     '```json\n{"bid": false, "confidence": 0, "reason": "not mine"}\n```',
     "fenced", False, 0.0),
    ("object inside prose",
     'Sure! Here is my bid:\n{"bid": true, "confidence": 95, "reason": "x"}\nHope that helps.',
     "embedded", True, 95.0),
    ("string bid and percent confidence",
     '{"bid": "true", "confidence": "95%", "reason": "coerced"}', "ok", True, 95.0),
    ("prose only", "I am confident I can do this task very well indeed.",
     "not_json", None, None),
    ("empty reply", "", "not_json", None, None),
    ("bid key missing", '{"confidence": 90}', "bad_shape", None, None),
    ("confidence out of range", '{"bid": true, "confidence": 140}', "bad_shape", None, None),
    ("bid not boolean", '{"bid": "maybe", "confidence": 90}', "bad_shape", None, None),
    ("no-bid forces confidence to zero",
     '{"bid": false, "confidence": 88, "reason": "outside"}', "ok", False, 0.0),
]


def suite_a():
    print("\n-- A. parse_bid")
    for name, raw, want_how, want_bid, want_conf in PARSE_CASES:
        parsed, how = parse_bid(raw)
        got = (how, None if parsed is None else parsed["bid"],
               None if parsed is None else parsed["confidence"])
        check(f"parse: {name}", got, (want_how, want_bid, want_conf))


# ------------------------------------------------------------------ suite B
# The message count is the lab's cost metric, so its arithmetic is pinned
# rather than trusted: announcements are one per contractor, a received bid is
# one message, an award is one, and a refusal or an unparseable reply is zero.

OWNER = {1: "A", 2: "A", 3: "B", 4: "B", 5: "C", 6: "C"}


def fake_bid(spec):
    def _bid(c, cid, desc, meter):
        meter.add(10, 5)
        verdict = spec(c.name, cid)
        if verdict is None:
            return None, "not_json", "prose, no json"
        if verdict == "nobid":
            return {"bid": False, "confidence": 0.0, "reason": "x"}, "ok", "{}"
        return {"bid": True, "confidence": float(verdict), "reason": "x"}, "ok", "{}"
    return _bid


def round_with(spec, condition="baseline"):
    manager.bid = fake_bid(spec)
    try:
        tasks = manager.load_tasks()
        meter = Meter()
        return manager.run_round(tasks, build_team(condition), meter,
                                 log=lambda *a: None), meter, len(tasks)
    finally:
        manager.bid = contractor.bid


def suite_b():
    print("\n-- B. run_round arithmetic")
    n = len(manager.load_tasks())

    r, _, _ = round_with(lambda who, cid: 100 if OWNER[cid] == who else "nobid")
    check("one bidder per task: messages", r.messages, 3 * n + n + n)
    check("one bidder per task: outcomes",
          (r.correct, r.misawards, r.unassigned), (n, 0, 0))

    r, _, _ = round_with(lambda who, cid: "nobid")
    check("nobody bids: messages", r.messages, 3 * n)
    check("nobody bids: all unassigned", (r.unassigned, r.correct), (n, 0))

    r, _, _ = round_with(lambda who, cid: 95)
    check("all tie at 95: every award to the first asked",
          {a["winner"] for a in r.awards}, {"A"})
    check("all tie at 95: messages", r.messages, 3 * n + 3 * n + n)

    r, _, _ = round_with(
        lambda who, cid: None if who == "C" else (100 if OWNER[cid] == who else "nobid"))
    check("unparseable replies are not messages", r.messages, 3 * n + 4 + 4)
    check("unparseable replies are counted", r.parse_fails, n)

    r, _, _ = round_with(
        lambda who, cid: 99 if who == "C" else (100 if OWNER[cid] == who else "nobid"))
    check("owner outbids a broad bidder", (r.correct, r.misawards), (n, 0))

    r, _, _ = round_with(
        lambda who, cid: 100 if who == "C" else (95 if OWNER[cid] == who else "nobid"))
    check("broad bidder outbids the owner",
          (r.correct, r.misawards), (2, n - 2))

    # The tie-break control. Same bids, reversed ask order: the winner of an
    # all-equal round follows the order, which is what makes `--order reverse`
    # a measurement of the loop rather than of the contractors.
    r, _, _ = round_with(lambda who, cid: 95, condition="homogeneous")
    check("all tie, forward order: first asked wins",
          {a["winner"] for a in r.awards}, {"A"})
    manager.bid = fake_bid(lambda who, cid: 95)
    try:
        tasks = manager.load_tasks()
        rr = manager.run_round(tasks, build_team("homogeneous", "reverse"),
                               Meter(), log=lambda *a: None)
    finally:
        manager.bid = contractor.bid
    check("all tie, reversed order: the other end wins",
          {a["winner"] for a in rr.awards}, {"C"})
    check("reversing order does not change the message count",
          rr.messages, r.messages)


# ------------------------------------------------------------------ suite C
# The writer has to produce the exact header, keep a crashed run's row and
# its metrics, name one log per run, and continue numbering on a rerun.

def suite_c():
    print("\n-- C. results writer")
    tmp = tempfile.mkdtemp(prefix="w03-writer-")
    saved = (run.RESULTS, run.LOGDIR)
    run.RESULTS = os.path.join(tmp, "results.csv")
    run.LOGDIR = os.path.join(tmp, "logs")

    calls = {"n": 0}

    def crashing(c, cid, desc, meter):
        calls["n"] += 1
        meter.add(12, 6)
        if calls["n"] == 14:
            raise RuntimeError("simulated provider hiccup")
        owner = OWNER[cid]
        if c.name == owner:
            return {"bid": True, "confidence": 96.0, "reason": "mine"}, "ok", "{}"
        return {"bid": False, "confidence": 0.0, "reason": "not mine"}, "ok", "{}"

    manager.bid = crashing
    try:
        tasks = manager.load_tasks()
        check("run numbering starts at 1 on an empty directory",
              run.next_run_number(), 1)

        row = run.one_run(1, "baseline", tasks)
        run.append_row(row)
        check("crashed run keeps its row", row["correct"], "")
        check("crashed run keeps its metrics",
              ("tokens=252" in row["note"], "calls=14" in row["note"]), (True, True))
        check("crashed run records the reason",
              "simulated provider hiccup" in row["note"], True)

        row = run.one_run(2, "overconfident", tasks)
        run.append_row(row)
        check("completed run reports outcomes",
              (row["correct"], row["unassigned"], row["misawards"]),
              (len(tasks), 0, 0))

        with open(run.RESULTS, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        check("header is exact", rows[0], run.HEADER)
        check("rows are appended, not replaced", len(rows), 3)
        check("numbering continues after existing rows", run.next_run_number(), 3)

        logs = sorted(os.listdir(run.LOGDIR))
        check("one log per run, named by condition and number",
              logs, ["baseline-01.txt", "overconfident-02.txt"])
        first = open(os.path.join(run.LOGDIR, logs[0]), encoding="utf-8").readline()
        check("log header names provider, model and sampling",
              all(k in first for k in ("provider=", "model=", "temperature=",
                                       "sdk=", "python=")), True)
    finally:
        manager.bid = contractor.bid
        run.RESULTS, run.LOGDIR = saved
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    suite_a()
    suite_b()
    suite_c()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
