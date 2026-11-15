# Week 04 — 같은 협상, 세 가지 메시지 형식

buyer 1명과 seller 1명이 같은 물건을 놓고 값을 흥정한다. 바뀌는 것은 메시지의 생김새
하나뿐이다. FIPA-ACL이 필수 필드로 만든 performative가 무엇을 벌어주고 무엇을 비용으로
치르는지 잰다.

```
  buyer ──── message ────> seller        한 턴
        <─── message ─────
                                        최대 10턴
  메시지 하나가 나올 때마다:

      message ──> protocol.read(condition) ──> (performative, price)
                       free    LLM reader
                       tagged  regex + LLM reader
                       struct  JSON parser

  종료:  accept-proposal -> deal     refuse -> no_deal     10턴 -> open
```

## 1. 설정

### 모델과 실행

| 항목 | 값 |
|---|---|
| provider / 모델 | OpenAI 호환 / `gpt-4o-mini` (`model.py`가 환경에서 판정) |
| temperature | 0.7 — 3회 반복의 변동을 보려면 0이어서는 안 된다 |
| 턴 한도 | 10 (`negotiate.MAX_TURNS`). 강의 뼈대는 8 |
| 도구 | 없음. 한 턴에 모델 호출 1회, 읽기에 최대 1회 더 |
| 시나리오 | 5개, `scenarios=7b9b2b94`. 첫 실행 전 커밋 (`6293372`) |
| 실행 | 3조건 × 3반복 × 5시나리오 = 45 에피소드, run 1–9 |
| 크래시 | 0건 |

```bash
pip install openai
export OPENAI_API_KEY=...
cd submissions/26510118/week-04
python run.py                      # 9 run 전부. 중단되면 같은 명령으로 이어서 실행
python run.py --only structured    # 한 조건만
```

로그 첫 줄에 `provider=openai model=gpt-4o-mini temperature=0.7 max_turns=10 scenarios=7b9b2b94` 가 찍힌다.
`scenarios` 는 `scenarios.json` 의 SHA-256 앞 8자리다. 지문이 같은 행끼리만 비교할 수 있다.

run 번호는 세는 값이 아니라 위치로 정한다.

```
run    1  2  3    4  5  6    7  8  9
       free       tagged     structured
       ↑ 반복 1·2·3

중단 후 다시 돌려도 같은 번호가 나오므로,
results.csv 에 이미 있는 (run, scenario) 쌍을 건너뛰고 이어서 실행한다.
```

### 시나리오

| id | 물건 | reserve | budget | ZOPA | 정답 |
|---:|---|---:|---:|---:|---|
| 1 | a used bicycle | 40 | 120 | +80 | 거래 |
| 2 | a pair of earbuds | 90 | 110 | +20 | 거래 |
| 3 | a mechanical keyboard | 63 | 127 | +64 | 거래 |
| 4 | a winter coat | 110 | 100 | -10 | 결렬 |
| 5 | a second-hand laptop | 300 | 150 | -150 | 결렬 |

```
ZOPA = budget − reserve = 둘이 나눠 가질 수 있는 몫

      −150        −10      +20        +64        +80
   ────┼───────────┼────┬────┼──────────┼──────────┼────
     노트북       코트  0  이어폰     키보드     자전거
        결렬이 정답  │      거래가 정답
```

reserve 와 budget 은 첫 실행 전에 커밋했다. 실행 결과에 맞춰 한도를 바꾸면
`violation` 을 잴 수 없다.

### 시스템 프롬프트

```
system_prompt(role, item, limit, condition, max_turns)

   역할 문단          +   공통 문단        +   형식 문단
   ROLE[role]            COMMON               FORMAT[condition]
   역할마다 다름          세 조건 모두 같음      조건마다 다름  ← 독립변수
   buyer / seller

앞 두 조각은 세 조건에서 글자 하나까지 같다. 자동 검사로 확인한다.
```

#### 역할 문단

> buyer — You are the buyer of {item}. You are negotiating the price with the seller. Your private limit: you can pay at most {limit}. Never agree to a price above {limit}, and do not state the number {limit} to the seller. A deal at or below {limit} is better for you than no deal, and a lower price is better than a higher one.

> seller — You are the seller of {item}. You are negotiating the price with the buyer. Your private limit: you can accept at least {limit}. Never agree to a price below {limit}, and do not state the number {limit} to the buyer. A deal at or above {limit} is better for you than no deal, and a higher price is better than a lower one.

`{limit}` 에는 자기 숫자만 들어간다. buyer 는 budget 만, seller 는 reserve 만 본다.
두 문단은 거울이다.

| | buyer | seller |
|---|---|---|
| 한도 방향 | `pay at most` | `accept at least` |
| 금지 | `above {limit}` | `below {limit}` |
| 선호 | `lower price is better` | `higher price is better` |
| 문장 수 | 4 | 4 |

한쪽 문단이 더 강하면 그 비대칭이 세 조건 전부에 실려 형식 효과처럼 보인다.

#### 공통 문단 (통제변수)

> Four acts are available: propose (offer a price), accept-proposal (agree to the other side's last price, which ends the negotiation with a deal), reject-proposal (decline the last price and keep negotiating), refuse (leave the negotiation for good, no deal). Every message you send is exactly one of these four acts. Send accept-proposal only in reply to a price the other side has already named. The negotiation stops after 10 messages in total, counting both sides. Score the outcome as a payoff. Ending with no deal is a real loss for you. A deal on your side of your private limit is a gain, and the further it sits from that limit in your favour, the larger the gain. A deal on the wrong side of your private limit is a far heavier loss than no deal at all, so never take one, however little of the clock is left. Within those bounds aim to come out ahead of the other side, but a small edge is enough: once a price leaves you any gain at all, accepting it beats risking no deal. One rule about which act to send, because it decides whether your number reaches the other side at all: reject-proposal carries no price, so it is only for the case where you will name no number. Whenever you do have a price in mind -- including when you are turning down what you just heard -- send propose with that number instead.

#### 형식 문단 (독립변수) — 강의자료 `acl.py` 원문 그대로

```
free        Write your message as one or two plain English sentences.
tagged      Start your message with exactly one performative tag in parentheses, one of (propose), (accept-proposal), (reject-proposal), (refuse), then write one plain English sentence.
structured  Reply with exactly one JSON object and nothing else: {"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "content": {"price": <whole number or null>}}.
```

#### 리더 프롬프트 — `free` 와 `tagged` 가 공유한다

> You are an observer reading a price negotiation between a buyer and a seller. Label the LAST message only. Reply with exactly one JSON object and nothing else, no prose and no code fences: {"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "price": <whole number or null>}. propose offers a price. accept-proposal agrees to the other side's last price and ends the negotiation with a deal. reject-proposal declines the last price and keeps negotiating. refuse leaves the negotiation for good. You must pick one of these four even when the message fits none of them. price is the price that the last message itself offers or agrees to, written as a whole number with no currency symbol; use null when the message names no such price.

`You must pick one of these four even when the message fits none of them.` 가 설계의
핵심이다. `unclear` 같은 출구를 주지 않았다. 네 행위로 못 덮는 메시지가 어디로 떨어지는지가
이 실습의 관측 대상이기 때문이다. `tagged` 는 이 리더에게서 `performative` 를 버리고 가격만
쓴다. 프롬프트를 같게 두어야 통제변수가 된다.

### 메시지를 읽는 계층 (`protocol.py`)

| 조건 | 행위를 정하는 것 | 가격을 뽑는 것 | 모델 호출 |
|---|---|---|---|
| `free` | LLM 리더 | 같은 호출 | 메시지마다 1회 |
| `tagged` | 정규식 (맨 앞 태그) | LLM 리더 | `propose` 일 때만 1회 |
| `structured` | JSON 파서 | 같은 파서 | 0회 |

판정 규칙은 세 조건이 같다.

```
                     ┌─ 네 행위 중 하나가 나왔나? ─ 아니오 ─→ format_errors += 1
  메시지 ─→ read() ─┤
                     └─ 예 ─┬─ propose 인가? ─ 아니오 ─→ 성공
                            └─ 예 ─┬─ 정수 가격이 있나? ─ 아니오 ─→ format_errors += 1
                                   └─ 예 ─→ 성공, 가격 기록

읽기에 실패해도 메시지는 그대로 상대에게 전달된다.
에이전트 둘은 계속 대화하고, 프로그램만 상태를 놓친다.
```

파서는 JSON 객체에서 멈춘다. JSON 뒤에 붙은 문장에 진짜 제안이 있어도 읽지 않는다.
그 손실이 측정 대상이므로 주워담지 않았다.

### 강의자료 조건에서 바꾼 것

| 항목 | 강의자료 | 이번 실험 | 이유 |
|---|---|---|---|
| 턴 한도 | `MAX_TURNS = 8` | `10` | 왕복 흥정 두세 번의 여유 |
| 한도 고지 | 뼈대에 없음 | 에이전트에게 알림 | 안 알리면 거절이 공짜다. reserve 40인 seller 가 100·110·115·118·119를 연속 거절하고 `open` 으로 끝났다 |
| 첫 발화 | 명시 없음 | `"Begin the negotiation by making your opening offer."` | 중립 오프너에서는 buyer 가 질문으로 열고, 네 행위 중 맞는 것이 없어 1턴에 죽는다 |
| 모델 | `claude-haiku-4-5` (CLI, temperature 설정 불가) | `gpt-4o-mini`, temperature 0.7 | temperature 를 명시할 수 있다 |
| 시나리오 | 6개 | 5개 | 최소 4개. ZOPA 폭을 넓혔다 |

### 공통 문단에 더한 것 — 세 조건에 동일하게 들어간다

| 추가 | 내용 | 무엇이 달라지나 |
|---|---|---|
| 보수 구조 | 거래 실패는 손해. 한도 안 거래는 이득. 한도를 넘은 거래는 실패보다 훨씬 큰 손해. 조금이라도 이득이면 닫는다 | `violation` 의 성격이 "지시를 어기는가"에서 "손해를 감수하면서까지 어기는가"로 바뀐다 |
| 역제안 규칙 | `reject-proposal` 은 숫자를 아예 안 낼 때만 쓴다. 숫자가 있으면 `propose` 로 보낸다 | FIPA 에서도 `reject-proposal` 은 가격을 싣지 못한다. 프로토콜을 명시한 것 |
| 리더의 강제 선택 | 넷 중 반드시 하나 | `unclear` 출구를 주지 않았다 |

`logs/free-neutral-opener.txt` 는 옛 중립 오프너로 `free` 3 에피소드를 돌린 별도 관측이다.
오프너를 바꾼 근거이며 `results.csv` 에는 들어가지 않는다.

### 지표

| 열 | 뜻 | 방향 |
|---|---|---|
| `correct` | 거래 가능하면 한도 안 거래가 정답, 불가능하면 결렬이 정답 | 높을수록 좋음 |
| `violation` | reserve 아래 또는 budget 위에서 성립한 거래 | 낮을수록 좋음 |
| `turns` | 주고받은 메시지 수. 10이면 `open` | |
| `format_errors` | 프로토콜 계층이 읽지 못한 메시지 수 | 낮을수록 좋음 |
| `reader_calls` | 메시지를 읽는 데 쓴 모델 호출 수 | 낮을수록 쌈 |

헤더는 고정이라 열을 늘릴 수 없다. 그래서 `note` 에 다음을 적었다.

| `note` 항목 | 담는 것 |
|---|---|
| `buyer_surplus` / `seller_surplus` | 거래가 성사됐을 때 ZOPA 를 누가 얼마나 가져갔나 |
| `accept-without-price=N` | 수락했는데 기록된 상대 가격이 없던 횟수 |
| `agent_calls` `agent_tokens` `reader_tokens` `retries` | 비용과 429 재시도 |
| `scenarios=7b9b2b94` | 시나리오 파일 지문 |

### 한계

1. 조건당 시나리오마다 3회다. 추세는 읽히지만 소수점 차이를 주장할 크기가 아니다.
2. `correct` 는 한도만 본다. 한도 안이면 1이므로 협상을 잘했는지는 보지 않는다.
   `note` 의 잉여 배분과 같이 읽어야 한다.
3. `free` 의 "seller 가 숫자를 냈다" 는 느슨한 대리 측정이다. 태그가 없어 "메시지에
   숫자가 있는가"로 셌고, 상대 가격을 인용만 해도 잡힌다.
4. `open` 을 거래 불가 시나리오에서 정답으로 셌다. README 가 "a deal exactly when
   reserve ≤ budget" 이라 적었고 `open` 은 거래가 아니기 때문이다. `outcome` 열이
   그대로 있으므로 다르게 채점하려면 `results.csv` 만으로 다시 셀 수 있다.

## 2. 결과

### 조건별 요약

| condition | correct / 15 | violation | deal · no_deal · open | 평균 turns | format_errors | reader_calls |
|---|---:|---:|---|---:|---:|---:|
| `free` | 14 | 0 | 8 · 1 · 6 | 7.6 | 0 | 114 |
| `tagged` | 14 | 0 | 8 · 0 · 7 | 7.9 | 21 | 59 |
| `structured` | 10 | 0 | 4 · 0 · 11 | 8.1 | 0 | 0 |

메시지 수로 나누면 읽기 단가가 나온다.

| condition | 메시지 | reader_calls | 메시지당 |
|---|---:|---:|---:|
| `free` | 114 | 114 | 1.00 |
| `tagged` | 118 | 59 | 0.50 |
| `structured` | 121 | 0 | 0.00 |

위반은 세 조건 모두 0건이다. 보수 구조를 넣은 뒤 45 에피소드에서 한 번도 자기 한도를
넘지 않았고, 거래가 불가능한 두 시나리오에서 억지 거래가 한 건도 성립하지 않았다.

```
메시지 하나를 읽는 값 (1.00 = 모델 호출 한 번)

free        ████████████████████████████████████████  1.00   (114회)
tagged      ████████████████████                      0.50   ( 59회)
structured                                            0.00   (  0회)
```

### 시나리오 × 조건

| 시나리오 | ZOPA | `free` | `tagged` | `structured` |
|---|---:|---|---|---|
| a used bicycle | +80 | 90 · 90 · 80 | open ✗ · 110 · 115 | open ✗ · 70 · open ✗ |
| a pair of earbuds | +20 | 90 · 90 · 90 | 100 · 100 · 100 | 90 · 90 · 90 |
| a mechanical keyboard | +64 | 95 · open ✗ · 110 | 100 · 120 · 100 | open ✗ · open ✗ · open ✗ |
| a winter coat | -10 | open · open · open | open · open · open | open · open · open |
| a second-hand laptop | -150 | no_deal · open · open | open · open · open | open · open · open |

숫자는 거래가, `open`/`no_deal` 은 결렬이다. `✗` 는 `correct` 가 0인 에피소드다.
아래 두 행의 결렬은 거래 불가 시나리오이므로 정답이다.

```
                  free      tagged    structured
ZOPA 넓음   +80·+64   5/6       5/6       1/6     ← 여기서만 갈린다
ZOPA 좁음   +20       3/3       3/3       3/3
거래 불가   −10·−150  6/6       6/6       6/6     (결렬이 정답)
```

깎을 여지가 클수록 seller 가 버틴다. `structured` 의 seller 는 버티면서 자기 숫자를
내놓지 않아 교착된다.

### 로그에서 집계한 보조 지표

`results.csv` 의 열만으로는 왜 그 숫자가 나왔는지 알 수 없어 로그에서 세 가지를 더 셌다.

#### ① seller 가 자기 가격을 제시한 메시지

| 조건 | 센 방법 | 결과 |
|---|---|---:|
| `free` | 메시지에 숫자가 있는가 (태그가 없으므로) | 55 / 57 |
| `tagged` | 맨 앞 태그가 `(propose)` 인가 | 0 / 59 |
| `structured` | `performative` 가 `propose` 인가 | 2 / 60 |

공통 문단이 "숫자가 있으면 `propose` 로 보내라"고 명시하는데도 `tagged` 와 `structured` 의
seller 는 사실상 역제안을 선언하지 않았다. 같은 모델이 `free` 에서는 거의 모든 메시지에
숫자를 담는다.

#### ② `free` 리더의 라벨 분포와 조용히 사라진 가격

| 리더가 붙인 라벨 | 건수 | 가격을 요구하나 |
|---|---:|---|
| `reject-proposal` | 53 | 아니오 |
| `propose` | 51 | 예 |
| `accept-proposal` | 9 | 아니오 |
| `refuse` | 1 | 아니오 |

| | 가격이 사라진 메시지 | `format_errors` 에 잡힌 수 |
|---|---:|---:|
| `free` | 61 / 112 (54%) | 0 |
| `tagged` | 21 / 59 (36%) | 21 |

판정 규칙이 `propose` 일 때만 가격을 요구하므로, 리더가 스스로 `reject-proposal` 이라고
답하면 가격이 없어도 통과한다.

```
free      행위와 가격을 리더가 함께 정한다
          가격을 못 뽑겠다  →  "reject-proposal" 이라고 답한다  →  통과
                                                                  ↑ 출구

tagged    행위는 정규식이 이미 propose 로 못박았다
          가격을 못 뽑겠다  →  propose 인데 가격 없음  →  format_error
                                                        ↑ 출구 없음
```

`tagged` 의 `format_errors` 가 큰 것은 더 자주 실패해서가 아니라 실패가 드러나기
때문이다. 태그의 역할 하나는 리더의 도망갈 구멍을 막는 것이다.

#### ③ 거래가 성사됐을 때 ZOPA 배분

| 조건 | 거래 | buyer 잉여 | seller 잉여 | seller 몫 |
|---|---:|---:|---:|---:|
| `free` | 8 | 209 | 219 | 51% |
| `tagged` | 8 | 106 | 306 | 74% |
| `structured` | 4 | 110 | 30 | 21% |

```
buyer 잉여 = budget − 거래가        seller 잉여 = 거래가 − reserve
둘의 합이 ZOPA.   50 대 50 이면 반씩 나눈 것.

free        buyer ████████████████████ │ ████████████████████ seller   51%
tagged      buyer ██████████ │ ██████████████████████████████ seller   74%
structured  buyer ████████████████████████████████ │ ████████ seller   21%
```

같은 시나리오의 거래가를 나란히 놓으면 `tagged` 가 왜 비싼지 보인다.

| 물건 | `free` | `tagged` |
|---|---|---|
| 이어폰 | 90 · 90 · 90 | 100 · 100 · 100 |
| 키보드 | 95 · 110 | 100 · 120 · 100 |
| 자전거 | 90 · 90 · 80 | 110 · 115 |

`tagged` 가 전부 비싸다. seller 가 `propose` 를 한 번도 보내지 않아 buyer 에게 기준점이
없고, buyer 가 혼자 가격을 올린다.

`structured` 의 21% 는 그대로 읽으면 안 된다. 성사된 4건 중 3건이 같은 시나리오
(이어폰, reserve 90)이고, buyer 의 첫 제안 90을 seller 가 즉시 수락한 것이다. 협상을 이긴
것이 아니라 협상이 필요 없던 건만 성사됐다. 표본 선택 효과이므로 배분을 논할 크기가 아니다.

### 에피소드 45건 (`results.csv` 전문)

| run | condition | sc | 가능 | outcome | price | correct | viol | turns | fmt | reader | note |
|---:|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---|
| 1 | free | 1 | 1 | deal | 90 | 1 | 0 | 8 | 0 | 8 | `buyer_surplus=30 seller_surplus=50 accept-without-price=1 agent_calls=8 agent_tokens=3826 reader_tokens=2202 retries=0` |
| 1 | free | 2 | 1 | deal | 90 | 1 | 0 | 6 | 0 | 6 | `buyer_surplus=20 seller_surplus=0 agent_calls=6 agent_tokens=2746 reader_tokens=1529 retries=0` |
| 1 | free | 3 | 1 | deal | 95 | 1 | 0 | 4 | 0 | 4 | `buyer_surplus=32 seller_surplus=32 agent_calls=4 agent_tokens=1740 reader_tokens=944 retries=0` |
| 1 | free | 4 | 0 | open |  | 1 | 0 | 10 | 0 | 10 | `agent_calls=10 agent_tokens=5148 reader_tokens=3090 retries=0` |
| 1 | free | 5 | 0 | no_deal |  | 1 | 0 | 10 | 0 | 10 | `agent_calls=10 agent_tokens=5131 reader_tokens=3083 retries=0` |
| 2 | free | 1 | 1 | deal | 90 | 1 | 0 | 4 | 0 | 4 | `buyer_surplus=30 seller_surplus=50 agent_calls=4 agent_tokens=1742 reader_tokens=947 retries=0` |
| 2 | free | 2 | 1 | deal | 90 | 1 | 0 | 4 | 0 | 4 | `buyer_surplus=20 seller_surplus=0 agent_calls=4 agent_tokens=1741 reader_tokens=942 retries=0` |
| 2 | free | 3 | 1 | open |  | 0 | 0 | 10 | 0 | 10 | `agent_calls=10 agent_tokens=5140 reader_tokens=3075 retries=0` |
| 2 | free | 4 | 0 | open |  | 1 | 0 | 10 | 0 | 10 | `agent_calls=10 agent_tokens=5166 reader_tokens=3115 retries=0` |
| 2 | free | 5 | 0 | open |  | 1 | 0 | 10 | 0 | 10 | `agent_calls=10 agent_tokens=5169 reader_tokens=3109 retries=0` |
| 3 | free | 1 | 1 | deal | 80 | 1 | 0 | 8 | 0 | 8 | `buyer_surplus=40 seller_surplus=40 agent_calls=8 agent_tokens=3791 reader_tokens=2166 retries=0` |
| 3 | free | 2 | 1 | deal | 90 | 1 | 0 | 4 | 0 | 4 | `buyer_surplus=20 seller_surplus=0 agent_calls=4 agent_tokens=1746 reader_tokens=947 retries=0` |
| 3 | free | 3 | 1 | deal | 110 | 1 | 0 | 6 | 0 | 6 | `buyer_surplus=17 seller_surplus=47 agent_calls=6 agent_tokens=2721 reader_tokens=1513 retries=0` |
| 3 | free | 4 | 0 | open |  | 1 | 0 | 10 | 0 | 10 | `agent_calls=10 agent_tokens=5157 reader_tokens=3103 retries=0` |
| 3 | free | 5 | 0 | open |  | 1 | 0 | 10 | 0 | 10 | `agent_calls=10 agent_tokens=5144 reader_tokens=3077 retries=0` |
| 4 | tagged | 1 | 1 | open |  | 0 | 0 | 10 | 2 | 5 | `agent_calls=10 agent_tokens=5234 reader_tokens=1403 retries=0` |
| 4 | tagged | 2 | 1 | deal | 100 | 1 | 0 | 4 | 0 | 2 | `buyer_surplus=10 seller_surplus=10 agent_calls=4 agent_tokens=1853 reader_tokens=454 retries=0` |
| 4 | tagged | 3 | 1 | deal | 100 | 1 | 0 | 8 | 3 | 4 | `buyer_surplus=27 seller_surplus=37 agent_calls=8 agent_tokens=4168 reader_tokens=1112 retries=0` |
| 4 | tagged | 4 | 0 | open |  | 1 | 0 | 10 | 2 | 5 | `agent_calls=10 agent_tokens=5232 reader_tokens=1404 retries=0` |
| 4 | tagged | 5 | 0 | open |  | 1 | 0 | 10 | 2 | 5 | `agent_calls=10 agent_tokens=5368 reader_tokens=1465 retries=0` |
| 5 | tagged | 1 | 1 | deal | 110 | 1 | 0 | 8 | 0 | 4 | `buyer_surplus=10 seller_surplus=70 agent_calls=8 agent_tokens=4120 reader_tokens=1093 retries=0` |
| 5 | tagged | 2 | 1 | deal | 100 | 1 | 0 | 4 | 0 | 2 | `buyer_surplus=10 seller_surplus=10 agent_calls=4 agent_tokens=1854 reader_tokens=452 retries=0` |
| 5 | tagged | 3 | 1 | deal | 120 | 1 | 0 | 6 | 0 | 3 | `buyer_surplus=7 seller_surplus=57 agent_calls=6 agent_tokens=2886 reader_tokens=731 retries=0` |
| 5 | tagged | 4 | 0 | open |  | 1 | 0 | 10 | 1 | 5 | `agent_calls=10 agent_tokens=5253 reader_tokens=1414 retries=0` |
| 5 | tagged | 5 | 0 | open |  | 1 | 0 | 10 | 2 | 5 | `agent_calls=10 agent_tokens=5219 reader_tokens=1397 retries=0` |
| 6 | tagged | 1 | 1 | deal | 115 | 1 | 0 | 8 | 1 | 4 | `buyer_surplus=5 seller_surplus=75 agent_calls=8 agent_tokens=4009 reader_tokens=1042 retries=0` |
| 6 | tagged | 2 | 1 | deal | 100 | 1 | 0 | 4 | 0 | 2 | `buyer_surplus=10 seller_surplus=10 agent_calls=4 agent_tokens=1851 reader_tokens=454 retries=0` |
| 6 | tagged | 3 | 1 | deal | 100 | 1 | 0 | 6 | 2 | 3 | `buyer_surplus=27 seller_surplus=37 agent_calls=6 agent_tokens=2893 reader_tokens=731 retries=0` |
| 6 | tagged | 4 | 0 | open |  | 1 | 0 | 10 | 2 | 5 | `agent_calls=10 agent_tokens=5237 reader_tokens=1412 retries=0` |
| 6 | tagged | 5 | 0 | open |  | 1 | 0 | 10 | 4 | 5 | `agent_calls=10 agent_tokens=5233 reader_tokens=1400 retries=0` |
| 7 | structured | 1 | 1 | open |  | 0 | 0 | 10 | 0 | 0 | `agent_calls=10 agent_tokens=5305 reader_tokens=0 retries=0` |
| 7 | structured | 2 | 1 | deal | 90 | 1 | 0 | 2 | 0 | 0 | `buyer_surplus=20 seller_surplus=0 agent_calls=2 agent_tokens=911 reader_tokens=0 retries=0` |
| 7 | structured | 3 | 1 | open |  | 0 | 0 | 10 | 0 | 0 | `agent_calls=10 agent_tokens=5305 reader_tokens=0 retries=0` |
| 7 | structured | 4 | 0 | open |  | 1 | 0 | 10 | 0 | 0 | `agent_calls=10 agent_tokens=5305 reader_tokens=0 retries=0` |
| 7 | structured | 5 | 0 | open |  | 1 | 0 | 10 | 0 | 0 | `agent_calls=10 agent_tokens=5315 reader_tokens=0 retries=0` |
| 8 | structured | 1 | 1 | deal | 70 | 1 | 0 | 5 | 0 | 0 | `buyer_surplus=50 seller_surplus=30 agent_calls=5 agent_tokens=2435 reader_tokens=0 retries=0` |
| 8 | structured | 2 | 1 | deal | 90 | 1 | 0 | 2 | 0 | 0 | `buyer_surplus=20 seller_surplus=0 agent_calls=2 agent_tokens=911 reader_tokens=0 retries=0` |
| 8 | structured | 3 | 1 | open |  | 0 | 0 | 10 | 0 | 0 | `agent_calls=10 agent_tokens=5305 reader_tokens=0 retries=0` |
| 8 | structured | 4 | 0 | open |  | 1 | 0 | 10 | 0 | 0 | `agent_calls=10 agent_tokens=5305 reader_tokens=0 retries=0` |
| 8 | structured | 5 | 0 | open |  | 1 | 0 | 10 | 0 | 0 | `agent_calls=10 agent_tokens=5390 reader_tokens=0 retries=0` |
| 9 | structured | 1 | 1 | open |  | 0 | 0 | 10 | 0 | 0 | `agent_calls=10 agent_tokens=5380 reader_tokens=0 retries=0` |
| 9 | structured | 2 | 1 | deal | 90 | 1 | 0 | 2 | 0 | 0 | `buyer_surplus=20 seller_surplus=0 agent_calls=2 agent_tokens=911 reader_tokens=0 retries=0` |
| 9 | structured | 3 | 1 | open |  | 0 | 0 | 10 | 0 | 0 | `agent_calls=10 agent_tokens=5305 reader_tokens=0 retries=0` |
| 9 | structured | 4 | 0 | open |  | 1 | 0 | 10 | 0 | 0 | `agent_calls=10 agent_tokens=5305 reader_tokens=0 retries=0` |
| 9 | structured | 5 | 0 | open |  | 1 | 0 | 10 | 0 | 0 | `agent_calls=10 agent_tokens=5315 reader_tokens=0 retries=0` |

크래시는 0건이다. `scenarios=7b9b2b94` 는 지면을 아끼려 위 표에서만 뺐고 `results.csv` 의
모든 행에 붙어 있다.

## 3. FIPA-ACL 과 세 조건

| 항목 | FIPA-ACL (2002) | `free` | `tagged` | `structured` |
|---|---|---|---|---|
| illocutionary force 가 어디에 있는가 | `performative`. 13개 파라미터 중 유일한 필수 필드 | 메시지 어디에도 없다. 문장에서 사후에 읽어낸다 | 문장 맨 앞 괄호 태그 하나 | JSON 의 `performative` 필드 |
| content 언어 | `fipa-sl` 같은 형식 언어 + 선언된 `ontology` | 영어 문장 | 영어 문장 | `{"price": 정수 또는 null}` |
| content 를 누가 해석하는가 | 규격은 "받는 쪽이 해석한다". 양쪽이 ontology 를 미리 공유 | LLM 리더가 매 메시지 | 태그는 정규식, 가격은 LLM 리더 | 파서. 모델이 개입하지 않는다 |
| 대화가 어떻게 끝나는가 | interaction protocol 이 종료 상태를 규정 | `accept-proposal` / `refuse` 라벨, 또는 10턴 | 같음 | 같음 |
| sincerity 를 보장하는 것 | 없다. 규격이 규범으로 요구하고 "성실하지 않은 경우는 범위 밖"이라 적었다 | system prompt 의 보수 구조뿐. 검증 불가 | 같음 | 같음 |
| 메시지 하나를 읽는 비용 | performative 문자열 비교. 사실상 0 | 1.00회 / 메시지 (총 114) | 0.50회 (총 59) | 0.00회 (총 0, 진짜 0) |
| 실패하는 방식 | semantic verification problem. 보내는 쪽의 믿음을 확인할 방법이 없다 | 리더가 역제안을 `reject-proposal` 로 읽어 가격이 조용히 사라진다 (61/112, `format_errors` 에 안 잡힘) | 태그는 읽히는데 문장 속 숫자를 리더가 못 뽑는다 (21/59) | seller 가 역제안을 선언하지 않아 교착된다 (`propose` 2/60) |

### 세 점의 위치

```
                읽는 값이 비싸다                        읽는 값이 0
            ◄───────────────────────────────────────────────────►

  free                    tagged                    structured
   │                        │                           │
   │ 무엇이든 표현된다        │ 행위만 고정,               │ 행위와 내용을 둘 다 고정
   │ 읽기가 틀려도           │ 내용은 자연어              │ 좁은 어휘가 에이전트의
   │ 형식 오류로 안 잡힌다    │ 가격 추출에서만 샌다        │ 행동 자체를 좁힌다

  FIPA-ACL 은 structured 쪽 끝에 있다. 대신 ontology 합의 비용과
  검증 불가능한 의미론을 떠안았다.
```

강의자료가 인용한 agent communication trilemma 의 세 점이 그대로 나온다. `tagged` 가
그 사이에 있다. 행위만 고정하고 내용은 자연어로 두면 리더 호출이 절반으로 줄고(114 → 59)
행위 판정 오류가 사라지는 대신, 남은 절반인 가격 추출에서 59 번 중 21 번 샌다.

한 가지는 FIPA 와 이번 구현이 같다. sincerity 를 보장하는 장치가 어느 쪽에도 없다는
점이다. FIPA 는 규범으로 요구하고 강제하지 못했고, 이번 실험은 system prompt 의 보수
구조로 요구했다. 위반 0건은 그것이 이번 모델과 이번 시나리오에서 지켜졌다는 관측이지,
보장된다는 뜻이 아니다.
## 4. 해석

이번 실험에서는 메시지 형식을 구조화할수록 해석 비용이 감소했지만, 협상 성과는 단조롭게
따라 내려가지 않았다. free와 tagged는 모두 15건 중 14건에서 올바른 결과를 얻었으나
structured는 10건에 그쳤고, tagged는 free의 절반 비용으로 같은 성과를 냈다. 특히 거래 가능
범위(ZOPA)가 넓은 시나리오에서 structured는 6건 중 1건만 거래에 성공했는데, 이는 형식적
제약이 에이전트의 협상 행동에도 영향을 미쳤을 가능성을 보여준다. 실제 로그에서 seller의
가격 제시는 free가 55/57건인 반면, tagged는 0/59건, structured는 2/60건으로 나타났다. 공통
문단이 "숫자가 있으면 propose로 보내라"고 지시했음에도 structured의 seller는
`{"performative": "reject-proposal", "content": {}}`(`logs/structured-1.txt`)처럼 가격
필드만 지우고 행위 선택은 바꾸지 않았다. 역제안이 거의 이루어지지 않으면서 tagged에서는
buyer가 스스로 가격을 올렸고(이어폰 free 90 대 tagged 100), structured에서는 협상이
교착되는 경우가 많았다. 한편 메시지당 리더 호출은 1.00회에서 0.50회, 0회로 감소했으며,
free에서는 가격 정보가 누락되어도 오류로 기록되지 않았던 반면 tagged에서는 21건의 추출
실패가 명시적으로 드러났다. `[buyer] (propose) I can offer 60 for the bicycle.` 에 대해
리더가 `{'performative': 'reject-proposal', 'price': None}`(`logs/tagged-1.txt:14`)을
반환한 경우가 그것으로, 태그가 행위를 고정한 탓에 free라면 거절로 넘어갔을 실패가 오류로
남았다. 반대로 free에서는 seller가 `I propose a price of 125.` 라고 적었는데도 리더가
`reject-proposal`로 읽어(`logs/free-1.txt:56`) 가격이 기록되지 않았고, 두 턴 뒤 거의 같은
문장은 propose로 읽혔다(`:60`). 이는 performative의 명시가 해석 비용과 행위 판정의
모호성을 줄이는 데 유용하지만, 메시지의 내용이나 협상 행동까지 보장하지는 못한다는 점을
보여준다. 반면 어떤 형식도 바꾸지 못한 것은 한도 위반으로, 세 조건 모두 0건이었다. 이는
형식이 아니라 공통 문단의 보수 구조가 만든 결과이며, FIPA가 sincerity를 규범으로만 요구하고
강제하지 못했던 지점과 같다. 따라서 에이전트 통신 프로토콜을 설계할 때는 메시지의 해석
효율성과 오류 탐지뿐 아니라, 형식적 제약이 실제 상호작용에 미치는 영향도 함께 고려해야 한다.
