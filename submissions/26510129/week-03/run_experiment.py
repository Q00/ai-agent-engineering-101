"""실행기. 조건 하나를 N번 돌리고 results.csv 한 줄 + logs/ 파일 하나씩을 남긴다.

  python run_experiment.py --condition baseline --runs 3
  python run_experiment.py --all --runs 3
  python run_experiment.py --all --runs 1 --fake --results /tmp/r.csv --logdir /tmp/logs
        (--fake: 모델을 부르지 않는 배선 점검용. 실제 제출 결과로 쓰지 않는다)

run 번호는 results.csv에 이미 있는 줄 다음부터 이어 붙인다. 크래시한 실행도
지우지 않고 카운트를 비운 채 note에 이유를 적는다.
"""
import argparse
import csv
import json
import sys
import time
import traceback
from pathlib import Path

import contract_net
from contract_net import Meter, build_team
from manager import run_round

HERE = Path(__file__).resolve().parent
HEADER = ["run", "condition", "tasks", "correct", "messages", "unassigned", "misawards", "note"]
CONDITIONS = ("baseline", "homogeneous", "overconfident")


class Tee:
    """화면과 로그 파일에 같이 쓴다."""

    def __init__(self, path: Path):
        self.f = path.open("w", encoding="utf-8")

    def __call__(self, *parts):
        line = " ".join(str(p) for p in parts)
        print(line)
        self.f.write(line + "\n")
        self.f.flush()

    def close(self):
        self.f.close()


def next_run_number(results: Path) -> int:
    if not results.is_file():
        return 1
    with results.open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.reader(f) if r and r[0].strip().isdigit()]
    return max((int(r[0]) for r in rows), default=0) + 1


def append_row(results: Path, row: list):
    new = not results.is_file()
    with results.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(HEADER)
        w.writerow(row)


def fake_call_model(system: str, user: str, meter: Meter) -> str:
    """배선 점검용 가짜 모델. 전문 분야 단어가 공고에 있으면 입찰한다."""
    meter.add(100, 20)
    skill = system.split("Your skill:")[1].split(";")[0].strip()
    overconf = "Always bid" in system
    text = user.lower()
    words = {"forensics": ("print", "ash", "residue", "ink", "paper"),
             "interviewing": ("sit down", "bartender", "in person", "remembers"),
             "records": ("policy", "number", "cross-reference", "payroll")}
    hit = "general" in skill or any(w in text for w in words.get(skill, ()))
    if overconf:
        return '{"bid": true, "confidence": 97, "reason": "I can do anything."}'
    if hit:
        return '{"bid": true, "confidence": 88, "reason": "matches my skill"}'
    return '{"bid": false, "confidence": 5, "reason": "outside my skill"}'


def one_run(run_no: int, condition: str, tasks, results: Path, logdir: Path, fake: bool):
    logdir.mkdir(parents=True, exist_ok=True)
    log = Tee(logdir / f"run-{run_no:02d}-{condition}.txt")
    meter = Meter()
    started = time.time()
    try:
        # 로그 첫 줄: 재현에 필요한 설정 전부 (키는 적지 않는다)
        log(f"provider={contract_net.PROVIDER} model={contract_net.MODEL} "
            f"temperature={contract_net.TEMPERATURE} max_tokens={contract_net.MAX_TOKENS} "
            f"min_interval={contract_net.MIN_INTERVAL} max_retries={contract_net.MAX_RETRIES} "
            f"run={run_no} condition={condition} tasks={len(tasks)} "
            f"python={sys.version.split()[0]} fake={fake} "
            f"started={time.strftime('%Y-%m-%dT%H:%M:%S')}")
        for c in build_team(condition):
            log(f"[contractor {c.name}] system: {c.system_prompt()}")

        r = run_round(tasks, build_team(condition), meter, log=log)

        elapsed = time.time() - started
        note = (f"parse_fails={r.parse_fails} ties={r.ties} "
                f"tokens={meter.tokens} calls={meter.calls} retries={meter.retries} "
                f"secs={elapsed:.0f}")
        if fake:
            note = "FAKE " + note
        log(f"\n== run {run_no} {condition}: tasks={r.tasks} correct={r.correct} "
            f"messages={r.messages} unassigned={r.unassigned} misawards={r.misawards} "
            f"| {note}")
        log(f"== awards: {r.awards}")
        append_row(results, [run_no, condition, r.tasks, r.correct, r.messages,
                             r.unassigned, r.misawards, note])
    except Exception as e:                      # 크래시도 기록에 남긴다
        log(f"\n!! crashed: {type(e).__name__}: {e}")
        log(traceback.format_exc())
        append_row(results, [run_no, condition, "", "", "", "", "",
                             f"crashed: {type(e).__name__}: {str(e)[:120]}"])
        raise
    finally:
        log.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", choices=CONDITIONS)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--tasks", default=str(HERE / "tasks.json"))
    ap.add_argument("--results", default=str(HERE / "results.csv"))
    ap.add_argument("--logdir", default=str(HERE / "logs"))
    ap.add_argument("--fake", action="store_true")
    a = ap.parse_args()

    if not a.all and not a.condition:
        ap.error("--condition or --all is required")
    if a.fake:
        contract_net.call_model = fake_call_model
        import manager
        manager.ask_bid.__globals__["call_model"] = fake_call_model

    tasks = json.loads(Path(a.tasks).read_text(encoding="utf-8"))
    results, logdir = Path(a.results), Path(a.logdir)
    plan = list(CONDITIONS) if a.all else [a.condition]

    # 한 run이 크래시하면 (키 없음, 한도 소진 등) 나머지도 같은 이유로 죽을 가능성이
    # 높으므로 거기서 멈춘다. 크래시 행은 남고, 모자란 조건은 --condition으로 채운다.
    for condition in plan:
        for _ in range(a.runs):
            n = next_run_number(results)
            try:
                one_run(n, condition, tasks, results, logdir, a.fake)
            except Exception:
                print(f"run {n} ({condition}) crashed; row kept with blank counts. "
                      f"Stopping here - fix the cause and rerun the missing conditions.",
                      file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
