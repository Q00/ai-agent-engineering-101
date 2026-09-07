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
`TOOLS_IMPL[block.name](**args)` with no `try`/`except`, one bad expression kills
the entire process rather than coming back as an observation the model can react
to.

Seconds are the one everyday quantity that is inherently modular: 57 + 5 has to
be 2, not 62. I chose this domain on purpose. I wanted to see what an agent does
when the task's natural arithmetic is exactly the operation its calculator cannot
perform.

## The sentence I deleted

My first description ended with "*the second value wraps past 59 (57 becomes
2)*". It is accurate, and it is the sentence I removed. Saying it teaches the
model modulo-60 — precisely the operator `calculator` will die on. I checked
rather than assumed:

```
calculator("(19 + 5) % 60")  ->  KeyError: <class 'ast.Mod'>   # agent process dies
calculator("19 + 5")         ->  24
```

The current wording keeps the constraint and drops the mechanism:

> Both are **final values**: whole numbers from 0 to 59, **already adjusted to
> stay in that range. Use them as they are; no further arithmetic is needed to
> interpret them.**

The model still knows the range it will get back, so it can sanity-check the
values; it is given no reason to reach for `%`. The wrap itself happens in Python
inside the tool — `(now + 5) % 60` — where the whitelist does not apply.

This is the wording I most want to defend, because nothing about the
implementation changed between the two versions. One sentence of prose is the
difference between a description that invites a crash and one that does not. The
description is not documentation *about* the tool; as far as the model is
concerned it *is* the tool. The commit that removes that sentence (`26106aa`) is
the whole argument in one diff.

## Making the agent actually loop: the `at_second` parameter

The first version of my task was a straight line — record, read back, add — which
is three tool calls in a fixed order and not much of an agent. The task is now:

> `seconds_recorder` records two numbers: the second it runs at, and the second
> five seconds after that. Find the second it must run at for those two numbers
> to add up to exactly 41. Test candidate seconds with `calculator` until you
> find it. Then run `seconds_recorder` at that second with path `notes.txt`.
> Finally read `notes.txt` with `read_file` and add its two values with
> `calculator` to confirm the sum is 41.

The arithmetic: `n + ((n + 5) % 60) = 41`. For `n <= 54` that is `2n + 5 = 41`,
so **n = 18** and the pair is 18 / 23. The wrapped branch (`n >= 55`) gives
`2n - 55 = 41`, i.e. n = 48, which contradicts `n >= 55` — no solution. So the
answer is unique.

Then the hard part: how does the agent *act at* second 18? I first considered
pure polling — call the tool, read it back, check, retry. It does not work here,
and the reason traces straight back to my own design choice:

- Because the tool returns only a confirmation and not the numbers, **one probe
  costs three requests** (`seconds_recorder`, `read_file`, `calculator`).
- Each API round trip is roughly five seconds, so the wall clock advances ~15
  seconds per probe. The probes therefore sample seconds about 15 apart —
  5, 20, 35, 50, 5, … — and can miss 18 indefinitely.
- One such run would also consume most of OpenRouter's 50-requests-per-day free
  allowance.

So rather than making the agent *guess* when to act, I gave it a way to *say*
when: an optional `at_second`. Its description is

> Pass `at_second` to make the tool wait until the clock reaches that second
> before recording; omit it to record immediately.

plus, on the property itself, "*Waiting takes up to one minute.*" That is the
same discipline as the modulo sentence: state the effect and its cost, say
nothing about the mechanism. The model is not told that the tool busy-waits, or
how it compares clock values — only what it gets and what it will pay. Omitting
the parameter keeps the original behaviour, so the earlier runs stay
reproducible.

This kept the tool count at three. It also moved the *search* — the part I
actually wanted to observe — into the model, where `calculator` is the only way
to test a candidate.

## Three smaller choices in the same spirit

1. **It returns a confirmation, not the numbers** — `wrote 2 lines to
   notes.txt`, and the description says so explicitly. Had I returned the
   values, the model would have had no reason to call `read_file`, and the run
   would have collapsed into a single tool call. Withholding them forces a real
   chain. As the polling analysis above shows, this choice has a cost I did not
   foresee when I made it, which seems worth admitting.
2. **`path` and `at_second` each carry their own `description`.** Neither
   starter tool describes its properties. If the description is the interface,
   the schema's property descriptions are part of it.
3. **Failures return strings; nothing raises.** A bad directory comes back as
   `failed to write: [Errno 2] ...`, an out-of-range wait as `denied: at_second
   must be a whole number 0-59, got 99`, so the model can observe the refusal
   and try something else instead of taking the process down. This mirrors
   `read_file`'s `denied: ...` string, but goes further than `read_file` does:
   my path check uses `os.path.commonpath`, not the starter's
   `full.startswith(os.getcwd())`, which lets a sibling directory like
   `week-01-backup` through on a prefix match. I left the two tools inconsistent
   on purpose — writing is more dangerous than reading, and the mismatch is
   easier to argue about than to silently paper over.

## What the runs showed

**Straight-line task** (`logs/run-03-minimax-m3.txt`): the chain ran first try
and produced `calculator('43 + 48') -> 91`. The sum is outside the 0–59 range the
description advertises, and the model did not try to reduce it — with the wrap
sentence gone, it never reached for the operator that would have killed the loop.

**Search task** (`logs/run-06-search-sum41.txt`, `run-07-search-sum41.txt`): both
runs solved it, but not identically.

```
run-06:  calculator('18 + (18 + 5)') -> 41
         calculator('17 + (17 + 5)') -> 39
         calculator('19 + (19 + 5)') -> 43
         seconds_recorder({'path': 'notes.txt', 'at_second': 18})
         read_file('notes.txt')      -> time: 18 / time_after_five: 23
         calculator('18 + 23')       -> 41

run-07:  calculator('18 + (18 + 5)') -> 41
         seconds_recorder({'path': 'notes.txt', 'at_second': 18})
         read_file('notes.txt')      -> time: 18 / time_after_five: 23
         calculator('18 + 23')       -> 41
```

Both hit 18 on the first candidate — the model clearly solved `2n + 5 = 41`
rather than searching blindly. The difference is what happened next: run-06
tested 17 and 19 as well, checking that the neighbours give 39 and 43 and that
the answer is therefore unique; run-07 skipped straight to acting. Same model,
same prompt, same tools, different amount of self-verification. Whatever
"iteration" the agent does here is a tendency, not a guarantee.

In neither run, nor in the straight-line runs, did the model ever put `%` in an
expression.

**A Windows-only crash worth recording**: run-06 finished all six tool calls,
wrote the correct `notes.txt`, and then died on the final `print` with
`UnicodeEncodeError: 'cp949' codec can't encode character '—'`. The model's
answer contained an em dash and the Windows console encoding could not represent
it. Nothing to do with the agent; the fix is one line reconfiguring stdout to
UTF-8 in `__main__`, and `run-07` is the same run with that fix in place. I kept
run-06 because "the work succeeded and the program still exited 1" is exactly the
kind of thing worth having in the record.

## What I have not shown

- **The wrapped branch has never been exercised by the model.** A target of 41
  has no solution with `n >= 55`, so `(now + 5) % 60` wrapping past 59 is only
  covered by my own unit check, never by an agent run. A target such as 3 (which
  needs n = 29) or a task built around the 55–59 range would force it.
- **The controlled A/B on the deleted sentence is still not done.** I have shown
  that the model does not attempt modulo with the current wording; I have not
  shown that it *does* with the original wording. Restoring that one sentence and
  changing nothing else is the obvious next experiment.
- **`google/gemma-4-31b-it:free` never produced a run.** First a 404 from a
  zero-data-retention account setting, then repeated upstream 429s on the shared
  free pool (`logs/run-01-gemma.txt`, `run-04-gemma.txt`,
  `run-05-gemma-retry.txt`). The cross-model comparison I wanted is missing.

## Files and how to reproduce

`notes.txt` is the agent's **output**, not its input — `seconds_recorder` writes
it during the run, and its committed contents (`time: 18` / `time_after_five:
23`) are the answer to the task. Nothing in this task reads a hand-written data
file; the starter's meeting-memo `notes.txt` was removed because leaving it there
would have given `read_file` a second, irrelevant file to pick from.

```bash
pip install openai
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<your OpenRouter key>     # never committed
export AGENT_MODEL=minimax/minimax-m3:free
python first_agent.py                            # the goal is the script's default
```

Model: `minimax/minimax-m3:free` via OpenRouter (OpenAI-compatible endpoint),
`max_steps = 20`. An OpenRouter account with zero-data-retention **off** is
required, or every free endpoint is filtered out with a 404 before the request
reaches a provider. A run takes up to a minute longer than the API calls
themselves, because `at_second` waits for the clock.
