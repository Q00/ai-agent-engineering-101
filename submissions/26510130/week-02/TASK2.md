# Task 2 — two-hop, the second hop depends on the first

Same file, same tools, same model, same harnesses as `TASK.md`. The only thing
that changes is that the answer to step 2 cannot be written until step 1 has
been observed.

task: In app.log, find the hour (HH:00) with the most ERROR lines, then report which ERROR message text occurs most often inside that hour.

## Why this task

`TASK.md` is single-hop: one `count_pattern` sweep answers it, and a plan
written before seeing the file is as good as one written after. That hides the
axis the two harnesses actually differ on — Plan-then-Execute's planner runs
with `tools=False` and never sees an observation, so a plan it writes blind is
only penalised when a later step depends on an earlier result.

Here it does. The hour is not known until the file has been read, so the
planner has to write step 2 without knowing what step 2 is about.

## The trap, and why it is deliberate

Across the whole file the most frequent ERROR message is
`NullReference in QuizService.score` (7 occurrences). Inside 14:00 it is
`upstream timeout after 5000ms` (3 of the 6). An agent that skips the first hop
and counts messages over the whole file gets a confident wrong answer, so
success here measures the dependency and not just the arithmetic.

## Success criterion

A run succeeds when the final answer contains the most frequent ERROR message
text inside the hour with the most ERROR lines. `app.log` is the reference
input, used unchanged. Written before any run of this task; not to be loosened
afterwards.

expected: upstream timeout
