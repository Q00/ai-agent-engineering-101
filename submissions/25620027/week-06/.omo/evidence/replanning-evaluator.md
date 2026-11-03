# Replanning evaluator regression evidence

Scope: `market_a2a/challenges.py` and `tests/test_challenge_replanning.py`. No model API calls, challenge-run log mutations, or commits performed.

Working directory: `submissions/25620027/week-06` in the Week 06 worktree.

Python binary: `/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python`.

## Reproduced defect

Invocation: the Python binary above with `-m pytest tests/test_challenge_replanning.py -q` before the evaluator change.

Observable: `1 failed, 7 passed`; `test_summary_accepts_released_failed_purchase_then_success` raised `HarnessError: Missing budget transitions` at the previous exact-count condition. The scenario contains two purchases and three settlements: lamp payment 120, failed chair checkout releasing its full 200 cap, then chair payment 200 under a new 250 cap. Total spent 320, budget 500, final reserved 0.

## Verified behavior

The evaluator replays emitted plans and settlements in order. Each plan covers exactly the pending IDs with unique reservations and affordable caps. Each settlement must match an active reservation, nondecreasing spent, exact cap release, outstanding reservation total, and fixed total budget. A positive payment must match that request's final receipt price and shop. Successful IDs cannot be purchased again. Final pending IDs and reservations must both be empty; final spent must equal receipt total.

- Valid failed → released → replan → success transcript: PASS.
- Corrupted spent, released, reserved, payment total, budget, reservation cap, or settled request ID: rejected.
- Existing buyer budget and concurrency scenarios remain passing.

Invocation: Python with `-m pytest tests/test_challenge_replanning.py tests/test_current_buyer.py -q`.
Binary observable: exit 0 and 18 tests passed.
Captured output: `replanning-pytest-full.txt`.

Invocation: Python with `-m ruff check market_a2a/challenges.py tests/test_challenge_replanning.py`.
Binary observable: exit 0, `All checks passed!`.
Captured output: `replanning-ruff.txt`.

Invocation: sibling `basedpyright` binary with `--pythonpath` set to the Python binary above, followed by `market_a2a/challenges.py tests/test_challenge_replanning.py`.
Binary observable: exit 0, `0 errors, 0 warnings, 0 notes`.
Captured output: `replanning-types.txt`.

Formatter: both owned files unchanged. Programming no-excuse audit: `no violations in 2 file(s)`. No suppressions added. Skills reflection: only provider-managed programming requirements were used; no user-owned reusable skill change was identified.
