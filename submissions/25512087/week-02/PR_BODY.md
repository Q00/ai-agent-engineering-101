## Summary

- preserves the original ReAct and Plan-then-Execute baseline implementation, logs, and `results.csv`
- adds a Plan-then-Execute variant whose planner is limited to at most three steps
- adds a separate no-replan variant (`max_replan=0`)
- adds an experiment runner that records success, tokens, iterations, interventions, `OFF_PLAN`, and replans in a separate result file
- adds unit tests proving each variant changes only its intended harness axis

## Verification

- `python3 -m unittest -v test_experiment_variants.py`
- `python3 -m py_compile harness_plan_execute.py run_plan_variants.py test_experiment_variants.py`
- `python3 scripts/check_week02.py submissions/25512087/week-02`

## Experiment status

The previous baseline measurements remain preserved. The new three-run-per-variant batch still requires `OPENAI_API_KEY` in the execution shell; no credential is stored in this submission and no unexecuted measurements are claimed.
