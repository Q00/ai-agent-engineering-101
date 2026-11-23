# Student's agreed pricing strategy

- Start each item at 15% above its fixed reserve, rounded to a whole number.
- Review six new completed negotiations for the same item. Count deals and
  explicit rejections; keep cancellations and unfinished conversations separate.
- Raise the previous ask by 3% when at least four of those six deals sold at
  95% or more of their respective opening asks.
- Lower the previous ask by 3% when credible evidence suggests the asking price
  is too high. Below-reserve budgets alone do not establish this. An unexplained
  low rating or repeated reviews from one author are insufficient by themselves.
- Hold when evidence is weak, mixed, or conflicts with the raise signal.
- Round adjustments to a whole number and never go below reserve.
- Mark each reviewed group as consumed even when holding. Wait for six new
  completed negotiations before another review.

## Persistence step

`pricing_state.py` provides state storage, separate from append-only event logs.
For each item it stores `ask`, `last_reviewed_task`, and `policy`.
The checkpoint identifies the last completed task in a consumed review group.
Save the ask and checkpoint together only after a review decision succeeds.
The first adaptive review must establish a boundary so old-policy or local test
results do not accidentally count as new live adaptive negotiations.

Missing state starts with asks 115 (lamp), 207 (chair), and 299 (bookshelf).
Invalid or corrupt state raises an error instead of silently losing the checkpoint.
Writes replace the file atomically and preserve other items' saved state.
The helper supports one seller process; the full decision transaction must be
serialized when connected to concurrent opening requests.

The state is now connected to `decide_opening`. New items start at 15% above
reserve. Six new results trigger a real harness review; Python calculates the
3% change and enforces the reserve. A lock serializes opening decisions. Even a
hold advances the checkpoint. Failed reviews leave saved state unchanged.
Existing acceptance rules remain: accept any buyer offer at or above reserve
at the actual offered price.

The full official trade test passed 25/25 with isolated test memory. A separate
experiment using synthetic histories and real harness replies matched all three
expected outcomes: raise 115 to 118, lower 115 to 112, and hold at 115. Each
scenario waited for six results and did not reuse the reviewed window. This
experiment is labeled synthetic under `logs/challenge-1/`; it is not evidence of
actual market trades or improved share/average price.

Reviews still come from the saved historical snapshot. Automatic refresh and
the live before/after comparison remain to be completed before claiming the
pricing challenge is done. The market seller has not been restarted by these tests.
