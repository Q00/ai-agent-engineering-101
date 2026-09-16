# Task

The same task for both harnesses. `run_ab.py` reads the `task:` and
`expected:` lines below. Write the success criterion before you run
anything, and do not change it after you see the results.

task: In app.log, which hour (HH:00) has the most ERROR lines? Answer with the hour in HH:00 form.

## Success criterion

A run succeeds when the last line beginning with `Answer:` in the harness's
final response contains the hour with the most ERROR lines in `app.log`,
written as HH:00. If the response contains no `Answer:` line, the whole
response is checked instead. `app.log` is the reference input; the graded
runs use it unchanged.

This criterion was fixed before the first run. It is narrower than a
substring match over the whole response, which would score a run O whenever
it named the expected hour anywhere while concluding otherwise.

expected: 14:00
