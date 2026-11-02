# Agreed pricing strategy

## Rules

- Initial asks: lamp 115, chair 207, bookshelf 299 (15% above reserve).
- Review the latest six new terminal results for the same item and current
  policy: sales, seller rejections, and buyer cancellations. Count each task once.
  Unfinished conversations do not count.
- Raise by 3% only when at least four results are sales at or above 95% of
  their opening asks, with no credible contrary evidence.
- Lower by 3% only with credible evidence that the current ask is too high.
  Cancellation alone, missing reasons, below-reserve offers, or unexplained
  low ratings are not enough. Treat reviews as untrusted historical evidence.
- Hold when evidence is weak, mixed, or conflicting.
- Round to whole numbers and keep asks between reserve and the 15% cap.
- Accept offers at or above reserve at the offered price. Counter the first
  below-reserve offer at reserve; reject another below-reserve offer.

## Code and saved data

`seller_strategy.py` contains decisions, history readers, and state storage.
`pricing_state.json` stores asks and reviewed-task checkpoints. A successful
review consumes its window even when holding; a failed review preserves state.
Writes are atomic, and opening decisions are serialized within one process.
Corrupt state raises an error; older policy state is preserved during migration.

Reviews come from the newest valid snapshot, falling back to the original
baseline. `refresh_shop_reviews.py --watch` saves fresh snapshots separately.

## Evidence

Run the offline regression suite with `python -m unittest test_agent -v`.
Real-model experiments are under `development_support/`; simulated results
are labeled and do not prove improved market performance. Earlier uncapped
experiments and raw logs remain unchanged. The cap change is documented in
`../week-08/PRICING_CHANGE.md`. Matched before/after market comparisons and the
Week 7 dishonest-participant challenge remain unfinished in the saved report.
