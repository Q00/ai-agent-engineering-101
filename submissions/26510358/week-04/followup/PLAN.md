# Week 04 보강 실험 사전 계획

추가 모델 호출과 로그 재판독 전에 이 계획을 고정함
기존 `results.csv`, `REPORT.md`, `logs/free-*`, `logs/tagged-*`, `logs/structured-*`는 실험 데이터 수집 중 수정하지 않음
보강 실험 수집 중 기존 PR #212 브랜치도 유지함

## 범위와 분리

1. **의미 판독 감사**: 기존 36개 에피소드의 free/tagged 발화를 모두 읽음
   마지막 발화의 주된 행위를 `propose`/`accept-proposal`/`reject-proposal`/`refuse`/`ambiguous`로 주석화함
   `propose` 가격은 화자가 새로 제안한 금액으로 정함
   기존 가격을 거절하며 새 가격 X를 제시하면 `propose`, 새 제안 없이 거절하면 `reject-proposal`로 판정함
   수락·거절에 언급된 기존 가격은 새 제안 가격으로 세지 않음
   다른 해석도 가능한 문장은 `ambiguous`로 남기고 명확한 행위 일치율의 분모에서 제외함
   tagged의 태그·본문 의미 불일치와 reader의 제안 가격 오독을 각각 셈
   structured는 자연어 본문이 없으므로 기계적 스키마만 검사함
   Codex 단일 주석자라는 한계를 명시함
2. **종료 행동**: 원본과 분리된 추가 실험을 진행함
   buyer와 seller의 공통 프롬프트에 같은 종료 지침을 추가함
   수락 가능한 상대 제안이 오면 수락하고, 남은 턴 안에 한도 내 제안을 기대하기 어려우면 `refuse`로 떠나도록 요청함
   reserve/budget을 상대에게 공개하거나 harness에서 결과를 미리 정하지 않음
   세 조건의 차이는 형식 문단과 reader로 한정함
   원본과 합쳐 인과 효과로 해석하지 않고 별도 표에 기록함
3. **제안 상태 규칙**: `accept-proposal` 대상 제안의 유효성을 표시하는 `pending` 상태기를 별도 모듈에 구현함
   새 제안은 이전 제안을 대체하고 `reject-proposal`은 현재 제안을 닫음
   닫힌 제안의 뒤늦은 `accept-proposal`은 거래로 세지 않음
   수업의 단순한 “상대의 마지막 가격”보다 엄격한 해석이므로 원본 실행 코드는 유지함
   합성 테스트와 기존 메시지 재생으로 결과 차이를 확인함
4. **반복과 순서**: 원본과 같은 프롬프트·파서·모델 설정·시나리오로 조건별 3회씩 추가 실행함
   추가 블록의 조건 순서는 (structured, tagged, free), (tagged, free, structured), (free, structured, tagged)로 고정함
   종료 지침 실험은 (tagged, structured, free), (free, tagged, structured), (structured, free, tagged) 순서로 조건별 3회씩 수행함
   시나리오 순서는 각 run에서 원본과 같음
   실행 순서·표본 수·조건별 결과·시나리오별 결과를 함께 보고함
   생성의 확률성과 시점 차이로 인해 동일 출력보다 경향 수준의 재현을 기대함

## 측정과 기록

- 기존 `results.csv`와 로그는 원본 그대로 보존함
- 추가 결과는 `followup/replication.csv`, `followup/termination.csv`에 분리하고 원본 콘솔 로그는 `logs/`에 별도 이름으로 저장함
- 추가 CSV에는 Week 04의 12열 계약을 그대로 적용함
- 연구별로 최소 9개 로그와 36개 행을 만들고 중단 시 `(run, scenario)` 단위로 재개함
- 실패한 에피소드도 삭제하지 않음
- 종료 연구의 공통 지침·실행 순서·코드 버전·해석 기준을 결과 전에 고정함
- 두 연구 간 성과 차이는 프롬프트 효과에 관한 탐색적 관찰로만 다룸

## 종료 지침 원문 (첫 추가 실행 전 고정)

```text
In this follow-up, use a deadline policy. Each side has at most four messages. If the other party's latest offered price is within your private limit, choose accept-proposal rather than another counter-offer. On your fourth message, if no offer you can accept has arrived, use refuse and leave; do not make a new offer or reject on that final turn. Never reveal either party's private limit.
```
