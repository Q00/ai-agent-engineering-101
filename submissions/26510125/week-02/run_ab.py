"""Week 02 assignment — run the A/B experiment and record results.csv.

Usage: python run_ab.py [--runs 3]

Same shape as the lab runner: read TASK.md, run each harness --runs times,
judge every run against the expected substring, append to results.csv, save
one log per run. Failed runs are kept as rows, not discarded.
"""
import argparse
import csv
import re
import time
from pathlib import Path

from harness_plan_execute import run_plan_execute
from harness_react import run_react

CSV_HEADER = ["run", "harness", "success", "tokens", "iters", "interventions", "note"]
HARNESSES = [("react", run_react), ("plan_exec", run_plan_execute)]


def load_task(path: str = "TASK.md") -> tuple[str, str]:
    text = Path(path).read_text(encoding="utf-8")
    task = re.search(r"^task:\s*(.+)$", text, flags=re.M)
    expected = re.search(r"^expected:\s*(.+)$", text, flags=re.M)
    if not task or not expected:
        raise SystemExit("TASK.md needs a 'task:' line and an 'expected:' line")
    return task.group(1).strip(), expected.group(1).strip()


def succeeded(answer: str, expected: str) -> bool:
    return expected.lower() in (answer or "").lower()


def next_run_number(csv_path: Path) -> int:
    if not csv_path.exists():
        return 0
    with csv_path.open(encoding="utf-8") as f:
        return sum(1 for _ in f) - 1


def run_once(harness_fn, task: str, run_no: int, harness_name: str):
    captured = []

    def log(msg, _captured=captured):
        print(msg)
        _captured.append(str(msg))

    started = time.time()
    note = ""
    try:
        result = harness_fn(task, log=log)
        answer, meter = result[0], result[1]
        if harness_name == "plan_exec":
            note = f"replans={result[2]}"
    except Exception as e:                  # a crash is a failed run, not a lost run
        answer, meter, note = "", None, f"crash: {type(e).__name__}: {e}"
        log(note)
    return answer, meter, note, captured, time.time() - started


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    task, expected = load_task()
    Path("logs").mkdir(exist_ok=True)
    csv_path = Path("results.csv")
    is_new = not csv_path.exists()
    run_no = next_run_number(csv_path)

    with csv_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(CSV_HEADER)
        for name, fn in HARNESSES:
            for _ in range(args.runs):
                run_no += 1
                answer, meter, note, captured, elapsed = run_once(fn, task, run_no, name)
                success = succeeded(answer, expected)
                captured.append(f"[final] {(answer or '').strip()[:300]}")
                captured.append(f"[judge] expected={expected!r} -> "
                                 f"{'O' if success else 'X'} ({elapsed:.1f}s)")
                print(captured[-2])
                print(captured[-1])

                Path("logs", f"{name}-{run_no:02d}.txt").write_text(
                    "\n".join(captured) + "\n", encoding="utf-8")
                writer.writerow([run_no, name, "O" if success else "X",
                                 meter.tokens if meter else "",
                                 meter.iters if meter else "",
                                 meter.interventions if meter else "", note])
                f.flush()

    print("\nresults.csv updated:", csv_path.resolve())


if __name__ == "__main__":
    main()
