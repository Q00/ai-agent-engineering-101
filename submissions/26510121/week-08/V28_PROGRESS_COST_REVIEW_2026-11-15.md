# v28 progress and API effectiveness — 2026-11-15

## Finding

The market observation loop is running, but the realized score and Book results do not establish a successful reversal strategy. The expensive reviewer repeatedly reviews a narrow set of operating choices. Reduce repeated paid review; do not treat the number of reports, tools or calls as strategy improvement.

Official comparison: Nov14 14:56:14 KST (deployment baseline) to Nov15 11:22:56 KST, 20.445 hours. Endpoint capture uses `Cache-Control: no-cache`, records server Date and ledger tail time, and is stored in `logs/v28-progress-source-20261115T022256766670Z.json`. Server Date and the newest ledger timestamp agree with the capture clock. Earlier Nov14 short-window capture and its local count discrepancies are preserved; no causal conclusion is based on that short-window discrepancy.

| Metric | Before | After | Change |
|---|---:|---:|---:|
| Shop Book | 812,515,473 | 810,285,994 | -2,229,479 |
| Shop revenue | 5,136,937,516 | 5,194,570,499 | +57,632,983 |
| Shop ALL / global rank | 0.787 / 11 | 0.776 / 12 | worse |
| Shop scored stars | 4.30 | 4.32 | +0.02 |
| Buyer Saved | 0.7390 | 0.7506 | +0.0116 |
| Buyer ALL / global rank | 0.812 / 12 | 0.808 / 12 | score lower |
| Buyer forum | 0.906 | 0.907 | +0.001 |
| Buyer posts | 2,528 | 2,765 | +237 |

Global ranks use published ALL, including published outside/house players. Class scores are a different scope and must not be mixed with these ranks. Scores are relative, so own Saved improvement can coexist with lower ALL.

## Realized execution

Local Shop records contain 1,626 distinct units versus official deal delta 1,634, an eight-deal boundary/coverage difference. Observed food gross margin is 3,732,237 from 1,528 sales; goods gross margin is 4,798,264 from 98 sales. Total gross margin is 8,530,501 before refund/storage/expiry.

Observed refunds total 7,096,951. Of this, 274,736 links to one pre-v28 sale and 6,822,215 links to nine sales since v28. This is not predominantly inherited old-sale refunds. The gross margin minus observed refunds minus actual Book increase leaves 3,663,029 of deductions/reconciliation residual. It includes uncovered costs and timing differences; it is not classified entirely as expiry.

A flow reconstruction estimates goods storage at 897,305 over the interval. All supply console costs could be classified by catalogue kind. At least 125 units from the starting food inventory expired in this interval without a recorded sale, with acquisition cost 1,737,471. This excludes expiry of newly acquired units and is subject to the bounded own-sales join. These two estimates explain a substantial portion of the residual but do not constitute an exact full ledger reconciliation.

Of 98 new goods sales, 50 are at least 6h10m old at cutoff. For Buyer goods, 489 of 637 local purchases are beyond that horizon and have canonical mature outcomes. Some outcomes remain pending, but enough time has passed to reject the claim that all weak results merely need a first maturity wait.

Actual purchases of inventory total 1,890 units / 63,913,507 over the interval. Inventory was 967 units on the official baseline board and 1,019 at cutoff. The later 11:24 private snapshot has 1,043 units (768 food, 275 goods), balance 696,071,990 and stock cost 119,220,107. Snapshot times differ; these are not exact same-time inventory-flow totals. Capital is available; total units alone do not explain weak Book growth.

Unpaid request observations: shelf-life reason 1,908; reserved stock 293; grade requirement 126; no matching stock 5. These are deduplicated local probes, not proven lost sales. Food C low-review observations are 83/321, food A 6/405; grade, price, request and lifetime composition confound causal interpretation.

Buyer operational conversion is food 1,642/2,072 (79.2%) and goods 637/859 (74.2%) among observed closed orders. This is not cumulative board paid rate. Exact healthy Saved per newly purchased unit is unavailable because other sellers' unit costs are private. The accounting-implied Saved change per added deal is about 0.850, mixing new transactions, old quality revisions and rounded official scores; it is not a mature new-cohort mean of 0.850.

Competitor same-window Book growth includes sergiobuilds +121,140,396, nocha12 +46,414,812, 25620027 +24,937,972 and inextro +21,642,396. Our -2,229,479 does not support an expectation of catching these competitors merely by waiting.

## API cost and action attribution

Recorded configured-price estimate for this Shop/Buyer journal: **471 calls / $2.6953**. It is not a provider invoice and excludes other applications using the API account.

| Role | Calls | Estimated USD | Share |
|---|---:|---:|---:|
| Terra research critic | 133 | 2.4018 | 89.1% |
| Luna research analyst | 134 | 0.1997 | 7.4% |
| Luna capability triage | 145 | 0.0653 | 2.4% |
| Luna capability designer | 59 | 0.0285 | 1.1% |

There were 133 research publications, zero approved new trials, zero new queued Buyer profiles and 14 distinct published operating signatures. Seventy-three consecutive publications repeated their predecessor's operating signature. Some operating parameters still changed: 59 signature transitions occurred, and numeric feedback produced 680 policy IDs. Therefore neither 'all calls were useless' nor 'all publications changed Main behavior' is supported.

Fifty-nine capability plans used six tool sets; the same three-tool set occurred 44 times. These are shallow read-only audit plans, not 59 new trading algorithms.

Framework experiment events contain one expiry without verified improvement and one restart after cooldown, with no observed promotion in this interval. The new economic-skip journal contains 603 records, but no economic-route pause activation. The pause code being deployed is not evidence it has changed a live route yet.

The supervisor remained active despite 31 recorded SQLite OperationalError events; analytical roles recorded 25 such errors. This supports resilience of the repaired scheduler, not elimination of database contention. Latest heartbeat, feed and research timestamps are fresh, and local/public recovery observations show online status.

## Targeted cost correction: v28.1

Add a one-hour de-duplication gate between the low-cost analyst and Terra critic. The key includes the exact executable proposal fields, rules hash, guard status/reasons, framework issues, a coarse leader-gap band and current control parameters. Only a recently reviewed proposal with `approve_trial=false` can hit the gate. Trial requests, rule/guard/issue/control changes, novel executable proposals or expired review records require the critic again.

A hit records `research_critic_skipped` and returns without a new publication or extending the old policy's expiry. It does not rebrand a previous review as fresh independent approval. Current numerical execution and its existing expiry/fallback rules continue. This may reduce operating-parameter publication frequency; it does not change prices, purchase caps, checkout protection or financial obligations.

All ten inspected core trading/risk/payment files are byte-identical to v28. Shop/Buyer strategy revisions, maturity epochs, models and the owner's no-dollar-cap preference remain unchanged. The gate stores at most 64 one-hour records. No new paid model role is added.

Validation: 613 tests pass in isolation, including integration verification that the second identical proposal invokes only the analyst and leaves the previous policy/expiry unchanged, and that changed executable content invokes the critic again. Existing Starlette deprecation warning remains. Actual post-deployment call and cost reduction is a future measurement; no guaranteed percentage saving or score improvement is claimed.

Evidence: `logs/progress-diagnosis-20261115T022420511260Z.json`, `logs/v28-cost-progress-20261115T023914031223Z.json`, `logs/stage-v281-full-regression-02.txt`, `logs/v281-cost-replay-20261115T024014860858Z.json`. SQL and filters are preserved in `audit_v28_cost_progress_20261115.py` and `diagnose_progress_20261114.py`.

The next economic issue is net realized contract profit after refunds/storage/expiry, service-eligible inventory and account-balanced quality. A cheaper analysis loop alone is not a solution to the ranking shortfall.
