# TOOLS.md — why `time_now_yday` is described the way it is

## The description

```
Get the current date and time in Asia/Seoul. Also returns the day-of-year
number (1-365), so date differences can be done as plain integer subtraction
with the calculator.
```

## Defending the wording

The third tool exists so the agent can answer "how many days until this
deadline", which is unanswerable without knowing what today is. Three
decisions in that sentence were deliberate.

**The timezone is named, not implied.** `datetime.now()` returns a naive
local time, so the same code would report a different day depending on the
machine that ran it — and half of this assignment is graded on whether
someone else can reproduce my result. Pinning `ZoneInfo("Asia/Seoul")` in the
implementation and saying so in the description means the answer only depends
on the wall clock, not on the grader's laptop.

**The return value is stated, and it is not just a date.** The agent's other
tool, `calculator`, parses arithmetic only; it cannot subtract two dates. If
`time_now_yday` returned `2026-09-02` alone, the model would be handed a
format its only arithmetic tool cannot consume. Returning the day-of-year
number alongside the human-readable timestamp closes that gap, and the
description says *why* the number is there ("so date differences can be done
as plain integer subtraction"). A description that lists a return value
without explaining what it unlocks gives the model no reason to use it.

**The tool takes no arguments, and the schema says so.** `"properties": {}`
with `"required": []`. This was learned the hard way — see run-01 below.

## What the runs actually showed

| run | change under test | result |
|---|---|---|
| 01 | schema still carried `path`, copy-pasted from `read_file` | `TypeError: time_now() got an unexpected keyword argument 'path'` |
| 02 | schema fixed; tool returned a bare timestamp | model passed `2026-09-08 - 2026-09-02` to `calculator` → `SyntaxError` |
| 03 | `time_now_yday` now returns day-of-year, description updated | same `SyntaxError` — the model ignored the number it had just been given |
| 04 | task text spelled out the conversion and forbade raw dates | success: 6 / 21 / 97 days |
| 05 | task text trimmed back; constraint moved into `calculator`'s description | `SyntaxError` again |
| 06 | one sentence of conversion guidance restored to the task text | success: 6 / 21 / 97 days |

Run-01 is the cleanest demonstration of the course's claim that the
description is the interface. The schema advertised a required `path`
argument that the function did not accept, so the model dutifully invented
one and the call crashed. The model was not wrong; it did exactly what the
interface told it to do.

Runs 03 and 05 complicate that claim, though. In 03 I improved the
description of `time_now_yday`; in 05 I put an explicit prohibition into
`calculator`'s description ("Only integers and floats are accepted; a date
such as 2026-09-08 is not a valid expression"). Neither changed the model's
behaviour. What changed it, in 04 and 06, was a sentence in the *task text*
telling it to convert dates to day-of-year first. So for this model
(`gpt-4o-mini`), tool descriptions were reliable at describing a call's
*shape* and unreliable at constraining *when and how* a tool should be
reached for; the task text won whenever the two competed.

## What I chose not to fix

When a tool raises, the exception propagates out of the loop and kills the
program, so the model never sees its own failure and cannot retry. The proper
fix is to catch the exception and return the error text as the tool result —
then the model reads `SyntaxError` and can switch to day-of-year on its own.
I took the faster route (one sentence in the task text) instead. The failing
logs are left in `logs/` unedited; runs 02, 03 and 05 are the evidence that
the descriptions alone were not enough.

## How to reproduce

```bash
pip install openai
export OPENAI_API_KEY=<your key>       # never committed
python first_agent.py "Read notes.txt and do what it says."
```

Model: `gpt-4o-mini` (override with `AGENT_MODEL`). Note that `notes.txt`
holds fixed 2026 dates, so the D-day numbers shrink as real time passes; the
logs were captured on 2026-09-02 KST.
