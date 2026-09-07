# TOOLS.md

I added `clock`, a zero-argument tool that returns the current date and time as a
string. I described it simply, as `"Return the current date and time."`, with no
argument schema (`properties: {}`). I picked this short wording deliberately, to
see how far a minimal description could get the model to still pick the tool.

The description is the interface: with only the short wording above, `python
first_agent.py "지금 몇 시야?"` never called `clock` at all — the model answered
directly with a fabricated, wrong date (`logs/run-01.txt`). Nothing in the
description told the model *when* to reach for it, so it fell back to guessing.
Once I asked more explicitly ("clock 도구를 써서 지금 시각을 알려줘", `logs/run-02.txt`),
the model called it correctly. And in a mixed task combining all three tools
(`logs/run-03.txt`), it chained `read_file` → `clock` → `calculator` on its own
and returned the right values for all three.

So the description alone was not enough to guarantee correct tool selection —
it depended on how explicit the user's prompt was. A stronger description (e.g.
"Use this whenever the user asks about the current time, date, or 'now'.") would
likely have made run-01 succeed too, at the cost of being longer. I kept the
short version so this gap would be visible in the logs, since observing where
description-driven tool selection breaks down was the point of the exercise.
