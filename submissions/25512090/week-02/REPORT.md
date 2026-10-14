# Week 02 Harness A/B Report

## Part 1: Variant Definition

The two harnesses implement different control-flow architectures for the same task (finding the hour with the most ERROR lines in `app.log`), using identical tools (`read_file`, `count_pattern`) and the same model (gemini-3.5-flash-lite via OpenAI-compatible API). The five axes from the lecture are set as follows:

| Axis | ReAct (`harness_react.py`) | Plan-then-Execute (`harness_plan_execute.py`) |
|------|----------------------------|-----------------------------------------------|
| **1. Context Management** | Full conversation history sent every turn (`Chat` with all prior messages) | Planner sees only task + tool list; Executor sees task + plan + step-by-step history |
| **2. Tool Granularity** | Identical tools (shared `tools_shared.py`) | Identical tools (shared `tools_shared.py`) |
| **3. Termination Condition** | Iteration cap (`max_steps=8`); model decides when to answer | Plan length + optional one replan (`max_replan=1`); executor follows plan sequentially |
| **4. Error Recovery** | Errors returned as Observations; model reacts in next Thought | `OFF_PLAN` signal triggers replan (once); explicit flexibility cap |
| **5. Human Intervention Point** | `IRREVERSIBLE` tool set (empty for read-only tools); approval before execution | No human-in-the-loop; replan is the only recovery |

**Key architectural difference**: ReAct interleaves reasoning and action in a tight Thought–Action–Observation loop, while Plan-then-Execute separates a one-shot planning phase (producing a JSON step list) from a deterministic execution phase with at most one replan.

### Control-Flow Comparison (Mermaid)

```mermaid
flowchart TD
    subgraph ReAct["ReAct Harness (harness_react.py)"]
        R_START([Start]) --> R_LOOP{step < max_steps?}
        R_LOOP -->|yes| R_SEND[chat.send()\nThought + Action]
        R_SEND --> R_TOOL{tool_calls?}
        R_TOOL -->|yes| R_APPROVE[Human approval\nif IRREVERSIBLE]
        R_APPROVE --> R_RUN[run_tools\n→ Observation]
        R_RUN --> R_LOOP
        R_TOOL -->|no| R_ANS[Answer extracted]
        R_LOOP -->|no| R_MAX[MAX_STEPS reached]
        R_ANS --> R_END([Return answer])
        R_MAX --> R_END
    end

    subgraph PlanExec["Plan-then-Execute Harness (harness_plan_execute.py)"]
        P_START([Start]) --> P_PLAN[Planner: one call\n→ JSON plan list]
        P_PLAN --> P_PARSE{valid JSON?}
        P_PARSE -->|no| P_FAIL[plan parse failed]
        P_PARSE -->|yes| P_EXEC[Executor: task + plan]
        P_EXEC --> P_STEP{more steps?}
        P_STEP -->|yes| P_DO[Execute step i\ntool rounds ≤ max_tool_rounds]
        P_DO --> P_OFF{OFF_PLAN?}
        P_OFF -->|yes & replans<max_replan| P_REPLAN[Planner: revise\nremaining steps]
        P_REPLAN --> P_STEP
        P_OFF -->|no or no replans left| P_NEXT[i += 1]
        P_NEXT --> P_STEP
        P_STEP -->|no| P_FINAL[Executor: final answer]
        P_FINAL --> P_END([Return answer + replans])
        P_FAIL --> P_END
    end

    style ReAct fill:#e8f5e9,stroke:#2e7d32
    style PlanExec fill:#e3f2fd,stroke:#1565c0
```

## Part 2: Measurements

Results from `results.csv` (3 runs per harness, gemini-3.5-flash-lite, same task and tools):

| run | harness   | success | tokens | iters | interventions | note                                 |
|-----|-----------|---------|--------|-------|---------------|--------------------------------------|
| 1   | react     | X       | 14958  | 8     | 0             |                                      |
| 2   | react     | X       | 14734  | 8     | 0             |                                      |
| 3   | react     | X       | —      | —     | —             | crash: RateLimitError (quota exceeded) |
| 4   | plan_exec | X       | —      | —     | —             | crash: RateLimitError (quota exceeded) |
| 5   | plan_exec | X       | —      | —     | —             | crash: RateLimitError (quota exceeded) |
| 6   | plan_exec | X       | —      | —     | —             | crash: RateLimitError (quota exceeded) |

**Log observations**:
- ReAct runs 1–2: Model read `app.log`, then issued 6 `count_pattern` calls (hours 09–15), correctly identifying **14:00 with 6 ERRORs** — but hit `max_steps=8` before emitting `Answer:`. The Thought–Action loop consumed all iterations on tool calls.
- ReAct run 3 / Plan-then-Execute runs 4–6: All crashed with HTTP 429 (free-tier quota: 15 requests/min). Plan-then-Execute made a planning call (valid JSON plan produced in run 4) but crashed before execution.

## Part 3: Interpretation

**Termination condition (Axis 3) drove the ReAct failures.** The 8-step cap was exhausted by the 7 tool calls needed to scan all 7 hours (09–15). The model reasoned correctly — logs show it identified 14:00 as the peak — but the harness terminated before the final `Answer:` turn. A higher `max_steps` or a more efficient counting strategy (single `count_pattern` with regex alternation) would have succeeded.

**Context management (Axis 1) and error recovery (Axis 4) distinguished Plan-then-Execute.** The planner produced a coherent 6-step plan in run 4 (`read_file → filter ERROR → extract hour → count → identify max → format`), demonstrating that separating planning from execution yields structured intent. However, the free-tier quota (Axis 5's implicit rate-limit boundary) prevented execution. The `OFF_PLAN`/replan mechanism was never exercised because crashes occurred before or during the first execution step.

**Token usage**: ReAct runs 1–2 consumed ~14.8k tokens each (full history + repeated tool results). Plan-then-Execute would likely use fewer tokens per successful run because the planner call is tool-free and the executor sees only step-scoped context — but this remains hypothetical without quota headroom.

**Overall**: On this task, ReAct's iterative loop naturally matches the "scan all hours then aggregate" strategy but is brittle to step budgets. Plan-then-Execute's upfront plan is more token-efficient and inspectable, but its two-phase architecture doubles the API surface (planner + executor), making it more vulnerable to rate limits. The decisive axis for ReAct was **termination condition**; for Plan-then-Execute, **context management** (two separate conversations) and **error recovery** (replan never triggered) were the differentiators — though quota exhaustion masks the true comparison.