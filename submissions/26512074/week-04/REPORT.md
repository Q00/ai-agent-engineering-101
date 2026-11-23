# Week-04 Report

## 1. 설정

### 실험 목적

buyer 1명과 seller 1명이 가격을 협상하는 환경에서 메시지 형식만 `free`, `tagged`, `structured`로 변경하고, 형식에 따른 협상 결과를 비교하였다.

측정한 지표는 다음과 같다.

* `correct`: 시나리오에서 기대한 결과와 실제 결과가 일치했는지
* `violation`: 거래 가격이 buyer의 budget 또는 seller의 reserve를 위반했는지
* `turns`: 협상에 사용된 메시지 수
* `format_errors`: 프로토콜 계층이 메시지를 읽지 못한 횟수
* `reader_calls`: 메시지 해석을 위해 reader 모델을 호출한 횟수

4개 시나리오를 각 조건에서 3회씩 실행하여 총 36개 episode를 수행했다. `reserve ≤ budget`인 시나리오 1~3은 거래 가능, `reserve > budget`인 시나리오 4는 거래 불가능하도록 실행 전에 고정하였다.

### 실행 환경

```text
provider=openrouter
base_url=https://openrouter.ai/api/v1
model=deepseek/deepseek-v4.1-flash
temperature=0.3
max_turns=8
```

### 시나리오

| ID | 물건                     | Seller reserve | Buyer budget | 협상 조건                                                                        |
| -: | ---------------------- | -------------: | -----------: | ---------------------------------------------------------------------------- |
|  1 | a used bicycle         |            120 |          150 | buyer가 먼저 가격을 제안하며 budget 이내에서 낮게 시작한다.                                      |
|  2 | a desk lamp            |             30 |           45 | seller가 의도적으로 높은 anchor를 제시하며, buyer가 지나치게 낮은 가격을 제시하면 seller가 refuse할 수 있다. |
|  3 | a second-hand textbook |             40 |           40 | 양측의 가능 가격이 정확히 40으로 동일하여 빠르게 합의할 수 있다.                                       |
|  4 | a mechanical keyboard  |             90 |           70 | 유효한 거래 가격이 존재하지 않으며, seller가 reserve보다 20 이상 낮은 제안 이후 refuse할 수 있다.          |

### 공통 역할 및 프로토콜

세 조건에서 buyer와 seller의 역할, 개인 한도, 네 가지 행위의 의미는 동일하게 유지하였다.

* `propose`: 가격을 제안한다.
* `accept-proposal`: 상대방의 마지막 가격을 수락하고 거래를 종료한다.
* `reject-proposal`: 상대방의 마지막 가격을 거절하고 협상을 계속한다.
* `refuse`: 협상을 종료하고 거래하지 않는다.

buyer가 먼저 메시지를 보내고 이후 두 에이전트가 번갈아 메시지를 보낸다. 최대 8턴까지 진행하며 그 안에 `accept-proposal` 또는 `refuse`가 나오지 않으면 `open`으로 종료한다.

### 조건별 형식 문단

**free**

```text
Write your message as one or two plain English sentences.
```

평문으로 메시지를 생성하며, 프로토콜 계층이 마지막 메시지를 reader 모델에 전달해 performative와 가격을 해석한다.

**tagged**

```text
Start your message with exactly one performative tag in parentheses, one of (propose), (accept-proposal), (reject-proposal), (refuse), then write one plain English sentence.
```

메시지의 맨 앞에 있는 performative 태그는 정규식으로 읽고, `propose`인 경우 가격은 reader 모델이 해석한다.

**structured**

```text
Reply with exactly one JSON object and nothing else: {"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "content": {"price": <whole number or null}}.
```

JSON의 `performative`와 `content.price`를 별도의 reader 호출 없이 parser가 직접 읽는다.

### Reader 프롬프트

free 조건에서는 마지막 메시지만 읽고 다음과 같은 JSON을 반환하도록 reader를 구성하였다.

```text
You are an observer reading a price negotiation between a buyer and a seller.
Label the LAST message only.
Reply with exactly one JSON object and nothing else:
{"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse",
 "price": <whole number or null>}.
```

tagged 조건에서도 가격 해석에는 같은 reader를 사용하였다.

### 실행 명령

```bash
python run_experiment.py
```

---

## 2. 결과표

### 조건별 집계

| Condition  | Correct | Accuracy | Violation | Avg. turns | Format errors | Reader calls |
| ---------- | ------: | -------: | --------: | ---------: | ------------: | -----------: |
| free       |   11/12 |    91.7% |         0 |       3.50 |          0.00 |         3.50 |
| tagged     |   12/12 |   100.0% |         0 |       5.17 |          0.00 |         2.58 |
| structured |    3/12 |    25.0% |         0 |       6.92 |          4.17 |         0.00 |

`structured`의 `format_errors`는 episode당 평균 4.17회이며, 12개 episode 전체에서는 **50회** 발생하였다.

전체 36개 episode에서 `violation`은 **0회**였다.

### Episode 전체 결과

| run | condition  | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note                                                      |
| --: | ---------- | -------: | ------------: | ------- | ----: | ------: | --------: | ----: | ------------: | -----------: | --------------------------------------------------------- |
|   1 | tagged     |        1 |             1 | deal    |   130 |       1 |         0 |     6 |             0 |            3 | model_calls=9; prompt_tokens=2916; completion_tokens=160  |
|   1 | tagged     |        2 |             1 | deal    |    30 |       1 |         0 |     8 |             0 |            4 | model_calls=12; prompt_tokens=4045; completion_tokens=205 |
|   1 | tagged     |        3 |             1 | deal    |    40 |       1 |         0 |     2 |             0 |            1 | model_calls=3; prompt_tokens=847; completion_tokens=46    |
|   1 | tagged     |        4 |             0 | no_deal |       |       1 |         0 |     6 |             0 |            3 | model_calls=9; prompt_tokens=2952; completion_tokens=157  |
|   2 | tagged     |        1 |             1 | deal    |   120 |       1 |         0 |     4 |             0 |            2 | model_calls=6; prompt_tokens=1814; completion_tokens=105  |
|   2 | tagged     |        2 |             1 | deal    |    30 |       1 |         0 |     8 |             0 |            4 | model_calls=12; prompt_tokens=4127; completion_tokens=224 |
|   2 | tagged     |        3 |             1 | deal    |    40 |       1 |         0 |     2 |             0 |            1 | model_calls=3; prompt_tokens=847; completion_tokens=46    |
|   2 | tagged     |        4 |             0 | no_deal |       |       1 |         0 |     8 |             0 |            4 | model_calls=12; prompt_tokens=4221; completion_tokens=218 |
|   3 | tagged     |        1 |             1 | deal    |   130 |       1 |         0 |     6 |             0 |            3 | model_calls=9; prompt_tokens=2957; completion_tokens=167  |
|   3 | tagged     |        2 |             1 | deal    |    30 |       1 |         0 |     2 |             0 |            1 | model_calls=3; prompt_tokens=857; completion_tokens=50    |
|   3 | tagged     |        3 |             1 | deal    |    40 |       1 |         0 |     2 |             0 |            1 | model_calls=3; prompt_tokens=847; completion_tokens=46    |
|   3 | tagged     |        4 |             0 | no_deal |       |       1 |         0 |     8 |             0 |            4 | model_calls=12; prompt_tokens=4142; completion_tokens=206 |
|   1 | free       |        1 |             1 | deal    |   120 |       1 |         0 |     4 |             0 |            4 | model_calls=8; prompt_tokens=2243; completion_tokens=154  |
|   1 | free       |        2 |             1 | deal    |    30 |       1 |         0 |     2 |             0 |            2 | model_calls=4; prompt_tokens=1007; completion_tokens=68   |
|   1 | free       |        3 |             1 | deal    |    40 |       1 |         0 |     2 |             0 |            2 | model_calls=4; prompt_tokens=952; completion_tokens=44    |
|   1 | free       |        4 |             0 | no_deal |       |       1 |         0 |     3 |             0 |            3 | model_calls=6; prompt_tokens=1623; completion_tokens=122  |
|   2 | free       |        1 |             1 | deal    |   125 |       1 |         0 |     6 |             0 |            6 | model_calls=12; prompt_tokens=3323; completion_tokens=184 |
|   2 | free       |        2 |             1 | deal    |    45 |       1 |         0 |     7 |             0 |            7 | model_calls=14; prompt_tokens=4316; completion_tokens=241 |
|   2 | free       |        3 |             1 | deal    |    40 |       1 |         0 |     2 |             0 |            2 | model_calls=4; prompt_tokens=952; completion_tokens=44    |
|   2 | free       |        4 |             0 | no_deal |       |       1 |         0 |     4 |             0 |            4 | model_calls=8; prompt_tokens=2318; completion_tokens=180  |
|   3 | free       |        1 |             1 | no_deal |       |       0 |         0 |     4 |             0 |            4 | model_calls=8; prompt_tokens=2150; completion_tokens=147  |
|   3 | free       |        2 |             1 | deal    |    30 |       1 |         0 |     2 |             0 |            2 | model_calls=4; prompt_tokens=1007; completion_tokens=68   |
|   3 | free       |        3 |             1 | deal    |    40 |       1 |         0 |     2 |             0 |            2 | model_calls=4; prompt_tokens=1030; completion_tokens=79   |
|   3 | free       |        4 |             0 | no_deal |       |       1 |         0 |     4 |             0 |            4 | model_calls=8; prompt_tokens=2318; completion_tokens=169  |
|   1 | structured |        1 |             1 | open    |       |       0 |         0 |     8 |             6 |            0 | model_calls=8; prompt_tokens=3286; completion_tokens=151  |
|   1 | structured |        2 |             1 | open    |       |       0 |         0 |     8 |             3 |            0 | model_calls=8; prompt_tokens=3346; completion_tokens=150  |
|   1 | structured |        3 |             1 | open    |       |       0 |         0 |     8 |             7 |            0 | model_calls=8; prompt_tokens=3333; completion_tokens=151  |
|   1 | structured |        4 |             0 | no_deal |       |       1 |         0 |     2 |             0 |            0 | model_calls=2; prompt_tokens=720; completion_tokens=35    |
|   2 | structured |        1 |             1 | open    |       |       0 |         0 |     8 |             7 |            0 | model_calls=8; prompt_tokens=3291; completion_tokens=152  |
|   2 | structured |        2 |             1 | open    |       |       0 |         0 |     8 |             0 |            0 | model_calls=8; prompt_tokens=3333; completion_tokens=145  |
|   2 | structured |        3 |             1 | open    |       |       0 |         0 |     8 |             7 |            0 | model_calls=8; prompt_tokens=3333; completion_tokens=151  |
|   2 | structured |        4 |             0 | no_deal |       |       1 |         0 |     7 |             3 |            0 | model_calls=7; prompt_tokens=2885; completion_tokens=131  |
|   3 | structured |        1 |             1 | open    |       |       0 |         0 |     8 |             7 |            0 | model_calls=8; prompt_tokens=3291; completion_tokens=152  |
|   3 | structured |        2 |             1 | open    |       |       0 |         0 |     8 |             3 |            0 | model_calls=8; prompt_tokens=3346; completion_tokens=150  |
|   3 | structured |        3 |             1 | open    |       |       0 |         0 |     8 |             7 |            0 | model_calls=8; prompt_tokens=3333; completion_tokens=151  |
|   3 | structured |        4 |             0 | no_deal |       |       1 |         0 |     2 |             0 |            0 | model_calls=2; prompt_tokens=720; completion_tokens=35    |

### 결과에서 확인된 주요 현상

`tagged`는 12개 episode 모두 correct였다. 가능한 거래 시나리오 1~3에서는 모두 `deal`, 불가능한 시나리오 4에서는 모두 `no_deal`이 나왔다. `format_errors`는 0이었다.

`free`는 12개 중 11개가 correct였다. 유일한 실패는 run 3의 scenario 1로, 거래가 가능한 bicycle 시나리오에서 `no_deal`로 종료되었다. `format_errors`는 0이었지만 모든 메시지를 reader가 해석해야 했기 때문에 reader 호출 수가 episode당 평균 3.50회였다.

`structured`는 가능한 거래 시나리오 1~3에서 9개 episode가 모두 `open`으로 종료되었다. 이 9개 episode의 `correct`는 모두 0이었다. 특히 `format_errors`는 scenario 1~3에서 반복되었으며 총 50회가 발생했다. 반면 거래가 불가능한 scenario 4에서는 세 번 모두 `no_deal`이 나왔고 모두 correct였다.

---

## 3. FIPA-ACL과 세 조건 비교

| 항목                           | FIPA-ACL                                      | free                                  | tagged                              | structured                                     |
| ---------------------------- | --------------------------------------------- | ------------------------------------- | ----------------------------------- | ---------------------------------------------- |
| illocutionary force가 어디에 있는가 | `performative`로 명시                            | 자연어 문장에 암묵적으로 존재                      | 메시지 앞 태그에 명시                        | JSON의 `performative` 필드에 명시                    |
| content 언어                   | 별도로 정의된 content 표현 사용 가능                      | 평문 영어                                 | 평문 영어                               | JSON                                           |
| content를 누가 해석하는가            | 수신자가 정의된 content 언어/의미를 해석                    | reader LLM                            | performative는 regex, 가격은 reader LLM | JSON parser                                    |
| 대화가 어떻게 끝나는가                 | 사용한 interaction protocol의 종료 규칙에 따름           | `accept-proposal`, `refuse`, 8턴 제한    | `accept-proposal`, `refuse`, 8턴 제한  | `accept-proposal`, `refuse`, 8턴 제한             |
| sincerity를 보장하는 것            | 프로토콜 자체가 발화자의 실제 의도나 사실성을 보장하지 않으며 별도의 검증이 필요 | 모델의 자연어 생성에 의존                        | 태그와 실제 문장의 일치 여부는 별도 문제             | 구조와 필드 타입은 검사하지만 실제 제안의 타당성은 별도 검증             |
| 메시지 하나를 읽는 비용                | 사용한 content 언어와 구현에 따라 다름                     | reader 모델 호출                          | 태그 파싱 + 가격 reader 호출                | JSON parsing                                   |
| 실패하는 방식                      | protocol/content 해석 및 준수 문제                   | 자연어에서 performative 또는 가격을 잘못 해석할 수 있음 | 태그와 본문 의미가 어긋날 수 있음                 | JSON 형식 자체가 parser의 계약과 맞지 않으면 format error 발생 |

이번 실험에서 세 형식의 핵심 차이는 **메시지의 의미를 얼마나 명시적으로 표현하고, 그 의미를 누가 해석하는가**였다. free는 가장 많은 의미를 자연어 해석기에 맡기고, tagged는 행위를 태그로 고정하여 일부 해석을 없앴으며, structured는 행위와 가격을 모두 데이터 필드로 표현하여 reader를 제거하였다.

---

## 4. 해석

이번 실험에서는 `tagged`가 12/12 correct, `free`가 11/12, `structured`가 3/12로 나타났다. 특히 `tagged`는 0개의 format error와 평균 2.58회의 reader 호출로 모든 episode에서 기대한 결과를 얻었다. `free` 역시 format error는 0이었지만 마지막 메시지를 매번 reader가 해석해야 하므로 평균 3.50회의 reader 호출이 필요했고, run 3의 scenario 1에서 거래가 가능한 상황을 `no_deal`로 처리하여 1회 실패했다. 반면 `structured`는 reader 호출을 완전히 제거하여 `reader_calls=0`이라는 비용상의 장점이 있었지만, 가능한 거래 시나리오 9개가 모두 `open`으로 종료되었다. 로그에서는 `performative=None price=None ok=False reason=non-propose acts must use null price`라는 parser 오류가 반복되었고, 그 결과 여러 episode가 `[result] open: max turn limit reached`로 종료되었다. 따라서 이번 결과에서 구조화된 형식의 문제는 reader가 없다는 것이 아니라 **모델이 생성한 메시지와 parser가 요구하는 JSON 계약이 맞지 않을 때, 자연어처럼 부분적으로 해석해서 진행할 수 없다는 점**으로 나타났다. 반대로 전체 36개 episode에서 `violation=0`이었기 때문에 이번 실험에서는 구매자와 판매자의 private limit 자체를 어긴 사례보다 메시지 형식과 프로토콜 계층의 불일치가 더 직접적인 실패 원인이었다.
