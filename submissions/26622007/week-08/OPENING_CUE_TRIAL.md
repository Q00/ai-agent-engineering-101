# Price-neutral first-response cue

## Hypothesis and scope

Append exactly `Please send your offer as a number.` to the initial stock quote.
A keeps the existing body. B adds only that sentence, after the existing price.
The numeric ask, metadata.ask, reserve, opening anchor, warranty, inventory
selection, adopted 3% opening and late-round concession remain unchanged.
This is an exploratory conversion hypothesis, not a diagnosed cause of cancellation.

In the preceding mature cohort, 46/197 tasks had no observed positive offer,
but cancellation reasons were not recorded. That is an upper bound on the
target population, not expected recovery. The alternative phrase containing
"single number to continue" fails a supported text-only parser fixture and is
not used. Do not combine a price-label change with this experiment.

## Public seams and verification

- OpeningCue.message: exact body delta, immutable prices/terms, full-task
  durable choice across flags/re-entry, admission caps, safe failure.
- Seller.open_from_stock: real StockBook and price code; transport captured
  at the SDK boundary. A/B metadata and protected deal fields must match.
- The isolated quote/turn/payment trace must never use an old price trial ID.
- The existing text-only price seam retains the same numeric price.

## Trial contract

Default off. Use an isolated MARKET_DIR/opening_cue directory. The bounded
seller_concession_trial.json schema is inherited from the existing trial
validator: enabled, id, start, end, seed, optional stop. Start is fixed before
the first enrollment after deployment/health and any buyer protection window.
Enroll for 30 minutes; no outcome-adaptive early stop. Maximum 200 total and
100 B; reaching either closes new enrollment on both sides. Fixed 600-second
per-task follow-up includes nonresponse in the denominator. Full UUID is the
key; kind, original price band and five-minute block are the strata.

Read the raw ledger by deduplicated sequence and exact task/unit/price.
Known unit mismatches stay excluded, with all assigned tasks in denominators
and unknown monetary effects left unknown. Real missing receipts, collector
gaps/conflicts or insufficient horizon coverage prevent final certification.
Trace "deal" and unverified callback events are not ledger settlement.

Compare strict paid/assigned, fixed-window settled/hour and revenue minus
actual cost and observed refunds. Record exact official Players/shop/buyer
snapshots separately; shared stock, repeated buyers, concurrent policy and
peer changes limit attribution. Calls/latency are secondary observations.
Statistical significance is not a prerequisite to continue experimentation.

## Controls, adoption and rollback

Missing/invalid config leaves new tasks in A. Existing task choice persists.
`opening_cue/disabled` blocks new treatment. `opening_cue/adopted` selects B
for new tasks after a reviewed practical adoption decision; disabled wins.
Removing flags does not rewrite historical or in-progress assignments.
Do not reprice or modify the old opening/late-concession cohorts.

Deploy only after complete local checks, independent code/security review,
recorded deployment gate, rollback preparation and current buyer/restart
coordination. Preserve current post-adoption tracking before server turnover.
A restart is not needed when switching this already-loaded feature's flags.
Six-hour refund maturity is measured from actual settlement, not quote time.
