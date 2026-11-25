# Procurement outcome tracking

The model approves a basket before the supply window. On opening, tools recheck
cash, reservations, stock and time, then return a `buy` protocol. Its
`_procurement` metadata links each line to the exact decision and reservation.

The frozen `seller_kit.py` loads the official shared kit without modifying it.
`procurement_transport.py` observes its existing restock calls. Authentication,
request bodies, three 429 retries, delays, inventory refresh and 402 batch stop
are still handled by that kit. Logged HTTP metadata contains no headers, keys,
tokens or response bodies.

Stages: `portfolio_execution_started` → `portfolio_execution` →
`portfolio_execution_completed` → `procurement_protocol_output` →
`procurement_protocol_received` → `procurement_request_started` →
`procurement_supply_result` → physical unit reconciliation. An emitted request,
an accepted supply response, arrival and resale are distinct observations.

Only an exact intent/decision/window/SKU/quantity match can release funds for a
definite rejection or a line never sent by a finished batch. These also stop
consuming the rolling exploration budget. A timeout or 5xx retains its
reservation. An accepted response also retains it until a physical unit is
observed. Conflicting physical evidence restores conservative accounting.

The independent planner worker reconciles about once a minute under the same
portfolio lock. `state/procurement-health.json` lists old unknown arrivals and
missing execution/kit/request/response stages. Warning events are deduplicated.
Alerts never trigger automatic retries or purchases.

Historical repair is conservative and does not rewrite raw logs. Run
`ops/reconcile_legacy_supply.py --runtime state/seller-runtime --console
logs/seller-r3-kit-console.log` for a dry run, adding `--apply` to persist it.
It requires a complete, uniquely matched printed protocol plus its timestamped
emission and exact saved intent. It can recover definite HTTP refusals and
lines after the official kit's `buy[:10]` cutoff. Duplicate batches or dates,
missing evidence, partially observed quantities and 5xx are not released.

Validation uses the actual shared kit's restock function with synthetic HTTP
responses: 402 batch interruption, never-sent tails, retry exhaustion, success,
5xx, timeout, conflicting evidence and ambiguous legacy batches.

## Capital policy v2

The old 12,000,000-won total exposure ceiling and 180-unit global inventory
ceiling are removed from recurring, cached and startup execution paths.
Quantity still requires model approval, forecast-based SKU targets, per-SKU and
category room, current cash, unresolved commitments and the official kit's
10 lines / 20 units per request. The 200-unit number is a window protocol
capacity, not a limit on existing inventory.

Actual inventory cost plus pending acquisition ceilings is bounded by a
tunable share of live cash plus unsold stock cost: default 80%, permitted
65–90%. Minimum cash, bankruptcy-line buffer, holding-cost buffer and warranty
cash reserves are mandatory regardless of that share. The 65% allocation target
remains guidance for the transaction model, rather than a forced purchase.

Meta can change `inventory_capital_share`, `window_budget`,
`rolling_exploration_budget`, `target_capital_share`, `epsilon` or `focus`.
Both budgets start at 24,000,000 won and can range from 1,200,000 to
1,000,000,000; live capital/risk always clips actual spending. One review can
change one parameter: share steps at most 0.10, budget multipliers 0.5–2,
epsilon steps at most 0.05. A refreshed-snapshot counterfactual must demonstrate
an actual budget/branch change or explicitly labeled prompt-guidance change.
Unspent budget is not profit, an order, or a demonstrated improvement.

Version-1 policy reads add the new operator defaults and replace the old
12,000,000 default window budget with 24,000,000. Learned target, epsilon and
focus, custom budgets, trade logs and pending reservations are preserved.
The next real review records schema 2, rebases its outcome baseline for the
new capital contract and cannot credit historical Book growth to this change.

## Recovery quantity choices (portfolio 15)

Increasing the cash ceiling alone did not expand near-window-only quantities.
During growth below the live inventory-cost target, proven sellers now offer
additional demand-derived coverage choices: 2/6 hours for goods and 1/2 hours
for food. Existing next-window low/base/high options remain available. These
are operator-defined alternatives, not meta-adjustable parameters or automatic
orders. The model compares margin, holding, expiry and uncertainty, then
chooses an exact target or holds. All quantities remain subject to the same
SKU, category, pending, current cash and execution-time limits. Second trials
remain one unit. Catalog shelf life never guarantees incoming remaining life.

An explore buy with replenishment must include the feasible novel floor:
up to two new SKUs for recovery, or one for an ordinary epsilon draw. A high
random draw cannot cancel an effective recovery override. An invalid basket
gets one correction before any intent is reserved; a concrete-risk hold is
allowed. The model allocates the shared ten lines, rather than pre-reserving
eight for replenishment and leaving only two possible novel choices.

Approval records retain the chosen forecast scenario, coverage hours and
forecast mean. Compare approved units, emitted requests, accepted responses,
physical arrivals and later sales separately; a larger model approval alone
does not establish better performance or actual capital deployment.

## Lost demand and exposure (portfolio 16)

The planner's once-per-minute maintenance now records fresh inventory snapshots
using the mtime of the file actually read. The old restock hook had recorded
these observations; switching to prepared procurement accidentally bypassed
that recording. New observations restore forward-looking exposure coverage.
Historical gaps remain unknown; today's inventory never backfills them.

Each forecast window uses the larger of (a) the exposure-adjusted paid-sales
rate and (b) `(confirmed sales + eligible missed requests * conversion) / hours`.
The two rates are not added, because exposure correction already infers some
unobserved demand. Low/base/high and multi-hour choices use the resulting rate.
No inferred missed sale is reported as an actual transaction or revenue.

Eligible missed requests are distinct `no_stock` tasks naming exactly one SKU.
Category-only and multi-SKU requests are excluded from SKU loss quantities.
Requests subsequently quoted or settled for that SKU are excluded too. A
legacy settlement can be joined only through a unique signed-receipt
SKU/unit/price match. A task is the available identity; unrelated new task IDs
cannot be proven to be the same demand. `no_stock` means no matching available
unit, including units unavailable because of reservations, not proof of zero
physical stock.

Conversion uses the last 12 hours of served quotes at least 15 minutes old.
Linked settlements are successes; explicit rejected/cancelled/unpaid outcomes
are non-successes. Pending, receipt-only, expired-unknown and immature attempts
are excluded. The shop has an explicit Beta(2,2) prior (50% without evidence),
and each SKU is shrunk toward that shop estimate with four prior attempts.
These are operator hypotheses, not calibrated conversion probabilities, and
meta cannot change them. The model sees the sample counts and uncertainty.
Demand based on missed requests does not bypass the proven-sale threshold or
single-unit new/second trials. Approval records preserve raw sales, eligible
missed requests, expected lost sales, conversion and exposure as separate facts.


## Audited allocation comparisons (portfolio 17)

The transaction model receives feasible broad, balanced and depth examples.
They reserve the existing feasible exploration floor, then fit listed targets
within the same cash, novel budget, line, unit, pending and category limits.
Broad emphasizes up to eight novel lines and near-window maintenance; balanced
uses up to four novel lines and intermediate buffers; depth uses up to two
novel lines and the longest supplied buffers. Existing exploration floors can
raise those counts. Identical or infeasible examples are omitted. These are
comparison baskets, not new spending constraints or automatic orders.

During growth capital recovery, if multiple examples and proven sellers exist,
a buy must compare at least two examples and explain the allocation tradeoff.
The model may select an exact example or a custom basket. Every omitted proven
SKU or lower-than-largest offered target requires a SKU-specific exception.
An absent/incomplete review gets the existing single correction before any
reservation; a hold remains available. The option named `high` is explicitly
identified as near-window coverage, rather than the deepest offered target.

Examples compare approximate mean-demand coverage over the SAME two-hour
horizon and same evidenced sellers. Novel demand is unknown, not worthless;
the coverage diagnostic omits margin, uncertainty, holding, spoilage and
refunds and cannot be treated as a profit score. Approval logs retain all
examples, the structured model comparison and actual buffered/omitted SKU
counts. Meta sees these cases, so an unused budget is not misdiagnosed as a
reason to raise the monetary ceiling again.
