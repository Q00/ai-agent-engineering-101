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


# ------------------------------------------------------------------ suite D
# The message layer. Delivery order, generation order, concurrency, and the
# causal cut that keeps a trajectory from reading the future.

import random
import tempfile as _tf
from protocol import (Endpoint, Journal, bus, trajectory,
                      ANNOUNCE, BID, AWARD, ACCEPTANCE, REFUSAL, REPORT)

MEMBERS = ("P1", "P2", "P3", "P4")
ROLES_1 = {"1": {"P1": "manager", "P2": "contractor",
               "P3": "contractor", "P4": "contractor"}}
ROLES_2 = {"1": {"P1": "manager", "P2": "contractor",
               "P3": "contractor", "P4": "contractor"},
           "2": {"P4": "manager", "P1": "contractor",
               "P2": "contractor", "P3": "contractor"}}


def _net(roles):
    j = Journal(os.path.join(_tf.mkdtemp(prefix="w03-proto-"), "messages.jsonl"))
    eps = {n: Endpoint(n, MEMBERS, journal=j) for n in MEMBERS}
    for task, per in roles.items():
        for name, role in per.items():
            eps[name].assign_role(task, role)
    return eps, bus(eps), j


def suite_d():
    print("\n-- D. protocol: causality, generation order, concurrency")

    # A message that was never generated in a legal order must be refused at
    # the source. The mailbox fixes arrival order and says nothing about this.
    eps, _, _ = _net(ROLES_1)
    check("award before announcing is refused",
          eps["P1"].send(AWARD, "P2", "1", {}), None)
    check("the refusal is recorded with a reason",
          eps["P1"].violations[0]["reason"],
          "cannot award a task this endpoint never announced")
    check("bidding before the announcement arrives is refused",
          eps["P2"].send(BID, "P1", "1", {"bid": True}), None)
    check("reporting without having accepted is refused",
          eps["P2"].send(REPORT, "P1", "1", {}), None)

    # Full legal sequence, and the envelope/content split.
    eps, send, j = _net(ROLES_1)
    send(eps["P1"].send(ANNOUNCE, ("P2", "P3", "P4"), "1", {"desc": "x"}))
    check("announcing twice is refused",
          eps["P1"].send(ANNOUNCE, ("P2",), "1", {}), None)
    send(eps["P2"].send(BID, "P1", "1", {"bid": True, "confidence": 92}))
    send(eps["P3"].send(BID, "P1", "1", {"bid": True, "confidence": 80}))
    send(eps["P4"].send(BID, "P1", "1", {"bid": False, "confidence": 0}))
    check("the manager sees only those who bid yes",
          eps["P1"].bidders("1"), ["P2", "P3"])
    check("a rival's bid is not readable",
          [m.type for m in eps["P2"].mailbox.delivered], [ANNOUNCE])
    check("but its clock accounts for that send",
          eps["P2"].clock.v["P3"], 1)
    send(eps["P1"].send(AWARD, "P2", "1", {}))
    check("so the award is not held back",
          [m.type for m in eps["P2"].mailbox.delivered], [ANNOUNCE, AWARD])
    check("awarding a candidate that did not bid is refused",
          eps["P1"].send(AWARD, "P4", "1", {}), None)
    check("re-awarding before an answer is refused",
          eps["P1"].send(AWARD, "P3", "1", {}), None)
    send(eps["P2"].send(ACCEPTANCE, "P1", "1", {}))
    check("accepting commits the candidate", eps["P2"].committed, "1")
    send(eps["P2"].send(REPORT, "P1", "1", {"answer": "14:00"}))
    check("reporting releases it", eps["P2"].committed, None)
    check("nothing is left held",
          sum(e.mailbox.pending() for e in eps.values()), 0)
    check("journal holds every message", len(j.records()), 7)

    # Two tasks at once. Capacity is one contract, so Smith's REFUSAL is what
    # a committed candidate answers with, and the manager falls to the next bid.
    eps, send, _ = _net(ROLES_2)
    send(eps["P1"].send(ANNOUNCE, ("P2", "P3", "P4"), "1", {}))
    send(eps["P4"].send(ANNOUNCE, ("P1", "P2", "P3"), "2", {}))
    for n in ("P2", "P3"):
        send(eps[n].send(BID, "P1", "1", {"bid": True, "confidence": 95}))
        send(eps[n].send(BID, "P4", "2", {"bid": True, "confidence": 95}))
    send(eps["P1"].send(AWARD, "P2", "1", {}))
    send(eps["P2"].send(ACCEPTANCE, "P1", "1", {}))
    aw2 = eps["P4"].send(AWARD, "P2", "2", {})
    send(aw2)
    check("a second manager may still award the same candidate",
          aw2.type, AWARD)
    check("but a committed candidate cannot accept",
          eps["P2"].send(ACCEPTANCE, "P4", "2", {}), None)
    ref = eps["P2"].send(REFUSAL, "P4", "2", {"justification": "committed"})
    send(ref)
    check("it refuses instead", ref.type, REFUSAL)
    aw3 = eps["P4"].send(AWARD, "P3", "2", {})
    send(aw3)
    check("and the manager falls to the next bidder", aw3.type, AWARD)
    check("no message stranded",
          sum(e.mailbox.pending() for e in eps.values()), 0)

    # Delivery order must not change the outcome.
    def one_run(seed=None):
        j2 = Journal(os.path.join(_tf.mkdtemp(prefix="w03-shuf-"), "m.jsonl"))
        e = {n: Endpoint(n, MEMBERS, journal=j2) for n in MEMBERS}
        for name, role in ROLES_1["1"].items():
            e[name].assign_role("1", role)
        box = []
        put = lambda m: box.append(m) if m is not None else None
        def flush():
            if seed is not None:
                random.Random(seed).shuffle(box)
            while box:
                m = box.pop(0)
                for ep in e.values():
                    ep.deliver(m)
        put(e["P1"].send(ANNOUNCE, ("P2", "P3", "P4"), "1", {})); flush()
        put(e["P2"].send(BID, "P1", "1", {"bid": True, "confidence": 90}))
        put(e["P3"].send(BID, "P1", "1", {"bid": True, "confidence": 80})); flush()
        put(e["P1"].send(AWARD, "P2", "1", {})); flush()
        put(e["P2"].send(ACCEPTANCE, "P1", "1", {})); flush()
        put(e["P2"].send(REPORT, "P1", "1", {"answer": "14:00"})); flush()
        return ([(r["type"], r["frm"]) for r in j2.records()],
                sum(x.mailbox.pending() for x in e.values()))

    base = one_run(None)
    for seed in (1, 7, 42, 99):
        check(f"shuffled delivery, seed {seed}, same journal",
              one_run(seed), base)

    # The trajectory a manager reads must stop at its decision.
    eps, send, j = _net(ROLES_1)
    send(eps["P1"].send(ANNOUNCE, ("P2",), "1", {}))
    send(eps["P2"].send(BID, "P1", "1", {"bid": True, "confidence": 90}))
    send(eps["P1"].send(AWARD, "P2", "1", {}))
    cut = eps["P1"].decision_cut()
    send(eps["P2"].send(ACCEPTANCE, "P1", "1", {}))
    send(eps["P2"].send(REPORT, "P1", "1", {"answer": "14:00"}))
    recs = j.records()
    check("uncut, the report is visible",
          trajectory(recs, "P2", members=MEMBERS)["reports"], 1)
    check("cut at the decision, it is not",
          trajectory(recs, "P2", before=cut, members=MEMBERS)["reports"], 0)
    check("what preceded the decision stays visible",
          trajectory(recs, "P2", before=cut, members=MEMBERS)["bids"], 1)
    check("and the answer never enters a trajectory",
          "14:00" in str(trajectory(recs, "P2", before=cut, members=MEMBERS)),
          False)
    # Depth. A winner that cannot finish alone breaks the task up and becomes
    # the manager of the pieces. Capacity has to be two things for this to
    # work at all: P2 owes the parent and runs the children at the same time.
    eps, send, j = _net(ROLES_1)
    send(eps["P1"].send(ANNOUNCE, ("P2", "P3", "P4"), "1", {"desc": "compound"}))
    send(eps["P2"].send(BID, "P1", "1", {"bid": True, "confidence": 88,
                                         "evidence": ["count_by_hour"]}))
    send(eps["P1"].send(AWARD, "P2", "1", {}))
    send(eps["P2"].send(ACCEPTANCE, "P1", "1", {}))
    check("the winner owes the parent", eps["P2"].committed, "1")
    check("reporting is blocked while nothing is decomposed yet",
          eps["P2"].can_send(REPORT, "1")[0], True)

    sub = eps["P2"].send(ANNOUNCE, ("P1", "P3", "P4"), "1.1", {"desc": "piece"})
    send(sub)
    check("the winner may announce a subtask", sub.type, ANNOUNCE)
    check("and is now managing it", "1.1" in eps["P2"].managing, True)
    check("while still committed to the parent", eps["P2"].committed, "1")
    check("subtask depth is 2", sub.depth, 2)
    check("parent is recorded on the subtask", sub.parent, "1")
    check("a contractor self-assigns on receiving the subtask announcement",
          eps["P3"].roles.get("1.1"), "contractor")
    check("the parent cannot report with an open subtask",
          eps["P2"].can_send(REPORT, "1")[0], False)

    check("depth 3 is refused",
          eps["P2"].send(ANNOUNCE, ("P1",), "1.1.1", {}), None)
    check("a candidate that never accepted a parent cannot announce a subtask",
          eps["P3"].send(ANNOUNCE, ("P1",), "1.2", {}), None)

    send(eps["P3"].send(BID, "P2", "1.1", {"bid": True, "confidence": 90,
                                           "evidence": ["read_log"]}))
    send(eps["P2"].send(AWARD, "P3", "1.1", {}))
    send(eps["P3"].send(ACCEPTANCE, "P2", "1.1", {}))
    send(eps["P3"].send(REPORT, "P2", "1.1", {"answer": "09:07 INFO ..."}))
    check("with the subtask reported the parent may report",
          eps["P2"].can_send(REPORT, "1")[0], True)
    send(eps["P2"].send(REPORT, "P1", "1", {"answer": "14:00 / NullReference"}))
    check("parent report releases the commitment", eps["P2"].committed, None)
    check("and stops managing the subtask tree", eps["P2"].managing, set())

    # An orphaned subtask: nobody bids, so the piece is written off and the
    # parent reports what it has rather than the whole contract collapsing.
    eps, send, j = _net(ROLES_1)
    send(eps["P1"].send(ANNOUNCE, ("P2", "P3", "P4"), "1", {}))
    send(eps["P2"].send(BID, "P1", "1", {"bid": True, "confidence": 70}))
    send(eps["P1"].send(AWARD, "P2", "1", {}))
    send(eps["P2"].send(ACCEPTANCE, "P1", "1", {}))
    send(eps["P2"].send(ANNOUNCE, ("P1", "P3", "P4"), "1.1", {}))
    check("open subtask blocks the parent",
          eps["P2"].can_send(REPORT, "1")[0], False)
    eps["P2"].mark_orphan("1.1", "no bid before expiry")
    check("an orphaned subtask unblocks it",
          eps["P2"].can_send(REPORT, "1")[0], True)
    check("and the parent report is marked partial",
          eps["P2"].partial("1"), True)
    check("the write-off is recorded",
          [v["type"] for v in eps["P2"].violations], ["orphan_subtask"])


# ------------------------------------------------------------------ suite E
# The work tools. Deterministic over a fixed file, so every answer here is
# pinned rather than described, and the capability helpers are checked against
# the manifests the extension actually uses.

import tools as T

MANIFESTS = {
    "P1": ["count_by_hour", "count_level"],
    "P2": ["grep_message"],
    "P3": ["read_log"],
    "P4": ["count_by_hour", "grep_message"],
}


def suite_e():
    print("\n-- E. work tools over app.log")

    out = T.run_tool("count_by_hour", {"level": "ERROR"})
    check("count_by_hour names the busiest hour",
          "highest: 14:00 with 6" in out, True)
    check("count_by_hour returns no message text",
          "QuizService" in out, False)
    check("count_level counts against the file total",
          T.run_tool("count_level", {"level": "WARN"}), "WARN: 11 of 60 lines")
    check("grep_message finds every hit",
          T.run_tool("grep_message", {"text": "QuizService"}).splitlines()[0],
          "7 line(s) contain 'QuizService':")
    check("read_log returns the raw text",
          T.run_tool("read_log", {"start": 2, "end": 2}).splitlines()[1],
          "2026-09-01 09:11:56 ERROR NullReference in QuizService.score")

    # Bad arguments come back as text the agent can read, not as exceptions.
    check("an unknown level is refused",
          T.run_tool("count_by_hour", {"level": "TRACE"}).startswith("error:"), True)
    check("an empty needle is refused",
          T.run_tool("grep_message", {"text": ""}).startswith("error:"), True)
    check("a reversed range is refused",
          T.run_tool("read_log", {"start": 5, "end": 2}).startswith("error:"), True)
    check("an over-wide range is capped, not refused",
          "capped at" in T.run_tool("read_log", {"start": 1, "end": 999}), True)
    check("an unknown tool is refused",
          T.run_tool("nope", {}).startswith("error:"), True)
    check("missing arguments do not raise",
          T.run_tool("grep_message", {"wrong": 1}).startswith("error:"), True)

    # capable is derived from the manifests, so it cannot drift from them.
    check("one tool, two holders",
          T.capable_for(["count_by_hour"], MANIFESTS), ["P1", "P4"])
    check("a pair only P4 holds",
          T.capable_for(["count_by_hour", "grep_message"], MANIFESTS), ["P4"])
    check("a pair nobody holds forces decomposition",
          T.capable_for(["count_by_hour", "read_log"], MANIFESTS), [])
    try:
        T.capable_for(["calculator"], MANIFESTS)
        check("a task needing an unknown tool is rejected", "no error", "ValueError")
    except ValueError:
        check("a task needing an unknown tool is rejected", "ValueError", "ValueError")

    cov = T.coverage(MANIFESTS, 2)
    uncovered = sorted(c for c, who in cov.items() if not who)
    check("four of six tool pairs need two candidates", len(uncovered), 4)
    check("count_by_hour + read_log is one of them",
          ("count_by_hour", "read_log") in uncovered, True)

    # Evidence is a claim about one's own manifest, so it is checkable.
    check("naming a tool you do not hold is false evidence",
          T.false_evidence(["count_by_hour", "grep_message"], MANIFESTS["P2"]),
          ["count_by_hour"])
    check("naming only what you hold is clean",
          T.false_evidence(["count_by_hour"], MANIFESTS["P4"]), [])
    check("an empty evidence list is clean",
          T.false_evidence(None, MANIFESTS["P1"]), [])

    # Declaration order is fixed, because the cached prefix depends on it.
    check("declaration order does not follow the manifest's order",
          [d["name"] for d in T.specs_for(["grep_message", "count_by_hour"])],
          [d["name"] for d in T.specs_for(["count_by_hour", "grep_message"])])
    check("every declaration carries a description and a schema",
          all(d.get("description") and d.get("input_schema")
              for d in T.specs_for(T.TOOL_NAMES)), True)


if __name__ == "__main__":
    suite_a()
    suite_b()
    suite_c()
    suite_d()
    suite_e()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
