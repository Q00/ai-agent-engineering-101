# Week 02 — Harness A/B Report

## 1. Variant definition

This experiment holds the task, model, and tool set constant and changes only
the harness. Both variants solve the task in `TASK.md` with the shared
`read_file(path)` and `count_pattern(path, pattern)` tools from
`tools_shared.py`.

The ReAct harness chooses the next action after every Observation. It retains
the full conversation, terminates when the model answers or after eight
steps, and returns tool errors to the model as Observations. The
Plan-then-Execute harness first generates the whole plan as a JSON list and
then executes its steps in order. It permits at most one replan and at most
three tool-call rounds per step. Therefore, the principal difference is the
termination/flexibility policy: ReAct can revise its next action at every
iteration, whereas Plan-then-Execute commits to an initial plan with one
explicitly bounded replan. Context organization also differs because ReAct
accumulates one continuous action/observation trajectory while
Plan-then-Execute separates planning from step execution. Tool granularity,
error-as-Observation recovery, and the human-intervention policy remain the
same. Since both tools are read-only, interventions are expected to remain
zero.

## 2. Measurements

Provider and model: **to be recorded before the graded runs**

Run command:

```bash
python run_ab.py --runs 3
```

The measurement table will be copied from `results.csv` after all six runs.

## 3. Interpretation

To be written after reading `results.csv` and the six raw files in `logs/`.
The interpretation will identify which harness changed success, token use,
and iteration count, then connect each difference to evidence in the traces.
