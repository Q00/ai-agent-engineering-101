# Week 02 — ReAct와 Plan-then-Execute A/B

학번 `26510358`. OpenAI `gpt-5.6-luna`, `reasoning_effort=none`, Python 3.13.15,
OpenAI SDK 3.8.0. 실행 전 조건은 [RUN_SETTINGS.md](RUN_SETTINGS.md),
예상은 [CHECKLIST.md](CHECKLIST.md)에 커밋했다.
실행 명령: `.venv/bin/python run_ab.py --runs 3`.

## 1. 변형 정의

같은 원본 `app.log`에서 ERROR가 가장 많은 시간을 답하게 했다. 두 하네스는 같은 모델과
`tools_shared.py`의 도구를 사용한다. `TASK.md`에 미리 정한 `14:00`이 최종 답변에 포함되면
성공이다. 두 하네스 본문은 starter 그대로이며, 공통 API 설정·환경 파일 접근 제한·준비
검사만 보완했다.

| 설계 요소 | ReAct | Plan-then-Execute |
|---|---|---|
| 컨텍스트 관리 | 한 대화에 도구 결과를 누적하고 매번 다음 행동 결정 | 계획용·실행용 대화 분리; 실행 대화에는 문제·계획·단계 결과 누적 |
| 도구 단위 | `read_file`, `count_pattern` | 같은 도구·스키마 사용; 최초 계획 생성에서는 도구 비활성화 |
| 종료 조건 | 도구 호출 없는 응답 또는 `max_steps=8` | 계획 단계 실행 뒤 별도 최종 답변 요청; 단계별 `max_tool_rounds=3` |
| 오류 복구 | 도구 오류를 Observation으로 전달 | 같은 도구 오류 처리에 `OFF_PLAN` 재계획 최대 1회 추가 |
| 사람 개입 | 승인 대상 없음 | 승인 흐름 없음 |

## 2. 측정

[results.csv](results.csv)의 6회 전체 결과다. `iters`는 모델 호출 횟수이며 계획·재계획·
최종 답변 호출도 포함한다. 토큰은 SDK가 보고한 입력·출력 합계다.

| run | harness | success | tokens | iters | interventions | note |
|---:|---|:---:|---:|---:|---:|---|
| 1 | react | O | 1817 | 2 | 0 | — |
| 2 | react | O | 1815 | 2 | 0 | — |
| 3 | react | O | 1805 | 2 | 0 | — |
| 4 | plan_exec | O | 7401 | 6 | 0 | replans=0 |
| 5 | plan_exec | O | 7106 | 6 | 0 | replans=0 |
| 6 | plan_exec | O | 7516 | 6 | 0 | replans=0 |

| 하네스 | 성공 | 토큰 평균 ± 표본 표준편차 | 호출 평균 | 개입 평균 |
|---|---|---:|---:|---:|
| ReAct | 3/3 | 1812.3 ± 6.4 | 2 | 0 |
| Plan-then-Execute | 3/3 | 7341.0 ± 211.5 | 6 | 0 |

## 3. 해석

**학습자 해석 작성 중:** 실행 전 예상과 실제 결과를 비교하고, 어느 설계 차이가 어떤 지표를
움직였는지 자신의 말로 작성한다. 측정표와 로그 확인은 완료했으며, 이 문단을 작성한 뒤 제출한다.

로그 확인: 6회 모두 `read_file`을 1회 사용했고 `count_pattern`은 호출하지 않았다.
[ReAct 예시](logs/react-01.txt)는 읽기 요청 후 다음 호출에서 답했고,
[Plan-then-Execute 예시](logs/plan_exec-04.txt)는 계획 생성·3단계 실행·별도 최종 답변을 남겼다.
세 계획 실행 모두 재계획은 0회였다.
[실행 5](logs/plan_exec-05.txt)는 중간 집계에서 14시 ERROR를 7개라고 적었지만 원본에는
6개다. 최종 시간대는 맞아 사전 기준상 O이며, 성공 판정이 중간 계산의 정확성까지 보장하지 않는다.
결과 범위는 한 입력·각 3회·ReAct 먼저 실행한 조건에 한정한다.
