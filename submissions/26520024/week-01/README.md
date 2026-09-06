# Week 01: GPU runtime notes

Status: both real Codex comparison runs completed successfully. Their original
logs, the saved report, and the comparison are included in this submission.

## Task and tools

Read the example GPU runtimes in notes.txt, calculate the total hours, and append
a summary to outputs/gpu-summary.txt. The data is synthetic and explicitly
labeled as such. The independently calculated reference total is 4.5 hours.

- read_file reads a complete .txt file inside this submission directory.
- calculator evaluates bounded arithmetic without eval.
- write_note appends text to .txt files under outputs/.

The loop offers the schemas in TOOLS and asks codex_backend.py for one model
decision. The backend calls the logged-in Codex CLI with a JSON output schema.
Python executes the requested function through TOOLS_IMPL and returns its
observation before asking for another decision. The model selects the tool
and arguments; Python does not prescribe read -> calculate -> write.
Both reading and writing resolve symlinks before checking their allowed paths.
This is a scoped local file tool, not an OS-level sandbox.

## Environment

Use Python 3.10 or newer (tested with 3.12.10) and Codex CLI (tested with 0.153.0).
Python uses only the standard library; no Anthropic or OpenAI SDK is required.
Run the commands below from submissions/26520024/week-01/.

Codex must be available on PATH and authenticated:

    codex login status

If it is not logged in, run codex login and finish the browser login. This
implementation reused the author's existing ChatGPT login; it does not require
ANTHROPIC_API_KEY. See the official [authentication documentation](https://learn.chatgpt.com/docs/auth).
The same Codex account limits and model access apply to these runs.

If the IDE bundles Codex but it is not on PATH, set CODEX_BIN to that executable.
The executable used on this server was:

    /home/uichan/.vscode-server/extensions/openai.chatgpt-26.901.22334-linux-x64/bin/linux-x86_64/codex

The default model is gpt-6-astra, matching the author's configured Codex model.
Use AGENT_MODEL or --model to override it, keeping the model identical between
comparison runs. Reasoning effort is fixed to low in codex_backend.py.

Defaults: 3 tools, 8 decisions maximum, and a 120-second subprocess timeout per
decision. --max-steps accepts 1 through 32. Each model call starts a fresh
ephemeral Codex session and receives the accumulated Python history.

The backend uses an empty temporary working directory, a read-only sandbox,
and explicit model settings. It does not load user configuration. Shell,
delegation, apps, plugins, hooks, browser, computer, image, and sleep features
are disabled, and web search is disabled. Codex's standard model instructions
still apply. Returned events are checked to reject internal tool actions.
The model sees input file contents only when Python supplies a read_file result.
See the official [non-interactive mode documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

Each run logs its model, CLI version, authentication method (not credentials),
full tool schemas, instructions, limits, goal, raw CLI events and stderr,
decisions, tool arguments, observations, and final answer. Tool errors are
returned to the model. Invalid decisions and step exhaustion fail the run.

## Live comparison

Use the same default goal and model for both runs. The only intended difference
is whether write_note is offered. The baseline is not allowed to dispatch it.
These commands create new log files without replacing older attempts.

    mkdir -p logs
    set -o pipefail
    python first_agent.py --tools 2 2>&1 | tee "$(mktemp logs/codex-model-2-XXXXXX.log)"
    python first_agent.py --tools 3 2>&1 | tee "$(mktemp logs/codex-model-3-XXXXXX.log)"

On this server, Python 3.12.10 can also be selected with
PYENV_VERSION=3.12.10 python in place of python.

The committed two-tool run read the input and calculated 4.5 hours, then
explained that saving was unavailable. The three-tool run additionally called
write_note and saved the input values and total. See OBSERVATIONS.md for the
actual sequences, log filenames, and limitations of this single comparison.

Inspect outputs/gpu-summary.txt after a successful three-tool run. Repeated
runs append new notes; an existing file alone is not proof that a later run
saved anything. Do not delete prior logs or overwrite failed attempts.
For additional runs, record the real sequences, errors, and outcomes in
OBSERVATIONS.md, then commit the raw logs, output, and observations.
Do not squash the history.

## Local verification

The offline tests use real tool functions and explicitly scripted decisions.
They verify file restrictions, arithmetic, feedback, errors, and termination;
they do not measure a model's tool-selection behavior.

    python test_first_agent.py
    python ../../../scripts/check_week01.py .

From the repository root, check ownership too:

    python scripts/check_pr_paths.py LeeUichann roster/26520024.md submissions/26520024/week-01/first_agent.py

Historical offline-only logs are retained as process evidence. The genuine
model runs are logs/codex-model-2-01.log and logs/codex-model-3-01.log.

## Submission

Branch: week-01-26520024. PR title: [week-01] 26520024.
The branch currently includes the roster commit because registration PR #38
was not merged when this work began. It leaves the registration branch intact.
The two full model runs and outputs/gpu-summary.txt are committed. GitHub push
from this execution environment previously failed because GitHub credentials
were unavailable; authentication to Codex is separate from Git authentication.

Read PROCESS.md for assistant involvement, design decisions, and check results.
Review the code and tool-description rationale before submitting this work.
