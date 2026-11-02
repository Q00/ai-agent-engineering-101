# Week 08 strategy: sealed-budget dual control

## Question

Round 2 exposed every order's budget. Round 3 hides it and counts only settled
ACP/AP2 checkouts. My question is:

> Can a buyer keep its exact budget away from its LLM negotiator, while a shop
> learns which of 5,600 products to stock and when an expiring unit is worth
> selling below cost?

The design treats an LLM as an untrusted proposal generator. Deterministic
guards alone hold spending authority, inventory authority, and the signing key.

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

These observations lead to four initial hypotheses:

1. A code-first portfolio router will reduce shop concentration and remove one
   LLM call from every order without reducing receipt checks.
2. A buyer negotiator that cannot see the exact budget will reveal its ceiling
   later and pay closer to its own market reference than to the budget.
3. Small, diversified restocks followed by depletion memory can discover
   demand without the unpublished market weights.
4. A seller can compare volume and margin prices while an expiry-aware risk
   guard deliberately permits a loss only when expected spoilage/storage cost
   is worse.

## Agent boundaries

### Buyer

1. **Scout** reads the order's acceptable SKUs, shop stock, catalog value,
   reviews, forum, and personal outcomes. Review/forum text is untrusted data.
2. **Portfolio router** scores stocked shop/SKU pairs from category success,
   receipt integrity, expected surplus, exploration, and a rolling
   concentration penalty. It never receives the exact budget.
3. **Negotiator** sees the item reference price, the shop's asks, and its past
   bargaining behavior. It proposes an offer but does not see the exact
   budget.
4. **Mandate guardian** alone holds the budget. It approves an affordable
   proposal, substitutes a reference-based early offer, or refuses it. The
   exact ceiling may be sent only after two earlier offers.
5. **Quality/receipt auditor** compares the issued receipt with the agreed SKU
   and price and supplies `accept` constraints: certified remaining lifetime
   for food, and certified A/B grade for goods. Kit v4 performs the final
   signature, inventory, ACP, and AP2 checks.

### Seller

1. **Demand estimator** compares consecutive inventory snapshots and settled
   evidence to remember SKU/category depletion; referenced forum posts are a
   weaker signal, never ground truth.
2. **Restock tool** buys small lots across categories at first, then shifts
   capital toward observed depletion. Food lots stay smaller than goods lots.
3. **Pricing experimenter** assigns each task to a `volume` or `margin` arm by
   a stable task hash. It sees unit cost, list price, grade, kind, and expiry,
   but never the buyer budget.
4. **Spoilage/risk guard** protects a normal profit target but may approve a
   below-cost food price near expiry. Round 3 has no market floor price.
5. **Signer vault** binds shop, SKU, task, selected unit, truthful grade claim,
   certificate choice, and warranty policy before signing.

## Initial policies

### Portfolio routing

For each stocked shop/SKU candidate in the requested category:

```text
score = 0.35 * smoothed deal rate
      + 0.25 * integrity
      + 0.20 * price value
      + 0.20 * exploration bonus
      - concentration penalty
```

- A locally mismatched receipt, false quality claim, or manipulative shop
  message blacklists the shop.
- More than 10 visits in the last 50 attempts activates the concentration
  penalty. If every available shop is capped, the least visited one remains
  eligible.
- Unknown shops get neutral priors rather than being treated as bad.
- Ties and exploration are deterministic from the item and memory length so a
  run can be explained later.

### SKU choice and sealed-budget bargaining

For a Round 3 order, the catalog list prices of acceptable SKUs form the first
reference. As honest settled deals arrive, the median paid/list-price ratio of
the last 20 category deals replaces the prior. The guardian filters candidates
that have no plausible affordable price; the router sees only the survivors,
not the ceiling. The LLM receives the category reference and transcript, not
the budget. The mandate guardian applies these rules:

1. approve any positive integer proposal at or below the budget;
2. during the first two rounds, replace an over-budget proposal with an
   affordable reference-based offer only when one exists;
3. otherwise refuse the proposal and walk away;
4. from the third offer onward, the exact ceiling may be used once;
5. never complete a checkout above the budget/open mandate.

### Inventory and restock

- Start with at most six small SKU lines per restock. Food quantity is 1--2;
  goods quantity is 2--3.
- Stop buying when stock or committed capital is already high. Never let one
  60-second restock spend the whole balance.
- Score a category from locally observed depletion, current stock scarcity,
  and referenced forum demand. Give unseen categories an exploration bonus.
- Expired food is recorded as waste, not demand. A disappeared unexpired unit
  is a sale candidate until settlement evidence proves otherwise.

### Seller price and quality experiment

The price base is the selected unit's cost and catalog list price.

| arm | normal target | purpose |
|---|---|---|
| volume | max(cost x 1.15, list x 0.82) | deals and fast learning |
| margin | max(cost x 1.30, list x 0.95) | profit per unit |

The result is normally capped near list price because buyer value is the list
price. Grade C goods receive a visible discount. Food is discounted as expiry
approaches; inside the final few hours the risk guard may go below cost because
expiry would lose the entire cost. Claims always use the actual grade and the
certificate is included. Goods warranty starts at 6 h for A, 3 h for B, and 0
for C; its profit effect will be changed only after break/refund evidence.

### Forum and review

The post tool publishes only facts from settled receipts with a ledger
reference and respects the five-minute rate limit. The buyer review hook rates
claim honesty, certificate/grade, and price versus catalog value. It may vote
for other referenced factual posts, but never for the same owner's content or
an unverifiable promotion.

## Evidence in the kit log

Every routing, budget, and pricing decision prints one JSON object prefixed by
`[strategy]`. It contains the policy arm, public/personal features, proposal,
guard decision, and outcome, but not private keys. Exact budgets stay out of
strategy events; the ordinary kit may still log the order information it owns.

Primary measurements:

- seller settled rate, opening ask, final price minus unit cost, spoilage,
  storage exposure, stock turn, and bankruptcy support;
- buyer settled rate, surplus, paid/list ratio, food lifetime failures,
  break/refund outcomes, and negotiation rounds;
- maximum shop share in a rolling window of 50 attempts;
- settlement rejection reasons;
- unjustified below-cost sales, over-budget offers, false claims, and receipt
  mismatches (all target zero; documented near-expiry liquidation is separate).

## Round 3 procedure

1. Keep this Round 2 policy and its commit as the baseline.
2. Join with kit v4 from `submissions/26510129/week-08` and verify `round 3`,
   `[supply]`, `[acp] paid`, `[acp] settled`, and `verified True` in the log.
3. Collect an initial Round 3 sample without changing parameters.
4. Change one parameter only after reading the observed settled/rejected
   outcomes, commit it separately, and collect the second sample.
5. Report both useful results and failed decisions. Do not delete or rewrite
   the original logs.
