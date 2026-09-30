# Week 02 — Harness A/B Report

## 1. Variant definition

The model, task, and tools were held constant. The experiment used OpenRouter's OpenAI-compatible endpoint with `nvidia/nemotron-3.5-lightning:free`. Both harnesses received `read_file(path)`, which returns at most 4,000 characters, and `count_pattern(path, pattern)`, which counts lines matching a regular expression. The fixed task and success criterion are in `TASK.md`; a run succeeds only when its final answer contains `14:00`.

The harnesses differed mainly in context management, termination, error recovery, and the intervention point. ReAct kept the full Thought–Action–Observation history and let the model choose its next action until it answered or reached an eight-call cap. Plan-then-Execute first requested a bare JSON plan, executed each step with at most three tool rounds, and allowed one replan after `OFF_PLAN`. Thus, ReAct recovered locally from each observation, while Plan-then-Execute committed to an up-front structure and could fail before execution if its plan was not valid JSON. Tool granularity was intentionally identical, and both variants required no human approval because all tools were read-only, so interventions remained zero.

To reproduce the run, install a current `openai` package, set `OPENAI_BASE_URL=https://openrouter.ai/api/v1`, `OPENAI_API_KEY`, and `AGENT_MODEL=nvidia/nemotron-3.5-lightning:free`, then run `python3 run_ab.py --runs 3` from this directory. No API key is stored in the submission.

### Additional one-axis experiments

`run_plan_variants.py` preserves the original baseline and runs three Plan-then-Execute configurations against the same task, model, and shared tools:

1. `baseline_replan_1`: the original planner and one allowed replan.
2. `short_plan_max_3`: only the planner instruction changes, requiring a minimal plan of at most three steps; recovery remains one replan.
3. `no_replan_0`: only the recovery cap changes, from one allowed replan to zero; the original planner prompt remains unchanged.

The runner appends measurements to a separate `experiment_results.csv`, saves one immutable console capture per run under `logs/`, and explicitly records `OFF_PLAN` observations and actual replans. Run `python3 run_plan_variants.py --runs 3` after setting the same OpenRouter environment variables above. Unit tests in `test_experiment_variants.py` verify that each experimental wrapper changes only its intended harness axis.

## 2. Measurements

| run | harness | success | tokens | iters | interventions | note |
|---:|---|:---:|---:|---:|---:|---|
| 1 | react | X | — | — | — | `aiohttp.SocketTimeoutError` compatibility crash |
| 2 | react | X | — | — | — | `aiohttp.SocketTimeoutError` compatibility crash |
| 3 | react | X | — | — | — | `aiohttp.SocketTimeoutError` compatibility crash |
| 4 | plan_exec | X | — | — | — | `aiohttp.SocketTimeoutError` compatibility crash |
| 5 | plan_exec | X | — | — | — | `aiohttp.SocketTimeoutError` compatibility crash |
| 6 | plan_exec | X | — | — | — | `aiohttp.SocketTimeoutError` compatibility crash |
| 7 | react | O | 3,613 | 2 | 0 | — |
| 8 | react | O | 3,776 | 2 | 0 | — |
| 9 | react | O | 3,621 | 2 | 0 | — |
| 10 | plan_exec | O | 49,064 | 17 | 0 | replans=1 |
| 11 | plan_exec | X | 534 | 1 | 0 | replans=0; plan was not valid JSON |
| 12 | plan_exec | X | — | — | — | OpenRouter free-tier 429 rate limit |

The first six rows are preserved failed attempts from the incompatible local dependency state. After updating the environment, ReAct succeeded in 3/3 runs. Plan-then-Execute succeeded in 1/3 runs; its other runs failed during plan parsing and execution respectively.

## 3. Interpretation

On the valid post-fix runs, ReAct won reliability (3/3 versus 1/3) and efficiency: every successful ReAct run used two model calls and 3,613–3,776 tokens, whereas the successful Plan-then-Execute run used 17 calls and 49,064 tokens. The logs connect this difference to the harness axes. ReAct's full-history loop needed one file read and then terminated when the model answered. The up-front plan in run 10 expanded the same task into six steps; the three-tool-round cap produced `OFF_PLAN`, the single allowed replan added more work, and repeated step execution drove up calls and tokens. Run 11 shows the brittleness of the planning boundary because a non-JSON planner response ended the run after one call. Run 12 is partly an external rate-limit failure, but the plan harness's larger call budget exposed it sooner. Neither harness moved the intervention metric because the shared tools were read-only and required no approval. The dependency crashes in runs 1–6 affected both variants equally, so they are reproducibility evidence rather than evidence that one harness was better.

## 4. Additional experiment status

The additional variants and their measurement runner are implemented, but no new model measurements are claimed in this report yet. At the time of the implementation run, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `AGENT_MODEL` were not present in the shell. An attempted batch was stopped before any external request was sent, so there is no honest token, iteration, success, `OFF_PLAN`, or replan dataset to report for these variants. Once `OPENAI_API_KEY` is available, the command above will produce the required three runs per configuration without changing or deleting the original `results.csv` or logs. The original baseline measurements in Section 2 remain intact.
