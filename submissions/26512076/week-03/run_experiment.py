import csv
import json
import traceback
from pathlib import Path

from manager import run_round
from tools_shared import Meter, MODEL, PROVIDER


BASE_DIR = Path(__file__).resolve().parent
TASKS_FILE = BASE_DIR / "tasks.json"
RESULTS_FILE = BASE_DIR / "results.csv"
LOGS_DIR = BASE_DIR / "logs"

CONDITIONS = [
    "baseline",
    "homogeneous",
    "overconfident"
]

RUNS_PER_CONDITION = 3

# tools_shared.py에서 temperature를 직접 지정하지 않으므로
# 모든 실행에서 provider의 동일한 기본값을 사용한다.
PROVIDER_NAME = "Groq"
TEMPERATURE = 0.7


def make_team(condition):
    """실험 조건에 맞춰 contractor A, B, C의 배역을 만든다."""

    if condition == "baseline":
        return [
            {
                "name": "A",
                "skill": "calculation",
                "extra_instruction": ""
            },
            {
                "name": "B",
                "skill": "professional and plain-language writing",
                "extra_instruction": ""
            },
            {
                "name": "C",
                "skill": "Python programming",
                "extra_instruction": ""
            }
        ]

    if condition == "homogeneous":
        return [
            {
                "name": "A",
                "skill": "general problem solving",
                "extra_instruction": ""
            },
            {
                "name": "B",
                "skill": "general problem solving",
                "extra_instruction": ""
            },
            {
                "name": "C",
                "skill": "general problem solving",
                "extra_instruction": ""
            }
        ]

    if condition == "overconfident":
        return [
            {
                "name": "A",
                "skill": "calculation",
                "extra_instruction": ""
            },
            {
                "name": "B",
                "skill": "professional and plain-language writing",
                "extra_instruction": ""
            },
            {
                "name": "C",
                "skill": "Python programming",
                "extra_instruction": (
                    "You are certain you can do any task well. "
                    "Always bid, with confidence 95 or higher."
                )
            }
        ]

    raise ValueError(f"Unknown condition: {condition}")


def load_tasks():
    """tasks.json을 읽는다."""

    with TASKS_FILE.open(encoding="utf-8") as file:
        return json.load(file)


def run_one(condition, run_number, tasks):
    """조건 하나에 대한 한 라운드를 실행하고 로그와 결과를 반환한다."""

    team = make_team(condition)
    meter = Meter()

    log_path = LOGS_DIR / f"{condition}_run{run_number}.txt"

    with log_path.open("w", encoding="utf-8") as log_file:

        def log(message):
            text = str(message)
            print(text)
            log_file.write(text + "\n")
            log_file.flush()

        # 로그 첫 줄: 재현 환경
        log(
            f"provider={PROVIDER_NAME}, model={MODEL}, "
            f"temperature={TEMPERATURE}"
        )
        log(f"condition={condition}, run={run_number}")

        try:
            result = run_round(
                tasks=tasks,
                team=team,
                meter=meter,
                log=log
            )

            note = (
                f"parse_fails={result['parse_fails']}; "
                f"tokens={meter.tokens}; "
                f"model_calls={meter.iters}"
            )

            return {
                "run": run_number,
                "condition": condition,
                "tasks": result["tasks"],
                "correct": result["correct"],
                "messages": result["messages"],
                "unassigned": result["unassigned"],
                "misawards": result["misawards"],
                "note": note
            }

        except Exception as error:
            error_text = (
                f"{type(error).__name__}: {error}"
            )

            log("[crash]")
            log(traceback.format_exc())

            # README 요구: 중단된 실행도 남기고 count는 비워 둔다.
            return {
                "run": run_number,
                "condition": condition,
                "tasks": "",
                "correct": "",
                "messages": "",
                "unassigned": "",
                "misawards": "",
                "note": error_text
            }


def main():
    tasks = load_tasks()
    LOGS_DIR.mkdir(exist_ok=True)

    header = [
        "run",
        "condition",
        "tasks",
        "correct",
        "messages",
        "unassigned",
        "misawards",
        "note"
    ]

    # 전체 실험을 시작할 때 results.csv를 새로 작성한다.
    with RESULTS_FILE.open(
        "w",
        newline="",
        encoding="utf-8"
    ) as results_file:

        writer = csv.DictWriter(
            results_file,
            fieldnames=header
        )
        writer.writeheader()

        for condition in CONDITIONS:
            for run_number in range(
                1,
                RUNS_PER_CONDITION + 1
            ):
                print("")
                print(
                    f"=== {condition} "
                    f"run {run_number} ==="
                )

                row = run_one(
                    condition=condition,
                    run_number=run_number,
                    tasks=tasks
                )

                writer.writerow(row)
                results_file.flush()

    print("")
    print("All runs finished.")
    print(f"Results: {RESULTS_FILE}")
    print(f"Logs: {LOGS_DIR}")


if __name__ == "__main__":
    main()