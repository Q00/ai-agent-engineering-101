# REPORT — 하네스 A/B: ReAct형 vs Plan-then-Execute형

학번 26512070 (@gom31) · 태스크와 성공 기준 [`TASK.md`](TASK.md) · 측정
[`results.csv`](results.csv) · 실행 원본 [`logs/`](logs/)

---

## 1. 변형 정의

두 하네스는 [`tools_shared.py`](tools_shared.py)를 **같이** import한다. 도구
두 개(`read_file`, `count_pattern`), 모델명, `Meter`가 전부 그 파일에 있으므로
두 팔 사이에서 도구가 어긋날 수 없다. 태스크와 성공 기준은 하네스 코드보다
**먼저** 커밋했다(`cba07d6`).

| 축 | ReAct | Plan-then-Execute | |
|---|---|---|---|
| 1 컨텍스트 관리 | 전체 transcript 누적. 3022자 로그 전문이 매 턴 재전송 | 계획 + 완료 단계당 **한 줄 요약**만. transcript를 안 봄 | **다름** |
| 2 도구 granularity | `read_file`, `count_pattern` | 동일 | 같음 |
| 3 종료 조건 | 도구 호출 없는 응답 **또는** `max_steps` | **계획 소진.** 목표 달성 확인 단계 없음 | **다름** |
| 4 에러 복구 | 예외를 Observation으로 되돌려 다음 턴이 고침 | `off_plan`이 켜지면 **재계획 1회**까지 | **다름** |
| 5 개입 지점 | `IRREVERSIBLE` 비어 있음, 완전 자율 | 동일 | 같음 |

축5를 양쪽 동일하게 고정한 것은 의도다. 이 태스크는 읽기와 세기뿐이라 되돌릴
수 없는 행동이 없고, 승인을 구할 자리가 없다. `interventions` 열이 24행 전부
0인 것은 측정 누락이 아니라 통제된 상수다.

**네 설정에서 각각 A/B를 돌렸다.** 설정 안에서는 하네스만 독립변수이고, 설정
사이에서는 모델 호출 상수만 바뀐다. **다섯 축의 하네스 코드는 네 설정에서
동일하다.**

| 설정 | run | model | `max_tokens` | `max_steps` | 왜 |
|---|---|---|---|---|---|
| A | 1–6 | `nemotron-3.5-lightning:free` | 1024 | 6 | 무료 티어 |
| B | 7–12 | `claude-sonnet-4.5` | 미지정 | 6 | 잘림 제거 |
| C | 13–18 | `claude-sonnet-4.5` | 미지정 | **8** | 예산 상한 되돌림(= starter 값) |
| D | 19–24 | `claude-sonnet-4.5` | 미지정 | **24** | 상한이 걸리지 않게 |

A·B의 `max_steps=6`은 설계 결정이 아니라 **무료 티어 하루 50요청에 6회를
넣으려고 고른 값**이었다. C에서 강의·starter 값인 8로 되돌렸고, 재계획 후 총
실행 턴을 6으로 묶었던 상한(역시 내가 예산 때문에 넣은 것)도 제거했다. D의
24는 가능한 시간대 24개를 전부 세고도 남아 상한이 걸리지 않는다.

---

## 2. 측정

| run | harness | success | tokens | iters | interventions | note |
|---:|---|:---:|---:|---:|---:|---|
| 1 | react | O | 3994 | 2 | 0 | finished by model |
| 2 | plan_exec | X | 2357 | 2 | 0 | plan parse failed twice (replans=1) |
| 3 | react | O | 3914 | 2 | 0 | finished by model |
| 4 | react | X | 4011 | 2 | 0 | finished by model *(실은 잘림)* |
| 5 | plan_exec | X | 2357 | 2 | 0 | plan parse failed twice (replans=1) |
| 6 | plan_exec | X | 2357 | 2 | 0 | plan parse failed twice (replans=1) |
| 7 | react | O | 18731 | 6 | 0 | finished by model |
| 8 | react | X | 19566 | 6 | 0 | hit max_steps=6 |
| 9 | react | X | 16520 | 6 | 0 | hit max_steps=6 |
| 10 | plan_exec | X | 12426 | 8 | 0 | replans=0 steps=6 plan_len=6 |
| 11 | plan_exec | X | 8905 | 6 | 0 | replans=0 steps=4 plan_len=4 |
| 12 | plan_exec | X | 14442 | 8 | 0 | replans=0 steps=6 plan_len=6 |
| 13 | react | X | 30277 | 8 | 0 | hit max_steps=8 |
| 14 | react | O | 24442 | 7 | 0 | finished by model |
| 15 | react | X | 27836 | 8 | 0 | hit max_steps=8 |
| 16 | plan_exec | O | 17088 | 10 | 0 | replans=0 steps=8 plan_len=8 |
| 17 | plan_exec | X | 14220 | 10 | 0 | replans=0 steps=8 plan_len=8 |
| 18 | plan_exec | X | 14743 | 10 | 0 | replans=0 steps=8 plan_len=8 |
| 19 | react | O | 24073 | 7 | 0 | finished by model |
| 20 | react | O | 41850 | 12 | 0 | finished by model |
| 21 | react | O | 20864 | 6 | 0 | finished by model |
| 22 | plan_exec | O | 71782 | 26 | 0 | replans=0 steps=24 plan_len=24 |
| 23 | plan_exec | X | 65065 | 26 | 0 | replans=0 steps=24 plan_len=24 |
| 24 | plan_exec | X | 58008 | 26 | 0 | replans=0 steps=24 plan_len=24 |

| 설정 | react success | react tokens | react iters | plan_exec success | plan_exec tokens | plan_exec iters | **plan/react 토큰** |
|---|:---:|---:|---|:---:|---:|---|---:|
| A `max_steps=6` | 2/3 | 3973 | 2,2,2 | 0/3 | 2357 | 2,2,2 | **0.59×** |
| B `max_steps=6` | 1/3 | 18272 | 6,6,6 | 0/3 | 11924 | 8,6,8 | **0.65×** |
| C `max_steps=8` | 1/3 | 27518 | 8,7,8 | 1/3 | 15350 | 10,10,10 | **0.56×** |
| D `max_steps=24` | **3/3** | 28929 | 7,12,6 | 1/3 | 64952 | 26,26,26 | **2.25×** |

합계 react **7/12**, plan_exec **2/12**.

---

## 3. 해석

**상한이 걸리는 동안에는 Plan-then-Execute가 토큰으로 이겼고 상한을 치우자
그 우위가 2.25배 열세로 뒤집혔으며, 뒤집은 축은 1이 아니라 3이다.** 설정
A·B·C에서 plan_exec은 react 토큰의 0.56–0.65배만 썼고 나는 이것을 축1의
효과로 읽었다 — react는 3022자 로그 전문이 든 transcript 전체를 매 턴
재전송하고 `execute_step`은 계획과 완료 단계당 한 줄만 보내니까. 그런데 설정
D에서 react의 `iters`는 7·12·6으로 상한 24 근처에도 가지 않는다. **react는
24스텝이 필요했던 적이 없고, 필요한 만큼 쓸 자유가 필요했을 뿐이다.** 반대로
plan_exec은 D에서 세 번 모두 정확히 26회(계획 1 + 24스텝 + 요약 1)를 썼는데,
계획을 `read_file`이 돌기 전에 굳혔으므로 로그에 09–17시만 있다는 것을 알
수 없어 24개 시간대를 전부 열거했고, 실행 중에 그것을 알게 된 뒤에도 계획을
끝까지 밀었기 때문이다. 즉 **앞선 설정에서 관찰된 토큰 우위는 하네스의 성질이
아니라 상한이 만든 인공물이었다.** 상한이 낮으면 두 팔이 같은 턴 수에서
잘리고, 그 조건에서는 턴당 컨텍스트가 작은 쪽이 싸 보인다. 상한을 치우면
관측에 맞춰 일찍 멈출 수 있는 쪽이 토큰과 성공률을 **동시에** 가져간다.
토큰을 아끼는 것은 컨텍스트 가지치기(축1)가 아니라 적응적 종료(축3)였다.

### 근거 — 같은 실수, 다른 결말 (`react-21` vs `plan_exec-23`)

두 실행은 같은 모델·같은 도구·같은 설정 D에서 **동일한 착각**으로 시작한다.
`^2026-09-01 HH:.*ERROR` 패턴을 쓰는데, 이 구현의 `count_pattern`은 파일 전문에
`re.findall`을 걸므로 `^`는 문자열 맨 앞에만 매치한다. 그래서 **0이 나온다.**

| | `react-21` (O, 6 iters) | `plan_exec-23` (X, 26 iters) |
|---|---|---|
| 1 | `read_file` | `read_file` |
| 2 | `^...09:.*ERROR` → **0** | `^...00:.*ERROR` → **0** |
| 3 | `^...10:.*ERROR` → **0** | `^...01:.*ERROR` → **0** |
| 4 | **`^`를 뺌** → `09:.*ERROR` → 1 ✔ | `^...02:.*ERROR` → 0 |
| … | `14:.*ERROR` → 6 → **종료** | … 22시까지 **24개 전부 0** |
| 끝 | 6스텝 | 최종 답: *"all hours checked so far returned 0 errors… the pattern looks correct, but all returned 0"* |

react는 0을 **두 번** 보고 다음 턴에 가설을 고쳤다. plan_exec은 깨진 패턴을
24개 단계에 복사한 채 계획을 굳혔으므로, 2단계에서 이미 실패가 확정됐는데도
22단계를 더 실행했다. 최종 요약에서 모순을 **알아차렸지만** 계획이 소진돼
손쓸 턴이 없었다. 축3(종료)과 축4(복구)가 여기서 같은 지점을 가리킨다.

### 근거 — 재계획 1회는 한 번도 쓰이지 않았다

설정 B·C·D의 plan_exec 9회 전부 **`replans=0`**이다. 유연성 상한이 1회라서
부족했던 것이 아니라, **트리거가 켜진 적이 없다.** `off_plan`을 "도구가 예외를
던졌는가"로 정의했는데 `count_pattern`이 0을 반환하는 것은 예외가 아니라
정상 결과다. 24번 연속 0을 받는 실행이 하네스 입장에서는 24번 연속 성공이다.
**축4에서 중요한 것은 상한이 1이냐 2냐가 아니라 복구 트리거를 무엇으로
정의했느냐이며, "성공했지만 쓸모없는 단계"는 이 하네스의 시야 밖에 있다.**
react에 별도의 복구 장치가 없는데도 복구가 일어난 이유는, 매 스텝 다시
판단하는 루프 자체가 암묵적 복구 장치이기 때문이다.

### 근거 — 축3은 세 가지 서로 다른 방식으로 success를 움직였다

- `react-04` (설정 A): 응답이 `max_tokens=1024`에 잘렸는데 도구 호출이 없다는
  이유로 하네스가 "모델이 finish를 택함"으로 판정하고, 결론에 이르지 못한 사고
  과정 덩어리를 최종 답으로 반환했다. **잘린 답과 완성된 답이 구분되지 않는다.**
  강의의 *"모델은 다 못 풀고도 끝났다고 말한다"*보다 한 단계 나아간 경우다 —
  모델은 끝났다고 **선언조차 하지 않았고**, 하네스가 종료를 추론했다.
- `react-08/09/13/15`: `max_steps` 상한(6과 8)에 걸려 미완 종료. 설정 D에서
  상한을 24로 올리자 같은 하네스가 **3/3**이 됐다.
- `plan_exec-12`: 최종 답이 *"I need to continue checking all hours from 05 to
  23"*이다. 계획 소진으로 종료하는 하네스에는 목표 달성 확인 단계가 없다.

### 근거 — 축2가 축3의 값을 결정한다

`count_pattern`은 호출당 정규식 하나를 센다. 따라서 "가장 많은 시간대"는 후보
시간대 수만큼 호출이 필요하고, 로그에 9개 시간대가 있으므로 **읽기 1 + 세기 9 +
종료 1 = 최소 11스텝**이다. 강의·starter 기본값 `max_steps=8`은 이 도구
granularity에서 **구조적으로 부족하다**(설정 C에서 react 1/3). 상한은 도구
granularity를 알고 정해야 하는 값이지 독립적으로 고를 수 있는 값이 아니다.

설정 A에서 약한 무료 모델이 강한 모델보다 높은 점수(2/3 vs 1/3)를 받은 것도
같은 축의 결과다. `read_file`의 상한이 4000자이고 `app.log`가 3022자라 파일이
통째로 반환되므로, 그 모델은 `count_pattern`을 한 번도 쓰지 않고 **눈으로 세어
2스텝에 끝냈다.** **약한 모델이 이긴 이유는 하네스가 열어 둔 지름길을 탔기
때문이지 더 잘해서가 아니다.**

---

## 4. 이 실험이 말하지 못하는 것 / 내 쪽의 오류

- **`count_pattern`의 의미가 starter와 다르다.** 나는 강의 뼈대의
  `len(re.findall(pattern, text))`(파일 전문 대상)를 구현했는데,
  `weeks/week-02/starter/tools_shared.py`는 `sum(1 for line in f if
  rx.search(line))`(줄 단위)로 센다. 줄 단위였다면 위의 `^` 실패는 일어나지
  않았다. **이 차이는 실행이 끝난 뒤에 발견했다.** 다만 도구는 두 팔에 완전히
  동일하게 적용됐으므로 A/B의 내적 타당성은 유지되며, 결과적으로 이 함정이 이
  실험에서 가장 날카로운 판별자가 됐다. 줄 단위 구현으로 다시 돌리면 두 팔의
  성공률이 함께 올라갈 것으로 예상하고, 그 경우 축4의 차이는 관측하기
  어려워질 것이다.
- **설정당 하네스당 3회.** 2/3과 1/3의 차이를 표집잡음과 분리하기에는 부족하다.
  설정 D의 3/3 대 1/3, 그리고 토큰 2.25배는 표본 수 대비 충분히 크다고 보지만,
  설정 C의 1/3 대 1/3은 아무것도 말해 주지 않는다.
- **모델은 설정당 하나.** A의 관찰이 무료 모델 특유의 성질인지 일반적인지
  구분할 수 없다.
- `react-07`(설정 B)은 O지만 답 안에서 14시 ERROR를 **7개로 틀렸다**(실제 6개).
  시간대는 맞았고 성공 기준이 시간대로 정의돼 있으므로 O가 맞지만, 정답에
  이른 경로가 정확하지는 않았다.
- **전체 9/24.** 이 A/B가 "어느 하네스가 더 나은가"보다 "어느 설계 결정이
  실패를 만들었는가"에 더 많은 것을 말해 주는 이유다.

---

## 5. 재현

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<your key>

export AGENT_MODEL=anthropic/claude-sonnet-4.5
export AGENT_MAX_STEPS=24            # 설정 D
py run_ab.py --runs 3
```

| 설정 | 환경변수 |
|---|---|
| A | `AGENT_MODEL=nvidia/nemotron-3.5-lightning:free  AGENT_MAX_TOKENS=1024  AGENT_MAX_STEPS=6` |
| B | `AGENT_MODEL=anthropic/claude-sonnet-4.5  AGENT_MAX_STEPS=6` |
| C | `AGENT_MODEL=anthropic/claude-sonnet-4.5  AGENT_MAX_STEPS=8` |
| D | `AGENT_MODEL=anthropic/claude-sonnet-4.5  AGENT_MAX_STEPS=24` |

`AGENT_MAX_TOKENS`를 주지 않으면 `max_tokens`는 **보내지 않는다**(공급자 기본값).
각 로그 파일 머리에 그 실행이 쓴 model·max_tokens·세 상한이 전부 적혀 있다.

API 키 없이 하네스 로직만 확인하려면:

```bash
py run_ab.py --dry-run --runs 1
```

스크립트된 가짜 모델로 두 팔을 끝까지 돌리며 요청을 하나도 쓰지 않는다.
`results.csv`와 `logs/`는 건드리지 않는다. 도구 오류 복구, 없는 도구 호출,
`max_steps` 도달, 계획 파싱 실패 경로가 모두 이 모드에서 검증됐다.
