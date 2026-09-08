# Week 02 — Harness A/B Report

## 1. Variant definition and reproducibility

The task, success criterion, model, and tools were held constant inside each
A/B batch. The provider was OpenRouter through its OpenAI-compatible endpoint
(`https://openrouter.ai/api/v1`) with OpenAI Python SDK 3.8.0. Runs 1–6 were an
initial pilot with `poolside/laguna-s-2.1:free`; provider rate limits and a 400
response made that batch unusable for a clean comparison, but the failed runs
remain in the data. Runs 7–12 used
`nvidia/nemotron-3.5-lightning:free`: runs 7–9 are ReAct and runs 10–12 are
Plan-then-Execute. The shared tools were `read_file(path)` (return the UTF-8
file contents) and `count_pattern(path, pattern)` (count regex-matching lines).
No API key is stored in the repository.

The harness variants set the five axes as follows. **Context management:**
ReAct keeps one full conversation across every thought, tool call, and
observation; Plan-then-Execute separates a tool-free planner conversation from
an executor conversation. **Tool granularity:** both variants use the same two
shared, read-only tools. **Termination:** ReAct stops when the model makes no
tool call or after eight steps; Plan-then-Execute first requires a JSON step
list, executes the finite list, limits tool rounds per step to three, and then
asks for a final answer. **Error recovery:** ReAct returns tool errors as
observations so the same loop can adapt; Plan-then-Execute uses `OFF_PLAN` and
allows at most one replan, while invalid plan JSON fails before execution.
**Human intervention:** both variants require approval only for tools in the
empty `IRREVERSIBLE` set, so the expected intervention count is zero.

Reproduce the comparison from this directory after setting
`OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `AGENT_MODEL`:

```powershell
$env:OPENAI_BASE_URL = "https://openrouter.ai/api/v1"
$env:AGENT_MODEL = "nvidia/nemotron-3.5-lightning:free"
& "C:\venvs\submissions\Scripts\python.exe" run_ab.py --runs 3
```

The shared OpenAI client uses a 120-second per-call timeout with automatic SDK
retries disabled, so a stalled provider call becomes a recorded failed run.

## 2. Measurements

| run | harness | success | tokens | iters | interventions | note |
|---:|---|:---:|---:|---:|---:|---|
| 1 | react | X | — | — | — | provider 429 |
| 2 | react | X | — | — | — | provider 400 (`reasoning_content`) |
| 3 | react | X | — | — | — | provider 429 |
| 4 | plan_exec | X | 175 | 1 | 0 | plan parse failed; `replans=0` |
| 5 | plan_exec | X | — | — | — | provider 429 |
| 6 | plan_exec | X | — | — | — | provider 429 |
| 7 | react | O | 3974 | 2 | 0 | |
| 8 | react | O | 5347 | 3 | 0 | |
| 9 | react | O | 3801 | 2 | 0 | |
| 10 | plan_exec | X | 73 | 1 | 0 | plan parse failed; `replans=0` |
| 11 | plan_exec | X | 794 | 1 | 0 | plan parse failed; `replans=0` |
| 12 | plan_exec | X | — | — | — | CP949 console encoding crash after producing `14:00` |

For the same-model comparison batch, ReAct succeeded 3/3 times, averaged
4,374 tokens and 2.3 iterations, and required zero interventions.
Plan-then-Execute succeeded 0/3 times; the two metered early failures averaged
433.5 tokens and one iteration, and all metered runs required zero
interventions. Because those Plan runs terminated before completing the task,
their lower token count is not a like-for-like efficiency result.

## 3. Interpretation — student reflection required

Write one paragraph here after reading `logs/react-07.txt` through
`logs/react-09.txt` and `logs/plan_exec-10.txt` through
`logs/plan_exec-12.txt`. In your own words, explain (a) why ReAct's iterative
context and termination rule produced 3/3 successful answers, (b) how the
Plan-then-Execute planner's strict bare-JSON boundary affected success in runs
10 and 11, (c) why run 12 is an execution-environment failure rather than
evidence that its answer was wrong, and (d) why zero interventions and the low
token counts of failed runs do not establish a winner on those metrics.
