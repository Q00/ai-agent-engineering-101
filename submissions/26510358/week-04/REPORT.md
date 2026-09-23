# Week 04 — 발화 행위 형식에 따른 buyer·seller 가격 협상

## 1. 설정과 재현

사용자 요청에 따라 Codex가 코드, 실제 모델 실험, 분석과 PR을 작성했다. [EXPERIMENT.md](EXPERIMENT.md)와 [scenarios.json](scenarios.json)을 첫 API 호출 전에 커밋했다(`2f1a7ef`). 예비 실행의 판독 오류와 수정 과정도 아래에 남겼다. [Week 04 강의·LAB](https://wpti.dev/ai-agent-engineering-101/week-04.html)과 [저장소 과제 명세](../../../weeks/week-04/README.md)의 고정 데이터 계약을 따른다.

| 항목 | 고정값 |
|---|---|
| provider / API | OpenAI, Chat Completions, `https://api.openai.com/v1` |
| 모델 / 생성 | `gpt-5.6-luna`, temperature `0.7`, `reasoning_effort=none`, `max_completion_tokens=512` |
| 실행 환경 | Python 3.13.15, `openai==3.8.0`, `python-dotenv==1.2.3` |
| 조건 / 순서 | `free` → `tagged` → `structured`, 각 조건에서 `bike` → `textbook` → `keyboard` → `laptop`을 3회 |
| 공통 설정 | buyer와 seller에 동일한 모델·temperature·네 행위 설명, buyer 선공, 메시지 상한 8 |
| 판독 | `free`: 모든 메시지에 LLM reader 1회, `tagged`: 태그는 정규식·제안 가격만 LLM reader, `structured`: 엄격한 JSON parser만 사용 |
| 실패·재개 | 429/5xx/연결 실패는 최대 6번 지수 대기 재시도, 각 에피소드 직후 CSV flush, 이미 기록된 `(run, scenario)`은 건너뜀 |

역할·한도·공통 행위 설명은 [negotiation.py](negotiation.py)의 `ROLE`과 `COMMON`으로 고정한다. 아래 세 문단만 조건에 따라 시스템 프롬프트 뒤에 붙였다. reader에도 같은 모델과 temperature를 적용했다.

**free 형식 문단 (원문)**

```text
Write one or two plain English sentences, with no tags or JSON.
```

**tagged 형식 문단 (원문)**

```text
Start with exactly one tag, (propose), (accept-proposal), (reject-proposal), or (refuse), followed by one space and a plain English sentence. If you propose, state exactly one integer price.
```

**structured 형식 문단 (원문)**

```text
Reply with exactly one JSON object and nothing else: {"performative":"propose|accept-proposal|reject-proposal|refuse","content":{"price":integer-or-null}}. Use an integer only for propose; use null for all other acts. No Markdown or explanatory text.
```

**Reader 시스템 프롬프트 (원문)**

```text
You observe a buyer/seller price negotiation. Label only the LAST message provided, using its intended speech act rather than the previous speaker's act. Reply with exactly one JSON object and nothing else: {"performative":"propose|accept-proposal|reject-proposal|refuse","price":integer-or-null}. Use one of the four literal act names, not the pipe-separated display above. Use an integer when a price is stated and null when no price is stated. A non-proposal can still mention a price. If a message has several prices, choose the speaker's offered price, not a price they reject or a private limit.
```

`free`는 전체 대화를 reader에게 주고 마지막 발화만 읽게 한다. `tagged`의 제안은 태그를 제외한 본문만 같은 reader에게 주며 그 답에서 가격만 사용한다. 파싱 실패한 메시지도 상대의 대화 이력에 전달한다. `accept-proposal`은 상대의 마지막 유효 `propose` 가격으로만 거래를 성립시킨다. `correct=1`은 가능한 거래가 양쪽 한도 안에서 성립하거나 불가능한 거래가 명시적 `no_deal`로 끝난 경우이고, `open`은 양쪽 모두 `0`이다. `violation=1`은 성립한 거래 가격이 reserve 미만 또는 budget 초과인 경우다.

저장소 루트에서 키를 환경변수로 제공한다. 아래 비공개 `.env` 경로는 예시이며 파일과 키 값은 커밋하지 않는다.

```bash
uv venv --python 3.13.15 /tmp/ai-agent-week04-venv
uv pip install --python /tmp/ai-agent-week04-venv/bin/python -r submissions/26510358/week-04/requirements.txt
export OPENAI_BASE_URL=https://api.openai.com/v1
export AGENT_MODEL=gpt-5.6-luna AGENT_TEMPERATURE=0.7 AGENT_MAX_TOKENS=512 AGENT_REASONING_EFFORT=none
export OPENAI_API_KEY=<private-key>
/tmp/ai-agent-week04-venv/bin/python submissions/26510358/week-04/run.py --check
/tmp/ai-agent-week04-venv/bin/python submissions/26510358/week-04/run.py
/tmp/ai-agent-week04-venv/bin/python submissions/26510358/week-04/verify_results.py
python3 scripts/check_week04.py submissions/26510358/week-04
```

기존 비공개 dotenv를 쓰려면 `--env-file /private/path/.env`를 `run.py` 호출에 붙일 수 있다. 실행기는 결과 파일이 이미 있으면 완료한 행을 다시 돌리지 않는다. 새 36개 실행은 별도 복사본의 제출 폴더에서 시작하면 된다. 각 로그의 첫 줄에 모델 설정, Python·SDK 버전, 시나리오 SHA-256을 기록했다.

## 2. 실제 결과

[results.csv](results.csv)의 본 실행 36개를 집계했다. 예비 실행 4개 행은 별도 [pilot-results.csv](pilot-results.csv)에 보존했고 아래 집계에 포함하지 않았다. 36개 에피소드에서 API 중단, 형식 오류, 한도 위반은 없었다. `format_errors=0`은 읽을 수 있었다는 뜻이며 reader의 의미 오독이 없었다는 뜻은 아니다.

| 조건 | correct / 12 | deal / no_deal / open | violation | 평균 turns | format_errors | reader_calls | 전체 모델 호출 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `free` | 2/12 | 2 / 0 / 10 | 0 | 7.50 | 0 | 90 | 180 |
| `tagged` | 4/12 | 4 / 0 / 8 | 0 | 7.08 | 0 | 48 | 133 |
| `structured` | 2/12 | 2 / 0 / 10 | 0 | 7.83 | 0 | 0 | 94 |

`reader_calls`는 판독을 위한 추가 API 요청이고, 전체 모델 호출에는 buyer·seller 요청도 포함된다. 거래 불가능한 두 시나리오의 18개 에피소드는 전부 `open`으로 끝나 `correct=0`이었다. 여덟 턴 안에 `refuse`가 나오지 않아 `no_deal`은 0건이었다.

### 에피소드 전체

`note`의 호출·토큰 수는 해당 에피소드의 실제 API 응답에서 얻었다. 빈 `price`는 거래가 없음을 뜻한다.

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---|
| 1 | free | bike | 1 | deal | 135 | 1 | 0 | 4 | 0 | 4 | model_calls=8;input_tokens=1452;output_tokens=103 |
| 1 | free | textbook | 1 | open |  | 0 | 0 | 8 | 0 | 8 | model_calls=16;input_tokens=3520;output_tokens=241 |
| 1 | free | keyboard | 0 | open |  | 0 | 0 | 8 | 0 | 8 | model_calls=16;input_tokens=3472;output_tokens=235 |
| 1 | free | laptop | 0 | open |  | 0 | 0 | 8 | 0 | 8 | model_calls=16;input_tokens=3212;output_tokens=200 |
| 2 | free | bike | 1 | open |  | 0 | 0 | 8 | 0 | 8 | model_calls=16;input_tokens=3366;output_tokens=220 |
| 2 | free | textbook | 1 | open |  | 0 | 0 | 8 | 0 | 8 | model_calls=16;input_tokens=3492;output_tokens=236 |
| 2 | free | keyboard | 0 | open |  | 0 | 0 | 8 | 0 | 8 | model_calls=16;input_tokens=3285;output_tokens=211 |
| 2 | free | laptop | 0 | open |  | 0 | 0 | 8 | 0 | 8 | model_calls=16;input_tokens=3202;output_tokens=198 |
| 3 | free | bike | 1 | deal | 125 | 1 | 0 | 6 | 0 | 6 | model_calls=12;input_tokens=2297;output_tokens=154 |
| 3 | free | textbook | 1 | open |  | 0 | 0 | 8 | 0 | 8 | model_calls=16;input_tokens=3484;output_tokens=240 |
| 3 | free | keyboard | 0 | open |  | 0 | 0 | 8 | 0 | 8 | model_calls=16;input_tokens=3217;output_tokens=199 |
| 3 | free | laptop | 0 | open |  | 0 | 0 | 8 | 0 | 8 | model_calls=16;input_tokens=3484;output_tokens=236 |
| 4 | tagged | bike | 1 | deal | 130 | 1 | 0 | 4 | 0 | 2 | model_calls=6;input_tokens=1202;output_tokens=93 |
| 4 | tagged | textbook | 1 | deal | 40 | 1 | 0 | 6 | 0 | 3 | model_calls=9;input_tokens=1875;output_tokens=128 |
| 4 | tagged | keyboard | 0 | open |  | 0 | 0 | 8 | 0 | 4 | model_calls=12;input_tokens=2600;output_tokens=164 |
| 4 | tagged | laptop | 0 | open |  | 0 | 0 | 8 | 0 | 4 | model_calls=12;input_tokens=2660;output_tokens=176 |
| 5 | tagged | bike | 1 | deal | 130 | 1 | 0 | 4 | 0 | 2 | model_calls=6;input_tokens=1194;output_tokens=89 |
| 5 | tagged | textbook | 1 | deal | 45 | 1 | 0 | 7 | 0 | 4 | model_calls=11;input_tokens=2357;output_tokens=165 |
| 5 | tagged | keyboard | 0 | open |  | 0 | 0 | 8 | 0 | 5 | model_calls=13;input_tokens=2835;output_tokens=195 |
| 5 | tagged | laptop | 0 | open |  | 0 | 0 | 8 | 0 | 4 | model_calls=12;input_tokens=2660;output_tokens=176 |
| 6 | tagged | bike | 1 | open |  | 0 | 0 | 8 | 0 | 7 | model_calls=15;input_tokens=3110;output_tokens=215 |
| 6 | tagged | textbook | 1 | open |  | 0 | 0 | 8 | 0 | 5 | model_calls=13;input_tokens=2845;output_tokens=196 |
| 6 | tagged | keyboard | 0 | open |  | 0 | 0 | 8 | 0 | 4 | model_calls=12;input_tokens=2680;output_tokens=180 |
| 6 | tagged | laptop | 0 | open |  | 0 | 0 | 8 | 0 | 4 | model_calls=12;input_tokens=2660;output_tokens=176 |
| 7 | structured | bike | 1 | deal | 140 | 1 | 0 | 7 | 0 | 0 | model_calls=7;input_tokens=1843;output_tokens=113 |
| 7 | structured | textbook | 1 | open |  | 0 | 0 | 8 | 0 | 0 | model_calls=8;input_tokens=2188;output_tokens=130 |
| 7 | structured | keyboard | 0 | open |  | 0 | 0 | 8 | 0 | 0 | model_calls=8;input_tokens=2184;output_tokens=132 |
| 7 | structured | laptop | 0 | open |  | 0 | 0 | 8 | 0 | 0 | model_calls=8;input_tokens=2181;output_tokens=131 |
| 8 | structured | bike | 1 | open |  | 0 | 0 | 8 | 0 | 0 | model_calls=8;input_tokens=2172;output_tokens=128 |
| 8 | structured | textbook | 1 | deal | 45 | 1 | 0 | 7 | 0 | 0 | model_calls=7;input_tokens=1850;output_tokens=113 |
| 8 | structured | keyboard | 0 | open |  | 0 | 0 | 8 | 0 | 0 | model_calls=8;input_tokens=2181;output_tokens=131 |
| 8 | structured | laptop | 0 | open |  | 0 | 0 | 8 | 0 | 0 | model_calls=8;input_tokens=2181;output_tokens=131 |
| 9 | structured | bike | 1 | open |  | 0 | 0 | 8 | 0 | 0 | model_calls=8;input_tokens=2172;output_tokens=128 |
| 9 | structured | textbook | 1 | open |  | 0 | 0 | 8 | 0 | 0 | model_calls=8;input_tokens=2180;output_tokens=128 |
| 9 | structured | keyboard | 0 | open |  | 0 | 0 | 8 | 0 | 0 | model_calls=8;input_tokens=2184;output_tokens=132 |
| 9 | structured | laptop | 0 | open |  | 0 | 0 | 8 | 0 | 0 | model_calls=8;input_tokens=2181;output_tokens=131 |

## 3. FIPA-ACL과의 비교

FIPA 측 설명은 강의에서 지정한 [ACL Message Structure Specification (SC00061G)](https://web.archive.org/web/2023/http://www.fipa.org/specs/fipa00061/SC00061G.html)과 [Communicative Act Library (SC00037J)](https://web.archive.org/web/2023/http://www.fipa.org/specs/fipa00037/SC00037J.pdf)를 기준으로 한다. 이 실습은 전체 FIPA 메시지 구조나 대화 프로토콜을 구현한 것이 아니라 네 행위의 가격 협상만 비교한다.

| 항목 | FIPA-ACL | free | tagged | structured |
|---|---|---|---|---|
| Illocutionary force 위치 | 메시지의 `performative` 필드 | 평문 문맥에서 reader가 추론 | 선두 괄호 태그를 정규식으로 읽음 | JSON `performative` 필드 |
| Content 언어 | `:language`와 `:ontology`가 해석 맥락을 선언, 내용은 별도의 표현식 | 제한 없는 영어 가격 문장 | 태그 뒤 영어 가격 문장 | `content.price` 정수 또는 null |
| Content 해석자 | 선언된 언어·온톨로지를 아는 수신자 | LLM reader가 매 메시지 해석 | 정규식이 태그, LLM reader가 제안 가격 해석 | Python JSON·타입 검사기 |
| 대화 종료 | ACL 형식 자체는 종료를 정하지 않음, 별도 상호작용 절차가 필요 | 이 실험의 reader가 수락/거절을 식별하면 종료, 8턴이면 open | 태그로 `accept-proposal`/`refuse`를 받으면 종료, 8턴이면 open | 필드로 `accept-proposal`/`refuse`를 받으면 종료, 8턴이면 open |
| Sincerity 보장 | 행위의 FP/RE는 의도·믿음에 관한 의미 조건이며 메시지 문법만으로 진실성을 검증하지 못함 | 프롬프트와 결과 검사가 있을 뿐 보장 없음 | 태그가 실제 의도·한도 준수를 보장하지 않음 | 타입 검사가 의도·한도 준수를 보장하지 않음 |
| 메시지 판독 비용 | 문법 파싱은 모델 호출 0회, 온톨로지 해석·운영 비용은 별개 | 메시지당 reader 1회, 총 90회 | 태그는 정규식, 제안 가격에만 reader, 총 48회 | JSON parser만 사용, reader 0회 |
| 관찰·가능한 실패 | 온톨로지 불일치, 의미 조건 검증 곤란 | 다중 가격 문장 오독 가능; 이번에는 형식 오류 0건이나 행위 오독 사례 있음 | 태그와 본문이 다를 수 있고 역제안이 거절 태그 안에 묻힐 수 있음; 이번에는 형식 오류 0건 | 문법상 유효해도 잘못된 행위·가격을 담을 수 있음; 이번에는 형식 오류 0건 |

## 4. 로그 근거 해석

`tagged`는 `free`보다 reader 호출이 90회에서 48회로 줄고 correct가 2/12에서 4/12로 늘었지만, 세 번의 작은 반복만으로 태그가 성공률을 올렸다고 단정할 수 없다. [free-01.txt L51–55](logs/free-01.txt#L51-L55)에서 seller의 “I reject your offer of 35. I can sell the textbook for 50.”을 reader가 `reject-proposal`로 읽어 새 제안 50을 상태에 반영하지 않았다. 같은 로그의 [L61–65](logs/free-01.txt#L61-L65)는 40 거절 뒤 45 제안을 `propose`로 읽었다. 두 경우 모두 JSON 라벨은 파싱돼 `format_errors=0`이므로 이 지표는 의미 오독을 잡지 못한다. `tagged`의 [tagged-04.txt L4–21](logs/tagged-04.txt#L4-L21)은 4턴 거래를 태그와 제안 가격 reader 2회로 기록했고, [L24–50](logs/tagged-04.txt#L24-L50)은 좁은 교집합의 40 거래를 6턴·reader 3회로 기록했다. `structured`는 reader 호출을 0으로 만들었으나 correct는 2/12이고 평균 turns는 7.83으로 가장 길었다. [structured-08.txt L4–27](logs/structured-08.txt#L4-L27)의 bike에서는 양쪽이 135와 140까지 접근했어도 모두 `propose`만 보내 8턴 `open`으로 끝났다. 거래 불가능한 18개 에피소드 모두 `refuse` 없이 `open`이었고 violation은 세 조건 모두 0이다. 즉 이번 비교에서 명시적 형식은 판독 호출 비용을 줄였지만, 에이전트의 수락·철회 결정이나 비공개 한도 준수까지 보장하지는 않았다.

예비 실행은 첫 코드에서 reader가 `accept-proposal`과 함께 가격을 출력하면 판독 실패로 처리했다. [pilot-free-01.txt L74–79](logs/pilot-free-01.txt#L74-L79)에서 seller가 40 수락을 말했는데 reader의 `{"performative":"accept-proposal","price":40}`을 거부해 거래가 `open`으로 남았다. 이 4개 완료 에피소드와 중단된 다음 실행의 원본 로그를 [pilot-results.csv](pilot-results.csv), [pilot-free-01.txt](logs/pilot-free-01.txt), [pilot-free-02.txt](logs/pilot-free-02.txt)에 보존했다. `d1c1a8c`에서 비제안 행위가 문장 속 가격을 가질 수 있도록 reader 계약과 파서를 고친 뒤, 본 실행 36개를 같은 코드로 처음부터 다시 수집했다. 시나리오와 한도는 바꾸지 않았다.
