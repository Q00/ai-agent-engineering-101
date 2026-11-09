# Week 02: reproducible harness comparison

Student: 26520024. Source: weeks/week-02/starter. See REPORT.md for measured
results and PROCESS.md for assistant involvement and chronological decisions.

## Environment and execution

Use the existing conda base environment (Python 3.8.19 tested). No pip/conda
installation, environment creation, or environment modification is needed.
The default backend uses the already authenticated Codex CLI, version 0.153.0.
Its existing ChatGPT login is reused; no API key or Python SDK is required.

```bash
conda activate base
cd /nas/home/uichan/ai-agent-engineering-101/submissions/26520024/week-02
export AGENT_PROVIDER=codex
export AGENT_MODEL=gpt-6-astra
python -m unittest -v test_week02
python run_ab.py --runs 3
```

Codex must be on PATH; CODEX_BIN can point to an existing executable.
The runner appends results and creates new, exclusively opened per-run logs.
It does not overwrite old runs. After an interruption, an incomplete log is
retained and its run number is not reused. Run only one runner at a time.
Read-only offline tests make no model calls. Live runs consume the existing
account's Codex allowance. No push or PR is part of these commands.

## Fixed model and tools

- Backend: Codex CLI with ChatGPT authentication; model gpt-6-astra; reasoning
  low; ephemeral fresh process per model call, 180-second timeout.
- No explicit temperature or seed; user config ignored, web search and internal
  Codex tools disabled. Internal-action events invalidate a run.
- The Python harness passes the entire relevant history to each fresh process.
  The empty scratch directory contains only the output schema; TASK.md and
  previous run logs are never supplied to the model.
- Tool schemas are the original TOOL_SPECS in tools_shared.py and are recorded
  in each run log: read_file(path: string), count_pattern(path: string,
  pattern: string). Only the original app.log is accessible. read_file returns
  its first 4000 characters; count_pattern counts matching lines with Python re.
- The JSON transport supports text plus zero or more host tool requests. It
  does not enforce the planner's inner JSON-list format or the final answer.
- The optional Anthropic/OpenAI SDK paths from the starter remain available
  with explicit AGENT_PROVIDER; they were not used or tested live here.

## Measurement and verification

Each run has [config], [run], [model-input], raw [codex-event]/stderr, harness
steps/tool observations, [answer-json], [judge], and [meter] records. Config
includes Python/CLI versions, source commit, and source/input SHA-256 hashes.
tokens = sum(input_tokens + output_tokens) from actual completed CLI events,
including cached input once, with no estimated tokens. This includes CLI
overhead and is not an API price estimate. iters counts attempted model calls,
including planner and final-answer calls. Unknown token usage is blank in CSV.
The unchanged judge checks whether the expected text occurs in the final answer;
it is permissive and does not itself prove the reasoning is correct.

```bash
python validate_results.py
cd /nas/home/uichan/ai-agent-engineering-101
python scripts/check_week02.py submissions/26520024/week-02
```

The course checker verifies structure, not experiment validity. The additional
validator cross-checks all CSV rows against raw usage and answers, shared
settings/input hashes, and actual tool observations. Logs are never edited.

Official transport reference: https://learn.chatgpt.com/docs/non-interactive-mode
