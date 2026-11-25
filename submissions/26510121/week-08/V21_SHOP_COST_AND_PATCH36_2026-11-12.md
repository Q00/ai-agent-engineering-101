# Shop v21 deployment, operating costs, and Buyer patch 3.6

## Applied Shop change

Source-only v21 promotion completed **2026-11-12 17:29:21 KST**, manifest
`logs/v21-source-promotion-20261112T082921918741Z.json`. The audited source promoter
is reused; its historical backup-directory prefix does not identify running code.
Prior runtime was cooperatively drained before Buyer negotiation/payment. Account,
credentials, cash, inventory, reservations and ledger were preserved.

The fixed public-demand factor 0.5 is replaced, in enabled mode, by a leave-one-SKU-out
group prior followed by time-decayed profitable own payments divided by observed
availability. Its inputs exclude future sales and deduplicate paid units. Group
and SKU shrinkage avoid counting the same own payment twice. The raw public rate
is neutral rather than arbitrarily halved. A stale calibration retains own paid
evidence in a neutral posterior fallback, rather than discarding it.

Target protection time now follows attempted supply-window reliability, phase to
supply, delivery allowance and the economic planning horizon. Confirmed success
within a window supersedes retryable failures; uncertain and unattempted windows
are not manufactured failures. This conditional-access model can extend beyond
the old one-hour ceiling when evidence supports it. Unattempted outages remain
unknown, and historical availability remains partly a physical-stock proxy.

A background observer updates numeric packets without paid model calls. The
merchant planner consumes the same rules-bound generation; prepared supply plans
invalidate when calibration changes. There is no new LLM call on Main's trading
path. Total/SKU-held quantity caps remain disabled, with twenty units per legal
supply POST, full refund collateral, cash, storage and concentration guards.

**428 tests passed** (`logs/stage-v21-full-regression-02.txt`), including neutral
cold start, empirical multiplier changes, no future/offline training, unit
deduplication, window-level failure attribution, coverage extension, stale/rules
invalidation and prepared-plan generation checks. Two initial regressions remain
in `logs/stage-v21-full-regression-01.txt`: an obsolete hardcoded 15% assertion
despite the existing configured 35% limit, and a fallback that initially discarded
own evidence. Both were corrected without loosening the configured cash guard.

Frozen replay `logs/v21-shop-replay-20261112T060402954027Z.json` used the historical
v20 source, not a current-market forecast. It produced 13 units under v20 versus
23 under v21, with commitments 1,992,214 versus 1,468,703. These use different rate
assumptions; their expected Book values are not evidence of realized superiority.
Calibration took 0.39 seconds in that replay. Main remains independent of it.

Live proof: `logs/v21-adaptive-live-proof-20261112T084025470970Z.json` confirms the
observer is active and the merchant consumes a fresh calibration. The median
multiplier was 0.653, not fixed at 0.5; conditional protection times were about
20.5–40.5 minutes. Reliable recent supply can shorten coverage; it is not forcibly
set to six hours. All four public HTTP checks, both online accounts, source hashes
and recovery guard passed in `logs/v21-live-verification-20261112T083537515770Z.json`.

## Why Shop performance looks weak

Review window: **14:34:23–17:28:37 KST**, approximately 2.904 hours, before v21.
The two full snapshots and exact read-only queries are documented in
`review_shop_cost_v21.py` and its timestamped `v21-shop-cost-review` outputs.

| Measure | Observation |
| --- | ---: |
| Official Shop ALL | 0.823 → 0.797 |
| Overall rank by published values | 10 → 11 / 66 |
| Official Book change | +1,087,257 |
| Official Revenue change | +26,445,028 |
| Official deals change | +222 |
| Book rank / Revenue rank | 5 / 9, both unchanged |
| Stars-rank metric | 4.34 → 4.31 |
| Stars-rank position | 35 → 44 / 66 |
| Cash change | −1,408,968 |
| Acquisition-cost inventory asset change | +2,488,997 |
| Cash + inventory-cost asset change | +1,080,029 |

The cash decrease is therefore not all consumption: inventory assets increased.
The small difference between official Book and asset deltas is retained as
snapshot/poll boundary timing, not forcibly reconciled into a nonexistent fee.

Local distinct sales with known costs generated gross margin **2,970,213**, with
zero below-cost sales in this window. Unit-matched confirmed refunds were
**1,752,477**, consuming approximately 59% of that margin. One grade-B unit sold
within this same window had price 1,752,477, acquisition cost 1,444,023 and six-hour
warranty. Its gross gain was 308,454; the full refund made that unit's realized
Book effect −1,444,023. A single draw does not prove that its ex-ante pricing was
unprofitable, but it materially depressed this short-period result.

The remaining **137,707** difference after gross margin, confirmed refunds and
asset changes is unclassified consumption/timing. It is not all labeled storage.
End-stock goods would cost approximately **581,694 per day** to hold at the stated
daily rate; this is a static forward holding-cost scenario, not an exact past fee.
Unit counts reconcile: 149 + 267 confirmed acquired − 220 local sales = 196.
Official deal deltas use board poll boundaries and differ by two sales.

Of local sales, food contributed 190 sales / 536,674 gross margin; goods contributed
30 sales / 2,433,539 gross margin. Refund losses must still be considered separately.
More cheap transactions alone do not identify the best Book/Revenue/Stars policy.

Matched peers grew faster over the same window: sergiobuilds Book +12,608,423 and
yms979 +5,504,174, versus our +1,087,257. The Stars component's nine-place drop
approximately explains the observed ALL decline under its 20% percentile weight;
the exact server tie rule is not reconstructed from rounded values.

The rating model is still **shadow**, with observed holdout MSE about 0.7558 versus
0.7500 for its baseline. It therefore does not add rating predictions to decisions.
That safeguard is correct, but no validated rating improvement has been established.
Demand correction by itself does not repair this Stars/insurance weakness.

Shared Shop/Buyer API usage during this review window was **58 calls, about $0.994**
by local token-price estimate: analyst $0.609, critic $0.316, designer $0.061 and
triage $0.008. There were 18 published reviews but only two distinct operating
parameter sets (coverage 3 in 15 reviews, 2 in three). This establishes repeated
paid analysis, not the value of every review or causal performance improvement.

Recorded daily shared cost through 17:28 was about **$2.059**, including **$0.356**
explicitly marked validation calls. One earlier Astra validation predates the
owner's exclusion; it is not an active role. These are journal estimates, not an
invoice, and do not include other applications on the account. Real USD cost
cannot be divided by virtual market Book as monetary ROI. No daily API cap was
introduced; the owner's observation preference is preserved.

Priorities established by this review are insurance net-margin/variance,
validated Stars improvement, and avoiding repeated paid reconsideration when
there is no material strategy change. More LLM calls do not establish superiority.

## Buyer discontinuity: patch 3.6

Official rule diff: `peer-market-source-20261112T054459689112Z.json` versus
`peer-market-source-20261112T085523230195Z.json`. The first live new-rule observation
was **17:41:07 KST**. For a served healthy deal, the score changed from
`(list price - price) / list price` to
`min(1, (list price - price) / (list price - unit cost))`. Unserved/refunded cases
remain subject to the published special values, and aggregation still divides by
`deals + 100`.

At 17:28, our Buyer had ALL 0.869, Saved 0.3416, overall rank 3 and Saved rank 9.
At 17:55 it had ALL 0.747, Saved 0.7079, overall rank 21 and Saved rank 25.
Deals and post component positions stayed 13 and 12; forum stayed 0.903. The main
discontinuity is the 50%-weighted Saved percentile. Raw Saved values across this
formula change are not a performance time series in consistent units.

Our Buyer still uses the old discount-based contribution and cap functions in
`r3/buyer_scoring.py`. It interprets new Saved 0.7079 plus margin 0.002 as a required
70.99% discount from list. A post-patch food quote was 23,440 against list 29,300,
while our cap was only 8,499, about 29% of list. This is a concrete ongoing policy
mismatch, not merely a display change.

The rule auditor in `r3/control.py` checks the Saved label, `deals + 100` and failure
special cases but not this denominator. It incorrectly continues to report
`buyer_compatible=true`. The Shop-only v21 release did not implement patch-3.6
Buyer arithmetic. Local purchase records and public settle observations also do
not currently retain the unit cost needed for exact per-deal replay.

Required Buyer correction is formula-version detection, cost-aware reward and
price caps using verified costs or explicitly uncertain cost estimates, and
version-scoped outcome evaluation and refusal memory. Old Saved outcome values
must not be mixed with the new official baseline. The frozen diagnostic script is
`diagnose_buyer_patch36.py`; it makes no trades or model calls.
