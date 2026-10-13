Week 05: Generate REPORT.md from your actual GroqCloud results
===============================================================

1. Download the attached week05-report-finalizer.zip into ~/Downloads.
2. From the Week-05 folder on your Mac, run:

   unzip -o ~/Downloads/week05-report-finalizer.zip
   python3 build_week05_report.py

This reads the existing results.csv, scenarios.json, and logs/*.txt. It does NOT
need any API key, does not call a model, and does not rerun any experiments.
It refuses to generate a report unless 24 genuine Groq LLM episodes exist.
It backs up your earlier REPORT.md to REPORT_before_groq.md.bak.

The report includes your exact result metrics, a 24-episode table, attempted
violations, server refusals, mean turns, same-turn recovery counts, a concrete
violation example from the logs, and an accurate Week04 vs Week05 comparison.

3. Review REPORT.md, especially the violation example and Week04 caveat.
4. From repo root:
   cd ../../..
   python3 scripts/check_week05.py submissions/25522018/week-05
   git status --short

IMPORTANT: The user-facing chat currently has only summary figures; this
script obtains detailed numbers from the local real results.csv. It does not
fabricate any attempted-violation/refusal metrics or per-episode rows.
