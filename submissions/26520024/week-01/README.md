# Week 01: GPU runtime notes

Status: implementation prepared; live model execution and observations pending.
Offline test logs do not meet the requirement for a full real agent run.

## Task and tools

Read the example GPU runtimes in notes.txt, calculate the total hours, and append
a summary to outputs/gpu-summary.txt. The data is synthetic and explicitly
labeled as such. The independently calculated reference total is 4.5 hours.

- read_file reads a complete .txt file inside this submission directory.
- calculator evaluates bounded arithmetic without eval.
- write_note appends text to .txt files under outputs/.

The loop offers the schemas in TOOLS and executes a model-requested function
through TOOLS_IMPL. It returns each result to the model before asking for the
next action. It does not prescribe read -> calculate -> write in Python.
Both reading and writing resolve symlinks before checking their allowed paths.
This is a scoped local file tool, not an OS-level sandbox.

## Environment

Use Python 3.10 or newer (tested with 3.12.10). Run these commands from
submissions/26520024/week-01/. Keep the environment outside the submission
directory because the course checker scans every file in the directory.

    python -m venv /tmp/week01-26520024-venv
    source /tmp/week01-26520024-venv/bin/activate
    python -m pip install -r requirements.txt

ANTHROPIC_API_KEY must already be configured in the shell's environment.
No key is included or loaded from a file. Without it, no API request is made.
The tested local environment currently has no API key.

The model defaults to the course starter's claude-sonnet-4-5 alias; availability
has not been tested. Use AGENT_MODEL or --model to choose a model your account
can access. Record the same model for both comparison runs.

Defaults: 3 tools, 8 model requests maximum, 1024 output tokens per request,
60-second SDK timeout, and no SDK retries. --max-steps accepts 1 through 32.
Each run logs the model, SDK version, full tool schemas, limits, goal, response
stop reasons, assistant text, tool arguments, results, and final answer.
Tool errors are returned to the model; incomplete replies and step exhaustion
exit with a failure status.

## Live comparison

Use the same default goal and model for both runs. The only intended difference
is whether write_note is offered. The baseline is not allowed to dispatch it.
These commands create new log files without replacing older attempts.

    mkdir -p logs
    set -o pipefail
    python first_agent.py --tools 2 2>&1 | tee "$(mktemp logs/model-2-XXXXXX.log)"
    python first_agent.py --tools 3 2>&1 | tee "$(mktemp logs/model-3-XXXXXX.log)"

The first run should be assessed for its arithmetic and how it handles the
unavailable write capability. The second should be assessed for its actual
tool choices, arithmetic, and saved text. These are evaluation criteria, not
results already observed. A model may choose a different sequence or fail.

Inspect outputs/gpu-summary.txt after a successful three-tool run. Repeated
runs append new notes; an existing file alone is not proof that a later run
saved anything. Do not delete prior logs or overwrite failed attempts.
Record the real sequences, errors, and outcomes in OBSERVATIONS.md, then
commit the raw logs, output, and observations. Do not squash the history.

## Local verification

The offline tests use real tool functions and scripted SDK response objects.
They verify file restrictions, arithmetic, feedback, errors, and termination;
they do not measure a model's tool-selection behavior.

    python test_first_agent.py
    python ../../../scripts/check_week01.py .

From the repository root, check ownership too:

    python scripts/check_pr_paths.py LeeUichann roster/26520024.md submissions/26520024/week-01/first_agent.py

The structural checker counts any file in logs/. Consequently, it can pass
with only offline logs; that does not make this assignment complete.

## Submission

Branch: week-01-26520024. PR title: [week-01] 26520024.
The branch currently includes the roster commit because registration PR #38
was not merged when this work began. It leaves the registration branch intact.
The final assignment needs at least one genuine full agent run; preserve the
two comparison runs as well, so observations have evidence.

Read PROCESS.md for assistant involvement, design decisions, and check results.
Review the code and tool-description rationale before submitting this work.
