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

## Live deployment

Source promotion completed 14:56:55 KST and initialization 14:56:59. New runtime PID 36976, session `a319274f8f874609a0b9c28fbf02c099`, started 14:57:23. The existing watcher and independent registration recovery guard supervise the same owner identity.

At 15:01:29 all four public endpoint probes returned 200; Shop and Buyer were both listed online. No deployed source hash mismatch was found. At 15:01:41, `verify_v28_live.py` confirmed a runtime started after promotion, a fresh scheduler heartbeat, three terminal-policy observations, four Shop settlements and six Buyer purchases after restart. All twelve numeric observer/evaluator/compiler roles had completed. No cycle-supervisor or analytical-role error was observed in this short interval.

The research pipeline also published a fresh result at 14:59:45, replacing the prior 09:56 result. Actual recorded calls used Luna for triage, design and analyst, and Terra for critic. The critic did not approve a new LLM trial; the bounded numeric operating policy remained available. A published analysis is not automatically a proven strategy improvement.

Initial live evidence:

- `logs/v28-live-verification-20261114T060129773241Z.json`
- `logs/v28-execution-proof-20261114T060141172166Z.json`

These observations establish deployment, market connectivity and resumed execution. They do not establish sustained availability, a higher realized Book growth rate, mature Saved improvement or final first place.

At 15:05, live execution evidence also confirmed a new `shop-v28-mature-contract-frontier` restock decision and successful supply responses, including one goods unit (Gf6b70da8) and six food units (G66ded25b). The first supply plan used fresh calculation rather than the prepared-plan fast path; no prepared-plan hit is claimed for that decision. Retryable supply responses remain distinguishable from completed purchases.

Final 15:40 verification again found Shop and Buyer online, all four public endpoints returning 200, no source hash mismatch, and the scheduler running. Since the 14:57 restart, observed counts were 44 Shop settlements, 82 Buyer purchases, 36 terminal-policy publications and six research publications. The new own-objective-skip journal had 19 observations; comparable-route pause count was still zero, so actual pause activation has not yet been demonstrated. Cycle-supervisor and analytical-role error counts were zero over this observation interval. These are bounded observations, not a guarantee of future uptime or economic superiority.

Final evidence: `logs/v28-live-verification-20261114T064000782027Z.json` and `logs/v28-execution-proof-20261114T064000962704Z.json`. Source deployment is preserved in commit `955ca36`; candidate validation and failed attempts are preserved in `40846bb`.
