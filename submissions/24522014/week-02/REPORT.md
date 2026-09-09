# Week 02 — Harness A/B Report

## 1. Variant definition and reproducibility

The task, success criterion, model, and tools were held constant inside each
A/B batch. The provider was OpenRouter through its OpenAI-compatible endpoint
(`https://openrouter.ai/api/v1`) with OpenAI Python SDK 3.8.0. Runs 1–6 were an
initial pilot with `poolside/laguna-s-2.1:free`; provider rate limits and a 400
response made that batch unusable for a clean comparison, but the failed runs
remain in the data. Runs 7–12 were the first batch with
`nvidia/nemotron-3.5-lightning:free`. Runs 13–18 repeated both harnesses with
that same model and `PYTHONUTF8=1`; this is the primary comparison batch because
the setting prevents the Windows CP949 console from terminating a run while
printing model output. The shared tools were `read_file(path)` (return the
UTF-8 file contents) and `count_pattern(path, pattern)` (count regex-matching
lines). No API key is stored in the repository.

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
$env:PYTHONUTF8 = "1"
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
| 13 | react | X | 3455 | 2 | 0 | final response was truncated before `14:00` |
| 14 | react | O | 6578 | 3 | 0 | |
| 15 | react | O | 3720 | 2 | 0 | |
| 16 | plan_exec | O | 38104 | 10 | 0 | `replans=0` |
| 17 | plan_exec | O | 39341 | 10 | 0 | `replans=0` |
| 18 | plan_exec | O | 46918 | 15 | 0 | `replans=1` |

In the primary comparison batch (runs 13–18), ReAct succeeded 2/3 times,
averaged 4,584.3 tokens and 2.3 iterations, and required zero interventions.
Plan-then-Execute succeeded 3/3 times, averaged 41,454.3 tokens and 11.7
iterations, and also required zero interventions. Plan-then-Execute therefore
used about 9.0 times the tokens and 5.0 times the iterations in this batch.

## 3. Interpretation — student reflection required

Write one paragraph here after reading `logs/react-13.txt` through
`logs/react-15.txt` and `logs/plan_exec-16.txt` through
`logs/plan_exec-18.txt`. In your own words, explain (a) the success-rate result
of 2/3 versus 3/3, (b) how Plan-then-Execute's up-front plan, per-step execution,
and one allowed replan affected tokens and iterations, (c) why run 13 failed
even though the model had found the right hour, and (d) why interventions did
not distinguish these read-only harnesses.
