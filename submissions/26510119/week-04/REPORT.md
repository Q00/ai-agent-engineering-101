# Week 04 Report

## 1. Setup

- provider: OpenRouter
- model: nvidia/nemotron-3-super-120b-a12b:free
- temperature: 0.7
- turn limit: 8 (open if exceeded)
- daily API limit: 50 calls (free tier), experiments spread over 5 days

### Format paragraphs

**free**: Write your message as one or two plain English sentences.

**tagged**: Start your message with exactly one performative tag in parentheses — one of (propose), (accept-proposal), (reject-proposal), (refuse) — then write one plain English sentence. Example: (propose) I offer $50 for the item.

**structured**: Reply with exactly one JSON object and nothing else: {"performative": "propose"|"accept-proposal"|"reject-proposal"|"refuse", "content": {"price": <integer or null>}}. Set price to an integer for propose, null for other acts.

### Reader prompt

"You are an observer reading a price negotiation between a buyer and a seller. Label the LAST message only. Reply with exactly one JSON object and nothing else: {"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "price": <integer or null>}. If the last message proposes a specific price, set price to that integer. Otherwise set price to null."

### How to run

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<your key>
export AGENT_MODEL=nvidia/nemotron-3-super-120b-a12b:free
python run_experiment.py --condition free --run 1
python run_experiment.py --condition tagged --run 1
python run_experiment.py --condition structured --run 1
```

## 2. Results

### Summary by condition

**Note**: structured-02 crashed on all 4 scenarios (missing API key in the shell, my mistake) and structured-01 scenarios 3–4 hit the daily limit; those rows stay in results.csv with the error in `note`.

| condition | episodes | correct | violations | mean turns | format errors | reader calls |
|---|---|---|---|---|---|---|
| free | 15 (valid) | 14 | 1 | 4.7 | 1 | 6.1 avg |
| tagged | 16 (valid) | 8 | 0 | 6.5 | 0 | 4.6 avg |
| structured | 10 (valid) | 6 | 0 | 5.8 | 1 | 0 |

### Per-episode results (from results.csv)

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| free-02 | free | 1 | 1 | deal | 90 | 1 | 0 | 4 | 0 | 4 | |
| free-02 | free | 2 | 1 | deal | 33 | 1 | 0 | 4 | 0 | 6 | |
| free-02 | free | 3 | 0 | no_deal | | 1 | 0 | 8 | 0 | 11 | |
| free-02 | free | 4 | 0 | no_deal | | 1 | 0 | 6 | 0 | 6 | |
| free-04 | free | 1 | 1 | deal | 100 | 1 | 0 | 4 | 0 | 4 | |
| free-04 | free | 2 | 1 | deal | 30 | 1 | 0 | 2 | 0 | 3 | |
| free-04 | free | 3 | 0 | no_deal | | 1 | 0 | 5 | 0 | 9 | |
| free-04 | free | 4 | 0 | no_deal | | 1 | 0 | 5 | 0 | 7 | |
| free-05 | free | 1 | 1 | deal | 90 | 1 | 0 | 5 | 0 | 8 | |
| free-05 | free | 2 | 1 | deal | 35 | 1 | 0 | 6 | 0 | 6 | |
| free-05 | free | 3 | 0 | no_deal | | 1 | 0 | 6 | 1 | 8 | |
| free-06 | free | 1 | 1 | deal | 90 | 1 | 0 | 2 | 0 | 3 | |
| free-06 | free | 2 | 1 | deal | 33 | 1 | 0 | 4 | 0 | 4 | |
| free-06 | free | 3 | 0 | deal | 65 | 0 | 1 | 6 | 0 | 6 | reader misread farewell as accept |
| free-06 | free | 4 | 0 | no_deal | | 1 | 0 | 6 | 0 | 6 | |
| tagged-01 | tagged | 1 | 1 | open | | 0 | 0 | 8 | 0 | 6 | |
| tagged-02 | tagged | 1 | 1 | no_deal | | 0 | 0 | 8 | 0 | 4 | |
| tagged-02 | tagged | 2 | 1 | deal | 35 | 1 | 0 | 4 | 0 | 2 | |
| tagged-02 | tagged | 3 | 0 | open | | 0 | 0 | 8 | 0 | 5 | |
| tagged-03 | tagged | 1 | 1 | open | | 0 | 0 | 8 | 0 | 7 | |
| tagged-04 | tagged | 1 | 1 | deal | 110 | 1 | 0 | 6 | 0 | 5 | |
| tagged-04 | tagged | 3 | 0 | open | | 0 | 0 | 8 | 0 | 6 | |
| tagged-04 | tagged | 4 | 0 | open | | 0 | 0 | 8 | 0 | 4 | |
| tagged-05 | tagged | 1 | 1 | deal | 105 | 1 | 0 | 7 | 0 | 12 | |
| tagged-05 | tagged | 2 | 1 | deal | 30 | 1 | 0 | 4 | 0 | 3 | |
| tagged-05 | tagged | 3 | 0 | no_deal | | 1 | 0 | 5 | 0 | 2 | |
| tagged-06 | tagged | 2 | 1 | no_deal | | 0 | 0 | 6 | 0 | 3 | |
| tagged-06 | tagged | 4 | 0 | no_deal | | 1 | 0 | 7 | 0 | 7 | |
| tagged-07 | tagged | 2 | 1 | deal | 35 | 1 | 0 | 4 | 0 | 3 | |
| tagged-07 | tagged | 3 | 0 | no_deal | | 1 | 0 | 7 | 0 | 6 | |
| tagged-07 | tagged | 4 | 0 | no_deal | | 1 | 0 | 7 | 0 | 7 | |
| structured-01 | structured | 1 | 1 | deal | 90 | 1 | 0 | 4 | 0 | 0 | |
| structured-01 | structured | 2 | 1 | deal | 30 | 1 | 0 | 4 | 0 | 0 | |
| structured-03 | structured | 1 | 1 | deal | 90 | 1 | 0 | 4 | 0 | 0 | |
| structured-03 | structured | 2 | 1 | deal | 30 | 1 | 0 | 4 | 0 | 0 | |
| structured-03 | structured | 3 | 0 | open | | 0 | 0 | 8 | 0 | 0 | |
| structured-03 | structured | 4 | 0 | open | | 0 | 0 | 8 | 0 | 0 | |
| structured-04 | structured | 1 | 1 | deal | 90 | 1 | 0 | 4 | 0 | 0 | |
| structured-04 | structured | 2 | 1 | deal | 30 | 1 | 0 | 6 | 1 | 0 | |
| structured-04 | structured | 3 | 0 | open | | 0 | 0 | 8 | 0 | 0 | |
| structured-04 | structured | 4 | 0 | open | | 0 | 0 | 8 | 0 | 0 | |

Crashed episodes (code bug or rate limit) are recorded in results.csv but omitted from the table above.

## 3. FIPA-ACL comparison

| | FIPA-ACL | free | tagged | structured |
|---|---|---|---|---|
| illocutionary force lives in | mandatory performative field | inferred from context by reader LLM | parenthesized tag parsed by regex | JSON performative field |
| content language | formal, declared ontology | plain English | plain English after tag | JSON with price field |
| who interprets content | formal parser | reader LLM (every message) | reader LLM (propose only) | JSON parser, no LLM |
| how conversation ends | protocol-defined | reader labels accept/refuse | regex reads accept/refuse tag | parser reads accept/refuse |
| what guarantees sincerity | protocol + social commitment | nothing; agent can contradict itself | tag can mismatch sentence content | JSON field is unambiguous |
| cost to read one message | zero (formal parse) | 1 LLM call per message | 0 or 1 LLM call (propose only) | 0 (JSON parse) |
| failure modes | mismatch with ontology | reader misreads price or act; questions mapped to refuse | tag present but counter-offer in sentence ignored; model outputs chain-of-thought | JSON with trailing text; price null when proposal is in text |

## 4. Interpretation

free 조건은 correct 14/15로 가장 높았지만, 이건 에이전트가 잘해서가 아니라 reader가 상황을 대충 맞췄기 때문이다. free-06 시나리오 3에서는 seller가 "I understand completely—thank you for your time"라고 작별 인사를 했는데 reader가 이걸 accept-proposal로 읽어서 deal이 되었고, 가격은 buyer의 마지막 propose인 65가 되었다. reserve 90 아래라 violation이다. reader가 두 에이전트의 실제 합의와 관계없이 거래를 만들어낸 셈이다. tagged는 correct 8/16으로 낮았는데, 이유는 open이 많기 때문이다 (6건). tagged-02 시나리오 1에서 buyer가 "(reject-proposal) $95 is still too high"라고 거절하면서 자기 가격을 계속 제시했지만, 태그가 reject이니 propose로 기록되지 않았고, 상대의 last_price가 갱신되지 않은 채 8턴이 지나 open으로 끝났다. tagged-03에서는 모델이 chain-of-thought를 메시지에 통째로 출력하기도 했다. structured는 유효 10건 중 correct 6/10이다. 거래 가능 시나리오(1, 2)는 6/6 모두 정확한 가격에 deal이었고 reader_calls는 전부 0이다. JSON 파싱만으로 행위와 가격을 읽으니 free-06 같은 오독은 생길 수 없다. 대신 파서는 형식이 조금만 어긋나도 메시지를 버린다: structured-04 시나리오 2에서 buyer가 `{"performative": "propose", "content": {"price": 25}`처럼 닫는 괄호 하나를 빠뜨리자 ok=False로 그 제안이 통째로 무시됐다 (그 다음 30에서 deal). 틀린 결과는 전부 거래 불가능 시나리오(3, 4)의 open 4건이다. structured-03 시나리오 3에서 buyer는 30→45→55→65로 올리고 seller는 네 번 모두 `{"performative": "reject-proposal", "content": {"price": null}}`만 보냈다. 형식 문단이 "propose 외에는 price를 null로" 요구하므로 거절에 반대 제안을 실을 자리가 없고, 누구도 refuse를 고르지 않아 8턴이 지났다. tagged의 open과 같은 구조적 원인(거절 행위에 가격이 없음)이 structured에서는 더 깨끗하게 드러난다. 즉 형식이 엄격해질수록 읽기 오류는 사라지지만, 프로토콜이 표현하지 못하는 행동(반대 제안을 담은 거절)은 대화에서 아예 사라진다.
