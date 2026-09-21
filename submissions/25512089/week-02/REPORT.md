# Week 02 A/B Experiment Report

## 1. Variant Definition

This experiment compares two agent harnesses: ReAct and Plan-then-Execute. Both harnesses use the same task, the same `app.log` input file, the same shared tools from `tools_shared.py`, and the same OpenRouter model (`nvidia/nemotron-3.5-lightning:free`). The main variable changed between the two conditions is the harness strategy.

ReAct repeatedly decides what to do after each observation, while Plan-then-Execute first creates a plan and then executes the planned steps. The experiment therefore changes the planning and execution structure while keeping the model, task, tools, and success criterion constant.

## 2. Measurements

The success criterion was to return `14:00`, the hour with the most ERROR lines in `app.log`.

| Run | Harness | Success | Tokens | Iterations | Interventions |
|---|---|---|---:|---:|---:|
| 7 | ReAct | X | 471 | 1 | 0 |
| 8 | ReAct | O | 3476 | 2 | 0 |
| 10 | ReAct | X | 478 | 1 | 0 |
| 11 | ReAct | X | 2139 | 2 | 0 |
| 9 | Plan-then-Execute | O | 32796 | 9 | 0 |
| 12 | Plan-then-Execute | O | 35679 | 14 | 0 |
| 13 | Plan-then-Execute | O | 36623 | 10 | 0 |

For the OpenRouter runs, ReAct succeeded in 1 of 4 runs (25%), while Plan-then-Execute succeeded in all 3 runs (100%). ReAct used about 1,641 tokens and 1.5 iterations on average, while Plan-then-Execute used about 35,033 tokens and 11 iterations on average.

## 3. Interpretation

Plan-then-Execute was more reliable for this task because all three runs returned the expected answer, `14:00`. Its explicit planning structure encouraged the agent to inspect the log, count or compare ERROR occurrences, and continue until it produced a final answer. However, this reliability came at a much higher cost: it used substantially more tokens and iterations than ReAct. ReAct was much cheaper and shorter, but several runs stopped too early or produced an incomplete final response. Therefore, for this task, Plan-then-Execute performed better on success rate, while ReAct performed better on token and iteration efficiency.

## Reproducibility

Provider: OpenRouter  
Model: `nvidia/nemotron-3.5-lightning:free`  
Task: Find the hour with the most ERROR lines in `app.log`  
Command:

`python run_ab.py --runs 3`

The API key is provided through an environment variable and is not committed to the repository.

## Experiment Note

The first six recorded runs failed because the Anthropic API account did not have sufficient credits. These failed attempts were retained in `results.csv` and the raw logs as process evidence. The A/B comparison above uses the later OpenRouter runs so that the model and provider remain constant across both harnesses.