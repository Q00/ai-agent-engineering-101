# Week 02 — Harness A/B: ReAct vs Plan-then-Execute

## Setup

Everything except the API key, so the runs can be reproduced.

- Provider: OpenAI-compatible endpoint (OpenRouter), `OPENAI_BASE_URL=https://openrouter.ai/api/v1`
- Model: `nvidia/nemotron-3.5-lightning:free` — the same model for both harnesses
- Tools (same set for both harnesses, imported from `tools_shared.py`):
  - `read_file(path)` — first 4000 characters of a text file in the working directory
  - `count_pattern(path, pattern)` — number of lines matching a regular expression
- Input: `app.log`, unchanged
- Limits: ReAct `max_steps=8`; Plan-and-Execute `max_replan=1`, `max_tool_rounds=3`
- Success criterion: fixed in `TASK.md` before the runs — the final answer must contain `14:00`
- How to run:

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<your openrouter key>
export AGENT_MODEL=nvidia/nemotron-3.5-lightning:free
python run_ab.py --runs 3
```

## 1. Harness Variation

I compared the two harnesses implemented in run_ab.py: ReAct and Plan-and-Execute. The main differences were in context management, termination, and error recovery. ReAct repeatedly reasons and acts using the accumulated trajectory until reaching max_steps, while Plan-and-Execute first creates an explicit plan and executes each planned step separately. It can also replan when execution goes OFF_PLAN. The available tools were kept the same, and human intervention was not enabled because no irreversible actions were defined.

| Axis | ReAct | Plan-and-Execute |
|---|---|---|
| 1. Context management | One conversation, full history accumulates | Two conversations: a planner without tools and an executor with tools |
| 2. Tool granularity | Same `tools_shared` tools | Same, but tools are hidden during planning (`tools=False`) |
| 3. Termination | Model stops calling tools, or `max_steps=8` | Plan length decides; `max_tool_rounds=3` per step |
| 4. Error recovery | Errors return as Observations, the model re-decides | Explicit OFF_PLAN signal, replanning capped at 1 |
| 5. Human intervention | `IRREVERSIBLE` set with an approval hook (empty here) | No hook in the code |

## 2. Results

The following results were recorded in results.csv.

| Run | Harness | Success | Tokens | Iterations | Interventions | Note |
|---|---|---|---|---|---|---|
| 1 | react | O | 3,783 | 2 | 0 | - |
| 2 | react | X | 23,625 | 8 | 0 | hit max_steps=8 |
| 3 | react | X | 1,224 | 1 | 0 | 600s, empty response |
| 4 | plan_exec | O | 48,295 | 16 | 0 | replans=1 |
| 5 | plan_exec | O | 43,589 | 13 | 0 | replans=1 |
| 6 | plan_exec | X | 144 | 1 | 0 | replans=0, plan parse failed |

## 3. Interpretation

The results show a trade-off between simplicity and robustness. For termination, ReAct run 2 exhausted max_steps=8, whereas Plan-and-Execute was governed by its explicit plan and completed successful runs in 13–16 iterations. This robustness came with much higher context and model-call cost: run 1 used 3,783 tokens, while successful Plan-and-Execute runs used about 44K–48K tokens.

Error recovery also differed. Runs 4 and 5 went OFF_PLAN but recovered through one replanning step and still succeeded. However, Plan-and-Execute introduced an additional failure mode: run 6 failed because the planner output could not be parsed as the required structured plan. Finally, all runs had interventions=0, so the human-intervention axis was not meaningfully evaluated in this experiment.
