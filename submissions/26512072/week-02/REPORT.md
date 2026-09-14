# Week 02 — Harness A/B: ReAct vs Plan-then-Execute

학번 **26512072**, GitHub **BJEon01**

## What I built

스타터 태스크인 “`app.log`에서 `ERROR`가 가장 많은 시간대를 찾아 `HH:00`으로 답하라”를 두 하네스로 비교했다. 성공 기준은 실행 전에 [TASK.md](TASK.md)에 커밋한 **`14:00`**이다. 최종 v3 조건은 OpenRouter의 `nvidia/nemotron-3.5-lightning:free`, 원본 `app.log`, 공용 `read_file(path)`·`count_pattern(path, pattern)`, `temperature=0.2`, `max_tokens=2048`, `max_steps=16`, reasoning effort `none`을 고정했다. 두 하네스는 같은 [tools_shared.py](tools_shared.py)의 모델과 도구를 import하며 독립변수는 하네스다.

| 하네스 축 | ReAct | Plan-then-Execute |
| --- | --- | --- |
| 컨텍스트 관리 | 한 대화에 Observation 누적 | Planner/Executor 분리, Executor에 계획과 Observation 누적 |
| 다음 행동 결정 | Observation 뒤마다 다음 Action 재판단 | 먼저 3단계 JSON 계획을 한 번 만들고 순서대로 실행 |
| 종료 조건 | 도구 호출 없는 답 또는 16회 상한 | 계획·단계·최종 답 완료 또는 16회 상한 |
| 오류 복구 | 도구 오류를 Observation으로 받고 계속 판단 | `OFF_PLAN`/계획 파싱 실패 때 최대 1회 재계획 |
| 사람 개입 | 읽기 전용이므로 0회 | 읽기 전용이므로 0회; 자동 재계획은 별도 |

아래 표는 v3 중 **모델 응답이 끝까지 완료된 실행** 전부다. 실패 실행도 포함했다. 응답 자체를 받지 못했거나 수동 중단된 시도도 삭제하지 않고 `results.csv`, `logs/`, 커밋 이력에 보존했다.

| run | harness | success | tokens | iters | interventions | wall time |
| ---: | --- | :---: | ---: | ---: | ---: | ---: |
| 13 | react | O | 24,023 | 7 | 0 | 70.8 s |
| 14 | plan_exec | O | 20,409 | 7 | 0 | 258.1 s |
| 15 | react | O | 4,204 | 2 | 0 | 75.5 s |
| 16 | plan_exec | O | 19,317 | 7 | 0 | 66.3 s |
| 25 | react | X | 6,784 | 4 | 0 | 210.4 s |
| 26 | plan_exec | O | 20,894 | 7 | 0 | 55.5 s |
| 27 | react | O | 3,744 | 2 | 0 | 20.4 s |
| 28 | plan_exec | O | 24,714 | 7 | 0 | 342.1 s |
| 30 | plan_exec | X | 45,357 | 16 | 0 | 102.0 s |

| harness | n | success | tokens mean / sample variance (min–max) | iters mean / sample variance (min–max) | wall time |
| --- | ---: | :---: | ---: | ---: | ---: |
| react | 4 | 3/4 | 9,688.75 / 93,110,276.92 (3,744–24,023) | 3.75 / 5.58 (2–7) | 20–210 s |
| plan_exec | 5 | 4/5 | 26,138.20 / 119,561,188.70 (19,317–45,357) | 8.80 / 16.20 (7–16) | 55–342 s |

ReAct는 성공 실행에서 파일을 읽은 뒤 바로 답하면 2회에 끝났지만, Observation을 잘못 해석하면 추가 Action을 만들었다. Plan은 JSON 계획 호출 1회, 각 단계 실행, 최종 답 호출이 고정 비용으로 붙어 성공한 네 실행도 모두 7회였다. 따라서 이 태스크에서는 **ReAct가 토큰과 반복 횟수에서 이겼고**, Plan은 실행 흐름이 일정했지만 더 비쌌다. “토큰이 적으면 불확실한 상황에서 덜 고민한 것인가?”라는 가설은 토큰만으로 판단하기 어렵다. run 15·27의 낮은 비용은 고민의 깊이보다 조기 종료 구조에서 왔고, run 30은 이미 `14:00`을 찾고도 도구를 반복해 16회 상한에서 실패했다. 이는 종료 조건이 iters를, 누적 컨텍스트가 tokens를 움직였다는 로그 근거다. 사람 개입은 모두 0회였다.

## What I tried and discarded

- 조건 A(run 1–6)의 Plan은 3회 모두 최초 JSON 계획 파싱에서 실패했다. 결과와 로그를 삭제하지 않았다.
- 조건 B(run 7–9)는 tolerant 파서만 시험했고, 조건 C(run 10–12)는 계획 프롬프트만 강화했다. 둘 다 최종 성공은 없었으며 별도 커밋으로 남겼다.
- v3는 계획을 `파일 1회 읽기 → 받은 원문 집계 → 형식에 맞춰 답하기`로 제한하고, 최초 파싱 실패에도 재계획 1회를 쓰도록 했다. 그 결과 Plan은 run 14, 16, 26, 28에서 실제로 `14:00`을 답했다.
- run 25의 ReAct 출력은 도중에 깨져 최종 답이 없었다. run 30의 Plan은 마지막 단계에서 도구를 반복해 재계획과 16회 상한을 소진했다. 둘 다 실패로 유지했다.
- 모델 응답이 끝나지 않은 실행도 `results.csv`와 원본 로그에 남겼고, 위 하네스 행동 평균에서는 분리했다.

## How to run

키는 환경변수로만 전달하고 파일에 저장하지 않는다.

```powershell
$env:OPENROUTER_API_KEY = "<your-openrouter-key>"
powershell -NoProfile -ExecutionPolicy Bypass -File .\submissions\26512072\week-02\run_openrouter.ps1
Remove-Item Env:\OPENROUTER_API_KEY
python scripts/check_week02.py submissions/26512072/week-02
```

## Checklist

- `python scripts/check_week02.py submissions/26512072/week-02` 통과
- `results.csv`에 실패를 포함해 하네스당 3회 이상 기록
- `logs/`에 실행 콘솔의 Thought, Action, Observation 원문 보존
- API 키와 `.env` 파일 미커밋
- 실패·수정 커밋 유지, squash/amend 미사용
