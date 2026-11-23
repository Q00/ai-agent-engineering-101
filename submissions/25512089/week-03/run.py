import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from contract_net import ContractNet, build_contractors


HERE = Path(__file__).resolve().parent
TASKS_PATH = HERE / "tasks.json"
RESULTS_PATH = HERE / "results.csv"
LOGS_DIR = HERE / "logs"

HEADER = [
    "run",
    "condition",
    "tasks",
    "correct",
    "messages",
    "unassigned",
    "misawards",
    "note",
]
CONDITIONS = ("baseline", "homogeneous", "overconfident")
TEMPERATURE = 0.0


def load_tasks():
    return json.loads(TASKS_PATH.read_text(encoding="utf-8"))


def ensure_results():
    if not RESULTS_PATH.exists():
        with RESULTS_PATH.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(HEADER)


def next_run_number():
    ensure_results()
    with RESULTS_PATH.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    return max(1, len(rows))


def append_result(row):
    with RESULTS_PATH.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)


def emit(lines, text=""):
    print(text)
    lines.append(text)


def run_once(run_no, condition, model):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    log_path = LOGS_DIR / f"run-{run_no:02d}-{condition}-{timestamp}.txt"

    lines = []
    tasks = load_tasks()
    contractors = build_contractors(condition)
    net = ContractNet(model=model, temperature=TEMPERATURE)

    correct = 0
    messages = 0
    unassigned = 0
    misawards = 0
    unparseable = 0

    emit(lines, f"run={run_no}")
    emit(lines, f"condition={condition}")
    emit(lines, f"provider=OpenAI-compatible API")
    emit(lines, f"model={model}")
    emit(lines, f"temperature={TEMPERATURE}")
    emit(lines, "")

    try:
        for task in tasks:
            emit(lines, f"TASK {task['id']} gold={task['gold']}")
            emit(lines, f"DESC {task['desc']}")

            bids = []
            for contractor in contractors:
                emit(lines, f"ANNOUNCE -> {contractor.name}: {task['desc']}")
                messages += 1

                bid = net.ask_for_bid(contractor, task)
                bids.append(bid)
                messages += 1

                if not bid.parsed:
                    unparseable += 1

                emit(
                    lines,
                    "BID <- "
                    f"{contractor.name}: parsed={bid.parsed} "
                    f"bid={bid.bid} confidence={bid.confidence} "
                    f"reason={bid.reason}"
                )
                emit(lines, f"RAW {contractor.name}: {bid.raw!r}")

            winner = net.award(bids)

            if winner is None:
                unassigned += 1
                emit(lines, "AWARD: none")
            else:
                messages += 1
                emit(lines, f"AWARD -> {winner}")
                if winner == task["gold"]:
                    correct += 1
                else:
                    misawards += 1

            emit(lines, "")

        note = f"unparseable_bids={unparseable}"
        append_result([
            run_no,
            condition,
            len(tasks),
            correct,
            messages,
            unassigned,
            misawards,
            note,
        ])

    except Exception as e:
        note = f"crash: {type(e).__name__}: {e}"
        emit(lines, note)
        append_result([run_no, condition, "", "", "", "", "", note])

    finally:
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"[saved] {log_path.name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--condition",
        choices=CONDITIONS,
        help="Run one condition only."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run baseline, homogeneous, and overconfident."
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Runs per selected condition."
    )
    args = parser.parse_args()

    if not args.all and not args.condition:
        parser.error("choose --all or --condition")

    if args.runs < 1:
        parser.error("--runs must be >= 1")

    model = os.environ.get("AGENT_MODEL")
    if not model:
        print("ERROR: AGENT_MODEL is not set.", file=sys.stderr)
        return 2

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY is not set.", file=sys.stderr)
        return 2

    # OpenAI() reads OPENAI_BASE_URL automatically.
    ensure_results()

    selected = CONDITIONS if args.all else (args.condition,)
    run_no = next_run_number()

    for condition in selected:
        for _ in range(args.runs):
            run_once(run_no, condition, model)
            run_no += 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
