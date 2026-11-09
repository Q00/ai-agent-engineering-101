# Week 02 — Harness A/B: ReAct vs Plan-then-Execute

같은 모델·태스크·툴에 하네스만 바꿔 각 3회 실행함. 효율(tokens·iters)은 ReAct가 크게 앞섰고(평균 약 12배·6배 적음), 성공률은 Plan-then-Execute가 앞섬(3/3 vs 2/3) → 지표별로 승자가 갈림. 원인은 3부에 정리함.

## 재현 조건 (모델·태스크·툴 고정, 하네스만 변화)

- Provider·model: OpenRouter(OpenAI 호환 API), `AGENT_MODEL=nvidia/nemotron-3.5-lightning:free`. API 키는 저장소에 없음.
- 환경변수: `OPENAI_BASE_URL=https://openrouter.ai/api/v1` · `OPENAI_API_KEY=<본인 키>`.
- Task(`TASK.md`): "app.log에서 ERROR 줄이 가장 많은 시각(HH:00)" · `expected: 14:00`.
- Tools(`tools_shared.py`, 두 하네스 공유): `read_file(path)`(파일 앞 4000자) · `count_pattern(path, pattern)`(정규식 매칭 줄 수).
- 실행: `python run_ab.py --runs 3` · 판정: 최종 답변에 `expected` 문자열(대소문자 무시) 포함 시 성공(O).

## 1. 변형 정의 — 다섯 축 중 무엇을 다르게 뒀나

```mermaid
flowchart LR
    subgraph R["ReAct — 하나의 평평한 루프"]
        direction TB
        r0([Task]) --> r1["model: Thought"]
        r1 -->|tool call| r2["run tool → Observation"]
        r2 --> r1
        r1 -->|"Answer (no tool)"| r3([Done])
        r1 -.->|"max_steps = 8"| r4([Stop: incomplete])
    end
    subgraph P["Plan-then-Execute — 계획 먼저, 스텝 실행"]
        direction TB
        p0([Task]) --> p1["Planner: 전체 계획을 JSON 리스트로"]
        p1 --> p2["Execute step i"]
        p2 -->|"tool calls, up to max_tool_rounds = 3"| p2
        p2 -->|"OFF_PLAN and replans < 1"| p3["남은 스텝 재계획"]
        p3 --> p2
        p2 -->|"step ok"| p4{"more steps?"}
        p4 -->|yes| p2
        p4 -->|no| p5([Final answer])
    end
```

| 축 | ReAct (`harness_react.py`) | Plan-then-Execute (`harness_plan_execute.py`) |
|---|---|---|
| **1. 맥락 관리** | 단일 대화. 매 콜마다 전체 히스토리 재전송 | 대화 2개(planner / executor)로 분리. executor도 히스토리 누적 |
| **2. 툴 입도** | `read_file`, `count_pattern` — **공유(동일)** | **동일** (의도적으로 고정) |
| **3. 종료 조건** | tool_call 없으면 종료, 아니면 `max_steps=8` 상한 | 계획 스텝 소진 시 종료. **스텝 내부는 `max_tool_rounds=3` 예산** |
| **4. 에러 복구** | 에러가 Observation으로 돌아와 다음 Thought에서 반응 | `OFF_PLAN:` 신호 → `max_replan=1`로 계획 1회 재작성 |
| **5. 인간 개입 지점** | `IRREVERSIBLE=∅` → 개입 0 | 개입 지점 없음(플래너·실행기 모두 자동) |

→ 실제로 달라진 축은 **1(맥락)·3(종료)·4(에러복구)**. 축2는 고정, 축5는 읽기전용 툴이라 두 하네스 모두 미발동.

## 2. 측정치 (`results.csv`)

| run | harness | success | tokens | iters | interventions | note |
|---|---|---|---|---|---|---|
| 1 | react | O | 4,747 | 2 | 0 | |
| 2 | react | O | 4,154 | 2 | 0 | |
| 3 | react | X | 6,903 | 4 | 0 | |
| 4 | plan_exec | O | 40,464 | 11 | 0 | replans=0 |
| 5 | plan_exec | O | 59,650 | 18 | 0 | replans=1 |
| 6 | plan_exec | O | 83,520 | 20 | 0 | replans=1 |

| 평균 | 성공률 | tokens | iters | interventions |
|---|---|---|---|---|
| **react** | 2/3 | ~5,268 | ~2.7 | 0 |
| **plan_exec** | **3/3** | ~61,211 | ~16.3 | 0 |

## 3. 해석 — 어느 하네스가 어느 지표로 이겼나

지표별로 승자가 갈림 → 효율(tokens·iters)은 ReAct가, 성공률은 Plan-then-Execute가 이김. 효율에서 ReAct는 평균 약 5,268토큰·2.7콜, Plan-then-Execute는 약 61,211토큰·16.3콜로 약 12배·6배 차이가 남. 이 격차를 만든 것은 고정된 툴 입도와 종료조건의 상호작용 → `count_pattern`이 시간대별 집계를 한 번에 못 해 어느 하네스든 시간마다 세야 하는데, ReAct는 이를 하나의 평평한 루프에서 흡수해 파일을 한 번 읽고 곧장 답으로 단락(short-circuit)지었으나(성공 런 2콜), Plan-then-Execute는 스텝마다 `max_tool_rounds=3` 예산에 걸려 `09:`,`10:`,`11:`,`14:`를 세다 예산을 초과하고 `OFF_PLAN`을 뱉어 재계획을 반복함(run5·6 로그의 "step exceeded the tool-call budget"→replan에서 확인). 재계획은 곧 추가 모델 콜이고, 매 콜이 전체 히스토리를 재전송하므로 토큰이 눈덩이처럼 불어남(run4→6: 40,464→59,650→83,520). 성공률은 반대로 Plan-then-Execute가 3/3, ReAct가 2/3인데 이 또한 종료조건에서 갈림 → ReAct의 유일한 실패(run3)는 `09:`·`10:`만 세고 `Answer:` 없이 빈 답으로 조기 종료한 것이라, 언제든 멈출 자유가 효율의 원천이자 실패의 원인이 됨. 반면 Plan-then-Execute는 계획 스텝을 소진할 때까지 강제로 진행하는 구조라 이런 조기 종료가 막혀 이 태스크에선 더 안정적이었음. 즉 종료조건이 효율과 성공률 두 지표를 모두 움직인 핵심 축이고, 툴 입도가 그 비용을 증폭시킨 배경임. 인간 개입은 어떤 지표도 움직이지 못함 → 읽기전용 툴에 `IRREVERSIBLE`이 비어 6런 모두 interventions=0. 벽시계 시간은 지표로 쓰지 않음(plan_exec 505→406→266초로 오히려 줄었으나 토큰은 증가 → 무료풀 혼잡도 변동일 뿐 하네스 성질이 아님).
