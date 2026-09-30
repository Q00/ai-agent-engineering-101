"""Week 02 starter — run the A/B experiment and record results.csv.

Usage: python run_ab.py [--runs 3]

Reads the task and the success criterion from TASK.md, runs each harness
--runs times, judges every run, appends one line per run to results.csv,
and saves each run's console output under logs/. Failed runs are kept:
they are data.
"""
import argparse
import csv
import inspect
import os
import shlex
import re
import time
from pathlib import Path

import harness_plan_execute
import harness_react
import tools_shared
from harness_plan_execute import run_plan_execute
from harness_react import run_react

HEADER = ["run", "harness", "success", "tokens", "iters", "interventions", "note"]

# lab 4번: "실행마다 프롬프트, 도구 집합, max_steps 같은 실험 조건을 기록한다".
# 조건을 손으로 옮겨 적으면 코드와 어긋나므로, 실제 쓰이는 값을 실행 시점에 읽어
# 각 로그 첫머리에 찍는다. 상한은 함수 시그니처의 기본값에서 가져온다.
HARNESS_MODULE = {"react": harness_react, "plan_exec": harness_plan_execute}
SYSTEM_ATTRS = ("SYSTEM", "SYSTEM_PLAN", "SYSTEM_EXEC")


def conditions_block(name: str, fn, task: str, expected: str) -> str:
    """이 런의 실험 조건 전문. 로그 파일 맨 앞에 들어간다."""
    mod = HARNESS_MODULE[name]
    out = ["# " + "=" * 70,
           "# 실험 조건 (이 런에 실제로 쓰인 값)",
           "# " + "=" * 70,
           f"# harness            : {name} ({fn.__module__}.{fn.__name__})",
           f"# model              : {tools_shared.MODEL}",
           f"# 호출 경로          : claude -p (구독 자격증명, API 키 미사용)",
           f"# claude -p 플래그   : {shlex.join(tools_shared._BASE_FLAGS)}",
           f"# 도구 집합          : {', '.join(sorted(tools_shared.TOOLS_IMPL))}"
           + ("  (+ finish: 하네스가 가로채는 종료 신호)" if name == "react" else ""),
           f"# IRREVERSIBLE       : {sorted(mod.IRREVERSIBLE) or '(빈 집합)'}"]

    for pname, param in inspect.signature(fn).parameters.items():
        if param.default is not inspect.Parameter.empty and pname != "log":
            out.append(f"# {pname:<18} : {param.default}")

    out += [f"# task               : {task}",
            f"# expected           : {expected}",
            "#",
            "# --- 시스템 프롬프트 전문 ---"]
    for attr in SYSTEM_ATTRS:
        text = getattr(mod, attr, None)
        if text is None:
            continue
        out.append(f"# [{attr}] ({len(text)}자)")
        out += ["#   " + line for line in text.splitlines()]
    out += ["# " + "=" * 70, ""]
    return "\n".join(out)


def read_task(path="TASK.md"):
    text = Path(path).read_text(encoding="utf-8")
    task = re.search(r"^task:\s*(.+)$", text, flags=re.M)
    expected = re.search(r"^expected:\s*(.+)$", text, flags=re.M)
    if not task or not expected:
        raise SystemExit("TASK.md needs a 'task:' line and an 'expected:' line")
    return task.group(1).strip(), expected.group(1).strip()


def judge(answer: str, expected: str) -> bool:
    """Success = the expected string appears in the final answer. Fix the
    criterion in TASK.md before running; do not loosen it afterwards."""
    return expected.lower() in (answer or "").lower()


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
        for name, fn in (("react", run_react), ("plan_exec", run_plan_execute)):
            for _ in range(args.runs):
                run_no += 1
                lines = []

                def log(msg, _lines=lines):
                    print(msg)
                    _lines.append(str(msg))

                t0 = time.time()
                note = ""
                try:
                    out = fn(task, log=log)
                    answer, meter = out[0], out[1]
                    if name == "plan_exec":
                        note = f"replans={out[2]}"
                except Exception as e:            # a crash is a failed run, not a lost run
                    answer, meter, note = "", None, f"crash: {type(e).__name__}: {e}"
                    log(note)
                success = judge(answer, expected)
                log(f"[final] {answer.strip()[:300]}")
                log(f"[judge] expected={expected!r} -> {'O' if success else 'X'} "
                    f"({time.time() - t0:.1f}s)")

                Path("logs", f"{name}-{run_no:02d}.txt").write_text(
                    conditions_block(name, fn, task, expected)
                    + "\n".join(lines) + "\n", encoding="utf-8")
                w.writerow([run_no, name, "O" if success else "X",
                            meter.tokens if meter else "",
                            meter.iters if meter else "",
                            meter.interventions if meter else "", note])
                f.flush()
    print("\nresults.csv updated;", os.path.abspath("results.csv"))


if __name__ == "__main__":
    main()
