"""러너 — 세 형식 × 3회 × 시나리오 전체.

run 하나는 "형식 하나로 시나리오 전체를 한 번"이고 로그 파일 하나에 대응한다.
results.csv에 이미 있는 (run, scenario) 쌍은 건너뛰므로 중단된 실행을 이어서 돌릴 수 있다.
죽은 에피소드는 지우지 않고 수치를 비운 채 note에 사유를 적는다.

  python run_experiment.py                 # 9 run 전부
  python run_experiment.py --workers 1     # 순차 (기본은 3)
  python run_experiment.py --conditions free
"""
from __future__ import annotations

import argparse
import csv
import json
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import acl
from negotiate import run_episode

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results.csv"
LOGS = HERE / "logs"
HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price", "correct",
          "violation", "turns", "format_errors", "reader_calls", "note"]
CONDITIONS = ("free", "tagged", "structured")
_lock = threading.Lock()


def done_pairs() -> set[tuple[str, str]]:
    if not RESULTS.is_file():
        return set()
    with RESULTS.open(encoding="utf-8", newline="") as f:
        return {(r["run"], r["scenario"]) for r in csv.DictReader(f)}


def append(row: dict):
    with _lock:
        new = not RESULTS.is_file()
        with RESULTS.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=HEADER)
            if new:
                w.writeheader()
            w.writerow(row)


def do_run(run_id: int, condition: str, scenarios: list[dict], skip: set):
    path = LOGS / f"{condition}-{run_id:02d}.txt"
    lines: list[str] = []
    lines.append(f"provider={acl.PROVIDER} model={acl.MODEL} "
                 f"temperature_requested={acl.TEMPERATURE} max_turns={acl.MAX_TURNS} "
                 f"run={run_id} condition={condition}")
    lines.append("")

    def log(s=""):
        lines.append(s)
        print(f"[{condition}-{run_id:02d}] {s}"[:160])

    for sc in scenarios:
        if (str(run_id), str(sc["id"])) in skip:
            log(f"--- scenario {sc['id']} 건너뜀 (results.csv에 이미 있음)")
            continue
        try:
            ep = run_episode(sc, condition, log)
            note = (f"tokens={ep.meter.in_tokens + ep.meter.out_tokens} calls={ep.meter.calls}"
                    + (f" unresolved_accepts={ep.unresolved_accepts}" if ep.unresolved_accepts else ""))
            append({"run": run_id, "condition": condition, "scenario": sc["id"],
                    "deal_possible": ep.deal_possible, "outcome": ep.outcome,
                    "price": "" if ep.price is None else ep.price, "correct": ep.correct,
                    "violation": ep.violation, "turns": ep.turns,
                    "format_errors": ep.format_errors, "reader_calls": ep.reader_calls,
                    "note": note})
        except Exception as e:  # 죽은 에피소드도 지우지 않는다
            log(f"[crash] scenario {sc['id']}: {e!r}")
            log(traceback.format_exc())
            append({"run": run_id, "condition": condition, "scenario": sc["id"],
                    "deal_possible": int(sc["reserve"] <= sc["budget"]), "outcome": "",
                    "price": "", "correct": "", "violation": "", "turns": "",
                    "format_errors": "", "reader_calls": "",
                    "note": f"crashed: {type(e).__name__}: {e}"})

    lines.insert(1, f"temperature_accepted_by_model={acl.TEMPERATURE_STATE['accepted']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path.name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--conditions", nargs="*", default=list(CONDITIONS))
    args = ap.parse_args()

    scenarios = json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))
    LOGS.mkdir(exist_ok=True)
    skip = done_pairs()

    jobs = []
    for ci, condition in enumerate(CONDITIONS):
        if condition not in args.conditions:
            continue
        for rep in range(args.repeats):
            jobs.append((ci * args.repeats + rep + 1, condition))

    print(f"model={acl.MODEL} temperature={acl.TEMPERATURE} scenarios={len(scenarios)} "
          f"runs={len(jobs)} workers={args.workers} 이미 완료={len(skip)}")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for name in pool.map(lambda j: do_run(j[0], j[1], scenarios, skip), jobs):
            print(f"  로그 기록: logs/{name}")


if __name__ == "__main__":
    main()
