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

## Instrumentation and offline checks

- Extended the runner to capture all stdout/stderr, not only the starter's
  log callback, so actual CLI events and usage remain in each run's log.
  Source hashes, versions, original prompts, and full final answers are saved.
  Existing logs are exclusively created and never overwritten. The runner
  owns each Meter so a crash retains attempted calls and partial usage.
- Added 17 offline tests covering file isolation, schemas, raw usage, failed
  calls, ReAct termination, planning/replanning, approval counting, error
  observations, and crash-log preservation. All passed in existing conda base
  Python 3.8.19; see logs/offline-tests-01.log. Mock outputs exist only in
  temporary test directories, never in experimental results.csv.
- Existing Codex authentication was checked without printing credentials:
  Codex CLI 0.153.0, ChatGPT login, reasoning low. No live pilot was used.

## Live measurements and interpretation

- Ran `AGENT_PROVIDER=codex AGENT_MODEL=gpt-6-astra
  /home/uichan/miniconda3/bin/python run_ab.py --runs 3` in the existing base
  environment. The fixed-order six-run batch completed with exit status 0.
- Runs 1-3 (ReAct): all correct, 2 model calls each, tokens 19368/19374/19376.
  Runs 4-6 (plan-execute): all correct, 6 calls each, tokens 60011/60037/61572.
  All runs made one read_file call and no count_pattern calls. Both variants
  used zero human interventions; plan-execute needed no replans. No model
  failures, retries, pilot runs, or discarded measurements occurred.
- Actual model requests and raw JSONL usage are in logs/react-01.txt through
  react-03.txt and logs/plan_exec-04.txt through plan_exec-06.txt. Nonfatal
  CLI state-db discrepancy warnings were retained verbatim. No Codex internal
  tool actions were observed. Each run terminated with Answer: 14:00.
- The independent base-Python validator checked all 24 completed CLI usage
  events, input/source hashes, shared settings/schemas, and CSV verdicts.
  Its deterministic input count confirms 14:00 has 6 ERROR lines. Validation
  passed; see logs/live-validation-01.log. Original inputs and experimental
  Python files have not been changed after measurements.
- Committed the genuine batch and validation separately in fdaf3fa, before
  writing REPORT.md. The report explains the measured context/termination
  overhead and explicitly limits claims about errors, human intervention,
  count_pattern use, sample size, cache effects, and single-axis causality.
- The course ownership checker uses Python 3.10-style annotations. For the
  base-Python 3.8 check, execute its unmodified source with postponed annotation
  evaluation (`__future__.annotations.compiler_flag`). No conda environment
  or shared course script is modified for compatibility.

## Final local verification

- Official scripts/check_week02.py passed in conda base; see
  logs/structural-check-01.log. The unchanged ownership checker passed for
  LeeUichann with postponed annotations; see logs/ownership-check-01.log.
- All seven submitted Python modules passed base-Python py_compile; git
  diff --check found no whitespace errors. The 17 offline tests and genuine
  raw-event validation passed. No Python/conda packages were installed.
- All work is restricted to submissions/26520024/week-02/ on branch
  week-02-26520024. Existing environments and the professor's starter/checker
  files are unchanged. Work and evidence are committed locally without
  rewriting history. No push or PR was performed, as requested.
