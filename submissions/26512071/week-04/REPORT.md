# Week 04 — 메시지 형식에 따른 가격 협상 비교

## 1. 실험 설정

구매자와 판매자는 동일한 네 시나리오에서 번갈아 협상했다. 구매자는 먼저
말하고, 유효한 `accept-proposal`은 직전 상대 제안 가격으로 거래를 끝내며,
`refuse`는 거래 없이 종료한다. 그 외에는 최대 8개 메시지까지 진행한다.
세 조건은 역할 규칙, 시나리오, 제공자, 모델, 온도와 턴 제한이 같고 출력 형식
문단과 메시지를 읽는 코드만 다르다.

| 항목 | 값 |
|---|---|
| 제공자 / 모델 | OpenAI API / `gpt-5.6-luna` |
| 온도 / reasoning | 0.2 / `none` |
| 최대 출력 / 턴 | 240 tokens / 8 messages |
| 시나리오 | 4개(거래 가능 2, 불가능 2) |
| 반복 | 조건별 3회, 총 36개 예정 에피소드 |

세 형식 문단은 다음과 같다.

- **free:** “Write one short, natural English message. Do not add a
  performative tag, JSON, metadata, or commentary. Your wording must clearly
  express exactly one allowed act. Include one integer price when you propose.”
- **tagged:** “Write exactly one performative tag in parentheses, then one
  short natural English message. The tag must be one of (propose),
  (accept-proposal), (reject-proposal), or (refuse). Include one integer price
  in the English text when you propose.”
- **structured:** “Write only one JSON object with exactly this shape:
  `{"performative":"propose|accept-proposal|reject-proposal|refuse","content":{"price":integer_or_null}}`.
  Use an integer only for propose and null for every other act. Do not use a
  Markdown fence.”

free reader 프롬프트는 메시지를 네 행위 중 하나로 분류하고 `propose`일 때만
정수 가격을 추출하여 정확히 `performative`, `price` 두 키의 JSON을 반환하도록
했다. tagged의 가격 reader는 `propose` 본문에서 단일 정수 가격을 추출해
`{"price": ...}`만 반환하도록 했다. free는 모든 메시지마다 reader를 호출하고,
tagged는 `propose`에서만 호출하며, structured는 Python JSON parser만 쓴다.

재현 명령은 다음과 같다. API 키는 환경변수로만 전달한다.

```bash
python -m pip install -r requirements.txt
export OPENAI_API_KEY=<key>
python negotiation.py --repeats 3
python ../../../scripts/check_week04.py .
```

## 2. 결과

평균 턴은 정상 종료된 에피소드만 대상으로 계산했다. structured의 첫 camera
에피소드는 잘못된 API 키로 401이 발생해 명세대로 빈 결과와 오류 note를
보존했고, 키 문자열은 `[REDACTED_API_KEY]`로 제거했다.

| 조건 | correct | violations | 평균 turns | format errors | reader calls | 실행 실패 |
|---|---:|---:|---:|---:|---:|---:|
| free | 8/12 (66.7%) | 0 | 4.92 | 0 | 59 | 0 |
| tagged | 9/12 (75.0%) | 0 | 4.75 | 0 | 20 | 0 |
| structured | 5/11 (45.5%) | 0 | 5.55 | 0 | 0 | 1 |

### 에피소드별 결과

| run | condition | scenario | possible | outcome | price | correct | violation | turns | errors | reader | note |
|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---|
| structured-01 | structured | camera | 1 | — | — | — | — | — | — | — | 401 invalid API key; redacted |
| structured-01 | structured | lamp | 0 | open | — | 0 | 0 | 8 | 0 | 0 | |
| structured-01 | structured | headphones | 1 | deal | 110 | 1 | 0 | 3 | 0 | 0 | |
| structured-01 | structured | chair | 0 | open | — | 0 | 0 | 8 | 0 | 0 | |
| free-01 | free | camera | 1 | deal | 80 | 1 | 0 | 2 | 0 | 2 | |
| free-01 | free | lamp | 0 | no_deal | — | 1 | 0 | 7 | 0 | 7 | |
| free-01 | free | headphones | 1 | deal | 90 | 1 | 0 | 5 | 0 | 5 | |
| free-01 | free | chair | 0 | open | — | 0 | 0 | 8 | 0 | 8 | |
| free-02 | free | camera | 1 | deal | 80 | 1 | 0 | 2 | 0 | 2 | |
| free-02 | free | lamp | 0 | open | — | 0 | 0 | 8 | 0 | 8 | |
| free-02 | free | headphones | 1 | deal | 95 | 1 | 0 | 4 | 0 | 4 | |
| free-02 | free | chair | 0 | no_deal | — | 1 | 0 | 3 | 0 | 3 | |
| free-03 | free | camera | 1 | deal | 70 | 1 | 0 | 2 | 0 | 2 | |
| free-03 | free | lamp | 0 | open | — | 0 | 0 | 8 | 0 | 8 | |
| free-03 | free | headphones | 1 | deal | 90 | 1 | 0 | 2 | 0 | 2 | |
| free-03 | free | chair | 0 | open | — | 0 | 0 | 8 | 0 | 8 | |
| tagged-01 | tagged | camera | 1 | deal | 80 | 1 | 0 | 2 | 0 | 1 | |
| tagged-01 | tagged | lamp | 0 | no_deal | — | 1 | 0 | 7 | 0 | 1 | |
| tagged-01 | tagged | headphones | 1 | deal | 90 | 1 | 0 | 2 | 0 | 1 | |
| tagged-01 | tagged | chair | 0 | no_deal | — | 1 | 0 | 7 | 0 | 3 | |
| tagged-02 | tagged | camera | 1 | deal | 70 | 1 | 0 | 2 | 0 | 1 | |
| tagged-02 | tagged | lamp | 0 | open | — | 0 | 0 | 8 | 0 | 1 | |
| tagged-02 | tagged | headphones | 1 | deal | 90 | 1 | 0 | 2 | 0 | 1 | |
| tagged-02 | tagged | chair | 0 | no_deal | — | 1 | 0 | 7 | 0 | 1 | |
| tagged-03 | tagged | camera | 1 | deal | 80 | 1 | 0 | 2 | 0 | 1 | |
| tagged-03 | tagged | lamp | 0 | open | — | 0 | 0 | 8 | 0 | 4 | |
| tagged-03 | tagged | headphones | 1 | deal | 90 | 1 | 0 | 2 | 0 | 1 | |
| tagged-03 | tagged | chair | 0 | open | — | 0 | 0 | 8 | 0 | 4 | |
| structured-02 | structured | camera | 1 | deal | 70 | 1 | 0 | 2 | 0 | 0 | |
| structured-02 | structured | lamp | 0 | open | — | 0 | 0 | 8 | 0 | 0 | |
| structured-02 | structured | headphones | 1 | deal | 100 | 1 | 0 | 3 | 0 | 0 | |
| structured-02 | structured | chair | 0 | open | — | 0 | 0 | 8 | 0 | 0 | |
| structured-03 | structured | camera | 1 | deal | 70 | 1 | 0 | 2 | 0 | 0 | |
| structured-03 | structured | lamp | 0 | open | — | 0 | 0 | 8 | 0 | 0 | |
| structured-03 | structured | headphones | 1 | deal | 110 | 1 | 0 | 3 | 0 | 0 | |
| structured-03 | structured | chair | 0 | open | — | 0 | 0 | 8 | 0 | 0 | |

## 3. FIPA-ACL과 세 조건 비교

| 비교 항목 | FIPA-ACL | free | tagged | structured |
|---|---|---|---|---|
| 발화수반력의 위치 | 필수 `performative` 필드 | 자연어 문맥에 암묵적 | 문두 괄호 태그 | JSON `performative` |
| 내용 언어 | 선언된 content language | 제한 없는 영어 | 태그 뒤 영어 | JSON `content.price` |
| content 해석자 | 선언 언어를 아는 agent/platform | LLM reader | regex + 제안 가격용 LLM | Python parser |
| 대화 종료 | 프로토콜의 종료 행위/상태 | reader가 분류한 accept/refuse | 태그의 accept/refuse | 필드의 accept/refuse |
| sincerity 보장 | 수행 가능성 조건은 있으나 진실 자체는 보장하지 않음 | 없음 | 태그가 진실임을 보장하지 않음 | JSON도 한도 준수를 보장하지 않음 |
| 메시지 읽기 비용 | 형식 파싱 비용 | 매 메시지 LLM 1회 | 태그는 regex, propose만 LLM 1회 | 로컬 JSON parsing만 사용 |
| 관찰된 실패 모드 | ontology/protocol 불일치 가능 | reader 비용 59회, open 4건 | 태그와 본문의 행위 불일치, open 3건 | 문법 오류는 없지만 open 6건, 실행 1건 실패 |

## 4. 해석

명시적 performative는 이번 실행에서 주로 **읽기 비용**을 바꿨다. free는 모든 메시지를 다시 분류해 59회의 reader 호출이 필요했지만, tagged는 제안 가격에만 20회, structured는 0회였다. 그럼에도 세 조건 모두 format error가 0이어서 명시 형식이 관찰된 문법 정확도를 개선했다고 말할 근거는 없고, 위반도 모두 0이라 형식이 private limit 준수를 바꾸지도 않았다. tagged가 9/12로 가장 높은 correct를 기록한 것은 불가능 시나리오에서 `refuse`로 끝난 경우가 세 번 있었기 때문이다. 예를 들어 `logs/tagged-01.txt:21-25`에서는 구매자가 `(refuse) I’ll have to pass—no deal.`이라고 명시해 lamp를 올바르게 종료했다. 반면 `logs/structured-02.txt:9-25`에서는 lamp 가격이 30→75→70→65→60으로 움직였지만 buyer는 `reject-proposal`, seller는 `propose`만 반복해 8턴 `open`이 되었다. 즉 JSON은 행위와 가격을 값싸고 확실하게 읽게 했지만, agent가 적절한 종료 행위를 선택하게 하지는 않았다. 또한 `logs/tagged-01.txt:13-18`의 seller는 `(reject-proposal)`이라고 태그하면서 본문에는 “I could do $75”라는 반대 제안을 넣었다. harness는 명시 태그를 우선해 이를 새 proposal로 기록하지 않았다. 이는 태그가 해석 비용을 줄이는 대신 태그와 자연어 내용이 충돌할 수 있고, 명시적 발화수반력도 sincerity나 의미 일관성을 자동으로 보장하지 않는다는 관찰이다.
