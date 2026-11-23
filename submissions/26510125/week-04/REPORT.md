# REPORT.md

## 1. Setup

- **Provider / model**: Anthropic API, `claude-haiku-4-5-20251001`. **temperature = 0.7**(모든 호출 동일), `max_tokens=300`.
  OpenRouter 무료 모델은 일일 50콜 한도 때문에 3주차에서 며칠을 잃어서 이번엔 처음부터 Anthropic으로 돌렸다.
- **turn limit = 8** (양쪽 4번씩). 에피소드는 `accept-proposal`(deal) / `refuse`(no_deal) / 턴 제한(open)으로 끝난다.
- **시나리오**: `scenarios.json`, 6개. s1–s3은 거래 가능(reserve ≤ budget, s3은 정확히 같음 300=300), s4–s6은 불가능. 런 전에 커밋했다.
- **역할 프롬프트**(세 조건 공통) — buyer:
  > You are the BUYER negotiating the price of a used {item}. Your private budget is {limit}: you must never agree to pay more than {limit}, and you should try to pay clearly less. You speak first. The seller cannot see your budget. Keep every message to one or two sentences.
  seller:
  > You are the SELLER negotiating the price of a used {item}. Your private reserve price is {limit}: you must never agree to sell for less than {limit}, and you should try to get clearly more. The buyer cannot see your reserve. Keep every message to one or two sentences.
- **형식 문단**(조건별로 이것만 바뀜):
  - `free`: Exactly four communicative acts exist in this conversation: propose (offer a price), accept-proposal (agree to the other side's last price, which closes the deal), reject-proposal (decline and keep talking), refuse (leave the negotiation with no deal). Write in plain English only. Do not use tags, labels, or JSON; say what you mean in a natural sentence.
  - `tagged`: Exactly four communicative acts exist in this conversation: propose (offer a price), accept-proposal (agree to the other side's last price, which closes the deal), reject-proposal (decline and keep talking), refuse (leave the negotiation with no deal). Start every message with exactly one act tag in parentheses, then plain English. Example: (propose) I can do 120 for it. Example: (accept-proposal) Deal, 120 it is.
  - `structured`: Exactly four communicative acts exist in this conversation: propose (offer a price), accept-proposal (agree to the other side's last price, which closes the deal), reject-proposal (decline and keep talking), refuse (leave the negotiation with no deal). Reply with exactly one JSON object and nothing else, of the form {"performative": "<act>", "content": {"price": <integer or null>}}. Use a price only with propose; use null otherwise. No prose, no code fences.
- **Reader 프롬프트**(free는 메시지마다, tagged는 propose의 가격만):
  > You label negotiation messages. Given one message, reply with exactly one JSON object: {"performative": "<one of propose|accept-proposal|reject-proposal|refuse>", "price": <integer or null>}. price is the number offered when the act is propose, otherwise null. No prose, no code fences.

  > Extract the price offered in this message. Reply with exactly one JSON object: {"price": <integer or null>}. No prose.
- **하네스 규칙**(코드가 메시지를 읽는 방식, `negotiation.py`):
  - reader는 **메시지 하나만** 본다(대화 맥락 없음). 참조 실행의 reader는 대화 전체를 봤다 — 이 차이가 4번의 free 결과를 설명한다.
  - `accept-proposal`은 **상대가 마지막으로 `propose`한 가격**에 묶인다. 상대의 standing offer가 없으면 format_error로 세고 대화는 계속된다.
  - 읽지 못한 메시지(태그 없음 / JSON 깨짐 / reader가 네 행위 밖의 답)는 format_error로 세고, 메시지는 그대로 상대에게 전달된다.
  - `correct` = 거래 가능하면 한도 안 가격의 deal, 불가능하면 no_deal. `violation` = deal 가격이 reserve 아래 또는 budget 위.

**실행**:
```bash
export ANTHROPIC_API_KEY=<your key>
python run_experiment.py --repeats 3 --turn-limit 8   # 이어서 돌리면 끝난 (run, scenario)는 건너뜀
```

## 2. Results

조건별 집계(54 에피소드, 크래시 0):

| condition | episodes | correct | violations | deal / no_deal / open | mean turns | format_errors | reader_calls |
|---|---|---|---|---|---|---|---|
| free | 18 | 10/18 | 0 | 7 / 4 / 7 | 6.00 | 1 | 108 |
| tagged | 18 | 11/18 | 3 | 6 / 11 / 1 | 6.50 | 2 | 27 |
| structured | 18 | 6/18 | 0 | 6 / 0 / 12 | 6.78 | 0 | 0 |

에피소드별(`results.csv` 그대로; `price`는 **하네스가 기록한** 값 — 4번에서 설명하듯 tagged의 위반 3건은 에이전트가 합의한 가격과 다르다):

| run | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls |
|---|---|---|---|---|---|---|---|---|---|
| free-r1 | s1 | 1 | deal | 260 | 1 | 0 | 7 | 0 | 7 |
| free-r1 | s2 | 1 | deal | 520 | 1 | 0 | 5 | 0 | 5 |
| free-r1 | s3 | 1 | open | — | 0 | 0 | 8 | 0 | 8 |
| free-r1 | s4 | 0 | open | — | 0 | 0 | 8 | 0 | 8 |
| free-r1 | s5 | 0 | open | — | 0 | 0 | 8 | 0 | 8 |
| free-r1 | s6 | 0 | open | — | 0 | 0 | 8 | 0 | 8 |
| free-r2 | s1 | 1 | deal | 210 | 1 | 0 | 4 | 0 | 4 |
| free-r2 | s2 | 1 | deal | 520 | 1 | 0 | 7 | 0 | 7 |
| free-r2 | s3 | 1 | no_deal | — | 0 | 0 | 1 | 0 | 1 |
| free-r2 | s4 | 0 | no_deal | — | 1 | 0 | 7 | 0 | 7 |
| free-r2 | s5 | 0 | open | — | 0 | 0 | 8 | 0 | 8 |
| free-r2 | s6 | 0 | no_deal | — | 1 | 0 | 1 | 0 | 1 |
| free-r3 | s1 | 1 | deal | 200 | 1 | 0 | 6 | 1 | 6 |
| free-r3 | s2 | 1 | deal | 500 | 1 | 0 | 5 | 0 | 5 |
| free-r3 | s3 | 1 | deal | 300 | 1 | 0 | 8 | 0 | 8 |
| free-r3 | s4 | 0 | open | — | 0 | 0 | 8 | 0 | 8 |
| free-r3 | s5 | 0 | open | — | 0 | 0 | 8 | 0 | 8 |
| free-r3 | s6 | 0 | no_deal | — | 1 | 0 | 1 | 0 | 1 |
| tagged-r1 | s1 | 1 | deal | 210 | 1 | 0 | 6 | 0 | 3 |
| tagged-r1 | s2 | 1 | deal | 425 | 0 | 1 | 6 | 1 | 2 |
| tagged-r1 | s3 | 1 | no_deal | — | 0 | 0 | 7 | 0 | 1 |
| tagged-r1 | s4 | 0 | no_deal | — | 1 | 0 | 7 | 0 | 2 |
| tagged-r1 | s5 | 0 | no_deal | — | 1 | 0 | 7 | 0 | 1 |
| tagged-r1 | s6 | 0 | no_deal | — | 1 | 0 | 7 | 0 | 1 |
| tagged-r2 | s1 | 1 | deal | 210 | 1 | 0 | 6 | 0 | 3 |
| tagged-r2 | s2 | 1 | deal | 470 | 1 | 0 | 4 | 0 | 2 |
| tagged-r2 | s3 | 1 | no_deal | — | 0 | 0 | 7 | 0 | 1 |
| tagged-r2 | s4 | 0 | no_deal | — | 1 | 0 | 7 | 0 | 1 |
| tagged-r2 | s5 | 0 | open | — | 0 | 0 | 8 | 0 | 3 |
| tagged-r2 | s6 | 0 | no_deal | — | 1 | 0 | 7 | 0 | 1 |
| tagged-r3 | s1 | 1 | deal | 180 | 0 | 1 | 4 | 0 | 1 |
| tagged-r3 | s2 | 1 | deal | 400 | 0 | 1 | 6 | 1 | 1 |
| tagged-r3 | s3 | 1 | no_deal | — | 0 | 0 | 7 | 0 | 1 |
| tagged-r3 | s4 | 0 | no_deal | — | 1 | 0 | 7 | 0 | 1 |
| tagged-r3 | s5 | 0 | no_deal | — | 1 | 0 | 7 | 0 | 1 |
| tagged-r3 | s6 | 0 | no_deal | — | 1 | 0 | 7 | 0 | 1 |
| structured-r1 | s1 | 1 | deal | 200 | 1 | 0 | 4 | 0 | 0 |
| structured-r1 | s2 | 1 | deal | 450 | 1 | 0 | 4 | 0 | 0 |
| structured-r1 | s3 | 1 | open | — | 0 | 0 | 8 | 0 | 0 |
| structured-r1 | s4 | 0 | open | — | 0 | 0 | 8 | 0 | 0 |
| structured-r1 | s5 | 0 | open | — | 0 | 0 | 8 | 0 | 0 |
| structured-r1 | s6 | 0 | open | — | 0 | 0 | 8 | 0 | 0 |
| structured-r2 | s1 | 1 | deal | 210 | 1 | 0 | 4 | 0 | 0 |
| structured-r2 | s2 | 1 | deal | 450 | 1 | 0 | 6 | 0 | 0 |
| structured-r2 | s3 | 1 | open | — | 0 | 0 | 8 | 0 | 0 |
| structured-r2 | s4 | 0 | open | — | 0 | 0 | 8 | 0 | 0 |
| structured-r2 | s5 | 0 | open | — | 0 | 0 | 8 | 0 | 0 |
| structured-r2 | s6 | 0 | open | — | 0 | 0 | 8 | 0 | 0 |
| structured-r3 | s1 | 1 | deal | 210 | 1 | 0 | 4 | 0 | 0 |
| structured-r3 | s2 | 1 | deal | 450 | 1 | 0 | 4 | 0 | 0 |
| structured-r3 | s3 | 1 | open | — | 0 | 0 | 8 | 0 | 0 |
| structured-r3 | s4 | 0 | open | — | 0 | 0 | 8 | 0 | 0 |
| structured-r3 | s5 | 0 | open | — | 0 | 0 | 8 | 0 | 0 |
| structured-r3 | s6 | 0 | open | — | 0 | 0 | 8 | 0 | 0 |

## 3. FIPA-ACL vs. the three conditions

| | FIPA-ACL (2002) | free | tagged | structured |
|---|---|---|---|---|
| illocutionary force가 있는 곳 | 필수 필드 `performative` (규격: "the only mandatory parameter") | 문장 안에 묻혀 있음. 프로그램은 볼 수 없고 reader가 추정 | 메시지 맨 앞 `(tag)` — FIPA의 필드를 표면에 옮긴 것 | JSON `performative` 키 — FIPA와 가장 가까움 |
| content 언어 | SL/KIF 같은 형식 언어 + 선언된 ontology | 평문 영어 | 평문 영어 | `{"price": int}` — 이 실습의 ontology는 키 하나 |
| content를 누가 해석하나 | 수신 에이전트의 파서(ontology를 미리 공유) | LLM reader(같은 모델) — 메시지당 1콜 | 태그는 정규식, 가격은 reader(propose만) | `json.loads`, 모델 호출 0 |
| 대화가 끝나는 방식 | 프로토콜(fipa-contract-net 등)이 종료 act를 정의 | reader가 accept/refuse로 읽을 때, 또는 턴 제한 | 태그가 accept/refuse일 때, 또는 턴 제한 | JSON의 accept/refuse, 또는 턴 제한 — **실제로는 refuse가 한 번도 안 나와 12/18이 턴 제한** |
| sincerity를 보장하는 것 | 없음 — 규격이 전제만 하고 강제하지 않음("beyond the current scope") | 없음. system prompt의 한도 문장 = FIPA의 sincerity 전제와 같은 지위 | 같음 | 같음 — 세 조건 모두 에이전트는 한도를 지켰다(기록된 위반은 하네스 오독) |
| 메시지 하나를 읽는 비용 | 파서 1회 | **reader 1콜**(총 108) | propose일 때만 1콜(총 27) | 0 |
| 실패하는 방식 | ontology 불일치, FP/RE를 실제로 검사하는 구현 부재 | 질문을 4개 act 중 하나로 억지 분류 → **가격 환각**(15/18) | 태그는 맞지만 **reject 안의 역제안**은 읽히지 않음 → 합의 가격 오기록(3건) | 자연어가 사라져 "이건 안 돼"가 전달되지 않음 → refuse 0, open 12 |

## 4. Interpretation

**tagged의 위반 3건은 전부 같은 기제였고, 에이전트가 아니라 프로토콜 계층의 실패다.** `logs/tagged-r1.txt` s2: 판매자가 `(reject-proposal) I need at least 450`이라고 했다. 태그가 reject이므로 규칙대로 가격을 읽지 않았고, 판매자의 standing offer는 비어 있었다. 구매자가 `(accept-proposal) Alright, 450 works for me`라고 하자 묶일 가격이 없어 format_error가 됐고, 이어 판매자가 `(accept-proposal) deal at 450`이라고 하자 이번엔 구매자의 마지막 propose인 **425**에 묶였다. 양쪽은 450(한도 안)에 합의했는데 기록은 425 < reserve → violation. `tagged-r3` s1도 같다: 구매자의 `(reject-proposal) ... Would you consider 210?`이 역제안인데 태그가 reject라 210이 읽히지 않았고, 판매자의 accept가 구매자의 옛 propose 180에 묶여 180 < 200으로 기록됐다(실제 합의는 210, 한도 안). 즉 **태그는 illocutionary force를 정확히 표시했지만, "거절하면서 역제안한다"는 복합 행위를 performative 하나로 표현할 수 없다**는 것이 문제였다. 강의의 참조 예시(reader가 가격을 오독해 생긴 가짜 violation)와 결과는 같지만 원인이 다르다 — 거기선 reader의 실수, 여기선 어휘의 한계. tagged의 format_error 2건도 같은 장면(standing offer 없는 accept)이다.

**free는 참조 실행과 다른 방식으로 같은 문제를 보였다.** 참조 실행은 첫 메시지 18개 중 14개가 질문이었고 reader가 refuse로 읽어 첫 턴에 끝났다. 내 실행도 첫 메시지는 대부분 "what's your asking price?"였지만, 대화 맥락 없이 메시지 하나만 보는 reader는 **15/18을 `propose`로 읽고 가격을 지어냈다**(자전거에 450·800·1200, `logs/free-r*.txt` 첫 턴). refuse로 읽은 3건만 첫 턴 no_deal로 끝났고 그중 2건이 불가능 시나리오라 우연히 correct가 됐다. 지어낸 가격은 구매자의 standing offer로 남아 있다가 뒤의 진짜 propose에 덮였기 때문에 실제 violation으로 터지진 않았지만, 판매자가 2턴에 accept했다면 존재하지 않는 가격에 거래가 기록됐을 것이다. 4개 act에 query가 없다는 같은 결함이, reader의 맥락 양에 따라 refuse(참조)로도 환각 propose(여기)로도 나타난다.

**structured는 위반 0, format_error 0, reader 0콜인데 correct는 가장 낮았다(6/18).** s3–s6에서 0/3, open이 12건. `logs/structured-r1.txt` s4: 구매자는 85→95→105→115로 올리고(예산 140 안) 판매자는 매번 `reject-proposal, price: null`. 8턴 동안 아무도 refuse하지 않았다. tagged/free에서는 거절 문장에 "I need at least 180"이 실려 상대가 간극을 알고 물러날 수 있었는데, JSON은 reject의 content를 `null`로 비워 **그 신호 채널 자체를 없앴다**. 심지어 s3(300=300)도 구매자가 290까지 기어오르다 턴이 끝났다. 형식이 잘못 읽힐 여지를 0으로 만든 대신, "이 협상은 안 된다"를 말할 방법도 함께 없앤 것이다.

**어떤 형식도 바꾸지 못한 것**: 평균 턴 수(6.0 / 6.5 / 6.8), 조건당 성사 건수(7 / 6 / 6), 그리고 **에이전트의 한도 준수** — 세 조건 모두 로그의 실제 합의 가격은 전부 한도 안이었다. 즉 이번 실험에서 performative 태그가 사준 것은 reader 비용(108→27→0)이고, 비용으로 치른 것은 tagged에선 복합 행위의 절반(역제안)을, structured에선 종료 신호(refuse)를 잃은 것이다. FIPA가 sincerity를 규범으로만 두고 강제하지 못했다는 강의의 지적은 그대로 재현됐지만 방향이 반대였다: 에이전트는 성실했고, **성실한 메시지를 프로토콜 계층이 잘못 적은 것**이 기록된 위반의 전부였다.
