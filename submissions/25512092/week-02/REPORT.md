# Week 02 — Harness A/B: ReAct vs Plan-then-Execute

## 1. Variant definition

모델(claude-sonnet-4-5), 태스크(app.log에서 ERROR가 가장 많은 시간대),
도구 집합(read_file, count_pattern)을 고정하고 하네스만 바꿨다. 두
하네스는 `tools_shared.py`의 같은 `TOOLS_IMPL`과 같은 `Chat`을 쓴다.

다섯 축 중 두 하네스가 다르게 설정한 것은 셋이다.

| 축            | ReAct                                                                  | Plan-then-Execute                                                                          |
| ------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| 컨텍스트 관리 | 전체 대화 이력을 유지하며 매 스텝의 Observation이 다음 판단에 반영된다 | 계획 단계는 `tools=False`로 도구 접근이 없다. 입력을 관찰하지 못한 채 전체 단계가 결정된다 |
| 종료 조건     | 모델이 도구 호출 없이 응답하거나 `max_steps=8` 도달                    | 계획의 단계 소진 후 최종 답 요청                                                           |
| 오류 복구     | 도구 오류가 Observation으로 되돌아와 다음 스텝에서 재시도 가능         | 실행 단계는 OFF_PLAN 시 replan 1회 허용. 계획 파싱 실패에는 복구 경로가 없다               |

도구 세분성은 두 하네스가 동일하다. 사람 개입 지점은 `IRREVERSIBLE`이
빈 집합이라 양쪽 모두 0으로 고정된다. 스타터 도구가 읽기 전용이기
때문이며, 하네스 차이에서 온 값이 아니다.

## 2. Measurements

| run | harness   | success | tokens | iters | interventions | note      |
| --- | --------- | ------- | ------ | ----- | ------------- | --------- |
| 1   | react     | O       | 6809   | 3     | 0             |           |
| 2   | react     | O       | 6914   | 3     | 0             |           |
| 3   | react     | O       | 6886   | 3     | 0             |           |
| 4   | plan_exec | O       | 31403  | 9     | 0             | replans=0 |
| 5   | plan_exec | X       | 200    | 1     | 0             | replans=0 |
| 6   | plan_exec | X       | 201    | 1     | 0             | replans=0 |

ReAct 3/3 성공, 평균 6,870 토큰, 3 iterations. Plan-then-Execute 1/3
성공. 성공한 실행은 31,403 토큰으로 ReAct의 4.6배, 9 iterations로 3배였다.
실패한 두 실행은 200토큰에서 종료됐다. 계획 호출 한 번만 소비한 값이다.

## 3. Interpretation

축마다 움직인 지표가 달랐다.

오류 복구 축이 성공률을 갈랐다. Plan-then-Execute의 실패 2회는 모두
계획 파싱 단계에서 발생했다. `SYSTEM_PLAN`은 "No prose, no code fences"를
명시했으나 모델은 `I'll help you find the hour with the most ERROR lines
in app.log.` 라는 문장을 앞에 붙이고 JSON을 코드펜스로 감쌌다
(`logs/plan_exec-05.txt`, `plan_exec-06.txt`). `parse_plan`은 펜스는
제거하지만 앞선 산문은 처리하지 못해 `json.loads`가 실패했고, 하네스는
`return "plan parse failed"`로 즉시 종료했다. 계획 내용 자체는 타당했으므로
추론 실패가 아니라 형식 불일치다. replan은 실행 단계의 OFF_PLAN에만
적용되므로 이 지점에는 복구 경로가 없다. 반면 ReAct는 오류가
Observation으로 되돌아온다. 성공한 Plan 실행에서도 `count_pattern`을
pattern 인자 없이 호출해 에러가 났지만 곧바로 재호출로 복구했다
(`logs/plan_exec-04.txt`). 차이는 오류가 나느냐가 아니라, 오류가 난
지점에 다음 스텝이 있느냐다.

컨텍스트 관리 축이 토큰과 반복 횟수를 갈랐다. 성공한 Plan 실행은
00:00부터 23:00까지 24개 시간대를 모두 셌다. 계획 단계의 `Chat`은
`tools=False`로 생성되어 `read_file`을 호출할 수 없으므로, 로그가 어느
시간대를 담고 있는지 모르는 상태에서 전 범위를 훑는 계획을 세울 수밖에
없었다. ReAct는 첫 스텝에서 `read_file`로 로그를 읽어 09:00~17:00
범위임을 확인한 뒤 9개 시간대만 셌다(`logs/react-01.txt`). 같은 정답에
도달했지만 15개의 불필요한 도구 호출이 토큰 4.6배, 반복 3배의 차이를
만들었다. 관찰을 계획에 되먹일 수 없다는 구조가 그대로 비용이 됐다.

이 태스크에서는 ReAct가 세 지표 모두 앞섰다. 다만 이는 태스크 성격에서
온 결과이기도 하다. 탐색 범위를 실행 중에 좁힐 수 있는 문제였기 때문에
관찰 반영의 이점이 컸다. 단계가 고정되어 있고 중간 관찰이 계획을 바꿀
여지가 없는 태스크라면 Plan-then-Execute의 예측 가능성이 유리할 수 있다.
또한 계획 파싱 실패는 하네스 설계보다 모델의 형식 준수 능력에 달린
문제이므로, 구조화 출력을 강제하는 API를 쓰면 성공률 차이는 줄어들 것이다.

## 4. How to run

- Python 3.10+, `pip install anthropic`
- Provider: Anthropic (`ANTHROPIC_API_KEY`가 설정되면 자동 선택)
- Model: `claude-sonnet-4-5` (`tools_shared.py`의 기본값, `AGENT_MODEL`로 재정의 가능)
- 도구: `read_file(path)`, `count_pattern(path, pattern)` — 두 하네스가
  `tools_shared.py`에서 동일하게 import
- 파라미터: ReAct `max_steps=8` / Plan-then-Execute `max_replan=1`, `max_tool_rounds=3`
