# Week 02 — Harness A/B: ReAct vs Plan-then-Execute

학번 **26512072**, GitHub **BJEon01**. 태스크는 `app.log`에서 `ERROR`가 가장 많은 시간대를 찾는 것이며, 실행 전에 커밋한 성공 기준은 최종 답에 **`14:00`**이 포함되는 것이다.

## 1. What I built — 변형 정의

최종 비교(run 13–18)는 OpenRouter의 `nvidia/nemotron-3.5-lightning:free`, 같은 태스크와 원본 `app.log`, 같은 `read_file(path)`·`count_pattern(path, pattern)` 도구를 썼다. 두 하네스 모두 `temperature=0.2`, `max_tokens=2048`, `max_steps=16`, reasoning effort `none`으로 실행했다. 독립변수는 하네스이며 ReAct와 Plan-then-Execute를 교대로 3회씩 실행했다.

| 하네스 축 | ReAct | Plan-then-Execute |
| --- | --- | --- |
| 컨텍스트 관리 | 하나의 대화에 Observation을 누적 | Planner와 Executor를 분리하고 Executor에 계획과 Observation을 누적 |
| 다음 행동 결정 | Observation 뒤마다 다음 Action을 다시 결정 | 먼저 3단계 JSON 계획을 만든 뒤 순서대로 실행 |
| 종료 조건 | 도구 호출 없는 답 또는 16회 상한 | 계획 파싱·각 단계·최종 답 완료 또는 16회 상한 |
| 오류 복구 | 도구 오류를 Observation으로 받고 계속 판단 | `OFF_PLAN` 또는 계획 파싱 실패 때 전체 실행 중 최대 1회 재계획 |
| 사람 개입 | 읽기 전용 도구이므로 0회 | 읽기 전용 도구이므로 0회; 자동 재계획은 사람 개입이 아님 |

Plan v3는 한 번의 모델 호출로 JSON 단계 목록 전체를 만들고, 첫 단계에서 파일을 한 번 읽으며, 다음 단계에서 이미 받은 내용을 집계하고, 마지막 단계에서 답을 쓰도록 범위를 좁혔다. 이는 앞선 실행에서 한 단계가 시간대별 도구 호출을 반복해 `max_tool_rounds`를 넘었던 문제를 고친 것이다. 실행 코드는 [harness_plan_execute.py](harness_plan_execute.py), 공용 모델·도구·측정 코드는 [tools_shared.py](tools_shared.py)에 있다.

## 2. Measurements — 측정표

`results.csv`의 최종 동조건 A/B 실행은 다음과 같다. 실패 실행도 그대로 포함했다.

| run | harness | success | tokens | iters | interventions | note |
| ---: | --- | :---: | ---: | ---: | ---: | --- |
| 13 | react | O | 24,023 | 7 | 0 | |
| 14 | plan_exec | O | 20,409 | 7 | 0 | replans=0 |
| 15 | react | O | 4,204 | 2 | 0 | |
| 16 | plan_exec | O | 19,317 | 7 | 0 | replans=0 |
| 17 | react | X | 13,457 | 7 | 0 | API 429로 응답 중단 |
| 18 | plan_exec | X | 0 | 1 | 0 | 첫 계획 호출에서 API 429 |

| harness | n | success | tokens 평균 / 표본분산 | iters 평균 / 표본분산 | interventions 평균 / 표본분산 |
| --- | ---: | :---: | ---: | ---: | ---: |
| react | 3 | 2/3 | 13,894.67 / 98,341,854.33 | 5.33 / 8.33 | 0.00 / 0.00 |
| plan_exec | 3 | 2/3 | 13,242.00 / 131,811,039.00 | 5.00 / 12.00 | 0.00 / 0.00 |

API 응답을 끝까지 받은 성공 실행만 보조적으로 비교하면 ReAct는 평균 **14,113.50 tokens / 4.50 iters**, Plan은 **19,863 tokens / 7.00 iters**였다. 이 값은 실패를 뺀 공식 성공률 표가 아니라, 하네스가 실제로 답을 완성했을 때의 비용을 해석하기 위한 보조 수치다. 원본 근거는 [react-13](logs/react-13.txt), [plan_exec-14](logs/plan_exec-14.txt), [react-15](logs/react-15.txt), [plan_exec-16](logs/plan_exec-16.txt), [react-17](logs/react-17.txt), [plan_exec-18](logs/plan_exec-18.txt)에 있다.

앞선 실패도 삭제하지 않았다. 조건 A(run 1–6)는 Plan 0/3으로 최초 JSON 계획 파싱에서 끝났고, 파서만 바꾼 B(run 7–9)와 계획 프롬프트만 강화한 C(run 10–12)도 Plan 0/3이었다. 이 실패들로부터 엄격한 JSON 출력, 최초 파싱 실패 때의 1회 재계획, 한 단계의 도구 호출 범위를 차례로 수정했고 각 시도와 수정은 별도 커밋으로 보존했다.

## 3. Interpretation — 해석

“토큰이 적으면 불확실한 상황에서 덜 고민하고 그대로 실행한 것인가?”라는 가설을 로그와 대조했다. 전체 3회를 단순 평균하면 Plan이 ReAct보다 652.67토큰과 0.33회 적지만, 이는 run 18이 첫 API 호출에서 답을 받지 못해 0토큰으로 기록된 영향이므로 하네스 효율의 우위로 볼 수 없다. 실제 성공 실행에서는 ReAct가 평균 14,113.50토큰·4.50회, Plan이 19,863토큰·7회로 더 적었다. Plan은 먼저 전체 계획을 만들고 세 단계를 각각 실행한 뒤 최종 답을 요청하므로 고정 호출 비용이 생겼고, 같은 파일 내용이 Executor 컨텍스트에 누적되어 뒤 호출의 입력 토큰도 커졌다. 반면 ReAct run 15는 파일을 읽은 다음 바로 `14:00`을 답해 2회에 끝났다. 따라서 이 태스크에서는 **성공한 실행의 토큰과 반복 횟수는 ReAct가 이겼고**, Plan은 강화된 계획으로 두 번 연속 정확히 성공하며 예측 가능한 흐름을 보였지만 비용은 더 컸다. 두 하네스 모두 사람 개입은 0회였다. 토큰 수만으로 모델이 얼마나 “고민”했는지는 판단할 수 없으며, 특히 조기 종료나 API 실패로 줄어든 토큰은 성공적인 효율과 구분해야 한다.

실행은 저장소 루트에서 다음과 같이 재현한다. 키는 환경변수로만 전달하며 파일에 저장하지 않는다.

```powershell
$env:OPENROUTER_API_KEY = "<your-openrouter-key>"
powershell -NoProfile -ExecutionPolicy Bypass -File .\submissions\26512072\week-02\run_openrouter.ps1
Remove-Item Env:\OPENROUTER_API_KEY
python scripts/check_week02.py submissions/26512072/week-02
```
