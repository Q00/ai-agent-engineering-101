# Week 02 — 하네스 A/B: ReAct vs Plan-then-Execute

## 실험 개요

이 실험은 두 단계로 진행했다.

- **v1 — 교수님 스타터 코드 그대로 실행.** `weeks/week-02/starter/`의 ReAct·Plan-then-Execute 하네스를 한 줄도 고치지 않고 돌려, 교수님이 설계한 두 하네스의 기본 차이를 측정했다(gpt-4o-mini 9+9회, gpt-5.4-mini 3+3회 = 24회). 처음에는 이것이 과제라고 이해했으나, "하네스를 **직접 설계**해 비교"가 요구임을 확인한 뒤 결과를 지우지 않고 **통제군(baseline)** 으로 삼았다.
- **v2 — 하네스를 직접 수정해 실행.** v1 실패 로그에서 본 약점을 겨냥해 하네스당 **축 하나씩**만 바꿨다. ReAct는 축 3(종료 조건)에 *답을 내면 한 번 검증하는 턴*을, Plan-then-Execute는 축 4(에러 복구)에 *실행 전 계획 검증*을 넣고 gpt-5.4-mini로 6+6회를 돌렸다. v2 ReAct에서 드러난 종료 프로토콜 결함(행 25)을 고친 **v2.1**로 ReAct 6회를 다시 재서 결함 전후를 비교했다.

총 **42회**. v1 원본 코드는 `v1_unmodified/`에, 최상위 `harness_*.py`는 v2.1이다. v1과 v2의 차이는 (1)에, 수치는 (2)에, 그 해석은 (3)에 있고, 로그·그림·전체 표는 부록에 뒀다.

**태스크** `app.log`에서 ERROR 줄이 가장 많은 시간대(HH:00). 정답 `14:00`, `TASK.md`의 `expected:`는 첫 실행 전 커밋(`20a01e3`). **고정 변수** 도구(`read_file`, `count_pattern`)·모델 호출·`Meter`는 두 하네스가 같은 `tools_shared.py`를 import하고, 태스크·입력 파일은 모든 실행에서 동일하다.

## (1) 변형 정의

| 축 | v1 ReAct | v1 Plan-then-Execute | **v2에서 내가 바꾼 것** |
|---|---|---|---|
| 1 컨텍스트 | 대화 1개, 전체 이력을 매 호출에 재전송 | 대화 2개(planner는 도구 없음 / executor). **계획은 데이터를 보기 전에 확정** | — |
| 2 도구 | `tools_shared.TOOL_SPECS` 공유 | 동일 | — |
| 3 종료 | `max_steps=8` + 모델이 도구 없이 답하면 즉시 종료 | 계획 소진 + 단계당 `max_tool_rounds=3` + 재계획 `max_replan=1` + "Answer:" 강제 | **ReAct: Answer 뒤 검증 턴 1회**(`max_verify=1`) — "Observation과 모순 없나?" → `VERIFIED`면 종료, 수정 Answer면 종료, 도구 호출로 재개하면 계속. v2.1: 마지막 Answer를 기억해 `VERIFIED` 한 단어 답변에도 잃지 않음 |
| 4 에러 복구 | 예외 → Observation으로 되돌림 | 동일 + `OFF_PLAN:`이면 재계획 1회, JSON 파싱 실패는 즉시 실패 | **Plan-Execute: 실행 전 계획 검증**(`max_plan_fixes=1`) — `name(...)` 꼴 단계가 실제 도구 이름·인자 수와 안 맞으면 위반 단계와 실제 시그니처를 planner에 되돌려 수정 요구. 산문 단계는 통과 |
| 5 개입 | `IRREVERSIBLE=set()`, 훅만 있음 | 훅 없음 | — |

**v2의 두 하네스는** v1의 축 1·3·4 차이를 그대로 물려받고, 거기에 ReAct는 축 3, Plan-Execute는 축 4에 검사 하나씩이 더해져 **축 2·5만 같다.** 프롬프트·예산 상한·도구는 v1과 동일, v1 원본은 `v1_unmodified/`. 핵심 함정: `count_pattern`은 매칭 줄 수만 돌려주고 시간대 집계는 못 하므로, 9번 나눠 부르거나 읽은 파일을 직접 집계해야 한다 — 이 판단을 **관찰 후에 하는가(ReAct) 전에 하는가(Plan-Execute)** 가 축 1의 실질이다.

## (2) 측정표

토큰 = 입력+출력, 호출 = 모델 호출 수, 개입은 전부 0. 실패 6건 포함(행 7·15·11·12·16 = v1 gpt-4o-mini의 모델 실수 → 부록 A, 행 25 = v2 ReAct의 하네스 결함 → 부록 B). 42행 원본은 부록 F.

| variant | model | harness | n | 성공 | 토큰 중앙값 | 토큰 평균 | 토큰 범위 | 호출 중앙값 | 호출 범위 |
|---|---|---|---|---|---|---|---|---|---|
| v1 | gpt-4o-mini | react | 9 | **7/9** | 3,754 | 4,981 | 2,552–11,031 | 3 | 3–8 |
| v1 | gpt-4o-mini | plan_exec | 9 | **6/9** | 39,183 | 260,040 | 17,900–1,157,879 | 21 | 11–86 |
| v1 | gpt-5.4-mini | react | 3 | **3/3** | 1,886 | 2,604 | 1,860–4,065 | 2 | 2–3 |
| v1 | gpt-5.4-mini | plan_exec | 3 | **3/3** | 11,524 | 12,448 | 8,573–17,248 | 8 | 7–9 |
| v2 | gpt-5.4-mini | react | 6 | **5/6** | 3,534 | 3,900 | 3,483–5,780 | 3 | 3–4 |
| v2 | gpt-5.4-mini | plan_exec | 6 | **6/6** | 9,719 | 11,999 | 8,694–21,895 | 7 | 7–11 |
| v2.1 | gpt-5.4-mini | react | 6 | **6/6** | 3,606 | 4,386 | 3,501–6,102 | 3 | 3–4 |

## (3) 해석

토큰과 호출 수는 두 모델·세 버전 모두에서 ReAct가 이겼다 — Plan-Execute는 토큰 6–10배, 호출 4–7배이며 원인은 축 1·3이다: ReAct는 데이터를 본 뒤 `count_pattern` 9개를 한 호출에 병렬로 던지지만, Plan-Execute는 planner→"Execute step i"×N→"Answer:" 강제라는 구조가 최소 호출 수를 강제하고(완벽한 계획인 행 22도 8회) 매 호출에 누적 이력을 재전송한다. 성공률은 하네스가 아니라 모델이 움직였다 — gpt-4o-mini의 실패 5건(정규식 `:00:` 리터럴, 존재하지 않는 도구로 짠 계획, 28단계 과대계획)은 하네스를 그대로 두고 gpt-5.4-mini로 바꾸자 0건이 됐고, 하네스는 그 실수를 **얼마나 비싸게** 실패시키느냐만 정했다(ReAct 2.5–3.6k 토큰으로 조용히, Plan-Execute 18k–1,158k로 시끄럽게 — 축 3의 단계 예산×재계획 상한이 종료가 아니라 낭비 상한으로 작동). v2에서 바로 그 두 지점에 검사를 넣자 비용 구조가 정반대로 갈렸다: 항상 실행되는 ReAct 검증 턴은 호출 +1, 토큰 ×1.87(1,886→3,534)을 냈고, 오류가 있을 때만 발동하는 Plan-Execute 계획 검증은 비용 0(`planfix` 0/6, 5.4-mini 계획은 전부 산문)이었으며, 둘 다 5.4-mini에서는 잡을 실패가 없어 성공률을 바꾸지 못했다. 검증 단계의 가치는 모델이 틀릴 때만 실현되므로 강한 모델 위에서는 순비용이다. 그리고 검증은 새 실패를 하나 만들었다 — 행 25에서 정답을 내고 도구로 재확인까지 한 모델이 `VERIFIED` 한 단어로 답하자 예산이 소진된 하네스가 그것을 최종 답으로 수용해 정답을 잃었고(내가 추가한 축 3 프로토콜의 파싱 실패), v2.1에서 마지막 Answer를 기억하도록 고치자 6/6이 됐다. 정리하면 이 태스크에서 **ReAct가 비용으로 이겼고 그 원인은 축 1·3**, 성공률 차이는 하네스보다 모델이 설명하며, 내가 넣은 검증은 이 모델에서는 비용(항상-검증 ×1.87, 조건부-검증 ×1.0)과 새 실패 모드 하나를 남겼다.

---

# 부록 — 근거 자료

## A. v1 gpt-4o-mini의 실패 5건 — 모델을 바꿔 다시 돌린 이유

| run | harness | 토큰 | 로그에서 본 것 | 걸린 축 |
|---|---|---|---|---|
| 7 | react | 3,642 | 정규식 `2026-09-01 09:00:.*ERROR`(분=00 리터럴) → 9개 시간대 전부 0 → *"There are no ERROR lines"* 라고 **확신하며 종료**. 직전 `read_file`로 ERROR 줄을 봤는데도 | 4(0은 정상값이라 무증상) + 3(자기 선언 종료 신뢰) |
| 15 | react | 2,552 | `ERROR [0-9]{1,2}:00`, `:01`, `:02`… **분 단위로** 매 스텝 1개씩 → 8회 상한 → `MAX_STEPS` | 3(상한이 작동, 무한루프는 막았으나 답은 없음) + 4 |
| 11 | plan_exec | 808,708 | **28단계** 계획, 매칭 불가 패턴 `'ERROR 00:00'`(로그는 시각이 ERROR 앞) → OFF_PLAN 25회 → 83회 호출 | 1(관찰 전 과대계획) + 3(단계 수 × 라운드 예산) |
| 12 | plan_exec | 17,900 | **2단계** 계획(`read_file`, `count_pattern('ERROR')`)뿐 → 시간대 집계 불가 → 예산 소진 → 포기 | 1(과소계획) + 3 |
| 16 | plan_exec | 37,287 | `analyze_result_for_hours()` 등 **존재하지 않는 도구** 3단계 → OFF_PLAN 5 → 재계획해도 가짜 → *"No ERROR lines"* | 1 + 4(재계획 1회 소진 후 무복구) |

gpt-4o-mini의 plan_exec 계획 9개 중 **8개에 실행 불가능한 단계**가 있었다. 성공한 6건도 예외가 아니며(OFF_PLAN 3–5회), 성공은 계획 *덕에* 아니라 executor가 단계 안에서 즉흥한 *덕에* 나왔다. gpt-5.4-mini의 계획(v1 3개, v2 6개)은 모두 3–4단계 산문으로 **가짜 도구 0**.

## B. v2의 실패 1건 — 행 25, 하네스 결함

| run | harness | 토큰 | 로그에서 본 것 | 걸린 축 |
|---|---|---|---|---|
| 25 | react v2 | 5,780 | `Answer: 14:00`(정답) → 검증 턴 → 모델이 **`count_pattern` 9개로 재확인**(14시=6, 전부 정답 방향) → 다음 턴에 **`VERIFIED` 한 단어** → 검증 예산이 소진된 하네스가 이 텍스트를 **최종 답으로 수용** → 판정기가 "VERIFIED"에서 14:00을 못 찾음 | **3** — 내가 추가한 종료 프로토콜의 결함. 검증 신호(`VERIFIED`)가 검증 턴 밖에서도 나올 수 있는데 하네스가 마지막 Answer를 기억하지 않았다 |

정답을 찾고, 도구로 재확인까지 하고, **하네스가 잃어버린** 경우다. v2.1에서 수정했고(`2e96e69`), 재측정 6회는 6/6.

## C. 그림

### C-1. 두 하네스의 제어 흐름 (v1)

```mermaid
flowchart LR
  subgraph R["ReAct — 대화 1개, 매 스텝 재판단"]
    direction TB
    R0["user: task"] --> R1["모델 호출<br/>Thought + tool_calls"]
    R1 -->|"tool_calls 있음"| R2["도구 실행<br/>여러 개 병렬 가능"]
    R2 -->|"Observation을 같은 대화에 추가 — 축1"| R1
    R2 -.->|"예외 → 'error: …' Observation — 축4"| R1
    R1 -->|"tool_calls 없음 — 축3"| R3["Answer 반환"]
    R1 -->|"step == max_steps 8 — 축3"| R4["MAX_STEPS: 미완"]
  end
  subgraph P["Plan-then-Execute — 대화 2개, 계획은 관찰 전 확정"]
    direction TB
    P0["planner 호출, 도구 없음<br/>→ JSON 단계 리스트 — 축1"] -->|"JSON 아님"| PX["즉시 실패 — 축4"]
    P0 --> P1["executor: 'Execute step i'"]
    P1 --> P2["모델 호출 + 도구 실행<br/>단계당 최대 3라운드 — 축3"]
    P2 -->|"완료"| P3{"남은 단계?"}
    P3 -->|"예"| P1
    P3 -->|"아니오"| P4["'Answer:' 강제 요청 — 축3"]
    P2 -->|"OFF_PLAN 또는 예산 초과"| P5{"재계획 남음?<br/>max_replan 1 — 축4"}
    P5 -->|"예"| P0
    P5 -->|"아니오, 다음 단계로"| P3
  end
```

### C-2. v2에서 추가한 검사와 행 25의 결함 경로

```mermaid
flowchart LR
  subgraph RV["ReAct v2 → v2.1 · 축3 종료 조건"]
    direction TB
    A0["모델: 'Answer: …'"] --> A1["검증 턴 1회<br/>'Observation과 모순 없나?'"]
    A1 -->|"'VERIFIED'"| A2["종료 · Answer 채택"]
    A1 -->|"수정된 Answer"| A3["종료 · 수정본 채택"]
    A1 -->|"도구 호출로 재개"| A4["도구 실행 → 루프 계속<br/>검증 예산 소진"]
    A4 --> A5["모델: 'VERIFIED' 한 단어"]
    A5 -->|"v2"| A6["텍스트 그대로 최종 답 → X<br/>행 25: 정답 14:00 유실"]
    A5 -->|"v2.1"| A7["기억해 둔 마지막 Answer 반환 → O"]
  end
  subgraph PV["Plan-Execute v2 · 축4 에러 복구"]
    direction TB
    B0["planner → JSON 계획"] --> B1{"계획 검증<br/>없는 도구? 인자 수?"}
    B1 -->|"문제 없음 — 5.4-mini 6/6"| B2["실행"]
    B1 -->|"문제 있음"| B3["planner에 위반 단계 + 실제 시그니처 제시<br/>max_plan_fixes 1"]
    B3 --> B1
    B3 -.->|"예산 소진"| B2
  end
```

### C-3. v1 실패 경로 — 같은 모델 실수가 하네스마다 어떻게 다르게 끝나는가

```mermaid
flowchart LR
  M["gpt-4o-mini의 실수<br/>정규식 ':00:' · 가짜 도구 · 과대/과소 계획"]
  M --> A0["ReAct"]
  A0 --> A1["잘못된 정규식 → 0 반환<br/>0은 정상 Observation<br/>축4: 감지 못 함"]
  A1 --> A2["모델: 'ERROR 없음' 선언<br/>축3: 검증 없이 종료"] --> A3["X · 3.6k 토큰<br/>싸고 조용함 — run 7"]
  A1 --> A4["패턴만 바꿔 한 스텝에 하나씩<br/>축3: 상한 8회 도달"] --> A5["X · 2.5k 토큰<br/>MAX_STEPS — run 15"]
  M --> B0["Plan-then-Execute"]
  B0 --> B1["관찰 전 계획 확정<br/>축1: 존재하지 않는 도구 단계"]
  B1 --> B2["단계마다 3라운드 소진 → OFF_PLAN<br/>축3"]
  B2 --> B3{"재계획 남음?<br/>축4"}
  B3 -->|"1회 사용"| B1
  B3 -->|"소진 → 다음 단계, 복구 없음"| B2
  B2 --> B4["X 또는 O · 18k–1,158k 토큰<br/>비싸고 시끄러움 — run 11 · 6"]
```

## D. 축 → 지표 대응

```mermaid
flowchart LR
  subgraph CAUSE["원인"]
    MOD["모델<br/>gpt-4o-mini → gpt-5.4-mini"]
    H13["하네스 축1·축3 (v1)<br/>컨텍스트 분리 · 단계 직렬화"]
    H3["하네스 축3 (v1)<br/>단계 예산 × 재계획 상한"]
    H4["하네스 축4 (v1)<br/>예외만 복구, 0은 통과"]
    V3["v2 축3 검증 턴 (ReAct)"]
    V4["v2 축4 계획 검증 (P-E)"]
  end
  subgraph METRIC["지표"]
    S["성공률<br/>실패 5/18 → 0/6"]
    T["토큰 배수<br/>6–10× · 두 모델 공통"]
    V["토큰 분산·폭주<br/>17.9k–1,158k → 8.6k–17.2k"]
    F["실패 비용<br/>ReAct 싸고 조용 · P-E 비싸고 시끄러움"]
    C["검사 비용<br/>항상-검증 ×1.87 · 조건부 ×0.84"]
    X["새 실패 모드<br/>행 25 'VERIFIED' 오인 → v2.1"]
  end
  MOD --> S
  H13 --> T
  MOD --> V
  H3 --> V
  H4 --> F
  H3 --> F
  V3 --> C
  V4 --> C
  V3 --> X
```

| 지표 | 무엇이 움직였나 | 근거 |
|---|---|---|
| 토큰 배수 (P-E / ReAct) | **하네스 v1 축 1·3** | 두 모델 모두 6–10× — 성공 여부와 무관 |
| 토큰 분산·폭주 | **모델 × 하네스 축 3** | 4o-mini P-E 17.9k–1,158k vs 5.4-mini 8.6k–17.2k |
| 호출 수 | **하네스 구조(축 3)** | P-E 최소 ~7 vs ReAct 최소 2; 행 22 완벽 계획도 8회 |
| 성공률 | **모델** | 5/18 실패 → 0/6; 하네스 내 차이는 1건 |
| 실패 비용 | **하네스** | ReAct 2.5–3.6k 조용 / P-E 18k–809k 시끄러움 |
| ReAct 검증 턴 (v2, 축 3) | 호출 +1, **토큰 ×1.87** | 컨텍스트 전체 재전송; 잡은 오류 0 (5.4-mini) |
| 검증 프로토콜 결함 (v2, 축 3) | 성공 1건 유실 (행 25) | `VERIFIED`를 답으로 오인 → v2.1 (6/6) |
| 계획 검증 (v2, 축 4) | 비용 0 (`planfix` 0/6), 토큰 ×0.84 | 산문 계획은 검사 대상이 아님 → 5.4-mini에서 무작동 |
| 개입 | 측정 불가 (전부 0) | 축 5는 react만 훅이 있고 plan_exec는 없음 |

## E. 한계

- **v2·v2.1은 gpt-5.4-mini에서만 돌렸다.** 두 검증이 겨냥한 실패(행 7·16형, 가짜 도구 계획)는 4o-mini에서만 났으므로, 검증이 그 실패를 실제로 **막는지**는 측정하지 않았다. 여기서 잰 것은 검증의 **비용**과 새 실패 모드다.
- 5.4-mini 표본은 v1 3회, v2 6회, v2.1 6회(ReAct)다. "실패율 0"은 표본 크기 안에서의 진술이다. Plan-Execute v2의 토큰 ×0.84는 노이즈 범위다.
- ReAct의 "읽고 머릿속으로 집계"(v1 행 19·20, v2 다수: `count_pattern` 0회)는 `app.log`가 3,022바이트로 `read_file`의 4,000자 캡 안에 들기 때문에 가능했다. 로그가 더 크면 축 2가 갈리는 조건이 되며 이번엔 재지 않았다.
- 스타터 executor의 `startswith("OFF_PLAN")` 판정은 모델이 `Answer: OFF_PLAN: …`으로 쓰면 걸리지 않는다(행 31·32). v1부터 있던 프로토콜 모호성이고 v2는 손대지 않았다.
- 스펙이 예고한 JSON 파싱 실패는 42회 중 0번. 축 5(개입)는 두 하네스 모두 훅이 비어 비교하지 못했다.

## F. 전체 42행 (`results.csv` 그대로; 행 1–18은 model/variant 태그 도입 전 = v1·gpt-4o-mini, 행 19–24는 v1)

| run | harness | success | tokens | iters | interventions | note |
|---|---|---|---|---|---|---|
| 1 | react | O | 3,715 | 3 | 0 |  |
| 2 | react | O | 7,212 | 5 | 0 |  |
| 3 | react | O | 3,812 | 3 | 0 |  |
| 4 | plan_exec | O | 147,023 | 27 | 0 | replans=1 |
| 5 | plan_exec | O | 29,246 | 13 | 0 | replans=1 |
| 6 | plan_exec | O | 1,157,879 | 86 | 0 | replans=1 |
| 7 | react | X | 3,642 | 3 | 0 |  |
| 8 | react | O | 3,754 | 3 | 0 |  |
| 9 | react | O | 11,031 | 6 | 0 |  |
| 10 | plan_exec | O | 35,779 | 15 | 0 | replans=1 |
| 11 | plan_exec | X | 808,708 | 83 | 0 | replans=1 |
| 12 | plan_exec | X | 17,900 | 11 | 0 | replans=1 |
| 13 | react | O | 5,399 | 4 | 0 |  |
| 14 | react | O | 3,710 | 3 | 0 |  |
| 15 | react | X | 2,552 | 8 | 0 |  |
| 16 | plan_exec | X | 37,287 | 21 | 0 | replans=1 |
| 17 | plan_exec | O | 67,358 | 21 | 0 | replans=1 |
| 18 | plan_exec | O | 39,183 | 13 | 0 | replans=1 |
| 19 | react | O | 1,860 | 2 | 0 | model=gpt-5.4-mini |
| 20 | react | O | 1,886 | 2 | 0 | model=gpt-5.4-mini |
| 21 | react | O | 4,065 | 3 | 0 | model=gpt-5.4-mini |
| 22 | plan_exec | O | 11,524 | 8 | 0 | model=gpt-5.4-mini;replans=0 |
| 23 | plan_exec | O | 17,248 | 9 | 0 | model=gpt-5.4-mini;replans=0 |
| 24 | plan_exec | O | 8,573 | 7 | 0 | model=gpt-5.4-mini;replans=0 |
| 25 | react | X | 5,780 | 4 | 0 | variant=v2;model=gpt-5.4-mini;verify=rejected-then-accepted |
| 26 | react | O | 3,535 | 3 | 0 | variant=v2;model=gpt-5.4-mini;verify=confirmed |
| 27 | react | O | 3,534 | 3 | 0 | variant=v2;model=gpt-5.4-mini;verify=confirmed |
| 28 | react | O | 3,541 | 3 | 0 | variant=v2;model=gpt-5.4-mini;verify=confirmed |
| 29 | react | O | 3,483 | 3 | 0 | variant=v2;model=gpt-5.4-mini;verify=confirmed |
| 30 | react | O | 3,530 | 3 | 0 | variant=v2;model=gpt-5.4-mini;verify=confirmed |
| 31 | plan_exec | O | 8,839 | 7 | 0 | variant=v2;model=gpt-5.4-mini;replans=0;planfix=0;invalid_left=0 |
| 32 | plan_exec | O | 8,694 | 7 | 0 | variant=v2;model=gpt-5.4-mini;replans=0;planfix=0;invalid_left=0 |
| 33 | plan_exec | O | 9,583 | 7 | 0 | variant=v2;model=gpt-5.4-mini;replans=0;planfix=0;invalid_left=0 |
| 34 | plan_exec | O | 9,855 | 7 | 0 | variant=v2;model=gpt-5.4-mini;replans=0;planfix=0;invalid_left=0 |
| 35 | plan_exec | O | 13,130 | 8 | 0 | variant=v2;model=gpt-5.4-mini;replans=0;planfix=0;invalid_left=0 |
| 36 | plan_exec | O | 21,895 | 11 | 0 | variant=v2;model=gpt-5.4-mini;replans=0;planfix=0;invalid_left=0 |
| 37 | react | O | 3,626 | 3 | 0 | variant=v2.1;model=gpt-5.4-mini;verify=revised |
| 38 | react | O | 3,585 | 3 | 0 | variant=v2.1;model=gpt-5.4-mini;verify=confirmed |
| 39 | react | O | 6,001 | 4 | 0 | variant=v2.1;model=gpt-5.4-mini;verify=confirmed |
| 40 | react | O | 3,503 | 3 | 0 | variant=v2.1;model=gpt-5.4-mini;verify=confirmed |
| 41 | react | O | 3,501 | 3 | 0 | variant=v2.1;model=gpt-5.4-mini;verify=confirmed |
| 42 | react | O | 6,102 | 4 | 0 | variant=v2.1;model=gpt-5.4-mini;verify=revised |

## G. 재현

```bash
pip install openai
export OPENAI_API_KEY=<key>                       # 커밋 안 함
cd submissions/26510124/week-02

# v1 (행 1–24): 최상위 harness_*.py는 지금 v2.1이므로 v1 원본을 먼저 되돌린다
cp v1_unmodified/harness_react.py v1_unmodified/harness_plan_execute.py .
python run_ab.py --runs 3 --tag v1                # ×3 → gpt-4o-mini (기본값)
AGENT_MODEL=gpt-5.4-mini python run_ab.py --runs 3 --tag v1

# v2 (행 25–36): git checkout a7b4fdf -- harness_react.py harness_plan_execute.py
AGENT_MODEL=gpt-5.4-mini python run_ab.py --runs 6 --tag v2

# v2.1 (행 37–42): git checkout 2e96e69 -- harness_react.py   (= 현재 최상위 파일)
AGENT_MODEL=gpt-5.4-mini python run_ab.py --runs 6 --tag v2.1 --only react
```

| 항목 | 값 |
|---|---|
| provider | openai (`ANTHROPIC_API_KEY` 미설정 → `tools_shared.py` 자동 선택), `OPENAI_BASE_URL` 미설정 |
| model | `gpt-4o-mini`(행 1–18, 기본값) / `gpt-5.4-mini`(행 19–42, `AGENT_MODEL`; reasoning effort 기본 `none`) |
| v1 하네스 설정 | `max_steps=8`, `max_replan=1`, `max_tool_rounds=3` (스타터 기본값) |
| v2 추가 설정 | ReAct `max_verify=1`; Plan-Execute `max_plan_fixes=1` |
| 도구 스키마 | `tools_shared.TOOL_SPECS` 무수정 |
| 코드 변경 커밋 | `571fcfd` run_ab note에 `model=` · `a7b4fdf` v2 하네스 + `--tag` + `v1_unmodified/` · `2e96e69` v2.1 + `--only` |
| 실행 커밋 | v1 `68de802` `195903a` `dcdfa84` `bd715c8` · v2 `6d591b0` · v2.1 `ff8a15b` |
| 입력 | `app.log` 스타터 원본, `TASK.md` `expected: 14:00` 선커밋 |
| 로그 | `logs/<harness>-<run>.txt` 42개, `run_ab.py`가 기록, 무편집 |

코딩 에이전트(Claude, Fable 5.1)를 세팅·실행·집계·초안에 썼다. 무엇을 시켰고 무엇을 버렸는지는 커밋 히스토리에 있다 — 첫 6회를 계획 확정 전에 돌려 폐기한 것(커밋 전이라 히스토리엔 없음), `max_tool_rounds` 3→9 변형을 검토 후 취소한 것, v1을 과제로 오해했다가 통제군으로 돌린 것, v2 ReAct의 종료 결함(행 25)과 v2.1 수정까지.
