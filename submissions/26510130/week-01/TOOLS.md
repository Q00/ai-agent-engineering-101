# TOOLS.md — 26510130, week-01

New tool: `list_files`. Model: `minimax/minimax-m3:free` via OpenRouter.

| Tool | Role | Origin |
|---|---|---|
| `calculator` | Arithmetic over an AST whitelist | starter |
| `read_file` | Read a text file under the working directory, 4000 chars | starter |
| `list_files` | List the files and directories the agent can see | **new** |

## Why `list_files`

The starter agent can read a file but cannot find one. With only
`read_file(path)`, the model learns a path from exactly one place: the user
prompt. That is why the starter's own example goal spells out `notes.txt`. The
gap is in the observation step of the loop — the agent is asked to act on an
environment it has no way to look at — so before adding anything I changed the
goal to stop naming the file:

> Find the memo file in this folder and sum the numbers written in it.

That makes `list_files` load-bearing rather than decorative, and it turned out
to be the whole experiment: every interesting failure below comes from the gap
between how the request names a file and what the file is actually called.

## Defending the wording

**v1** was the same terse one-liner as the two starter tools:

```
List files in the working directory.
```

I expected a free model not to reach for a tool described that thinly and to
guess a filename instead. That was wrong, and run-01 disproved it on step 1:
the model called `list_files` first thing, unprompted. Tool *selection* was
never the problem.

What v1 actually failed at is everything after the listing. The listing comes
back without `memo` in it, and v1 never says what that means, so the model
invented a policy per run:

| Run | v1 behaviour after the listing | Outcome |
|---|---|---|
| run-01 | also listed `logs/`, then read `notes.txt` | 69504 |
| run-02 | read `notes.txt` directly | 69504 |
| run-03 | listed `logs/` first, read `notes.txt` on step 3 | 69504 |
| run-04 | read `notes.txt` and listed `logs/` in one step | 69504 |
| run-05 | read `notes.txt` directly | 69504 |
| run-06 | **stopped and asked the user which file was meant** | **failed** |

run-06 is the one that matters. It said:

> I don't see a memo file in this folder... There's no file specifically named
> "memo"... Could you clarify which file you'd like me to look at? If you meant
> `notes.txt`, I can read that and sum any numbers in it.

It named the correct next action and then declined to take it, with `notes.txt`
in front of it. Same code, same goal, same `temperature=0.0`, same directory
contents as run-05, which succeeded.

**v2** adds two sentences, each aimed at one half of that failure:

```
List the files and directories in the working directory, with each name and
its size. This listing is complete: these are the only files that exist. A
request may describe a file by what it contains rather than by its name, so if
no name matches, read the most likely candidate instead of asking which file
was meant.
```

1. **"This listing is complete: these are the only files that exist."** The
   model kept treating a listing without the requested name as inconclusive —
   hence the repeated digging in `logs/`, and run-06's conclusion that the file
   "doesn't appear to exist in this folder". A tool that reports what it found
   says nothing about what it did not find. This sentence is what turns *I have
   not found it* into *it is not there*, and closing the search is a
   precondition for moving on.

2. **"...read the most likely candidate instead of asking which file was
   meant."** v1 described a return value and left the next decision to the
   model, which is why the next decision came out differently on nearly every
   run. Naming the fallback moves that decision out of the sampler and into the
   interface. This is the sentence I would defend hardest: a description that
   only says what a tool returns is not an interface, it is a docstring.

**Result:** run-07, run-08 and run-09 all solved the task, 3/3, no clarifying
question. The give-up failure did not recur.

**Result I did not want:** run-08 read `first_agent.py` as well as `notes.txt`.
"Read the most likely candidate" invited it to read *the candidates*, and with
two files listed it read both. So v2 bought determinism in the **outcome** —
9 successful runs, 69504 every time — and not in the **path**, which still
varies between three and four tool calls. I am reporting that rather than
claiming the description fixed reproducibility, because it did not.

## What I deliberately left out of the description

- **The sandbox rule.** `read_file` and `list_files` both refuse paths outside
  the working directory and anything inside `logs/`, and the description says
  none of it. A refusal is an observation the tool returns
  (`denied: path is outside the working directory...`), not documentation the
  model needs up front. Advertising a restriction spends context on every call
  and invites attempts to work around it.
- **Call ordering.** My first plan for v2 was to write "call this before
  `read_file`". run-01 had already shown the tool gets called first without
  being told, so the sentence would have defended a problem that did not exist.
  Dropped.

## What I tried and discarded

- **Hypothesis: a terse description is too thin for a free model to pick the
  tool.** Wrong (run-01). Tool selection worked; the listing's aftermath did
  not.
- **Hypothesis: the varying tool path is a sampling artifact.** Pinned
  `temperature=0.0`. run-03 and run-04 still took different paths. Wrong, but
  the pin stays — "state everything except the API key" is not satisfied by a
  sampling parameter left at a client default.
- **Claim: "same code, same goal, same model, different path."** Also wrong,
  and this was my own bug rather than the model's. run-03 caught the agent
  listing `logs/` and seeing the 0-byte file *the run in progress was writing*.
  `logs/` gains a file every run, so run-01 observed one log and run-04
  observed four: the environment was changing under every run and the
  comparison was never valid. Fixed by making the agent's own transcript
  directory unobservable (`HIDDEN`, enforced in `_safe_path` so `read_file`
  honours it too — a rule applied to one tool and not the other is not a rule).
- **Consequence I did not predict:** with `logs/` hidden, the wasted
  `list_files('logs')` calls disappeared and the task started failing outright
  (run-06). Those wasted calls had been the detour the model used to get
  unstuck from "no file called memo". Removing the symptom exposed the disease.

## Other changes to the starter, and why

- `_safe_path()` replaces `os.path.abspath(path).startswith(os.getcwd())`. The
  original follows no symlinks and accepts a sibling directory on a prefix
  collision — with `cwd=C:\work` it admits `C:\workspace`. `os.path.realpath`
  plus a `root + os.sep` comparison fixes both, and sharing it between the two
  file tools is what made the `HIDDEN` rule enforceable in one place.
- Tool exceptions and unknown tool names come back as `tool error: ...`
  observations instead of raising. The starter lets one bad tool call kill the
  loop; an agent that cannot see its own failure cannot retry. Free models do
  invent tool names, so the unknown-name branch is not hypothetical.
- The run header prints `model`, `temperature` and `max_steps`, so every
  capture in `logs/` states the configuration it ran under instead of relying
  on this file to be accurate about it.

## Reproducing this

```bash
pip install openai

# PowerShell
$env:OPENAI_BASE_URL = "https://openrouter.ai/api/v1"
$env:OPENAI_API_KEY  = (Read-Host "paste OpenRouter key").Trim()
$env:PYTHONUTF8      = "1"
python first_agent.py
```

| Setting | Value | Where |
|---|---|---|
| Model | `minimax/minimax-m3:free` | `MODEL`, overridable via `AGENT_MODEL` |
| Temperature | `0.0` | `TEMPERATURE`, pinned in code |
| Max steps | `8` | `MAX_STEPS` |
| `max_tokens` | not set (provider default) | — |
| Tool schemas | 3, OpenAI function format | `TOOLS` |
| Goal | `Find the memo file in this folder and sum the numbers written in it.` | `__main__`, or `argv[1]` |
| Input | `notes.txt` (starter's file, unmodified) | — |
| Expected answer | **69504** = 4 + 48000 + 9500 + 12000 | `notes.txt` says "sum the numbers above" |
| API key | env var only, never in the repo | — |
| Python / SDK | 3.14.3 / `openai` 3.8.0 | — |

Logs were captured with `cmd /c "python first_agent.py > logs\run-NN.txt 2>&1"`
rather than `Tee-Object`: on Windows PowerShell 5.1 `Tee-Object` has no
`-Encoding` and writes UTF-16LE, and `2>&1` on a native command makes
PowerShell wrap stderr in ErrorRecords and mix locale decoration into the
capture. `run-00` and `run-01` were captured that way before I noticed and were
re-encoded to UTF-8 by hand; their content is unmodified.

### What reproduces and what does not

Nine runs, nine answers of 69504. The **answer** reproduces. The **tool path**
does not: 3–4 tool calls, 4–5 steps, and under v1 one run in six abandoned the
task. Two variance sources I found and closed (the observable `logs/`
directory, the unpinned temperature) and one I left open on purpose: the
listing reports `first_agent.py`'s size, so editing the agent changes what the
agent sees. It is constant within a version, which is what a reproduction
needs, and hiding the agent's own source would have meant hiding the only other
file in the task environment.

### Run index

| Log | Version | Result |
|---|---|---|
| `run-00-auth-failure.txt` | v1 | never reached the model: OpenRouter 401, key was one character short |
| `run-01.txt` | v1 | 69504, 4 tool calls, detoured into `logs/` |
| `run-02.txt` | v1 | 69504, 3 tool calls |
| `run-03.txt` | v1 + `temperature=0` | 69504, 5 steps; caught the agent observing its own log |
| `run-04.txt` | v1 + `temperature=0` | 69504, 4 tool calls |
| `run-05.txt` | v1 + `logs/` hidden | 69504, 3 tool calls |
| `run-06.txt` | v1 + `logs/` hidden | **failed** — asked the user to clarify |
| `run-07.txt` | **v2** | 69504, 3 tool calls |
| `run-08.txt` | **v2** | 69504, 4 tool calls — also read `first_agent.py` |
| `run-09.txt` | **v2** | 69504, 3 tool calls |

## On using an LLM for this

Written with Claude Code, which is allowed. Every hypothesis above that turned
out wrong was wrong in the commit history before it was corrected, and the
runs that produced the evidence are in `logs/` unedited. The two rewrites I
would flag: the `logs/`-hiding change came from reading run-03's transcript,
not from planning, and the v2 wording was written against run-06's specific
refusal rather than from general advice about writing tool descriptions.
