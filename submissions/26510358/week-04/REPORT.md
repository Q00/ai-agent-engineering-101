# Week 04 — 발화 행위 형식에 따른 buyer·seller 가격 협상
## 1. 설정과 재현
사용자 요청에 따라 Codex가 코드·실제 모델 실험·분석을 수행하고 PR #212를 작성함
[EXPERIMENT.md](EXPERIMENT.md)와 [scenarios.json](scenarios.json)은 첫 API 호출 전에 커밋함(`2f1a7ef`)
예비 실행의 판독 오류와 수정 과정도 아래에 기록함
[Week 04 강의·LAB](https://wpti.dev/ai-agent-engineering-101/week-04.html)과 [저장소 과제 명세](../../../weeks/week-04/README.md)의 데이터 계약을 따름
| 항목 | 고정값 |
|---|---|
| provider / API | OpenAI, Chat Completions, `https://api.openai.com/v1` |
| 모델 / 생성 | `gpt-5.6-luna`, temperature `0.7`, `reasoning_effort=none`, `max_completion_tokens=512` |
| 실행 환경 | Python 3.13.15, `openai==3.8.0`, `python-dotenv==1.2.3` |
| 조건 / 순서 | `free` → `tagged` → `structured`, 각 조건에서 `bike` → `textbook` → `keyboard` → `laptop`을 3회 |
| 공통 설정 | buyer와 seller에 동일한 모델·temperature·네 행위 설명, buyer 선공, 메시지 상한 8 |
| 판독 | `free`: 모든 메시지에 LLM reader 1회, `tagged`: 태그는 정규식·제안 가격만 LLM reader, `structured`: 엄격한 JSON parser만 사용 |
| 실패·재개 | 429/5xx/연결 실패는 최대 6번 지수 대기 재시도, 각 에피소드 직후 CSV flush, 이미 기록된 `(run, scenario)`은 건너뜀 |
역할·한도·공통 행위 설명은 [negotiation.py](negotiation.py)의 `ROLE`과 `COMMON`으로 고정함
조건별 차이는 아래 형식 문단으로 한정하고 reader에도 같은 모델과 temperature를 적용함
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
`free`는 전체 대화에서 마지막 발화를 reader로 판독함
`tagged`는 정규식으로 태그를 읽고, 제안 본문의 가격만 같은 reader로 판독함
파싱 실패 메시지도 상대에게 전달하지만 프로토콜 상태에는 반영하지 않음
`accept-proposal`은 상대의 마지막 유효 `propose` 가격으로 거래를 성립시킴
`correct=1`은 가능한 거래가 양쪽 한도 안에서 성립하거나 불가능한 거래가 명시적 `no_deal`로 끝난 경우임
`open`은 두 경우 모두 `correct=0`이며, `violation=1`은 거래 가격이 reserve 미만이거나 budget을 넘은 경우임
저장소 루트에서 API 키를 환경변수로 제공함
아래 비공개 `.env` 경로는 예시이며 파일과 키 값은 커밋하지 않음
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
비공개 dotenv를 쓰는 경우 `run.py`에 `--env-file /private/path/.env`를 전달할 수 있음
실행기는 완료된 행을 다시 호출하지 않으므로 새 36회 실험에는 별도 작업 복사본이 필요함
각 로그 첫 줄에 모델 설정·Python/SDK 버전·시나리오 SHA-256을 기록함
## 2. 실제 결과
[results.csv](results.csv)의 본 실행 36개를 집계함
예비 실행 4행은 [pilot-results.csv](pilot-results.csv)에 보존하고 아래 집계에서 제외함
본 실행에는 API 중단·형식 오류·한도 위반이 없었음
`format_errors=0`은 문법상 판독 성공만 뜻하며 의미 오독이 없었다는 뜻은 아님
| 조건 | correct / 12 | deal / no_deal / open | violation | 평균 turns | format_errors | reader_calls | 전체 모델 호출 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `free` | 2/12 | 2 / 0 / 10 | 0 | 7.50 | 0 | 90 | 180 |
| `tagged` | 4/12 | 4 / 0 / 8 | 0 | 7.08 | 0 | 48 | 133 |
| `structured` | 2/12 | 2 / 0 / 10 | 0 | 7.83 | 0 | 0 | 94 |
`reader_calls`는 판독을 위한 추가 API 요청 수이며 전체 모델 호출에는 buyer·seller 요청도 포함됨
거래 불가능한 시나리오의 18개 에피소드는 모두 `refuse` 없이 8턴 `open`으로 끝나 `correct=0`, `no_deal=0`이었음
### 에피소드 전체
`note`의 호출·토큰 수는 에피소드별 실제 API 응답에서 집계함
빈 `price`는 거래가 없음을 뜻함
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
FIPA 측 설명은 강의에서 지정한 [ACL Message Structure Specification (SC00061G)](https://web.archive.org/web/2023/http://www.fipa.org/specs/fipa00061/SC00061G.html)과 [Communicative Act Library (SC00037J)](https://web.archive.org/web/2023/http://www.fipa.org/specs/fipa00037/SC00037J.pdf)를 기준으로 함
전체 FIPA 메시지 구조나 대화 프로토콜 대신 네 행위로 구성한 가격 협상만 비교함
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
`tagged`는 `free`보다 reader 호출이 90회에서 48회로 줄었고 correct는 2/12에서 4/12로 늘었음
다만 조건별 3회 반복만으로 성공률 차이를 형식의 효과로 단정할 수 없음
[free-01.txt L51–55](logs/free-01.txt#L51-L55)의 seller는 35를 거절하며 50을 새로 제안했지만 reader는 `reject-proposal`로 읽어 새 제안을 상태에 반영하지 못함
같은 로그 [L61–65](logs/free-01.txt#L61-L65)의 40 거절 뒤 45 제안은 `propose`로 읽음
두 경우 모두 JSON 라벨은 파싱돼 `format_errors=0`이므로 형식 오류 수만으로 의미 오독을 알 수 없음

[tagged-04.txt L4–21](logs/tagged-04.txt#L4-L21)은 4턴 거래에 태그와 제안 가격 reader 2회를 사용함
같은 로그 [L24–50](logs/tagged-04.txt#L24-L50)은 좁은 한도 교집합에서 가격 40 거래를 6턴·reader 3회로 기록함
`structured`는 reader 호출이 0회였지만 correct는 2/12, 평균 turns는 7.83으로 세 조건 중 가장 길었음
[structured-08.txt L4–27](logs/structured-08.txt#L4-L27)의 bike는 양쪽이 135와 140까지 접근했어도 `propose`만 보내 8턴 `open`으로 종료됨
거래 불가능한 18개 에피소드도 모두 `refuse` 없이 `open`이었음
이 실험에서 명시적 형식은 판독 호출을 줄였지만 수락·철회 결정이나 비공개 한도 준수까지 보장하지는 못함
예비 실행에서는 reader가 `accept-proposal`과 함께 문장 속 가격을 반환하면 첫 파서가 이를 거부했음
[pilot-free-01.txt L74–79](logs/pilot-free-01.txt#L74-L79)의 seller는 40 수락을 말했지만 reader의 `{"performative":"accept-proposal","price":40}`을 거부해 결과가 `open`으로 남음
완료된 예비 실행 4행과 중단된 다음 실행의 로그는 [pilot-results.csv](pilot-results.csv), [pilot-free-01.txt](logs/pilot-free-01.txt), [pilot-free-02.txt](logs/pilot-free-02.txt)에 보존함
`d1c1a8c`에서 비제안 행위의 문장 속 가격을 허용하도록 reader 계약과 파서를 고친 뒤 본 실행 36개를 다시 수집함
시나리오와 한도는 유지함
## 보강 실험
발화 의미 재판독·순서를 섞은 추가 반복·공통 종료 지침·제안 상태 재생은 별도 [보강 보고서](followup/REPORT.md)에 기록함
원본 36개 결과와 위 집계는 유지함
