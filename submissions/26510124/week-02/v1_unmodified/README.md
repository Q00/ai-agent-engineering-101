# v1 — starter harnesses, unmodified

Byte-identical copies of `weeks/week-02/starter/harness_react.py` and
`harness_plan_execute.py`. Rows 1–24 of `../results.csv` (and `../logs/*-01..24.txt`)
were produced with these. Kept so the v1 baseline stays reproducible after the
top-level harnesses became v2.

To rerun v1: copy the two files up one level (or `git checkout 3e33db1 -- harness_*.py`)
and run `python run_ab.py --runs 3 --tag v1`.
