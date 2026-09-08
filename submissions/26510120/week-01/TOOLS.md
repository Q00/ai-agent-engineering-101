# TOOLS.md — why `write_note` is described the way it is

My third tool is `write_note`, and I described it as
**"Save a result or note you want to keep, appended to a file."**

I wrote it that way on purpose. The obvious alternative was to describe the
mechanism — "append a line of text to a file in the working directory" — but
that only tells the model *what the function does*, and the model already sees
that from the parameter names. What the model cannot infer is *when* it is
supposed to reach for this tool, so that is what I put in the description:
"a result you want to keep". I wanted the agent to decide for itself that a
computed total is worth keeping, rather than only writing when a prompt orders
it to. I also let the model choose the `path` instead of fixing one in code,
because a tool that picks its own filename gives me more of the model's
behaviour to look at.

The deliberate omission is the working-directory limit. The implementation
refuses any path outside the current directory, exactly like the starter's
`read_file`, but the description does not mention that restriction. I could
have closed the gap by documenting it. I chose to leave it open so I could see
what the agent does when a tool fails for a reason it was never told about —
whether it gives up, repeats the same call, or repairs itself.

**What I predicted before running:** (1) with a third tool available the model
would call `write_note` on its own once the goal implied keeping a result, and
(2) it would eventually hit `denied` because nothing in the description warns
it about the directory limit.

**What actually happened** (`logs/`): on the starter's default goal — "Read
notes.txt and sum the numbers in it" — the model used `read_file` and
`calculator` and *never* touched `write_note`. Adding a tool did not by itself
change behaviour; the goal had to imply keeping something. When I changed the
goal to "...keep a record of the total so I can find it later", the model
called all three tools and invented the filename `total.txt` (and `total_sum.txt`
on a rerun) without being told one. When I asked it to save to `/tmp`, it was
denied, and then — with no hint from me — retried the same content at a
relative path and reported the failure honestly in its final answer. So the
gap in my description cost one wasted call, and the model recovered from it.

The omission also exposed a real bug in my own tool: the model repeatedly sent
`text` as the bare number `69504` even though the schema declares it a string,
which crashed `text.rstrip()`. That crash is in `logs/run-0908-1834-denied.txt`
and the one-line fix (`text = str(text)`) is its own commit. A schema is a
request, not a guarantee, and the tool has to survive the model ignoring it.

## How to reproduce

```bash
export OPENAI_API_KEY=...            # an OpenRouter key
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export AGENT_MODEL=cohere/north-mini-code:free
python first_agent.py "Read notes.txt, sum the numbers, and save the total to /tmp/meeting_total.txt"
```

Note on models: the README's suggested `meta-llama/llama-3.3-70b-instruct:free`
no longer exists as a free model on OpenRouter. Of the 15 free tool-capable
models I tried, 10 returned tool calls at all; the Google ones kept returning
429 and `nvidia/nemotron-3-super-120b-a12b:free` returned an empty `choices`
array mid-run (`logs/run-0908-1833-save.txt`). `cohere/north-mini-code:free`
was the one that ran the whole loop reliably.
