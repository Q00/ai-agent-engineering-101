# Week 02 — Harness A/B: ReAct vs Plan-then-Execute

## 1. Variant definition

The task was fixed before runs: identify the hour with the most `ERROR` lines
in the unchanged `app.log`; success means that the final answer contains
`14:00`. The provider was OpenRouter, the model was
`nvidia/nemotron-3.5-lightning:free`, and both variants imported the same
`read_file(path)` and `count_pattern(path, pattern)` tools from
`tools_shared.py`. The recorded runtime was Python 3.9.13 with openai 2.48.0.
`OPENAI_BASE_URL` was `https://openrouter.ai/api/v1`; the API key was supplied
only through `OPENAI_API_KEY` in the terminal. Each variant ran three times
via `python run_ab.py --runs 3`.

| Harness axis | ReAct | Plan-then-Execute | What was controlled |
| --- | --- | --- | --- |
| Context management | One `Chat` keeps the full task, tool, and observation history at every call. | A tools-off planner makes a JSON plan; a separate executor receives the task, serialized plan, step prompts, and observations. | Same task, model, and shared tool schemas. |
| Tool granularity | Uses `read_file` and `count_pattern`. | Uses the same two imported tools. | This axis was held constant; different outcomes cannot be attributed to the tool set. |
| Termination condition | Stops when the model makes no tool call, with `max_steps=8` as a global cap. | Runs a JSON plan step by step, with at most three tool rounds per step, then explicitly requests a final answer. | Neither variant was given a different answer criterion. |
| Error recovery | Tool exceptions are returned as observations; the next model response can adapt within the step cap. | A step over budget becomes `OFF_PLAN`; at most one replan is attempted and must parse as JSON. | Shared tool errors are returned through the same `Chat.run_tools` path. |
| Human intervention point | Has an approval hook for tools listed in `IRREVERSIBLE`, but that set is empty. | Has no approval prompt. | Both tools are read-only, so all measured interventions were zero. |

## 2. Measurements

The raw rows below are copied from `results.csv`. `tokens` is prompt plus
completion tokens accumulated by `Meter`; `iters` is the number of model calls.
`replans` is recorded only for Plan-then-Execute. Logs are kept unchanged in
`logs/`.

| Run | Harness | Success | Tokens | Iters | Interventions | Note |
| ---: | --- | --- | ---: | ---: | ---: | --- |
| 1 | react | O | 3,889 | 2 | 0 |  |
| 2 | react | X | 23,344 | 8 | 0 |  |
| 3 | react | O | 3,700 | 2 | 0 |  |
| 4 | plan_exec | O | 30,718 | 9 | 0 | replans=1 |
| 5 | plan_exec | O | 15,108 | 4 | 0 | replans=0 |
| 6 | plan_exec | O | 39,566 | 11 | 0 | replans=0 |

| Harness | Successful runs | Mean tokens | Mean iters | Mean interventions |
| --- | ---: | ---: | ---: | ---: |
| ReAct | 2 / 3 (66.7%) | 10,311 | 4.0 | 0 |
| Plan-then-Execute | 3 / 3 (100%) | 28,464 | 8.0 | 0 |

## 3. Interpretation

For this three-run sample, Plan-then-Execute achieved more judged successes
but used about 2.8 times the mean tokens and twice the mean model calls. The
termination axis is visible in `react-02.txt`: after reading the log, ReAct
made seven narrow or malformed hourly `count_pattern` calls, reached its
eight-step cap, and returned `MAX_STEPS reached: incomplete`. In contrast,
Plan-then-Execute retained a route to an explicitly requested final answer
after plan execution. This likely helped it avoid that particular failure
mode, but the logs do not show consistently faithful plan execution. In
`plan_exec-04.txt`, a step exceeded the three-tool-round budget, produced
`OFF_PLAN`, and then received an invalid JSON replan; the final answer still
contained `14:00`, so the stated substring criterion judged it successful.
`plan_exec-05.txt` planned only `["14:00"]`, and `plan_exec-06.txt` repeated a
generic ERROR count rather than visibly grouping every hour. Thus the observed
success rate validates final-answer correctness under the defined judge, not
the quality or reliability of the plans themselves. Both variants had zero
interventions because the shared tools were read-only. With one fixed task,
three runs per harness, no seed, and a free model whose outputs vary, these
results describe this sample rather than establishing a general winner or a
causal effect of one description of the harness.
