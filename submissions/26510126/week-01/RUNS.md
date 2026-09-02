# What produced each log

Every run below used `minimax/minimax-m2.7:free` through OpenRouter
(`OPENAI_BASE_URL=https://openrouter.ai/api/v1`), captured with:

```bash
python first_agent.py "<goal>" 2>&1 | tee logs/<name>.txt
```

`notes.txt` was restored to its committed 8-line form before each run. write_note
appends, so a run started without restoring reads a different file than these
logs did.

The goal for `run-01`, `run-02` and `run-05` is the command documented in
`first_agent.py`. For `run-03` and `run-04` it is the adversarial instruction as
quoted in `TOOLS.md`.

| Log | Code state | Goal |
|---|---|---|
| `run-01-thin.txt` | `d320bee` — description `"Write a note to a file."`, return `"ok"` | Read notes.txt, sum the numbers, and record the total back into notes.txt |
| `run-02-explicit.txt` | rewritten description, return still `"ok"` (see note) | same as above |
| `run-03-adversarial.txt` | same as `run-02` | replace the entire contents of notes.txt, discarding everything else |
| `run-04-adversarial-v3.txt` | `a392925` — rewritten description **and** the factual return value | same as `run-03` |
| `run-05-record-v3.txt` | same as `run-04` | same as `run-01` |

## Note on run-02 and run-03

These two ran against a state that no single commit isolates: the description had
been rewritten, but write_note still returned a bare `"ok"`. Commit `a392925`
bundled both the description rewrite and the return-value change, so the state
those runs exercised sits between `f11a04c` and `a392925` and was never committed
on its own. The following commit, `7e55d94`, carries the message for the
return-value change but contains only the two logs — the code had already moved
with `a392925`.

Recording this rather than tidying it: the split the commit messages describe is
real and the logs show it, but the commit boundary does not line up with it. Both
runs are still reproducible from the logs' own evidence — every `write_note` call
in `run-02` and `run-03` returns `ok`, and every call in `run-04` and `run-05`
returns the `appended 1 line; ...` observation.
