# Week 04 — Speech acts in practice

## 1. 설정

이 실험은 로컬 Ollama의 OpenAI 호환 API를 사용했다.

| 항목 | 값 |
|---|---|
| Provider | `ollama-local` |
| Base URL | `http://localhost:11434/v1` |
| Model | `qwen2.5:7b-instruct` |
| Temperature | `0` |
| Maximum messages per episode | `8` |
| Scenarios | 4 (`S1`–`S4`) |
| Conditions | `free`, `tagged`, `structured` |
| Repetitions | scenario와 condition 조합마다 3회 |
| Total | 4 scenarios × 3 conditions × 3 runs = 36 episodes |

구매자가 먼저 말하고 구매자와 판매자가 번갈아 응답한다. 두 역할의 기본 프롬프트와 협상 규칙은 같게 두고, 아래 형식 지시문만 condition에 따라 바꿨다.

- `free`: `Reply in one plain English message.` 자연어 메시지를 별도의 LLM reader가 읽고 performative와 가격을 JSON으로 분류한다.
- `tagged`: `Begin the message with exactly one of (propose), (accept-proposal), (reject-proposal), or (refuse), then write one plain English sentence.` 코드가 문장 맨 앞의 괄호 태그를 regex로 읽는다. `propose`의 가격만 LLM reader가 읽는다.
- `structured`: `Reply with exactly one JSON object and nothing else: {"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "content": {"price": <whole number or null>}}.` 코드의 JSON parser만 사용하며 LLM reader는 호출하지 않는다.

free와 tagged에서 사용하는 reader 프롬프트는 다음과 같다.

> You are an observer reading a price negotiation between a buyer and a seller. Label the LAST message only. A propose offers a new price; accept-proposal agrees to the other party's previous proposed price; reject-proposal declines but keeps negotiating; refuse leaves the negotiation. For propose, price is the new price offered by the last speaker, not a quoted earlier price or a private limit. Reply with exactly one JSON object and nothing else: {"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "price": <whole number or null>}.

공식 실행 명령은 아래와 같다. 로컬 Ollama의 OpenAI 호환 endpoint는 API key 항목이 비어 있지 않기를 요구하므로 실제 비밀키 대신 non-empty dummy value를 사용한다. 그 값은 보고서에 기록하지 않는다.

```bash
AGENT_PROVIDER='ollama-local' \
OPENAI_BASE_URL='http://localhost:11434/v1' \
OPENAI_API_KEY='<non-empty dummy value>' \
AGENT_MODEL='qwen2.5:7b-instruct' \
AGENT_TEMPERATURE='0' \
python run_experiment.py
```

## 2. 결과

조건별 집계는 `results.csv`의 36개 행을 다시 계산한 값이다. `correct`와 `violation`은 합계이며, mean turns는 condition별 12개 episode의 평균이다.

| condition | episodes | correct | violation | mean turns | format_errors | reader_calls | deal | no_deal | open |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| free | 12 | 6 | 0 | 5.5 | 0 | 66 | 6 | 0 | 6 |
| tagged | 12 | 0 | 0 | 8.0 | 96 | 0 | 0 | 0 | 12 |
| structured | 12 | 4 | 0 | 6.0 | 3 | 0 | 4 | 2 | 6 |

공식 실행 중 crash는 없었다. 아래는 `results.csv`의 전체 episode다. `—`는 CSV에서 빈 값이다.

| run | condition | scenario | deal possible | outcome | price | correct | violation | turns | format errors | reader calls | note |
|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---|
| 1 | free | S1 | 1 | deal | 45 | 1 | 0 | 2 | 0 | 2 | — |
| 1 | free | S2 | 1 | deal | 100 | 1 | 0 | 4 | 0 | 4 | turn 3: accept-proposal without opponent proposal |
| 1 | free | S3 | 0 | open | — | 0 | 0 | 8 | 0 | 8 | — |
| 1 | free | S4 | 0 | open | — | 0 | 0 | 8 | 0 | 8 | — |
| 2 | free | S1 | 1 | deal | 45 | 1 | 0 | 2 | 0 | 2 | — |
| 2 | free | S2 | 1 | deal | 100 | 1 | 0 | 4 | 0 | 4 | turn 3: accept-proposal without opponent proposal |
| 2 | free | S3 | 0 | open | — | 0 | 0 | 8 | 0 | 8 | — |
| 2 | free | S4 | 0 | open | — | 0 | 0 | 8 | 0 | 8 | — |
| 3 | free | S1 | 1 | deal | 45 | 1 | 0 | 2 | 0 | 2 | — |
| 3 | free | S2 | 1 | deal | 100 | 1 | 0 | 4 | 0 | 4 | turn 3: accept-proposal without opponent proposal |
| 3 | free | S3 | 0 | open | — | 0 | 0 | 8 | 0 | 8 | — |
| 3 | free | S4 | 0 | open | — | 0 | 0 | 8 | 0 | 8 | — |
| 1 | tagged | S1 | 1 | open | — | 0 | 0 | 8 | 8 | 0 | — |
| 1 | tagged | S2 | 1 | open | — | 0 | 0 | 8 | 8 | 0 | — |
| 1 | tagged | S3 | 0 | open | — | 0 | 0 | 8 | 8 | 0 | — |
| 1 | tagged | S4 | 0 | open | — | 0 | 0 | 8 | 8 | 0 | — |
| 2 | tagged | S1 | 1 | open | — | 0 | 0 | 8 | 8 | 0 | — |
| 2 | tagged | S2 | 1 | open | — | 0 | 0 | 8 | 8 | 0 | — |
| 2 | tagged | S3 | 0 | open | — | 0 | 0 | 8 | 8 | 0 | — |
| 2 | tagged | S4 | 0 | open | — | 0 | 0 | 8 | 8 | 0 | — |
| 3 | tagged | S1 | 1 | open | — | 0 | 0 | 8 | 8 | 0 | — |
| 3 | tagged | S2 | 1 | open | — | 0 | 0 | 8 | 8 | 0 | — |
| 3 | tagged | S3 | 0 | open | — | 0 | 0 | 8 | 8 | 0 | — |
| 3 | tagged | S4 | 0 | open | — | 0 | 0 | 8 | 8 | 0 | — |
| 1 | structured | S1 | 1 | deal | 45 | 1 | 0 | 4 | 1 | 0 | turn 3: accept-proposal without opponent proposal |
| 1 | structured | S2 | 1 | deal | 95 | 1 | 0 | 4 | 0 | 0 | — |
| 1 | structured | S3 | 0 | open | — | 0 | 0 | 8 | 0 | 0 | — |
| 1 | structured | S4 | 0 | open | — | 0 | 0 | 8 | 0 | 0 | — |
| 2 | structured | S1 | 1 | no_deal | — | 0 | 0 | 4 | 1 | 0 | turn 3: accept-proposal without opponent proposal |
| 2 | structured | S2 | 1 | deal | 95 | 1 | 0 | 4 | 0 | 0 | — |
| 2 | structured | S3 | 0 | open | — | 0 | 0 | 8 | 0 | 0 | — |
| 2 | structured | S4 | 0 | open | — | 0 | 0 | 8 | 0 | 0 | — |
| 3 | structured | S1 | 1 | no_deal | — | 0 | 0 | 4 | 1 | 0 | turn 3: accept-proposal without opponent proposal |
| 3 | structured | S2 | 1 | deal | 95 | 1 | 0 | 4 | 0 | 0 | — |
| 3 | structured | S3 | 0 | open | — | 0 | 0 | 8 | 0 | 0 | — |
| 3 | structured | S4 | 0 | open | — | 0 | 0 | 8 | 0 | 0 | — |

## 3. FIPA-ACL과 세 조건 비교

| 비교 항목 | FIPA-ACL | free | tagged | structured |
|---|---|---|---|---|
| Illocutionary force의 위치 | 메시지의 필수 `performative` 필드 | 자연어 문맥 안에 암묵적으로 존재 | 자연어 앞의 괄호 태그 | JSON의 `performative` 필드 |
| Content 표현 방식 | 선언된 content language와 ontology | 제한 없는 영어 문장 | 태그 뒤의 영어 문장 | `content.price`를 가진 JSON 객체 |
| Content 해석 주체 | 해당 content language를 이해하는 agent 또는 parser | LLM reader | 태그는 regex, 제안 가격은 LLM reader | 결정적 JSON parser |
| 대화 종료 방식 | protocol과 performative의 의미에 따름 | reader가 읽은 `accept-proposal` 또는 `refuse`, 아니면 8-message limit | 유효한 태그의 `accept-proposal` 또는 `refuse`, 아니면 limit | 유효한 JSON의 `accept-proposal` 또는 `refuse`, 아니면 limit |
| Sincerity 보장 | ACL 자체는 진실성이나 private constraint 준수를 강제하지 않음 | system prompt에 의존 | system prompt에 의존 | system prompt에 의존 |
| 메시지 하나를 읽는 비용 | 구현에 따라 달라짐 | 매 메시지마다 reader model call 1회 | 태그는 무료이며 `propose` 가격에만 reader call 1회 | local parser만 사용, model call 0회 |
| 주요 failure mode | 잘못된 performative, ontology 불일치, protocol 위반 | reader의 의도·가격 오분류와 추가 model-call 비용 | 괄호 등 정확한 태그 형식을 어기면 메시지 전체가 parse되지 않음 | malformed JSON, schema 불일치, 대화 상태와 맞지 않는 act |

## 4. 해석

이번 결과에서 가장 먼저 눈에 들어온 차이는 correct였다. free는 12개 중 6개, structured는 4개였고 tagged는 하나도 맞지 않았다. free가 6개를 맞힌 것은 거래 가능한 S1과 S2를 세 번씩 deal로 끝냈기 때문이다. 다만 reader가 항상 뜻을 제대로 읽은 것은 아니다. `free-01.log`에서 판매자가 `propose-a-price 95`라고 말했는데 reader는 이를 `reject-proposal`로 분류했다(11–12행). 그 다음 구매자의 `accept-proposal`은 받아들일 상대 제안이 없는 act가 되었고, 결과에도 `accept-proposal without opponent proposal`이 남았다(13–17행). 그래도 판매자가 뒤이어 accept하면서 이전의 구매자 제안 100으로 deal이 성립했다. 즉 free의 높은 correct 수치만 보면 reader의 중간 실수를 놓치기 쉽다. 이 조건은 format error가 0인 대신 모든 메시지를 reader가 읽어서 66번의 추가 호출이 들었다. tagged는 원래 태그만 regex로 읽으므로 force를 바로 확인할 수 있고, 제안이 아닌 메시지는 reader를 부르지 않아도 된다는 장점이 있다. 하지만 이번 공식 실행에서는 그 장점이 실제로 작동한 성공 사례가 없었다. 모델이 요구된 `(propose)` 대신 `propose`라고 썼고, `tagged-01.log`의 첫 메시지와 바로 다음 protocol 결과가 그 예다(3–4행). 사람에게는 제안이라는 뜻이 분명하지만 정확한 괄호가 없어서 parser는 메시지를 거부했다. 같은 일이 12개 episode의 96개 메시지에서 전부 반복되어 format error 96, open 12, correct 0이 되었다. tagged의 reader calls가 0인 것도 정상적인 태그 처리가 읽기 비용을 줄인 결과가 아니다. 모든 메시지가 regex 단계에서 먼저 실패해 `propose` 가격을 읽는 reader까지 한 번도 도달하지 못한 결과다. 명시적인 performative는 모델과 parser가 같은 표기법을 지킬 때 도움이 되지만, 이번에는 작은 문법 차이 때문에 대화를 전혀 읽지 못하는 비용만 드러났다. structured는 reader call을 완전히 없앴고 정상 JSON은 가격까지 바로 읽었다. 예를 들어 `structured-01.log`의 S2는 90 제안, 거절, 95 재제안, 수락을 모두 parser가 읽어 4턴의 올바른 deal로 끝냈다(13–21행). 반면 S1에서 판매자가 `"accept-proposal" | "reject-proposal"`처럼 두 값을 한 필드에 넣어 유효하지 않은 JSON을 만들었다(5–6행). 같은 오류가 세 run에서 한 번씩 나와 format error가 3이 되었다. run 1은 이후 deal로 회복했지만, run 2와 3에서는 구매자가 상대의 유효한 제안 없이 accept한 뒤 판매자가 refuse해서 거래 가능한 상황인데도 no_deal이 되었다. `structured-02.log` 3–11행에서 이 흐름을 그대로 볼 수 있다. 구조화 형식은 읽는 비용과 해석의 모호함은 줄였지만, 모델이 올바른 JSON을 생성하는 문제나 현재 대화 상태에 맞는 act를 고르는 문제까지 해결하지는 못했다. 세 조건 모두 violation은 0이었다. 따라서 reserve 아래 판매나 budget 위 구매 사례는 이번 공식 결과에는 없다. 그렇다고 private constraint가 형식 덕분에 보장됐다고 말할 수는 없다. 세 형식 모두 그 제한은 system prompt에만 적혀 있고 parser는 agent의 sincerity를 검사하지 않는다. 또 거래 불가능한 S3와 S4는 free와 structured에서 reject와 새 proposal을 반복하다 8턴 limit에 도달해 모두 open으로 끝났다. 가령 `structured-01.log`의 S3는 구매자가 35, 30, 25, 20을 차례로 제안하고 판매자가 계속 reject한 뒤 open이 되었다(23–39행). 두 당사자의 가격 범위가 겹치지 않는다는 사실을 메시지 형식만으로 알아내거나 반드시 refuse로 끝내게 하지는 못한 것이다.
