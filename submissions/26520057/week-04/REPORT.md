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
python run.py             # run 1-9, results.csv에 에피소드마다 한 줄 + logs/runNN-<condition>.txt
python summarize.py       # 아래 2절의 표 출력
```

중간에 끊기면 같은 명령을 다시 실행한다. results.csv에 이미 있는 `(run, scenario)`는 건너뛴다. crash로 끝난 에피소드도 건너뛰므로, 다시 돌리려면 `--first-run 10`으로 새 번호를 붙인다. 로그 파일 첫 줄에 provider, 모델, temperature, max_tokens, 턴 한도, Python 버전을 기록하고, 에피소드마다 두 에이전트의 system prompt 전문, 메시지 전문, 읽기 결과(reader 원출력 포함), 에피소드 결과를 남긴다.

---

## 2. 결과

### 실행 경과

- **run 01–09 (54 에피소드):** 전부 첫 모델 호출에서 `AuthenticationError 401`로 crash했다. `OPENAI_API_KEY`에 키가 아니라 hex로 인코딩된 문자열(`73656375…` = `"secu…"`)이 들어가 있었다. 행은 results.csv에 그대로 남겼고(커밋 `3884cc8`) 아래 집계에서 빠진다.
- **run 10–18:** 키를 고친 뒤 `python run.py --first-run 10`으로 다시 돌렸다. 54 에피소드 모두 끝났고 crash는 0건이다. 아래 수치는 모두 이 18개 run(조건마다 18 에피소드)의 결과다.

### 조건별 요약 (`python summarize.py`)

| condition | episodes | crashed | correct | violations | deal / no_deal / open | mean turns | format errors | reader calls |
|---|---|---|---|---|---|---|---|---|
| `free` | 36 | 18 | 6/18 | 0 | 0 / 4 / 14 | 9.9 | 14 | 179 |
| `tagged` | 36 | 18 | 12/18 | 4 | 10 / 0 / 8 | 8.9 | 7 | 46 |
| `structured` | 36 | 18 | 12/18 | 0 | 6 / 0 / 12 | 8.4 | 0 | 0 |

(crashed 18 = run 01–09의 401. correct와 그 오른쪽 열은 crash하지 않은 18 에피소드 기준)

딜 불가 시나리오(s5, s6)는 세 조건 모두 18/18 correct였다. 전부 `open`이고, 딜이 성사된 경우는 없었다. 조건 간 차이는 전부 딜 가능 시나리오(s1–s4, 조건마다 12 에피소드)에서 나왔다.

| 딜 가능 에피소드 (12개씩) | `free` | `tagged` | `structured` |
|---|---|---|---|
| correct | 0 | 6 | 6 |
| 프로토콜이 기록한 딜 | 0 | 10 | 6 |
| 기록 가격 ≠ 대화 텍스트에서 합의한 가격 | – | 8 | 0 |
| violation (기록 가격 기준) | 0 | 4 | 0 |
| 텍스트상으로도 한도를 어긴 딜 | 0 | 1 (run 13 s2, seller가 $245에 accept, reserve $250) | 0 |

### 에피소드별 결과 (`results.csv`)

run 01–09의 crash `note`는 마스킹된 키 문자열이 300자 가까이 이어져서 이 표에서는 줄였다. 원문은 results.csv에 있다.

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | free | s1 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 1 | free | s2 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 1 | free | s3 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 1 | free | s4 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 1 | free | s5 | 0 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 1 | free | s6 | 0 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 2 | free | s1 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 2 | free | s2 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 2 | free | s3 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 2 | free | s4 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 2 | free | s5 | 0 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 2 | free | s6 | 0 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 3 | free | s1 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 3 | free | s2 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 3 | free | s3 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 3 | free | s4 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 3 | free | s5 | 0 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 3 | free | s6 | 0 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 4 | tagged | s1 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 4 | tagged | s2 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 4 | tagged | s3 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 4 | tagged | s4 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 4 | tagged | s5 | 0 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 4 | tagged | s6 | 0 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 5 | tagged | s1 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 5 | tagged | s2 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 5 | tagged | s3 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 5 | tagged | s4 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 5 | tagged | s5 | 0 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 5 | tagged | s6 | 0 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 6 | tagged | s1 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 6 | tagged | s2 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 6 | tagged | s3 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 6 | tagged | s4 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 6 | tagged | s5 | 0 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 6 | tagged | s6 | 0 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 7 | structured | s1 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 7 | structured | s2 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 7 | structured | s3 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 7 | structured | s4 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 7 | structured | s5 | 0 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 7 | structured | s6 | 0 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 8 | structured | s1 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 8 | structured | s2 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 8 | structured | s3 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 8 | structured | s4 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 8 | structured | s5 | 0 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 8 | structured | s6 | 0 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 9 | structured | s1 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 9 | structured | s2 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 9 | structured | s3 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 9 | structured | s4 | 1 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 9 | structured | s5 | 0 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 9 | structured | s6 | 0 |  |  |  |  |  |  |  | crash: AuthenticationError 401 (전문은 results.csv) |
| 10 | free | s1 | 1 | open |  | 0 | 0 | 10 | 1 | 10 |  |
| 10 | free | s2 | 1 | open |  | 0 | 0 | 10 | 0 | 10 |  |
| 10 | free | s3 | 1 | no_deal |  | 0 | 0 | 10 | 1 | 10 | seller refused at turn 10 |
| 10 | free | s4 | 1 | open |  | 0 | 0 | 10 | 1 | 10 |  |
| 10 | free | s5 | 0 | open |  | 1 | 0 | 10 | 1 | 10 |  |
| 10 | free | s6 | 0 | open |  | 1 | 0 | 10 | 1 | 10 |  |
| 11 | free | s1 | 1 | open |  | 0 | 0 | 10 | 1 | 10 |  |
| 11 | free | s2 | 1 | no_deal |  | 0 | 0 | 9 | 0 | 9 | buyer refused at turn 9 |
| 11 | free | s3 | 1 | open |  | 0 | 0 | 10 | 1 | 10 |  |
| 11 | free | s4 | 1 | open |  | 0 | 0 | 10 | 1 | 10 |  |
| 11 | free | s5 | 0 | open |  | 1 | 0 | 10 | 1 | 10 |  |
| 11 | free | s6 | 0 | open |  | 1 | 0 | 10 | 1 | 10 |  |
| 12 | free | s1 | 1 | open |  | 0 | 0 | 10 | 0 | 10 |  |
| 12 | free | s2 | 1 | open |  | 0 | 0 | 10 | 0 | 10 |  |
| 12 | free | s3 | 1 | no_deal |  | 0 | 0 | 10 | 1 | 10 | seller refused at turn 10 |
| 12 | free | s4 | 1 | no_deal |  | 0 | 0 | 10 | 1 | 10 | seller refused at turn 10 |
| 12 | free | s5 | 0 | open |  | 1 | 0 | 10 | 1 | 10 |  |
| 12 | free | s6 | 0 | open |  | 1 | 0 | 10 | 1 | 10 |  |
| 13 | tagged | s1 | 1 | deal | 825 | 1 | 0 | 8 | 1 | 3 |  |
| 13 | tagged | s2 | 1 | deal | 240 | 0 | 1 | 10 | 0 | 4 |  |
| 13 | tagged | s3 | 1 | deal | 325 | 0 | 1 | 8 | 1 | 3 |  |
| 13 | tagged | s4 | 1 | deal | 230 | 1 | 0 | 8 | 1 | 3 |  |
| 13 | tagged | s5 | 0 | open |  | 1 | 0 | 10 | 0 | 1 |  |
| 13 | tagged | s6 | 0 | open |  | 1 | 0 | 10 | 0 | 1 |  |
| 14 | tagged | s1 | 1 | deal | 765 | 1 | 0 | 10 | 1 | 4 |  |
| 14 | tagged | s2 | 1 | open |  | 0 | 0 | 10 | 0 | 5 |  |
| 14 | tagged | s3 | 1 | deal | 350 | 0 | 1 | 8 | 1 | 3 |  |
| 14 | tagged | s4 | 1 | open |  | 0 | 0 | 10 | 0 | 1 |  |
| 14 | tagged | s5 | 0 | open |  | 1 | 0 | 10 | 0 | 1 |  |
| 14 | tagged | s6 | 0 | open |  | 1 | 0 | 10 | 0 | 5 |  |
| 15 | tagged | s1 | 1 | deal | 825 | 1 | 0 | 8 | 1 | 3 |  |
| 15 | tagged | s2 | 1 | deal | 250 | 1 | 0 | 10 | 0 | 5 |  |
| 15 | tagged | s3 | 1 | deal | 300 | 0 | 1 | 8 | 1 | 1 |  |
| 15 | tagged | s4 | 1 | deal | 200 | 1 | 0 | 2 | 0 | 1 |  |
| 15 | tagged | s5 | 0 | open |  | 1 | 0 | 10 | 0 | 1 |  |
| 15 | tagged | s6 | 0 | open |  | 1 | 0 | 10 | 0 | 1 |  |
| 16 | structured | s1 | 1 | deal | 800 | 1 | 0 | 4 | 0 | 0 |  |
| 16 | structured | s2 | 1 | deal | 250 | 1 | 0 | 6 | 0 | 0 |  |
| 16 | structured | s3 | 1 | open |  | 0 | 0 | 10 | 0 | 0 |  |
| 16 | structured | s4 | 1 | open |  | 0 | 0 | 10 | 0 | 0 |  |
| 16 | structured | s5 | 0 | open |  | 1 | 0 | 10 | 0 | 0 |  |
| 16 | structured | s6 | 0 | open |  | 1 | 0 | 10 | 0 | 0 |  |
| 17 | structured | s1 | 1 | deal | 800 | 1 | 0 | 6 | 0 | 0 |  |
| 17 | structured | s2 | 1 | deal | 250 | 1 | 0 | 6 | 0 | 0 |  |
| 17 | structured | s3 | 1 | open |  | 0 | 0 | 10 | 0 | 0 |  |
| 17 | structured | s4 | 1 | open |  | 0 | 0 | 10 | 0 | 0 |  |
| 17 | structured | s5 | 0 | open |  | 1 | 0 | 10 | 0 | 0 |  |
| 17 | structured | s6 | 0 | open |  | 1 | 0 | 10 | 0 | 0 |  |
| 18 | structured | s1 | 1 | deal | 800 | 1 | 0 | 4 | 0 | 0 |  |
| 18 | structured | s2 | 1 | deal | 250 | 1 | 0 | 6 | 0 | 0 |  |
| 18 | structured | s3 | 1 | open |  | 0 | 0 | 10 | 0 | 0 |  |
| 18 | structured | s4 | 1 | open |  | 0 | 0 | 10 | 0 | 0 |  |
| 18 | structured | s5 | 0 | open |  | 1 | 0 | 10 | 0 | 0 |  |
| 18 | structured | s6 | 0 | open |  | 1 | 0 | 10 | 0 | 0 |  |

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
| 나타난 실패 유형 | (형식 불일치는 파서에서 거부) | buyer의 여는 질문(15/18)은 네 act 중 어디에도 맞지 않는다. reader는 이를 가격 없는 `propose`로 14번, 앞선 제안이 없는데도 `reject-proposal`로 1번 라벨링했고, `refuse`로 읽은 적은 없다. 대화에서 accept가 한 번도 나오지 않아 딜 0건 | 역제안 가격을 `reject-proposal` 태그 안에 넣는다 (97/97개 모두 `$` 금액 포함, seller의 `(propose)`는 0번). 그래서 프로토콜은 seller의 가격을 모르고, buyer가 그 가격에 accept하면 format error가 나며(7번) 딜 가격이 텍스트와 달라진다(8/10) | seller가 가격 없는 `reject-proposal`만 보낸다 (seller `propose` 0번). 역제안 채널이 사라져 buyer 혼자 가격을 올리다 s3, s4는 6/6 모두 `open`. 파싱 실패 0 |

---

## 4. 해석

명시적 performative는 **대화를 끝내는 힘**을 만들었지만, **가격을 옮기는 채널**은 조건마다 다른 곳에서 새어 나갔다. `free`에서는 두 에이전트가 사람처럼 흥정만 이어가고 누구도 받아들이지 않았다. 딜 가능 12 에피소드가 전부 `open` 또는 턴 9–10의 `refuse`로 끝났다 (예: run 10 s1 `[10] SELLER: ... I can't accept $810. My lowest price is $825.`, reader는 `reject-proposal price=825`로 읽음). 과제가 예상한 "여는 질문을 `refuse`로 읽고 1턴 종료"는 이 설정에서 나오지 않았다. 대신 reader가 `Can you tell me ... what price you're asking for?`를 가격 없는 `propose`로 14번 읽었다. 네 act 어휘에 query-ref나 cfp가 없다는 점이 여기서는 format error 14개로 드러났다. `tagged`는 딜을 10건 만들었지만, 에이전트가 태그와 내용을 섞어 썼다. `(reject-proposal) ... I can go down to $400.`처럼 reject 태그 안에 역제안을 넣었고(97/97), 프로토콜은 FIPA 의미론대로 reject-proposal에서 가격을 읽지 않는다. 그 결과 run 13–15 s3에서 buyer의 `(accept-proposal) $400 works for me.`는 받아들일 seller 제안이 없어 format error가 되었다. 이어서 seller의 `(accept-proposal) Great! I'm glad we could agree on $400.`은 buyer의 마지막 `propose`($325/$350/$300)를 받아들인 것으로 기록되었다. violation 4건 가운데 3건은 텍스트상으로는 reserve 그대로인 $400 합의였는데, 태그가 만든 공식 기록에서만 reserve 미만 판매가 된 경우다. 나머지 1건(run 13 s2, `[10] SELLER: (accept-proposal) Thank you for your final offer of $245.`, reserve $250)은 seller가 텍스트상으로도 실제로 reserve 아래에서 판 경우라 따로 센다. 가격은 $240으로 기록됐다. `structured`는 파싱 실패 0, reader 호출 0으로 가장 싸고 기록과 텍스트가 어긋날 여지도 없었다. 하지만 seller가 `{"performative": "reject-proposal", "content": {"price": null}}`만 반복하고 한 번도 `propose`하지 않아서, buyer가 스스로 한도 안의 가격까지 올라온 s1, s2만 딜이 되고 s3, s4는 6/6 모두 `open`이었다. 형식이 바꾸지 못한 것도 있다. 딜 불가 시나리오(s5, s6)는 세 조건 모두 18/18 `open`으로, 어느 쪽도 한도를 넘겨 합의하지 않았다. 텍스트상 한도 위반은 형식과 무관하게 한 번 나왔다(`tagged` s2). 비용은 예상대로 읽기 방식이 정했다: `free` 179회(메시지마다 1회), `tagged` 46회(`propose`일 때만), `structured` 0회. 정리하면, 태그는 act 읽기의 모호함을 없앴지만 LLM이 태그 하나에 act 두 개(거절+역제안)를 담는 것을 막지 못했다. JSON은 그 혼합을 원천 차단했지만 seller가 역제안 자체를 하지 않게 만들었다. 네 act 어휘에 "거절하면서 다른 가격 제시"가 따로 없다는 점이 세 조건 모두에서 다른 모습으로 드러났다.
