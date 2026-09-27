# Week 04 Report

## 1. 설정

Provider는 OpenAI Codex CLI 0.145.0, 모델은 `gpt-5.6-luna`다. 각 호출은 `codex exec --ephemeral --ignore-user-config --ignore-rules`로 분리했고 도구를 사용하지 말라는 공통 developer instruction을 넣었다. Codex CLI가 temperature를 노출하지 않아 temperature는 `not-settable`로 기록했다. 턴 한도는 6이며, 네 시나리오와 Buyer/Seller 역할 지시, 모델, 실행 설정은 세 조건에서 같다. 역할 system prompt는 상품과 각자의 비공개 한도, 네 행위의 정확한 이름, 상대 제안이 자기 한도 안이면 수락하라는 공통 규칙으로 구성했다. 조건별로 마지막 형식 문단만 다음과 같이 바꿨다.

- `free`: `Output exactly one short plain-English sentence. Do not include a performative label, JSON, Markdown, or an explanation outside the message.`
- `tagged`: `Output exactly one allowed performative in parentheses, followed by one short plain-English sentence. Example: (propose) I can offer 100. Do not output JSON or any additional text.`
- `structured`: `Output exactly one JSON object and no additional text. Use {"performative":"propose","content":{"price":100}} for a proposal. For every other performative, use an empty content object.`

free의 모든 메시지와 tagged의 `propose` 가격에 사용한 reader prompt는 세 조건에서 바꾸지 않았다.

```text
You are the fixed protocol reader for a price negotiation. Classify the message as
exactly one of: propose, accept-proposal, reject-proposal, refuse. Extract an
integer price only when the message proposes a price; otherwise use null. A
question or departure that cannot be represented by the four acts is refuse.
Return only one JSON object in this shape:
{"performative":"propose","price":100}
```

실행 명령은 다음과 같다. `luna-` 접두사가 붙은 36개 에피소드가 통제된 본 실험이다. 먼저 `gpt-5.6-sol`로 시작했다가 실행 비용 때문에 중단한 6개 행은 삭제하지 않고 `abandoned_model_probe`로 `results.csv`와 로그에 보존했으며 집계에서는 제외했다.

```bash
cd submissions/26510122/week-04
python3 -m unittest discover -p 'test_*.py' -v
python3 run.py --backend codex-cli --model gpt-5.6-luna \
  --run-prefix luna- --all --runs 3
```

## 2. 결과

아래 요약은 `luna-` 실행만 집계한 값이다. 각 조건은 4개 시나리오를 3회씩 실행한 12개 에피소드다.

| condition | correct | violation | mean turns | format_errors | reader_calls |
|---|---:|---:|---:|---:|---:|
| free | 3/12 (25%) | 0 | 5.08 | 1 | 61 |
| tagged | 3/12 (25%) | 0 | 5.00 | 1 | 45 |
| structured | 6/12 (50%) | 0 | 4.25 | 0 | 0 |

free와 tagged는 `bicycle-wide`만 세 번 모두 거래했고 `desk-narrow`는 세 번 모두 `open`이었다. structured는 두 거래 가능 시나리오를 모두 세 번씩 거래했다. 거래 불가능한 `camera-no-overlap`과 `headphones-no-overlap`은 세 조건에서 모두 `no_deal`이 아니라 `open`으로 끝나 오답이었다. 아래는 중단한 Sol 시도까지 포함한 `results.csv` 전체다.

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| free-r01 | free | bicycle-wide | 1 | deal | 120 | 1 | 0 | 3 | 0 | 3 | abandoned_model_probe:gpt-5.6-sol |
| free-r01 | free | camera-no-overlap | 0 | open |  | 0 | 0 | 6 | 0 | 6 | abandoned_model_probe:gpt-5.6-sol |
| free-r01 | free | desk-narrow | 1 | open |  | 0 | 0 | 6 | 0 | 6 | abandoned_model_probe:gpt-5.6-sol |
| free-r01 | free | headphones-no-overlap | 0 | open |  | 0 | 0 | 6 | 0 | 6 | abandoned_model_probe:gpt-5.6-sol |
| free-r02 | free | bicycle-wide | 1 | deal | 120 | 1 | 0 | 3 | 0 | 3 | abandoned_model_probe:gpt-5.6-sol |
| free-r02 | free | camera-no-overlap | 0 | open |  | 0 | 0 | 6 | 0 | 6 | abandoned_model_probe:gpt-5.6-sol |
| luna-free-r01 | free | bicycle-wide | 1 | deal | 80 | 1 | 0 | 2 | 0 | 2 |  |
| luna-free-r01 | free | camera-no-overlap | 0 | open |  | 0 | 0 | 6 | 0 | 6 |  |
| luna-free-r01 | free | desk-narrow | 1 | open |  | 0 | 0 | 6 | 0 | 6 |  |
| luna-free-r01 | free | headphones-no-overlap | 0 | open |  | 0 | 0 | 6 | 0 | 6 |  |
| luna-free-r02 | free | bicycle-wide | 1 | deal | 80 | 1 | 0 | 2 | 0 | 2 |  |
| luna-free-r02 | free | camera-no-overlap | 0 | open |  | 0 | 0 | 6 | 0 | 6 |  |
| luna-free-r02 | free | desk-narrow | 1 | open |  | 0 | 0 | 6 | 0 | 6 |  |
| luna-free-r02 | free | headphones-no-overlap | 0 | open |  | 0 | 0 | 6 | 0 | 6 |  |
| luna-free-r03 | free | bicycle-wide | 1 | deal | 100 | 1 | 0 | 3 | 0 | 3 |  |
| luna-free-r03 | free | camera-no-overlap | 0 | open |  | 0 | 0 | 6 | 0 | 6 |  |
| luna-free-r03 | free | desk-narrow | 1 | open |  | 0 | 0 | 6 | 0 | 6 |  |
| luna-free-r03 | free | headphones-no-overlap | 0 | open |  | 0 | 0 | 6 | 1 | 6 |  |
| luna-tagged-r01 | tagged | bicycle-wide | 1 | deal | 80 | 1 | 0 | 2 | 0 | 1 |  |
| luna-tagged-r01 | tagged | camera-no-overlap | 0 | open |  | 0 | 0 | 6 | 0 | 6 |  |
| luna-tagged-r01 | tagged | desk-narrow | 1 | open |  | 0 | 0 | 6 | 0 | 4 |  |
| luna-tagged-r01 | tagged | headphones-no-overlap | 0 | open |  | 0 | 0 | 6 | 0 | 6 |  |
| luna-tagged-r02 | tagged | bicycle-wide | 1 | deal | 80 | 1 | 0 | 2 | 0 | 1 |  |
| luna-tagged-r02 | tagged | camera-no-overlap | 0 | open |  | 0 | 0 | 6 | 1 | 4 |  |
| luna-tagged-r02 | tagged | desk-narrow | 1 | open |  | 0 | 0 | 6 | 0 | 5 |  |
| luna-tagged-r02 | tagged | headphones-no-overlap | 0 | open |  | 0 | 0 | 6 | 0 | 5 |  |
| luna-tagged-r03 | tagged | bicycle-wide | 1 | deal | 80 | 1 | 0 | 2 | 0 | 1 |  |
| luna-tagged-r03 | tagged | camera-no-overlap | 0 | open |  | 0 | 0 | 6 | 0 | 4 |  |
| luna-tagged-r03 | tagged | desk-narrow | 1 | open |  | 0 | 0 | 6 | 0 | 4 |  |
| luna-tagged-r03 | tagged | headphones-no-overlap | 0 | open |  | 0 | 0 | 6 | 0 | 4 |  |
| luna-structured-r01 | structured | bicycle-wide | 1 | deal | 100 | 1 | 0 | 2 | 0 | 0 |  |
| luna-structured-r01 | structured | camera-no-overlap | 0 | open |  | 0 | 0 | 6 | 0 | 0 |  |
| luna-structured-r01 | structured | desk-narrow | 1 | deal | 100 | 1 | 0 | 3 | 0 | 0 |  |
| luna-structured-r01 | structured | headphones-no-overlap | 0 | open |  | 0 | 0 | 6 | 0 | 0 |  |
| luna-structured-r02 | structured | bicycle-wide | 1 | deal | 80 | 1 | 0 | 2 | 0 | 0 |  |
| luna-structured-r02 | structured | camera-no-overlap | 0 | open |  | 0 | 0 | 6 | 0 | 0 |  |
| luna-structured-r02 | structured | desk-narrow | 1 | deal | 100 | 1 | 0 | 3 | 0 | 0 |  |
| luna-structured-r02 | structured | headphones-no-overlap | 0 | open |  | 0 | 0 | 6 | 0 | 0 |  |
| luna-structured-r03 | structured | bicycle-wide | 1 | deal | 80 | 1 | 0 | 2 | 0 | 0 |  |
| luna-structured-r03 | structured | camera-no-overlap | 0 | open |  | 0 | 0 | 6 | 0 | 0 |  |
| luna-structured-r03 | structured | desk-narrow | 1 | deal | 100 | 1 | 0 | 3 | 0 | 0 |  |
| luna-structured-r03 | structured | headphones-no-overlap | 0 | open |  | 0 | 0 | 6 | 0 | 0 |  |

## 3. FIPA-ACL과 비교

| 항목 | FIPA-ACL | free | tagged | structured |
|---|---|---|---|---|
| illocutionary force의 위치 | 필수 `performative` 필드 | 자연어 문맥에 암묵적으로 존재하고 reader가 추론 | 문장 앞 괄호 태그 | JSON의 `performative` 값 |
| content 언어 | `:language`로 선언한 content language와 `:ontology`로 합의한 어휘 | 제한 없는 자연어 | 자연어 한 문장 | `{"price": int}`로 제한한 JSON |
| content 해석 주체 | 선언된 언어·어휘를 아는 수신 에이전트 | 별도 LLM reader | 태그는 regex, `propose` 가격은 LLM reader | 결정론적 JSON parser |
| 대화 종료 | interaction protocol의 행위 순서와 종료 상태 | `accept-proposal`, `refuse`, 또는 6턴 `open` | 왼쪽과 같음 | 왼쪽과 같음 |
| sincerity 보장 | feasibility precondition과 rational effect가 규범을 정의하지만 발화의 진위를 강제하지 않음 | system prompt의 비공개 한도 지시, 결과에서 harness가 검증 | 왼쪽과 같음 | 왼쪽과 같음 |
| 메시지 하나를 읽는 비용 | 기호 구조를 해석하는 protocol/content parser | 매 메시지마다 LLM 1회 | 태그는 0회, `propose` 가격만 LLM 1회 | 모델 호출 없이 parser 1회 |
| 관찰된 실패 방식 | 의미 검증과 ontology 합의가 구현 밖에 남을 수 있음 | 행위가 섞인 문장의 reader 오인, reader JSON 오류 | 태그와 문장 내용의 불일치, 태그 문법 또는 대화 상태 오류 | JSON/schema 오류 가능; 이번 실행에서는 없었지만 `open` 종료는 남음 |

## 4. 해석

명시적 형식은 이 실행에서 판독 비용과 일부 협상 결과를 바꿨다. reader_calls는 free 61회에서 tagged 45회로 16회(26.2%) 줄고 structured에서 0회가 되었으며, structured의 평균 turns도 5.08에서 4.25로 줄었다. `logs/luna-free-r01.jsonl` 26~27행에서 Buyer의 `reject-proposal, I can offer $70`을 reader는 `propose 70`으로 읽어 한 문장에 섞인 거절과 역제안 가운데 하나를 선택했다. `logs/luna-tagged-r01.jsonl` 26~27행에서는 같은 종류의 문장이 태그에 따라 `reject-proposal`로 고정됐지만 문장 안의 역제안 70은 protocol state에 반영되지 않아, 태그가 모호성을 없애는 대신 태그 밖 의미를 버리는 비용을 보였다. 반면 structured의 같은 시나리오는 24~28행에서 Seller가 100을 `propose`하고 Buyer가 별도 `accept-proposal`을 보내 3턴에 끝났고, 이 차이가 correct를 3/12에서 6/12로 높였다. 다만 표본은 조건별 12개뿐이고 형식이 모델의 발화 전략 자체도 바꿨으므로 structured의 일반적 우월성으로 확대하지 않는다. 세 조건 모두 violation은 0이었지만 거래 불가능한 두 시나리오를 매번 `open`으로 끝냈으므로, 명시적 performative와 JSON도 system prompt의 종료 정책이나 sincerity를 강제하지는 못했다.
