# Week 04 — 화행에서 FIPA-ACL까지: free / tagged / structured

- Student ID: 23530007
- 실험: buyer 1 + seller 1, 시나리오 6개 × 형식 3종 × 3회 = **에피소드 54개**, 크래시 0건
- 시나리오와 구현은 **실행 전 커밋**(`84b7be8`)

## 1. 설정

| 항목 | 값 |
|---|---|
| provider | Anthropic Messages API (`anthropic` 0.125.0) |
| 모델 | `claude-haiku-4-5-20251001` (`AGENT_MODEL`로 지정) |
| temperature | **0 — 요청했고 모델이 받아들였다** |
| max_tokens | 512 |
| 턴 한도 | `MAX_TURNS = 8` |
| 호출 | 386회 (buyer/seller 284 + reader 102), 토큰 105,585 |

3주차에는 `claude-opus-5`가 `temperature`를 400으로 거부해 고정하지 못했다. 이번 모델은
받아들이므로 9개 run 전부 `temperature=0`으로 돌았고, 로그 첫 줄에
`temperature_accepted_by_model=True`가 기록돼 있다. 거부됐다면 `False`가 적히도록
`acl.py`가 그 사실 자체를 지표로 남긴다.

### 시나리오 (`scenarios.json`, 실행 전 커밋)

| id | item | reserve | budget | deal_possible |
|---|---|---|---|---|
| 1 | a used bicycle | 120 | 150 | 1 |
| 2 | a desk lamp | 30 | 45 | 1 |
| 3 | a second-hand textbook | 40 | 40 | 1 (경계: reserve == budget) |
| 4 | a mechanical keyboard | 90 | 70 | 0 |
| 5 | a 27-inch monitor | 200 | 150 | 0 |
| 6 | an acoustic guitar | 180 | 260 | 1 |

### 세 조건의 형식 문단 (`acl.py`의 `FORMAT`)

세 조건이 다른 곳은 **이 문단 하나와 메시지를 읽는 코드뿐**이다. 역할 문단(`ROLE`),
네 행위의 뜻(`COMMON`), reader 프롬프트, 모델, temperature, 턴 한도는 전부 공유한다.

```
free        Write your message as one or two plain English sentences.

tagged      Start your message with exactly one performative tag in parentheses, one of
            (propose), (accept-proposal), (reject-proposal), (refuse), then write one
            plain English sentence.

structured  Reply with exactly one JSON object and nothing else: {"performative":
            "propose" | "accept-proposal" | "reject-proposal" | "refuse",
            "content": {"price": <whole number or null>}}.
```

공통 문단:

```
ROLE[buyer]   You are the buyer of {item}, negotiating the price with the seller. Your
              private limit: you can pay at most {limit}. Never agree to a price above
              {limit}. Pay as little as you can, but a deal within your limit is better
              than no deal.
ROLE[seller]  (대칭. you can accept at least {limit}. Never agree to a price below {limit}.)

COMMON        Four acts are available: propose (offer a price), accept-proposal (agree to
              the other side's last price, which ends the negotiation with a deal),
              reject-proposal (decline the last price and keep negotiating), refuse (leave
              the negotiation for good, no deal). Every message you send is exactly one of
              these four acts. Keep it to one or two short sentences.
```

reader 프롬프트 (`free`와 `tagged`가 **같은 것**을 쓴다):

```
READER_SYSTEM  You are an observer reading a price negotiation between a buyer and a
               seller. Label the LAST message only. Reply with exactly one JSON object and
               nothing else: {"performative": "propose" | "accept-proposal" |
               "reject-proposal" | "refuse", "price": <whole number or null>}. price is
               the number the last message puts on the table, or null if it names no price.
```

### 프로토콜 계층

| 조건 | performative | 가격 | 모델 호출 |
|---|---|---|---|
| `free` | reader | reader | 메시지마다 1회 |
| `tagged` | 정규식 `^\s*\((act)\)` | reader | propose마다 1회 |
| `structured` | JSON 파싱 | JSON 파싱 | **0** |

읽지 못한 메시지는 `format_errors`에 세고, **메시지는 그대로 상대에게 전달한다**.
`accept-proposal`이 왔는데 상대의 기록된 propose 가격이 없으면 거래로 잡지 않고 계속
진행하며 `unresolved_accepts`에 세어 `note`로 내보낸다.

### 실행 방법

```bash
export ANTHROPIC_API_KEY=...          # 커밋 금지, 환경변수로만
export AGENT_MODEL=claude-haiku-4-5-20251001
export AGENT_TEMPERATURE=0
cd submissions/23530007/week-04
python run_experiment.py --repeats 3 --workers 3   # --workers 1 이면 순차, 지표는 동일
```

`results.csv`에 이미 있는 `(run, scenario)` 쌍은 건너뛰므로 중단된 실행을 이어서 돌릴 수
있다. run 하나가 로그 파일 하나(형식 하나로 시나리오 전체를 한 번)에 대응한다.

## 2. 결과표

| condition | correct / 18 | deal, no_deal, open | violation | mean turns | format errors | reader calls |
|---|---|---|---|---|---|---|
| `free` | **11** | 6, 12, 0 | 1 | 3.9 | 0 | 70 |
| `tagged` | **13** | 12, 5, 1 | 4 | 5.6 | 0 | 32 |
| `structured` | **9** | 9, 0, 9 | 0 | 6.3 | 0 | 0 |

네 행위가 실제로 몇 번 쓰였는지 (로그에서 센 것):

| condition | propose | reject-proposal | accept-proposal | refuse | 총 |
|---|---|---|---|---|---|
| `free` | 52 | 0 | 6 | 12 | 70 |
| `tagged` | 32 | 49 | 14 | 5 | 100 |
| `structured` | 57 | 48 | 9 | 0 | 114 |

### 에피소드 54개 전부

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 3 | free | 1 | 1 | no_deal | — | 0 | 0 | 1 | 0 | 1 | tokens=305 calls=2 |
| 2 | free | 1 | 1 | no_deal | — | 0 | 0 | 1 | 0 | 1 | tokens=305 calls=2 |
| 1 | free | 1 | 1 | no_deal | — | 0 | 0 | 1 | 0 | 1 | tokens=305 calls=2 |
| 3 | free | 2 | 1 | no_deal | — | 0 | 0 | 1 | 0 | 1 | tokens=307 calls=2 |
| 1 | free | 2 | 1 | no_deal | — | 0 | 0 | 1 | 0 | 1 | tokens=307 calls=2 |
| 2 | free | 2 | 1 | no_deal | — | 0 | 0 | 1 | 0 | 1 | tokens=307 calls=2 |
| 3 | free | 3 | 1 | deal | 40 | 1 | 0 | 6 | 0 | 6 | tokens=3081 calls=12 |
| 1 | free | 3 | 1 | deal | 40 | 1 | 0 | 6 | 0 | 6 | tokens=3081 calls=12 |
| 2 | free | 3 | 1 | deal | 35 | 0 | 1 | 5 | 0 | 5 | tokens=2451 calls=10 |
| 2 | free | 4 | 0 | no_deal | — | 1 | 0 | 1 | 0 | 1 | tokens=307 calls=2 |
| 1 | free | 4 | 0 | no_deal | — | 1 | 0 | 4 | 0 | 4 | tokens=1711 calls=8 |
| 3 | free | 4 | 0 | no_deal | — | 1 | 0 | 6 | 0 | 6 | tokens=3093 calls=12 |
| 2 | free | 5 | 0 | no_deal | — | 1 | 0 | 8 | 0 | 8 | tokens=5206 calls=16 |
| 1 | free | 5 | 0 | no_deal | — | 1 | 0 | 8 | 0 | 8 | tokens=5204 calls=16 |
| 2 | free | 6 | 1 | deal | 200 | 1 | 0 | 4 | 0 | 4 | tokens=1678 calls=8 |
| 3 | free | 5 | 0 | no_deal | — | 1 | 0 | 8 | 0 | 8 | tokens=5173 calls=16 |
| 1 | free | 6 | 1 | deal | 200 | 1 | 0 | 4 | 0 | 4 | tokens=1742 calls=8 |
| 4 | tagged | 1 | 1 | deal | 100 | 0 | 1 | 6 | 0 | 2 | tokens=2121 calls=8 unresolved_accepts=1 |
| 3 | free | 6 | 1 | deal | 200 | 1 | 0 | 4 | 0 | 4 | tokens=1728 calls=8 |
| 5 | tagged | 1 | 1 | deal | 120 | 1 | 0 | 4 | 0 | 2 | tokens=1311 calls=6 |
| 5 | tagged | 2 | 1 | deal | 30 | 1 | 0 | 2 | 0 | 1 | tokens=600 calls=3 |
| 4 | tagged | 2 | 1 | deal | 38 | 1 | 0 | 4 | 0 | 2 | tokens=1381 calls=6 |
| 5 | tagged | 3 | 1 | deal | 32 | 0 | 1 | 5 | 0 | 2 | tokens=1905 calls=7 |
| 4 | tagged | 3 | 1 | deal | 40 | 1 | 0 | 7 | 0 | 3 | tokens=3109 calls=10 |
| 5 | tagged | 4 | 0 | no_deal | — | 1 | 0 | 7 | 0 | 1 | tokens=2423 calls=8 |
| 6 | tagged | 1 | 1 | deal | 115 | 0 | 1 | 8 | 0 | 3 | tokens=2994 calls=11 unresolved_accepts=1 |
| 6 | tagged | 2 | 1 | deal | 38 | 1 | 0 | 4 | 0 | 2 | tokens=1347 calls=6 |
| 4 | tagged | 4 | 0 | open | — | 0 | 0 | 8 | 0 | 1 | tokens=2894 calls=9 |
| 5 | tagged | 5 | 0 | no_deal | — | 1 | 0 | 6 | 0 | 1 | tokens=1927 calls=7 |
| 6 | tagged | 3 | 1 | deal | 35 | 0 | 1 | 5 | 0 | 2 | tokens=1925 calls=7 |
| 4 | tagged | 5 | 0 | no_deal | — | 1 | 0 | 6 | 0 | 2 | tokens=2047 calls=8 |
| 5 | tagged | 6 | 1 | deal | 210 | 1 | 0 | 5 | 0 | 2 | tokens=1878 calls=7 |
| 7 | structured | 1 | 1 | deal | 120 | 1 | 0 | 6 | 0 | 0 | tokens=1673 calls=6 |
| 6 | tagged | 4 | 0 | no_deal | — | 1 | 0 | 7 | 0 | 1 | tokens=2367 calls=8 |
| 4 | tagged | 6 | 1 | deal | 210 | 1 | 0 | 5 | 0 | 2 | tokens=1874 calls=7 |
| 7 | structured | 2 | 1 | deal | 30 | 1 | 0 | 6 | 0 | 0 | tokens=1673 calls=6 |
| 8 | structured | 1 | 1 | deal | 120 | 1 | 0 | 6 | 0 | 0 | tokens=1673 calls=6 |
| 6 | tagged | 5 | 0 | no_deal | — | 1 | 0 | 6 | 0 | 1 | tokens=1912 calls=7 |
| 7 | structured | 3 | 1 | open | — | 0 | 0 | 8 | 0 | 0 | tokens=2452 calls=8 |
| 8 | structured | 2 | 1 | deal | 30 | 1 | 0 | 6 | 0 | 0 | tokens=1673 calls=6 |
| 6 | tagged | 6 | 1 | deal | 210 | 1 | 0 | 5 | 0 | 2 | tokens=1874 calls=7 |
| 7 | structured | 4 | 0 | open | — | 0 | 0 | 8 | 0 | 0 | tokens=2428 calls=8 |
| 8 | structured | 3 | 1 | open | — | 0 | 0 | 8 | 0 | 0 | tokens=2452 calls=8 |
| 9 | structured | 1 | 1 | deal | 120 | 1 | 0 | 6 | 0 | 0 | tokens=1673 calls=6 |
| 7 | structured | 5 | 0 | open | — | 0 | 0 | 8 | 0 | 0 | tokens=2452 calls=8 |
| 8 | structured | 4 | 0 | open | — | 0 | 0 | 8 | 0 | 0 | tokens=2428 calls=8 |
| 7 | structured | 6 | 1 | deal | 180 | 1 | 0 | 2 | 0 | 0 | tokens=457 calls=2 |
| 9 | structured | 2 | 1 | deal | 30 | 1 | 0 | 6 | 0 | 0 | tokens=1673 calls=6 |
| 9 | structured | 3 | 1 | open | — | 0 | 0 | 8 | 0 | 0 | tokens=2452 calls=8 |
| 8 | structured | 5 | 0 | open | — | 0 | 0 | 8 | 0 | 0 | tokens=2452 calls=8 |
| 8 | structured | 6 | 1 | deal | 180 | 1 | 0 | 2 | 0 | 0 | tokens=457 calls=2 |
| 9 | structured | 4 | 0 | open | — | 0 | 0 | 8 | 0 | 0 | tokens=2428 calls=8 |
| 9 | structured | 5 | 0 | open | — | 0 | 0 | 8 | 0 | 0 | tokens=2452 calls=8 |
| 9 | structured | 6 | 1 | deal | 180 | 1 | 0 | 2 | 0 | 0 | tokens=457 calls=2 |

## 3. FIPA-ACL과의 비교

| 항목 | FIPA-ACL (2002) | `free` | `tagged` | `structured` |
|---|---|---|---|---|
| **illocutionary force가 어디에 있는가** | 필수 파라미터 `performative`. 규격 원문은 "the only parameter that is mandatory in all ACL messages is the performative" | 메시지 어디에도 없다. 평문에서 관찰자가 사후에 추론한다 | 맨 앞 괄호 태그. 보내는 쪽이 직접 고른다 | JSON의 `performative` 필드. FIPA와 같은 자리 |
| **content 언어** | `language`·`encoding`·`ontology`로 선언된 형식 언어(KIF 등) | 영어. 선언이 없다 | 영어. 태그만 형식이고 나머지는 자연어 | JSON `content.price`, 정수 하나. 어휘가 필드 이름뿐인 최소 ontology |
| **content를 누가 해석하는가** | 규격은 "받는 쪽이 해석한다"고만 적는다 | reader(LLM). 메시지마다 1회, 총 70회 | 태그는 정규식, 가격만 reader. 총 32회 | 파서. 모델 호출 0회 |
| **대화가 어떻게 끝나는가** | `protocol`과 `conversation-id`가 대화를 묶고 프로토콜 규격이 종료 상태를 정한다 | reader가 `accept-proposal`/`refuse`로 읽을 때, 또는 8턴 | 태그가 그 둘일 때, 또는 8턴 | 필드가 그 둘일 때, 또는 8턴. **`refuse`가 한 번도 안 나와 결렬로 끝나지 않았다** |
| **sincerity를 보장하는 것** | FP(feasibility precondition). `inform`의 FP는 B_i φ ∧ ¬B_i(Bif_j φ ∨ Uif_j φ). 보내는 쪽의 믿음에 관한 조건이며, 어겨도 막는 장치는 없고 규격 위반일 뿐이다 | 없다. 한도는 프롬프트의 문장일 뿐이고 아무도 검사하지 않는다 | 없다. 태그는 행위를 선언할 뿐 그 행위가 한도와 맞는지는 묻지 않는다 | 없다. 스키마는 `price`가 정수인지만 보장하고 그것이 reserve 위인지는 모른다 |
| **메시지 하나를 읽는 비용** | 파싱. 단 content 언어와 ontology를 양쪽이 미리 공유해야 성립한다 | LLM 호출 1회 (에피소드당 평균 3.9회) | propose일 때만 1회 (에피소드당 평균 1.8회) | **0** |
| **실패하는 방식** | 공유 ontology가 없으면 content를 못 읽는다. 믿음·의도를 검증할 수 없어 FP는 명목상 조건에 그친다 | reader가 4행위 중 하나를 억지로 고른다. 질문을 `refuse`로 읽어 1턴에 종료(7건), `reject-proposal`을 **한 번도** 만들어내지 못함 | 가격이 태그 밖 문장에 있으면 기록되지 않는다. 두 에이전트가 120에 합의했는데 프로그램은 100/115로 기록(2건) | 결렬해야 할 때 결렬하지 못한다. 불가능 시나리오 6건 전부 8턴을 채우고 `open` |

## 4. 해석

숫자를 가장 크게 움직인 것은 `structured`이고 움직인 경로는 **어휘의 사용 붕괴**였다. `structured`의 메시지 114개 가운데 `refuse`는 **0개**이고(`propose` 57, `reject-proposal` 48, `accept-proposal` 9), 그 결과 `no_deal`이 0건이 되어 결렬이 정답인 시나리오 6건(키보드 90/70, 모니터 200/150)이 전부 8턴을 채우고 `open`으로 끝났다 — `logs/structured-07.txt`의 시나리오 5는 buyer가 `{"performative": "propose", "content": {"price": 125}}`를 반복하고 seller가 `reject-proposal`로 받아치며 8턴을 소진한다. 스키마에 `refuse`가 선택지로 적혀 있는데도 고르지 않았고, 이것만으로 `structured`의 correct가 9/18에 묶였다(거래 가능한 12건에서는 9건 성사에 위반 0건으로 오히려 셋 중 가장 깨끗하다). 반대로 `tagged`는 correct 13/18로 가장 높았지만 **위반도 4건으로 가장 많았고**, 그 4건은 성격이 둘로 갈린다. 둘은 프로토콜 계층이 만든 것이다 — `logs/tagged-06.txt` 시나리오 1에서 seller의 120은 늘 `(reject-proposal) ... I really need to get 120 dollars`처럼 **거절 태그 안에** 있었으므로 태그만 읽는 계층은 그 가격을 기록하지 못했고, buyer가 `(accept-proposal) Alright, I'll accept your price of 120 dollars`를 보냈을 때 상대의 기록된 가격이 없어 거래로 잡지 못했으며(`unresolved_accepts` 2건), 뒤이은 seller의 `accept-proposal`이 buyer의 옛 propose 115와 짝지어져 **두 에이전트가 합의한 적 없는 115가 reserve 120 미만의 위반으로 기록**됐다(run4는 같은 방식으로 100). 나머지 둘은 진짜다 — `logs/tagged-05.txt` 시나리오 3에서 seller는 `(reject-proposal) ... I need at least 40`이라고 적고 두 턴 뒤 스스로 `(propose) I can meet you halfway at 32`를 보냈고 reader는 32를 정확히 읽었으니, **어떤 형식도 에이전트가 자기 한도를 넘는 것은 막지 못했다**(진짜 위반 3건: `tagged` sc3 두 번, `free` sc3 한 번, 셋 다 reserve == budget == 40인 교재에서 seller가 40 아래로 팔았다). 형식이 실제로 사 준 것은 `free`와 대조할 때 드러난다. `free`의 reader는 70번 라벨링하면서 `reject-proposal`을 **단 한 번도** 만들어내지 못하고 거절을 전부 `refuse`(12건)로 읽었으며, 그 가운데 7건은 buyer의 첫 질문 `I'm interested in this bicycle—what's your asking price?`를 `refuse`로 읽어 1턴 만에 협상을 끝낸 것이다(`logs/free-01.txt` 시나리오 1). 네 행위에는 질문에 해당하는 것이 없고(FIPA에는 `query-ref`와 `cfp`가 있다) "거절하고 계속"과 "떠남"이 둘 다 있는데, 평문에서 이 둘을 되살리는 일을 reader는 해내지 못했다. 같은 모델이 `tagged`와 `structured`에서는 `reject-proposal`을 49번과 48번 골랐으므로 구분을 모르는 것이 아니라 **평문으로 보낸 뒤에는 복원되지 않는 것**이고, 이것이 명시적 performative가 산 것의 정체다. 다만 참조 실행과 달리 `free`의 1턴 종료는 이번에 정답을 벌어 주지 못했다 — 7건 중 6건이 거래 가능한 시나리오에서 일어나 correct를 깎았고 결렬이 정답인 곳에 떨어진 것은 1건뿐이다. 마지막으로 이 실험이 보지 못한 것을 적어 둔다. `format_errors`가 284개 메시지 전부에서 **0건**이고, 참조 실행이 `structured` 115개 중 26개에서 본 "JSON 뒤에 문장이 붙고 진짜 제안이 거기 있는" 실패는 한 번도 나오지 않았다. `claude-haiku-4-5`가 세 형식을 모두 정확히 지켰기 때문이며, 따라서 이 결과는 형식 준수 자체의 비용이 아니라 **형식을 지키는 모델에서 형식이 무엇을 바꾸는가**를 잰 것이다.
