# Process

## Before measurements, 2026-09-08

- The student asked the coding assistant to implement and execute week 02
  using Codex as the model, and explicitly requested no PR yet. No push or
  PR will be performed. The assistant is authoring code and analysis; these
  are not presented as unaided student work.
- Read the course README, both harnesses, shared tools, runner, and checker.
  Copied the unmodified starter and committed TASK.md before any live runs
  in ce4e80e. The expected answer and app.log will remain unchanged.
- Adapted the week-01 Codex approach after inspecting its committed adapter
  and official documentation: https://learn.chatgpt.com/docs/non-interactive-mode
- Preserve the starter control flows, prompts, and shared tool schemas.
  Add a shared Codex model adapter; keep tool execution in Python, disable
  internal Codex tools, and reject internal-action events. Each model call
  gets a fresh empty scratch directory plus explicit conversation history.
  The planner's text is NOT constrained to a valid JSON plan by the output
  schema: its JSON-list parsing failure remains an observable failure mode.
- The benchmark tools now permit only the provided app.log, preventing access
  to TASK.md, reports, old logs, secrets, and other repository files. The
  same restriction applies to both harnesses. Input contents and tool schemas
  are unchanged. The read_file 4000-character guard remains.
- Corrected ReAct's dormant human-approval counter to count approvals as well
  as denials. With the unchanged read-only tools and empty IRREVERSIBLE set,
  neither harness requests human intervention in the benchmark.
- The initial sandbox read attempts failed because bwrap could not create a
  namespace. Read access was retried with tool approval. A lookup under
  /nas/home/uichan/.codex found no directory; actual CLI is already on PATH.
- A preliminary version-only check used pyenv Python 3.12.10. The student
  then specified conda base only, without modifying any existing environment.
  All implementation tests and model experiments will instead use the existing
  /home/uichan/miniconda3/bin/python (base, Python 3.8.19). No package installs,
  upgrades, conda configuration changes, or new environments are required.

## Fixed experiment design

- Provider: authenticated Codex CLI 0.153.0, existing ChatGPT login.
- Model: gpt-6-astra; reasoning effort: low; timeout: 180 s per model call.
- Same task, unmodified input, two schemas, tool implementations, transport
  envelope, and decoding settings for both variants. No reference answer is
  included in model requests. Sampling defaults are left to the CLI; no seed
  or temperature is configured.
- ReAct: full conversation, maximum 8 model calls, stop on no tool calls.
- Plan-execute: separate planner/executor histories, maximum 1 replan,
  starter max_tool_rounds=3, and a final-answer phase. The starter's guard
  services one pending batch at the cap, so it can execute 4 tool batches
  within a step before marking OFF_PLAN. This is retained, not silently fixed.
- Three consecutive ReAct runs, then three plan-execute runs, matching the
  starter order. No retries or removal of failed rows. No pilot model runs.
- Success uses the unchanged starter case-insensitive substring criterion.
  Tokens sum each completed CLI turn's input_tokens + output_tokens, including
  cached input in the input total; this is not a monetary billing estimate.
  Iterations count attempted model calls, including planning/final synthesis.
  A failed call with incomplete usage leaves CSV tokens blank, not invented.
- Small fixed-order sample: no claim of statistical significance, universal
  superiority, or isolated causal effects of a single axis.
