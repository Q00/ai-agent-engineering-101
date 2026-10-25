# 공통 역할표 적용 후 최종 세 조건 실험 사전 규약

2026-09-21. 코드·설정·입력·정답·이 규약을 커밋한 뒤 실행한다.
기존 PROTOCOL.md와 과거 로그는 당시 실험의 기록으로 보존한다.

## 고정 조건

- 동일 case.json의 합성 출시 검토 과제를 조건별 3회, 총 9회 실행한다.
- baseline: A 제품·기술, B 사업·분석, C 운영·커뮤니케이션. 모든 단계에서 공통 팀 역할표를 제공한다.
- homogeneous: 본인 전문성 및 공통 역할표의 모든 전문성을 동일한 범용 역할로 교체한다.
- overconfident: baseline에서 C의 propose에만 항상 입찰하고 confidence>=95를 반환하라는 지시를 추가한다.
- 모델 deepseek/deepseek-v4.1-flash, temperature=0, max_tokens=2200, reasoning disabled.
- provider.only=[fireworks], require_parameters=true. 모든 호출에 단계별 strict JSON Schema를 전달한다.
- 요청자 A, max_depth=5, max_tasks=12, max_steps=4, max_calls=64, max_parallel=3.
- 작업별 독립 세션, 고정 선정 규칙, 의존성·자원 충돌 검사, 새 Runtime, 장기 메모리 없음.
- 12개 expected.json 정답은 사후 평가에만 사용한다. 실험 중 프롬프트·정답·스키마·전송 설정을 바꾸지 않는다.

## 실행 일정과 실패 처리

직전 단일 실행에서도 HTTP 429가 반복됐다. 실험끼리의 중첩 부하를 줄이기 위해
실험은 하나씩 실행하고 매 실험 사이 15초를 기다린다. 각 실험 내부의 최대 3개 병렬 호출은 유지한다.
세 블록의 순서는 baseline/homogeneous/overconfident, homogeneous/overconfident/baseline,
overconfident/baseline/homogeneous로 순환한다.

실험당 벽시계 한도는 600초다. HTTP 요청당 기존 최대 시도 2회, 재시도 대기 2초를 유지한다.
성공한 실행만 골라 남기거나 실패를 대체하는 추가 실험은 하지 않는다. 오류·원본·부분 결과를 모두 보존한다.
입찰 파싱 실패는 기존 런타임의 거절 규칙, 실행 실패는 기존 실패 전파 규칙을 따른다.

```bash
submissions/26622007/.venv/bin/python -u submissions/26622007/week-03/extensions/peer_dag/condition_study.py --serial --gap-seconds 15 --protocol FINAL_PROTOCOL.md
```

## 측정과 해석

- 전체 완료 및 12개 정답 통과율, 미완료/완료했으나 오답/HTTP 오류/형식 오류를 구분한다.
- 조건당 분모는 3회와 36개 필드다. 최종 산출물 없는 실행은 0/12로 기록하되 12개 오답이라고 해석하지 않는다.
- 핵심 facts 벡터 종류 수와 산출물 수, 전체 산출물·계획·배정의 반복 차이를 별도로 기록한다.
- 실제 execute/synthesize 호출 겹침 및 동일 Worker의 겹침, 의존성 순서, 실제 깊이, 호출·비용·시간을 검사한다.
- 공통 역할표·프롬프트·response_format의 실제 요청 전달 및 수신 응답의 스키마를 독립 검증한다.
- C 배정 비율, 루트 선정, confidence>=95 지시 준수를 기록한다. 모델이 만든 하위 작업에 사후 gold를 부여하지 않는다.
- 사례 하나의 3회 반복이므로 일반 성능이나 완전한 결정성을 주장하지 않는다. 핵심 값의 일치와 전체 계획·문장 일치를 구분한다.
- 과거에는 프롬프트·스키마·라우팅·동시성 정책이 달랐다. 이번 결과와 합쳐 역할표만의 인과적 효과를 주장하지 않는다.
- 실제 LLM 실행과 기록된 응답을 고정한 replay의 보장 범위는 다르다. 필요시 성공 실행을 순차/병렬 replay하고 따로 보고한다.
- 강의 기본 Contract Net의 tasks/results/logs와 합산하지 않는 현재 peer DAG 구조의 확장 실험이다.
