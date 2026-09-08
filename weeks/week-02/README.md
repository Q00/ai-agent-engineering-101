# Week 02 — Harness A/B: ReAct vs Plan-then-Execute

**Due: before the start of the week-03 class.** The deadline is judged by the PR open timestamp.

## Background

The lecture split what a harness decides into five axes: context management, tool granularity, termination condition, error recovery, and the human intervention point. The lab compared two harnesses that set those axes differently, on one task, with one model and one tool set. The starter code in `starter/` is that lab.

## Assignment

Hold the model, the task, and the tools constant. Vary only the harness. Run each harness at least three times on the same task, record success, tokens, iterations, and interventions for every run, and explain which axis moved which metric.

The starter task is "which hour in `app.log` has the most ERROR lines". `app.log` is the reference input; the graded runs use it unchanged. You may add a second task of your own on top, but the six starter runs must be in `results.csv`.

## What to submit

Everything goes in `submissions/<student-id>/week-02/`:

| File | Contents |
|---|---|
| `tools_shared.py` | Tools, model call, and `Meter`. Both harnesses import from here. |
| `harness_react.py` | The ReAct harness. |
| `harness_plan_execute.py` | The Plan-then-Execute harness. |
| `TASK.md` | The task and the success criterion, with the `task:` and `expected:` lines `run_ab.py` reads. Commit it before the runs. |
| `results.csv` | One line per run. Header exactly `run,harness,success,tokens,iters,interventions,note`. At least three runs per harness. Failed runs stay. |
| `logs/` | One console capture per run. `run_ab.py` writes them. |
| `REPORT.md` | One page in three parts: (1) variant definition, which of the five axes the two harnesses set differently and how; (2) measurements, the table from `results.csv`; (3) one paragraph of interpretation, which harness won on which metric and why. |

## Grading

- **Half: reproducibility.** Someone else must be able to get the same trend from your code and settings alone. State everything except the API key: provider, model name, tool schemas, how to run.
- **Half: interpretation.** Not a winner declaration. Which axis moved which metric, with evidence from the logs. A run that failed because Plan-then-Execute could not parse its own plan is a finding, not an embarrassment.

## Running the starter

```bash
cp -r weeks/week-02/starter/. submissions/<student-id>/week-02/
cd submissions/<student-id>/week-02
export ANTHROPIC_API_KEY=...          # or the OpenAI-compatible variables below
python run_ab.py --runs 3             # writes results.csv and logs/
```

`run_ab.py` reads `TASK.md`, runs each harness three times, judges every run against the `expected:` line, and appends one row per run. Run it again and rows are appended, not replaced.

## Checks

CI verifies structure only: the three `.py` files parse and both harnesses import `tools_shared`, `TASK.md` has its two lines, `results.csv` has the exact header and at least three rows per harness, `logs/` has one file per run, `REPORT.md` exists, and your PR touches only your own directory. Run it locally first:

```bash
python scripts/check_week02.py submissions/<student-id>/week-02
```

## Using an OpenRouter free model

`tools_shared.py` uses the Anthropic SDK when `ANTHROPIC_API_KEY` is set and the OpenAI-compatible API otherwise, so OpenRouter is three environment variables:

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<your openrouter key>
export AGENT_MODEL=nvidia/nemotron-3.5-lightning:free   # tested with the starter; any tool-calling model works
python run_ab.py --runs 3
```

Free-tier models vary in tool-calling quality, and the Plan-then-Execute harness also needs the model to return a bare JSON list. If a model keeps failing at either, that goes in `results.csv` as X rows and in `REPORT.md` as a finding.
