# Week 04 run instructions

This folder is intended to become `submissions/25522018/week-04/`.

## Git Bash / macOS / Linux

```bash
export OPENROUTER_API_KEY='YOUR_KEY'
export OPENROUTER_MODEL='nvidia/nemotron-3.5-lightning:free'
python run_week04.py
```

## Windows PowerShell

```powershell
$env:OPENROUTER_API_KEY='YOUR_KEY'
$env:OPENROUTER_MODEL='nvidia/nemotron-3.5-lightning:free'
python run_week04.py
```

The script runs 4 scenarios × 3 conditions × 3 repeats = 36 episodes. It creates 9 log files, appends every episode (including crashes) to `results.csv`, and regenerates `REPORT.md` from the measured results. Re-running resumes completed `(run, scenario)` pairs.

After the run, from the repository root:

```bash
python scripts/check_week04.py submissions/25522018/week-04
```

Do not commit your API key.
