# Week 04 실험 설계

## 질문

같은 buyer·seller·시나리오에서 메시지 형식만 `free`, `tagged`, `structured`로 바꾸면
정답, 한도 위반, 턴 수, 형식 오류, reader 호출 수가 어떻게 달라지는가?

## 고정하는 것과 바꾸는 것

- 고정: 네 시나리오, 역할·한도 문단, 네 행위의 뜻, 모델, temperature, 8턴 상한,
  reader 프롬프트, buyer 선공, 조건별 3회.
- 변경: system prompt의 마지막 형식 문단과 메시지를 읽는 프로토콜 계층.
- 비공개: seller의 reserve는 seller만, buyer의 budget은 buyer만 본다.

## 시나리오 선택

| id | 상황 | reserve | budget | 사전 정답 | 선택 이유 |
|---:|---|---:|---:|---|---|
| 1 | used portable monitor | 120 | 150 | deal 가능 | 일반적인 가격 겹침 |
| 2 | mechanical keyboard | 90 | 90 | deal 가능 | 한 점에서만 가능한 경계 |
| 3 | second-hand bicycle | 120 | 100 | no deal | 작은 비겹침 |
| 4 | noise-cancelling headphones | 180 | 120 | no deal | 큰 비겹침 |

시나리오는 커밋 `1e2724e`에서 첫 실행 전에 고정했다. 결과를 보고 한도를 바꾸지 않는다.

## 구성

```text
buyer LLM ── message ──> protocol reader ── parsed act/price ──> episode state
    ▲                         │                                      │
    │                         ├─ free: LLM reader                    │
    │                         ├─ tagged: regex + propose price reader│
    │                         └─ structured: JSON parser             │
    │                                                                ▼
seller LLM <─ delivered message ───────────────────────────── result/log/CSV
```

- 형식을 읽지 못해도 원문 메시지는 상대에게 전달한다.
- `accept-proposal`은 상대의 마지막으로 기록된 제안 가격을 수락한다.
- 상대 가격이 기록되지 않은 수락은 거래로 만들지 않고 형식 오류로 남긴다.
- 429는 증가 대기 후 다시 시도하고, 저장된 `(run, condition, scenario)`는 재개 시 건너뛴다.

## 예상되는 관찰

- free: 자연어의 정보는 많이 남지만 reader 비용과 행위·가격 오독 가능성이 큼.
- tagged: 행위 오독은 줄지만 태그 뒤 역제안을 프로토콜 상태가 놓칠 수 있음.
- structured: reader 비용은 0이지만 JSON 뒤 문장이나 `price=null`의 실제 가격을 버릴 수 있음.
- 공통: 형식은 agent가 자기 한도를 실제로 지키는지 보장하지 못함.
