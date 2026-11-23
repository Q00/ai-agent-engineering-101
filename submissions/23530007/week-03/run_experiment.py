"""Week 03 — run the three conditions and record results.csv.

Usage: python run_experiment.py [--runs 3] [--workers 9]

Reads tasks.json, runs every condition --runs times on the same task set with
the same prompts and the same model, writes one line per run to results.csv,
and saves one console capture per run under logs/. A crashed run keeps its row
with blank counts and the error in `note`: it is data, not a lost run.

Parallelism is a runner detail, not an experimental variable. Each run builds
its own team and its own Meter, so runs share no state. Inside a run the three
contractors are still called in order, because the manager's tie-break rule is
"highest confidence, and on a tie the contractor that replied first" — that
rule only means something if the order is fixed. --workers 1 runs everything
sequentially and changes nothing but wall-clock time.
"""
import argparse
import csv
import time
from concurrent.futures import ThreadPoolExecutor
from json import loads
from pathlib import Path

import contract_net as cn
from contract_net import Meter, build_team, run_round

HEADER = ["run", "condition", "tasks", "correct", "messages", "unassigned",
          "misawards", "note"]
CONDITIONS = ("baseline", "homogeneous", "overconfident")


def settings_line() -> str:
    """First line of every log file: the settings needed to reproduce it."""
    temp = (f"temperature={cn.TEMPERATURE}" if cn.TEMPERATURE is not None else
            "temperature=UNSET (claude-opus-5 rejects the parameter with a 400: "
            "'`temperature` is deprecated for this model'; provider-default "
            "sampling was used)")
    return (f"provider={cn.PROVIDER} model={cn.MODEL} {temp} "
            f"max_tokens={cn.MAX_TOKENS} sdk=anthropic-python")


def one_run(run_no: int, condition: str, tasks) -> dict:
    lines = []

    def log(msg, _lines=lines):
        _lines.append(str(msg))

    log(settings_line())
    log(f"run={run_no} condition={condition} tasks={len(tasks)}")
    team = build_team(condition)
    for c in team:
        log(f"  contractor {c.name}: skill={c.skill!r} overconfident={c.overconfident}")
    log("")

    meter = Meter()
    t0 = time.time()
    try:
        r = run_round(tasks, team, meter, log=log)
        note = f"parse_fails={r.parse_fails} tokens={meter.tokens} calls={meter.calls}"
        row = [run_no, condition, r.tasks, r.correct, r.messages, r.unassigned,
               r.misawards, note]
        summary = (f"correct={r.correct}/{r.tasks} messages={r.messages} "
                   f"unassigned={r.unassigned} misawards={r.misawards} "
                   f"parse_fails={r.parse_fails}")
    except Exception as e:                      # a crash keeps its row, blank counts
        note = f"crash: {type(e).__name__}: {e} tokens={meter.tokens}"
        row = [run_no, condition, "", "", "", "", "", note]
        summary = note
        log(note)
    elapsed = time.time() - t0
    log("")
    log(f"[summary] {summary} ({elapsed:.1f}s)")

    Path("logs", f"{condition}-{run_no:02d}.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    return {"run_no": run_no, "condition": condition, "row": row,
            "elapsed": elapsed, "summary": summary}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3, help="runs per condition")
    ap.add_argument("--workers", type=int, default=9,
                    help="runs executed concurrently; 1 = sequential")
    args = ap.parse_args()

    tasks = loads(Path("tasks.json").read_text(encoding="utf-8"))
    Path("logs").mkdir(exist_ok=True)
    new_file = not Path("results.csv").exists()
    offset = 0
    if not new_file:
        with open("results.csv", encoding="utf-8") as f:
            offset = sum(1 for _ in f) - 1

    jobs = []
    run_no = offset
    for condition in CONDITIONS:
        for _ in range(args.runs):
            run_no += 1
            jobs.append((run_no, condition))

    workers = max(1, min(args.workers, len(jobs)))
    print(settings_line())
    print(f"{len(jobs)} run(s), {workers} at a time, {len(tasks)} tasks each\n")

    wall0 = time.time()
    if workers == 1:
        results = [one_run(n, c, tasks) for n, c in jobs]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(one_run, n, c, tasks) for n, c in jobs]
            results = [f.result() for f in futures]
    wall = time.time() - wall0

    results.sort(key=lambda r: r["run_no"])
    with open("results.csv", "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HEADER)
        for r in results:
            w.writerow(r["row"])

    for r in results:
        print(f"  run {r['run_no']:>2} {r['condition']:<14} {r['summary']}  "
              f"({r['elapsed']:.1f}s)")
    serial = sum(r["elapsed"] for r in results)
    print(f"\nwall {wall:.1f}s (sum of runs {serial:.1f}s, speedup {serial / wall:.1f}x)")
    print("results.csv updated")


if __name__ == "__main__":
    main()
