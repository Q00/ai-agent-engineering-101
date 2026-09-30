"""한 조건을 N번 돌리고 results.csv와 logs/에 기록한다.

블록 실행이다. 조건 하나씩 끊어 돌린다.

    python run.py --condition baseline --runs 3
    python run.py --condition homogeneous --runs 3
    python run.py --condition overconfident --runs 3

results.csv에 append하고 run 번호는 파일의 마지막 번호에서 이어간다. 9런을 한
프로세스로 묶지 않는 이유는 162콜 중간에 인증이 끊기거나 한도에 걸렸을 때
남은 조건만 다시 돌리기 위해서다. 중단된 런도 지우지 않고 카운트를 비운 채
note에 사유를 적는다.
"""
import argparse
import csv
import datetime
import json
import subprocess
from pathlib import Path

from contractor import build_team
from manager import run_round
from model import MODEL, ModelError, Meter

HERE = Path(__file__).parent
RESULTS = HERE / "results.csv"
LOGS = HERE / "logs"
HEADER = ["run", "condition", "tasks", "correct", "messages",
          "unassigned", "misawards", "note"]


def cli_version() -> str:
    try:
        return subprocess.run(["claude", "--version"], capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except Exception:
        return "unknown"


def next_run_number() -> int:
    if not RESULTS.is_file():
        return 1
    with RESULTS.open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.reader(f)][1:]
    used = [int(r[0]) for r in rows if r and r[0].strip().isdigit()]
    return max(used) + 1 if used else 1


def append_row(row: list):
    new = not RESULTS.is_file()
    with RESULTS.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(HEADER)
        w.writerow(row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", required=True,
                    choices=["baseline", "homogeneous", "overconfident"])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--tasks", default=str(HERE / "tasks.json"))
    args = ap.parse_args()

    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8"))
    LOGS.mkdir(exist_ok=True)
    version = cli_version()

    for _ in range(args.runs):
        n = next_run_number()
        team = build_team(args.condition)
        meter = Meter()
        path = LOGS / f"run-{n:02d}-{args.condition}.txt"
        started = datetime.datetime.now().astimezone().isoformat(timespec="seconds")

        with path.open("w", encoding="utf-8") as fh:
            def log(line=""):
                print(line)
                fh.write(str(line) + "\n")
                fh.flush()

            # 강의노트 요구: 로그 첫머리에 provider, 모델, temperature.
            # 직접 지정할 수 없으면 추측하지 말고 제한과 도구·버전을 적는다.
            log(f"run: {n}")
            log(f"condition: {args.condition}")
            log(f"provider: Anthropic subscription via `claude -p`")
            log(f"tool: Claude Code CLI {version}")
            log(f"model: {MODEL}")
            log(f"temperature: 직접 설정 불가, 내부 값 미확인")
            log(f"tasks: {Path(args.tasks).name} ({len(tasks)} tasks)")
            log(f"contractors: " + ", ".join(
                f"{c.name}[{'overconfident' if c.overconfident else 'plain'}] {c.skill}"
                for c in team))
            log(f"started: {started}")
            log("-" * 72)

            try:
                r = run_round(tasks, team, meter, log=log)
            except ModelError as e:
                note = f"crashed: {e}"
                log(f"\n!! {note}")
                append_row([n, args.condition, len(tasks), "", "", "", "", note])
                print(f"[run {n}] CRASHED -> {path.name}")
                continue

            note = (f"parse_fails={r.parse_fails} fences={r.fences} "
                    f"calls={meter.calls} tokens={meter.tokens}")
            log("-" * 72)
            log(f"correct={r.correct} messages={r.messages} "
                f"unassigned={r.unassigned} misawards={r.misawards}")
            log(note)

            append_row([n, args.condition, r.tasks, r.correct, r.messages,
                        r.unassigned, r.misawards, note])
            print(f"[run {n}] correct={r.correct}/{r.tasks} -> {path.name}")


if __name__ == "__main__":
    main()
