# Week 02 — ReAct vs Plan-then-Execute

26510130 Hyunsik Wang. Same tools, same task, same model within each block;
only the harness varies.

## 1. The two variants, and which axes they set differently

Both harnesses import every tool, the model call, and the `Meter` from
`tools_shared.py`, so axis 2 is held constant by construction. Of the remaining
four axes, two differ by design, one differs as a consequence, and one cannot
be measured with this tool set.

| Axis | ReAct | Plan-then-Execute | Differs? |
|---|---|---|---|
| 1. Context management | One `Chat`. Full history; every observation is visible when the next action is chosen. | Two `Chat`s. The planner runs with `tools=False` and never sees an observation; the executor sees the plan but not the planner's reasoning. Only a 300-char failure string flows back, on replan. | **Yes — the designed difference** |
| 2. Tool granularity | `TOOL_SPECS` from `tools_shared` | identical | No (held constant) |
| 3. Termination | `max_steps=8`, and the model ends the run by emitting no tool call | plan length decides the step count; plus `max_tool_rounds=3` per step and `max_replan=1` | **Yes — and this is what decided the outcome** |
| 4. Error recovery | a tool error returns as an Observation; the model may re-plan freely on any step | same Observation handling, plus three failure modes ReAct cannot have: unparseable plan (aborts before any tool runs), tool-round budget exceeded (forced `OFF_PLAN`), unparseable replan (`break`) | Yes |
| 5. Human intervention | `IRREVERSIBLE` set + `ask_human()` | no mechanism at all | **Not measurable** |

Axis 5 is worth stating plainly rather than reporting a column of zeros without
comment: `interventions` is 0 in all 29 runs because `IRREVERSIBLE` is empty,
and it is empty because every tool here is read-only. Producing a non-zero
number would mean adding a tool that writes or deletes — which would break
"same tools for both variants" and turn the A/B into a tool comparison. The
axis is present in the code and inert in the measurement; that is a property of
the task, not an oversight.

### The task, and why a second one

`TASK.md` (t1) is the starter task: which hour in `app.log` has the most ERROR
lines. It is single-hop — one sweep answers it — so a plan written before
reading the file is as good as one written after, which is exactly the case
where the planner's blindness costs nothing.

`TASK2.md` (t2) makes hop 2 depend on hop 1: find that hour, then report the
most frequent ERROR message *inside* it. The planner has to write step 2
without knowing which hour step 2 is about. The success string was chosen so
the dependency is what gets measured: over the whole file the most frequent
ERROR is `NullReference in QuizService.score` (7), but inside 14:00 it is
`upstream timeout after 5000ms` (3 of 6), so skipping hop 1 produces a
confident wrong answer. Both task files were committed before any run.

## 2. Measurements

29 runs. `results.csv` is the source; averages below exclude runs with no
meter (crashes).

| Model | Task | Harness | n | O | X | tokens avg | iters avg |
|---|---|---|---|---|---|---|---|
| gemini-3.5-flash-lite | t1 | react | 3 | 0 | **3** | 14,874 | 8 |
| gemini-3.5-flash-lite | t1 | plan_exec | 3 | **3** | 0 | 13,943 | 8 |
| gemini-3.5-flash-lite | t2 | react | 3 | 1 | 2 | 10,658 | 6 |
| gemini-3.5-flash-lite | t2 | plan_exec | 3 | **3** | 0 | 22,962 | 10 |
| nemotron-3.5-lightning:free | t1 | react | 4 | 3 | 1 | 7,272 | 3 |
| nemotron-3.5-lightning:free | t1 | plan_exec | 3 | 3 | 0 | 43,558 | 11 |
| nemotron-3.5-lightning:free | t2 | react | 3 | 3 | 0 | 4,324 | 2 |
| nemotron-3.5-lightning:free | t2 | plan_exec | 3 | 0 | 3 | — | — |
| gemini-2.5-flash | t2 | react | 3 | 0 | 3 | — | — |
| gemini-2.5-flash | t2 | plan_exec | 1 | 0 | 1 | — | — |

Two blocks have no usable numbers, and neither is a harness result. The
nemotron t2 plan_exec cell is three 429 crashes: OpenRouter's free tier allows
50 requests a day, and plan_exec had already spent 33 of the session's 52 —
63% of the requests for 23% of the runs — so the cell that my hypothesis was
about is the one the quota ate. The four `gemini-2.5-flash` rows are crashes from a
session I interrupted: two 429s after discovering that model's free tier is 20
requests **per day**, not per minute, and two 503s ("this model is currently
experiencing high demand") which are not a quota at all. All are kept because
deleting a failed run would be editing the record, and the quota pair is itself
a finding (§3).

Interventions are 0 in all 29 runs (§1).

## 3. Interpretation

**Axis 3 decided success, and not in the direction I predicted.** Before
running I registered that ReAct would win on success and lose on tokens. On
gemini-3.5-flash-lite it lost 1–6 while Plan-then-Execute went 6–6, and it lost
the token comparison on t1 as well. Every one of the five ReAct failures ends
the same way: seven `count_pattern` calls, one per hour, then `MAX_STEPS
reached: incomplete` — the model enumerates hours one at a time and the
iteration cap cuts it off before it can compare them. The one ReAct success on
that model (`t2-react-21`, 2 iterations) made **zero** `count_pattern` calls: it
read the file once and reasoned over it. The harness did not choose between
those two strategies; the model did, differently on different runs, and only
the cheap strategy fits inside eight steps. So `max_steps=8` is not a safety
net here but the binding constraint, and ReAct's other termination condition —
the model decides it is done — is precisely what a weak model cannot do
reliably. Plan-then-Execute never faces that choice: its planner commits to a
step decomposition once, before any tool runs, and the plans it produced
grouped the counting into a single step. The axis usually described as
Plan-then-Execute's weakness, its rigidity, is what protected it; flexibility is
only an asset for a model that can exercise it.

**The success criterion is weaker than it looks, and the one ReAct success
shows it.** `t2-react-21` is the single ReAct run that solved the two-hop task,
and it got the first hop's arithmetic wrong: "An hour with the most ERROR
lines: 14:00 (with 5 ERROR lines)" — there are 6. It scored O because `judge()`
tests whether `upstream timeout` appears in the answer, so it checks hop 2 and
never checks hop 1. The run reached the right final string through a miscount
that happened not to change which hour won. I am not loosening or tightening
the criterion after seeing results, which is the rule I set in `TASK2.md`, so
the row stands as O; but "6 of 6" for plan_exec and "1 of 6" for ReAct are
counts of answers containing the expected string, not of fully correct
reasoning. A criterion that validated both hops — requiring the hour *and* the
message — would have been the better design, and picking one string per task is
what made it easy to miss.

**Which harness is more expensive is not a property of the harness.** On
nemotron, plan_exec cost 6× ReAct's tokens (43,558 vs 7,272) and 11 iterations
against 3. On gemini-3.5-flash-lite the ordering reverses on t1 (13,943 vs
14,874). The same two harnesses, the same task, the same tools — and the token
ranking flips with the model, because ReAct's cost depends on a strategy the
model picks at runtime while plan_exec's is fixed by the plan's length. Any
claim of the form "Plan-then-Execute costs more" is a claim about a
model–harness pair, not about the harness.

**Axis 1 got its test and lost it to axis 3.** t2 ran to completion on
gemini-3.5-flash-lite — react 3, plan_exec 3 — and blindness cost the planner
nothing: plan_exec went 3–3 on the two-hop task, the same as on the one-hop
task. So the designed difference did not show up as a success difference. But
that result cannot be credited to axis 1, because ReAct lost t2 the same way it
lost t1: `MAX_STEPS reached: incomplete` after per-hour enumeration, in both
failing runs. When one harness dies of the iteration cap on both tasks, the
second task stops discriminating between "the planner was not handicapped" and
"the comparison never got far enough to find out". Isolating axis 1 needs a
ReAct run that fails t2 *on the dependency* rather than on the step budget —
which means raising `max_steps` until the cap stops binding, and that is a
change to axis 3, so it would have to be its own experiment.

The nemotron replication of t2 would have helped here, and it is the block the
rate limit destroyed: plan_exec issues 11–13 model calls per run against
ReAct's 2–3, so the harness with the higher iteration count exhausted a shared
daily quota and took its own comparison down with it. Worth noting what that
does and does not cost: the runs the assignment asks for — the six starter runs
on `TASK.md` — are complete for both models, and the missing cell is the second
model's copy of a task I added myself.

## 4. Reproducing this

```bash
pip install openai            # anthropic instead, if ANTHROPIC_API_KEY is set

# PowerShell — key read from a file outside the repo, never echoed
$env:OPENAI_BASE_URL   = "https://generativelanguage.googleapis.com/v1beta/openai/"
$env:AGENT_MODEL       = "gemini-3.5-flash-lite"
$env:AGENT_MIN_INTERVAL= "6"
$env:PYTHONUTF8        = "1"
$env:OPENAI_API_KEY    = (Get-Content <path-to-key-file> -Raw).Trim()
python run_ab.py --runs 3 --task TASK.md
python run_ab.py --runs 3 --task TASK2.md
```

| Setting | Value |
|---|---|
| Provider | OpenAI-compatible; Google AI Studio endpoint above (OpenRouter `https://openrouter.ai/api/v1` for the nemotron rows) |
| Models | `gemini-3.5-flash-lite` (12 runs), `nvidia/nemotron-3.5-lightning:free` (13), `gemini-2.5-flash` (2 crash rows) |
| Temperature | not set — provider default (see the caveat below) |
| ReAct | `max_steps=8` |
| Plan-then-Execute | `max_replan=1`, `max_tool_rounds=3` |
| Tools | `read_file`, `count_pattern`, identical for both harnesses |
| Input | `app.log`, starter file, unchanged |
| Expected | t1 `14:00`; t2 `upstream timeout` |
| Judging | `expected.lower() in answer.lower()`, fixed before running |
| Pinned models | exact ids only — no `-latest` or `-preview` alias, which would resolve to something else on a later reproduction |
| Python / SDK | 3.14.3 / `openai` 3.8.0 |
| API key | environment variable only |

**Transport accommodation, not a harness variable.** `tools_shared.send()` paces
calls (`AGENT_MIN_INTERVAL` seconds apart) and retries a 429, honouring the
provider's `retry in Ns` hint. Without it plan_exec cannot finish a run on a
free tier at all, so the A/B would measure which harness reached the quota
first. It sits in the module both harnesses share, so it applies to each
identically, and `meter.add()` still runs only on a call that returned — a
retry costs wall-clock time and moves no measured metric. Reactive retry alone
was not enough: the retry landed in the same full window and the provider
answered "retry in 53.8s" twice in a row, which is why pacing was added.

**What reproduces and what does not.** The direction reproduces: on
gemini-3.5-flash-lite plan_exec beat ReAct on success in both tasks, and the
`MAX_STEPS`-after-per-hour-enumeration failure appeared on **both** models
(5 times on gemini, once on nemotron at `t1-react-02`, which stopped at 13:00).
Individual runs do not: `temperature` is left at the provider default here, and
week-01 showed that pinning it to 0 did not stabilise the tool path either.
Under ReAct the same model on the same task took 2 iterations on one run and
8 on the next, which is the whole finding — so a reproduction should expect the
trend, not the row.

## 5. What I discarded

- **My pre-registered prediction** (ReAct wins success, loses tokens). Wrong on
  both counts for gemini-3.5-flash-lite. Left in the commit history.
- **`gemini-2.5-flash`** as the model, after two runs: its free tier is 20
  requests *per day*. Its two crash rows stay in `results.csv`.
- **Reactive-retry-only** for rate limits, after watching it burn two 55-second
  waits in a row against a per-minute window.
- **A single-string success criterion**, in hindsight. Keeping it was the
  right call once runs existed (changing it after seeing results is exactly
  what `TASK2.md` forbids), but `expected:` should have covered both hops.
- **A WARN-count version of t2** ("how many WARN lines in that hour"): the
  answer is 0, and `judge()`'s substring match would pass almost any answer
  containing a zero. The criterion has to be a string the wrong answer cannot
  accidentally contain.

## 6. On using an LLM for this

Written with Claude Code, which the course permits. The hypotheses that turned
out wrong are in the commit history in the order I held them, the runs are in
`logs/` unedited, and the two crashed blocks were kept rather than re-run into
silence. Two decisions came from reading transcripts rather than from planning:
the pacing change came from the "retry in 53.8s" pair, and the axis-3 reading
in §3 came from noticing that every ReAct failure had exactly seven
`count_pattern` calls while the one success had none.
