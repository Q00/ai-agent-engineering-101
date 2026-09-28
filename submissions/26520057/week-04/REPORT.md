# Week 04 — Speech acts in practice: free, tagged, structured negotiation

학번 26520057 · buyer LLM 1 + seller LLM 1, 조건 3개 (`free` / `tagged` / `structured`) × 시나리오 6개 × 3회

---

## 1. 설정

### 고정된 조건 (재현에 필요한 전부)

| 항목 | 값 |
|---|---|
| provider | OpenAI Chat Completions (`openai` Python SDK) |
| 모델 | `gpt-4o-mini` (`AGENT_MODEL` 미설정 시 기본값). 에이전트와 reader가 같은 모델 사용 |
| temperature | `0.0` (에이전트, reader 모두) |
| max_tokens | 에이전트 `200`, reader `60` |
| 턴 한도 | buyer와 seller 메시지 합계 `10`. 넘기면 `open` |
| 시나리오 | [`scenarios.json`](scenarios.json) 6개. 딜 가능 4개(s1 여유 있음, s2 폭 30, s3 reserve = budget, s4 여유 있음), 딜 불가 2개(s5, s6). 실행 **전** 커밋 (`8279e33`) |
| 대화 구성 | 각 에이전트 = system prompt 1개. 자기가 보낸 메시지는 `assistant`, 상대 메시지는 원문 그대로 `user`. buyer의 첫 `user` 메시지는 kickoff 문장 (아래) |
| 실행 순서 | run 1–3 `free`, 4–6 `tagged`, 7–9 `structured`. run 하나 = 시나리오 6개 전부 |

### 프로토콜 규칙 ([`negotiation.py`](negotiation.py) `run_episode`)

메시지마다 protocol layer가 `(performative, price)`로 읽고, 에피소드의 흐름은 텍스트가 아니라 이 읽기 결과로 정해진다.

| 읽은 결과 | 처리 |
|---|---|
| `propose` + 가격 | 그 쪽의 "마지막 제안가"를 갱신하고 계속 |
| `accept-proposal` | **상대의 마지막 제안가**로 `deal`, 종료. 메시지 안에 다른 가격이 있으면 `note`에 기록 |
| `reject-proposal` | 계속 |
| `refuse` | `no_deal`, 종료 |
| 읽기 실패 / 가격 없는 `propose` / 상대 제안이 없는 `accept-proposal` | `format_errors` +1. 상태 변화 없이 메시지는 상대에게 그대로 전달하고 계속 |

- `correct`: 딜 가능 시나리오에서는 `deal`이면서 reserve ≤ price ≤ budget일 때, 딜 불가 시나리오에서는 `deal`이 아닐 때(`no_deal` 또는 `open`) 1
- `violation`: `deal`이면서 price < reserve 또는 price > budget일 때 1
- `turns`: 주고받은 메시지 수. `reader_calls`: 메시지를 읽는 데 쓴 모델 호출 수 (에이전트 호출은 포함하지 않음)

### 프롬프트

system prompt = 역할 문단 + 규칙 문단 + **형식 문단** 순서이며, 조건마다 형식 문단만 바뀐다.

역할 문단 (buyer / seller):

```
You are a buyer negotiating to buy a {item}. Your budget is ${budget}: you must never pay more than that. The budget is private; do not reveal it. Try to pay as little as possible.

You are a seller negotiating to sell a {item}. Your reserve price is ${reserve}: you must never sell for less than that. The reserve is private; do not reveal it. Try to sell for as much as possible.
```

규칙 문단 (공통, `{other}` = 상대 역할):

```
You are talking to the {other}. The buyer speaks first. Every message you send performs exactly one of four acts: propose (offer a price), accept-proposal (agree to the {other}'s last proposed price; this ends the negotiation with a deal), reject-proposal (decline the {other}'s last proposal and keep negotiating), refuse (walk away; this ends the negotiation with no deal). The negotiation also ends with no deal after 10 messages in total.
```

형식 문단 (독립변수):

| 조건 | 형식 문단 | harness가 읽는 방법 |
|---|---|---|
| `free` | `Write your message in plain English, as you would to a person. Do not use tags, labels, or JSON.` | 메시지마다 LLM reader (`READER_SYSTEM`)가 performative와 price를 라벨링. reader 출력에서 첫 `{...}`를 찾아 JSON으로 읽음 |
| `tagged` | `Start your message with exactly one tag in parentheses: (propose), (accept-proposal), (reject-proposal), or (refuse). After the tag, write the rest in plain English. When you propose, state the price in the text.` | 메시지 맨 앞의 태그를 regex `^\s*\((propose\|accept-proposal\|reject-proposal\|refuse)\)`로 읽음 (대소문자 무시). `propose`일 때만 price reader (`PRICE_READER_SYSTEM`) 호출 |
| `structured` | `Reply with one JSON object and nothing else: {"performative": "propose" \| "accept-proposal" \| "reject-proposal" \| "refuse", "content": {"price": integer dollars or null}}. price is required for propose and null for the other acts.` | `json.loads`만 사용하고 모델은 호출하지 않음. 엄격하게 읽음: 코드펜스로 감싸면 실패 |

buyer kickoff (buyer의 첫 `user` 메시지, 세 조건 공통):

```
(The negotiation starts now. Send your first message to the seller.)
```

reader 프롬프트 (`free`). user 메시지는 `"{buyer|seller}: {메시지 원문}"`이며 대화 이력 없이 메시지 하나만 본다.

```
You read one message from a two-party price negotiation between a buyer and a seller. Label it with exactly one performative from this list: propose (the speaker offers a specific price), accept-proposal (the speaker agrees to the other party's last offered price), reject-proposal (the speaker declines the other party's last offer but keeps negotiating), refuse (the speaker ends the negotiation without a deal). Reply with one JSON object and nothing else: {"performative": "...", "price": integer or null}. price is the price the speaker offers in this message, or null if there is none.
```

price reader 프롬프트 (`tagged`, `propose`일 때만 호출). user 메시지는 메시지 원문이다.

```
You read one message from a price negotiation. The speaker is proposing a price. Reply with only the price in whole dollars that the speaker offers in this message, as digits and nothing else. If the message offers no price, reply NONE.
```

### 실행 명령

```bash
cd submissions/26520057/week-04
pip install openai
export OPENAI_API_KEY=...
python dry_test.py        # API 없이 가짜 모델로 protocol layer 확인 (파일 쓰지 않음)
python run.py             # run 1-9, results.csv에 에피소드마다 한 줄 + logs/runNN-<condition>.txt
python summarize.py       # 아래 2절의 표 출력
```

중간에 끊기면 같은 명령을 다시 실행한다. results.csv에 이미 있는 `(run, scenario)`는 건너뛴다. crash로 끝난 에피소드도 건너뛰므로, 다시 돌리려면 `--first-run 10`으로 새 번호를 붙인다. 로그 파일 첫 줄에 provider, 모델, temperature, max_tokens, 턴 한도, Python 버전을 기록하고, 에피소드마다 두 에이전트의 system prompt 전문, 메시지 전문, 읽기 결과(reader 원출력 포함), 에피소드 결과를 남긴다.

---

## 2. 결과

<!-- TODO(실행 후): python summarize.py 출력의 첫 표를 붙여넣기 -->

| condition | episodes | crashed | correct | violations | deal / no_deal / open | mean turns | format errors | reader calls |
|---|---|---|---|---|---|---|---|---|
| `free` | | | | | | | | |
| `tagged` | | | | | | | | |
| `structured` | | | | | | | | |

### 에피소드별 결과 (`results.csv`)

<!-- TODO(실행 후): python summarize.py 출력의 두 번째 표를 붙여넣기 -->

---

## 3. FIPA-ACL과 세 조건 비교

| 항목 | FIPA-ACL (2002) | `free` | `tagged` | `structured` |
|---|---|---|---|---|
| illocutionary force의 위치 | 필수 필드 `:performative` | 문장 속 어딘가. 명시되지 않음 | 메시지 첫머리 태그 `(act)` | JSON 필드 `performative` |
| content language | 선언된 형식 언어 (SL 등) + `:ontology` | 자연어 영어 | 자연어 영어 (태그 뒤) | JSON `{"price": int}`. 온톨로지는 선언하지 않았고 프롬프트의 한 문장뿐 |
| content를 해석하는 주체 | 수신자가 온톨로지에 따라 파싱 | LLM reader (act와 price 모두) | regex (act) + LLM price reader (price) | `json.loads` (둘 다) |
| 대화가 끝나는 방식 | interaction protocol (예: FIPA Contract Net, Iterated Contract Net)이 정한 상태 전이 | reader가 `accept-proposal`/`refuse`로 라벨링하거나 턴 한도 | 태그가 `accept-proposal`/`refuse`이거나 턴 한도 | 필드가 `accept-proposal`/`refuse`이거나 턴 한도 |
| sincerity를 보장하는 것 | feasibility precondition / rational effect 의미론 (보장이 아니라 가정) | 없음. 프롬프트의 "never below reserve"뿐 | 같음 | 같음 |
| 메시지 하나를 읽는 비용 | 파서 1회 | 모델 호출 1회 | `propose`일 때만 모델 호출 1회 | 모델 호출 0회 |
| 나타난 실패 유형 | (형식 불일치는 파서에서 거부) | <!-- TODO: 로그에서 --> | <!-- TODO --> | <!-- TODO --> |

---

## 4. 해석

<!-- TODO(실행 후): 어느 조건이 어떤 지표를 움직였고 왜 그런지, 로그 인용을 근거로. 예상 포인트:
- free: buyer의 첫 질문("what's your asking price?")을 reader가 refuse로 라벨링해 1턴에 끝나는지
- 역제안(counter-offer)을 accept로 읽은 에피소드가 있는지
- reserve 미만 판매나 budget 초과 구매(violation)가 조건과 무관하게 나오는지. 형식은 sincerity나 제약 준수를 바꾸지 못한다
- structured의 코드펜스 등 format error, tagged의 태그 누락
- reader_calls 비용 차이 -->
