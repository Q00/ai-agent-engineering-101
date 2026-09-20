## What I built

Added write_note to a Python agent that reads sample GPU runtimes, calculates
their total, and saves a report. The decision model is authenticated Codex CLI
(gpt-6-astra, low reasoning); the Python loop executes the three declared tools
and feeds results back. Codex's built-in tool actions are disabled and checked.

## What I tried and discarded

Started from the Anthropic example, then switched to the student's existing
Codex login at the student's request because no Anthropic API key was available.
Replaced the ambiguous starter memo with explicitly synthetic runtime values.
Implementation and execution used a coding assistant; PROCESS.md records the
decisions, failed setup/push attempts, and actual runs. All earlier commits and
logs are retained.

The two-tool live run called read_file -> calculator, returned 4.5 hours, and
reported that saving was unavailable. The three-tool run called read_file ->
calculator -> write_note and saved the inputs and total. Both used the same
goal, model, and loop settings. One run per mode is not a reliability estimate
or a test of description wording in isolation.

## How to run

Requires Python 3.10+ and an authenticated Codex CLI; tested with Python 3.12.10
and Codex CLI 0.153.0 using ChatGPT authentication. No Python SDK or Anthropic
API key is required.

    cd submissions/26520024/week-01
    python first_agent.py --tools 2
    python first_agent.py --tools 3

README.md documents the full settings and commands that preserve new logs.
OBSERVATIONS.md links both genuine run logs and the saved output.
Local verification: 16 offline tests, the course structural check, and a
comparison of the actual logs and saved output passed.

## Checklist

- [x] The week-01 structural check passes locally.
- [x] Genuine model run logs and the generated report are committed.
- [x] No API keys are included.
- [x] History is not squashed.
