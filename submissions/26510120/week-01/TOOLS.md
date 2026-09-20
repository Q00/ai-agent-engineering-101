# TOOLS.md — why `write_note` is described the way it is

My third tool is `write_note`. I described it as
**"Save a result or note you want to keep, appended to a file."**

At first I was going to describe what the function does: "append a line of text
to a file in the working directory." I changed it because the model can already
guess that much from the parameter names `path` and `text`. What it cannot
guess is *when* it should use the tool, so I put that in the description
instead. I wanted the agent to decide on its own that a computed total is worth
saving, not to wait until a prompt tells it to write something. For the same
reason I let the model pick the `path` rather than fixing a filename in code.

I also left one thing out on purpose. The implementation refuses any path
outside the current directory, the same guard the starter's `read_file` has,
but the description says nothing about that limit. I could have documented it.
I left it open because I wanted to see what the agent does when a tool fails
for a reason it was never told about.

**What I predicted before running:** (1) once a third tool existed the model
would call `write_note` by itself when the goal implied keeping a result, and
(2) it would hit `denied` at some point, since nothing warns it about the
directory limit.

**What the logs show:** On the starter's default goal, "Read notes.txt and sum
the numbers in it," the model used `read_file` and `calculator` and never
called `write_note` at all. So adding a tool did not change the behaviour by
itself; the goal had to imply keeping something. When I changed the goal to
"...keep a record of the total so I can find it later," it called all three
tools and made up the filename `total.txt` on its own (`total_sum.txt` on a
rerun). When I asked it to save to `/tmp`, it was denied, then retried the same
content at a relative path without any hint from me, and said in its final
answer that `/tmp` had been refused. My missing sentence cost one wasted call
and the model recovered from it.

The same run also found a bug in my tool. The model kept sending `text` as the
number `69504` even though my schema says it is a string, and `text.rstrip()`
crashed on it. The crash is in `logs/run-0908-1834-denied.txt` and the fix
(`text = str(text)`) is a separate commit. Declaring a type in the schema does
not mean the model will send that type, so the tool has to handle it.

## How to reproduce

```bash
export OPENAI_API_KEY=...            # an OpenRouter key
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export AGENT_MODEL=cohere/north-mini-code:free
python first_agent.py "Read notes.txt, sum the numbers, and save the total to /tmp/meeting_total.txt"
```

A note on models: the README suggests `meta-llama/llama-3.3-70b-instruct:free`,
but that model is no longer free on OpenRouter. I tried the 15 free models that
list `tools` support and 10 of them returned tool calls. The Google ones kept
returning 429 and `nvidia/nemotron-3-super-120b-a12b:free` returned an empty
`choices` array in the middle of a run (`logs/run-0908-1833-save.txt`).
`cohere/north-mini-code:free` ran the whole loop reliably, so that is the model
in the command above.
