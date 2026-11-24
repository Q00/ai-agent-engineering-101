# Offline A/B/C/D competition

This package is pure Python and makes zero network or LLM calls.

## Strategies

- Seller A (stable): one-unit lots, 20% cost margin floor, little concession,
  and delayed food buying until demand has been observed.
- Seller B (discount): larger lots, price near 82% of list, faster concession,
  and an explicit floor down to 95% of cost. Below-cost sales are measured, not
  hidden.
- Seller C (trust): truthful grade claim, Mock certificate, six-hour goods
  warranty, grade-sensitive price, and medium lots.
- Seller D (adaptive): uses only prior observed orders and settlements to
  change price and lot size. Each seed creates a fresh instance.
- Buyer A (stable): prioritizes verified reliability and quality.
- Buyer B (discount): prioritizes the lowest safe ask and negotiates a lower
  ceiling.
- Buyer C (trust): prioritizes certificate, grade and warranty value.
- Buyer D (adaptive): past-only UCB shop selection and observed-surplus bids;
  it receives no future orders or outcomes.

All strategies pass through the same safety filter. Same-owner shops, false
claims, invalid certificates, expired/insufficient-life food, unknown SKUs,
budget violations, duplicate units and duplicate orders cannot settle.

## Fairness

`build_world(seed, attempts)` pre-generates the order sequence. Competitor
offers, supply lots and breakage use separate SHA-256-derived random streams,
so one strategy's random consumption cannot change another's exogenous events.
Seller experiments hold the Buyer fixed; Buyer experiments hold competitors
fixed. Strategy state is reset for every seed.

## Official rules versus Mock assumptions

Official supply grade probabilities and grade-specific cost ranges are used.
Storage is 1% of goods cost per day; food expires; covered breakage refunds the
price; profit is cash balance minus starting/injected capital.

The following are explicit Mock assumptions because v4 does not publish them:

- goods break probability: 15%;
- competitor pricing, reliability and concession distributions;
- category demand weights and the mid-run demand shift;
- one-hour spacing between orders;
- breakage occurs two hours after sale;
- Mock certificate structural validity (not a market signature).

Official percentile, stars and forum reputation are always `null`.

## Executed phases

- Phase 1: seeds 101–105, 20 attempts, 800 valid attempts.
- Phase 2: seeds 201–210, 50 attempts, 4,000 attempts.
- Phase 3: seeds 301–320, 100 attempts, Buyer B/C only, 4,000 attempts.

The first Phase-1 diagnostic exposed a food shelf-life prefilter bug. Its 800
rows are preserved in `results/phase1_initial_failed_safety` and excluded from
comparisons. Valid total is 8,800; all executed including that diagnostic is
9,600.

Each phase stores `attempts.csv`, `seed_summaries.csv`, `summary.json`,
`config.json`, and `REPORT.md`. See `results/FINAL_REPORT.md` and
`results/FINAL_ANALYSIS.json` for selection evidence.

