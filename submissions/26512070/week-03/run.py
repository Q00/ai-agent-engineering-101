"""Runner. Drives the arms, writes results.csv, results_bias.csv, logs/, history/.

    python run.py                          # every arm, 3 runs each
    python run.py --arms core --runs 1
    python run.py --arms bias bias+ib --seed 20260921

Three arms, because Bias and icebreaking are two separate interventions and
folding them together would make it impossible to say which one moved a metric:

    core      plain contract net. Bias off, no icebreaking.   -> results.csv
    bias      Bias advises and can veto. No icebreaking, so   -> results_bias.csv
              Bias only ever sees the winner execute.
    bias+ib   Bias, plus an icebreaking round first in which  -> results_bias.csv
              every contractor executes every warm-up task.

`results.csv` holds the core arm alone and keeps the fixed header, so its
`messages` column means what it means for everyone else.

SEEDING. The model API takes no seed, so a transcript cannot be reproduced
exactly at temperature 0.7. What is seeded is every decision this code makes
on its own -- above all the tie-break in Manager.pick_winner, which decides the
whole `homogeneous` condition. Each run's seed is derived from --seed and the
run id, and printed at the top of its log.
"""
import argparse
import csv
import json
import random
import threading
import traceback
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, is_dataclass
from pathlib import Path

import conditions as C
import llm
from bias import Bias
from contractor import build
from manager import Manager
from protocol import Task

HERE = Path(__file__).parent
HEADER = ["run", "condition", "tasks", "correct", "messages",
          "unassigned", "misawards", "note"]
BIAS_HEADER = HEADER + [
    "arm", "bias_messages", "icebreak_messages",
    "unassigned_nobid", "unassigned_veto", "veto_hit_gold",
    "bias_helped", "bias_hurt", "first_intervention", "first_flip",
]

ARMS = {
    # name: (use_bias, use_icebreak, output file)
    "core":    (False, False, "results.csv"),
    "bias":    (True,  False, "results_bias.csv"),
    "bias+ib": (True,  True,  "results_bias.csv"),
}


class Tee:
    """Writes every line to the console and to the run's log file at once, both
    in UTF-8, so a Korean log survives a cp949 console redirect on Windows.

    With --jobs > 1 the console half is dropped: several runs writing to one
    terminal would interleave into something unreadable. The per-run log files
    are unaffected and stay the complete record."""

    def __init__(self, path: Path, quiet: bool = False):
        self.fh = path.open("w", encoding="utf-8")
        self.quiet = quiet

    def __call__(self, *parts):
        line = " ".join(str(p) for p in parts)
        self.fh.write(line + "\n")
        self.fh.flush()
        if self.quiet:
            return
        try:
            print(line)
        except UnicodeEncodeError:
            print(line.encode("ascii", "replace").decode("ascii"))

    def close(self):
        self.fh.close()


def load_tasks():
    data = json.loads((HERE / "tasks.json").read_text(encoding="utf-8"))
    tasks = [Task(t["id"], t["desc"], t["gold"],
                  t.get("phase", "measure"), t.get("check", {}))
             for t in data]
    icebreak = [t for t in tasks if t.phase == "icebreak"]
    measure = [t for t in tasks if t.phase == "measure"]
    # Disjoint by construction: warming Bias up on a task it will later be
    # scored on would let it carry that task's answer into the measurement.
    assert not ({t.id for t in icebreak} & {t.id for t in measure})
    return icebreak, measure


def _plain(obj):
    if is_dataclass(obj):
        return {k: _plain(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def _count(records, attr):
    return sum(1 for r in records if getattr(r, attr) is True)


def one_run(condition, arm, icebreak_tasks, measure_tasks, run_id, seed, quiet=False):
    use_bias, use_ib, _ = ARMS[arm]
    log = Tee(HERE / "logs" / f"{run_id}.txt", quiet=quiet)
    meter = llm.Meter()

    contractors = build(C.roster(condition))
    # Fresh Bias for every run. Memory dies here, so three runs of an arm are
    # three replicates rather than one learning curve. See bias.py.
    bias = Bias([c.name for c in contractors]) if use_bias else None
    manager = Manager(contractors, bias=bias, log=log, rng=random.Random(seed))

    log(f"=== run {run_id} ===")
    log(f"provider={llm.PROVIDER} model={llm.MODEL} temperature={llm.TEMPERATURE}")
    log(f"condition={condition} arm={arm} bias={'on' if use_bias else 'off'} "
        f"icebreak={'on' if use_ib else 'off'}")
    log(f"seed={seed} veto_threshold={C.VETO_THRESHOLD} "
        f"adj_range=({C.ADJ_MIN},{C.ADJ_MAX})")
    log(f"contractors={[c.name for c in contractors]}")
    log(f"icebreak_tasks={[t.id for t in icebreak_tasks] if use_ib else []}")
    log(f"measure_tasks={[t.id for t in measure_tasks]}")
    log(f"fixtures={{{', '.join(t.id + ':' + t.check.get('kind', 'none') for t in measure_tasks)}}}")
    log("")

    ib_records, ib_messages = [], 0
    if use_ib:
        log("########## icebreaking (not scored) ##########")
        for task in icebreak_tasks:
            rec = manager.icebreak(task, meter)
            ib_records.append(rec)
            ib_messages += rec.messages
            log("")

    log("########## measured ##########")
    records = []
    for task in measure_tasks:
        log(f"--- {task.id} (gold={task.gold}, fixture={task.check.get('kind','none')}) ---")
        records.append(manager.run_task(task, meter))
        log("")

    first_flip = next((i for i, r in enumerate(records, 1)
                       if r.winner != r.counterfactual_winner), None)
    fixtured = [r for r in records if r.delivered is not None]

    row = {
        "run": run_id,
        "condition": condition,
        "tasks": len(records),
        "correct": _count(records, "correct"),
        "messages": sum(r.messages for r in records),
        "unassigned": _count(records, "unassigned"),
        "misawards": _count(records, "misawarded"),
        # `correct` is allocation. `delivered` is whether the awarded work
        # actually ran. They come apart, and the note is where that shows.
        "note": (f"tokens={meter.tokens} llm_calls={meter.calls} seed={seed} "
                 f"delivered={sum(1 for r in fixtured if r.delivered)}/{len(fixtured)} "
                 f"judge_disagree={_count(records, 'judge_disagreed')}"),
    }
    if use_bias:
        row.update({
            "arm": arm,
            "bias_messages": bias.messages,
            "icebreak_messages": ib_messages,
            "unassigned_nobid": _count(records, "unassigned_nobid"),
            "unassigned_veto": _count(records, "unassigned_veto"),
            "veto_hit_gold": _count(records, "veto_hit_gold"),
            "bias_helped": _count(records, "bias_helped"),
            "bias_hurt": _count(records, "bias_hurt"),
            "first_intervention": bias.first_intervention or "",
            "first_flip": first_flip or "",
        })

    log("=== summary ===")
    for k, v in row.items():
        log(f"  {k}: {v}")
    if bias is not None:
        log("  final hypotheses:")
        for n, h in bias.hypotheses.items():
            log(f"    {n}: {h}")
    log.close()

    hist = HERE / "history" / f"{run_id}.json"
    hist.parent.mkdir(exist_ok=True)
    hist.write_text(json.dumps(
        {"run": run_id, "condition": condition, "arm": arm, "seed": seed,
         "model": llm.MODEL, "temperature": llm.TEMPERATURE,
         "veto_threshold": C.VETO_THRESHOLD,
         "final_hypotheses": bias.hypotheses if bias else None,
         "icebreak": [_plain(r) for r in ib_records],
         "records": [_plain(r) for r in records]},
        ensure_ascii=False, indent=2), encoding="utf-8")

    return row


def append_row(path: Path, header, row):
    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in header})


def next_index(path: Path) -> int:
    if not path.exists():
        return 1
    with path.open(encoding="utf-8", newline="") as f:
        return sum(1 for r in csv.reader(f) if any(c.strip() for c in r))


def plan_runs(arms, conditions, runs, base_seed):
    """Assign every run its id and seed up front.

    Ids have to be handed out before anything starts: numbering them from the
    logs on disk works fine one at a time, but with --jobs several workers
    would read the same count and collide. Allocating here makes the plan
    deterministic and lets a worker do nothing but run.
    """
    taken = {p.stem for p in (HERE / "logs").glob("*.txt")}
    plan = []
    for arm in arms:
        use_bias, _, filename = ARMS[arm]
        pool = C.BIAS_CONDITIONS if use_bias else C.CORE_CONDITIONS
        for condition in (conditions or pool):
            if condition not in pool:
                continue
            stem = f"{condition}-{arm.replace('+', '_')}-run"
            k = 1
            for _ in range(runs):
                while f"{stem}{k}" in taken:      # never reuse a finished run's id
                    k += 1
                run_id = f"{stem}{k}"
                taken.add(run_id)
                # crc32, not hash(): Python randomises string hashing per
                # process, so hash() would hand the same run a different seed
                # on every invocation and quietly defeat the point of seeding.
                seed = base_seed + (zlib.crc32(run_id.encode()) % 10_000)
                plan.append((condition, arm, run_id, seed, HERE / filename,
                             BIAS_HEADER if use_bias else HEADER))
    return plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", nargs="*", default=list(ARMS), choices=list(ARMS))
    ap.add_argument("--conditions", nargs="*", default=None)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--seed", type=int, default=20260921,
                    help="base seed; each run derives its own from this and its id")
    ap.add_argument("--jobs", type=int, default=1,
                    help="runs to execute concurrently. Runs are independent by "
                         "construction -- Bias memory never crosses a run boundary "
                         "-- so this changes wall clock, not results. Raise it and "
                         "the endpoint may start rate-limiting; those land as "
                         "crashed rows, which is visible rather than silent.")
    args = ap.parse_args()

    icebreak_tasks, measure_tasks = load_tasks()
    (HERE / "logs").mkdir(exist_ok=True)

    plan = plan_runs(args.arms, args.conditions, args.runs, args.seed)
    print(f"planned {len(plan)} run(s), {args.jobs} at a time:")
    for condition, arm, run_id, seed, out, _ in plan:
        print(f"  {run_id:34} {arm:8} seed={seed} -> {out.name}")

    write_lock = threading.Lock()
    quiet = args.jobs > 1
    done = 0

    def execute(item):
        condition, arm, run_id, seed, out, header = item
        try:
            row = one_run(condition, arm, icebreak_tasks, measure_tasks,
                          run_id, seed, quiet=quiet)
        except Exception as e:
            # A crashed run stays in the table with blank counts. Deleting it
            # would hide a failure mode that is part of the result.
            traceback.print_exc()
            row = {"condition": condition, "arm": arm, "tasks": "", "correct": "",
                   "messages": "", "unassigned": "", "misawards": "",
                   "note": f"CRASH {type(e).__name__}: {e}".replace("\n", " ")[:300]}
        with write_lock:
            row["run"] = next_index(out)
            append_row(out, header, row)
        return run_id, out, row

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        for run_id, out, row in ex.map(execute, plan):
            done += 1
            print(f"[{done}/{len(plan)}] {run_id:34} -> {out.name}: "
                  f"correct={row.get('correct')} msgs={row.get('messages')} "
                  f"unassigned={row.get('unassigned')} misawards={row.get('misawards')} "
                  f"| {row.get('note', '')}")


if __name__ == "__main__":
    main()
