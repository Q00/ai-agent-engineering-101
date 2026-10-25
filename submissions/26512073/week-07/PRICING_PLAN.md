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

This unit is not connected to `decide_opening` yet. The running code still uses
the previous 30% prompt, and the saved review snapshot does not refresh itself.
Next: connect the six-result review and persist its decision, then test the full
pricing behavior before restarting the market seller. Existing acceptance rules
remain: accept any buyer offer at or above reserve at the actual offered price.
