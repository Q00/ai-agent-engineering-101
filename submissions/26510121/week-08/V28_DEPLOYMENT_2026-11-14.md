# v28: mature risk, contract opportunities and scheduler recovery

## Problem and change

The live Shop continued trading while its market-cycle scheduler had died at 12:33 KST. `logs/auto-join-20261114T033048623982Z.txt` contains a traceback in `MarketCycle.run`: writing the heartbeat failed with SQLite `database is locked`; the exception handler then failed while recording the error to the same database, ending the thread. Online status therefore did not establish that periodic market analysis or research still ran.

v28 contains these changes:

- The scheduler survives failure of its error logging. The existing outbox hook can restart a dead scheduler and does not create a second live scheduler. Slow numeric observers run before current market analytics and policy compilation, so a preparation cycle does not publish already-stale analysis timestamps.
- Shop procurement evaluates observed upper-price contract segments as alternatives to the median-price segment. Higher prices reduce the demand scenario by the observed tail fraction; they do not inherit full-market traffic. Actual Shop quote candidates include the same observed grade/warranty prices. These are historical selected trade segments, not causal demand curves.
- Fully insured expected payoffs can use a mature posterior mean only after a temporal holdout with a 6h10m embargo passes. Sparse, stale or failed checks keep the prior risk upper bound. Public Buyer quality means also require the validated own-grade cross-check. Portfolio stress uses upper refund risk, and cash still reserves full gross refund liabilities.
- Buyer initial routing and quote comparison use full incremental ALL progress per execution time. Explicit own-objective skips are recorded separately from seller refusals. Repeated comparable unproductive contracts receive short, recoverable pauses, with probes and release on changed terms, improved price, positive value or paid recovery. Transport errors do not create economic-refusal labels.
- Shop price candidates can use public Saved benchmarks for a payer only through market-confirmed identity links. Unauthenticated names do not select a personal target. Existing grade, certificate, unit, price and checkout checks remain authoritative.
- Terminal planning uses the official Nov 17 23:59 KST end. Aged goods do not receive a generic below-cost clearance discount larger than remaining storage avoided, since stock retains acquisition value in final Book.
- Buyer information selection rotates toward categories not recently served, within the existing information format policy and with real settlement references.
- A numeric terminal-contract observer provides a compact packet to the existing research roles. No new paid LLM role or foreground LLM call was added. Existing Luna/Terra profiles are preserved; Astra remains excluded from active roles. Existing internal inventory/capital caps remain disabled; legal supply limits and liquidity checks remain.

## Validation before release

Final isolated regression: **602 passed**, one existing Starlette/httpx deprecation warning. Earlier failed runs are preserved. One old exact-dictionary test was updated for additional calibration metadata, and the v27 pinned Buyer revision test now explicitly checks the v28 revision when enabled. Legacy fixture tests retain their v26 feature configuration.

The live-data risk check rejected the seller A-grade mean: its embargoed test Brier was worse than using the prior upper bound as a point forecast. v28 therefore retained the upper bound for that cohort. It did not loosen risk merely to increase purchases.

Frozen shared-market restock comparisons produced different candidate quantities but higher forecast Book and revenue in successive snapshots. In the final pre-release replay, baseline forecast Book was 64,264.72 and v28 134,269.92; forecast revenue was 387,905.70 and 1,960,899.40. Both selected 22 units, with 1 versus 3 goods units. These are six-hour conditional model outputs, **not realized returns or causal superiority**. The v28 preparation took 7.42s versus 6.06s in this snapshot; this work runs in background before supply windows.

Final context replay retained market examples at **22,468 bytes**, below the unchanged 23,000-byte cap, including the new terminal packet. It used a coherent isolated generation clock because live analytics were stale; it is not a claim that the old live research pipeline was healthy. No market transactions, paid model calls or live policy writes occurred in replay.

Evidence:

- `logs/stage-v28-full-regression-04.txt`
- `logs/v28-reversal-replay-20261114T054705808118Z.json`
- `logs/v28-context-replay-20261114T054633462364Z.json`
- `validate_v28_reversal.py`
- `release_v28_reversal.py`
- `verify_v28_live.py`

Release changes only hash-checked source files after cooperative Buyer drain. Identity keys, owner credentials, balances, inventory and raw journal records remain in place. Strategy revision epochs distinguish subsequent observations without deleting older evidence. Live availability, scheduler freshness, published research and paid execution must be verified after restart; final rank and profit superiority require subsequent mature results.
