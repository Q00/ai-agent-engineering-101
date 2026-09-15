"""Runner for the extension layer: four conditions, rounds, one journal each.

Writes beside the spec layer, never into it. check_week03.py counts any
condition value outside baseline|homogeneous|overconfident as a malformed row,
so results_ext.csv and logs_ext/ are separate files the way week 02's
early-exit appendix was.

    python run_ext.py --runs 3 --rounds 2
    python run_ext.py --conditions ext_baseline --runs 1 --rounds 1   # smoke

THE FOUR CONDITIONS

  ext_baseline        tools differentiated, the manager decides, no history.
  ext_coded_manager   only the manager changes: the harness takes
                      max(confidence), week 03's rule exactly. Isolates what
                      making the award a judgement did.
  ext_trajectory      the manager may call get_trajectory. The direct answer
                      to the trust gap week 03 could only describe.
  ext_uniform_tools   every candidate holds every tool. The extension of week
                      03's homogeneous condition, now that capability is a
                      fact rather than a string.

`capable` and `gold` are recomputed per condition rather than read from
tasks_ext.json, because they are derived from the manifests and a condition
may change those. Under ext_uniform_tools every candidate is capable and
none is more specialised than another, so gold is None and `optimal` is
undefined for that condition — which is the finding, not a gap.

ROUNDS

A round is every task once. get_trajectory has nothing to read in round 1, so
its effect can only appear from round 2; rounds are therefore a row dimension
and not averaged away.

Appendix conditions, run separately and kept out of the main table:
  ext_fresh           like ext_baseline with the conversation reset per turn,
                      to price what the persistent prefix is worth.
"""

import argparse
import csv
import json
import os
import sys
import time
import traceback

import agents as A
import model
import phases as P
import tools as T
from protocol import Endpoint, Journal

HEADER = ["run", "condition", "round", "tasks", "feasible", "optimal",
          "solved", "partial", "unassigned", "messages", "calls", "tokens",
          "cache_read", "cache_created", "note"]
RESULTS = "results_ext.csv"
LOGDIR = "logs_ext"

CONDITIONS = {
    "ext_baseline":      {"coded_manager": False, "trajectory": False,
                          "uniform": False, "persistent": True},
    "ext_coded_manager": {"coded_manager": True,  "trajectory": False,
                          "uniform": False, "persistent": True},
    "ext_trajectory":    {"coded_manager": False, "trajectory": True,
                          "uniform": False, "persistent": True},
    "ext_uniform_tools": {"coded_manager": False, "trajectory": False,
                          "uniform": True,  "persistent": True},
    # appendix
    "ext_fresh":         {"coded_manager": False, "trajectory": False,
                          "uniform": False, "persistent": False},
    # Appendix. Identical to ext_baseline except that the tools' output cap is
    # tightened via AGENT_MAX_LINES, which is what decides whether tool
    # specialisation binds. At the default cap of 20 a candidate holding only
    # read_log surveys all 60 lines in three calls, so it can substitute for
    # every other tool and no decomposition ever fires. Run it as:
    #   AGENT_MAX_LINES=5 python run_ext.py --conditions ext_tight_reads
    "ext_tight_reads":   {"coded_manager": False, "trajectory": False,
                          "uniform": False, "persistent": True},
}


def manifests_for(cfg):
    if cfg["uniform"]:
        return {n: T.TOOL_NAMES for n in T.CANDIDATES}
    return {n: T.MANIFESTS[n] for n in T.CANDIDATES}


def tasks_for(cfg, path="tasks_ext.json"):
    """Re-derive capable and gold under this condition's manifests."""
    doc = json.load(open(path, encoding="utf-8"))
    man = manifests_for(cfg)
    out = []
    for t in doc["tasks"]:
        try:
            gold = T.gold_for(t["requires"], man)
        except ValueError:
            gold = None          # nobody is more specialised: no best answer
        out.append({**t, "capable": T.capable_for(t["requires"], man),
                    "gold": gold})
    return out


def one_run(run_no, condition, runs_rounds, log=print):
    cfg = CONDITIONS[condition]
    os.makedirs(LOGDIR, exist_ok=True)
    stem = os.path.join(LOGDIR, f"{condition}-{run_no:02d}")
    journal = Journal(stem + ".jsonl")
    man = manifests_for(cfg)
    tasks = tasks_for(cfg)
    meter = model.Meter()
    endpoints = {n: Endpoint(n, T.CANDIDATES, journal=journal)
                 for n in T.CANDIDATES}
    agents = {n: A.Agent(n, journal=journal, persistent=cfg["persistent"],
                         meter=meter, manifest=man[n],
                         allow_trajectory=cfg["trajectory"])
              for n in T.CANDIDATES}

    rows, details = [], []
    with open(stem + ".txt", "w", encoding="utf-8") as fh:
        def out(*a):
            line = " ".join(str(x) for x in a)
            print(line, file=fh, flush=True)
            log(line)

        out(model.run_header())
        out(f"run={run_no} condition={condition} rounds={runs_rounds} "
            f"persistent={cfg['persistent']} trajectory={cfg['trajectory']} "
            f"coded_manager={cfg['coded_manager']} uniform={cfg['uniform']}")
        for n in T.CANDIDATES:
            out(f"  {n}: tools={list(man[n])}")
        out("-" * 72)

        for rnd in range(1, runs_rounds + 1):
            started = time.time()
            before = (meter.calls, meter.tokens)
            got = {"feasible": 0, "optimal": 0, "solved": 0, "partial": 0,
                   "unassigned": 0, "messages": 0}
            note_bits = []
            try:
                for task in tasks:
                    out(f"\n[round {rnd}] TASK {task['id']} ({task['grain']}) "
                        f"capable={task['capable']} gold={task['gold']}")
                    r = P.run_task(task, endpoints, agents, journal, cfg, rnd)
                    row = r.row()
                    row.update({"run": run_no, "condition": condition,
                                "round": rnd})
                    details.append(row)
                    out(f"  manager={r.manager} awarded={r.awarded} "
                        f"feasible={int(r.feasible)} optimal={int(r.optimal)} "
                        f"solved={int(r.solved)} msgs={r.messages}")
                    if r.failures:
                        out(f"  failures: {';'.join(r.failures)}")
                    for who, tool, args, head in r.work_calls:
                        out(f"  [tool] {who} {tool}({args}) -> {head}")
                    if r.report:
                        out(f"  report: {r.report.strip().splitlines()[-1][:120]}")
                    got["feasible"] += int(r.feasible)
                    got["optimal"] += int(r.optimal)
                    got["solved"] += int(r.solved)
                    got["partial"] += int(r.partial)
                    got["unassigned"] += int(r.awarded is None)
                    got["messages"] += r.messages
                    note_bits.extend(r.failures)
            except Exception as exc:
                wall = time.time() - started
                reason = f"{type(exc).__name__}: {exc}"
                out("-" * 72)
                out(f"CRASH in round {rnd} after {meter.calls} call(s): {reason}")
                out(traceback.format_exc())
                cache = journal.cache_stats()
                rows.append({
                    "run": run_no, "condition": condition, "round": rnd,
                    "tasks": len(tasks), "feasible": "", "optimal": "",
                    "solved": "", "partial": "", "unassigned": "",
                    "messages": got["messages"],
                    "calls": meter.calls - before[0],
                    "tokens": meter.tokens - before[1],
                    "cache_read": cache["read"], "cache_created": cache["created"],
                    "note": f"crash: {reason} wall={wall:.1f}s"})
                break

            wall = time.time() - started
            cache = journal.cache_stats()
            modes = {}
            for f in note_bits:
                modes[f.split(":")[0]] = modes.get(f.split(":")[0], 0) + 1
            rows.append({
                "run": run_no, "condition": condition, "round": rnd,
                "tasks": len(tasks), **got,
                "calls": meter.calls - before[0],
                "tokens": meter.tokens - before[1],
                "cache_read": cache["read"], "cache_created": cache["created"],
                "note": " ".join(f"{k}={v}" for k, v in sorted(modes.items()))
                        + f" reuse={cache['reuse']} wall={wall:.1f}s"})
            out("-" * 72)
            out(f"[round {rnd}] " + " ".join(f"{k}={v}" for k, v in got.items())
                + f" calls={rows[-1]['calls']} tokens={rows[-1]['tokens']} "
                f"cache_read={cache['read']} reuse={cache['reuse']}")

    journal.close()
    return rows, details


def next_run_number():
    if not os.path.exists(RESULTS):
        return 1
    with open(RESULTS, newline="", encoding="utf-8") as fh:
        nums = [int(r[0]) for r in csv.reader(fh)
                if r and r[0].isdigit()]
    return max(nums) + 1 if nums else 1


def append(path, header, rows):
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        if not exists:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in header})


DETAIL = "tasks_ext_detail.csv"
DETAIL_HEADER = ["run", "condition", "round", "task", "number", "manager", "gold",
                 "awarded", "feasible", "optimal", "solved", "partial",
                 "messages", "bids", "false_evidence", "trajectory_calls",
                 "subtasks", "tool_calls", "failures"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conditions", nargs="+", default=[
        "ext_baseline", "ext_coded_manager", "ext_trajectory",
        "ext_uniform_tools"], choices=list(CONDITIONS))
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--tasks", default=None,
                    help="limit to these task ids, for a smoke run")
    ap.add_argument("--results", default=None,
                    help="write rows here instead of results_ext.csv. An "
                         "appendix condition belongs in its own table, and "
                         "separate files also let it run beside the main set "
                         "without racing on the run number — next_run_number "
                         "reads and append writes, with a gap between them")
    ap.add_argument("--detail", default=None)
    ap.add_argument("--logdir", default=None)
    args = ap.parse_args()

    global RESULTS, DETAIL, LOGDIR
    if args.results:
        RESULTS = args.results
    if args.detail:
        DETAIL = args.detail
    if args.logdir:
        LOGDIR = args.logdir

    if args.tasks:
        wanted = set(args.tasks.split(","))
        orig = tasks_for
        globals()["tasks_for"] = lambda cfg, path="tasks_ext.json": [
            t for t in orig(cfg, path) if t["id"] in wanted]

    run_no = next_run_number()
    print(model.run_header())
    print(f"conditions={args.conditions} runs={args.runs} rounds={args.rounds} "
          f"first_run={run_no}")
    for condition in args.conditions:
        for _ in range(args.runs):
            print("\n" + "=" * 72)
            rows, details = one_run(run_no, condition, args.rounds)
            append(RESULTS, HEADER, rows)
            append(DETAIL, DETAIL_HEADER, details)
            print(f"[appended] run {run_no} ({condition}) -> {RESULTS}, {DETAIL}")
            run_no += 1


if __name__ == "__main__":
    main()
