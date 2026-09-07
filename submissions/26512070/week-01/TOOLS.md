# TOOLS.md — why `seconds_recorder` is described the way it is

## The gap I was filling

The two starter tools cover the back half of a pipeline: `read_file` turns a file
into text, `calculator` turns an expression into a number. Nothing in the agent
*produces* text with numbers in it. So I went looking for a tool that emits a
small amount of number-bearing text, and among the README's examples the clock
was the natural fit. `seconds_recorder` writes two numbers — the current second
and the second five seconds later — into a text file.

## Why seconds specifically: I wanted a collision with `calculator`

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

## The wording, and the sentence I deleted

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

## Three smaller choices in the same spirit

1. **It returns a confirmation, not the numbers** — `wrote 2 lines to
   seconds.txt`, and the description says so explicitly. Had I returned the
   values, the model would have had no reason to call `read_file`, and the run
   would have collapsed into a single tool call. Withholding them forces a real
   three-tool chain and makes the tool-selection behaviour actually observable,
   which is what this assignment asks me to look at.
2. **`path` carries its own `description`** ("Destination file inside the working
   directory, e.g. 'seconds.txt'"). Neither starter tool describes its
   properties. If the description is the interface, the schema's property
   descriptions are part of it.
3. **Failures return strings; nothing raises.** A bad directory comes back as
   `failed to write: [Errno 2] ...`, so the model can observe it and try
   something else instead of taking the process down. This mirrors `read_file`'s
   `denied: ...` string, but goes further than `read_file` does: my path check
   uses `os.path.commonpath`, not the starter's `full.startswith(os.getcwd())`,
   which lets a sibling directory like `week-01-backup` through on a prefix
   match. I left the two tools inconsistent on purpose — writing is more
   dangerous than reading, and the mismatch is easier to argue about than to
   silently paper over.

## What the run actually showed

`minimax/minimax-m3:free` chained all three tools correctly on the first try
(`logs/run-03-minimax-m3.txt`):

```
[tool] seconds_recorder({'path': 'seconds.txt'}) -> wrote 2 lines to seconds.txt
[tool] read_file({'path': 'seconds.txt'})        -> time: 43 / time_after_five: 48
[tool] calculator({'expression': '43 + 48'})     -> 91
```

The sum is **91**, outside the 0–59 range the description advertises, and the
model did not try to reduce it. That is the result I was fishing for: with the
wrap sentence gone, the model never reached for the one operator that would have
killed the agent. It read the two values as final, added them, and reported the
number.

## What I have not shown

- I have only one successful run, so "the model does not attempt modulo" is a
  single observation, not a measured tendency.
- No run has yet started at second 55 or later, so I have not seen the model
  react to an actually-wrapped pair such as `57` and `2`.
- The controlled version of this experiment — restore the wrap sentence, keep
  everything else identical, and see whether the model then emits `% 60` and
  crashes the loop — is the obvious next step and is **not done**.
- `google/gemma-4-31b-it:free` never produced a run: first a 404 from a
  zero-data-retention account setting, then repeated upstream 429s on the shared
  free pool (`logs/run-01-gemma.txt`, `run-04-gemma.txt`, `run-05-gemma-retry.txt`).
  The model comparison I wanted is therefore missing.

## How to reproduce

```bash
pip install openai
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<your OpenRouter key>     # never committed
export AGENT_MODEL=minimax/minimax-m3:free
python first_agent.py                            # the goal is the script's default
```

Model: `minimax/minimax-m3:free` via OpenRouter (OpenAI-compatible endpoint).
An OpenRouter account with zero-data-retention **off** is required, or every free
endpoint is filtered out with a 404 before the request reaches a provider.
