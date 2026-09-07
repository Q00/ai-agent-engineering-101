# TOOLS.md

`clock` is my third tool: a zero-argument function that returns the current
date and time as a string. I gave it the simplest description I could —
`"Return the current date and time."`, with an empty parameter schema — on
purpose, as a small experiment in how little a description can say and still
work.

It didn't always work. Asking plainly, `"지금 몇 시야?"`, the model never called
`clock` — it just made up a date, and got it wrong (`logs/run-01.txt`). The
description says *what* the tool does but never *when* to reach for it, so the
model filled that gap by guessing instead of asking. Asked more directly,
`"clock 도구를 써서 지금 시각을 알려줘"`, it called the tool correctly
(`logs/run-02.txt`). And given a task that needed all three tools at once, it
chained `read_file` → `clock` → `calculator` on its own without any hand-holding
(`logs/run-03.txt`).

That contrast is the actual finding: the description alone didn't guarantee the
right tool got picked — how explicit the prompt was mattered just as much. A
longer description ("use this whenever the user asks about the time, date, or
'now'") would probably have fixed run-01, at the cost of being longer and more
rigid. I left it short on purpose, because the failure was more informative
than a quiet success would have been.
