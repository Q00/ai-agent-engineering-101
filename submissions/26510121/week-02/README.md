# Week 02 — Harness A/B reproduction guide

This submission compares the ReAct and Plan-then-Execute harnesses on the
same log-analysis task. The task, expected answer, and controls were fixed in
`TASK.md` before the model runs. The six original runs, their logs, and the
interpretation are included in `results.csv`, `logs/`, and `REPORT.md`.

## Recorded environment

- Provider: OpenRouter through the OpenAI-compatible API
- Base URL: `https://openrouter.ai/api/v1`
- Model: `nvidia/nemotron-3.5-lightning:free`
- Python actually used for the recorded runs: 3.9.13 on Windows PowerShell
- OpenAI Python SDK actually used: 2.48.0
- Harness settings: ReAct `max_steps=8`; Plan-then-Execute `max_replan=1`
  and `max_tool_rounds=3`; no temperature or seed is set, so the provider
  defaults apply.

The model is a free service and its availability and responses may change.
This experiment therefore reports the recorded six-run sample rather than
claiming identical results on every rerun.

## Shared tool schemas

Both harnesses import the same implementation and schemas from
`tools_shared.py`; the tool set is intentionally held constant.

```text
read_file(path: string) -> string
  Read the first 4,000 characters of a text file in the working directory.

count_pattern(path: string, pattern: string) -> string
  Count lines in a working-directory text file that match a regular expression.
```

Both arguments are required. The implementations reject paths outside the
working directory. `Meter` records total input plus output tokens, model-call
iterations, and human interventions for both harnesses.

## Run from a fresh checkout

Use a new PowerShell window and start from this submission directory. The API
key is entered interactively and remains only in that terminal environment.
Do not put it in code, logs, Git, or an `.env` file.

```powershell
cd submissions/26510121/week-02
python -m pip install -r requirements.txt

$secret = Read-Host "OpenRouter API key" -AsSecureString
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new("", $secret).Password
Remove-Variable secret

$env:OPENAI_BASE_URL = "https://openrouter.ai/api/v1"
$env:AGENT_MODEL = "nvidia/nemotron-3.5-lightning:free"
$env:PYTHONIOENCODING = "utf-8"

python run_ab.py --runs 3
```

`run_ab.py` runs ReAct three times first, then Plan-then-Execute three times.
It writes one line per run to `results.csv` and one UTF-8 log per run under
`logs/`. It judges success solely by whether the final answer contains the
fixed expected value `14:00` from `TASK.md`.

## Reproducing a new six-run sample

`run_ab.py` appends to `results.csv` and does not replace existing logs. To
produce a clean new six-run comparison, use a fresh checkout or a copy of this
submission directory that does not contain `results.csv` or `logs/`. Do not
edit the original six logs or rows in this submission; they are the evidence
for the reported measurements.

## Verify structure

From the repository root, run:

```powershell
python scripts/check_week02.py submissions/26510121/week-02
```

This structural check verifies files, CSV shape, minimum run counts, logs, and
basic Python parsing. It does not re-run the paid/networked model experiment or
prove the interpretation in `REPORT.md`.
