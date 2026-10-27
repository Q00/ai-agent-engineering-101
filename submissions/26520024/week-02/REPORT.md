# Week 02: ReAct vs Plan-then-Execute

## 1. Variant definition

Both variants use authenticated Codex CLI 0.153.0 (ChatGPT login), gpt-6-astra,
low reasoning, the original app.log, and identical read_file/count_pattern
schemas and implementations. Python executes tools; Codex internal actions
are disabled and checked. The shared transport preserves the planner's free
text, so invalid JSON plans could still fail. See [README.md](README.md) for
commands/settings and [PROCESS.md](PROCESS.md) for assistant involvement.
TASK.md was committed in ce4e80e before any live run; experiment code was
frozen at eb69042. All six runs used existing conda base Python 3.8.19.

| Axis | ReAct | Plan-then-Execute |
|---|---|---|
| Context | One full conversation | Separate planner and executor; executor retains plan, steps, and observations |
| Tool granularity | read_file and count_pattern | Same tools, schemas, and app.log-only access |
| Termination | No tool calls, or 8-model-call cap | Finish planned steps, then final synthesis; per-step tool-round guard |
| Error recovery | Tool exceptions returned as observations | Same tool observations; OFF_PLAN permits at most one replan |
| Human intervention | Empty approval set for read-only tools | No approval step for the same read-only tools |

## 2. Measurements

Exact rows from [results.csv](results.csv); O means the unchanged substring
judge found 14:00 in the final answer. Tokens are actual CLI input + output
usage, including cached input once and CLI overhead, not estimated billing.
Iterations count all model calls, including planning and final synthesis.

| run | harness | success | tokens | iters | interventions | note |
|---|---|---|---:|---:|---:|---|
| 1 | react | O | 19368 | 2 | 0 | |
| 2 | react | O | 19374 | 2 | 0 | |
| 3 | react | O | 19376 | 2 | 0 | |
| 4 | plan_exec | O | 60011 | 6 | 0 | replans=0 |
| 5 | plan_exec | O | 60037 | 6 | 0 | replans=0 |
| 6 | plan_exec | O | 61572 | 6 | 0 | replans=0 |

ReAct: 3/3 success, mean 19,372.67 tokens and 2 calls. Plan-execute: 3/3
success, mean 60,540 tokens and 6 calls. Both: zero interventions. Every run
called read_file once; none called count_pattern. All 24 completed model
turns were checked against their raw usage events in
[logs/live-validation-01.log](logs/live-validation-01.log).

## 3. Interpretation

ReAct used fewer tokens and calls on this input, with tied observed success
and interventions. [react-01](logs/react-01.txt) reads the file and immediately
answers on call 2. [plan_exec-04](logs/plan_exec-04.txt) makes a plan (call 1),
requests the file (2), confirms that step (3), counts hours (4), answers the
last plan step (5), and repeats the answer in final synthesis (6). Thus the
termination policy and explicit plan/step context explain the observed extra
calls and repeated input tokens; they were not independently ablated, so this
is a mechanism-based interpretation, not an isolated causal estimate. In
[plan_exec-06](logs/plan_exec-06.txt), step 1 also reproduces all ERROR lines;
that longer reply is carried into later calls, consistent with its higher
61,572-token total despite the same 6 calls. Tool granularity was held fixed,
and neither errors/replanning nor human intervention occurred, so those axes
cannot explain a measured difference here. All plans parsed; no failures or
runs were discarded. All agents counted from read_file's full input instead
of using count_pattern; an independent deterministic check confirms 14:00 has
6 ERROR lines (the next highest is 12:00 with 3). This small log therefore does
not test robust regex-tool use or large-file behavior. Three fixed-order runs
per variant, a permissive substring judge, shared CLI overhead/cache effects,
and one task do not establish statistical reliability or a universal winner.
