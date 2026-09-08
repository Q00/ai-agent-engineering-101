# Week 02 — Harness A/B: ReAct vs Plan-then-Execute

Student 26510126. Task, tools and success criterion are fixed in `TASK.md`
and were committed before the first run.

---

## 1. Variant definition

Both harnesses run the same task from `TASK.md`, call the same model through
the same `Chat` wrapper, and import the same two tools from
`tools_shared.py`. Of the five elements, two are set differently and three
are held constant.

### Held constant

**Tool granularity (element 2).** Both import `read_file` and
`count_pattern` from `tools_shared.py`; `TOOL_SPECS` is a single module-level
list neither harness modifies. The file is byte-identical to the starter.
Counting ERROR lines per hour therefore costs one `count_pattern` call per
hour in either harness.

**Human intervention point (element 5).** `IRREVERSIBLE` is the empty set in
`harness_react.py:20`, so the approval branch at `:43` is never entered.
Plan-then-Execute has no approval branch at all. `interventions` is 0 in all
twelve runs; on a read-only task this element cannot vary.

**Tool-level error recovery (element 4).** Every tool call in both harnesses
goes through `Chat.run_tools` (`tools_shared.py:181`), which wraps the call
in `try/except` and turns the exception text into the observation:

```python
except Exception as e:           # error recovery: the error is an Observation
    out = f"error: {e}"
```

Neither harness stops on a tool error, and neither sees a different error
than the other would.

### Set differently

**Context management (element 1).** ReAct keeps one conversation. `Chat` is
constructed once (`harness_react.py:30`) and every assistant reply and tool
result is appended to the same message list, so step *n* sees everything from
steps 1..*n*-1.

Plan-then-Execute keeps two. The `planner` (`harness_plan_execute.py:42`) is
built with `tools=False`: it sees the task and the tool *names* but never a
tool result. The `executor` (`:53`) receives the task and the whole plan as
JSON up front (`:54`), then one `Execute step N` user message per step
(`:58`), accumulating across every step. On a replan the failure text is
appended to the **planner's** history (`:73`), not the executor's, so the two
conversations hold different views of the run from that point on.

Both `Chat` objects share one `Meter`, so `iters` counts model calls across
the planner and the executor together.

**Termination condition (element 3).** ReAct has two exits: the model returns
a reply with no tool calls (`harness_react.py:38`), or the loop reaches
`max_steps=8` (`:33`, falling through to `:52`). The model decides when the
task is done.

Plan-then-Execute is bounded by the plan instead. The outer loop runs
`while i < len(plan)` (`harness_plan_execute.py:57`) — the number of
iterations is fixed by how many steps the planner wrote, and there is no
early exit: a step that already contains the final answer does not stop the
loop. After the plan is exhausted the harness forces one more call asking for
the answer (`:85`), and one more again if that call still requests tools
(`:87`). Two inner bounds apply: `max_tool_rounds=3` per step (`:65`) and a
hard exit if the plan does not parse as a JSON list (`:47`).

### One classification left open

Plan-then-Execute has a second recovery path that ReAct does not: a step
whose reply starts with `OFF_PLAN` triggers one rebuild of the remaining plan
(`:71`–`:82`, `max_replan=1`). Whether that belongs under error recovery
(element 4) or under the termination/flexibility budget (element 3) is a
judgment call — the lecture defines element 4 in terms of tool exceptions and
malformed arguments, and `OFF_PLAN` is raised by the model or by the
tool-round budget, not by a tool throwing. It fired in exactly one of the
twelve runs (`logs/plan_exec-05.txt`).

## 2. Measurements

Twelve runs, six per model, three per harness per model. Failed runs are
kept. `interventions` is 0 in every row and is omitted from the tables below.

### Set A — `nvidia/nemotron-3.5-lightning:free` via OpenRouter

| run | harness | success | tokens | iters | wall | note |
|---:|---|:---:|---:|---:|---:|---|
| 1 | react | X | 22,803 | 8 | 56.3s | MAX_STEPS reached |
| 2 | react | O | 3,833 | 2 | 43.1s | |
| 3 | react | O | 3,785 | 2 | 26.4s | |
| 4 | plan_exec | O | 53,613 | 13 | 395.5s | replans=0 |
| 5 | plan_exec | X | — | — | 449.1s | 429 free-tier daily cap |
| 6 | plan_exec | X | — | — | 1.4s | 429 free-tier daily cap |

### Set B — `claude-sonnet-5` via the Anthropic API

| run | harness | success | tokens | iters | wall | note |
|---:|---|:---:|---:|---:|---:|---|
| 7 | react | O | 6,154 | 3 | 9.1s | |
| 8 | react | O | 5,895 | 3 | 6.6s | |
| 9 | react | O | 5,861 | 3 | 6.6s | |
| 10 | plan_exec | O | 55,054 | 12 | 50.4s | replans=0 |
| 11 | plan_exec | O | 24,088 | 9 | 22.5s | replans=0 |
| 12 | plan_exec | O | 34,920 | 11 | 31.2s | replans=0 |

### Harness comparison within Set B

Set B is the comparable one: the model, the task and the tools are fixed and
both harnesses completed three runs.

| | react | plan_exec | ratio |
|---|---:|---:|---:|
| success | 3/3 | 3/3 | — |
| tokens, median | 5,895 | 34,920 | 5.9× |
| tokens, total | 17,910 | 114,062 | 6.4× |
| iters, median | 3 | 11 | 3.7× |
| wall, median | 6.6s | 31.2s | 4.7× |
| tokens, spread | 5,861–6,154 (±2%) | 24,088–55,054 (±44%) | |

### Same harness across models

| harness | metric | Set A (free) | Set B (Sonnet 5) |
|---|---|---|---|
| react | success | 2/3 | 3/3 |
| react | iters | 2, 2, 8 | 3, 3, 3 |
| react | tokens | 3,785–22,803 | 5,861–6,154 |
| plan_exec | success | 1/1 measured | 3/3 |
| plan_exec | iters | 13 | 9, 11, 12 |

### Cost

Set B consumed 131,972 tokens in total (react 17,910; plan_exec 114,062). At
the Claude Sonnet 5 rate of $2 / $10 per MTok, and assuming 80–90% of those
tokens are input — an agent loop resends its history on every call, and no
prompt caching was used — the set cost roughly **$0.37–$0.48**: about $0.02
per ReAct run and $0.12 per Plan-then-Execute run.

The estimate is a range rather than a figure because `Meter.add` sums input
and output into one counter, so the split cannot be recovered from
`results.csv`.

### How to reproduce

```bash
cp -r weeks/week-02/starter/. submissions/26510126/week-02
cd submissions/26510126/week-02

# Set A
env -u ANTHROPIC_API_KEY \
    OPENAI_BASE_URL=https://openrouter.ai/api/v1 \
    OPENAI_API_KEY=<openrouter key> \
    AGENT_MODEL=nvidia/nemotron-3.5-lightning:free \
    python run_ab.py --runs 3

# Set B
ANTHROPIC_API_KEY=<console key> AGENT_MODEL=claude-sonnet-5 \
    python run_ab.py --runs 3
```

Python 3.12.14; `openai` 3.6.0 for Set A, `anthropic` 1.4.0 for Set B.
Console captures are in `logs/`, one file per run, named `<harness>-<run>.txt`.

### Reading the tables

Four things about the measurements themselves, before any interpretation:

1. **Runs 5 and 6 did not fail at the task.** They hit OpenRouter's free-tier
   daily cap of 50 requests (`X-RateLimit-Remaining: 0`). The cause is the
   account, not the harness, so Set A cannot be used to compare success rates
   between the two harnesses. Set B exists for that reason.
2. **Crashed runs report no metrics.** `run_ab.py` discards the `Meter` when
   it catches an exception, so runs 5 and 6 have empty token and iteration
   cells even though run 5 had made roughly eleven model calls before dying.
   Set A's plan_exec token figure is therefore an undercount of what was
   actually spent.
3. **The `note` column carries `provider:model` from run 7 onward.** Rows 1–6
   predate that change; they were all
   `nvidia/nemotron-3.5-lightning:free`, recorded in the commit that added them.
4. **The success criterion checks the last `Answer:` line, not the whole
   response.** A plain substring match over the full text would score a run O
   whenever it named 14:00 anywhere while concluding otherwise, and the two
   harnesses do not end the same way, so that error would not have fallen
   equally on them. Changed in `run_ab.py` and stated in `TASK.md` before the
   first run.

---

## 3. Interpretation

> **TODO — 직접 작성.** 한 문단. 어느 하네스가 어떤 지표로 이겼고 왜인지가
> 아니라, **어느 요소의 차이가 어느 지표를 움직였는지**를 로그에서 근거를
> 들어 설명한다. 채점의 절반이 여기다.
>
> 로그에 남아 있는 근거들:
>
> - `logs/react-01.txt` — 09시부터 15시까지 한 시간씩 세다 8스텝을 다 쓰고
>   `MAX_STEPS reached`. 14시가 6건이라는 답을 이미 손에 쥔 상태였다.
> - `logs/react-07.txt` — 파일을 읽고 14시만 한 번 검증하고 3스텝에 종료.
> - `logs/plan_exec-10.txt` — 계획서 3단계의 `HH:00.*ERROR`를 실행기가 문자
>   그대로 따라 아홉 시간대 전부 0을 받았다. step 1에서 이미 센 값이 컨텍스트에
>   남아 있어 step 4가 그리로 되돌아가 답을 건졌다. 같은 실행에 도구 인자
>   누락 에러가 하나 있고 Observation으로 돌아가 다음 호출에서 교정됐다.
> - `logs/plan_exec-05.txt` — `max_tool_rounds`와 `max_replan`이 실제로
>   발동한 유일한 실행. 6단계 계획이 5단계로 재작성됐다.
> - run 10·11·12 모두 `[step 1]`에서 `Answer: 14:00`이 나왔는데 계획의 나머지
>   단계를 끝까지 걷는다.
