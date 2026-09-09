# Week 03 — Contract Net with LLM contractors

**Due: before the start of the week-04 class.** The deadline is judged by the PR open timestamp.

## Background

Smith (1980) allocated tasks in a distributed problem solver by negotiation: a manager announces a task, contractors bid, the manager awards. In 1980 a contractor computed its bid from a fixed rule. This week the contractor is an LLM that reads the announcement and judges whether it can do the job. The lab reproduces the protocol with one manager and three contractors and measures what the judged bid does to allocation.

## No starter this week

There is no `starter/` directory. You design the code: how the manager announces, how a contractor bids, how the award is chosen, how a run is logged. What is fixed is the data contract below, because CI and the grader read it. For the model call, copy the `Chat` class or the `Meter` from `weeks/week-02/starter/tools_shared.py`; the contract net needs no tools, only a system prompt and one user message per bid.

## Assignment

Build a contract net: one manager, three contractors, each contractor an LLM call with its own system prompt. Give every task a gold contractor (the one whose skill matches). Run three conditions, at least three runs each, on the same task set, the same prompts, the same model, the same temperature:

| Condition | What changes |
|---|---|
| `baseline` | Three contractors with three different skills. |
| `homogeneous` | Three contractors with the same generalist skill. Nothing else changes. |
| `overconfident` | Baseline, but one contractor's system prompt tells it to bid on everything with high confidence. Nothing else changes. |

Measure, per run: how many tasks went to their gold contractor, how many messages were exchanged (count one announcement per contractor, one per bid, one per award), how many tasks got no bid, and how many were awarded to the wrong contractor.

## What to submit

Everything goes in `submissions/<student-id>/week-03/`:

| File | Contents |
|---|---|
| `*.py` | Your contract net and the runner. Any layout, any file names. |
| `tasks.json` | A JSON list of at least 5 tasks. Each entry has `id`, `desc`, and `gold` (the contractor name that should win). At least two different `gold` values. Commit it before the runs. |
| `results.csv` | One line per run. Header exactly `run,condition,tasks,correct,messages,unassigned,misawards,note`. `condition` is one of `baseline`, `homogeneous`, `overconfident`. Counts are integers. At least three runs per condition. Crashed runs stay, with blank counts and the error in `note`. |
| `logs/` | One console capture per run: every announcement, every bid with its confidence and reason, every award. At least 9 files. |
| `REPORT.md` | Four parts: (1) setup, provider, model, temperature, prompts, how to run; (2) the results table from `results.csv`; (3) a comparison table, Smith 1980's distributed sensing setup against your reproduction, row by row (who the nodes are, how a bid is produced, what guarantees bid honesty, what allocation quality means, what negotiation costs, which failure modes appear); (4) one paragraph of interpretation, which condition moved which metric and why, with lines from the logs as evidence. |

## Grading

- **Half: reproducibility.** Someone else must be able to get the same trend from your code and settings alone. State everything except the API key.
- **Half: interpretation.** Not a winner declaration. Where did the judged bid help, where did it break, and what in Smith's protocol had no defense against it. A run where the overconfident contractor sweeps the awards is a finding. A run where the free model returns unparseable JSON is also a finding: count it and say so.

## Checks

CI verifies structure only: at least one `.py` parses, `tasks.json` has the required fields, `results.csv` has the exact header and three runs per condition, `logs/` has one file per run, `REPORT.md` exists with a table, and your PR touches only your own directory. Run it locally first:

```bash
python scripts/check_week03.py submissions/<student-id>/week-03
```

## Using an OpenRouter free model

Same three variables as week 02:

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<your openrouter key>
export AGENT_MODEL=nvidia/nemotron-3.5-lightning:free   # tested; any chat model works
```

This model sometimes answers with its reasoning instead of the JSON bid. Treat an unparseable reply as a contractor that did not bid, count it, and report the count.
