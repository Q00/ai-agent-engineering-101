# Task

The same task for both harnesses. `run_ab.py` reads the `task:` and
`expected:` lines below. Write the success criterion before you run
anything, and do not change it after you see the results.

task: In app.log, find the ERROR message text (the part of the line after the word ERROR) that appears on the most lines. Reply with one JSON object on a single line, using exactly this key order and spacing - double quotes, one space after each colon, one space after the comma, no indentation: {"msg": "some error text", "count": 3}

## Success criterion

A run succeeds when the final answer contains the exact string on the
`expected:` line. The object in the `task:` line is a format sample with a
placeholder message and a placeholder count, not a hint at the answer. The
judge is a plain substring match, so the format is part of the criterion: a
different key order, missing spaces after the colons, or a pretty-printed
object is judged X. `app.log` is the reference input; the graded runs use it
unchanged.

expected: {"msg": "NullReference in QuizService.score", "count": 7}
