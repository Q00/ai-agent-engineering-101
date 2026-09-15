import os
import json
import csv
import time
from dataclasses import dataclass
from pathlib import Path
from contractor import Contractor, setup_team, bid


# 1. 지표 집계용 데이터 클래스
@dataclass
class RoundResult:
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0


# 2. 강의 표준 매니저 루프 (Smith 1980 계약망 프로토콜)
def run_round(tasks: list, team: list[Contractor], meter=None, log=print) -> RoundResult:
    r = RoundResult(tasks=len(tasks))
    for t in tasks:
        log(f"\n[Task {t['id']}] {t['desc']} (Gold: {t['gold']})")
        r.messages += len(team)  # 방송 공고: contractor 수만큼 (3)
        bids = []
        for c in team:
            # Rate limit 완화를 위해 필요시 슬립
            time.sleep(0.5)

            b = bid(c, t["id"], t["desc"], meter)
            if b is None:
                r.parse_fails += 1
                log(f"  [bid] {c.name}: PARSE_FAIL (입찰 미참여 처리)")
                continue

            log(f"  [bid] {c.name}: bid={b.get('bid')} confidence={b.get('confidence')} reason={b.get('reason')}")
            if b.get("bid") is True:
                r.messages += 1  # 입찰 1건 = 메시지 1
                bids.append((b.get("confidence", 0), c))

        if not bids:
            r.unassigned += 1  # 유찰 (입찰자 없음)
            log(f"  -> [result] UNASSIGNED (No valid bids)")
            continue

        # 확신도 최고, 동점이면 먼저 입찰한 순서
        bids.sort(key=lambda x: -x[0])
        winner = bids[0][1]
        r.messages += 1  # 낙찰 메시지 1건

        if winner.name == t["gold"]:
            r.correct += 1
        else:
            r.misawards += 1

        log(f"  -> [award] {winner.name} (gold: {t['gold']})")

    return r


# 3. 9회 실험 실행 및 파일 저장 진입점
def main():
    base_dir = Path(__file__).resolve().parent
    tasks_path = base_dir / "tasks.json"
    logs_dir = base_dir / "logs"
    results_path = base_dir / "results.csv"

    logs_dir.mkdir(exist_ok=True)

    with open(tasks_path, "r", encoding="utf-8") as f:
        tasks = json.load(f)

    # 3개 조건 x 3회 실행 = 총 9 runs
    conditions = ["baseline", "homogeneous", "overconfident"]
    runs_per_condition = 3

    csv_rows = []
    run_id = 1

    # 모델 메타데이터 (README 요구사항: 로그 상단 필수 정보)
    provider = os.getenv("PROVIDER", "Google AI Studio (OpenAI Compatible)")
    model_name = os.getenv("AGENT_MODEL", "gemini-3.5-flash-lite")
    temperature = 0.2

    print(f"Starting Contract Net Experiment (Total 9 runs)...")

    for condition in conditions:
        for _ in range(runs_per_condition):
            log_file_path = logs_dir / f"run-{run_id:02d}.log"
            team = setup_team(condition)
            note = ""

            with open(log_file_path, "w", encoding="utf-8") as lf:
                # 로그 파일 필수 헤더 기록
                lf.write(f"provider: {provider}\n")
                lf.write(f"model: {model_name}\n")
                lf.write(f"temperature: {temperature}\n")
                lf.write(f"condition: {condition}\n")
                lf.write(f"run: {run_id}\n")
                lf.write("=" * 50 + "\n")

                def log_both(msg: str):
                    print(msg)
                    lf.write(msg + "\n")

                log_both(f"\n--- Run {run_id} | Condition: {condition} ---")

                try:
                    result = run_round(tasks, team, meter=None, log=log_both)

                    if result.parse_fails > 0:
                        note = f"parse_fails={result.parse_fails}"

                    csv_rows.append({
                        "run": run_id,
                        "condition": condition,
                        "tasks": result.tasks,
                        "correct": result.correct,
                        "messages": result.messages,
                        "unassigned": result.unassigned,
                        "misawards": result.misawards,
                        "note": note
                    })
                except Exception as e:
                    # 크래시가 나도 빈 카운트와 에러 메시지를 note에 기록 (README 규정)
                    log_both(f"CRASH: {str(e)}")
                    csv_rows.append({
                        "run": run_id,
                        "condition": condition,
                        "tasks": len(tasks),
                        "correct": "",
                        "messages": "",
                        "unassigned": "",
                        "misawards": "",
                        "note": f"crash: {str(e)}"
                    })

            run_id += 1

    # results.csv 저장 (과제 필수 헤더 정확히 준수)
    fieldnames = ["run", "condition", "tasks", "correct", "messages", "unassigned", "misawards", "note"]
    with open(results_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f"\nAll experiments finished. Results saved to {results_path} and {logs_dir}/")


if __name__ == "__main__":
    main()