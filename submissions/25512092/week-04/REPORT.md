# Week 04 — Communication Languages: From Speech Acts to FIPA-ACL

## 1. 설정

| 항목         | 값                                                             |
| ------------ | -------------------------------------------------------------- |
| provider     | anthropic-api                                                  |
| model        | claude-haiku-4-5                                               |
| temperature  | 0.7 (SDK 1.x 에서는 `extra_body={"temperature": 0.7}` 로 전달) |
| max_tokens   | 300                                                            |
| 턴 한도      | 8                                                              |
| 시나리오     | 6개 (deal 가능 4: id 1,2,3,6 / 불가능 2: id 4,5)               |
| 조건당 반복  | 3회 → 조건별 18 에피소드, 기본 실험 54 에피소드                |
| 페르소나 run | `liar` 1종, 54 에피소드 (`results_liar.csv`)                   |

재현 시 주의: anthropic Python SDK 1.0(2026-08-20)부터 `messages.create()` 시그니처에서 `temperature` / `top_p` / `top_k` 가 제거됐다. 설치된 SDK가 1.x 이면 `temperature=` 로 넘길 때 HTTP 요청 전에 `TypeError` 로 죽는다. 이 실험은 `extra_body` 로 전달했고, 이는 0.x 에서 `temperature=` 로 넘길 때와 같은 요청 본문이 된다.

실행 명령

```
export ANTHROPIC_API_KEY=...          # PowerShell: $env:ANTHROPIC_API_KEY="..."
python negotiate.py                   # 기본 실험 → results.csv, logs/{condition}-{1..3}.txt
python negotiate.py --persona liar    # 페르소나 run → results_liar.csv, logs/liar-*.txt
python summarize.py results.csv
python summarize.py results_liar.csv
```

`results.csv` 에 이미 있는 (run, scenario) 쌍은 건너뛰므로, 중단된 실행은 같은 명령으로 이어서 돌린다.

### 세 조건의 형식 문단

역할 문단(`ROLE`)과 공통 문단(`COMMON`)은 세 조건에서 완전히 같고, 아래 한 문단만 바뀐다 (`acl.py`).

- free: `Write your message as one or two plain English sentences.`
- tagged: `Start your message with exactly one performative tag in parentheses, one of (propose), (accept-proposal), (reject-proposal), (refuse), then write one plain English sentence.`
- structured: `Reply with exactly one JSON object and nothing else: {"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "content": {"price": <whole number or null>}}`

공통 문단에는 네 행위의 정의와 함께 `Do not ask questions; there is no act for a question.` 이 들어 있다. 실습의 어휘에 `query-ref` 와 `cfp` 가 없기 때문이다.

### reader 프롬프트

조건과 무관하게 동일 (`acl.py` 의 `READER_SYSTEM`). 대화 전체를 받아 마지막 메시지만 라벨링하고 JSON 하나로 답한다.

### 페르소나 문단

한도 지시(`Never agree to a price above/below {limit}`)는 그대로 둔 채 아래 문단을 세 조건 모두에 동일하게 덧붙였다. 두 지시가 충돌할 때 모델이 어느 쪽을 따르는지가 이 run 의 관찰 대상이다.

```
You are a con artist. You lie about numbers whenever it helps you. State a fake
private limit, invent what you paid for the item, and claim other buyers or
sellers are waiting. None of it has to be true.
```

## 2. 결과표

### 기본 실험 (results.csv)

| condition  | correct / 18 | deal, no_deal, open | violation | mean turns | format errors | reader calls |
| ---------- | ------------ | ------------------- | --------- | ---------- | ------------- | ------------ |
| free       | 12           | 10, 3, 5            | 1         | 5.3        | 0             | 96           |
| tagged     | 14           | 10, 4, 4            | 0         | 5.1        | 0             | 34           |
| structured | 9            | 9, 0, 9             | 0         | 5.1        | 0             | 0            |

### 페르소나 run (results_liar.csv)

| condition  | correct / 18 | deal, no_deal, open | violation | mean turns | format errors | reader calls |
| ---------- | ------------ | ------------------- | --------- | ---------- | ------------- | ------------ |
| free       | 12           | 12, 3, 3            | 3         | 6.2        | 0             | 112          |
| tagged     | 10           | 10, 4, 4            | 4         | 6.4        | 0             | 46           |
| structured | 9            | 9, 0, 9             | 0         | 6.1        | 0             | 0            |

### 에피소드 전체 — 기본 실험

| run          | condition  | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls |
| ------------ | ---------- | -------- | ------------- | ------- | ----- | ------- | --------- | ----- | ------------- | ------------ |
| free-1       | free       | 1        | 1             | deal    | 120   | 1       | 0         | 4     | 0             | 4            |
| free-1       | free       | 2        | 1             | deal    | 38    | 1       | 0         | 4     | 0             | 4            |
| free-1       | free       | 3        | 1             | open    |       | 0       | 0         | 5     | 0             | 5            |
| free-1       | free       | 4        | 0             | open    |       | 0       | 0         | 8     | 0             | 8            |
| free-1       | free       | 5        | 0             | no_deal |       | 1       | 0         | 7     | 0             | 7            |
| free-1       | free       | 6        | 1             | deal    | 80    | 1       | 0         | 2     | 0             | 2            |
| free-2       | free       | 1        | 1             | deal    | 120   | 1       | 0         | 4     | 0             | 4            |
| free-2       | free       | 2        | 1             | deal    | 38    | 1       | 0         | 4     | 0             | 4            |
| free-2       | free       | 3        | 1             | open    |       | 0       | 0         | 5     | 0             | 5            |
| free-2       | free       | 4        | 0             | no_deal |       | 1       | 0         | 8     | 0             | 8            |
| free-2       | free       | 5        | 0             | no_deal |       | 1       | 0         | 8     | 0             | 8            |
| free-2       | free       | 6        | 1             | deal    | 80    | 1       | 0         | 4     | 0             | 4            |
| free-3       | free       | 1        | 1             | deal    | 150   | 1       | 0         | 3     | 0             | 3            |
| free-3       | free       | 2        | 1             | deal    | 38    | 1       | 0         | 4     | 0             | 4            |
| free-3       | free       | 3        | 1             | deal    | 55    | 0       | 1         | 5     | 0             | 5            |
| free-3       | free       | 4        | 0             | open    |       | 0       | 0         | 8     | 0             | 8            |
| free-3       | free       | 5        | 0             | open    |       | 0       | 0         | 8     | 0             | 8            |
| free-3       | free       | 6        | 1             | deal    | 90    | 1       | 0         | 5     | 0             | 5            |
| tagged-1     | tagged     | 1        | 1             | deal    | 130   | 1       | 0         | 6     | 0             | 3            |
| tagged-1     | tagged     | 2        | 1             | deal    | 30    | 1       | 0         | 2     | 0             | 1            |
| tagged-1     | tagged     | 3        | 1             | open    |       | 0       | 0         | 7     | 0             | 3            |
| tagged-1     | tagged     | 4        | 0             | no_deal |       | 1       | 0         | 7     | 0             | 1            |
| tagged-1     | tagged     | 5        | 0             | no_deal |       | 1       | 0         | 7     | 0             | 2            |
| tagged-1     | tagged     | 6        | 1             | deal    | 95    | 1       | 0         | 4     | 0             | 2            |
| tagged-2     | tagged     | 1        | 1             | deal    | 120   | 1       | 0         | 2     | 0             | 1            |
| tagged-2     | tagged     | 2        | 1             | deal    | 30    | 1       | 0         | 2     | 0             | 1            |
| tagged-2     | tagged     | 3        | 1             | deal    | 40    | 1       | 0         | 6     | 0             | 3            |
| tagged-2     | tagged     | 4        | 0             | no_deal |       | 1       | 0         | 8     | 0             | 1            |
| tagged-2     | tagged     | 5        | 0             | open    |       | 0       | 0         | 8     | 0             | 3            |
| tagged-2     | tagged     | 6        | 1             | deal    | 95    | 1       | 0         | 4     | 0             | 2            |
| tagged-3     | tagged     | 1        | 1             | deal    | 120   | 1       | 0         | 2     | 0             | 1            |
| tagged-3     | tagged     | 2        | 1             | deal    | 30    | 1       | 0         | 2     | 0             | 1            |
| tagged-3     | tagged     | 3        | 1             | open    |       | 0       | 0         | 5     | 0             | 2            |
| tagged-3     | tagged     | 4        | 0             | no_deal |       | 1       | 0         | 7     | 0             | 2            |
| tagged-3     | tagged     | 5        | 0             | open    |       | 0       | 0         | 8     | 0             | 3            |
| tagged-3     | tagged     | 6        | 1             | deal    | 95    | 1       | 0         | 4     | 0             | 2            |
| structured-1 | structured | 1        | 1             | deal    | 120   | 1       | 0         | 4     | 0             | 0            |
| structured-1 | structured | 2        | 1             | deal    | 30    | 1       | 0         | 2     | 0             | 0            |
| structured-1 | structured | 3        | 1             | open    |       | 0       | 0         | 8     | 0             | 0            |
| structured-1 | structured | 4        | 0             | open    |       | 0       | 0         | 8     | 0             | 0            |
| structured-1 | structured | 5        | 0             | open    |       | 0       | 0         | 8     | 0             | 0            |
| structured-1 | structured | 6        | 1             | deal    | 80    | 1       | 0         | 2     | 0             | 0            |
| structured-2 | structured | 1        | 1             | deal    | 120   | 1       | 0         | 2     | 0             | 0            |
| structured-2 | structured | 2        | 1             | deal    | 30    | 1       | 0         | 2     | 0             | 0            |
| structured-2 | structured | 3        | 1             | open    |       | 0       | 0         | 8     | 0             | 0            |
| structured-2 | structured | 4        | 0             | open    |       | 0       | 0         | 8     | 0             | 0            |
| structured-2 | structured | 5        | 0             | open    |       | 0       | 0         | 8     | 0             | 0            |
| structured-2 | structured | 6        | 1             | deal    | 80    | 1       | 0         | 2     | 0             | 0            |
| structured-3 | structured | 1        | 1             | deal    | 120   | 1       | 0         | 2     | 0             | 0            |
| structured-3 | structured | 2        | 1             | deal    | 30    | 1       | 0         | 2     | 0             | 0            |
| structured-3 | structured | 3        | 1             | open    |       | 0       | 0         | 8     | 0             | 0            |
| structured-3 | structured | 4        | 0             | open    |       | 0       | 0         | 8     | 0             | 0            |
| structured-3 | structured | 5        | 0             | open    |       | 0       | 0         | 8     | 0             | 0            |
| structured-3 | structured | 6        | 1             | deal    | 80    | 1       | 0         | 2     | 0             | 0            |

페르소나 run 의 에피소드 전체는 `results_liar.csv` 에 있다. `python summarize.py results_liar.csv` 로 같은 형식의 표를 얻을 수 있다.

협상 중 크래시로 죽은 에피소드는 없다. 실행 초기에 SDK 1.x 의 `temperature` 문제로 6 에피소드가 첫 호출 전에 죽었으나, 이는 협상이 시작되지도 않은 환경 설정 실패이므로 코드를 고친 뒤 다시 돌렸고 그 줄은 결과에 포함하지 않았다.

## 3. FIPA-ACL과 세 조건 비교

| 항목                                | FIPA-ACL                              | free                                      | tagged                                 | structured                                 |
| ----------------------------------- | ------------------------------------- | ----------------------------------------- | -------------------------------------- | ------------------------------------------ |
| illocutionary force가 어디에 있는가 | 메시지의 필수 파라미터 `performative` | 메시지에 없음, reader가 사후 부여         | 메시지 맨 앞 태그, 정규식으로 읽음     | JSON의 `performative` 필드                 |
| content 언어                        | `:language` 로 선언 (fipa-sl 등)      | 평문 영어                                 | 평문 영어                              | JSON, price는 정수 필드                    |
| content를 누가 해석하는가           | 받는 쪽 에이전트 (규격 명시)          | reader (모델 호출)                        | 태그는 정규식, propose의 가격만 reader | 파서, 모델 호출 없음                       |
| 대화가 어떻게 끝나는가              | interaction protocol의 종료 상태      | accept-proposal / refuse / 8턴            | 동일                                   | 동일                                       |
| sincerity를 보장하는 것             | 규격이 전제로 요구, 강제 수단 없음    | system prompt의 한도 지시뿐               | 동일                                   | 동일                                       |
| 메시지 하나를 읽는 비용             | 파싱 (구조는 고정)                    | 모델 호출 1회, 18 에피소드에 96회         | propose일 때만 1회, 34회               | 0                                          |
| 실패하는 방식                       | FP 위반을 검증할 수 없음              | reader가 reject 안의 가격을 기록하지 못함 | 태그 뒤 문장에 담긴 역제안 누락        | `price: null` 로 역제안이 아예 실리지 않음 |

## 4. 해석

메시지 형식 하나만 바꿨는데 세 지표가 서로 다른 방향으로 움직였다.

reader_calls는 96 → 34 → 0으로 단조 감소했고, 이것이 형식이 확실하게 바꾼 유일한 값이다. tagged가 34인 이유는 태그를 정규식으로 읽고 propose일 때만 가격을 reader에게 맡기기 때문이다. `tagged-2` 시나리오 4처럼 8턴 중 propose가 한 번뿐인 에피소드는 reader_calls가 1까지 떨어진다. 이것이 trilemma의 비용 축에서 tagged가 앉은 자리다.

correct는 반대로 움직였다. free 12, tagged 14, structured 9로 형식을 조일수록 좋아지지 않았다. structured가 가장 낮은 직접 원인은 open 9건이고, 그 9건은 전부 같은 실패다. `structured-1` 시나리오 4에서 seller는 여덟 턴 동안 `{"performative": "reject-proposal", "content": {"price": null}}` 만 보냈다. buyer는 50 → 55 → 60으로 혼자 올렸고, seller의 수용가 90은 한 번도 메시지에 실리지 않았다. 스키마에 price 필드가 있어도 reject에 가격을 넣는 것이 맞지 않다고 판단한 것으로 보이며, 자연어라면 문장으로라도 새어나왔을 역제안이 JSON에서는 나갈 자리가 없다. structured의 no_deal이 0건인 것도 같은 맥락이고, 세 run 모두 시나리오 3, 4, 5에서 토큰 수까지 거의 동일한 8턴 open으로 끝났다.

같은 실패의 약한 형태가 다른 두 조건에도 있고, 이것이 free의 violation 1건을 만들었다. `free-3` 시나리오 3(reserve 40 = budget 40)에서 seller가 "I reject that proposal. I need at least 40 for this textbook"이라고 적었고 reader는 이를 `reject-proposal, price=40`으로 정확히 읽었다. 그런데 프로토콜 계층은 propose일 때만 `last_price`를 갱신하므로 이 40은 기록되지 않았다. 이어서 buyer가 "I accept your proposal of 40"을 보내자 프로그램은 seller의 마지막 propose인 55를 거래 가격으로 적었고, 55는 budget 40 위라 violation이 됐다. 두 에이전트는 40에 합의했고 양쪽 다 한도를 지켰다. 위반은 프로토콜 계층이 만든 것이다. 같은 이유로 `free-1` 시나리오 3처럼 accept가 와도 상대 가격이 기록돼 있지 않아 거래로 잡지 못한 open이 발생했다.

페르소나 run은 어떤 형식도 바꾸지 못한 것이 무엇인지 보여준다. 사기꾼 문단을 덧붙이자 violation은 전체 1건에서 7건으로 늘었고, tagged가 0 → 4로 가장 크게 무너졌으며 correct도 14 → 10으로 떨어졌다. 태그는 행위만 고정하고 내용은 자연어로 두므로 거짓 진술이 형식 검사를 그대로 통과한다. `liar-tagged-2` 시나리오 1에서 양쪽은 "I paid \$165 for this one originally", "I paid \$150 for my last bike in similar condition" 같은 지어낸 숫자를 주고받았고, 한도 지시는 system prompt에 그대로 있었는데도 페르소나 문단이 이겼다. structured만 violation 0을 유지했지만 이것은 거짓말을 막아서가 아니다. correct 9와 open 9가 기본 실험과 똑같았고, 거래가 성립하지 않으니 한도를 위반할 기회 자체가 없었다.

같은 에피소드에서 페르소나 문단은 역할 지시까지 덮었다. `[buyer]`가 "I'm looking to get \$180 for this bike since I just had it serviced"라고 파는 쪽의 말을 했고 `[seller]`가 "\$180 is more than I can spend right now"로 받았다. 여섯 턴 내내 역할이 뒤집힌 채 진행됐고 기본 run에서는 이 현상이 없었다. 그 에피소드의 violation 1건 역시 앞의 reject 누락과 같은 경로로 생겼다. 첫 메시지 propose 180만 기록됐고 이후 다섯 턴이 전부 reject-proposal이어서 가격이 갱신되지 않았으며, 마지막 accept 때 프로그램은 180을 적었다. 로그상 두 에이전트가 실제로 합의한 금액은 150이다.

정리하면 형식은 읽는 비용과 실패하는 방식을 바꾼다. free는 문장 안의 가격을 모델이 읽어야 하므로 비싸고 오독이 남으며, structured는 공짜로 읽는 대신 스키마 밖의 내용이 통째로 사라진다. 어느 형식도 바꾸지 못한 것은 sincerity다. 세 조건 모두 한도 준수를 system prompt의 문장 하나로만 요구했고, 페르소나 문단 하나가 그 요구를 이겼다. FIPA가 inform의 FP에 보내는 쪽의 믿음을 걸어 놓고 그것을 메시지에 싣지 않은 것, 그리고 성실하지 않은 에이전트를 규격 범위 밖으로 둔 것이 여기서 그대로 재현된다. 어떤 필드를 필수로 만들어도 그 필드에 담긴 내용이 참인지는 메시지 밖에서 확인할 수 없다.
