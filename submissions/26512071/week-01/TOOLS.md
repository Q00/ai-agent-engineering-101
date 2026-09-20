# TOOLS.md - why the `clock` description is worded the way it is

The third tool is `clock`. Its first description was one line - "Return the
current date and time." - and the part I expected to fail did not: asked how
many days had passed since the memo date, the model called `clock()`
immediately, so the word "today" in the goal was enough of a trigger on its
own. What broke was composition. `clock` had already returned
`day_number: 739866`, and the model ignored it and called
`calculator("7 - 1")` instead, subtracting the day-of-month numbers. The
answer, 6, was right - but only because the memo date and the run date happened
to fall in the same month. Move the memo to August and the same reasoning
returns a wrong number with no visible error. It also never called
`clock(date_str=...)`, because a description that says only "return the current
date and time" gives no reason to believe a second, non-clock use exists. So
the description I settled on spends its words on the two things the model got
wrong rather than on the one it got right: it states that `day_number` is an
absolute integer day count and that an interval is obtained by calling `clock`
twice, once per date, and subtracting the two `day_number`s **with the
calculator**; it explicitly forbids the shortcut the model actually took
("never subtract the day-of-month numbers instead; that is only correct when
both dates fall in the same month"); and it closes with "this tool does no
arithmetic itself" so the boundary between `clock` and `calculator` is stated
rather than inferred. Both arguments also got their own `description` fields
with concrete formats (`'Asia/Seoul'`, `'YYYY-MM-DD'`), since before that they
carried only a JSON type and the model had no way to know what `date_str` was
for. The tool returns JSON rather than a sentence for the same reason - field
names carry the meaning, so nothing has to be parsed out of prose.

## What I discarded

The obvious version of this tool is `days_since(date)`: one call, no
arithmetic, no composition. I rejected it because it would have absorbed the
calculator's job, and what this week is asking me to observe is how the model
*routes* work between tools - a tool that finishes the whole task end to end
makes that unobservable. But a clock that can only report *now* cannot supply
the second operand for the subtraction, which would have pushed the model back
to guessing. The optional `date_str` argument is the compromise: the tool is
still just a date-to-fields lookup, it simply does not insist on the current
date, and that makes `day_number` usable as a general date-to-integer
converter the calculator can consume.

## Observation: what the third tool changed

Two runs, same code, one description rewritten:

| | `clock` description | tool calls | how the interval was computed |
|---|---|---|---|
| `run-01` | one line | `read_file` -> `clock()` -> `calculator` x2 | `7 - 1` (day-of-month; correct by coincidence) |
| `run-02` | explicit | `read_file` -> `clock()` -> `calculator` -> `clock(date_str)` -> `calculator` | `739866 - 739860` (day_number) |

With two tools, selection was effectively deterministic - a file went to
`read_file`, a number went to `calculator`. The third tool introduced a
question neither of the first two could answer alone, and that is where the
model started taking shortcuts: it will use the cheapest path that produces
*a* number, and a coincidentally-correct answer looks identical to a correct
one in the output. The description is what closed that gap. Nothing in the
executable code changed between the two runs.
