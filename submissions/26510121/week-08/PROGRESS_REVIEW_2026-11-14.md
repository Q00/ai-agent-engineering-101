# Shop / Buyer progress diagnosis — 2026-11-14

## Measurement

Official board comparison: 2026-11-13 16:09:36 to 2026-11-14 12:37:53 KST (20.47 hours).
This is an observed deployment interval, including reconnection and interruption, not a controlled experiment.
Local execution events use this same half-open interval. Private inventory and controller observations are later snapshots and are separately timestamped.

Sources:
- `logs/autonomous-deploy-before-20261113T070936759881Z.json`
- `logs/peer-market-source-20261114T033752254761Z.json`
- `logs/progress-diagnosis-20261114T034839695020Z.json`
- `logs/v271-live-verification-20261114T045510571347Z.json`
- Reproduction: `diagnose_progress_20261114.py` (read-only database queries; new output per run).

## Findings

Shop ALL 0.786 → 0.790; rank 10 → 10. Book 807,845,888 → 811,954,592 (+4,108,704, +0.51%); net revenue 5,067,309,609 → 5,130,938,996 (+63,629,387, +1.26%). Official deals +1,852; rank-scoring stars 4.29 → 4.31. Inventory units 454 → 1,000; stock cost 91,234,009 → 116,006,474.

ALL weights percentiles of Book, revenue and stars, rather than raw currency levels. Our local eligibility proxy leaves Book rank 5 and revenue rank 9 unchanged. The exact proxy score differs from official ALL (0.7853 versus 0.790 currently), so its component ranks are diagnostic estimates, not official component ranks. Published next-higher Book is 86,005,288 away and next-higher revenue 1,035,130,174 away at cutoff. Those thresholds also move as competitors trade. A registered population change or others' performance can change ALL without a component rank crossing.

Same-window Book growth: inextro +76,071,051; sergiobuilds +118,086,261; yms979 -542,152. Their net revenue gains are respectively 933,973,279, 1,321,125,939 and 751,592,018. Thus some leaders are materially outgrowing us, but higher ALL does not imply every leader grew Book.

### Net Book retention

Recorded own settlements: 1,853, gross revenue 67,272,435, known acquisition cost 56,854,200, gross margin 10,418,235. Observed refunds on our identified sale cohorts total 3,631,201. Actual Book growth is 4,108,704, leaving an unattributed reconciliation deduction of 2,678,330. This residual can include expiry, storage, fees, uncovered refunds and boundary mismatch; it is not confirmed entirely as waste. Local settlement count differs from official delta by one and local gross revenue minus refunds differs from official net revenue delta by 11,847. These differences prevent an exact cash-flow attribution. Approximately 39.4% of observed gross margin is retained as the official Book increase, without asserting a causal retention rate for new sales alone.

Food: 1,778 sales, revenue 33,139,366, gross margin 4,905,767. Goods: 75 sales, revenue 34,133,069, gross margin 5,512,468. Goods generate 52.9% of gross margin with 4.0% of sales; these are before refunds, so the result does not justify indiscriminate goods expansion.

### Inventory and service fit

Successful acquisition quantity is 2,552, cost 83,549,803, independently matched between successful execution events and console lines. The policy is actually buying more, rather than merely proposing it. Of 895 portfolio decisions, 570 selected an alternative and 325 retained baseline. There were 110 research publications; it is inaccurate to conclude that no subordinate analysis or policy application occurs.

At the later 12:48 private inventory snapshot, 972 units remain: 768 food and 204 goods. Food is 79.0% of units but goods use 90.2% of inventory cost. Cash balance is 701,379,571, with controller spendable cash approximately 669 million after liabilities. Available capital alone does not explain the sales constraint; additional purchases need demand and net-value evidence.

There are 1,535 distinct locally observed unpaid stockout tasks, deduplicated by task and excluding those later settled by our shop within the interval. Reasons: shelf life 1,070, reserved stock 270, grade requirement 190, no matching stock 5. These are probes, not proven unique lost market orders. All 1,070 shelf-life requests are within nominal catalogue maximum life; supplier grade, actual age and competition still affect feasibility. The pattern supports examining remaining-life coverage and replenishment timing rather than total units alone.

### Rating quality

Of 1,851 observed reviews, 244 have at most 3 stars. Joining paid unit ledger to review shows food C: 117/263 low reviews, mean 3.665; food B: 104/976, mean 4.610; food A: 18/537, mean 4.838. These conditional differences are observational and may reflect request selection or remaining life as well as grade. The official rank-scoring rating is not the same as the mean of these new reviews.

The reputation predictor remains in shadow mode. Its observed holdout MSE improvement is modest and reviewer-account coverage is incomplete. Therefore active, validated rating optimization has not been established.

### Availability and feedback freshness

At 13:55 public verification, Shop and Buyer are listed and online, and all four public endpoint probes succeed. The earlier 12:52 read-only public check failed with network ConnectError inside the sandbox; its false online/listed fields are not evidence of a market disconnection. Connectivity recovery does not imply analytic freshness. At 12:48 the controller is defensive due to ledger lag; the feed caught-up timestamp remains 10:07 and latest research was produced 09:56, expiring 10:16. The 13:55 verifier still observes that same research timestamp. Ledger processing is advancing but had not reached caught-up state at the 12:48 diagnostic snapshot. The feed gate can defer new preparation while trading existing stock continues. The prior process log ends near 10:07 and registration resumes near 12:32, an observed gap of approximately 2h25m; exact downtime and lost profit are not measured. Startup also exhibited SQLite locking. These are execution constraints, not evidence that the market itself remained down.

336 control observations comprise 191 observe-and-trade and 145 defensive states. These are sample counts, not durations. No precise lost-sales amount is inferred.

## Buyer

ALL 0.786 → 0.809; rank 15 → 12. Saved 0.7253 → 0.7375; forum 0.905 → 0.906; deals 17,259 → 19,191; posts 2,291 → 2,501; surplus +108,643,389. Buyer improved, while forum score barely moved despite 210 more posts. More posts alone do not establish more useful information or improved forum votes.

For observed orders with a market result in the comparison interval, matched local purchase conversion is food 1,489/1,887 (78.9%), goods 444/793 (56.0%). This is a bounded operational conversion measure, not the board's cumulative paid metric or mature Saved. Initial Saved contribution is unavailable in these purchase rows; it is not replaced with zero. Verified result totals differ by one per segment at interval boundaries.

## Interpretation and priorities

Waiting alone is not supported as a sufficient route to first place. Realized Book and revenue growth are too slow against material rivals. Inventory expansion has executed but has not established economic superiority.

1. Restore and monitor feed freshness, policy age and bounded database latency independently of online status.
2. Evaluate inventory on realized net Book after refunds, expiry, storage and fees, separating sale cohorts and service conditions. Reconcile the 2.68 million residual before attributing it.
3. Improve coverage of feasible remaining-life/grade requests; link age-at-sale, pricing and warranty to subsequent refunds and reviewer-account outcomes.
4. Expand high-value goods where observed conversion and net lifetime value justify capital, rather than unit-count competition or unconditional acquisition.
5. For Buyer, prioritize goods completion and forum value per post, maintaining Saved and mature quality evaluation.

No trading-policy change or restart was performed for this diagnosis. Unit tests from the existing deployment establish code behavior; they do not establish realized rank or profit superiority.

## Evidence validation

`validate_progress_evidence_20261114.py` independently matches every chart row to the paired official Book values, reconciles the financial bridge and checks event-count totals. Its successful output is `logs/progress-20261114-artifact-validation-02.txt`. The first inline validation attempt failed due to PowerShell argument quoting; its console is preserved. The bundled chart checker could not spawn python3 on this Windows sandbox, and the bundled Sources renderer rejected its own Windows asset path. The reviewed Sources payload is preserved, but no functioning Sources inspector or browser rendering verification is claimed. The Markdown report and frozen JSON retain inspectable evidence.
