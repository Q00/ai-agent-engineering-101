# Week 02 — Harness A/B 실험 보고서

## 1. 변형 정의와 실험 조건

두 하네스는 동일한 태스크(`app.log`에서 ERROR가 가장 많은 시간을 `HH:00`으로 답하기), 성공 기준(`14:00` 포함), 모델(OpenRouter `nvidia/nemotron-3.5-lightning:free`), 공용 모델 래퍼와 읽기 전용 도구 `read_file`, `count_pattern`을 사용했다. 다섯 요소 중 **도구 세분성**과 **사람 개입점**은 동일하며, 쓰기 도구가 없어 개입 대상도 없다. **컨텍스트 관리**는 ReAct가 한 대화의 전체 이력을 매 반복에 사용하는 반면 Plan은 별도 planner가 계획을 만들고 executor가 계획과 단계별 누적 이력을 사용한다. **종료 조건**은 ReAct가 도구 호출 없는 최종 응답 또는 `max_steps=8`에서 끝나고, Plan은 계획 실행 후 최종 응답을 생성하되 전체 `max_steps=16`과 단계당 도구 라운드 3회를 적용한다. **오류 복구**는 ReAct가 도구 오류를 Observation으로 돌려 다음 반복에서 복구하고, Plan은 `OFF_PLAN` 시 최대 1회만 재계획한다. 토큰은 API usage의 입력+출력 토큰 합, iterations는 모델 호출 수, interventions는 사람 승인/거절 횟수다. 각 실행의 프롬프트·도구·상한은 `logs/`에 있으며, 실행 명령은 `./.venv/bin/python run_ab.py --runs 3`이다.

## 2. 측정 결과

`results.csv`의 9개 실행을 모두 표시했다. Run 4~6은 OpenRouter HTTP 429에 의한 외부 환경 실패로 보존하되 성능 통계에서는 제외했다.

| run | harness | success | tokens | iterations | interventions | note |
|---:|---|:---:|---:|---:|---:|---|
| 1 | react | O | 4,411 | 2 | 0 | — |
| 2 | react | O | 4,454 | 2 | 0 | — |
| 3 | react | O | 3,860 | 2 | 0 | — |
| 4 | plan_exec | X | — | — | — | HTTP 429 rate limit |
| 5 | plan_exec | X | — | — | — | HTTP 429 rate limit |
| 6 | plan_exec | X | — | — | — | HTTP 429 rate limit |
| 7 | plan_exec | O | 36,692 | 13 | 0 | replans=1 |
| 8 | plan_exec | O | 42,024 | 13 | 0 | replans=1 |
| 9 | plan_exec | O | 32,271 | 10 | 0 | replans=0 |

| 유효 실행 통계(모집단 분산) | ReAct | Plan-then-Execute |
|---|---:|---:|
| 유효 실행 / 성공률 | 3 / 100% | 3 / 100% |
| 토큰 평균 / 분산 | 4,241.67 / 73,142.89 | 36,995.67 / 15,899,608.22 |
| iterations 평균 / 분산 | 2.00 / 0.00 | 12.00 / 2.00 |
| interventions 평균 / 분산 | 0.00 / 0.00 | 0.00 / 0.00 |

## 3. 해석

두 하네스는 유효 실행 3회 모두 성공했고 사람 개입도 0회로 동률이었지만, 이 태스크에서는 ReAct가 평균 토큰과 반복 횟수 및 그 안정성에서 우세했다. ReAct 로그는 세 번 모두 `read_file` 관찰 뒤 두 번째 모델 호출에서 `14:00`을 결정해 2회 반복으로 끝난 반면, Plan 로그는 답을 얻기 전에 계획을 생성하고 각 단계를 누적 대화 이력으로 순차 실행한 뒤 최종 답변을 별도로 생성하여 10~13회 호출을 사용했다. 특히 run 7과 8은 단계당 도구 호출 상한을 넘어 `OFF_PLAN`이 발생하고 재계획 1회가 추가되어, Plan의 평균 토큰이 36,995.67로 ReAct의 4,241.67보다 높고 토큰 분산과 반복 분산도 더 커졌다. 따라서 파일을 한 번 읽은 뒤 바로 집계할 수 있었던 이번 단순 태스크에서는 Plan의 선행 계획·단계 실행·재계획 비용이 성공률 이점으로 이어지지 않아, 성공률과 개입은 동률이지만 토큰 효율·호출 효율·일관성에서는 ReAct가 더 적합했다.
