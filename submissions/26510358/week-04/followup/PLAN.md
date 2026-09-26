# Week 04 보강 실험 사전 계획

이 문서는 추가 모델 호출과 로그 재판독 전에 고정한다. 기존 `results.csv`, `REPORT.md`, `logs/free-*`, `logs/tagged-*`, `logs/structured-*`는 수정하지 않는다. 기존 PR #212의 브랜치도 이번 작업 중에는 수정하지 않는다.

## 범위와 분리

1. **의미 판독 감사**: 기존 36개 에피소드의 free/tagged 발화를 모두 읽는다. 원문에 명시된 마지막 발화의 주된 행위를 `propose`/`accept-proposal`/`reject-proposal`/`refuse`/`ambiguous`로 주석화한다. `propose` 가격은 화자가 새로 제안한 금액이다. “기존 가격은 거절하지만 X를 제안한다”는 `propose`로, 제안 없이 거절하면 `reject-proposal`로 읽는다. 수락·거절에 언급된 이전 가격은 새 제안 가격으로 세지 않는다. 다른 해석이 가능한 문장은 `ambiguous`로 남기고 확실한 행위 일치율의 분모에서 제외한다. tagged는 태그와 본문의 의미 불일치 및 reader의 제안 가격 오독을 따로 센다. structured는 자연어 본문이 없으므로 기계적 스키마 검사만 감사한다. Codex 단일 주석자라는 한계를 명시한다.
2. **종료 행동**: 원본과 독립된 추가 실험을 한다. buyer/seller 모두에 똑같은 종료 지침을 공통 프롬프트에 덧붙인다. 가능한 상대 제안을 받으면 수락하고, 남은 턴 안에 한도 내 제안을 기대하기 어렵다면 `refuse`로 명시적으로 떠나라고 한다. reserve/budget을 상대에게 알려주거나 harness가 결과를 미리 정하지 않는다. 세 형식의 차이는 다시 형식 문단과 reader만으로 제한한다. 기존 결과와 합쳐 인과 효과로 해석하지 않고 별도 표로 둔다.
3. **제안 상태 규칙**: FIPA의 `accept-proposal` 대상인 제안의 유효성을 명확히 한 `pending` 상태기를 별도 모듈로 구현한다. 새로운 제안은 이전 것을 대체하고 `reject-proposal`은 현재 제안을 닫는다. 닫힌 제안에 대한 뒤늦은 `accept-proposal`은 거래가 아니다. 이 해석은 수업의 단순한 “상대의 마지막 가격”보다 엄격하므로 원본 실행 코드를 바꾸지 않는다. 합성 테스트와 기존 메시지 재생으로 영향 범위를 확인한다.
4. **반복과 순서**: 원본과 동일한 프롬프트·파서·모델 설정·시나리오로 조건별 3회씩 추가 실행한다. 추가 블록의 조건 순서는 (structured, tagged, free), (tagged, free, structured), (free, structured, tagged)로 고정한다. 종료 지침 실험도 조건별 3회씩 수행하며 블록별 순서는 (tagged, structured, free), (free, tagged, structured), (structured, free, tagged)로 고정한다. 시나리오 순서는 각 run에서 원본과 같다. 실행 순서를 기록하고 표본 수·조건별 결과·시나리오별 결과를 함께 보고한다. 같은 API라도 생성의 확률성과 시간 변화 때문에 재현은 완전한 동일 출력이 아닌 경향 수준이다.

## 측정과 기록

- 기존 `results.csv`와 로그는 원본 그대로 보존한다. 추가 실험은 `followup/replication.csv`, `followup/termination.csv`, 새 원본 콘솔 로그는 `logs/`에 각각 별도 이름으로 저장한다.
- 추가 CSV는 Week 04의 12열 계약을 그대로 사용한다. 최소 9개 로그와 36개 행을 연구별로 만들고 중단 시 `(run, scenario)` 단위로 재개한다. 실패한 에피소드는 삭제하지 않는다.
- 종료 연구의 공통 지침, 실행 순서, 코드 버전과 해석 기준을 결과 보기 전에 확정한다. 두 연구 간의 성과 차이는 프롬프트 효과에 대한 탐색적 관찰로만 다룬다.

## 종료 지침 원문 (첫 추가 실행 전 고정)

```text
In this follow-up, use a deadline policy. Each side has at most four messages. If the other party's latest offered price is within your private limit, choose accept-proposal rather than another counter-offer. On your fourth message, if no offer you can accept has arrived, use refuse and leave; do not make a new offer or reject on that final turn. Never reveal either party's private limit.
```
