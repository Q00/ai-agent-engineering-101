# REPORT — 하네스 A/B: ReAct형 vs Plan-then-Execute형

학번 26512070 (@gom31). 태스크·성공 기준·실험 조건은 [`TASK.md`](TASK.md),
실행 원본은 [`logs/`](logs/), 측정값은 [`results.csv`](results.csv).

---

## 1. 변형 정의 — 무엇이 같고 무엇이 다른가

두 하네스는 `tools_shared.py`를 **같이** import한다. 도구 두 개(`read_file`,
`count_pattern`), 모델명, `Meter`가 전부 그 파일에 있으므로 두 팔 사이에서
도구가 어긋날 수 없다. 태스크와 성공 기준은 실행 전에 `TASK.md`에 커밋했다
(커밋 `cba07d6`, 하네스 코드보다 먼저).

| 축 | ReAct | Plan-then-Execute | |
|---|---|---|---|
| 1 컨텍스트 관리 | 전체 transcript 누적. 3022자 로그 전문이 매 턴 재전송 | 계획 + 완료 단계당 **한 줄 요약**만. transcript를 안 봄 | **다름** |
| 2 도구 granularity | `read_file`, `count_pattern` | 동일 | 같음 |
| 3 종료 조건 | 도구 호출 없는 응답 **또는** `max_steps=6` | **계획 소진**. 목표 달성 확인 없음 | **다름** |
| 4 에러 복구 | 예외를 Observation으로 되돌려 다음 턴이 고침 | `off_plan`이 켜지면 **재계획 1회**까지 | **다름** |
| 5 개입 지점 | `IRREVERSIBLE` 비어 있음, 완전 자율 | 동일 | 같음 |

축5를 양쪽 동일하게 고정한 것은 의도다. 이 태스크는 읽기와 세기뿐이라
되돌릴 수 없는 행동이 없고, 따라서 승인을 구할 자리가 없다. `interventions`
열이 12행 전부 0인 것은 측정 누락이 아니라 통제된 상수다.

A/B는 **두 설정**에서 각각 돌렸다. 설정 안에서는 하네스만 독립변수이고,
설정 사이에서는 모델 호출 상수 두 개(`model`, `max_tokens`)만 바뀐다.
**다섯 축의 값은 두 설정에서 동일하다** — `max_steps=6`, `max_replan=1` 모두 그대로다.

---

## 2. 측정

| run | harness | success | tokens | iters | interventions | note |
|---:|---|:---:|---:|---:|---:|---|
| 1 | react | O | 3994 | 2 | 0 | finished by model |
| 2 | plan_exec | X | 2357 | 2 | 0 | plan parse failed twice (replans=1) |
| 3 | react | O | 3914 | 2 | 0 | finished by model |
| 4 | react | **X** | 4011 | 2 | 0 | finished by model *(실은 잘림)* |
| 5 | plan_exec | X | 2357 | 2 | 0 | plan parse failed twice (replans=1) |
| 6 | plan_exec | X | 2357 | 2 | 0 | plan parse failed twice (replans=1) |
| 7 | react | O | 18731 | 6 | 0 | finished by model |
| 8 | react | X | 19566 | 6 | 0 | hit max_steps=6 |
| 9 | react | X | 16520 | 6 | 0 | hit max_steps=6 |
| 10 | plan_exec | X | 12426 | 8 | 0 | replans=0 steps=6 plan_len=6 |
| 11 | plan_exec | X | 8905 | 6 | 0 | replans=0 steps=4 plan_len=4 |
| 12 | plan_exec | X | 14442 | 8 | 0 | replans=0 steps=6 plan_len=6 |

run 1–6 = 설정 A (`nvidia/nemotron-3.5-lightning:free`, `max_tokens=1024`)
run 7–12 = 설정 B (`anthropic/claude-sonnet-4.5`, `max_tokens` 미지정)

| 설정 | 하네스 | success | tokens 평균 | iters |
|---|---|---|---:|---|
| A | react | **2/3** | 3973 | 2 |
| A | plan_exec | 0/3 | **2357** (−41%) | 2 |
| B | react | **1/3** | 18272 | 6 |
| B | plan_exec | 0/3 | **11924** (−35%) | 6–8 |

전체 3/12. **어느 하네스도 이 태스크를 안정적으로 풀지 못했다.**

---

## 3. 해석

**토큰은 Plan-then-Execute가, 성공률은 ReAct가 이겼고, 둘 다 원인이 분명하다.**
토큰 차이는 축1이 만든 것이고 두 설정에서 41%와 35%로 재현됐다 — ReAct는 3022자
로그 전문을 포함한 transcript 전체를 매 턴 재전송하는 반면 Plan-then-Execute의
`execute_step`은 계획과 완료 단계당 한 줄만 보내기 때문이다. 반대로 성공률은
축3이 갈랐는데, ReAct가 이긴 것은 더 똑똑해서가 아니라 **매 스텝 다시 판단할
기회가 있기 때문**이다. 설정 B에서 이것이 가장 선명한데, 세 번의 plan_exec은
모두 계획을 `read_file`이 돌기 **전에** 굳혔으므로 로그에 09–17시만 있다는 것을
알 수 없었고, 그래서 셋 다 00시부터 세기 시작해 스텝 전부를 존재하지 않는
시간대에 썼다(`plan_exec-10/11/12`). ReAct는 같은 모델로 같은 실수를 하지 않았다 —
먼저 읽고, 본 시간대만 셌다. 그런데 결정적으로, 이 실패에서 **재계획은 한 번도
발동하지 않았다**(`replans=0` ×3): 00시를 세어 0이 나오는 것은 예외가 아니라
정상 결과이고, `off_plan`을 예외 발생으로 정의한 탓에 유연성 상한 1회는 시험될
기회조차 없었다. 즉 축4에서 중요한 것은 상한값이 1이냐 2냐가 아니라 **복구
트리거를 무엇으로 정의했느냐**이며, 예외가 아닌 "성공했지만 쓸모없는 단계"는
이 하네스의 시야 밖에 있다.

### 근거 — 축별로, 로그에서

**축3 → success, 세 가지 다른 방식으로.** (a) `react-04`: 응답이
`max_tokens=1024`에 잘렸는데 도구 호출이 없다는 이유로 하네스가 "모델이 finish를
택함"으로 판정하고, 결론에 도달하지 못한 사고 과정 덩어리를 최종 답으로 반환했다.
잘린 답과 완성된 답이 하네스 입장에서 구분되지 않는다. (b) `react-08/09`:
`max_steps=6` 상한에 걸려 미완 종료. (c) `plan_exec-12`: 최종 답이 문자 그대로
*"I need to continue checking all hours from 05 to 23"* 이다. 모델은 끝나지 않은
것을 알지만, 계획이 소진되면 종료하는 하네스에는 목표 달성을 확인하는 단계가
없어서 계속할 방법이 없다. 강의가 축3에서 경고한 *"모델은 다 못 풀고도 끝났다고
말한다"* 가 여기서는 한 단계 더 나아간다 — 모델이 끝났다고 **선언조차 하지 않았는데**
하네스가 종료를 추론했다.

**축2 × 축3 상호작용.** `count_pattern`은 호출당 정규식 하나를 센다. 따라서
"가장 많은 시간대"는 후보 시간대 수만큼 호출이 필요하고, 로그에 9개 시간대가
있으므로 `max_steps=6`은 **구조적으로 부족하다.** 상한을 도구 granularity와
무관하게 정한 것이 실패의 직접 원인이다. 흥미롭게도 설정 A의 약한 모델은 이 벽에
부딪히지 않았는데, `read_file`의 상한이 4000자이고 `app.log`가 3022자라 파일이
통째로 반환되어 **눈으로 세고 2스텝 만에 끝냈기** 때문이다. **약한 모델이 더 높은
점수를 받았고, 그 이유는 하네스가 허용한 지름길을 탔기 때문이다.**

**축4 → ReAct에서는 작동, plan_exec에서는 미발동.** `react-08/09`에서 모델이
`^2026-09-01 09:.* ERROR`로 0을 받는다(이 구현의 `count_pattern`은 `re.M`을 쓰지
않으므로 `^`가 문자열 처음에만 걸린다). 다음 턴에서 `^`를 빼고 1을 얻는다 —
Observation을 보고 스스로 고치는 과정이 로그에 그대로 남아 있으며, 그 복구에
스텝 하나를 썼다. plan_exec에서는 위에 쓴 대로 트리거가 켜지지 않았다.

**축1 → tokens.** 위 표의 41% / 35%. 설정 A의 plan_exec 토큰이 2357로 3회 모두
**완전히 동일**한 것은 결정론이 아니라 두 호출 모두 `max_tokens=1024`에 잘렸다는
뜻이다(`2357 = 2×1024 + 309`). 계획 응답 6건이 전부 단어 중간에서 끊겨 있다.
Plan-then-Execute가 계획에 실패한 것이 아니라 **계획을 출력할 차례가 오기 전에
잘린** 것이며, 이것이 설정 B를 만든 이유다.

### 이 실험이 말하지 못하는 것

- **`max_steps=6`은 내가 예산 때문에 고른 값이고, 설정 B의 결과를 지배했다.**
  무료 티어 하루 50요청에 6회 실행을 넣으려고 강의 기본값 8 대신 6으로 잡았는데,
  이 태스크는 이 도구 granularity에서 9스텝 이상을 요구한다. 상한을 9 이상으로
  두고 다시 돌리면 설정 B의 success 열은 달라질 가능성이 높다. 이것은 교란변수이며,
  결과를 해석할 때 빼고 읽어서는 안 된다.
- **설정당 하네스당 3회, 모델은 설정당 하나.** 성공률 2/3과 1/3의 차이를
  표집잡음과 분리하기에는 부족하다.
- `react-07`은 O지만 답 안에서 14시 ERROR를 **7개로 틀렸다**(실제 6개). 시간대는
  맞았고 성공 기준이 시간대로 정의돼 있으므로 O가 맞지만, 정답을 맞힌 경로가
  정확하지는 않았다.
- 두 하네스가 **3/12**밖에 못 풀었다는 사실 자체가, 이 A/B가 "어느 하네스가 더
  나은가"보다 "어느 설계 결정이 실패를 만들었는가"에 더 많은 것을 말해 준다는
  뜻이다.

### 재현

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<your key>
export AGENT_MODEL=anthropic/claude-sonnet-4.5   # 설정 B
py run_ab.py --runs 3
```

설정 A는 `AGENT_MODEL=nvidia/nemotron-3.5-lightning:free`와
`AGENT_MAX_TOKENS=1024`를 함께 준다. API 키 없이 하네스 로직만 확인하려면
`py run_ab.py --dry-run --runs 1` — 스크립트된 가짜 모델로 두 팔을 끝까지
돌리며 요청을 하나도 쓰지 않는다.
