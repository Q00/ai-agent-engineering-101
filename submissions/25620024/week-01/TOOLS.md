# TOOLS.md — defending the wording of `write_note`

The third tool is `write_note`, and the description handed to the model is:

> Append one line of text to a note file in the working directory. Appends
> only — it never overwrites or deletes existing content, and it creates the
> file if it does not exist. Use it when the user asks for a result to be
> recorded or saved; do not use it to think out loud. Returns the file's new
> line count.

Every clause is there to remove a specific guess the model would otherwise have
to make. The first cut said only "Write a note to a file", and the problem with
that sentence is that it does not say whether calling it destroys what is
already in the file: a model that cannot tell the difference between append and
overwrite has to reason about blast radius before every call, which is exactly
the kind of hesitation a tool description exists to prevent — so the append
semantics and the create-if-missing behaviour are now stated outright. The
second clause exists because a third tool changes the selection problem rather
than just extending it: with two tools the chain was forced by the data
(nothing but `read_file` can start, nothing but `calculator` can follow), while
`write_note` is callable at *every* step and gives the model a place to narrate
intermediate thoughts, so the description names the condition under which it
should fire and then rules out the failure mode the positive clause still
leaves open. The scope phrase "in the working directory" is not decoration
either — the implementation enforces that sandbox, and a description that
promises more than the code allows produces denials mid-loop that the model
cannot interpret, so the interface and the guard are deliberately kept in
agreement. Finally the tool returns the file's new line count instead of "ok",
because the return value is the model's only sense organ: "ok" is a claim the
model must take on faith, whereas a line count is an observation it can check
the next time around, which is the whole point of putting the tool inside a
loop rather than calling it once.

## How to run

```bash
pip install anthropic
export ANTHROPIC_API_KEY=...        # never committed; environment only
cd submissions/25620024/week-01
python first_agent.py 2>&1 | tee logs/run-01.txt
```

Model: `claude-sonnet-4-5`. Default goal: read `notes.txt`, sum every number in
it, record the total in `result.md`. Tool schemas are the `TOOLS` list in
`first_agent.py`; `max_steps` defaults to 8.

Without an API key, `python verify_offline.py` exercises the same loop against
a scripted stub client and writes `logs/offline-loop-verification.txt`. That
file proves the harness, not the model — it is not a substitute for `run-01.txt`.
