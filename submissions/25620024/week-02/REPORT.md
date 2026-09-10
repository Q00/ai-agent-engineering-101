# Week 02 — Harness A/B: ReAct vs Plan-then-Execute

Task, tools and model held constant; only the harness varies. Task and success
criterion are in `TASK.md`, committed before any run (`cbbffcf`).

## How to reproduce

```bash
cd submissions/25620024/week-02
export ANTHROPIC_API_KEY=...            # not in this repo
export AGENT_MODEL=claude-haiku-4-5

PLAN_EARLY_EXIT=0 python run_ab.py --runs 3 --tag "model=claude-haiku-4-5 baseline"
PLAN_EARLY_EXIT=1 python run_ab.py --runs 3 --only plan_exec \
    --tag "model=claude-haiku-4-5 variant=axis3-early-exit"
```

| Setting | Value |
|---|---|
| Provider | Anthropic (`tools_shared.py` selects it when `ANTHROPIC_API_KEY` is set) |
| Model | `claude-haiku-4-5`, `max_tokens=1024`, no thinking, temperature default |
| Tools | `read_file(path)` — first 4000 chars; `count_pattern(path, pattern)` — count lines matching a Python regex. Identical for both harnesses, defined once in `tools_shared.py`. |
| Input | `app.log`, 60 lines, 19 ERROR lines spanning hours 09–17. Ground truth 14:00 (6 ERROR lines). |
| Metrics | `Meter` in `tools_shared.py`: one iteration = one model call; tokens = input + output summed over calls; interventions = human approvals/denials. |
| Judge | `run_ab.py` — success iff the `expected:` string appears in the final answer. |

Rows 1–12 in `results.csv` are an earlier block on OpenRouter
`nvidia/nemotron-3.5-lightning:free`; they are kept because failed runs are
data, and they are reproducible by unsetting `ANTHROPIC_API_KEY` and setting
`OPENAI_BASE_URL` / `OPENAI_API_KEY` / `AGENT_MODEL` per the week-02 README.
The numbers below use the Claude block (rows 13–21) so that one model is held
constant across the comparison.

## 1. Variant definition

Three harness configurations, differing on two of the five axes.

| Axis | ReAct (`harness_react.py`) | Plan-then-Execute baseline (`PLAN_EARLY_EXIT=0`) | Plan-then-Execute variant (`PLAN_EARLY_EXIT=1`) |
|---|---|---|---|
| 1 · Context management | One conversation, full history on every call | Two conversations: planner (no tools) and executor. Each planned step is appended to the executor's history as a new user turn, so the transcript grows monotonically | same as baseline |
| 2 · Tool granularity | identical — both import `TOOL_SPECS` / `TOOLS_IMPL` from `tools_shared.py` | identical | identical |
| 3 · **Termination condition** | The model decides: a reply with no tool call ends the run. Cap `max_steps=8` | **The plan is exhausted**: the loop runs every planned step, then makes one more call for the final answer. Iterations have a floor of `len(plan) + 1` however early the answer appears | **The model decides**, as in ReAct: a step whose reply declares `Answer:` ends the run immediately (`ANSWER_RX` in `harness_plan_execute.py`) |
| 4 · Error recovery | Tool exceptions come back as Observations; the model sees the error text and retries | Same for tool errors. Additionally `OFF_PLAN` triggers one replan (`max_replan=1`); a step that exceeds `max_tool_rounds=3` is forced to `OFF_PLAN` | same as baseline |
| 5 · Human intervention | `IRREVERSIBLE` is empty (tools are read-only), so interventions are 0 by construction | no gate | no gate |

The variant is exactly one axis away from the baseline: same planner, same
executor, same system prompts, same tools, same `max_replan`, same
`max_tool_rounds`. The flag is read from the environment so both
configurations are reproducible from one file.

## 2. Measurements

From `results.csv`, rows 13–21. Wall times from `logs/`.

| run | harness | config | success | tokens | iters | interventions | time | note |
|---|---|---|---|---|---|---|---|---|
| 13 | react | baseline | O | 6,816 | 3 | 0 | 10.4s | |
| 14 | react | baseline | O | 6,953 | 3 | 0 | 10.2s | |
| 15 | react | baseline | O | 6,897 | 3 | 0 | 9.8s | |
| 16 | plan_exec | baseline | O | 396,779 | 55 | 0 | 87.6s | replans=0 |
| 17 | plan_exec | baseline | O | 382,170 | 57 | 0 | 99.7s | replans=1 |
| 18 | plan_exec | baseline | O | 527,331 | 57 | 0 | 91.2s | replans=1 |
| 19 | plan_exec | variant | X | 834 | 1 | 0 | 6.9s | plan parse failed |
| 20 | plan_exec | variant | O | 9,991 | 5 | 0 | 13.3s | early exit at step 1 |
| 21 | plan_exec | variant | O | 62,705 | 12 | 0 | 52.1s | replans=1, early exit at step 2 |

| group | success | mean tokens | mean iters | mean time |
|---|---|---|---|---|
| react, baseline | 3/3 | 6,888 | 3.0 | 10.1s |
| plan_exec, baseline | 3/3 | 435,426 | 56.3 | 92.8s |
| plan_exec, variant | 2/3 | 24,510 (36,348 over successes) | 6.0 | 24.1s |

Interventions are 0 in every run. Axis 5 was never exercised: the starter
tools are read-only, so `IRREVERSIBLE` is empty and no run had anything to
approve. That column measures nothing here and should not be read as
"Plan-then-Execute needs no supervision".

Earlier block for reference (rows 1–12, `nvidia/nemotron-3.5-lightning:free`):
react 4/6 O at 3,974–22,836 tokens; plan_exec 2/6 O at 2,876–72,505 tokens.
Rows 9–12 are `RateLimitError` crashes — the free tier's 50 requests/day were
spent by six baseline runs, so that model's variant configuration was never
measured. That is why the Claude block exists.

## 3. Interpretation

**Axis 3 moved tokens and iterations; nothing else came close.** The two
Plan-then-Execute configurations differ only in their termination condition,
and that one change took the mean from 435,426 tokens / 56.3 iterations to
24,510 / 6.0 — roughly 18x fewer tokens, and 40x on run 20 alone
(9,991 vs 435,426). The mechanism is visible in the logs rather than inferred.
In `logs/plan_exec-16.txt` the planner expands the task into one step per hour
from `'00:00 ERROR'` to `'23:00 ERROR'` — 24 steps for a log that only spans
09–17, so 15 of them count hours that cannot exist. The baseline execute loop
pays one model call per planned step and resends a growing transcript each
time, which is why 24 steps cost 55 iterations and ~400k tokens. The answer
was not 55 iterations' worth of work: in `logs/plan_exec-20.txt` the executor
gathers all nine hours inside step 1 and writes `Answer: 14:00`, and the
variant stops there, skipping five planned steps and the final-answer call.
The baseline had the same information available at the same point and kept
walking, because "the plan is exhausted" cannot notice that the task is done.
This is the axis, not the model: the same pattern is in the nemotron block,
where `logs/plan_exec-04.txt` shows `Answer: 14:00` at step 1 followed by five
more steps.

**The cost of that axis is reliability, and the honest reading is that the
variant did not pay it.** Success went 3/3 to 2/3, but run 19 failed in the
PLAN phase: the planner answered with prose plus a fenced JSON block, and
`parse_plan` returned `None` before the execute loop — the only place the
early exit exists — ever ran. The same failure appears in the nemotron
baseline at row 6, under `PLAN_EARLY_EXIT=0`. So it is a property of the
planner's free-form-JSON contract (axis 4: the plan is parsed once with no
retry) and it would have occurred identically in the baseline. Two runs is too
few to claim the change is reliability-neutral, but the evidence points at the
plan contract, not at the termination rule.

**ReAct won on every metric that was measured, for a reason that is about
context, not intelligence.** At 6,888 tokens and 3.0 iterations it is 63x
cheaper than the Plan-then-Execute baseline and 9x cheaper than the variant.
`read_file` returns all 60 lines inside the 4000-character guard, so the whole
input is in context after one call and the model can batch the nine
`count_pattern` calls into a single turn. Plan-then-Execute cannot exploit
that: it commits to a step list before reading anything, so it plans for 24
hypothetical hours instead of the nine that exist. Planning first is a bet
that the task shape is knowable in advance, and on this task it is not.

**The axis that produced wrong intermediate numbers was tool granularity, and
neither harness protected against it.** `count_pattern` takes a raw regex, and
in `logs/react-13.txt` the model used `'11:.*ERROR'`, which also matches the
minute field — `09:11:56` counts as hour 11. React reported 11:00 as 2 (actual
1) and 15:00 as 3 (actual 2). The final answer survived only because 14:00
leads by a wide margin. The successful variant run anchored its patterns with
the date prefix (`'2026-09-01 11:.*ERROR'`) and got all nine hours right — the
difference is which regex the model happened to write, not which harness ran
it. A tool that took an hour argument instead of a regex would have removed
the failure mode from both.

**Caveat.** Three runs per configuration, one task, one 60-line input. The
token ratios are large enough to survive that sample size; the 3/3 vs 2/3
success difference is not.
