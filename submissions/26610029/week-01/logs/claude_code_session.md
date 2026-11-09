# Claude Code session log — week-01

Summary of the Claude Code session used while building this submission.
Kept as process evidence (course grades on visible attempts/discards, not just the final diff).

## 1. API key found hardcoded in first_agent.py

`first_agent.py` (copied from starter) had an OpenRouter key pasted as a stray
string literal at the end of the file. Claude flagged it as a secret-exposure risk — this repo
never squashes/rewrites history, so a committed key stays in public history permanently even
after later removal. Action taken: key deleted from the file; key revoked and regenerated on
OpenRouter; new key exported as `OPENAI_API_KEY` in the shell (never written to a file in the repo).

## 2. Choosing the third tool

Discussed the three example tools from the README (`fetch`, `clock`, `write_note`).
Decided on **`clock`** — returns today's date, paired with `read_file` (read a deadline from
`notes.txt`) and `calculator` (compute days remaining) so the task exercises all three tools.

## 3. Claude implemented the tool without asking — reverted

Claude wrote the `clock` function, its `TOOLS_IMPL`/`TOOLS` schema entries, and changed the
default goal string, without checking whether the student wanted to write the implementation
themselves. Student pushed back ("내가 하려고 했어") since implementing the third tool is the
assignment's core learning objective. Claude reverted the implementation back to a
`# TODO: add your third tool's schema here.` placeholder and left the two-tool starter state
otherwise intact. Student then wrote the `clock` implementation themselves.

## 4. Syntax check + schema bug found

Ran `python3 -m py_compile first_agent.py` (parses the file without executing it — no API key
needed) to check for syntax errors. Syntax was valid, but the clock tool's schema had
`"required": []` placed as a sibling of `"parameters"` inside the `"function"` dict, instead of
nested inside `"parameters"` (unlike the `calculator`/`read_file` entries). Functionally
harmless since the list is empty, but not the correct OpenAI function-schema shape. Fixed by
moving `"required": []` inside `"parameters"`; re-ran `py_compile` to confirm.

## 5. Next: TOOLS.md

Discussed what belongs in `TOOLS.md` per the README — one paragraph defending the wording of the
new tool's description (`"Get the current date and time."`), written by the student, not Claude,
since it's the assignment's reflection component. Claude offered guiding questions instead of a
draft:
- why this exact phrasing over a shorter/longer alternative
- whether the description could mislead the model about *when* to call `clock`
- how it compares stylistically to `calculator`/`read_file`'s descriptions
- what the actual run log showed about the model's tool-call timing, as supporting evidence
