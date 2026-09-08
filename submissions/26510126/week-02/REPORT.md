# Week 02 — Harness A/B: ReAct vs Plan-then-Execute

Student 26510126. Task, tools and success criterion are fixed in `TASK.md`
and were committed before the first run.

---

## 1. Variant definition

Both harnesses run the same task from `TASK.md`, call the same model through
the same `Chat` wrapper, and import the same two tools from
`tools_shared.py`. Of the five elements, two are set differently and three
are held constant.

### Structure

```mermaid
flowchart TB
  subgraph SH["tools_shared.py — one tool set per experiment, shared by both harnesses"]
    TS{"AGENT_TOOLSET"}
    TS -->|fine| FI["read_file + count_pattern<br/>one call per hour to tally"]
    TS -->|coarse| CO["read_file + count_by_hour<br/>whole tally in one call"]
    RT["Chat.run_tools — a tool exception<br/>becomes the observation (element 4)"]
    MT["Meter — tokens, iters, interventions<br/>shared by every Chat in a run"]
  end

  subgraph RA["harness_react.py — the model decides when it is done"]
    R1["Chat — one history,<br/>every step sees all earlier ones (element 1)"]
    R1 --> R2{"step &lt; max_steps = 8 ?<br/>(element 3)"}
    R2 -->|no| R7(["MAX_STEPS reached: incomplete"])
    R2 -->|yes| R3["send"]
    R3 --> R4{"tool calls in the reply ?"}
    R4 -->|no| R5(["Answer — element 3, model chose to stop"])
    R4 -->|yes| R6["run_tools"]
    R6 --> R2
  end

  subgraph PE["harness_plan_execute.py — the plan decides when it is done"]
    P1["planner Chat — tools=False,<br/>never sees a tool result (element 1)"]
    P1 --> P2{"reply parses as a JSON list ?"}
    P2 -->|no| P3(["plan parse failed"])
    P2 -->|yes| P4["executor Chat — plan up front,<br/>every step accumulates (element 1)"]
    P4 --> P5["execute step i"]
    P5 --> P6{"tool rounds &lt; max_tool_rounds = 3 ?"}
    P6 -->|exceeded| P7["OFF_PLAN"]
    P7 --> P9{"replans &lt; max_replan = 1 ?<br/>(element 3)"}
    P9 -->|yes| P1
    P9 -->|no| P8
    P6 -->|ok| P8{"steps remaining ?<br/>no early exit (element 3)"}
    P8 -->|yes| P5
    P8 -->|no| P10["forced call: give the final answer"]
    P10 --> P11(["Answer"])
  end
```

The two loops differ in what ends them. ReAct's exit is a property of the
reply — no tool calls means done — with the step cap as a backstop.
Plan-then-Execute's exit is a property of the plan: the loop runs once per
step the planner wrote, and a step that already contains the answer does not
shorten it.

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

### Where the replan path belongs

Plan-then-Execute has a second recovery path that ReAct does not: a step
whose reply starts with `OFF_PLAN` triggers one rebuild of the remaining plan
(`harness_plan_execute.py:71`–`:82`, `max_replan=1`). It is counted here
under the termination condition (element 3), not error recovery (element 4).

The lecture defines element 4 as what the harness does when a tool throws an
exception or is called with malformed arguments, and that path is the shared
`try/except` in `Chat.run_tools` described above — identical in both
harnesses. `OFF_PLAN` is not raised by a tool. It comes either from the model
declaring the step impossible as planned, or from the harness itself when a
step exceeds `max_tool_rounds` (`:65`), and its remedy is a counter that caps
how many times the loop may restructure itself. `max_replan=1` is a bound of
the same kind as `max_steps=8` and `max_tool_rounds=3`: a limit on how long
and in what shape the loop is allowed to continue.

Each path fired exactly once in the twelve runs, and in different runs. The
only genuine tool error is in `logs/plan_exec-10.txt` —
`count_pattern() missing 1 required positional argument: 'pattern'` — which
went through `run_tools`, came back as an observation, and was corrected on
the next call; that is element 4, and ReAct would have handled it the same
way. The only `OFF_PLAN` is in `logs/plan_exec-05.txt`, where a step ran past
the tool-round budget and the plan was rebuilt from six steps to five.

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

The element that moved the numbers is the termination condition. ReAct ends
when the model stops asking for tools, so the model decides it is finished as
soon as it holds the answer; Plan-then-Execute ends when the plan list runs
out, so the number of model calls is fixed by how many steps the planner
happened to write, and the answer arriving early changes nothing. All three
Sonnet runs show that directly — `plan_exec-10`, `-11` and `-12` each print
`Answer: 14:00` at `[step 1]` and then walk the remaining steps anyway,
finishing at 9, 11 and 12 iterations against ReAct's flat 3. The token gap
follows from that gap rather than standing on its own: it is the same
difference compounding, because every extra step is both one more call and
one more block of history carried into every call after it, which is why
tokens grew 6.4× while calls grew only 3.7×. Success is where the two
elements meet instead of acting alone. Both harnesses answered correctly in
every Sonnet run, and ReAct's single failure — `react-01` on the free model —
was not caused by its step cap by itself: the harness spent one
`count_pattern` call per hour because that is the only counting tool it has,
reached `MAX_STEPS` at the eighth step with 09:00 through 15:00 counted, and
stopped holding the correct answer of 6 errors at 14:00 without ever
reporting it. A coarser tool would have fit inside eight steps, and a higher
cap would have let the fine-grained approach finish; neither the termination
condition nor the tool granularity produced that failure alone. The same tool
set cost Plan-then-Execute nothing, because it has no global step cap for a
long chain of calls to run into — the rigidity that made it expensive is also
what kept it from failing this way.
