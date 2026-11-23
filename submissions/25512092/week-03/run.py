"""실행기. 조건마다 여러 번 실행하고 results.csv 와 logs/ 에 기록한다.

사용법 (week-03 폴더 안에서)
  python run.py                                  세 조건 x 3회 = 9회
  python run.py --condition baseline --runs 1    한 조건만
  python run.py --smoke --tag after-boundary     태스크 3개로 동작 확인, smoke/ 에만 저장

실행 순서는 baseline, homogeneous, overconfident 를 번갈아 돈다.
중간에 오류나 Ctrl+C 로 멈춘 실행도 results.csv 에 빈 칸 + note 로 남는다.
"""
import argparse
import csv
import datetime
import json
import traceback
from pathlib import Path

import llm
from contractor import CONDITIONS, make_team
from manager import run_round

HERE = Path(__file__).resolve().parent
TASKS_PATH = HERE / "tasks.json"
RESULTS_PATH = HERE / "results.csv"
LOG_DIR = HERE / "logs"
SMOKE_DIR = HERE / "smoke"
HEADER = ["run", "condition", "tasks", "correct", "messages",
          "unassigned", "misawards", "note"]


def load_tasks():
    with open(TASKS_PATH, encoding="utf-8") as f:
        return json.load(f)


def next_run_number() -> int:
    """results.csv 에 이미 있는 run 번호 다음 번호."""
    if not RESULTS_PATH.exists():
        return 1
    with open(RESULTS_PATH, encoding="utf-8", newline="") as f:
        runs = [int(row["run"]) for row in csv.DictReader(f)
                if (row.get("run") or "").isdigit()]
    return max(runs, default=0) + 1


def append_row(row):
    is_new = not RESULTS_PATH.exists()
    with open(RESULTS_PATH, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(HEADER)
        writer.writerow(row)


def one_line(text: str) -> str:
    return " ".join(str(text).split())


def one_run(run_no: int, condition: str, tasks, save: bool = True, log_path=None):
    """save=True 면 logs/ 와 results.csv 에 기록. smoke 는 save=False + log_path 로 smoke/ 에만 남긴다."""
    meter = llm.Meter()
    team = make_team(condition)
    if save:
        LOG_DIR.mkdir(exist_ok=True)
        log_path = LOG_DIR / f"run-{run_no:02d}-{condition}.txt"
    log_file = open(log_path, "a", encoding="utf-8") if log_path else None

    def log(line=""):
        print(line)
        if log_file:
            log_file.write(line + "\n")
            log_file.flush()

    try:
        log(llm.settings_line())                                  # 첫 줄: provider, model, temperature
        log(f"run={run_no} condition={condition} tasks={len(tasks)} "
            f"started={datetime.datetime.now().isoformat(timespec='seconds')}")
        for c in team:
            log(f"system[{c.name}]: {c.system_prompt()}")

        try:
            r = run_round(tasks, team, meter, log)
        except (Exception, KeyboardInterrupt) as e:
            reason = "interrupted by user (Ctrl+C)" if isinstance(e, KeyboardInterrupt) \
                else f"{type(e).__name__}: {one_line(e)}"
            log("")
            log(f"[crash] {reason}")
            if not isinstance(e, KeyboardInterrupt):
                log(traceback.format_exc())
            if save:
                append_row([run_no, condition, "", "", "", "", "", 
                            f"crashed: {reason}; calls={meter.calls}; tokens={meter.tokens}"])
            if isinstance(e, KeyboardInterrupt):
                raise
            return

        note = f"parse_fails={r.parse_fails}; calls={meter.calls}; tokens={meter.tokens}"
        log("")
        log(f"[summary] tasks={r.tasks} correct={r.correct} messages={r.messages} "
            f"unassigned={r.unassigned} misawards={r.misawards} {note}")
        if save:
            append_row([run_no, condition, r.tasks, r.correct, r.messages,
                        r.unassigned, r.misawards, note])
    finally:
        if log_file:
            log_file.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--condition", choices=CONDITIONS + ("all",), default="all")
    p.add_argument("--runs", type=int, default=3, help="조건마다 실행 횟수")
    p.add_argument("--smoke", action="store_true",
                   help="gold A/B/C 태스크 하나씩만 실행. results.csv 는 건드리지 않고 smoke/ 에만 저장")
    p.add_argument("--tag", default="", help="smoke 파일 이름에 붙일 설명 (예: after-boundary)")
    args = p.parse_args()

    tasks = load_tasks()
    conditions = CONDITIONS if args.condition == "all" else (args.condition,)

    if args.smoke:
        picked = {}
        for t in tasks:
            picked.setdefault(t["gold"], t)
        SMOKE_DIR.mkdir(exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"smoke-{stamp}" + (f"-{args.tag}" if args.tag else "") + ".txt"
        path = SMOKE_DIR / name
        for cond in conditions:
            one_run(0, cond, list(picked.values()), save=False, log_path=path)
        print(f"\n[saved] {path}")
        return

    for _ in range(args.runs):
        for cond in conditions:
            one_run(next_run_number(), cond, tasks)


if __name__ == "__main__":
    main()