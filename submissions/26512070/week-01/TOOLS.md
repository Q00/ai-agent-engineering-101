# TOOLS.md — why `seconds_recorder` is described the way it is

## The gap I was filling

The two starter tools cover the back half of a pipeline: `read_file` turns a file
into text, `calculator` turns an expression into a number. Nothing in the agent
*produces* text with numbers in it. So I went looking for a tool that emits a
small amount of number-bearing text, and among the README's examples the clock
was the natural fit. `seconds_recorder` writes two numbers — the second it runs
at, and the second five seconds later — into a text file.

## Why seconds specifically: a deliberate collision with `calculator`

Reading the starter closely, both tools are deliberately thin. `calculator` does
not use `eval()`; it parses the expression with `ast` and only executes operators
present in a six-entry whitelist (`+ - * / **` and unary minus). `ast.Mod` is not
in it, so `10 % 3` raises `KeyError` — and because the loop calls
`TOOLS_IMPL[call.function.name](**args)` with no `try`/`except`, one bad
expression kills the entire process rather than coming back as an observation the
model can react to.

Seconds are the one everyday quantity that is inherently modular: 57 + 5 has to
be 2, not 62. I chose this domain on purpose. I wanted to see what an agent does
when the task's natural arithmetic is exactly the operation its calculator cannot
perform.

## The claim I made, and what testing did to it

My first description ended with "*the second value wraps past 59 (57 becomes
2)*". I removed it, and argued that removing it was what kept the model away from
`%`. I could show that `%` is fatal —

```
calculator("(19 + 5) % 60")  ->  KeyError: <class 'ast.Mod'>   # process dies
calculator("19 + 5")         ->  24
```

— but I never showed that the *description* was what produced it. So I ran the
comparison. Three wordings, same implementation, same model
(`minimax/minimax-m3:free`), differing only in the sentence between the output
format and the `at_second` sentence:

- **A** — "Both are whole numbers in 0-59; the second value wraps past 59 (57
  becomes 2)."
- **B** — "Both are final values: whole numbers from 0 to 59, already adjusted to
  stay in that range. Use them as they are; **no further arithmetic is needed to
  interpret them.**" *(the version I shipped)*
- **C** — "Both are whole numbers from 0 to 59: the second value is already
  adjusted to stay in that range, so five seconds after 57 is 2, not 62. Use the
  two numbers exactly as they are." *(A's concrete example, B's "as they are",
  but no "no further arithmetic" clause)*

| run | wording | task | expression the model sent | outcome |
|---|---|---|---|---|
| 07 | B | sum 41 | `18 + (18 + 5)` | ok |
| 09 | A | sum 41 | `18 + 23` | ok |
| 14 | B | sum 41 | `18 + (18+5)`, `19 + 24`, `17 + 22` | ok |
| **13** | **C** | sum 41 | `18 + ((18 + 5) % 60)` | **KeyError, agent dies** |
| 10 | A | all seconds summing to 57 | `56 + ((56+5) - 60)` | ok, wrap exercised |
| **11** | **B** | all seconds summing to 57 | contained `%` | **KeyError, agent dies** |
| 12 | B | all seconds summing to 57 | `56 + ((56+5) - 60)` | ok, wrap exercised |
| **08** | **B** | sum 56 (no solution) | `for s in range(60): print(s, s+5, (s+5)%60, …)` | **SyntaxError, agent dies** |

Three things follow, and none of them is the thing I originally claimed.

**1. My stated reason for deleting the sentence was wrong.** Wording A, the one I
removed for being dangerous, has not produced a `%` in either of its runs — and
in run 10 it produced `56 + ((56+5) - 60)`, reaching the same answer with
subtraction. Whatever protection I thought I was buying by deleting it, I was not
buying.

**2. What actually predicts `%` is the task, not the wording.** Every crash came
from a task that genuinely needs wrap reasoning (all seconds summing to 57) or
that has no answer at all (56). The sum-41 task, whose solution is n = 18 with no
wrap anywhere, never produced `%` under A or B. Even then it is not
deterministic: runs 11 and 12 are the *same* wording on the *same* task, one
crashing and one succeeding. A single run per arm cannot separate these, and I
had been about to write down a causal claim from exactly that.

**3. The one wording that broke an otherwise safe task is the one I wrote to
replace B.** Wording C raised the wrap — "five seconds after 57 is 2" — in front
of a task that never needs it, and the model immediately produced
`18 + ((18 + 5) % 60)` on a problem it had solved cleanly three times before.
C also dropped B's "no further arithmetic is needed to interpret them". I cannot
tell from one run which half did it, but the cheap hypothesis is that the
explicit instruction *not* to keep computing is doing more work than any phrasing
of the range itself.

So the defensible version of my original argument is narrower and, I think, more
useful: **a tool description should not introduce a concept the task does not
already require.** B says what the values are and tells the model to stop there.
A is also fine in practice. C fails not because it is inaccurate — it is the most
accurate of the three — but because it puts modular arithmetic in the model's
head on a task where modular arithmetic never comes up.

I shipped B, and I reverted C after one bad run rather than keeping it, which is
the same discipline: do not change the interface on n = 1. Run 14 is the
canonical run of the shipped wording.

## Making the agent actually loop: the `at_second` parameter

The first version of my task was a straight line — record, read back, add — which
is three tool calls in a fixed order and not much of an agent. The task is now:

> `seconds_recorder` records two numbers: the second it runs at, and the second
> five seconds after that. Find the second it must run at for those two numbers
> to add up to exactly 41. Test candidate seconds with `calculator` until you
> find it. Then run `seconds_recorder` at that second with path `notes.txt`.
> Finally read `notes.txt` with `read_file` and add its two values with
> `calculator` to confirm the sum is 41.

Then the hard part: how does the agent *act at* second 18? I first considered
pure polling — call the tool, read it back, check, retry. The arithmetic ruled it
out:

- Because the tool returns only a confirmation and not the numbers, a probe needs
  `seconds_recorder` **and then** `read_file` before the agent knows what it
  recorded — strictly sequential, so they cannot be batched into one turn. That
  is **two requests minimum**, three if the model also asks `calculator` to check
  the sum. Had the tool returned the values, a probe would have been **one**
  request; the extra cost is a direct consequence of my own choice to withhold
  them.
- I measured the round trip at **2.1 s and 4.5 s**, so a probe burns roughly 4–14
  seconds of wall clock. With `max_steps = 20` that allows on the order of 6–10
  probes, each landing on about one second out of sixty — a hit probability near
  **1 − (59/60)⁷ ≈ 11%**, for ~20 requests, 40% of OpenRouter's free daily
  allowance.

An earlier draft claimed the probes would sample at a fixed ~15-second stride and
could miss 18 *indefinitely*. That was wrong; measuring corrected it. The latency
varies by more than a factor of two, so the sampled seconds drift rather than
locking into a lattice. The real objection to polling is not impossibility, it is
a bad price for a coin flip.

So instead of making the agent *guess* when to act, I gave it a way to *say*
when: an optional `at_second`, described as

> Pass `at_second` to make the tool wait until the clock reaches that second
> before recording; omit it to record immediately.

plus, on the property itself, "*Waiting takes up to one minute.*" State the
effect and its cost, say nothing about the mechanism — the model is not told that
the tool busy-waits. **The wait costs no API requests at all**, which is the
whole point: polling spends requests to pass time, `at_second` spends only time.
Omitting the parameter keeps the original behaviour, so earlier runs stay
reproducible, and the tool count stays at three.

## The solution space, worked out before running anything

`sum(n) = n + ((n + 5) mod 60)`. For `n ≤ 54` that is `2n + 5`; for `n ≥ 55` it is
`2n − 55`. Both are odd, so:

- **Every reachable sum is odd.** An even target such as **56 has no solution at
  all** — worth knowing before spending requests on it.
- `2n − 55` only reaches 55, 57, 59, 61, 63, and every one of those is also
  reachable from the first branch. So **55, 57, 59, 61, 63 each have two
  answers** (e.g. 57 ← n = 26 giving 26/31, and n = 56 giving 56/1), and every
  other reachable sum has exactly one.
- **No target forces the wrapped branch on its own.** To make the model deal with
  the wrap I had to ask for *every* second that works, not just one.

## Three smaller choices in the same spirit

1. **It returns a confirmation, not the numbers.** Had I returned them, the model
   would have had no reason to call `read_file` and the run would collapse to one
   tool call. Withholding them forces a real chain — at the probe cost analysed
   above, which I did not foresee when I made the choice.
2. **`path` and `at_second` each carry their own `description`.** Neither starter
   tool describes its properties. If the description is the interface, the
   schema's property descriptions are part of it.
3. **Failures return strings; nothing raises.** A bad directory comes back as
   `failed to write: [Errno 2] ...`, an out-of-range wait as `denied: at_second
   must be a whole number 0-59, got 99`. This mirrors `read_file`'s `denied: ...`
   string but goes further: my path check uses `os.path.commonpath`, not the
   starter's `full.startswith(os.getcwd())`, which lets a sibling directory like
   `week-01-backup` through on a prefix match. I left the two tools inconsistent
   on purpose — writing is more dangerous than reading.

## Three things the runs taught me that I was not looking for

**The model treats `calculator` as a Python REPL when cornered** (run 08, target
56, no solution). It bracketed the target with `25 + 30 = 55` and `26 + 31 = 57`,
inferred there was no integer between them, and then sent

```
for s in range(60): print(s, s+5, (s+5)%60, s+((s+5)%60))
```

`ast.parse(..., mode="eval")` rejects a statement, so it died on `SyntaxError`
before `ast.Mod` even got a chance. An impossible task did not make the agent
give up; it made it escalate to a tool use the description never offered.

**The model writes modulo in prose and avoids it in tool calls.** Runs 10, 12 and
14 all say "mod 60" in the final answer while sending `(56+5) - 60` or plain
`18 + (18 + 5)` to `calculator`. Whatever is happening, the expression it emits
is not a direct transcription of the sentence it writes.

**My logging hid the evidence.** The starter prints `[tool] name(args) -> out`
*after* the call returns, so the one call that kills the process is the one that
never gets logged — run 11 shows a bare traceback with no indication of what was
sent. I split the print in two, before and after execution, and re-ran; run 12
onward shows the arguments of every call including fatal ones. This is the change
I would keep even if nothing else survived.

## What I have not shown

- **The A/B is not settled.** One run per arm, and runs 11 and 12 prove the same
  arm can go both ways. Separating wording from sampling noise needs several runs
  per arm; at 50 free requests a day and ~5 per run, that is a second day's
  budget, not this one's.
- **Whether B's protection comes from "no further arithmetic" or from staying
  silent about the wrap** is untested. C changed both at once — my mistake in
  designing it.
- **The description cannot be the real fix.** Both shipped wordings have crashed
  at least once. The structural fixes — wrapping the tool call in `try`/`except`
  and returning the error as a `tool` message so the model can retry, or adding
  `ast.Mod: operator.mod` to the whitelist — are one line each and I deliberately
  did neither, because the crash is the evidence this assignment is about. A
  grader running the sum-41 default will most likely see it succeed (runs 07, 14),
  but it is not guaranteed.
- **`google/gemma-4-31b-it:free` never produced a run** — first a 404 from a
  zero-data-retention account setting, then repeated upstream 429s on the shared
  free pool (runs 01, 04, 05). The cross-model comparison is missing entirely.

## Files and how to reproduce

`notes.txt` is the agent's **output**, not its input: `seconds_recorder` writes it
during the run, and its committed contents (`time: 18` / `time_after_five: 23`)
are the answer to the default task. `seconds57.txt` (`time: 56` /
`time_after_five: 1`) is from the two-answer experiment and is the only artifact
where the wrapped branch actually ran. The starter's meeting-memo `notes.txt` was
removed — leaving it would have given `read_file` a second, irrelevant file to
pick from.

```bash
pip install openai
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<your OpenRouter key>     # never committed
export AGENT_MODEL=minimax/minimax-m3:free
export AGENT_MAX_STEPS=20                        # optional; also the cost ceiling
python first_agent.py                            # the goal is the script's default
python first_agent.py "<your own goal>"          # e.g. the sum-57 experiment
```

Requests per run, counted from the logs (one request per turn, one tool call per
turn here): run 03 → 4, run 07 → 5, run 14 → 8, run 08 → 3 before crashing. An
OpenRouter account with zero-data-retention **off** is required, or every free
endpoint is filtered out with a 404 before the request reaches a provider. A run
takes up to a minute longer than the API calls themselves, because `at_second`
waits for the clock.
