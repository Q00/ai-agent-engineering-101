# Objective feedback v16 — Shop / Buyer

The objective is the official Shop ALL plus Buyer ALL. Inventory, deal count,
latency and API usage are means, not independent optimization targets. Current
rules weight Shop Book/revenue/stars 0.5/0.3/0.2 and Buyer Saved/forum/deals/posts
0.5/0.3/0.1/0.1. Scenario rank progress is not an exact official scoring oracle.

## Verified breaks in v15

- Research context was attached after numerical candidate selection. Fast Main
  execution returned that numerical choice; reading a summary was not actuation.
- One `approve_trial` signal coupled unproven strategy experiments to ordinary
  operating responses, including responses for the other role.
- Contract failure memory was exact-SKU scoped. Eight failed sibling SKU paths
  could therefore fail to qualify for an exact-SKU pause.
- Own paid evidence was required for deep stock. Thin initial stock could itself
  prevent the paid evidence needed for expansion.
- Research saw current competitor levels but not explicit matched peer trends
  or outcome feedback showing which operating policy Main actually used.

## Implemented interaction

1. A background market observer measures external 5/20/60/360-minute settlements,
   includes established and emerging SKUs, counts distinct recent buyers, and
   excludes our own/same-owner Buyer. Realized purchases are observed demand
   proxies, not hidden orders or private preferences.
2. `feedback.py` compares matched Shop/Buyer peers against its own earlier
   observations, records supply blocks, stockouts, closed-order conversion,
   above-cap quotes, objective thresholds and abrupt market-volume changes.
3. The LLM analyst and independent critic see these facts, the profile catalog,
   execution receipts and mature outcomes. They separately authorize bounded
   `operating_actions`; `approve_trial` continues to govern experimental proposals.
   Operating authorization is the intersection of both agents' approved actions.
4. A typed, checksummed operating packet applies before candidate generation.
   Shop demand horizon and SKU trend scenarios, or Buyer category-frontier routing,
   can actually change candidates. Main still selects by the current objective.
5. Main records its actual selected candidate, policy ID, expected component
   changes, order/task and preview status. The observer links these records to
   paid Shop tasks, closed Buyer orders and later canonical mature Buyer quality.
   Sparse evidence never becomes a claim of verified economic improvement.
6. Meta state records applied patches and specific waiting reasons. Research can
   refresh after a regime/guard change, with a five-minute minimum interval;
   normal reasoning remains about every twenty minutes and before supply opens.

## Inventory sizing

Competitor stock quantities are context, not a purchase target. Quantities use
obtainable SKU arrival scenarios, current holdings, supply interval, costs,
expected Book/revenue, grade/warranty/refund scenarios and cash obligations.

Reviewed stock coverage can extend the coverage horizon up to three supply
cycles for SKUs supported by repeat own paid demand. During a detected volume
shock, the extension is narrowed. A bounded trend factor uses recent 20-minute
versus preceding 40-minute external activity.

At least 20 external payments in one hour, including at least 10 in the latest
20 minutes from at least three distinct buyers, can qualify an initial batch
without three prior own sales. This external-only qualification permits up to
six units per SKU initially. Repeated own paid demand can use the legal twenty
units per line. These are candidate limits, not instructions to buy that quantity:
the marginal objective and affordability checks still decide every unit.

The total inventory ceiling can expand within the existing bounded 240-unit
envelope when the current ceiling actually binds. No rule forces cash spending
or copies another Shop's inventory. The global ceiling is not proven optimal.

## Buyer routing and quality

Category-frontier memory requires at least eight distinct above-cap failed orders
over at least three SKUs with the same stable seller identity, category, grade and
warranty in twenty minutes. Technical failures do not count as price refusals.
Improved public terms or a later actual paid recovery release the pause. Timed
and randomized recovery probes remain; a paused category cannot consume every
sibling-SKU route on every order. Existing safe price caps and receipt checks
are retained.

Sparse challengers can receive up to 30% of future randomized goods assignments.
Assignments stay frozen. Inverse assignment propensity weighting and effective
sample checks account for changing allocation. Mature 6h+10m cohorts, at least
20 paid outcomes per arm, uncertainty and latency gates still govern promotion.

The rating model uses only explicitly linked paid reviews with verified accounts.
It trains on the earlier 70% and tests the later 30%, with account-balanced loss.
It remains shadow unless sample/account/coverage checks pass and holdout MSE
beats the account-balanced baseline by at least 5%. An active model adds a small,
uncertainty-discounted rating planning surrogate; it does not claim to reproduce
the official `stars_rank` mapping or a causal effect of lowering a price.

## Context, expiry and verification

Main reads a small typed packet, not research transcripts. Operating packets
expire after 150 seconds and require fresh board/feed and compatible rules.
Buyer policy is frozen per order, including checkout recovery. Price, current
inventory, certificates, signatures, reservations and payments remain live checks.
Main-to-research feedback keeps totals plus four cohorts per measure; full raw
records remain in the journal. Paid reasoning inputs have a 32 KB bound.

- Final isolated regression: **350 passed**, one existing Starlette warning.
- Frozen legacy cases: all decision outputs identical with the new operating
  path inactive. This is compatibility evidence, not an economic lift claim.
- Frozen preparation replay: restock foreground 3.609 s → 0.615 s;
  Buyer route lookup 0.160 s → 0.024 s, with identical outputs in that replay.
- Real analyst/critic API calls validated the schema and separate operating
  authorization. The synthetic example authorized stock coverage while declining
  economic promotion. No smoke test made market transactions.
- Source release uses a fixed manifest, hashes, safe Buyer drain, source backup
  and atomic replacement. Credentials, accounts, market balances and histories
  are preserved. No daily API cost cap was introduced.

Release candidate: `logs/v16-release-candidate-20261111T085550663430Z.json`.
Tests: `logs/stage-v16-full-regression-05.txt`.
Latency: `logs/v16-preparation-benchmark-20261111T083057053160Z.json`.
Model contract: `logs/v16-live-model-smoke-20261111T085437631171Z.json`.

Sustained market connectivity and economic superiority require live observation;
unit tests, predictions and a successful registration are not proof of either.
