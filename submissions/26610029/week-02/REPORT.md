# Week 02 — 하니스 A/B 비교 보고서

모델: 실행 시점 셸 환경변수 `AGENT_MODEL` (OpenRouter, OpenAI 호환 API 경유, `tools_shared.py` 참고)
태스크: [TASK.md](TASK.md) 참고 — "`app.log`에서 ERROR 라인이 가장 많은 시간대(HH:00)는?", `expected: 14:00`
툴: `read_file`, `count_pattern` (`tools_shared.py`), 두 하니스에서 동일하게 사용
실행 방법: `python run_ab.py --runs 3` (`results.csv`, `logs/` 생성)

## 1. Part 1 - 변형 정의 (Variant definition)

두 하니스 모두 같은 `tools_shared.py`(같은 `Chat`, `Meter`, `TOOLS_IMPL`, 같은 모델명)를 import합니다 — 오직 하니스 루프 구조만 다릅니다. 구조도: [harness_react.png](harness_react.png) / [harness_react.mmd](harness_react.mmd), [harness_plan_execute.png](harness_plan_execute.png) / [harness_plan_execute.mmd](harness_plan_execute.mmd).

| 축 | ReAct (`harness_react.py`) | Plan-then-Execute (`harness_plan_execute.py`) |
|---|---|---|
| 1. context 관리 | `Chat` 하나에 매 스텝 전체 히스토리 누적 | Planner용 `Chat`(툴 없음, 1회 호출)과 Executor용 `Chat`(전체 step 공유)으로 분리 |
| 2. tool granularity | 매 스텝마다 모델이 자유롭게 툴 호출, 호출당 제한 없음 | 계획의 각 step 안에서만 툴 호출, step당 최대 `max_tool_rounds=3`으로 제한 |
| 3. 종료 조건 | 모델이 툴 호출 없이 "Answer:"로 답하거나 `max_steps=8` 안전망 도달 시 종료 | 계획의 모든 step을 실행한 뒤 최종 "Answer:" 요청; 계획 자체에는 step 수 상한 없음 |
| 4. 에러 복구 | 툴 에러가 다음 Observation으로 그대로 들어가고, 모델이 알아서 반응 | 계획 파싱 실패 시 복구 불가(즉시 종료); step이 `OFF_PLAN`으로 판정되면 재계획 1회(`max_replan=1`) 허용, 그 이후엔 그냥 다음 step으로 넘어감 |
| 5. 사람 개입 시점 | `IRREVERSIBLE` 툴 집합에 있는 툴은 실행 전 사람 승인 필요(현재는 비어있어 개입 0회) | 개입 시점 없음(두 툴 다 읽기 전용) |

## 2. Part 2 - 측정 결과 (Measurements)

`results.csv` 기준, 총 6회 실행(하니스당 3회, 실패 포함):

| run | harness | success | tokens | iters | interventions | note |
|---|---|---|---|---|---|---|
| 1 | react | O | 3612 | 2 | 0 | |
| 2 | react | O | 3689 | 2 | 0 | |
| 3 | react | O | 4230 | 2 | 0 | |
| 4 | plan_exec | O | 48779 | 11 | 0 | replans=0 |
| 5 | plan_exec | O | 32942 | 10 | 0 | replans=0 |
| 6 | plan_exec | X | 2232 | 1 | 0 | replans=0 |

집계:

| | react | plan_exec |
|---|---|---|
| 성공률 | 3/3 (100%) | 2/3 (67%) |
| 평균 토큰 (전체 런) | 3,844 | 27,984 |
| 평균 토큰 (성공한 런만) | 3,844 | 40,861 |
| 평균 iteration (모델 호출 횟수) | 2 | 성공 런 평균 10.5 / run 6은 1 |

개별 런에서 발견한 구체적 근거:
- `logs/react-01.txt`: 시스템 프롬프트가 매 툴 호출 전 "Thought:"로 시작하는 줄을 쓰라고 지시하지만, 이 런에서는 모델이 `read_file`을 호출하고 곧바로 답하며 "Thought:" 줄을 한 번도 쓰지 않았음. 나머지 두 런(`react-02.txt`, `react-03.txt`)은 정상적으로 "Thought:"를 포함함.
- `logs/plan_exec-06.txt`: planner가 JSON 리스트가 아니라 `'{"read_file": "app.log"}'`(JSON 객체)를 반환. `parse_plan()`이 이를 거부하며 런이 즉시 종료(`iters=1`, `tokens=2232`)되어 회복할 기회 자체가 없었음.
- `logs/plan_exec-04.txt`: 5단계 계획 중 executor가 아직 아무것도 세기 전인 **1단계에서 이미 "Answer: 14:00"이라고 답변**. 실제로 제대로 센 답은 3~4단계에서 나왔지만, 이 조기 답변이 하니스를 멈추지는 않음(전체 step이 끝날 때까지 executor의 중간 "Answer:" 출력을 체크하는 로직이 없음).

## 3. 해석 (Interpretation)

이번 태스크에서 ReAct가 성공률(3/3 vs 2/3)과 비용(토큰 약 10배) 양쪽에서 이겼는데, 이 격차의 직접적 원인은 axis 1(context 관리) 자체보다 axis 2(tool granularity)와 axis 3(종료조건)이다. 
Plan-then-Execute는 계획을 step 단위로 미리 쪼개고 모든 step을 다 끝내야만 종료되는 구조라, `logs/plan_exec-04.txt`처럼 5단계 계획에 iters=11(step당 2회 이상 모델 호출)이 걸리는 등 호출 횟수 자체가 구조적으로 강제로 늘어난다. 
그리고 이렇게 늘어난 호출마다 axis 1(executor의 누적 히스토리)이 지금까지의 전체 대화를 통째로 다시 모델에 보내다 보니, 비용이 (호출 횟수) × (누적 context 길이)로 곱셈처럼 커져서 react(평균 2회 호출, 평균 3,844 토큰) 대비 plan_exec 성공 런은 평균 40,861 토큰까지 치솟았다. 

이 오버헤드는 이 태스크에서는 순수한 손해였는데, `app.log` 하나를 읽고 세는 이 작업 자체가 애초에 여러 단계로 쪼갤 필요가 거의 없는 단순한 일이라 — 5단계짜리 계획을 억지로 만든 것 자체가 낭비였고(`plan_exec-04.txt`에서 아직 아무것도 세기 전인 1단계부터 "Answer: 14:00"을 조기에 내뱉은 것도 계획이 태스크의 실제 난이도에 안 맞았다는 신호다), `plan_exec-06.txt`처럼 계획이 JSON 리스트가 아니면 axis 4(에러 복구)가 재계획 기회조차 주지 못하고 바로 실패로 끝나 신뢰성도 떨어졌다. 다만 이 결과가 Plan-then-Execute 구조 자체의 열세를 의미하지는 않는다고 본다 — 더 길고 분기가 많은 태스크라면, 매 스텝 즉흥적으로 판단하는 ReAct보다 미리 계획을 세워두는 쪽이 헤매지 않고 오히려 안정적일 수 있을 것이다.
