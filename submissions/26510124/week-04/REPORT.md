# Week 04 — 같은 협상, 세 가지 메시지 형식

학번: 26510124 · 실험일: 2026-09-22 · 기준 과제: [공식 Week 04 README](../../../weeks/week-04/README.md)

## 1. 설정과 재현 방법

buyer 1개와 seller 1개가 자기 관점의 history로 교대 협상한다. reader는
협상 참여자가 아니라 공개 transcript의 마지막 발화를 해석하는 보조 호출이다.
도구 호출은 없으며 세 역할 모두 동일한 모델 설정을 사용했다.

| 설정 | 실제 값 |
|---|---|
| provider / endpoint | OpenAI / https://api.openai.com/v1 |
| 요청 모델 | `gpt-5.4-mini` |
| 응답에 기록된 모델 | `gpt-5.4-mini-2026-03-17` |
| SDK / Python | `openai==3.14.0` / Python 3.12.6 |
| sampling | `temperature=0.2`, `reasoning_effort=none`; top_p 등은 전송하지 않음 |
| 출력 한도 / timeout | `max_completion_tokens=500` / 요청당 90초 |
| 재시도 | SDK 재시도 0; 408/409/429, 일부 5xx와 연결·timeout 오류에 최대 4회, 1/2/4/8초 대기 |
| seed | 미설정. 동일 문자열·동일 수치의 재현을 보장하지 않음 |
| 턴 / 실행 수 | buyer 먼저, 메시지 1개=1턴, 최대 8턴; 4시나리오 × 3조건 × 3회 = 36 |
| 실행 순서 | 반복별 free → tagged → structured; 각 조건 안에서는 아래 시나리오 순서 |
| 실제 실행 시간 | 2026-09-22 12:05:06–12:08:23 UTC (21:05:06–21:08:23 KST) |

`reasoning_effort=none`과 temperature 조합은
[OpenAI GPT-5.4 mini 문서](https://developers.openai.com/api/docs/models/gpt-5.4-mini)와
[모델 사용 가이드](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.4)를 확인해 적용했다.
모델 별칭은 향후 다른 스냅샷을 가리킬 수 있으므로 실제 응답 식별자도 보존했다.
JSON mode, response_format, function calling은 사용하지 않았다. 구조화 출력을
강제하는 API 기능 없이 FORMAT 문단의 효과를 비교한다.

### 실행 전 고정한 시나리오

| id | item | reserve | budget | deal_possible |
|---|---|---:|---:|---:|
| bicycle-wide | a used bicycle | 120 | 150 | 1 |
| lamp-narrow | a desk lamp | 35 | 45 | 1 |
| textbook-boundary | a second-hand textbook | 40 | 40 | 1 |
| keyboard-impossible | a mechanical keyboard | 90 | 70 | 0 |

`scenarios.json`은 첫 실제 모델 호출 전에 단독 커밋 `9aa2be9`로 고정했다.
실행 코드 커밋은 `287ca7c`이며 이후 실행 소스와 프롬프트는 바꾸지 않았다.
[experiment.json](experiment.json)에 코드 SHA-256, 시나리오, 전체 프롬프트,
설정과 환경 버전을 저장했다. 아래 결과는 첫 공식 36개 에피소드 전부다.

### 프롬프트 전문

각 system prompt는 `ROLE[role].format(item=item, limit=limit) + COMMON + FORMAT[condition]`이다.
buyer의 limit에는 budget만, seller에는 reserve만 넣는다.
상대 한도는 system prompt나 reader 입력에 전달하지 않는다.
에이전트가 대화에서 자신의 한도를 말하면 그 발화는 공개 정보가 된다.
다음 코드 블록에서 COMMON과 FORMAT의 첫 공백도 실제 결합 문자열의 일부다.

ROLE.buyer:

~~~text
You are the buyer of {item}, negotiating the price with the seller. Your private limit: you can pay at most {limit}. Never agree to a price above {limit}.
~~~

ROLE.seller:

~~~text
You are the seller of {item}, negotiating the price with the buyer. Your private limit: you can accept at least {limit}. Never agree to a price below {limit}.
~~~

COMMON:

~~~text
 Four acts are available: propose (offer a price), accept-proposal (agree to the other side's last price, which ends the negotiation with a deal), reject-proposal (decline the last price and keep negotiating), and refuse (leave the negotiation for good, no deal).
~~~

FORMAT.free:

~~~text
 Write your message as one or two plain English sentences.
~~~

FORMAT.tagged:

~~~text
 Start your message with exactly one performative tag in parentheses, one of (propose), (accept-proposal), (reject-proposal), (refuse), then write one plain English sentence.
~~~

FORMAT.structured:

~~~text
 Reply with exactly one JSON object and nothing else: {"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "content": {"price": <whole number or null>}}.
~~~

READER_SYSTEM:

~~~text
You are an observer reading a price negotiation between a buyer and a seller. Label the LAST message only. Reply with exactly one JSON object and nothing else: {"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "price": <whole number or null>}.
~~~

buyer history에만 추가하는 첫 user 메시지(세 조건 동일):

~~~text
The negotiation begins now. Send the buyer's opening message.
~~~

reader의 user 입력은 공개 메시지 리스트를 `json.dumps(transcript, ensure_ascii=False)`로 직렬화한 것이다.
항목은 `{"role":"buyer" 또는 "seller","content":원문}`이다.
reader에게는 scenario 메타데이터·비공개 한도·opening cue를 따로 주지 않는다.
각 협상자의 history에서 자기 발화는 assistant, 상대 발화는 user다.

### 프로토콜과 평가 규칙

- `free`: 모든 메시지에 reader를 호출하고 응답 전체를 JSON으로 해석한다.
- `tagged`: `^\s*\((propose|accept-proposal|reject-proposal|refuse)\)\s+`로 맨 앞 태그를 읽는다.
  `propose`일 때만 같은 reader로 가격을 추출하며 reader의 performative는 무시한다.
- `structured`: 선행 공백 뒤 첫 JSON 객체를 읽는다. 뒤의 자연어는 `trailing`으로
  기록하되 해석하지 않는다. 문장 안 JSON이나 Markdown fence는 찾아내어 보정하지 않는다.
  필수 필드와 타입을 검사하고 추가 필드는 무시한다. reader 호출은 0이다.
- 가격은 0 이상의 정수다. bool·실수·음수·숫자 문자열·중복 JSON 키·NaN/Infinity는
  거절한다. `propose`의 null 가격은 오류다. 다른 행위의 정수 가격은 타입만
  검증하고 무시하며 null도 허용한다.
- `propose`만 해당 역할의 마지막 가격을 갱신한다. `accept-proposal`은
  상대의 마지막 유효한 제안 가격으로 거래한다. accept 본문의 다른 숫자는 쓰지 않는다.
  상대 제안 없는 accept는 `semantic_errors`에 세고 계속한다.
- `reject-proposal`은 상태 가격을 바꾸지 않으며 `refuse`는 `no_deal`로 끝낸다.
  8턴까지 끝나지 않으면 `open`이다. parse 실패 시 `format_errors`를 늘리고 계속한다.
  해석 성공 여부와 무관하게 원문은 이미 양쪽 history에 전달되어 있다.
- `correct=1`은 가능할 때 양쪽 한도 안에서 거래하거나, 불가능할 때
  `no_deal`로 끝난 경우뿐이다. `open`은 항상 0이다. 거래 가격이 reserve보다
  낮거나 budget보다 높으면 `violation=1`이다. 거래 전 한도 차단은 하지 않아
  위반을 관측할 수 있게 했다. `no_deal/open`의 price는 빈칸, violation은 0이다.
- `format_errors`는 프로토콜 파싱 실패만 센다. 문법은 맞지만 의미가 어긋나는
  accept나 잘못된 거래는 별도의 semantic_errors, correct, violation으로 나타난다.

### 실행 명령과 보존

저장소 루트에서:

~~~bash
cd submissions/26510124/week-04
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest -v test_week04
# OPENAI_API_KEY는 환경변수로만 설정; 공식 API에서는 OPENAI_BASE_URL 미설정
.venv/bin/python run_experiment.py --runs 3 --max-turns 8 --model gpt-5.4-mini --temperature 0.2 --reasoning-effort none
.venv/bin/python analyze_results.py
python3 ../../../scripts/check_week04.py .
~~~

같은 실행 명령은 기존 `(condition,run,scenario)`를 건너뛴다.
완료·실패 행 모두 남기며 CSV에 append/flush/fsync한다.
CSV 저장 직전에 중단되면 로그의 결과로 복구하고, 시작만 남으면 중단 행을 기록한다.
원본 로그는 수정하지 않는다. 반복별·조건별 콘솔 JSONL을 `logs/*.log` 9개로 보존했다.
독립 재현 시에는 원본을 덮어쓰지 않도록 `--output-dir reproduced`를 추가한다.
재현 결과의 검사는 `.venv/bin/python analyze_results.py --root reproduced`로 한다.
코드·시나리오·프롬프트·설정 fingerprint가 다른 실험은 동일 결과 디렉터리에 섞지 못한다.
키, .env, .venv, cache는 커밋하지 않는다. 잠금에 fcntl을 사용하므로 macOS/Linux 기준이다.

## 2. 실제 결과

각 조건 12개 에피소드이며 정확률은 `sum(correct)/12`, 턴은 메시지 수의 평균이다.
API 오류·중단·재시도는 이번 실제 실행에서 모두 0건이다. 파싱 오류도 모두 0건이며,
실패한 협상이나 한도 위반을 재실행·삭제하지 않았다.

| condition | correct | deal / no_deal / open | violation | mean turns | format errors | reader calls |
|---|---:|---:|---:|---:|---:|---:|
| free | 11/12 (91.7%) | 9 / 2 / 1 | 0 | 4.08 | 0 | 49 |
| tagged | 6/12 (50.0%) | 7 / 5 / 0 | 4 | 4.75 | 0 | 20 |
| structured | 7/12 (58.3%) | 7 / 0 / 5 | 0 | 5.92 | 0 | 0 |

### 읽기 비용과 전체 생성 비용

| condition | agent calls | reader calls | 전체 API calls | input tokens | output tokens | total tokens | semantic errors |
|---|---:|---:|---:|---:|---:|---:|---:|
| free | 49 | 49 | 98 | 14,990 | 1,496 | 16,486 | 0 |
| tagged | 57 | 20 | 77 | 13,946 | 1,409 | 15,355 | 3 |
| structured | 71 | 0 | 71 | 14,838 | 1,170 | 16,008 | 0 |

reader_calls는 협상자 생성 호출을 포함하지 않는다. 재시도가 있었다면
추가 시도도 호출 수에 포함하며 usage를 알 수 없는 실패 시도의 비용을 0으로
가정하지 않는다. 이번에는 246회 요청 모두 성공하여 응답 usage 합계는 47,849토큰이다.
이는 API 청구액이 아니라 토큰 계측값이다. structured는 reader가 없어도 대화가
길어져 총 토큰이 tagged보다 많았다. 읽기 비용 절감과 전체 실험 비용은 같은 지표가 아니다.

### 전체 36개 에피소드

[results.csv](results.csv)의 모든 열·모든 행을 아래에 옮겼다. `—`는 CSV의 빈 price다.
note의 agent_calls는 생성 요청 수, input/output/total_tokens는 생성과 reader 합계,
retries는 재시도 수, semantic_errors는 선행 상대 제안 없는 accept 수다.

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | free | bicycle-wide | 1 | deal | 150 | 1 | 0 | 3 | 0 | 3 | agent_calls=3;input_tokens=825;output_tokens=97;total_tokens=922;retries=0;semantic_errors=0 |
| 1 | free | lamp-narrow | 1 | deal | 40 | 1 | 0 | 3 | 0 | 3 | agent_calls=3;input_tokens=788;output_tokens=88;total_tokens=876;retries=0;semantic_errors=0 |
| 1 | free | textbook-boundary | 1 | deal | 40 | 1 | 0 | 4 | 0 | 4 | agent_calls=4;input_tokens=1124;output_tokens=114;total_tokens=1238;retries=0;semantic_errors=0 |
| 1 | free | keyboard-impossible | 0 | no_deal | — | 1 | 0 | 5 | 0 | 5 | agent_calls=5;input_tokens=1623;output_tokens=159;total_tokens=1782;retries=0;semantic_errors=0 |
| 1 | tagged | bicycle-wide | 1 | deal | 100 | 0 | 1 | 4 | 0 | 1 | agent_calls=4;input_tokens=819;output_tokens=100;total_tokens=919;retries=0;semantic_errors=0 |
| 1 | tagged | lamp-narrow | 1 | deal | 30 | 0 | 1 | 4 | 0 | 1 | agent_calls=4;input_tokens=805;output_tokens=80;total_tokens=885;retries=0;semantic_errors=1 |
| 1 | tagged | textbook-boundary | 1 | no_deal | — | 0 | 0 | 3 | 0 | 1 | agent_calls=3;input_tokens=609;output_tokens=72;total_tokens=681;retries=0;semantic_errors=0 |
| 1 | tagged | keyboard-impossible | 0 | no_deal | — | 1 | 0 | 8 | 0 | 3 | agent_calls=8;input_tokens=2306;output_tokens=221;total_tokens=2527;retries=0;semantic_errors=0 |
| 1 | structured | bicycle-wide | 1 | deal | 120 | 1 | 0 | 4 | 0 | 0 | agent_calls=4;input_tokens=736;output_tokens=66;total_tokens=802;retries=0;semantic_errors=0 |
| 1 | structured | lamp-narrow | 1 | deal | 38 | 1 | 0 | 4 | 0 | 0 | agent_calls=4;input_tokens=736;output_tokens=66;total_tokens=802;retries=0;semantic_errors=0 |
| 1 | structured | textbook-boundary | 1 | open | — | 0 | 0 | 8 | 0 | 0 | agent_calls=8;input_tokens=1776;output_tokens=132;total_tokens=1908;retries=0;semantic_errors=0 |
| 1 | structured | keyboard-impossible | 0 | open | — | 0 | 0 | 8 | 0 | 0 | agent_calls=8;input_tokens=1768;output_tokens=132;total_tokens=1900;retries=0;semantic_errors=0 |
| 2 | free | bicycle-wide | 1 | deal | 150 | 1 | 0 | 3 | 0 | 3 | agent_calls=3;input_tokens=865;output_tokens=106;total_tokens=971;retries=0;semantic_errors=0 |
| 2 | free | lamp-narrow | 1 | deal | 40 | 1 | 0 | 3 | 0 | 3 | agent_calls=3;input_tokens=788;output_tokens=88;total_tokens=876;retries=0;semantic_errors=0 |
| 2 | free | textbook-boundary | 1 | deal | 40 | 1 | 0 | 5 | 0 | 5 | agent_calls=5;input_tokens=1527;output_tokens=144;total_tokens=1671;retries=0;semantic_errors=0 |
| 2 | free | keyboard-impossible | 0 | open | — | 0 | 0 | 8 | 0 | 8 | agent_calls=8;input_tokens=2918;output_tokens=242;total_tokens=3160;retries=0;semantic_errors=0 |
| 2 | tagged | bicycle-wide | 1 | deal | 120 | 1 | 0 | 2 | 0 | 1 | agent_calls=2;input_tokens=410;output_tokens=49;total_tokens=459;retries=0;semantic_errors=0 |
| 2 | tagged | lamp-narrow | 1 | deal | 30 | 0 | 1 | 4 | 0 | 1 | agent_calls=4;input_tokens=813;output_tokens=84;total_tokens=897;retries=0;semantic_errors=1 |
| 2 | tagged | textbook-boundary | 1 | deal | 40 | 1 | 0 | 7 | 0 | 3 | agent_calls=7;input_tokens=2007;output_tokens=174;total_tokens=2181;retries=0;semantic_errors=0 |
| 2 | tagged | keyboard-impossible | 0 | no_deal | — | 1 | 0 | 8 | 0 | 3 | agent_calls=8;input_tokens=2297;output_tokens=210;total_tokens=2507;retries=0;semantic_errors=0 |
| 2 | structured | bicycle-wide | 1 | deal | 120 | 1 | 0 | 2 | 0 | 0 | agent_calls=2;input_tokens=331;output_tokens=33;total_tokens=364;retries=0;semantic_errors=0 |
| 2 | structured | lamp-narrow | 1 | deal | 35 | 1 | 0 | 6 | 0 | 0 | agent_calls=6;input_tokens=1212;output_tokens=98;total_tokens=1310;retries=0;semantic_errors=0 |
| 2 | structured | textbook-boundary | 1 | deal | 40 | 1 | 0 | 7 | 0 | 0 | agent_calls=7;input_tokens=1495;output_tokens=115;total_tokens=1610;retries=0;semantic_errors=0 |
| 2 | structured | keyboard-impossible | 0 | open | — | 0 | 0 | 8 | 0 | 0 | agent_calls=8;input_tokens=1768;output_tokens=132;total_tokens=1900;retries=0;semantic_errors=0 |
| 3 | free | bicycle-wide | 1 | deal | 120 | 1 | 0 | 2 | 0 | 2 | agent_calls=2;input_tokens=497;output_tokens=71;total_tokens=568;retries=0;semantic_errors=0 |
| 3 | free | lamp-narrow | 1 | deal | 40 | 1 | 0 | 3 | 0 | 3 | agent_calls=3;input_tokens=797;output_tokens=89;total_tokens=886;retries=0;semantic_errors=0 |
| 3 | free | textbook-boundary | 1 | deal | 40 | 1 | 0 | 3 | 0 | 3 | agent_calls=3;input_tokens=835;output_tokens=96;total_tokens=931;retries=0;semantic_errors=0 |
| 3 | free | keyboard-impossible | 0 | no_deal | — | 1 | 0 | 7 | 0 | 7 | agent_calls=7;input_tokens=2403;output_tokens=202;total_tokens=2605;retries=0;semantic_errors=0 |
| 3 | tagged | bicycle-wide | 1 | deal | 125 | 1 | 0 | 4 | 0 | 2 | agent_calls=4;input_tokens=961;output_tokens=95;total_tokens=1056;retries=0;semantic_errors=0 |
| 3 | tagged | lamp-narrow | 1 | deal | 30 | 0 | 1 | 4 | 0 | 1 | agent_calls=4;input_tokens=805;output_tokens=84;total_tokens=889;retries=0;semantic_errors=1 |
| 3 | tagged | textbook-boundary | 1 | no_deal | — | 0 | 0 | 3 | 0 | 1 | agent_calls=3;input_tokens=609;output_tokens=72;total_tokens=681;retries=0;semantic_errors=0 |
| 3 | tagged | keyboard-impossible | 0 | no_deal | — | 1 | 0 | 6 | 0 | 2 | agent_calls=6;input_tokens=1505;output_tokens=168;total_tokens=1673;retries=0;semantic_errors=0 |
| 3 | structured | bicycle-wide | 1 | deal | 120 | 1 | 0 | 4 | 0 | 0 | agent_calls=4;input_tokens=736;output_tokens=66;total_tokens=802;retries=0;semantic_errors=0 |
| 3 | structured | lamp-narrow | 1 | deal | 38 | 1 | 0 | 4 | 0 | 0 | agent_calls=4;input_tokens=736;output_tokens=66;total_tokens=802;retries=0;semantic_errors=0 |
| 3 | structured | textbook-boundary | 1 | open | — | 0 | 0 | 8 | 0 | 0 | agent_calls=8;input_tokens=1776;output_tokens=132;total_tokens=1908;retries=0;semantic_errors=0 |
| 3 | structured | keyboard-impossible | 0 | open | — | 0 | 0 | 8 | 0 | 0 | agent_calls=8;input_tokens=1768;output_tokens=132;total_tokens=1900;retries=0;semantic_errors=0 |


## 3. FIPA-ACL과의 비교

FIPA 쪽은 [강의 A2/A3](../../../week-04.html)의 메시지 구조, FP/RE,
sincerity와 interaction protocol 설명에 근거한다. 이 실험은 네 행위 이름을 빌린
축소 협상 프로토콜이며 FIPA 전체 규격 구현이나 성실성 검증기가 아니다.

| 비교 항목 | FIPA-ACL | free | tagged | structured |
|---|---|---|---|---|
| illocutionary force가 있는 위치 | 외부 performative 필드; 모든 ACL 메시지에서 유일한 필수 파라미터 | 자연어 문맥에서 reader가 추론 | 문장 맨 앞 태그 | JSON performative |
| content 언어 | 별도 content 언어(예: FIPA-SL)와 ontology를 공유·지정; language 등은 선택 파라미터 | 영어 문장 | 태그 뒤 영어 문장 | content.price 정수/null의 축소 JSON 스키마 |
| content 해석 주체 | 수신 에이전트의 content-language/ontology 처리기 | reader LLM이 행위와 가격; 상대 LLM도 원문을 읽음 | regex가 행위, propose에서만 reader가 가격; 상대 LLM은 원문을 읽음 | 로컬 JSON parser가 행위·가격; 상대 LLM은 원문을 읽음 |
| 대화 종료 방식 | 선택한 interaction protocol에 따름; ACL 자체가 8턴을 정하지 않음 | 해석된 accept/refuse 또는 8턴 | 태그 accept/refuse 또는 8턴 | JSON accept/refuse 또는 8턴 |
| sincerity를 보장하는 것 | FP가 믿음·의도와 성실성을 전제하지만 메시지만으로 내부 상태나 RE 달성을 보장하지 않음 | 역할 지시뿐; 외부 보장 없음 | 태그가 성실성·한도 준수를 보장하지 않음 | 스키마가 성실성·한도 준수를 보장하지 않음 |
| 메시지 하나를 읽는 비용 | 파싱·내용 해석 및 사전 언어/ontology 합의 비용; LLM 호출은 규격의 요구가 아님 | reader 1회 + 로컬 검증 | regex; propose에만 reader 1회 | 로컬 JSON parser만, reader 0회 |
| 주요 실패 방식 | 잘못된 content/ontology, 성실성 전제 위반, 정신 상태 검증의 한계 | 문맥·복수 가격 오독 가능; 이번에는 open 1회, 명백한 오독 미관찰 | 태그와 역제안 본문의 불일치; 선행제안 없는 accept 3회, 공식 거래 위반 4회 | 스키마 오류 가능; 이번에는 오류 0이지만 8턴 open 5회 |

## 4. 로그 기반 해석

free는 11/12가 정답이었지만 읽기 호출 49회를 썼고, tagged는 이를 20회로 줄인 대신
정답이 6/12로 낮아졌다. 태그의 장점은 행위를 로컬에서 확정해 reader를 줄인다는
것이고, 비용은 자연어의 역제안과 공식 제안 상태가 분리될 수 있다는 점이다.
free의 “I can’t accept 30, but I can offer 40”는 reader가 propose·40으로
읽어 상태를 갱신했다([free-run-01.log](logs/free-run-01.log#L42), 42·45–47행).
반면 tagged bicycle run 1에서는 buyer가 “(reject-proposal) … move to 125”라고
쓰고 seller가 “(accept-proposal) 125 works for me”라고 답했지만,
125는 유효 propose가 아니어서 공식 거래는 첫 제안 100으로 남았다
([tagged-run-01.log](logs/tagged-run-01.log#L19), 19–27행).
이 100은 reserve 120 미만이며 lamp에서도 같은 종류의 위반이 3회 더 나타났다.
따라서 네 위반을 seller가 자연어로 낮은 가격에 동의한 사건이라고 단정할 수는 없고,
태그의 상태와 자연어 합의가 어긋난 공식 거래의 위반으로 해석해야 한다.
structured는 reader 0회와 위반 0회였지만 정답 7/12, 평균 5.92턴,
open 5회였다. textbook run 1에서 buyer는 25→30→35→38을 제안하고 seller는
null 가격의 reject만 반복해, 가능 가격 40이 있는데도 8턴에서 끝났다
([structured-run-01.log](logs/structured-run-01.log#L54), 54–92행).
파싱 오류가 세 조건 모두 0이었다는 사실은 성공이나 한도 준수의 보장이 아니다.
세 형식 어디에도 내부 의도·성실성을 검증하거나 올바른 종료를 강제하는 장치는 없고,
이번 표본의 위반 0도 그러한 보장이 생겼다는 증거는 아니다. 따라서 이 결과는
자유어 또는 JSON의 일반적 우승을 말하기보다, 행위 해석 비용과 표현·상태 일치의
교환관계를 보여 준다.

### 대표 실패 유형 점검

정규식으로 찾은 후보와 원문을 직접 읽은 의미 판정을 구분했다.
analyzer의 `tagged_reject_with_number_candidate=22`에는 기존 가격을 단순 거절한
문장도 포함되며, 실제 새로운 역제안을 담은 메시지는 13개(11개 에피소드)였다.

| 점검 유형 | 이번 관찰 | 근거 |
|---|---|---|
| free 질문을 refuse로 오분류 | 0건; 첫 턴 no_deal도 0건 | free-run-02 62·65행의 “Would you take 25…”는 propose·25로 해석 |
| 여러 가격 중 잘못된 reader 가격 선택 | 명백한 오독 미관찰 | free-run-01 106·109행: 55·120·100 중 최종 역제안 100 선택 |
| tagged reject 뒤 자연어 역제안 | 13메시지 / 11에피소드 | tagged-run-01 14·19행 등; 태그가 reject이므로 상태 가격 미갱신 |
| structured JSON 뒤 자연어 | 0건 | 71개 structured 메시지의 trailing이 모두 빈 문자열 |
| structured null 가격, 후행 문장에만 숫자 | 0건 | 후행 자연어 자체가 미관찰 |
| 상대 유효 제안 없이 accept | 3건 | lamp buyer 3턴; tagged-run-01 48행, run-02 38행, run-03 51행 |
| buyer의 budget 초과 거래 | 0건 | 모든 deal 가격과 buyer의 자연어 수락 가격 점검 |
| seller의 reserve 미만 공식 거래 | 4건 | tagged-run-01 27·54행, run-02 44행, run-03 57행 |
| 8턴 open | 6건: free 1 + structured 5 | free-run-02 168행; structured-run-01 92·136행, run-02 131행, run-03 92·136행 |

### 검증 범위와 한계

| 확인 | 결과 |
|---|---|
| `.venv/bin/python -m unittest -q test_week04` | 오프라인 테스트 45개 통과 |
| `.venv/bin/python analyze_results.py` | 36개 결과와 9개 로그의 독립 상태 재계산 일치 |
| `python3 ../../../scripts/check_week04.py .` | 공식 형식 검사 통과 |
| 실행 소스 SHA-256 / 보고서 결과표 대조 | 고정한 5개 실행 파일 해시 일치 / CSV 36행의 모든 셀 일치 |

오프라인 테스트는 실제 실험과 별개인 가짜 backend를 사용한다. 파싱, 상대 마지막
제안 수락, 한도 위반 계측, 비공개 한도 격리, 429 재시도, 중단/resume, 설정 혼합
거부를 검증한다. 분석기는 실행 상태 기계를 import하지 않고 로그의 parse 결과부터
다시 계산하며, CSV와 로그를 함께 변조해도 상태와 맞지 않으면 검출하는 테스트를 둔다.
단, 이 검사는 자연어 reader의 의미 판단 자체가 옳다는 증명이 아니므로 위 사례는
별도로 원문과 비교했다.

조건당 12개 표본, 고정된 실행 순서, 단일 모델·temperature, seed 미설정이라는
한계가 있다. reader도 같은 LLM이므로 독립적인 정답 라벨러는 아니다. 한도 위반은
메시지가 아니라 공식 deal 가격에서 계산한다. 모든 메시지는 네 행위 중 하나로만
읽으며 query-ref/cfp는 없다. 이번에는 강의의 “첫 질문이 refuse로 읽히는” 현상이
나오지 않았고 이를 유도하려고 재실행하지 않았다. hidden state는 모델의 실제
믿음이나 성실성 측정값이 아니다.

실행 중 API 실패와 강제 중단이 없었으므로 해당 복구 기능은 오프라인 테스트로만
검증했다. backoff 대기 중 강제 중단 시 로그의 예정된 재시도와 실제 송신 수는
구분이 불가능할 수 있으며 분석기는 해당 미확인 호출 수를 unknown으로 다룬다.
사후에 조건별 프롬프트를 바꾸거나 실패를 지운 실행은 없다.
추가 모델·턴 제한·표현 방식 실험은 이 결과에 섞지 않았으며 필수 36개 실험으로
범위를 한정했다.
