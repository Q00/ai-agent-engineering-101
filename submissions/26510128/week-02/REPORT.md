# Week 02 A/B Experiment Report

## 1. Variant Definition

This experiment compares two harnesses while keeping the model, task, tools, and input file fixed.

### Common settings

- Provider: OpenRouter (OpenAI-compatible API)
- Model: `nvidia/nemotron-3.5-lightning:free`
- Task: find the hour (`HH:00`) with the most `ERROR` lines in `app.log`
- Expected answer: `14:00`
- Input: the provided `app.log`, unchanged
- Tools: `read_file(path)` and `count_pattern(path, pattern)`
- Human intervention policy: both tools are read-only, so no approval step is required and interventions remain 0

The experiment was run with:

```powershell
$env:OPENAI_BASE_URL="https://openrouter.ai/api/v1"
$env:OPENAI_API_KEY="<YOUR_OPENROUTER_API_KEY>"
$env:AGENT_MODEL="nvidia/nemotron-3.5-lightning:free"
python run_ab.py --runs 3
```

### ReAct harness

The ReAct harness decides what to do after each observation. It keeps the full interaction history, can adapt its next action after a tool result, and terminates when the model returns a final answer or the iteration cap is reached. Tool errors are returned as observations, so recovery can happen on the next reasoning step.

### Plan-then-Execute harness

The Plan-then-Execute harness first asks the model to generate the entire plan as a JSON list, then executes each step in order. If a step returns `OFF_PLAN`, the harness allows at most one replan. This makes the initial planning format and the limited recovery policy important failure points.

Thus, the model, task, tools, and input are held constant while the harness changes mainly the decision timing, termination behavior, and error-recovery flexibility.

## 2. Measurements

| Run | Harness | Success | Tokens | Iterations | Interventions | Note |
|---:|---|:---:|---:|---:|---:|---|
| 1 | react | O | 3889 | 2 | 0 | |
| 2 | react | O | 3740 | 2 | 0 | |
| 3 | react | O | 4279 | 2 | 0 | |
| 4 | plan_exec | X | 4315 | 3 | 0 | replans=0 |
| 5 | plan_exec | X | 210 | 1 | 0 | replans=0 |
| 6 | plan_exec | O | 67799 | 16 | 0 | replans=1 |

Summary:

- ReAct success rate: **3/3 (100%)**
- Plan-then-Execute success rate: **1/3 (33.3%)**
- ReAct average tokens: **3969.3**
- Plan-then-Execute average tokens: **24108.0**
- ReAct average iterations: **2.0**
- Plan-then-Execute average iterations: **6.7**
- Human interventions: **0 for every run**

## 3. Interpretation

ReAct was more reliable on this task: all three ReAct runs succeeded, and each finished in two model calls with roughly 3.7k-4.3k tokens. In contrast, Plan-then-Execute succeeded only once. The first failed Plan-then-Execute run produced `['14:00']` as the plan rather than a useful sequence of actions, so execution returned no valid final answer. The second failed before execution because the planner returned `Here`, which was not a valid JSON list. These failures show that front-loading the full plan adds a brittle formatting dependency before tool execution even begins. The third Plan-then-Execute run created a sensible six-step plan and eventually succeeded, but it hit the tool-call budget during execution, triggered the single allowed replan, and required 16 model calls and 67,799 tokens. Therefore, for this small log-analysis task, ReAct's observation-by-observation decision loop produced higher success and lower measured cost. Plan-then-Execute's explicit up-front plan did not reduce work in these runs; instead, plan-format failures and limited replanning increased failure risk and, in the successful run, substantially increased iterations and token usage. The intervention metric did not differ because both harnesses used the same read-only tools and required no human approval.
