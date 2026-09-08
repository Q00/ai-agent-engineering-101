\# Week 02 — Harness A/B Report



\## 1. Variant definition



이번 실험에서는 모델, 태스크, 도구를 동일하게 유지하고 하네스만 변경했다. Provider는 OpenRouter, 모델은 `openrouter/free`를 사용했다. 공통 도구는 `read\_file(path)`와 `count\_pattern(path, pattern)`이다. 태스크는 `app.log`에서 ERROR가 가장 많이 발생한 시간대를 찾는 것이며, 정답 문자열이 최종 답변에 포함되면 성공으로 판정했다.



ReAct 하네스는 매 단계에서 Thought → Action → Observation을 반복하며 전체 대화 기록을 다음 호출에 전달한다. 모델이 답변을 완료하거나 최대 8단계에 도달하면 종료한다. 도구 오류도 Observation으로 돌려주므로 다음 단계에서 행동을 수정할 수 있다.



Plan-then-Execute 하네스는 먼저 전체 계획을 JSON 리스트로 생성한 다음 각 단계를 순서대로 실행한다. 한 단계당 도구 호출은 최대 3라운드이며, 계획에서 벗어났을 때 재계획은 최대 1회 허용된다. 따라서 두 하네스는 주로 컨텍스트 관리, 종료 조건, 오류 복구 축에서 다르다. 도구 granularity는 동일하며, 읽기 전용 도구만 사용했으므로 사람의 개입 지점도 동일하게 0회였다.



실행 명령은 다음과 같다.



`python run\_ab.py --runs 3`



\## 2. Measurements



| run | harness | success | tokens | iters | interventions | note |

|---:|---|:---:|---:|---:|---:|---|

| 1 | react | O | 15395 | 5 | 0 | |

| 2 | react | O | 11782 | 4 | 0 | |

| 3 | react | O | 8838 | 4 | 0 | |

| 4 | plan\_exec | O | 15382 | 7 | 0 | replans=1 |

| 5 | plan\_exec | X | 225 | 1 | 0 | replans=0 |

| 6 | plan\_exec | X | 738 | 1 | 0 | replans=0 |



ReAct의 성공률은 3/3이며 평균 토큰은 12,005개, 평균 반복 횟수는 4.33회였다. Plan-then-Execute의 성공률은 1/3이며 평균 토큰은 약 5,448개, 평균 반복 횟수는 3회였다.



\## 3. Interpretation



이 태스크에서는 ReAct가 성공률에서 우세했다. ReAct는 세 번 모두 성공했으며, 매 단계의 Observation을 다음 판단에 반영하는 오류 복구 방식이 안정성에 기여했다. 반면 Plan-then-Execute는 한 번만 성공했고 두 번은 계획 생성 호출 1회 후 종료되었다. 5번과 6번 실행은 반복 횟수가 1이고 재계획도 0회이므로, 실행 단계에 들어가기 전 계획을 유효한 JSON으로 파싱하지 못한 실패임을 로그에서 확인할 수 있다. Plan 방식의 평균 토큰이 더 적은 것은 단순히 효율성이 높아서가 아니라 두 실행이 초기에 실패했기 때문이다. 유일하게 성공한 Plan 실행은 재계획 1회를 사용했고, ReAct의 각 성공 실행보다 많은 7회 호출과 15,382토큰을 소비했다. 따라서 이번 실험에서는 ReAct의 단계별 컨텍스트 갱신과 유연한 오류 복구가 성공률을 높였고, Plan-then-Execute의 엄격한 계획 형식과 제한된 재계획 조건은 초기 계획 오류에 취약하게 작용했다.

