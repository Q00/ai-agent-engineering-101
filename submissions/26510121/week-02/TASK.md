# Task

The same task for both harnesses. `run_ab.py` reads the `task:` and
`expected:` lines below. Write the success criterion before you run
anything, and do not change it after you see the results.

task: In app.log, which hour (HH:00) has the most ERROR lines? Answer with the hour in HH:00 form.

## Success criterion

A run succeeds when the final answer contains the hour with the most ERROR
lines in `app.log`, written as HH:00. `app.log` is the reference input; the
graded runs use it unchanged.

expected: 14:00

## Experiment controls

Locked before any model run on 2026-09-12. Both harnesses will use this
unchanged task, the unchanged `app.log`, the same shared tools, and the same
OpenRouter model configuration. Only the harness varies. Failed runs remain
in `results.csv` and the success criterion above will not be loosened after
observing results.
