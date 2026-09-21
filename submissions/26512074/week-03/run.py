import argparse
import csv
import os

from contractor import (
    Contractor,
    MODEL,
    TEMPERATURE,
)
from manager import (
    load_tasks,
    run_round,
    CONFIDENCE_WEIGHT,
    HISTORY_WEIGHT,
)
from trajectory import load_history


TASK_FILE = "tasks.json"
RESULT_FILE = "results.csv"


def make_team(condition):
    if condition == "baseline":
        return [
            Contractor(
                name="A",
                skill="calculation",
            ),
            Contractor(
                name="B",
                skill="writing",
            ),
            Contractor(
                name="C",
                skill="coding",
            ),
        ]

    if condition == "homogeneous":
        return [
            Contractor(
                name="A",
                skill="general problem solving",
            ),
            Contractor(
                name="B",
                skill="general problem solving",
            ),
            Contractor(
                name="C",
                skill="general problem solving",
            ),
        ]

    if condition == "overconfident":
        return [
            Contractor(
                name="A",
                skill="calculation",
            ),
            Contractor(
                name="B",
                skill="writing",
            ),
            Contractor(
                name="C",
                skill="coding",
                overconfident=True,
            ),
        ]

    raise ValueError(
        f"Unknown condition: {condition}"
    )


def append_result(
    run_number,
    condition,
    result,
):
    file_exists = os.path.exists(
        RESULT_FILE
    )

    with open(
        RESULT_FILE,
        "a",
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow(
                [
                    "run",
                    "condition",
                    "tasks",
                    "correct",
                    "messages",
                    "unassigned",
                    "misawards",
                    "note",
                ]
            )

        note = (
            f"parse_fails="
            f"{result.parse_fails}; "
            f"model={MODEL}; "
            f"temperature={TEMPERATURE}; "
            f"selection=confidence_"
            f"{CONFIDENCE_WEIGHT}_history_"
            f"{HISTORY_WEIGHT}"
        )

        writer.writerow(
            [
                run_number,
                condition,
                result.tasks,
                result.correct,
                result.messages,
                result.unassigned,
                result.misawards,
                note,
            ]
        )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--condition",
        required=True,
        choices=[
            "baseline",
            "homogeneous",
            "overconfident",
        ],
    )

    parser.add_argument(
        "--run",
        required=True,
        type=int,
    )

    args = parser.parse_args()

    tasks = load_tasks(
        TASK_FILE
    )

    # 이번 run 시작 시점의 history를 snapshot으로 저장
    history_snapshot = load_history()

    print("================================")
    print("Contract Net Week 03")
    print(
        f"condition={args.condition}"
    )
    print(
        f"run={args.run}"
    )
    print(
        f"model={MODEL}"
    )
    print(
        f"temperature={TEMPERATURE}"
    )
    print(
        f"selection="
        f"{CONFIDENCE_WEIGHT:.1f} confidence + "
        f"{HISTORY_WEIGHT:.1f} history"
    )
    print(
        f"history_records="
        f"{len(history_snapshot)}"
    )
    print("================================")

    team = make_team(
        args.condition
    )

    result = run_round(
        tasks,
        team,
        history_snapshot=history_snapshot,
    )

    append_result(
        args.run,
        args.condition,
        result,
    )

    print()
    print(
        f"correct={result.correct}"
    )
    print(
        f"messages={result.messages}"
    )
    print(
        f"unassigned={result.unassigned}"
    )
    print(
        f"misawards={result.misawards}"
    )
    print(
        f"parse_fails={result.parse_fails}"
    )


if __name__ == "__main__":
    main()