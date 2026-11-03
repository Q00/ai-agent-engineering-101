"""R3 layer 4 meta-harness (shadow; no decision-path module imports this package).

judge.py  pure scoring function over ledger rows (the fixed judge)
lock.py   sha256 pins of judge.py / bounds.py / experiments/decide.py (lock.json); score_locked = verify + judge
bounds.py range lock for policy edits (whitelisted keys, base bounds, step <= 0.25 x range, policy.validate)
runs.py   policy versions on disk: <root>/v0001/{policy.json, meta.json, score.json, trace.json}
rollback.py  apply a proposal / roll back to a version on a live policy file (registers a version, atomic write)
"""
