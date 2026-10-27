# Week 02 — ReAct vs Plan-then-Execute

같은 과제·모델·도구를 유지하고 하네스만 바꿔 비교했다. 과제는 `app.log`에서
ERROR가 가장 많은 시간대를 찾는 것이며, 최종 답변에 `14:00`이 포함되면 성공이다.
OpenRouter의 `nvidia/nemotron-3.5-lightning:free` 모델을 사용했고, 공통 도구는
`read_file(path)`와 `count_pattern(path, pattern)`이다. 재현하려면 아래와 같이 실행한다.

```bash
cd submissions/26512071/week-02
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<본인 키>
export AGENT_MODEL=nvidia/nemotron-3.5-lightning:free
python run_ab.py --runs 3
```

## 1. 변형 정의

| 요소 | ReAct | Plan-then-Execute | 차이 |
|---|---|---|---|
| 컨텍스트 관리 | 하나의 대화에 생각·도구 호출·관찰을 누적 | planner와 executor를 분리하고 executor에 전체 실행 기록을 누적 | O |
| 도구 세분화 | `read_file`, `count_pattern` | 동일 | X |
| 종료 조건 | 모델이 답하면 종료, 최대 8회 | 계획의 모든 단계를 수행한 뒤 최종 답변 호출을 강제 | O |
| 오류 복구 | 도구 오류를 관찰로 돌려주고 모델이 다음 행동 결정 | OFF_PLAN이면 한 번 재계획하며 JSON 파싱 실패 시 실행 루프 중단 | O |
| 인간 개입 지점 | 쓰기 도구 승인 훅이 있으나 이번 도구는 모두 읽기 전용 | 승인 훅 없음 | 실험상 X |

따라서 실제로 움직인 요소는 컨텍스트 관리, 종료 조건, 오류 복구다. 모델, 태스크,
도구와 성공 판정 기준은 고정했고 모든 실행에서 인간 개입은 0회였다.

## 2. 측정표

정식 배치 6회의 `results.csv` 결과다. 실패 실행도 그대로 포함했다.

| run | harness | success | tokens | iters | interventions | note |
|---:|---|:---:|---:|---:|---:|---|
| 0 | react | O | 3,967 | 2 | 0 | |
| 1 | react | X | 20,123 | 8 | 0 | |
| 2 | react | O | 4,146 | 2 | 0 | |
| 3 | plan_exec | O | 21,050 | 8 | 0 | replans=1 |
| 4 | plan_exec | O | 70,752 | 13 | 0 | replans=0 |
| 5 | plan_exec | O | 75,626 | 14 | 0 | replans=0 |

ReAct는 2/3, Plan-then-Execute는 3/3 성공했다. 토큰 중앙값은 각각 4,146과
70,752, 반복 중앙값은 각각 2회와 13회였다. `results.csv` 첫 행의 별도 ReAct
실행은 정식 배치 전 첫 회가 저장된 뒤 Codex 실행 세션이 외부에서 중단된 기록이다.
기록은 보존하되 위 집계에서는 제외했다. 각 정식 실행의 근거는 `logs/react-00.txt`부터
`logs/plan_exec-05.txt`까지 남아 있다.

## 3. 해석

성공률에서는 Plan-then-Execute가 이겼다. `react-01`은 시간대별 개수를 모두 구했지만
8회 상한을 도구 호출에 소진해 정답을 말할 기회 없이 종료된 반면, Plan-then-Execute는
실행 루프 뒤 최종 답변을 강제했기 때문이다. 이는 종료 조건이 성공률을 움직인 결과다.
이번 실험에서 Plan-then-Execute는 실행 과정의 전체 컨텍스트를 계속 누적하고, 정답을
이미 찾은 뒤에도 계획의 나머지 단계를 수행했기 때문에 토큰과 반복 수가 크게 증가했다.
따라서 이번 과제처럼 짧고 단순하며 조기 종료가 가능한 작업에서는 ReAct가 더 효율적이었다.
반면 단계의 순서와 누락 방지가 중요한 복잡한 작업에서는 Plan-then-Execute가 유용할 수
있지만, 불필요한 비용을 줄이려면 컨텍스트 요약과 조기 종료 조건을 추가해야 한다.
`plan_exec-03`은 OFF_PLAN 뒤 재계획 JSON 파싱에 실패했는데도 강제 최종 답변으로
통과했으므로, 오류 복구가 성공했다기보다 종료 조건이 복구 실패를 가린 사례로 해석했다.
