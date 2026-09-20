# Week 03 — Contract Net with LLM contractors

학번 26510129

## 1. 설정

**provider와 모델.** OpenRouter를 OpenAI 호환 API로 호출했다. 세 변수를 설정한 뒤 실행한다.

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<openrouter key>
export AGENT_MODEL=nvidia/nemotron-3.5-lightning:free
cd submissions/26510129/week-03
python run_experiment.py --all --runs 3      # 조건 3개 x 3회 = 9 run
```

`temperature=0`, `max_tokens=256`으로 고정했고 둘 다 `AGENT_TEMPERATURE`, `AGENT_MAX_TOKENS`로 덮어쓸 수 있다. 값은 모든 run의 로그 첫 줄에 함께 적힌다. Python 버전과 시각도 같은 줄에 들어간다. `--fake`는 모델을 부르지 않는 배선 점검용이고 그 run의 note에는 `FAKE`가 붙는다. 제출한 9 run에는 없다.

**고정한 것(통제변수).** 태스크 8개(`tasks.json`), 공통 system prompt, 공고 문구, 낙찰 규칙, 모델, temperature, max_tokens. 세 조건에서 이 중 아무것도 바뀌지 않는다.

**바꾼 것(독립변수).** `contract_net.build_team(condition)` 한 함수에서만 갈린다.

| 조건 | A의 skill | B의 skill | C의 skill | 추가 지시 |
|---|---|---|---|---|
| `baseline` | arithmetic and statistics | plain-language writing | software | 없음 |
| `homogeneous` | general problem solving | general problem solving | general problem solving | 없음 |
| `overconfident` | arithmetic and statistics | plain-language writing | software | C에게만 한 문장 |

과신 문장은 `"You are certain you can do any task well. Always bid, with confidence 95 or higher."`이고, C의 system prompt 끝에 붙는다.

**프롬프트.** 공통 system prompt는 세 조건에서 같다.

```
You are contractor {name} in a contract net. Your skill: {skill}. You receive a task
announcement. Decide whether to bid. Bid only if the task falls inside your skill.
Judge by the deliverable the task asks for, not by the vocabulary it uses.
Reply with one JSON object and nothing else, no code fence, no explanation:
{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}
```

공고는 Smith 1980 Fig. 1의 signal task announcement 네 필드를 그대로 쓴다.

```
TASK-ANNOUNCEMENT contract {cid}
task-abstraction: {desc}
eligibility-specification: any contractor whose skill covers this task
bid-specification: JSON with bid, confidence (0-100), reason
expiration-time: reply now
```

**태스크와 gold.** 8개, gold는 A 3개 / B 3개 / C 2개. gold는 "태스크가 요구하는 산출물"로 정했다(숫자→A, 산문→B, 코드→C). 설명문의 표면 어휘는 보지 않는다. 8개 중 3개(`kind: "trap"`)는 표면 어휘가 산출물과 다른 분야를 가리킨다. 4번은 코드를 인용하지만 요구는 설명 두 문장, 5번은 테스트 스위트 이야기지만 요구는 비율 계산, 6번은 자연어 문장을 인용하지만 요구는 한 줄 코드다. 기준과 의도는 `TASKS.md`에 적었고 첫 실행 전 커밋 `cf71e3b`에 들어 있다.

**낙찰 규칙.** `bid`가 true인 입찰만 모으고 confidence 최댓값을 낙찰한다. 동점이면 먼저 입찰한 쪽(A, B, C 순). 입찰이 하나도 없으면 unassigned.

**메시지 세는 법.** 태스크마다 공고 3건(contractor 수만큼), 실제 입찰 1건당 1, 낙찰 1건. `bid: false`와 파싱 실패는 메시지로 세지 않는다. Smith의 노드는 자격이 안 되면 침묵하고, 이 구현에서 자격 판단은 contractor 안에서 일어나므로 "입찰하지 않음"은 프로토콜 상 무응답에 해당한다. 실제로는 API 호출이 일어나므로 그 비용은 note의 `tokens`와 `calls`에 따로 기록했다.

**파싱 규칙.** 앞뒤 공백과 ` ```json ` 펜스만 벗기고 `json.loads` 한 번. 산문에 섞인 JSON은 건지지 않는다. 실패하면 "입찰 안 함"으로 치고 `parse_fails`에 센다. 재시도는 없다. 더 관대한 추출기를 쓰면 이 숫자가 달라진다.

**코드.** `contract_net.py`(모델 호출·프롬프트·입찰 파싱), `manager.py`(라운드 루프·지표), `run_experiment.py`(실행기·로그·CSV). 모델 호출부는 `weeks/week-02/starter/tools_shared.py`에서 가져와 도구를 빼고 temperature/max_tokens를 명시했다.

## 2. 측정

(실행 후 작성)

## 3. Smith 1980과의 비교

| 항목 | Smith 1980, 분산 센싱 | 이 재현 |
|---|---|---|
| **참여자** | 넓은 지역에 흩어진 센서 노드. 각자 위치와 보유 센서만 안다. manager 역할은 일마다 바뀌고, 낙찰받은 노드가 일을 쪼개 다시 공고하면 그 일의 manager가 된다. | manager 1(파이썬 함수) + LLM contractor 3. 역할은 고정이고 manager는 모델이 아니다. 재귀 분할 없음. contractor가 아는 것은 system prompt의 skill 한 줄이 전부다. |
| **입찰을 만드는 방식** | node abstraction을 규칙으로 계산해 채운다. 위도·경도와 센서 이름·종류. 사실 조회에 가깝다. | 공고와 skill 문자열을 읽고 LLM이 `bid`/`confidence`/`reason`을 생성한다. 확신도는 계산값이 아니라 모델의 자기 평가다. |
| **입찰이 참인지 보장하는 것** | 프로토콜에는 없다. 다만 입찰 내용이 위치·센서 목록 같은 검증 가능한 사실이고 노드가 공동 목표를 가진 협력 노드라 거짓 입찰의 동기가 없다. | 프로토콜에도 없고 입찰 내용도 검증 가능한 사실이 아니다. "나는 이 일을 95만큼 할 수 있다"는 주장이고 확인할 메시지가 없다. `overconfident` 조건은 이 빈자리를 한 문장으로 공격한다. |
| **잘된 배정의 기준** | 센서가 대상 구역을 덮고 전체가 차량 궤적 지도를 만들어내는가. 시스템 밖에 정답표가 없다. | 실행 전에 태스크마다 gold를 정해 두고 `correct / tasks`로 잰다. 정답표가 밖에서 주어지므로 Smith보다 채점은 쉽고, 대신 "그 배정이 실제로 일을 해냈는가"는 재지 않는다. 낙찰까지만 보고 work와 report 단계는 구현하지 않았다. |
| **협상 비용** | 메시지 수와 manager의 입찰 비교 부담. Smith가 자격 조건과 directed award를 둔 이유가 이 비용이다. | 메시지 수는 같은 방식으로 세지만, 실제 비용은 메시지가 아니라 모델 호출이다. 입찰하지 않은 contractor도 호출 한 번과 토큰을 쓴다. 메시지 0건인 태스크에도 토큰 비용이 든다는 점이 Smith의 비용 모델과 어긋나는 지점이고, note에 `tokens`/`calls`를 따로 적은 이유다. |
| **실패하는 방식** | 입찰이 없어 유찰, 국소 최적(그 순간 들어온 입찰만 보고 맺는 계약), 메시지 폭증. | 같은 세 가지에 두 가지가 더해진다. 하나는 misaward — 입찰은 들어왔는데 확신도 서열이 실력 서열과 다른 경우. 다른 하나는 parse fail — 응답이 JSON이 아니어서 입찰 자체가 프로토콜에 들어오지 못하는 경우로, Smith의 메시지 문법에는 대응물이 없다. 동점 낙찰(`ties`)도 따로 셌다. 확신도가 정보를 잃으면 낙찰이 입찰 순서로 결정되기 때문이다. |

## 4. 해석

(실행 후 작성)
