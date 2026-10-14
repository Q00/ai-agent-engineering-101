"""Week 02 — Plan-then-Execute 만 추가로 실행하는 러너.

`run_ab.py` 는 react 를 먼저 3회 돌린 뒤 plan_exec 를 돌린다. 그 순서 때문에
1차 제출 때 react 가 무료 티어의 하루 호출 한도를 먼저 소진했고, plan_exec
차례인 10~12번 런이 전부 429 로 죽었다. 그래서 최종 코드 상태에서 plan_exec
쪽 유효 데이터가 없었다.

이 스크립트는 그 짝을 맞추기 위한 것이다. 모델, 태스크, 도구, 하네스 코드는
`run_ab.py` 와 완전히 같은 것을 쓰고 (읽는 곳도 같은 `TASK.md`, 판정도 같은
`judge`), plan_exec 만 돌려 호출 수를 줄인다. 기존 행은 건드리지 않고
`results.csv` 에 덧붙인다.

사용법: python run_plan_exec.py [--runs 3]
"""
import argparse
import csv
import time
from pathlib import Path

from harness_plan_execute import run_plan_execute
from run_ab import HEADER, judge, read_task


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    task, expected = read_task()
    Path("logs").mkdir(exist_ok=True)
    new_file = not Path("results.csv").exists()
    run_no = 0
    if not new_file:
        with open("results.csv", encoding="utf-8") as f:
            run_no = sum(1 for _ in f) - 1

    with open("results.csv", "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HEADER)
        for _ in range(args.runs):
            run_no += 1
            lines = []

            def log(msg, _lines=lines):
                print(msg, flush=True)
                _lines.append(str(msg))

            t0 = time.time()
            try:
                answer, meter, replans = run_plan_execute(task, log=log)
                note = f"replans={replans}"
            except Exception as e:
                answer, meter = "", None
                note = f"crash: {type(e).__name__}: {e}"
                log(note)
            success = judge(answer, expected)
            log(f"[final] {answer.strip()[:300]}")
            log(f"[judge] expected={expected!r} -> {'O' if success else 'X'} "
                f"({time.time() - t0:.1f}s)")

            Path("logs", f"plan_exec-{run_no:02d}.txt").write_text(
                "\n".join(lines) + "\n", encoding="utf-8")
            w.writerow([run_no, "plan_exec", "O" if success else "X",
                        meter.tokens if meter else "",
                        meter.iters if meter else "",
                        meter.interventions if meter else "", note])
            f.flush()
    print("\nresults.csv updated")


if __name__ == "__main__":
    main()
