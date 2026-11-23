# Week 04 추가 실험: buyer 2 + seller 1 경쟁 협상

## 1. 설정

기존 buyer 1 + seller 1, 최대 8턴 baseline은 그대로 두고, 같은 시나리오·모델·temperature·메시지 형식을 buyer 2 + seller 1, 최대 4라운드로 바꿨다. 한 라운드에서 두 buyer가 서로의 현재 제안을 보지 않고 각각 제안한 뒤 seller가 하나를 수락하거나 둘 다 거절한다. 둘 다 거절하면 protocol layer가 두 buyer 모두에게 그 라운드의 `highest_valid_offer`를 알려 준다.

| 항목 | 값 |
|---|---|
| provider | OpenAI API (`https://api.openai.com/v1`) |
| model | `gpt-5.6-luna` |
| temperature | 0 |
| agents | `buyer_1`, `buyer_2`, `seller` |
| round limit | 4 (최대 agent call 12회) |
| repetitions | condition별 3회 × scenario 4개 = 36 episodes |
| conditions | `free`, `tagged`, `structured` |
| source | `competitive_negotiation.py`, `run_competitive_experiment.py` |
| data | `competitive_results.csv`, `competitive_logs/` |

형식 문단은 baseline과 동일하다. `free`는 plain English를 reader가 읽고, `tagged`는 맨 앞 performative tag를 정규식으로 읽은 뒤 proposal 가격과 accepted buyer를 reader가 읽으며, `structured`는 JSON을 local parser가 읽는다. reader는 대화의 마지막 메시지만 `propose`, `accept-proposal`, `reject-proposal`, `refuse` 중 하나로 분류하고 `price`와 `buyer_id`를 JSON으로 반환한다.

추가 규칙은 다음과 같다.

- strict highest valid bidder만 affinity `+1`; 동점은 아무도 받지 않고, budget 초과 제안은 대상에서 제외한다.
- affinity 1점은 seller의 선택에서 `reserve × 1%`만큼 effective offer에 더한다. 실제 거래 가격은 바꾸지 않는다.
- buyer budget 초과 penalty = `100 × max(0, offer - budget) / budget`.
- seller reserve penalty = `100 × max(0, reserve - accepted_price) / reserve`.
- seller opportunity penalty = `100 × max(0, highest_valid_offer - accepted_price) / highest_valid_offer`.

실행 명령은 다음과 같다.

```bash
python3 run_competitive_experiment.py --runs 3 --rounds 4
```

## 2. 결과

### 조건별 요약

| condition | episodes | outcomes (deal/no-deal/open) | correct | violations | mean rounds | mean agent calls | format errors | reader calls | mean total calls | affinity points | buyer penalty | seller reserve penalty |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| free | 12 | 12/0/0 | 9 (75.0%) | 3 | 1.00 | 3.00 | 0 | 36 | 6.00 | 0 | 0.0 | 82.5000 |
| tagged | 12 | 6/4/2 | 2 (16.7%) | 4 | 2.00 | 5.67 | 13 | 60 | 10.67 | 14 | 0.0 | 101.7858 |
| structured | 12 | 10/0/2 | 7 (58.3%) | 3 | 1.50 | 4.50 | 0 | 0 | 4.50 | 0 | 0.0 | 45.8333 |

`total calls = agent calls + reader calls`이다. 세 조건 모두 buyer budget 초과 penalty와 seller opportunity penalty는 0이었다. seller reserve penalty는 10건의 limit violation을 모두 수치화했다. affinity는 seller가 첫 라운드에 수락한 free·structured에서는 발생하지 않았고, tagged의 반복 거절에서만 14점 발생했다.

### 에피소드 전체

마지막 penalty 열은 `buyer_1 / buyer_2 / seller reserve / seller opportunity` 순서다.

| run | condition | scenario | outcome | winner | price | correct | violation | rounds | agent | fmt | reader | total | affinity (b1/b2) | penalties |
|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| 1 | free | headphones | deal | buyer_1 | 80 | 1 | 0 | 1 | 3 | 0 | 3 | 6 | 0/0 | 0/0/0/0 |
| 1 | free | coffee-maker | deal | buyer_2 | 91 | 0 | 1 | 1 | 3 | 0 | 3 | 6 | 0/0 | 0/0/24.1667/0 |
| 1 | free | textbook | deal | buyer_1 | 35 | 1 | 0 | 1 | 3 | 0 | 3 | 6 | 0/0 | 0/0/0/0 |
| 1 | free | desk-lamp | deal | buyer_2 | 84 | 1 | 0 | 1 | 3 | 0 | 3 | 6 | 0/0 | 0/0/0/0 |
| 1 | tagged | headphones | deal | buyer_1 | 85 | 1 | 0 | 1 | 3 | 0 | 3 | 6 | 0/0 | 0/0/0/0 |
| 1 | tagged | coffee-maker | deal | buyer_2 | 85 | 0 | 1 | 1 | 3 | 0 | 3 | 6 | 0/0 | 0/0/29.1667/0 |
| 1 | tagged | textbook | no_deal | — | — | 0 | 0 | 4 | 11 | 2 | 8 | 19 | 2/1 | 0/0/0/0 |
| 1 | tagged | desk-lamp | deal | buyer_2 | 60 | 0 | 1 | 1 | 3 | 0 | 3 | 6 | 0/0 | 0/0/14.2857/0 |
| 1 | structured | headphones | deal | buyer_1 | 100 | 1 | 0 | 1 | 3 | 0 | 0 | 3 | 0/0 | 0/0/0/0 |
| 1 | structured | coffee-maker | deal | buyer_2 | 85 | 0 | 1 | 1 | 3 | 0 | 0 | 3 | 0/0 | 0/0/29.1667/0 |
| 1 | structured | textbook | deal | buyer_1 | 45 | 1 | 0 | 1 | 3 | 0 | 0 | 3 | 0/0 | 0/0/0/0 |
| 1 | structured | desk-lamp | open | — | — | 0 | 0 | 4 | 12 | 0 | 0 | 12 | 0/0 | 0/0/0/0 |
| 2 | free | headphones | deal | buyer_1 | 80 | 1 | 0 | 1 | 3 | 0 | 3 | 6 | 0/0 | 0/0/0/0 |
| 2 | free | coffee-maker | deal | buyer_2 | 85 | 0 | 1 | 1 | 3 | 0 | 3 | 6 | 0/0 | 0/0/29.1667/0 |
| 2 | free | textbook | deal | buyer_2 | 35 | 1 | 0 | 1 | 3 | 0 | 3 | 6 | 0/0 | 0/0/0/0 |
| 2 | free | desk-lamp | deal | buyer_2 | 70 | 1 | 0 | 1 | 3 | 0 | 3 | 6 | 0/0 | 0/0/0/0 |
| 2 | tagged | headphones | no_deal | — | — | 0 | 0 | 1 | 3 | 0 | 2 | 5 | 0/0 | 0/0/0/0 |
| 2 | tagged | coffee-maker | deal | buyer_2 | 85 | 0 | 1 | 1 | 3 | 0 | 3 | 6 | 0/0 | 0/0/29.1667/0 |
| 2 | tagged | textbook | open | — | — | 0 | 0 | 4 | 12 | 4 | 12 | 24 | 0/4 | 0/0/0/0 |
| 2 | tagged | desk-lamp | no_deal | — | — | 0 | 0 | 4 | 10 | 3 | 8 | 18 | 1/2 | 0/0/0/0 |
| 2 | structured | headphones | deal | buyer_2 | 95 | 1 | 0 | 1 | 3 | 0 | 0 | 3 | 0/0 | 0/0/0/0 |
| 2 | structured | coffee-maker | deal | buyer_2 | 110 | 0 | 1 | 1 | 3 | 0 | 0 | 3 | 0/0 | 0/0/8.3333/0 |
| 2 | structured | textbook | deal | buyer_1 | 45 | 1 | 0 | 1 | 3 | 0 | 0 | 3 | 0/0 | 0/0/0/0 |
| 2 | structured | desk-lamp | deal | buyer_2 | 85 | 1 | 0 | 1 | 3 | 0 | 0 | 3 | 0/0 | 0/0/0/0 |
| 3 | free | headphones | deal | buyer_2 | 80 | 1 | 0 | 1 | 3 | 0 | 3 | 6 | 0/0 | 0/0/0/0 |
| 3 | free | coffee-maker | deal | buyer_2 | 85 | 0 | 1 | 1 | 3 | 0 | 3 | 6 | 0/0 | 0/0/29.1667/0 |
| 3 | free | textbook | deal | buyer_1 | 40 | 1 | 0 | 1 | 3 | 0 | 3 | 6 | 0/0 | 0/0/0/0 |
| 3 | free | desk-lamp | deal | buyer_2 | 85 | 1 | 0 | 1 | 3 | 0 | 3 | 6 | 0/0 | 0/0/0/0 |
| 3 | tagged | headphones | no_deal | — | — | 0 | 0 | 1 | 3 | 0 | 2 | 5 | 0/0 | 0/0/0/0 |
| 3 | tagged | coffee-maker | deal | buyer_2 | 85 | 0 | 1 | 1 | 3 | 0 | 3 | 6 | 0/0 | 0/0/29.1667/0 |
| 3 | tagged | textbook | open | — | — | 0 | 0 | 4 | 11 | 4 | 10 | 21 | 3/1 | 0/0/0/0 |
| 3 | tagged | desk-lamp | deal | buyer_2 | 85 | 1 | 0 | 1 | 3 | 0 | 3 | 6 | 0/0 | 0/0/0/0 |
| 3 | structured | headphones | deal | buyer_2 | 95 | 1 | 0 | 1 | 3 | 0 | 0 | 3 | 0/0 | 0/0/0/0 |
| 3 | structured | coffee-maker | deal | buyer_2 | 110 | 0 | 1 | 1 | 3 | 0 | 0 | 3 | 0/0 | 0/0/8.3333/0 |
| 3 | structured | textbook | deal | buyer_1 | 45 | 1 | 0 | 1 | 3 | 0 | 0 | 3 | 0/0 | 0/0/0/0 |
| 3 | structured | desk-lamp | open | — | — | 0 | 0 | 4 | 12 | 0 | 0 | 12 | 0/0 | 0/0/0/0 |

## 3. 비교

### 기존 1×8 baseline과 2×4 경쟁 구조

| condition | correct 1×8 → 2×4 | violations 1×8 → 2×4 | mean agent calls 1×8 → 2×4 | mean total calls 1×8 → 2×4 |
|---|---:|---:|---:|---:|
| free | 10/12 → 9/12 | 0 → 3 | 6.67 → 3.00 | 13.33 → 6.00 |
| tagged | 5/12 → 2/12 | 1 → 4 | 5.67 → 5.67 | 10.00 → 10.67 |
| structured | 6/12 → 7/12 | 0 → 3 | 5.67 → 4.50 | 5.67 → 4.50 |

경쟁 구조는 한 라운드에 agent call이 3회이므로 rounds와 calls를 구분했다. free와 structured는 첫 라운드 수락이 많아 호출 비용이 줄었지만, correct가 일관되게 좋아지지는 않았다. tagged는 reader가 accepted buyer를 잘못 표현한 실패가 반복되어 비용과 오류가 함께 증가했다.

### FIPA-ACL과 세 메시지 형식

| 항목 | FIPA-ACL | free | tagged | structured |
|---|---|---|---|---|
| illocutionary force | 필수 performative | reader가 자연어에서 추론 | 앞쪽 tag | JSON performative |
| content 언어 | 선언한 language/ontology | plain English | tag 뒤 plain English | 고정 JSON schema |
| content 해석자 | 수신 agent와 ontology | LLM reader | 정규식 + LLM reader | local parser |
| 종료 | interaction protocol | reader가 읽은 accept/refuse 또는 4라운드 | tag/reader가 읽은 accept/refuse 또는 4라운드 | parsed accept/refuse 또는 4라운드 |
| sincerity/한도 보장 | FP/RE가 기술하나 외부 검증 불가 | 사후 penalty만 기록 | 사후 penalty만 기록 | syntax 검증 + 사후 penalty; 전략은 미보장 |
| 메시지 읽기 비용 | formal interpretation | 매 메시지 reader 1회 | proposal·acceptance에 reader | reader 0회 |
| 관찰된 실패 | 정신 상태 검증 불가 | seller의 reserve 위반 | buyer id 오독이 acceptance를 rejection으로 바꿈 | 유효 JSON으로 비합리적 저가 반복 |

## 4. 해석

첫째, 두 buyer의 경쟁은 협상을 빠르게 만들었지만 안전하게 만들지는 않았다. free는 모든 에피소드가 1라운드 deal이어서 평균 agent call이 baseline 6.67에서 3.00으로 줄었지만, 거래가 불가능한 coffee-maker에서도 seller가 세 번 모두 reserve 120 아래의 제안을 받아 correct가 9/12에 그쳤다. 예를 들어 [free-02 13–23행](competitive_logs/free-02.jsonl#L13)에서 두 buyer의 75·85 제안 뒤 seller가 85를 즉시 수락했고, harness는 `seller_reserve_penalty=29.1667`과 violation을 남겼다. penalty는 책임 소재와 손실 크기를 보이게 했지만, 사후 기록이므로 위반 자체를 막지는 못했다.

둘째, 최고가 공개와 affinity는 정상적인 경쟁 보상이라기보다 protocol error를 증폭하기도 했다. [tagged-02 24–33행](competitive_logs/tagged-02.jsonl#L24)에서 seller는 buyer 2의 31을 수락했지만 reader가 `buyer_id: 2`를 반환했다. schema가 요구하는 `buyer_2`가 아니어서 acceptance가 parse error와 rejection으로 바뀌었고 buyer 2가 affinity를 받았다. 같은 오류가 네 라운드 반복되어 [64행](competitive_logs/tagged-02.jsonl#L64)에서는 실제로 매번 수락 의사가 있었는데도 `open`, format error 4, affinity 4, total call 24가 됐다. 즉 affinity 계산 자체는 규칙대로였지만, 그 입력인 “거절”이 잘못 만들어지면 호감도가 잘못된 행동을 보상한다. 책임을 피하려는 일반론의 문제라기보다, 이 실행에서는 식별자 schema의 작은 불일치가 사회적 기록을 오염시킨 것이 핵심이다.

셋째, structured는 reader 비용과 형식 오류를 제거했지만 합리적 전략을 보장하지 않았다. [structured-01 26–55행](competitive_logs/structured-01.jsonl#L26)에서 두 buyer는 동률 1, 2, 3, 4만 제안했다. 매 라운드 최고가는 둘에게 정확히 broadcast되었지만 두 buyer가 같은 값만 따라가서 strict-highest 규칙상 affinity는 끝까지 0이었고, 실제로 70 이상을 낼 수 있는 buyer 2가 있는데도 `open`으로 끝났다. 형식적 유효성과 전략적 유효성은 별개라는 사례다.

마지막으로 이번 36회에서는 buyer budget 초과와 seller opportunity loss가 0이었다. 따라서 설계한 세 penalty 가운데 실제 변별력을 보인 것은 seller reserve penalty뿐이었다. 후속 실험에서는 penalty를 로그에만 쓰지 말고 protocol gate로 사용해 `accepted_price < reserve` 또는 `accepted_price > winner budget`인 acceptance를 거부하고, reader의 `buyer_id`를 `2 → buyer_2`처럼 정규화하는 조건을 별도 축으로 비교해야 한다. 그래야 “책임 기록”과 “위반 예방”의 효과를 분리해서 측정할 수 있다.
