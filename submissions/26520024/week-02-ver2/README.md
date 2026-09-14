# Week 02 ver2: efficient harness A/B

Second experiment for student 26520024. The original week-02 submission and
course starter remain unchanged. See REPORT.md for the one-page report,
PROCESS.md for provenance and pre-run decisions, and logs/ for raw evidence.

## Run in the existing conda base environment

No package installation or environment modification is needed. This experiment
uses Python 3.8.19 in base and the already authenticated Codex CLI 0.153.0.

```bash
conda activate base
cd /nas/home/uichan/ai-agent-engineering-101/submissions/26520024/week-02-ver2
export AGENT_PROVIDER=codex
export AGENT_MODEL=gpt-6-astra
python -m unittest -v test_ver2
python run_ab.py --runs 3
python validate_results.py
```

Codex must be on PATH (or set CODEX_BIN to its existing executable) and logged
in. Live runs consume that account's Codex allowance. No API key is required
for this backend. The optional SDK backend paths are inherited, not used in
these measurements. Run only one experiment process at a time. Re-execution
appends rows and exclusive per-run logs; it does not replace old evidence.

## Shared settings and tools

- gpt-6-astra, low reasoning, 180-second timeout per call. No seed/temperature
  override. Each call uses a fresh ephemeral CLI process with explicit history;
  local user config and internal Codex tools are disabled. Unexpected internal
  actions invalidate the run. The underlying backend is identical to week-02.
- Both variants use the original TOOL_SPECS from tools_shared.py:
  read_file(path: string) reads up to 4000 characters; count_pattern(path:
  string, pattern: string) counts matching lines using Python re. Only the
  unchanged app.log is accessible. No reference answer, old result, report,
  or TASK.md content is passed to the model.
- Model calls, not individual tool executions, are counted as iters. Tokens
  sum actual CLI input/output usage including cache input once and overhead;
  no token or billing estimates are substituted.
- ReAct: 8 total model calls, last 4 observation batches, concise adaptive
  actions, evidence-gated Answer: termination.
- Plan-execute: 8 total model calls including planner/repair, 1-to-3-step
  plan, strict 3 tool batches per step, 1 shared repair/replan allowance,
  all tool observations retained, early final without an extra synthesis call.
- Run order for --runs 3 is R/P, P/R, R/P. Failures remain in results.csv.
  The unchanged judge checks the expected substring; it is permissive.

## Verification

From the repository root, explicitly check this directory:

```bash
python scripts/check_week02.py submissions/26520024/week-02-ver2
```

The course checker is structural, not a correctness grade. Its original CI
path handling should not be assumed to select this nonstandard ver2 directory;
the explicit command above checks the intended files. No shared CI files
are modified. validate_results.py independently reconciles raw usage, input
hashes, settings, successful input observations, answers, and every CSV row.

All interpretation of improvement over week-02 is a historical comparison,
not a randomized baseline rerun or an isolated ablation of each change.
