# Week 04 확장 실험 — 상태 기반 혼합 프로토콜

## 연구 질문

`free`, `tagged`, `structured`를 에피소드별 고정 조건으로 쓰지 않고 협상 상태에 맞게
전환하면, 기존 조건보다 합의 정확도와 한도 안전성을 높이면서 해석 비용을 줄일 수 있는가?

## 핵심 설계

| 상태 | 공개 메시지 형식 | 전환 조건 | 처리기 |
|---|---|---|---|
| discovery | free | 첫 유효 제안 가격을 읽음 | 매 메시지 LLM reader |
| bargaining | tagged | `accept-proposal` 후보를 읽음 | 태그 정규식, 제안 가격만 LLM reader |
| settlement | structured | 양쪽 확인과 양쪽 로컬 한도 검사 | JSON parser와 결정론적 guard |

- discovery에서는 가격을 아직 정하지 않은 질문·설명이 자연스럽게 오갈 수 있다.
- 첫 유효 가격이 상태에 기록되면 이후 공개 발화는 tagged로 바뀐다.
- 공개 `accept-proposal`은 거래 확정이 아니라 settlement 요청이다.
- settlement에서는 buyer와 seller를 각각 한 번 호출한다. 각 역할은 후보 가격과 자기 한도만
  보고 `confirm-deal` 또는 `abort-deal` JSON을 반환한다.
- 두 JSON이 모두 후보 가격을 확인하고 buyer guard(`price <= budget`)와 seller
  guard(`price >= reserve`)가 모두 통과할 때만 `deal`을 커밋한다.
- 확인 실패·중단·guard 거부가 생기면 거래를 만들지 않고 bargaining으로 돌아간다.
- settlement JSON은 상대에게 전달하지 않으며 공개 협상 턴에도 포함하지 않는다.

```text
discovery/free
  └─ 첫 유효 가격 ─> bargaining/tagged
                         └─ 수락 후보 ─> settlement/structured
                                           ├─ 양쪽 확인 + 양쪽 guard 통과 ─> deal
                                           └─ 실패·중단·거부 ─────────────> bargaining
```

## 비교 통제

- 기존 `results.csv`와 로그는 수정하지 않는다.
- 같은 모델, temperature, 4개 시나리오, buyer 선공, 최대 8개 공개 발화, 반복 3회를 쓴다.
- 실행 순서는 반복 1~3 안에서 시나리오 1~4로 고정한다.
- 새 결과는 `extension/hybrid_results.csv`와 `extension/logs/`에 따로 저장한다.
- 기존 세 조건과 같은 핵심 지표를 기록하고, 확장 전용으로 `settlement_calls`,
  `settlement_errors`, `settlement_vetoes`, `guard_vetoes`를 추가한다.

## 사전 해석 기준

- guard가 있으므로 hybrid의 한도 위반 0건은 모델의 자율적 판단 성과로 해석하지 않는다.
  이는 아키텍처가 보장한 안전성이다.
- `correct`가 올라가도 거래 가능 상황에서 실제 deal이 늘었는지 따로 본다. 거래 불가능
  상황의 `open`도 기존 채점에서는 정답이기 때문이다.
- reader 호출 수만으로 비용을 비교하지 않는다. hybrid는 settlement에서 추가 모델 호출이
  생기므로 전체 API 시도와 토큰도 함께 비교한다.
- 12개 에피소드의 후속 탐색 실험이며 통계적 일반화나 기존 조건의 공정한 순위표를 주장하지
  않는다.

## 구현 경계

- 공개 협상 행위 네 가지는 과제와 동일하게 유지한다.
- `confirm-deal`과 `abort-deal`은 공개 행위를 늘리는 것이 아니라 내부 settlement 제어
  메시지다.
- 각 역할 프롬프트에는 자기 한도만 넣고 상대 한도는 넣지 않는다.
- 모든 파싱 실패와 모델 실패를 로그에 보존하며 완료 키 `(run, scenario)`로 재개한다.
