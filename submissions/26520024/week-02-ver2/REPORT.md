# Week 02 ver2: Efficient Harness A/B

## 1. Variant Definition

Both variants use gpt-6-astra (low), Codex CLI 0.153.0 with ChatGPT login,
base Python 3.8.19, the unchanged app.log/task, and identical read_file and
count_pattern tools. Python executes tools; Codex internal actions are disabled.
TASK.md was committed before runs; code was frozen at 127718f.
[Setup and schemas](README.md); [process and assistance disclosure](PROCESS.md).

| Axis | ReAct v2 | Plan-then-Execute v2 |
|---|---|---|
| Context | Task + last 4 observation batches; no narration history | Short plan + current step + all tool observations; no narration history |
| Tool granularity | Original two tools; independent calls may be batched | Same schemas and implementations |
| Termination | Evidence-backed Answer; 8-call cap | Early Answer even with remaining steps; no extra synthesis; same 8-call total cap |
| Error recovery | Adapt using error observations | One shared plan-repair/replan allowance; strict 3-tool-batch step cap |
| Human intervention | Read-only tools; empty approval set | Read-only tools; no approval step |

## 2. Measurements

All rows from [results.csv](results.csv), in actual R/P, P/R, R/P order.
O = unchanged expected-substring judge passes. Tokens = actual CLI input +
output, including cached input once and CLI overhead; iters = model calls.
Failed runs would remain; no failures or discarded runs occurred.

| run | harness | success | tokens | iters | interventions | note |
|---|---|---|---:|---:|---:|---|
| 1 | react | O | 19456 | 2 | 0 | |
| 2 | plan_exec | O | 28656 | 3 | 0 | replans=0 |
| 3 | plan_exec | O | 28674 | 3 | 0 | replans=0 |
| 4 | react | O | 19456 | 2 | 0 | |
| 5 | react | O | 19458 | 2 | 0 | |
| 6 | plan_exec | O | 28653 | 3 | 0 | replans=0 |

Means: ReAct **19,456.67 tokens / 2 calls**; plan-execute **28,661 / 3**.
Both succeeded **3/3**, with **0 interventions**. All 15 model calls and
their input observations passed [raw-event validation](logs/live-validation-01.log).

## 3. Interpretation

ReAct won on tokens and calls, tying success and interventions. In
[react-01](logs/react-01.txt), it reads then answers; in
[plan_exec-02](logs/plan_exec-02.txt), planning adds one call before the same
read/answer sequence. Early termination accepts that answer during step 1,
removing redundant confirmations and synthesis: versus historical week-02,
plan-execute fell from 6 to 3 calls and 60,540 to 28,661 mean tokens (52.66%
less). ReAct remained at 2 calls but tokens rose 0.43%; added instructions
and observation packaging did not save tokens on this short history.
Context and termination changes were bundled, not independently ablated;
the old runs were on a different date. Each new run read once and never used
count_pattern, even though the plans proposed it: early completion favors
task success over strict plan adherence. No memory eviction, live errors,
replans, or human approvals occurred, so their benefits are unmeasured.
Three runs per variant, CLI/cache overhead, and a permissive substring judge
limit generalization beyond this small task.
