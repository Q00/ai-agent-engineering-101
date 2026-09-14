# Week 02 ver2: process and pre-run design

## Scope and preservation (2026-09-14)

The student requested a second experiment with improved versions of both
starter harnesses, leaving week-02 unchanged. Work is in week-02-ver2 on local
branch week-02-ver2-26520024. The coding assistant implemented and executed
the experiment and wrote its interpretation; this is not unaided student work.
No push or PR update is authorized for this work.

- Recorded checksums of all original week-02 and course week-02 files before
  work. An already staged week-02/ARCHITECTURE.md is left staged and unchanged;
  ver2 commits explicitly exclude it.
- Copied the starter and committed its original TASK.md and app.log in
  aca38e6 before any live run. The actual starter filename is
  harness_plan_execute.py. No professor-owned starter files are edited.
- Reused the prior Codex backend, shared tool implementations/schemas, runner,
  and evidence validator in the new directory. The backend is unchanged.
- Verified the existing base interpreter: /home/uichan/miniconda3/bin/python,
  Python 3.8.19; CONDA_DEFAULT_ENV=base. No packages or environments are created,
  installed, upgraded, or otherwise changed.
- Existing Codex login check: CLI 0.153.0, ChatGPT authentication. No credential
  values are read or printed. All experiments use gpt-6-astra, reasoning low,
  and the same shared backend with its 180-second call timeout.

## Improvement hypotheses, fixed before live runs

1. ReAct rebuilds each request from the task and the most recent four tool
   observation batches. It omits accumulated assistant narration, requests
   concise responses, and allows independent calls in one model reply. No
   tools are pre-executed and no answer is hardcoded. A final requires a
   nonempty Answer: prefix, no pending calls, and successful tool evidence.
2. Plan-execute requests a minimal 1-to-3-step plan, then returns as soon as
   an Executor response solves the whole task. It removes the unconditional
   extra synthesis call and does not wait for redundant remaining plan steps.
   Each executor call receives the plan, current step, and all actual tool
   observations, but not prior narrative or repeated step acknowledgments.
3. Both have an 8-call total budget. Planning and repair count against the
   plan-execute budget. Plan-execute has a strict 3-tool-batch per-step cap,
   without the starter's extra batch after that cap, and a shared one-repair
   allowance for invalid initial plans or OFF_PLAN. ReAct adapts from error
   observations without a separate planner.
4. Human intervention is zero for these read-only tools. ReAct retains the
   approval hook and counts both approval and denial if explicitly configured.
   Plan-execute has no approval hook. We do not add write/delete tools.

Only shared run_tools instrumentation changes: it additionally returns the
exact tool results and explicit success/error flags to the observation memory.
Tool names, schemas, input data, actual tool functions, and model transport
remain the same. Python never calculates the benchmark answer in a harness;
the independent validator calculates reference counts only after the runs.

## Measurement protocol

- Run exactly three repetitions per variant in paired alternating order:
  react, plan_exec, plan_exec, react, react, plan_exec. This limits but does
  not eliminate order/cache effects. No pilot model runs or cherry-picking.
- Task and substring success criterion remain identical to the starter and
  week-02. Failed, malformed-plan, budget-exhausted, or crashed runs remain
  in results.csv and logs/. Further invocations append rather than overwrite.
- Preserve full prompts, raw CLI events/stderr, tool observations, UTC start,
  code/input hashes, Python/CLI versions, config, final answer, and Meter.
- Tokens are actual input_tokens + output_tokens, including cached input
  once. They include CLI overhead and are not API billing estimates. Iterations
  are attempted model calls. Unknown usage is blank, never estimated.
- Primary comparison: improved ReAct versus improved plan-execute in this
  new six-run batch. Prior week-02 measurements are historical context only,
  not a contemporaneous controlled ablation of the improvements.
- No runtime claims are made before measurements. Multiple harness changes
  are bundled, so improvements cannot be attributed to one axis in isolation.
- Deliver REPORT.md as one concise page with three sections: variant
  definition across five axes, full CSV measurement table, one interpretation
  paragraph. Supporting setup and process details stay in other documents.
