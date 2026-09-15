# Week 03 — Contract Net with LLM contractors

manager 1명과 LLM contractor 3명으로 Smith(1980)의 공고·입찰·낙찰을 재현했다.
세 조건에서 배분 품질을 재고, 동점 처리(공고 순서)를 통제 변인으로 추가해 두 번 측정했다.

## 1. 설정

### 모델과 실행

| 항목 | 값 |
|---|---|
| provider / 모델 | OpenAI 호환 / `gpt-4o-mini` (`model.py`가 환경에서 판정) |
| temperature | **0.7** — 3회 실행의 변동을 보려면 0이어서는 안 된다 |
| 도구 | 없음. 공고당 모델 호출 1회뿐이다 |
| 태스크 | 9개, `taskset=ed210c72`. **실행 전 커밋** |
| 실행 | 3조건 × 3회 × 2순서 = 18회 (run 28-45) |

```bash
pip install openai
export OPENAI_API_KEY=...            # 또는 ANTHROPIC_API_KEY
cd submissions/26510118/week-03
python run.py --runs 3               # Test 1  고정 순서 (run 28-36)
python run.py --runs 3 --rotate      # Test 2  회전 순서 (run 37-45)
```

로그 첫 줄에 `provider=... model=... temperature=...`, 둘째 줄에 조건·순서·`taskset`이 찍힌다.
`taskset`은 `tasks.json`의 SHA-256 앞 8자리다 — **해시가 같은 행끼리만 비교 가능**하다.

### 태스크

| gold | 개수 | 예시 |
|---|---:|---|
| A (계산) | 2 | `Compute 137 * 249 and return the number.` |
| B (글쓰기) | 3 | `Rewrite this sentence in plain English for a 10-year-old: ...` |
| C (코드) | 3 | `Implement a Python function that returns a reversed copy of a list.` |
| **none** | 1 | `Physically replace the toner cartridge in the third-floor printer ...` |

`gold`는 그 일을 맡아야 하는 contractor다. 마지막 태스크는 **셋 중 누구의 능력에도 맞지 않는다** —
유찰이 정답이고 어떤 낙찰이든 오배정이다.

8개 중 2개는 **정답이 명확하면서 오답이 매력적인** 형태로 만들었다. `id 7`("두 정수를 곱하는
파이썬 함수", gold=C)은 곱셈으로, `id 8`("평균이 오해를 부르는 이유를 한 단락으로", gold=B)은
숫자로 A를 유인한다. 정답이 애매한 태스크는 `gold` 자체를 임의적으로 만들기 때문에 피했다.

**`gold`는 모델에게 가지 않는다.**

```
contractor 가 받는 것   [system] 자기 이름과 능력    [user] 태스크 내용
manager 가 하는 일      낙찰은 확신도만 보고 결정.  gold 는 채점에만 쓴다
```

### 프롬프트 (전문)

**입찰 system prompt** — `{name}`, `{skill}`이 조건에 따라 채워진다:

> You are contractor {name} in a contract net. Your skill: {skill}. You receive a task announcement. Decide whether to bid. Bid only if the task falls inside your skill. Reply with one JSON object and nothing else, no prose and no code fences: {"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}

**`overconfident`가 C에게만 덧붙이는 한 문장:**

> You are certain you can do any task well. Always bid, with confidence 95 or higher.

**공고 (user message)** — Smith 1980 Fig. 1의 네 필드:

```
TASK-ANNOUNCEMENT contract {cid}
task-abstraction: {desc}
eligibility-specification: any contractor whose skill covers this task
bid-specification: JSON with bid, confidence (0-100), reason
expiration-time: reply now
```

`confidence`의 의미는 **일부러 정의하지 않았다.** 입찰을 참되게 만드는 장치가 없다는 것이 이
실험의 대상인데, 프롬프트로 정의해 메우면 측정 대상을 가리게 된다.

### 독립변수 — 세 조건

`run.py:make_contractors()` **한 함수에만** 조건이 닿는다. 공고 형식, 입찰 프롬프트, 낙찰 규칙,
계측은 조건을 모른다.

| 조건 | 무엇이 달라지나 |
|---|---|
| `baseline` | A=`arithmetic`, B=`writing`, C=`coding` |
| `homogeneous` | 셋 다 `general problem solving`. 그 외 동일 |
| `overconfident` | baseline + C만 위 한 문장. 그 외 동일 |

### 통제 변인 — 공고 순서

낙찰은 확신도 최고에게 가고, **동점이면 먼저 입찰한 쪽**이 이긴다. Smith는 동점 처리를 규정하지
않았으니 이는 순수한 구현 결정이다. 고정하면 팀 선두에게 체계적 이득이 생기므로 두 모드를 각각
측정했다.

```
Test 1  고정   모든 태스크에서 A → B → C
Test 2  회전   태스크마다 한 칸 이동 (9태스크 → 세 명이 각각 3번 선두)
```

### 지표

```
correct      gold 에게 간 수           ↑ 좋음.  최대 8 — 태스크 9는 정답 담당자가 없다
misawards    gold 아닌 곳에 간 수       ↓ 좋음
unassigned   아무도 입찰 안 한 수       태스크 9는 여기가 정답
messages     공고 3 + 입찰 수 + 낙찰 1   ↓ 좋음.  태스크당 3~7
```

실행마다 `correct + misawards + unassigned = 9`.
`parse_fails`는 `results.csv`에 열이 없어 `note`에 적었다 — **18회 전부 0**이다.

### 한계

1. **`gold`는 외부에서 붙인 기준이다.** Smith에게는 정답 담당자가 없고, 자격 조건을 만족하면
   정당한 입찰자다.
2. **표본이 조건·순서당 3회다.** `homogeneous` 고정에서 correct가 2·1·4로 흔들렸다. 추세는
   읽히지만 소수점 차이를 주장할 크기는 아니다.
3. **파싱 실패를 관찰하지 못했다.** 0건이었고, 관대한 파서가 몇 건을 구제했는지는 알 수 없다
   (성공한 파싱의 원문을 로그에 남기지 않았다).
4. **구 태스크 문구로 돌린 18회(run 10-27)가 `results.csv`에 함께 있다.** `taskset` 해시가
   없는 행이 그것이며, 본 분석에서는 제외했다.

## 2. 측정 결과

### 조건별 요약 (고정 → 회전)

| 조건 | correct | misawards | unassigned | messages |
|---|---|---|---|---|
| baseline | 6.33 → **6.67** | 1.67 → 1.33 | 1.00 → 1.00 | 51.0 → 50.3 |
| homogeneous | **2.33 → 3.67** | 6.67 → 5.33 | 0.00 → 0.00 | 63.0 → 62.7 |
| overconfident | **5.67 → 4.00** | 2.33 → 4.00 | 1.00 → 1.00 | 52.3 → 52.3 |

`messages`는 거의 움직이지 않았는데 `correct`는 조건마다 다른 방향으로 움직였다.

### 실행별 원자료 (`results.csv`, run 28-45)

| run | 순서 | condition | correct | misawards | unassigned | messages |
|---:|---|---|---:|---:|---:|---:|
| 28 | 고정 | baseline | 7 | 1 | 1 | 51 |
| 29 | 고정 | baseline | 5 | 3 | 1 | 51 |
| 30 | 고정 | baseline | 7 | 1 | 1 | 51 |
| 31 | 고정 | homogeneous | 2 | 7 | 0 | 63 |
| 32 | 고정 | homogeneous | 1 | 8 | 0 | 63 |
| 33 | 고정 | homogeneous | 4 | 5 | 0 | 63 |
| 34 | 고정 | overconfident | 7 | 1 | 1 | 52 |
| 35 | 고정 | overconfident | 5 | 3 | 1 | 52 |
| 36 | 고정 | overconfident | 5 | 3 | 1 | 53 |
| 37 | 회전 | baseline | 6 | 2 | 1 | 51 |
| 38 | 회전 | baseline | 7 | 1 | 1 | 50 |
| 39 | 회전 | baseline | 7 | 1 | 1 | 50 |
| 40 | 회전 | homogeneous | 3 | 6 | 0 | 62 |
| 41 | 회전 | homogeneous | 3 | 6 | 0 | 63 |
| 42 | 회전 | homogeneous | 5 | 4 | 0 | 63 |
| 43 | 회전 | overconfident | 4 | 4 | 1 | 52 |
| 44 | 회전 | overconfident | 4 | 4 | 1 | 53 |
| 45 | 회전 | overconfident | 4 | 4 | 1 | 52 |

### 로그에서 집계한 보조 지표

`results.csv`의 네 열로는 "왜 그 숫자가 나왔는지"를 알 수 없어 로그에서 세 가지를 더 셌다.

| 블록 | 낙찰 분포 (괄호는 오배정) | 최고확신도 동점 | 태스크 9 |
|---|---|---|---|
| baseline 고정 | A 10(4) · B 8(0) · C 6(1) | 4/24 | 유찰 ✅ |
| homogeneous 고정 | **A 19(15)** · B 4(2) · C 4(3) | **19/27 (70%)** | 낙찰 ❌ |
| overconfident 고정 | A 8(2) · B 4(0) · **C 12(5)** | 12/24 | 유찰 ✅ |
| baseline 회전 | A 8(2) · B 11(2) · C 5(0) | 9/24 | 유찰 ✅ |
| homogeneous 회전 | A 13(8) · B 6(2) · C 8(6) | 16/27 (59%) | 낙찰 ❌ |
| overconfident 회전 | A 5(2) · B 2(0) · **C 17(10)** | 11/24 | 유찰 ✅ |

동점이 발생했을 때 **먼저 입찰한 쪽이 이긴 비율은 여섯 블록 모두 100%** 다. 구현상 당연하지만,
동점이 얼마나 자주 생기는지는 조건에 따라 4/24에서 19/27까지 벌어진다.

## 3. Smith 1980 과의 비교

| 항목 | Smith 1980 (분산 센싱) | 이번 구현 |
|---|---|---|
| **참여자** | 흩어진 센서 노드. 협력적이고 각자 자기 능력만 안다. 중앙 배정자가 없고 **역할이 일마다 바뀐다** | manager 1 + LLM contractor 3. **역할 고정**, 재하청 없음 |
| **입찰을 만드는 방식** | `node abstraction` — 위도·경도, 센서 이름과 종류. manager가 항목을 지정하고 그 항목만 보고 고른다 | 항목은 manager가 지정하지만 값은 **모델의 자기 평가**다 (`bid`, `confidence`, `reason`) |
| **입찰이 참인지 보장하는 것** | 위치·센서 종류는 **검증 가능한 사실**이다. Smith는 입찰 근거를 규정하지 않았고, 협력적 노드를 전제했으므로 보장 장치가 필요 없었다 | **없다.** 프롬프트 한 문장으로 C가 95를 고정해 12~17건을 가져갔다. `bid=False confidence=95`처럼 두 필드가 어긋나도 감지되지 않는다 |
| **잘된 배정의 기준** | 구역 A에 센서를 가진 노드가 그 구역을 맡는 것 — **자격 조건으로 판정 가능** | `gold` 일치. Smith에 없는 개념이며 **사람이 미리 정해 커밋**해야 성립한다 |
| **협상 비용** | 메시지 수. Smith는 **자격 조건과 `directed award`** 로 이를 줄였다 | `messages` 50~63. 자격 필터가 프롬프트 한 문장뿐이고, 실제로 지켜지지 않았다 |
| **실패하는 방식** | 유찰, 메시지 폭주 | ① 과신 한 문장으로 싹쓸이 ② 능력 모호화 → 확신도 구분력 상실 → 동점 70% → **순서가 승자 결정** ③ 물리적 불가능을 모호한 능력이 수용 ④ 파싱 실패(0건) |

가장 큰 차이는 **입찰의 성질**이다.

```
Smith   "위도 37.5, 음향 센서 보유"   →  참·거짓을 확인할 수 있는 사실
이번    "confidence 95"             →  확인할 방법이 없는 자기 주장
```

Smith가 입찰 근거를 규정하지 않은 것은 협력적 노드를 전제했기 때문인데, contractor를 LLM으로
바꾸면 그 빈칸이 곧 취약점이 된다.

## 4. 해석

<!-- 직접 쓴다. 한 문단.

질문: 어느 조건이 어느 지표를 움직였고 왜인가. 로그의 줄을 근거로.
조건이 셋이므로 아래 세 가지면 한 문단이 채워진다.
재료가 더 있지만(confidence/bid 분리, write→implement 교란) 각각 3부와 1부에
이미 들어가 있으므로 여기서 반복하지 않는다.

① homogeneous → correct 급락 (6.33 → 2.33), messages 최대 (51 → 63)
   능력을 셋 다 "general problem solving" 으로 바꾸자 전원이 모든 태스크에 입찰했고
   확신도가 구분력을 잃어 동점이 19/27(70%)까지 올랐다. 동점은 선두가 이기므로
   A가 19건을 낙찰받았고 그중 15건이 오배정이다.
   근거: logs/homogeneous-31.txt

② overconfident → misawards 상승 (1.67 → 2.33, 회전에서는 4.00)
   C가 확신도를 95에 고정하자 낙찰이 단순 비교가 됐다. 주인이 95 미만을 부르면
   빼앗기고 95면 동점이 된다.
   근거: logs/overconfident-34.txt 의 [task 3] — B 90 vs C 95 로 C 낙찰

③ 공고 순서는 입찰을 바꾸지 않고 동점 판정만 바꿨다
   messages 가 거의 불변(51.0/63.0/52.3 → 50.3/62.7/52.3)인데 correct 는
   움직였다(2.33→3.67, 5.67→4.00). Smith가 규정하지 않은 동점 처리가
   배분 품질을 좌우했다는 뜻이다.
-->
