# Week 02 — Harness A/B: ReAct vs Plan-then-Execute

Model, task and tools are held constant; only the harness varies.

- **Provider / model:** OpenRouter (OpenAI-compatible API), `nvidia/nemotron-3.5-lightning:free`
- **Task:** `TASK.md` — "In app.log, which hour (HH:00) has the most ERROR lines?"
- **Success criterion:** the `expected:` string `14:00` appears in the final answer (`run_ab.py:judge`). Ground truth in `app.log` is hour 14 with 6 ERROR lines.
- **Tools (identical for both harnesses, `tools_shared.py`):**
  - `read_file(path)` — first 4000 characters of a file inside the working directory
  - `count_pattern(path, pattern)` — number of lines matching a regex
- **How to run:**

  ```bash
  cd submissions/26512071/week-02
  export OPENAI_BASE_URL=https://openrouter.ai/api/v1
  export OPENAI_API_KEY=<your openrouter key>
  export AGENT_MODEL=nvidia/nemotron-3.5-lightning:free
  python run_ab.py --runs 3
  ```

## 1. Variant definition — which of the five axes differ

| Axis | ReAct (`harness_react.py`) | Plan-then-Execute (`harness_plan_execute.py`) | Differs? |
|---|---|---|---|
| 1 Context management | One `Chat`; the full history (thoughts, tool calls, observations) is resent on every call | Two `Chat` objects: a tool-less planner and an executor. The executor keeps the full transcript and gets one new `Execute step i` user message per step; the planner only sees the task and any failure message | **yes** |
| 2 Tool granularity | `read_file` + `count_pattern`, identical | identical | no (held constant) |
| 3 Termination condition | Model-decided: it stops when it returns a reply with no tool call. Hard cap `max_steps=8` | Plan-decided: the loop ends when every step of the JSON plan has been executed, then one forced final-answer call. Per-step cap `max_tool_rounds=3` | **yes** |
| 4 Error recovery | Tool errors come back as ordinary observations; the model decides what to do next. No separate recovery path | A step that reports `OFF_PLAN:` (or blows the tool-round budget) triggers one replan (`max_replan=1`); the planner is asked for a fresh JSON list of remaining steps. If that reply is not parseable JSON the loop breaks | **yes** |
| 5 Human intervention point | `IRREVERSIBLE` set, empty here (read-only tools), so no approval prompt fires | none at all | no in effect (both 0 interventions) |

So three axes move: **context management, termination condition, error recovery.** Tool granularity is held constant by design, and the intervention point is untested because both tools are read-only.

## 2. Measurements

From `results.csv` verbatim (`O` = success, `X` = failure):

| run | harness | success | tokens | iters | interventions | note |
|---|---|---|---|---|---|---|
| 1 | react | O | 4243 | 2 | 0 | |
| 0 | react | O | 3967 | 2 | 0 | |
| 1 | react | X | 20123 | 8 | 0 | |
| 2 | react | O | 4146 | 2 | 0 | |
| 3 | plan_exec | O | 21050 | 8 | 0 | replans=1 |
| 4 | plan_exec | O | 70752 | 13 | 0 | replans=0 |
| 5 | plan_exec | O | 75626 | 14 | 0 | replans=0 |

Aggregates over the six starter runs (rows 0–5):

| harness | success | tokens (min / median / max) | iters (min / median / max) | interventions |
|---|---|---|---|---|
| react | 2/3 | 3967 / 4146 / 20123 | 2 / 2 / 8 | 0 |
| plan_exec | 3/3 | 21050 / 70752 / 75626 | 8 / 13 / 14 | 0 |

**Run accounting.** Rows 0–5 are the six graded starter runs from a single `python run_ab.py --runs 3`; their per-run captures are `logs/react-00.txt`, `react-01.txt`, `react-02.txt`, `plan_exec-03.txt`, `plan_exec-04.txt`, `plan_exec-05.txt`. `logs/console-0908-2033.txt` is the `tee` capture of that whole batch. The row numbered `1` at the top of `results.csv` is an earlier ReAct run; it is left in place rather than deleted. <!-- TODO(hb): say in one line how that first row was produced (standalone `python harness_react.py`? an earlier run_ab invocation?) so the record is honest, or drop the row and say so. -->

## 3. Interpretation

Evidence in the logs, before the reading of it:

- `react-00.txt`, `react-02.txt` — 2 iterations, ~4k tokens: one `read_file`, then the model counted the hours in its own head and answered. No `count_pattern` call at all.
- `react-01.txt` — the only failure. The model went hour by hour with `count_pattern` (`09:`→1, `10:`→2, `11:`→1, `12:`→3, `13:`→2, `14:`→6, `15:`→2), used all 8 steps on tool calls and ended on `MAX_STEPS reached: incomplete`. The counts it needed were already in the transcript; it never got a turn in which to say them. 20123 tokens, 5× the successful ReAct runs.
- `plan_exec-03.txt` — step 1 hit `max_tool_rounds`, so the harness emitted `OFF_PLAN: step exceeded the tool-call budget`; the replan came back as prose (`"Here's a thinking process:\n\n1. **Analyze User Input:**…"`), `parse_plan` returned `None`, and the execute loop broke. The forced final-answer call still produced `Answer: 14:00`, so the run is scored `O` at 21050 tokens.
- `plan_exec-04.txt`, `plan_exec-05.txt` — `Answer: 14:00` already appears at step 1, but the loop kept executing steps 2–6 and re-read `app.log` two or three more times. 13–14 iterations, 70–76k tokens. In `plan_exec-04.txt` step 2 answered `12:00` (wrong) and step 5 dropped into visible scratch reasoning; the final answer was right anyway.
- Interventions are 0 in every row: `IRREVERSIBLE` is empty and Plan-then-Execute has no approval hook, so this A/B says nothing about axis 5.

<!-- TODO(hb): one paragraph, your own words. Answer three things with the evidence above:
     (a) which harness won on success, and which axis is responsible;
     (b) which harness won on tokens/iters, and which axis is responsible;
     (c) what the plan_exec-03 replan-parse failure shows about axis 4 — note that it broke the recovery path and the run still passed.
     Do not just declare a winner. -->
