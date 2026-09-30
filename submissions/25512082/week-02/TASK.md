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

## Review-requested rerun protocol

- Keep the task, reference input, tools, provider, model, harness limits, and
  success criterion above unchanged from the original experiment.
- Run each harness three additional times with the provider default for OpenAI
  maximum output tokens.
- Preserve every new run in `results.csv` and `logs/`, including failures, and
  commit each run separately before starting the next one.
- Update `REPORT.md` only after all six reruns are complete.
