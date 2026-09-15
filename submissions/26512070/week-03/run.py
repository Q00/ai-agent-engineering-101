"""Runner. Drives the arms, writes results.csv, results_bias.csv, logs/, history/.

    python run.py                          # every arm, 3 runs each
    python run.py --arms core --runs 1
    python run.py --arms bias bias+ib

Three arms, because Bias and icebreaking are two separate interventions and
folding them together would make it impossible to say which one moved a metric:

    core      plain contract net. Bias off, no icebreaking.   -> results.csv
    bias      Bias advises and can veto. No icebreaking, so   -> results_bias.csv
              Bias only ever sees the winner execute.
    bias+ib   Bias, plus an icebreaking round first in which  -> results_bias.csv
              every contractor executes every warm-up task.

`results.csv` holds the core arm alone and keeps the fixed header, so its
`messages` column means what it means for everyone else.
"""
import argparse
import csv
import json
import traceback
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
    in UTF-8, so a Korean log survives a cp949 console redirect on Windows."""

    def __init__(self, path: Path):
        self.fh = path.open("w", encoding="utf-8")

    def __call__(self, *parts):
        line = " ".join(str(p) for p in parts)
        self.fh.write(line + "\n")
        self.fh.flush()
        try:
            print(line)
        except UnicodeEncodeError:
            print(line.encode("ascii", "replace").decode("ascii"))

    def close(self):
        self.fh.close()


def load_tasks():
    data = json.loads((HERE / "tasks.json").read_text(encoding="utf-8"))
    tasks = [Task(t["id"], t["desc"], t["gold"], t.get("phase", "measure"))
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


def one_run(condition, arm, icebreak_tasks, measure_tasks, run_id):
    use_bias, use_ib, _ = ARMS[arm]
    log = Tee(HERE / "logs" / f"{run_id}.txt")
    meter = llm.Meter()

    contractors = build(C.roster(condition))
    # Fresh Bias for every run. Memory dies here, so three runs of an arm are
    # three replicates rather than one learning curve. See bias.py.
    bias = Bias([c.name for c in contractors]) if use_bias else None
    manager = Manager(contractors, bias=bias, log=log)

    log(f"=== run {run_id} ===")
    log(f"provider={llm.PROVIDER} model={llm.MODEL} temperature={llm.TEMPERATURE}")
    log(f"condition={condition} arm={arm} bias={'on' if use_bias else 'off'} "
        f"icebreak={'on' if use_ib else 'off'}")
    log(f"veto_threshold={C.VETO_THRESHOLD} adj_range=({C.ADJ_MIN},{C.ADJ_MAX})")
    log(f"contractors={[c.name for c in contractors]}")
    log(f"icebreak_tasks={[t.id for t in icebreak_tasks] if use_ib else []}")
    log(f"measure_tasks={[t.id for t in measure_tasks]}")
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
        log(f"--- {task.id} (gold={task.gold}) ---")
        records.append(manager.run_task(task, meter))
        log("")

    first_flip = next((i for i, r in enumerate(records, 1)
                       if r.winner != r.counterfactual_winner), None)

    row = {
        "run": run_id,
        "condition": condition,
        "tasks": len(records),
        "correct": sum(r.correct for r in records),
        "messages": sum(r.messages for r in records),
        "unassigned": sum(r.unassigned for r in records),
        "misawards": sum(r.misawarded for r in records),
        "note": f"tokens={meter.tokens} llm_calls={meter.calls}",
    }
    if use_bias:
        row.update({
            "arm": arm,
            "bias_messages": bias.messages,
            "icebreak_messages": ib_messages,
            "unassigned_nobid": sum(r.unassigned_nobid for r in records),
            "unassigned_veto": sum(r.unassigned_veto for r in records),
            "veto_hit_gold": sum(r.veto_hit_gold for r in records),
            "bias_helped": sum(r.bias_helped for r in records),
            "bias_hurt": sum(r.bias_hurt for r in records),
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
        {"run": run_id, "condition": condition, "arm": arm,
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", nargs="*", default=list(ARMS), choices=list(ARMS))
    ap.add_argument("--conditions", nargs="*", default=None)
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    icebreak_tasks, measure_tasks = load_tasks()
    (HERE / "logs").mkdir(exist_ok=True)

    for arm in args.arms:
        use_bias, _, filename = ARMS[arm]
        pool = C.BIAS_CONDITIONS if use_bias else C.CORE_CONDITIONS
        for condition in (args.conditions or pool):
            if condition not in pool:
                continue
            out = HERE / filename
            header = BIAS_HEADER if use_bias else HEADER
            for i in range(args.runs):
                n = next_index(out)
                run_id = f"{condition}-{arm.replace('+', '_')}-run{i + 1}"
                print(f"\n########## {run_id} ##########")
                try:
                    row = one_run(condition, arm, icebreak_tasks,
                                  measure_tasks, run_id)
                    row["run"] = n
                except Exception as e:
                    # A crashed run stays in the table with blank counts.
                    # Deleting it would hide a failure mode that is a result.
                    traceback.print_exc()
                    row = {"run": n, "condition": condition, "arm": arm,
                           "tasks": "", "correct": "", "messages": "",
                           "unassigned": "", "misawards": "",
                           "note": f"CRASH {type(e).__name__}: {e}".replace("\n", " ")[:300]}
                append_row(out, header, row)
                print(f"-> {out.name}: {row}")


if __name__ == "__main__":
    main()
