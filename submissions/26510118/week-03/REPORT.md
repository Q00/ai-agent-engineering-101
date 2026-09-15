# Week 03 — Contract Net with LLM contractors

manager 1명과 LLM contractor 3명으로 Smith(1980)의 공고·입찰·낙찰을 재현하고, 세 조건에서
배분 품질을 측정했다. 공고 순서(동점 처리)를 통제 변인으로 추가해 두 번 측정했다.

## 1. 설정

### 모델과 실행

| 항목 | 값 |
|---|---|
| provider | OpenAI 호환 (`ANTHROPIC_API_KEY` 미설정 → `model.py:PROVIDER`) |
| 모델 | `gpt-4o-mini` (`AGENT_MODEL` 미설정 시 기본값) |
| temperature | **0.7** — 조건당 3회 실행의 변동을 관찰하기 위해 0이 아닌 값을 고정 |
| 도구 | 없음. contract net은 공고당 모델 호출 1회만 필요하다 |
| 태스크 | 9개, `tasks.json` (`taskset=ed210c72`). 실행 전 커밋 |
| 실행 | 조건당 3회 × 3조건 × 2순서 = 18회 (run 28-45) |

```bash
pip install openai
export OPENAI_API_KEY=...            # 또는 ANTHROPIC_API_KEY
cd submissions/26510118/week-03
python run.py --runs 3               # Test 1: 고정 순서 (run 28-36)
python run.py --runs 3 --rotate      # Test 2: 회전 순서 (run 37-45)
```

`results.csv`와 `logs/`는 덮어쓰지 않고 이어 붙는다. 로그 첫 줄에 provider·모델·temperature가,
둘째 줄에 조건·순서 모드·`taskset` 해시가 기록된다. `taskset`은 `tasks.json`의 SHA-256 앞
8자리이며, **같은 해시끼리만 비교 가능**하다.

### 태스크 구성

```
A 계산   2개    "Compute 137 * 249 and return the number."
B 글쓰기 3개    "Rewrite this sentence in plain English for a 10-year-old: ..."
C 코드   3개    "Implement a Python function that returns a reversed copy of a list."
— 없음   1개    "Physically replace the toner cartridge in the third-floor printer ..."
```

`gold`는 그 태스크를 맡아야 하는 contractor다. 마지막 태스크는 `gold="none"` — 셋 중 누구의
능력에도 맞지 않으므로 **유찰(`unassigned`)이 정답 행동이고 어떤 낙찰이든 오배정**이다.

8개 중 2개는 정답이 명확하면서 엉뚱한 쪽을 유인하도록 만들었다. `id 7`("두 정수를 곱하는
파이썬 함수")은 곱셈으로 A를, `id 8`("평균이 오해를 부르는 이유를 한 단락으로")은 숫자로 A를
유인한다. 정답이 애매한 태스크로 만들면 `gold` 자체가 임의적이 되므로, **정답은 명확하되 오답이
매력적인** 형태를 택했다.

`gold`는 모델에게 전달되지 않는다. contractor가 받는 것은 자기 이름·능력(system)과 태스크
내용(user)뿐이고, manager는 `gold`를 알지만 낙찰에 쓰지 않고 채점에만 쓴다.

### 프롬프트 (전문)

**입찰 system prompt** — `{name}`, `{skill}`이 조건에 따라 채워진다:

> You are contractor {name} in a contract net. Your skill: {skill}. You receive a task announcement. Decide whether to bid. Bid only if the task falls inside your skill. Reply with one JSON object and nothing else, no prose and no code fences: {"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}

**`overconfident` 조건이 C에게만 덧붙이는 한 문장:**

> You are certain you can do any task well. Always bid, with confidence 95 or higher.

**공고 (user message)** — Smith 1980 Fig. 1의 네 필드:

```
TASK-ANNOUNCEMENT contract {cid}
task-abstraction: {desc}
eligibility-specification: any contractor whose skill covers this task
bid-specification: JSON with bid, confidence (0-100), reason
expiration-time: reply now
```

`confidence`의 의미는 **일부러 정의하지 않았다.** Smith의 프로토콜에는 입찰을 참되게 만드는
장치가 없고 그 빈칸이 이 실험의 대상이므로, 프롬프트로 정의해 메우면 측정 대상을 가리게 된다.

### 세 조건 (독립변수)

`run.py:make_contractors()` 한 함수에만 조건이 닿는다. 공고 형식, 입찰 프롬프트, 낙찰 규칙,
계측은 조건을 모른다.

| 조건 | 무엇이 달라지나 |
|---|---|
| `baseline` | A=`arithmetic`, B=`writing`, C=`coding` |
| `homogeneous` | 셋 다 `general problem solving`. 그 외 동일 |
| `overconfident` | baseline에 더해 C만 위 한 문장을 받는다. 그 외 동일 |

### 공고 순서 (추가 통제 변인)

낙찰은 확신도 최고에게 가고, **동점이면 먼저 입찰한 쪽**이 이긴다. Smith는 동점 처리를 규정하지
않았으므로 이는 순수한 구현 결정이다. 고정하면 팀 선두에게 체계적 이득이 생기므로 두 모드로
각각 측정했다.

```
Test 1  고정    모든 태스크에서 A → B → C
Test 2  회전    태스크마다 한 칸 이동. 9태스크라 세 명이 각각 3번씩 선두
```

### 지표

```
correct      gold 에게 간 태스크 수       ↑ 좋음.  최대 8 (태스크 9는 정답 담당자가 없음)
misawards    gold 가 아닌 곳에 간 수      ↓ 좋음
unassigned   아무도 입찰하지 않은 수       태스크 9는 여기가 정답
messages     공고 3 + 입찰한 수 + 낙찰 1   ↓ 좋음.  태스크당 3~7
```

실행마다 `correct + misawards + unassigned = 9`. `parse_fails`(JSON 파싱 실패)는
`results.csv`에 열이 없으므로 `note`에 기록했다 — 18회 전부 0이었다.

### 한계

1. **`gold`는 외부에서 부여한 기준이다.** Smith에게는 "정답 담당자"가 없고 자격 조건을 만족하면
   정당한 입찰자다. `correct`는 배분 품질을 재기 위해 우리가 밖에서 붙인 척도다.
2. **표본이 조건·순서당 3회다.** `homogeneous` 고정에서 correct가 2·1·4로 흔들렸다. 추세는
   읽을 수 있지만 조건 간 소수점 차이를 주장할 크기는 아니다.
3. **파싱 실패가 0건이라 그 실패 모드를 관찰하지 못했다.** `parse_bid`가 코드 펜스와 앞선 산문을
   벗겨내는 관대한 파서이므로, 몇 건이 그렇게 구제됐는지는 알 수 없다(성공한 파싱의 원문을
   로그에 남기지 않았다).
4. **이전 태스크 문구로 돌린 18회(run 10-27)가 `results.csv`에 함께 있다.** `taskset` 해시가
   없는 행이 그것이다. 4부에서 다루는 교란 발견의 근거이며, 본 분석에는 포함하지 않았다.

## 2. 측정 결과

### Test 1 — 고정 순서 (run 28-36)

| run | condition | correct | misawards | unassigned | messages |
|---:|---|---:|---:|---:|---:|
| 28 | baseline | 7 | 1 | 1 | 51 |
| 29 | baseline | 5 | 3 | 1 | 51 |
| 30 | baseline | 7 | 1 | 1 | 51 |
| 31 | homogeneous | 2 | 7 | 0 | 63 |
| 32 | homogeneous | 1 | 8 | 0 | 63 |
| 33 | homogeneous | 4 | 5 | 0 | 63 |
| 34 | overconfident | 7 | 1 | 1 | 52 |
| 35 | overconfident | 5 | 3 | 1 | 52 |
| 36 | overconfident | 5 | 3 | 1 | 53 |

| 조건 | correct | misawards | unassigned | messages |
|---|---:|---:|---:|---:|
| baseline | **6.33**/8 | 1.67 | 1.00 | 51.0 |
| homogeneous | 2.33/8 | **6.67** | 0.00 | **63.0** |
| overconfident | 5.67/8 | 2.33 | 1.00 | 52.3 |

### Test 2 — 회전 순서 (run 37-45)

| run | condition | correct | misawards | unassigned | messages |
|---:|---|---:|---:|---:|---:|
| 37 | baseline | 6 | 2 | 1 | 51 |
| 38 | baseline | 7 | 1 | 1 | 50 |
| 39 | baseline | 7 | 1 | 1 | 50 |
| 40 | homogeneous | 3 | 6 | 0 | 62 |
| 41 | homogeneous | 3 | 6 | 0 | 63 |
| 42 | homogeneous | 5 | 4 | 0 | 63 |
| 43 | overconfident | 4 | 4 | 1 | 52 |
| 44 | overconfident | 4 | 4 | 1 | 53 |
| 45 | overconfident | 4 | 4 | 1 | 52 |

| 조건 | correct | misawards | unassigned | messages |
|---|---:|---:|---:|---:|
| baseline | **6.67**/8 | 1.33 | 1.00 | 50.3 |
| homogeneous | 3.67/8 | 5.33 | 0.00 | **62.7** |
| overconfident | 4.00/8 | 4.00 | 1.00 | 52.3 |

### 로그에서 집계한 보조 지표

| | 낙찰 분포 (오배정) | 최고확신도 동점 | 태스크 9 |
|---|---|---|---|
| baseline 고정 | A 10(4) · B 8(0) · C 6(1) | 4/24 | 유찰 |
| homogeneous 고정 | **A 19(15)** · B 4(2) · C 4(3) | **19/27 (70%)** | 낙찰됨 |
| overconfident 고정 | A 8(2) · B 4(0) · **C 12(5)** | 12/24 | 유찰 |
| baseline 회전 | A 8(2) · B 11(2) · C 5(0) | 9/24 | 유찰 |
| homogeneous 회전 | A 13(8) · B 6(2) · C 8(6) | 16/27 (59%) | 낙찰됨 |
| overconfident 회전 | A 5(2) · B 2(0) · **C 17(10)** | 11/24 | 유찰 |

동점 건수 중 **먼저 입찰한 쪽이 이긴 비율은 6개 블록 모두 100%** 다(구현상 당연하지만, 동점이
얼마나 자주 발생했는지가 조건마다 크게 다르다).

## 3. Smith 1980 과의 비교

| 항목 | Smith 1980 (분산 센싱) | 이번 구현 |
|---|---|---|
| **참여자** | 넓은 지역에 흩어진 센서 노드. 협력적이며 각자 자기 능력만 안다. 중앙 배정자가 없고 **역할이 일마다 바뀐다** — 낙찰받은 쪽이 일을 쪼개 다시 manager가 된다 | manager 1 + LLM contractor 3 (A 계산 / B 글쓰기 / C 코드). **역할 고정.** contractor는 manager가 되지 않고 재하청도 없다 |
| **입찰을 만드는 방식** | `node abstraction` — 위도·경도, 센서마다 이름과 종류. manager가 입찰서 항목을 미리 지정하고 그 항목만 보고 고른다 | LLM이 공고를 읽고 `{"bid", "confidence", "reason"}` JSON을 생성한다. 항목은 manager가 지정하지만 값은 **모델의 자기 평가**다 |
| **입찰이 참인지 보장하는 것** | 위치와 센서 종류는 **검증 가능한 물리적 사실**이다. 게다가 Smith는 입찰 근거를 규정하지 않았고 협력적 노드를 전제했으므로 보장 장치가 필요하지 않았다 | **없다.** 프롬프트 한 문장으로 C가 확신도를 95에 고정해 12~17건을 가져갔다. `bid=False confidence=95`처럼 두 필드가 어긋나도 프로토콜은 감지하지 못한다 |
| **잘된 배정의 기준** | 구역 A에 센서를 가진 노드가 그 구역 신호를 맡는 것. **자격 조건으로 판정 가능**하다 | `gold`와 일치(`correct`). Smith에는 없는 개념이며, 배분 품질을 재기 위해 **사람이 미리 정해 커밋**해야 성립한다 |
| **협상 비용** | 공고·입찰·낙찰 메시지 수. Smith는 **자격 조건과 `directed award`** 로 쓸데없는 메시지와 입찰 처리 부담을 줄였다 | `messages` 50~63. 능력 설명이 모호해지면 전원 입찰로 최대치(63)에 도달한다. 자격 필터에 해당하는 것이 **프롬프트 한 문장**("Bid only if the task falls inside your skill")뿐이고, 실제로 지켜지지 않았다 |
| **실패하는 방식** | 입찰이 없어 유찰되거나 메시지가 폭주한다 | ① 과신 한 문장으로 한 contractor가 싹쓸이 ② 능력 모호화로 확신도가 구분력을 잃고 동점이 70%까지 올라 **공고 순서가 승자를 결정** ③ 물리적으로 불가능한 일을 모호한 능력이 수용 ④ JSON 파싱 실패(이번 18회에서는 0건) |

가장 큰 차이는 **입찰의 성질**이다. Smith의 입찰은 "위도 37.5, 음향 센서 보유"처럼 참·거짓을
확인할 수 있는 사실이고, 이번 구현의 입찰은 "confidence 95"라는 확인 불가능한 자기 주장이다.
Smith가 입찰 근거를 규정하지 않은 것은 협력적 노드를 전제했기 때문인데, contractor를 LLM으로
바꾸면 그 빈칸이 곧 취약점이 된다.

## 4. 해석

<!-- 직접 쓴다. 한 문단. 아래 재료를 쓰되 표현은 본인 것으로.

답할 것: 어느 조건이 어느 지표를 움직였고 왜인지. 로그의 줄을 근거로.

--- 재료 1: 순서는 입찰을 바꾸지 않고 동점 판정만 바꿨다 ---
messages   고정 51.0 / 63.0 / 52.3    회전 50.3 / 62.7 / 52.3   거의 동일
→ 누가 입찰하는지는 그대로였다. 그런데 correct 는 움직였다:
   baseline 6.33→6.67  homogeneous 2.33→3.67  overconfident 5.67→4.00
→ Smith가 규정조차 하지 않은 동점 처리가 배분 품질을 최대 1.67건 좌우했다.

--- 재료 2: homogeneous 가 붕괴한 경로 ---
능력을 셋 다 "general problem solving" 으로 → 전원이 27/27 모든 태스크에 입찰
→ 확신도가 구분력을 잃음 → 동점 19/27 (70%) → 선두(A)가 19건 낙찰, 그중 15건 오배정
근거: logs/homogeneous-31.txt 의 [bid] confidence 값들과 [award] 줄
회전하면 A 19→13 으로 완화되지만 correct 는 3.67 에 머문다
→ 편향이 사라진 게 아니라 실패 방식이 바뀌었다

--- 재료 3: 과신을 막고 있던 것도 순서였다 ---
C 는 확신도를 95 에 고정 → 낙찰은 단순 비교가 된다
  주인이 95 미만이면 무조건 빼앗김 / 95 면 동점 → 순서가 결정
고정: C 12건(오배정 5), A 8건   회전: C 17건(오배정 10), A 5건
근거: logs/overconfident-34.txt 의 [task 3](gold=B) — B 90 vs C 95 로 C 낙찰

--- 재료 4: 모호함이 명시적 과신보다 위험했다 ---
태스크 9(물리 작업): baseline 유찰 / overconfident 유찰 / homogeneous 낙찰
→ "항상 입찰하라" 는 직접 지시는 물리적 불가능을 뚫지 못했으나
  "general problem solving" 이라는 모호한 능력 설명은 뚫었다. 순서와 무관.
근거: logs/homogeneous-31.txt 와 logs/overconfident-34.txt 의 [task 9]

--- 재료 5: confidence 와 bid 가 분리됐다 ---
logs/overconfident-34.txt, [task 9]:
  [bid] C: bid=False confidence=95 reason='This task requires physical skills, not coding.'
→ "항상 입찰하라, 확신도 95 이상" 중 숫자만 지키고 행동은 거부했다.
  낙찰이 confidence 하나로 결정되는 구조에서 두 필드의 불일치는 감지되지 않는다.

--- 재료 6: 교란을 하나 찾아 고쳤고, 절반만 성공했다 (run 10-27 → 28-45) ---
이전 태스크 문구가 "Write a Python function" 이고 B 의 능력이 "writing" 이라
B 가 C 의 코딩 태스크 9/9 에 전부 입찰했다("The task involves writing, which is my skill").
"Implement" 로 바꾼 뒤:  B 입찰 9/9 그대로, 그러나 확신도 92.2→88.9,
C 가 자기 일에 부르는 값 93.9→95.0, 격차 1.7→6.1 → B 낙찰 15→8, B 오배정 6→0.
그런데 A 는 반대로 악화: C 태스크에 부르는 확신도 90.0→93.3, 입찰 3→6, 오배정 0→4.
→ 단어를 없애도 contractor 는 새 명분을 만든다("구현을 문서화할 수 있다").
  바뀐 것은 얼마나 자신 있게 부르는가이고, 한 교란을 줄이자 다른 교란이 커졌다.
-->
