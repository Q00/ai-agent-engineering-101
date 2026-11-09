# PROCESS.md — week-01 work log

Student 26512070 (@gom31). `TOOLS.md` argues the tool-description question this
assignment actually asks; this file is the chronology around it — what broke,
in what order, and what each blocker cost. Grading is half process, so the
process is written down rather than implied.

## Setup

Forked, cloned, `upstream` added. Roster PR (`[roster] 26512070`) opened first —
`scripts/check_pr_paths.py` resolves `submissions/<id>/**` ownership through
`roster/<id>.md`, so a week-01 PR cannot pass the ownership check until the
roster file is on upstream `main`.

No Anthropic key, so the whole assignment runs on OpenRouter's free tier. That
turned out to shape almost every decision below.

## Choosing a model

The README's example, `meta-llama/llama-3.3-70b-instruct:free`, is no longer in
OpenRouter's catalogue. Querying `/api/v1/models` directly: 18 free models, 17 of
which declare `tools` support. Sorting those by Artificial Analysis'
`agentic_index`:

| agentic | coding | model |
|---:|---:|---|
| 31.0 | 58.6 | `minimax/minimax-m3:free` |
| 25.1 | 52.9 | `thinkingmachines/inkling-small:free` (no `tool_choice`) |
| 21.7 | 49.3 | `nvidia/nemotron-3-ultra-550b-a55b:free` |
| 6.8 | 43.4 | `google/gemma-4-31b-it:free` |
| — | 52.6 | `minimax/minimax-m2.7:free` (`reasoning.mandatory: true`) |

I picked Gemma first anyway. Declaring `tools` only means the API accepts the
parameter, so before spending a full 8-step run on a model I wrote a smoke test
that sends **one** request with the three real schemas and reports whether a
`tool_call` came back. One request per model instead of eight is the difference
between testing four models and testing zero, on a 50-request daily budget.

## Blockers, in the order they happened

1. **Gemma: `404 zdr-violation-by-account`.** Not the model — the account. A
   zero-data-retention preference filters out every free endpoint that retains
   prompts, which is most of them. Configurable at
   `openrouter.ai/settings/privacy`.
2. **Turning ZDR fully on blocked MiniMax M3 too**, which had passed the smoke
   test ten minutes earlier. Same 404, and for a while it looked like a model
   problem rather than a settings problem. Logs `run-01`, `run-02`.
3. **ZDR off → M3 works.** Gemma then failed differently: `429`,
   `limit_source: upstream_provider_shared_pool`, twice, minutes apart. Logs
   `run-04`, `run-05`. **Gemma never produced a single run**, so the
   weak-model/strong-model comparison I wanted does not exist.
4. **`UnicodeEncodeError: 'cp949'`** (`run-06`). Six tool calls succeeded,
   `notes.txt` was written correctly, and then the final `print` died because the
   model's answer contained an em dash and the Windows console is cp949. The work
   succeeded and the process still exited 1. Fixed with
   `sys.stdout.reconfigure(encoding="utf-8")` in `__main__`.
5. **The log hid the call that killed the agent.** The starter prints
   `[tool] name(args) -> out` *after* the call returns, so a tool that raises
   leaves no record of what it was asked to do — `run-11` is a bare traceback.
   Splitting the print into before/after is what let `run-13` capture
   `18 + ((18 + 5) % 60)`.

## Working environment

Windows 11, Python 3.14. `python` is shadowed by the Windows Store alias, so
everything runs through `py`. The OpenRouter key lives in a PowerShell script
**outside the clone** (`../../../../or-key.ps1`, dot-sourced) — the repository's
`.gitignore` covers only `.env`, and `.gitignore` sits outside
`submissions/26512070/`, so I cannot add an ignore rule for a key file even if I
wanted one. Outside the repo is the only place git cannot reach.

## Decisions, and where they are argued

| Decision | Where |
|---|---|
| Third tool = a clock recorder that emits number-bearing text | `TOOLS.md` § The gap I was filling |
| Seconds chosen to collide with `calculator`'s missing `%` | `TOOLS.md` § Why seconds specifically |
| Returns a confirmation, not the numbers | `TOOLS.md` § Three smaller choices |
| `at_second` instead of polling | `TOOLS.md` § Making the agent actually loop |
| Wording A / B / C comparison | `TOOLS.md` § The claim I made |

Two corrections I had to make to my own reasoning are recorded in the history
rather than quietly patched: commit `faa041b` replaces an unmeasured latency
argument ("polling can miss the target indefinitely") with measured numbers and
says the earlier version was wrong; commit `4191e9f` records that the central
claim of my first `TOOLS.md` draft did not survive being tested.

## Run index

| log | model | wording | task | outcome |
|---|---|---|---|---|
| `run-01-gemma` | gemma | — | sum 41 chain | 404 ZDR |
| `run-02-minimax-m3` | m3 | — | sum 41 chain | 404 ZDR |
| `run-03-minimax-m3` | m3 | B | straight-line record/read/add | ok, sum 91 |
| `run-04-gemma` | gemma | B | search 41 | 429 upstream |
| `run-05-gemma-retry` | gemma | B | search 41 | 429 upstream |
| `run-06-search-sum41` | m3 | B | search 41 | tools ok, cp949 crash on print |
| `run-07-search-sum41` | m3 | B | search 41 | ok |
| `run-08-target56-no-solution` | m3 | B | search 56 (impossible) | sent a Python `for` loop, SyntaxError |
| `run-09-AB-wrap-hint-restored` | m3 | A | search 41 | ok |
| `run-10-A-target57-all` | m3 | A | all seconds summing to 57 | ok, wrap branch exercised |
| `run-11-B-target57-all` | m3 | B | all seconds summing to 57 | `%` → KeyError |
| `run-12-B-target57-all-visible` | m3 | B | all seconds summing to 57 | ok (same arm as 11) |
| `run-13-final-description` | m3 | C | search 41 | `%` → KeyError |
| `run-14-B-canonical` | m3 | B | search 41 | ok — canonical run of shipped code |

Roughly 48 of the day's 50 free requests. Failed runs are kept unedited.

## What I would do next, given budget

1. **Replicate the A/B properly.** Runs 11 and 12 are the same arm with opposite
   outcomes; five runs per arm is the minimum that could separate wording from
   sampling noise. This is the single biggest hole.
2. **Separate C's two changes.** C dropped "no further arithmetic is needed" *and*
   added a wrapped example. Testing them independently would say which one
   matters.
3. **Get any second model to run**, so the observations are not all one model.
4. **Decide on the structural fix.** Catching tool exceptions and returning them
   as `tool` messages would turn every crash in this log into something the model
   could recover from. I left it out on purpose — the crashes are the evidence —
   but that is a choice, not an oversight.

## LLM use

Claude Code was used throughout, which the course permits on the condition that
what was asked for and what was discarded stays visible. The commit history is
the record: every failed run, both self-corrections, and the reverted wording C
are all in it, unsquashed.
