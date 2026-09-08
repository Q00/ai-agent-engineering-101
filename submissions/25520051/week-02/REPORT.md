# Week 02 — Harness A/B: ReAct vs Plan-then-Execute

Provider: OpenAI-compatible API via OpenRouter (`OPENAI_BASE_URL=https://openrouter.ai/api/v1`).
Model: `nvidia/nemotron-3.5-lightning:free` (free tier).
Tools: `read_file(path)`, `count_pattern(path, pattern)` — both defined once in `tools_shared.py` and imported by both harnesses.
Task: "In app.log, which hour (HH:00) has the most ERROR lines?" (`TASK.md`), success = answer contains `14:00`.
To reproduce: `export` the three variables above (or set `ANTHROPIC_API_KEY` for the Anthropic path), then `python run_ab.py --runs 3` from `submissions/25520051/week-02/`.

## 1. Variant definition

Same model, same tools, same task for both harnesses (`tools_shared.py` is the only place either one talks to the model or the filesystem). They differ on three of the five axes from the lecture:

- **Termination condition.** ReAct terminates when the model itself emits a reply with no tool call (or after `max_steps=8`) — the model decides it is done. Plan-then-Execute terminates when every step of a plan fixed in advance has been executed in order (or a replan budget of 1 is exhausted) — the *plan length* decides how many model calls happen, not the model's judgment of progress.
- **Context management.** ReAct keeps one running conversation (`Chat`) that accumulates every Thought/Action/Observation in place. Plan-then-Execute splits context into two conversations: a planner call with no tools that produces the whole plan up front, and a separate executor conversation that is re-prompted once per plan step ("Execute step N: …") regardless of whether the model already has enough information to answer.
- **Error recovery.** ReAct folds a tool error back into the same loop as an Observation and lets the model route around it on the next step. Plan-then-Execute treats an unparseable planner reply, or a step the executor calls `OFF_PLAN:` on, as a distinct failure mode with its own path (parse failure ends the run immediately; `OFF_PLAN` triggers one replan call to the planner).

Tool granularity and the human-intervention point are held equal: both harnesses share the same two tools with no batching difference, and `IRREVERSIBLE` is empty in the starter tools so both had 0 interventions on every run.

## 2. Measurements

From `results.csv` (6 runs, 3 per harness):

| run | harness | success | tokens | iters | interventions | note |
|---|---|---|---|---|---|---|
| 1 | react | O | 3633 | 2 | 0 | |
| 2 | react | O | 3847 | 2 | 0 | |
| 3 | react | O | 3881 | 2 | 0 | |
| 4 | plan_exec | O | 51371 | 12 | 0 | replans=0 |
| 5 | plan_exec | X | 744 | 1 | 0 | replans=0 |
| 6 | plan_exec | O | 40963 | 11 | 0 | replans=0 |

| harness | success rate | avg tokens | avg iters |
|---|---|---|---|
| react | 3/3 | 3787 | 2.0 |
| plan_exec | 2/3 | 31026 | 8.0 |

## 3. Interpretation

ReAct won on every metric that matters for a fixed-tool, single-question task: 3/3 success at roughly 3800 tokens and 2 model calls per run, because the *termination-condition* axis lets the model stop the instant it has read the file and computed the answer. Plan-then-Execute's fixed step list is what drove both of its problems. First, cost: in runs 4 and 6 the planner produced a reasonable 5-6 step plan (`read app.log`, `filter ERROR lines`, `extract hour`, …), but the executor had already worked out `14:00` by step 1-2 and then just re-answered `Answer: 14:00` at every remaining step because the harness re-prompts once per plan step regardless of whether that step still needs work — 11-12 iterations and 40-51k tokens to re-confirm an answer it already had, a 10x-13x cost over ReAct for the same correct answer. Second, fragility: run 5 failed outright because the *context-management* axis requires the planner to return bare JSON with nothing else, and this free-tier model sometimes emits a "thinking process" preamble or an empty string instead (`logs/plan_exec-05.txt`) — `parse_plan` has no recovery path for that, so the run ends in one iteration with no chance to try again, unlike ReAct's error-recovery axis which would have just fed a bad tool call back as an Observation and continued. So for this task the fixed plan structure was pure overhead: it neither improved accuracy nor gave useful error recovery, it just added format-compliance risk and per-step re-prompting cost that ReAct's model-decides-when-done loop doesn't pay.
