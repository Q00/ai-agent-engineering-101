# Week 03 — 블랙잭 Contract Net 재현 (manager 1 + LLM contractor 3)

## 1. 설정

### 목적

Smith(1980)의 공고 → 입찰 → 낙찰 프로토콜을 manager 1명과 LLM contractor 3명으로 재현하고, contractor의 입찰을 고정 규칙이 아닌 LLM의 판단으로 만들었을 때 배정 품질이 어떻게 달라지는지 측정한다. 태스크 도메인은 블랙잭의 판단 장면이다.

### 구성

| 파일            | 역할                                                                                                                                                |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tasks.json`    | 태스크 12개. `id`, `desc`, `gold` 외에 `player_cards`, `dealer_up`, `bankroll`, `last_result`를 둔다. gold는 A/B/C 각 4개이며 첫 실행 전에 커밋했다 |
| `contractor.py` | contractor 정의, 조건별 팀 생성(`make_team`), 입찰 JSON 판정(`parse_bid`)                                                                           |
| `manager.py`    | `run_round`: 공고, 입찰 수집, 낙찰, 지표 계산                                                                                                       |
| `llm.py`        | week-02 starter의 `Meter`/`Chat`을 복사해 도구를 제거한 모델 호출 계층                                                                              |
| `run.py`        | 조건을 번갈아 실행하고 `results.csv`, `logs/`, `smoke/`에 기록                                                                                      |

### contractor 역할 (baseline)

| 이름 | 담당                                               | 성향     | gold 태스크 |
| ---- | -------------------------------------------------- | -------- | ----------- |
| A    | 페어가 아니고 합이 9·10·11이 아닌 손패의 hit/stand | 신중형   | 1–4         |
| B    | 페어, 또는 하드 합 9·10·11의 double/split          | 공격형   | 5–8         |
| C    | insurance 제안, 새 판 베팅 크기                    | 기댓값형 | 9–12        |

### 모델과 환경

- provider: Anthropic (Messages API)
- model: `claude-haiku-4-5-20251001`
- temperature: 지정 불가. 설치된 `anthropic==1.4.0`의 `messages.create()`가 temperature 인자를 받지 않아 provider 기본값으로 호출했다. 코드는 실행 전에 SDK 시그니처를 확인해 이 사실을 각 로그 첫 줄에 기록한다
- max_tokens: 1024
- python 3.11.9, Windows PowerShell
- 호출 방식: 공고 1건당 contractor 1명에게 system prompt 1개 + user 메시지 1개로 1회 호출. 호출마다 새 대화를 만들므로 contractor는 이전 태스크나 다른 contractor의 입찰을 보지 못한다
- 재시도: 속도 제한(429)과 서버 오류(5xx)만 최대 3회. 코드 오류와 인증 오류는 즉시 실행 중단으로 기록한다

### 프롬프트

system prompt 템플릿 (`contractor.py`의 `BID_SYSTEM`):

```
You are contractor {name} in a contract net. Your skill: {skill} You receive a task announcement. Decide whether to bid. Bid only if the task falls inside your skill. Reply with one JSON object and nothing else: {"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}
```

skill 문자열:

```
A: blackjack hit or stand choices only, for a hand that is not a pair and does not total 9, 10 or 11. Your style is cautious: you try not to bust. Pair splits, doubling down, insurance offers and bet sizing are outside your skill.
B: blackjack double down and pair split choices only: a pair, or a hard total of 9, 10 or 11. Your style is aggressive: you press your advantage. Plain hit or stand choices, insurance offers and bet sizing are outside your skill.
C: blackjack insurance choices when the dealer offers insurance, and bet sizing before a new hand, only. Your style is probability-focused: you decide by expected value. Hit, stand, double down and split choices are outside your skill.
```

공고 (user 메시지, Smith 1980의 공고 필드를 따름):

```
TASK-ANNOUNCEMENT contract {id}
task-abstraction: {desc}
eligibility-specification: any contractor whose skill covers this task
bid-specification: JSON with bid, confidence (0-100), reason
expiration-time: reply now
```

### 조건

| 조건          | baseline 대비 바뀌는 것                                                                                            |
| ------------- | ------------------------------------------------------------------------------------------------------------------ |
| baseline      | 없음                                                                                                               |
| homogeneous   | 세 명의 skill 문자열을 모두 `general problem solving.`으로 교체 (분야와 성향이 함께 사라짐)                        |
| overconfident | C의 system prompt 끝에 ` You are certain you can do any task well. Always bid, with confidence 95 or higher.` 추가 |

### 낙찰 규칙과 지표

- `bid`가 true인 답만 모아 confidence가 가장 높은 contractor에게 낙찰. 동점이면 먼저 답한 쪽(A → B → C 순)
- 답 전체가 JSON 객체 하나일 때만 입찰로 인정한다 (```json 코드 블록만 허용). 그 외는 입찰하지 않은 것으로 보고 `parse_fails`에 센다
- messages = 공고(contractor 수 3) + 입찰(`bid=true` 1건당 1) + 낙찰(1)
- correct: gold와 일치한 낙찰 / misawards: gold와 다른 낙찰 / unassigned: `bid=true`가 하나도 없는 태스크

### skill 경계를 넣은 경위

첫 smoke 실행에서는 경계 문장 없이 "blackjack hit or stand decisions" 식으로만 적었고, 세 명 모두 거의 모든 태스크에 입찰했다 (`smoke/smoke-01-before-boundary.txt`). baseline이 기준선 역할을 하도록 각 skill 끝에 "... are outside your skill."을 붙였고, 이후 smoke에서 baseline이 3/3 정배정, messages 최소값(15)을 기록했다 (`smoke/smoke-*-after-boundary.txt`). gold는 바꾸지 않았다.

### 실행 방법

week-03 폴더에서:

```powershell
python -m pip install anthropic
$env:ANTHROPIC_API_KEY = "<your key>"
$env:AGENT_MODEL = "claude-haiku-4-5-20251001"
python run.py                                   # 세 조건 x 3회
python run.py --smoke --tag test                # 태스크 3개로 동작 확인, smoke/에만 저장
python ..\..\..\scripts\check_week03.py .        # 구조 검사
```

`run.py`는 baseline → homogeneous → overconfident 순서로 번갈아 실행하며, `results.csv`의 마지막 run 번호 다음부터 이어서 기록한다. 이번 결과는 `python run.py`를 두 번 실행한 것이다 (run 1–9, run 10–18).

## 2. 결과

### 실행별 결과 (`results.csv`)

| run | condition     | tasks | correct | messages | unassigned | misawards | parse_fails |
| --- | ------------- | ----- | ------- | -------- | ---------- | --------- | ----------- |
| 1   | baseline      | 12    | 11      | 58       | 1          | 0         | 2           |
| 2   | homogeneous   | 12    | 3       | 84       | 0          | 9         | 0           |
| 3   | overconfident | 12    | 11      | 58       | 1          | 0         | 2           |
| 4   | baseline      | 12    | 11      | 58       | 1          | 0         | 1           |
| 5   | homogeneous   | 12    | 4       | 84       | 0          | 8         | 0           |
| 6   | overconfident | 12    | 11      | 58       | 1          | 0         | 1           |
| 7   | baseline      | 12    | 10      | 56       | 2          | 0         | 1           |
| 8   | homogeneous   | 12    | 3       | 84       | 0          | 9         | 0           |
| 9   | overconfident | 12    | 10      | 56       | 2          | 0         | 3           |
| 10  | baseline      | 12    | 10      | 56       | 2          | 0         | 2           |
| 11  | homogeneous   | 12    | 4       | 83       | 0          | 8         | 0           |
| 12  | overconfident | 12    | 10      | 56       | 2          | 0         | 1           |
| 13  | baseline      | 12    | 11      | 58       | 1          | 0         | 2           |
| 14  | homogeneous   | 12    | 4       | 84       | 0          | 8         | 0           |
| 15  | overconfident | 12    | 11      | 58       | 1          | 0         | 2           |
| 16  | baseline      | 12    | 10      | 56       | 2          | 0         | 1           |
| 17  | homogeneous   | 12    | 4       | 82       | 0          | 8         | 0           |
| 18  | overconfident | 12    | 10      | 59       | 1          | 1         | 1           |

중단된 실행은 없다.

### 조건별 평균 (각 6회)

| condition     | correct / 12 | messages | unassigned | misawards | parse_fails 합계 |
| ------------- | ------------ | -------- | ---------- | --------- | ---------------- |
| baseline      | 10.50        | 57.00    | 1.50       | 0.00      | 9                |
| homogeneous   | 3.67         | 83.50    | 0.00       | 8.33      | 0                |
| overconfident | 10.50        | 57.50    | 1.33       | 0.17      | 10               |

messages의 이론적 범위는 36(입찰 0건)부터 84(모든 contractor가 모든 태스크에 입찰)까지이며, 모든 태스크가 gold contractor 한 명의 입찰로 낙찰되면 60이다.

## 3. Smith(1980) 분산 센싱과의 비교

| 항목               | Smith 1980 분산 센싱                                                                          | 이번 재현                                                                                                                                                       |
| ------------------ | --------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 노드               | 센서와 처리 능력을 가진 노드들. 한 노드가 상황에 따라 manager도 contractor도 된다             | manager는 Python 코드 1개, contractor는 system prompt만 다른 LLM 호출 3개. 역할은 고정                                                                          |
| 입찰 생성          | 노드가 공고의 eligibility 조건을 자기 상태(위치, 센서 종류, 가용 자원)와 대조하는 절차로 계산 | LLM이 공고 문장을 읽고 자연어 skill과 비교해 입찰 여부와 confidence를 자기보고                                                                                  |
| 입찰의 정직성 보장 | 설계자가 만든 절차를 협력적 노드가 그대로 실행하므로 입찰은 계산 결과와 일치                  | 보장 장치 없음. prompt 준수에만 의존한다. bid 값과 reason이 모순되는 답이 나왔고, 엄격한 JSON 판정이 그중 일부를 우연히 걸러냈다                                |
| 배정 품질의 의미   | 태스크를 실제로 수행할 수 있는 노드에 배정되어 전체 문제 해결이 진행되는 것                   | 낙찰자가 사전에 정한 gold와 일치하는 것 (correct / misaward / unassigned)                                                                                       |
| 협상 비용          | 공고 방송과 입찰에 드는 통신량. focused addressing 등으로 공고 대상을 줄여 절감               | 실행당 메시지 56–84건, 모델 호출 36회, 토큰 약 8천–1만. 입찰이 정직할수록 메시지가 최소값에 가까워진다                                                          |
| 실패 방식          | 적격 노드가 없거나 바쁠 때의 유찰과 재공고, 통신 과부하                                       | 자격 조건의 의미 해석 오류(언어, 행동 기준 판단)로 인한 유찰, bid와 reason의 모순, 보정되지 않은 confidence로 인한 오배정, 능력이 같을 때 동점 규칙에 의한 쏠림 |

## 4. 해석

homogeneous는 세 지표를 모두 움직인 유일한 조건이다. 능력 문자열이 같아지자 세 contractor가 거의 모든 태스크에 같은 confidence로 입찰해 messages가 최대값 84에 가까워졌고(평균 83.5), 동점은 먼저 답한 A에게 낙찰되므로 correct가 A의 담당 태스크 수(4) 이하인 3–4로 떨어졌다. smoke 로그에서 세 명이 task 5와 task 9에 모두 `confidence=75`로 입찰하고 `[award] task 9 -> A (confidence 75, gold C) MISAWARD`가 된 것이 이 구조를 보여준다. 반대로 overconfident는 baseline과 거의 구분되지 않았다. C는 6회 모두 자기 담당인 task 9–12에만 `confidence=95`로 입찰했고(예: run 3, "Task asks for bet sizing before a new hand ... directly within my blackjack bet sizing skill"), 과신 지시는 입찰 범위가 아니라 confidence 값만 올렸다. 같은 과신 문장이 경계 문장 없는 smoke에서는 `[award] task 1 -> C (confidence 95, gold A) MISAWARD`를 만들었으므로, 이번 결과는 과신 지시가 무력하다는 뜻이 아니라 prompt 안의 명시적 자격 조건이 과신 지시보다 우선했다는 뜻으로 읽는다. baseline과 overconfident에 남은 오류는 조건과 무관하게 같은 태스크에서 반복되었다. task 8(10, 10)은 baseline과 overconfident의 12회 실행 모두에서 유찰되었는데, B가 "a standing hand, not a double down or pair split situation"이라며 분야가 아니라 최적 행동을 기준으로 자격을 판단했기 때문이다. task 6(5, 6)에서는 B가 "falls within my skill, but the Korean language and context suggest this may require broader decision-making"이라며 입찰을 포기했다. 유일한 오배정인 run 18 task 3은 B가 hard 16에 대해 "this specific scenario requires hitting"이라고 스스로 쓰면서도 `confidence=92`로 입찰해 A(85)를 이긴 경우다. Smith의 프로토콜은 입찰이 노드 상태의 계산 결과라는 전제 위에 있어서, 자격 조건의 해석이 바뀌거나 입찰 내용이 자기모순일 때 이를 검증하는 단계가 없다. 이번 재현에서 판단형 입찰은 명시적 경계가 있을 때 오배정을 거의 없앴지만, 그 대가로 오류가 오배정에서 유찰로 옮겨갔고 confidence는 적합도의 척도로 기능하지 않았다.

## 5. 실패 사례

### 사례 1: 언어 문맥을 자격 문제로 해석 (task 6, gold B)

````
[announce] task 6 -> A, B, C | 내 카드 5, 6 / 딜러 업카드 10. 어떻게 할까?
  [parse-fail] A: ```json {"bid": true, "confidence": 92, "reason": "Hand totals 11, which is excluded from my skill scope - I only handle hands that are NOT 9, 10, or 11. Cannot...
  [bid] B: bid=False confidence=95 reason=Task presents a hard 11 (5+6) vs dealer 10, which falls within my skill, but the Korean language and context suggest this may require broader decision-making be...
  [unassigned] task 6 (gold B)
````

(run 7) prompt는 영어, 태스크 설명은 한국어로 둔 설정이 입찰 판단에 개입했다. 6회 중 3회(run 7, 10, 16)의 baseline에서 이 태스크가 유찰되었다.

### 사례 2: 분야가 아니라 최적 행동으로 자격을 판단 (task 8, gold B)

```
[announce] task 8 -> A, B, C | 내 카드 10, 10 / 딜러 업카드 5. 어떻게 할까?
  [bid] A: bid=False confidence=95 reason=Task involves a pair (10, 10), which is outside my skill scope of non-pair hands only.
  [bid] B: bid=False confidence=95 reason=Task involves a pair of 10s (20 total), which is a standing hand, not a double down or pair split situation within my skill
  [unassigned] task 8 (gold B)
```

(run 7) gold는 "페어이므로 B"라는 분야 기준으로 정했지만, B는 "split할 상황이 아니므로 내 일이 아니다"라고 판단했다. 이는 gold 정의 자체가 자연어 skill 설명과 어긋날 수 있다는 한계이기도 하다.

### 사례 3: bid와 reason의 모순, 보정되지 않은 confidence (task 3, gold A)

```
[announce] task 3 -> A, B, C | 내 카드 9, 7 / 딜러 업카드 7. 어떻게 할까?
  [bid] A: bid=True confidence=85 reason=Hand totals 16 against dealer 7, non-pair, not 9/10/11: hit cautiously to avoid bust risk
  [bid] B: bid=True confidence=92 reason=Hard total of 16 vs dealer 7 requires decision analysis within my double down and pair split expertise, though this specific scenario requires hitting rather th...
  [award] task 3 -> B (confidence 92, gold A) MISAWARD
```

(run 18) 같은 유형의 모순은 다른 실행에서 `"bid": true`와 "I cannot bid" reason, 그리고 뒤따르는 자기 수정 문장(`Wait, ...`)으로 나타나 파싱 실패로 걸러졌다. 파싱 실패 19건은 모두 `"bid": true`와 "skill 밖"이라는 reason이 함께 나온 이 유형이었으며, JSON 부분만 추출하는 느슨한 판정을 썼다면 유찰 대신 오배정으로 집계되었을 것이다.

### 참고: confidence의 이중 의미

`bid=False`인 답도 대부분 `confidence=95`를 달았다. 같은 필드가 입찰할 때는 "수행 능력에 대한 확신", 입찰하지 않을 때는 "거절 판단에 대한 확신"으로 쓰였다. manager는 `bid=True`만 비교하므로 이번 지표에는 영향이 없었다.
