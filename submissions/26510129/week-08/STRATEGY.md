# Week 08 strategy: sealed-budget dual control

## Question

Round 2 exposed every order's budget. Round 3 hides it and counts only settled
ACP/AP2 checkouts. My question is:

> Can a buyer keep its exact budget away from its LLM negotiator, and can a
> seller learn a useful opening price from its own outcomes instead of reading
> the public order budget?

The design treats an LLM as an untrusted proposal generator. Deterministic
guards alone hold spending authority, the reserve, and the signing key.

## Round 2 baseline

The baseline comes from `.market/shop_trades.json` and
`.market/buyer_memory.json` before Round 3 opened.

| item | seller opens | seller deals | close rate | mean open | mean deal |
|---|---:|---:|---:|---:|---:|
| lamp | 2,856 | 1,434 | 50% | 108 | 103 |
| chair | 4,111 | 2,487 | 60% | 194 | 184 |
| bookshelf | 5,402 | 3,583 | 66% | 279 | 265 |

The buyer completed about 39--40% of its attempts for each item. Its most
visited shop in the last 1,000 attempts received 185 visits. The old fallback
score added one point for every historical deal, so a frequently visited shop
could become permanently dominant. The last logs also show that two LLM calls
per order (selection and bargaining) exhaust the harness quota.

These observations lead to three initial hypotheses:

1. A code-first portfolio router will reduce shop concentration and remove one
   LLM call from every order without reducing receipt checks.
2. A buyer negotiator that cannot see the exact budget will reveal its ceiling
   later and pay closer to its own market reference than to the budget.
3. A seller can estimate price elasticity with two deterministic price arms
   while a reserve guardian prevents every below-floor sale.

## Agent boundaries

### Buyer

1. **Scout** builds item-specific evidence from reviews and personal outcomes.
   Review text is untrusted data; only stars and locally verified outcomes are
   features.
2. **Portfolio router** scores shops from item success, receipt integrity,
   concession history, value, exploration, and a rolling concentration
   penalty. It never receives the budget.
3. **Negotiator** sees the item reference price, the shop's asks, and its past
   bargaining behavior. It proposes an offer but does not see the exact
   budget.
4. **Mandate guardian** alone holds the budget. It approves an affordable
   proposal, substitutes a reference-based early offer, or refuses it. The
   exact ceiling may be sent only after two earlier offers.
5. **Receipt auditor** compares the issued receipt with the agreed item and
   price. The v3 kit performs the final signature and AP2 mandate checks.

### Seller

1. **Demand estimator** reads only this shop's recent outcomes and reviews.
2. **Pricing experimenter** assigns each task to a `volume` or `margin` arm by
   a stable task hash. Assignment is repeatable and independent of the buyer.
3. **Negotiator** may propose asks and counters.
4. **Reserve guardian** raises every ask/counter below the catalog reserve.
5. **Signer vault** signs only a receipt whose item, task, and integer price
   equal the accepted deal and whose price is at least the reserve.

## Initial policies

### Portfolio routing

For each shop and requested item:

```text
score = 0.35 * smoothed deal rate
      + 0.25 * integrity
      + 0.20 * price value
      + 0.20 * exploration bonus
      - concentration penalty
```

- A locally mismatched receipt or a manipulative shop message blacklists the
  shop.
- More than 10 visits in the last 50 attempts activates the concentration
  penalty. If every available shop is capped, the least visited one remains
  eligible.
- Unknown shops get neutral priors rather than being treated as bad.
- Ties and exploration are deterministic from the item and memory length so a
  run can be explained later.

### Sealed-budget bargaining

The initial personal reference prices are the Round 2 mean deal prices:

```text
lamp 103, chair 184, bookshelf 265
```

As new honest deals arrive, the median of the last 20 personal deals replaces
the initial value. The LLM receives this reference and the transcript, not the
budget. The mandate guardian applies these rules:

1. approve any positive integer proposal at or below the budget;
2. during the first two rounds, replace an over-budget proposal with an
   affordable reference-based offer only when one exists;
3. otherwise refuse the proposal and walk away;
4. from the third offer onward, the exact ceiling may be used once;
5. never complete a checkout above the budget/open mandate.

### Seller price experiment

The base is the median of the last 20 deal prices for the item.

| item | volume share | volume anchor | margin anchor |
|---|---:|---:|---:|
| lamp | 80% | 1.03 | 1.08 |
| chair | 65% | 1.03 | 1.08 |
| bookshelf | 50% | 1.03 | 1.08 |

Both arms are clamped to `[reserve * 1.05, reserve * 1.50]`. A close rate below
70% or item review average below 3.5 stars applies the same 5% correction to
both arms. Thus the experiment changes the anchor, not the safety rules.

## Evidence in the kit log

Every routing, budget, and pricing decision prints one JSON object prefixed by
`[strategy]`. It contains the policy arm, public/personal features, proposal,
guard decision, and outcome, but not private keys. Exact budgets stay out of
strategy events; the ordinary kit may still log the order information it owns.

Primary measurements:

- seller settled rate, opening ask, final price, and price minus reserve;
- buyer settled rate, final price divided by personal reference, and rounds;
- maximum shop share in a rolling window of 50 attempts;
- settlement rejection reasons;
- below-reserve sales, over-budget offers, and receipt mismatches (all target
  zero).

## Round 3 procedure

1. Keep this Round 2 policy and its commit as the baseline.
2. Join with kit v3 and verify `round 3`, `[acp] paid`, `[acp] settled`, and
   `verified True` in the log.
3. Collect an initial Round 3 sample without changing parameters.
4. Change one parameter only after reading the observed settled/rejected
   outcomes, commit it separately, and collect the second sample.
5. Report both useful results and failed decisions. Do not delete or rewrite
   the original logs.
