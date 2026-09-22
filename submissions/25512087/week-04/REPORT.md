# Week 04 — PART B: 같은 협상, 세 가지 메시지 형식

## 1. 설정

이번 보고서는 명세 차이를 수정한 harness의 실험 결과를 다룬다. 기존 실행 결과는 보존하기 위해 `results.csv`와 `logs/`에 남겨 두었고, 수정 후 실행 결과는 `results_corrected.csv`와 `logs_corrected/`에 저장했다. 아래 결과표와 해석은 모두 corrected 결과를 기준으로 한다.

buyer 1명과 seller 1명이 네 가지 물건의 가격을 협상한다. buyer는 최대 지불액(`budget`), seller는 최저 수용가(`reserve`)를 비공개로 가진다. buyer가 먼저 말하고, 양쪽이 번갈아 최대 8개의 메시지를 주고받는다. `accept-proposal`은 상대방의 마지막 `propose` 가격으로 거래를 끝내며, `refuse`는 결렬로 끝난다. 8턴까지 끝나지 않으면 `open`이다.

provider는 `https://api.openai.com/v1`, model은 `gpt-5.6-luna`, temperature는 `0`, turn limit은 `8`이다. 같은 시나리오·역할 프롬프트·모델·temperature·턴 제한을 세 조건에 공통으로 사용하고, 메시지 형식 문단과 protocol reader만 바꿨다. 각 조건을 3회 반복해 총 36개 에피소드를 실행했다.

시나리오는 다음과 같다.

| scenario | reserve | budget | deal possible |
|---|---:|---:|---|
| headphones | 80 | 100 | yes |
| coffee-maker | 120 | 100 | no |
| textbook | 30 | 45 | yes |
| desk-lamp | 70 | 50 | no |

메시지 형식은 다음과 같다.

- `free`: 한두 문장의 plain English. reader가 전체 대화의 마지막 메시지에서 performative와 정수 가격을 읽는다.
- `tagged`: `(propose)` 같은 performative tag 뒤에 plain English를 쓴다. tag는 정규식으로 읽고, `propose`의 가격만 reader가 읽는다.
- `structured`: `{"performative":"propose","content":{"price":80}}` 같은 JSON 하나를 보낸다. 로컬 parser가 읽으므로 reader 호출이 없다.

reader prompt는 다음과 같다.

```text
You are an observer reading a price negotiation between a buyer and a seller.
Label the LAST message only with exactly one of propose, accept-proposal,
reject-proposal, or refuse. Extract an integer price only when the speaker
proposes one. Return exactly one JSON object and nothing else:
{"performative":"...", "price": integer or null}.
```

수정된 실행 명령은 다음과 같다.

```bash
python3 run_experiment.py \
  --runs 3 \
  --turn-limit 8 \
  --results results_corrected.csv \
  --log-dir logs_corrected
```

각 agent는 별도의 Chat history를 가진다. 자기 메시지는 `assistant`, 상대 메시지는 `user`로 기록하며, free reader에는 누적 대화와 마지막 메시지를 전달한다. 읽지 못한 메시지도 history에 남기고 다음 턴으로 진행한다.

## 2. 결과

### 조건별 요약

| condition | episodes | deal | no-deal | open | correct | violations | mean turns | format errors | reader calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| free | 12 | 6 (50.0%) | 4 (33.3%) | 2 (16.7%) | 10 (83.3%) | 0 | 6.67 | 0 | 80 |
| tagged | 12 | 6 (50.0%) | 0 | 6 (50.0%) | 5 (41.7%) | 1 | 5.67 | 0 | 52 |
| structured | 12 | 6 (50.0%) | 0 | 6 (50.0%) | 6 (50.0%) | 0 | 5.67 | 0 | 0 |

전체 36회에서는 deal 18회, no-deal 4회, open 14회가 발생했다. correct는 21회, violation은 1회였으며 format error는 없었다. 총 reader 호출은 free 80회, tagged 52회, structured 0회였다.

가능한 시나리오에서는 headphones가 9/9 deal, textbook이 9/9 deal로 끝났다. 불가능한 시나리오에서는 coffee-maker가 1회 no-deal·8회 open, desk-lamp가 3회 no-deal·6회 open이었다. 즉 모델은 성립 불가능한 협상을 항상 즉시 종료하지는 않았다.

### 에피소드별 결과

셀 형식은 `outcome / price / correct / violation / turns / reader_calls`이다. `-`는 거래 가격이 없다는 뜻이다.

| condition | scenario | repeat 1 | repeat 2 | repeat 3 |
|---|---|---|---|---|
| free | headphones | deal / 85 / 1 / 0 / 6 / 6 | deal / 85 / 1 / 0 / 6 / 6 | deal / 85 / 1 / 0 / 6 / 6 |
| free | coffee-maker | no_deal / - / 1 / 0 / 8 / 8 | open / - / 0 / 0 / 8 / 8 | open / - / 0 / 0 / 8 / 8 |
| free | textbook | deal / 35 / 1 / 0 / 4 / 4 | deal / 37 / 1 / 0 / 6 / 6 | deal / 40 / 1 / 0 / 5 / 5 |
| free | desk-lamp | no_deal / - / 1 / 0 / 7 / 7 | no_deal / - / 1 / 0 / 8 / 8 | no_deal / - / 1 / 0 / 8 / 8 |
| tagged | headphones | deal / 70 / 0 / 1 / 4 / 2 | deal / 88 / 1 / 0 / 7 / 6 | deal / 95 / 1 / 0 / 3 / 2 |
| tagged | coffee-maker | open / - / 0 / 0 / 8 / 7 | open / - / 0 / 0 / 8 / 5 | open / - / 0 / 0 / 8 / 7 |
| tagged | textbook | deal / 30 / 1 / 0 / 2 / 1 | deal / 30 / 1 / 0 / 2 / 1 | deal / 30 / 1 / 0 / 2 / 1 |
| tagged | desk-lamp | open / - / 0 / 0 / 8 / 4 | open / - / 0 / 0 / 8 / 8 | open / - / 0 / 0 / 8 / 8 |
| structured | headphones | deal / 100 / 1 / 0 / 3 / 0 | deal / 100 / 1 / 0 / 3 / 0 | deal / 100 / 1 / 0 / 3 / 0 |
| structured | coffee-maker | open / - / 0 / 0 / 8 / 0 | open / - / 0 / 0 / 8 / 0 | open / - / 0 / 0 / 8 / 0 |
| structured | textbook | deal / 35 / 1 / 0 / 4 / 0 | deal / 35 / 1 / 0 / 4 / 0 | deal / 40 / 1 / 0 / 3 / 0 |
| structured | desk-lamp | open / - / 0 / 0 / 8 / 0 | open / - / 0 / 0 / 8 / 0 | open / - / 0 / 0 / 8 / 0 |

## 3. FIPA-ACL과 세 조건 비교

| 항목 | FIPA-ACL | free | tagged | structured |
|---|---|---|---|---|
| illocutionary force의 위치 | 메시지의 필수 `performative` 필드 | 자연어 문맥을 reader가 추론 | 괄호 tag에 명시, 정규식으로 판독 | JSON의 `performative` 필드에 명시 |
| content 언어 | 선언된 content language와 ontology | plain English | tag 뒤 plain English | JSON schema |
| content 해석자 | 수신 agent와 공유 ontology | LLM reader | act는 정규식, 가격은 LLM reader | local parser |
| 대화 종료 | performative 의미와 protocol에 따름 | accept/refuse 또는 8턴 | 동일 | 동일 |
| sincerity 보장 | FP/RE가 믿음과 의도를 기술하지만 외부 검증은 못함 | 별도 보장 없음 | 별도 보장 없음 | syntax만 검증, 의도는 검증 못함 |
| 메시지 하나의 읽기 비용 | 수신자의 formal interpretation | 매 메시지 reader 1회 | proposal에만 reader 호출 | reader 호출 0회 |
| 관찰된 실패 | ontology·정신 상태 불일치 | open 또는 자연어 숫자/행위 오독 | tag와 본문 가격의 불일치 | 문법은 안정적이나 전략적 종료 부족 |

## 4. 해석

수정 후 실험에서는 free가 10/12 correct로 가장 높았고, structured는 6/12, tagged는 5/12였다. 이는 이번 모델과 네 시나리오에서는 자연어 reader가 proposal과 refusal을 충분히 해석해 준 반면, 명시적 형식이 자동으로 협상 전략까지 개선하지는 않았다는 뜻이다. free의 `logs_corrected/free-01.jsonl:21-45`에서는 coffee-maker의 reserve 120과 buyer budget 100이 맞지 않자 seller가 마지막에 `refuse`를 내고, 8턴에 correct no-deal로 끝났다. 반면 `free-02.jsonl:21-45`에서는 양쪽이 100과 120 사이의 제안을 반복해 8턴 `open`이 됐다. tagged에서는 performative tag가 행위 판독을 안정화했지만 본문 가격과 tag가 충돌할 수 있었다. `logs_corrected/tagged-01.jsonl:2-12`에서 buyer는 먼저 70을 제안하고 이후 `(reject-proposal) ... offer $80`을 보냈는데, seller의 `(accept-proposal)`을 harness는 상대의 마지막 `propose`인 70에 대한 동의로 해석했다. seller reserve는 80이므로 기록된 거래 가격 70은 violation이 되었고, correct도 0이 되었다. 이 사례는 tag가 illocutionary force를 명확히 하는 대신, tag 뒤 자연어에 적힌 counter-offer를 protocol 내용으로 인정하지 않을 때 비용이 생긴다는 증거다. structured는 `logs_corrected/structured-01.jsonl:2-8`에서 세 개의 JSON 메시지만으로 headphones를 100에 거래했고 reader 호출은 0회였다. 그러나 `structured-01.jsonl:9-25`의 coffee-maker처럼 JSON이 모두 유효해도 seller는 8턴 동안 계속 propose만 하며 `refuse`하지 않을 수 있다. 따라서 structured는 파싱 비용과 형식 오류를 제거하지만, 불가능한 협상을 감지하고 종료하는 전략까지 보장하지 않는다. 이번 36회에서는 format error가 0회였으므로 수정한 “읽지 못한 메시지를 history에 남기고 계속 진행” 경로는 실행 로그에서는 사용되지 않았지만, 단위 테스트로 그 동작을 검증했다. 전체적으로 명시적 performative는 act 해석의 위치를 분명히 했고 reader 비용을 줄였지만, 가격 내용과 협상 종료 정책까지 자동으로 보장하지는 않았다.
