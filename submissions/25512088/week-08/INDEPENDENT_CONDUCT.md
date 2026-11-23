# Round 3 independent-performance conduct

Deployment audit started 2026-11-05 16:18 KST. This file separates code, tests,
deployment and measured market outcome; passing tests are not called deployment.

## Buyer

- Implemented: the market order is used unchanged. `restrict_order` and its food
  exclusion are no longer in the execution path. Every acceptable SKU offered by
  every eligible non-self shop is enumerated. Ranked waves use at most six network
  calls at once and stop at the order/85-second deadline.
- Implemented: hard budget and grade-specific price caps, expiry and food
  `use_within_hours`, goods 6h warranty, registered signing key, signed task/SKU/
  amount, same-owner exclusion, one receipt and uncertain-payment stop remain.
- Implemented: ranking records absolute expected surplus and estimated valid
  checkout probability. Food 5% and goods 15% breakage are explicitly labelled
  `conservative-unmeasured`; no warranty recovery is credited before a matured
  cohort exists. These are ranking assumptions, not measured rates.
- Implemented: `no_stock`, `no_quote`, `condition_rejected`, `price_rejected`,
  `network_error` and `protocol_error` are separate durable events. Cooldown and
  new stock/order evidence can reopen a rival; uncertain payment cannot.
- Preserved: the post-payment hook writes the actual conditions and ledger-backed
  factual review. Forum output is factual price/condition or actual purchase data,
  rate-limited, and does not auto-vote or fabricate a review. Buyer weights are
  surplus 50%, forum 30%, deals 10%, posts 10%.

## Seller

- Implemented: goods-only TOP10 v3 runs from 180 seconds before each supply
  opening. It records 1h/6h exact grade+6h honest trades, identifiable distinct
  buyers, current-alive/recent-active buyer evidence, own inquiries and stockouts.
  Our buyer orders are labelled biased personal evidence, never obligations.
- Implemented: competitor same SKU and same-category 50-150% price-band
  substitute inventory both reduce likelihood. Ranking is estimated sale
  likelihood times net contribution/capital; high price is not a gate and
  negative expected contribution is never forced into TOP10.
- Implemented: A/B/C arrival probabilities are 70/20/10 and acquisition ranges
  are A 55-75%, B 40-60%, C 25-45%. The 15% hazard prior is explicitly
  conservative/unmeasured. Missing grade trade evidence is costed as unsold.
- Implemented: allocation uses current holdings, 1h/6h sell-through, unmet demand
  and protected capital. There is no fixed-four loop or per-SKU unit cap. The
  2.5m limit is current unsold inventory cost and reopens after official inventory
  removal. Cash protection remains 500k plus warranty, credits and unpaid rewards.
- Implemented: an unknown/5xx supply POST or an invalid 200 cost stops for durable
  reconciliation; GETs retain bounded retry. Pricing combines same-condition
  settlements, actual quote/sale conversion and same/substitute competition.
  Scarcity alone cannot raise price. Cost, benefit, warranty and prior lower quote
  protections remain.
- Preserved: Don Quixote, honest actual A/B/C receipt claim before reservation,
  signed receipts, unit reservation, no self-trade/double sale, real-stock OFFERS,
  existing discounts/referrals and frozen promises. Referral parsing by rivals is
  not assumed and campaign state is not reset.

## Evidence boundaries

- Live public reads attempted with 12-second bounds at 2026-11-05 16:12 KST for
  `/agent.md`, `/api/board`, `/api/shops`, `/api/ledger?after=-1000`,
  `/api/forum?limit=100`, and `/api/demand?limit=100`; the endpoint timed out from
  this shell. No zero-market conclusion is drawn.
- Connectivity recovered at 16:25 KST. Bounded reads then succeeded for all six
  sources: `agent.md` 17,386 bytes, board, shops (only one row in that transient
  snapshot and not ours), ledger `after=-50` with seq 2,834,032-2,834,081,
  forum `limit=20` with 20 rows, and demand `limit=20` (the server returned its
  full 140-row category list). The live rule says buyer ALL weights are surplus
  .5, buyer-role forum votes .3, deals .1 and visible buyer posts .1.
- The already running authenticated processes continued refreshing public-derived
  files: demand 16:18:46, forum 16:18:44 and market log 16:18:45 KST. Shops last
  refreshed 16:10:54 before the market began returning no-shop responses. The
  latest cached board source was 16:07:29 KST and showed the buyer alive.
- `strategy.json` v3 stores `selected_at`, `ledger_through`, `ledger_after=-1000`,
  returned row count and 900-second freshness bound. Pagination/freshness is
  evidence metadata, not a completeness claim.
- Tested: unit/integration tests cover food and complete-SKU restoration,
  alternative shops/SKUs, conservative expected surplus, caps and certificate
  checks, uncertain stop, outcome taxonomy, sold inventory capital release,
  active/distinct demand, substitute competition, positive-net allocation,
  read failure recovery, honest claims and protected pricing.
- Deployment and measured outcome are recorded only after process restart and
  fresh worker/order/card evidence below. No fake purchase is issued.

## Deployment record

- Buyer parent PID 76006 remained attached to its original terminal and loads a
  fresh `buyer_r3.py` for each order, so no parent restart was needed. At 16:20 it
  enumerated multiple shops/SKUs for a food order instead of emitting the old
  strategic-stock skip. At 16:21 order `o-3e95b4dcc1bf` (food, 84h required)
  bought `Gac0c1622` B grade for 15,984, verified the certificate and settled at
  ledger #2833035; the factual review was accepted. This proves loading and one
  real food checkout, not performance improvement.
- Seller parent PID 78645, proxy and tunnel were preserved. Its child exited on
  SIGTERM and the existing watchdog loaded the current code as PID 58591 at
  16:24:48. The public card at the existing tunnel reports Don Quixote, nonfood,
  honest grades, cert, signed receipt and 6h warranty. A read-only public `OFFERS`
  call returned actual A-grade stock with SKU, price, warranty and instructions.
  Local inventory was fresh and had 15 units costing 294,248 with 6,083,669 cash.
- The seller spawned v3 procurement worker PID 58594 using the required week-06
  venv. It recovered after two empty registry snapshots, then after three
  market-side public-card validation timeouts. At epoch 1793863571 it logged
  `started`; subsequent `waiting_selection` events target select_at 1793864341.
  No supply POST was made and no fake purchase was forced.
- The next official supply window was more than three minutes away when checked,
  so `snapshot()` correctly returned no early selection. Registry/card access has
  recovered and the worker is operational, but a real v3 TOP10 and any resulting
  purchase remain unmeasured until `select_at`. Code tests, a waiting worker and
  public card/OFFERS are deployment evidence, not sales-performance evidence.
- No hourly automation, email, fake forum content, vote or test purchase was
  created. Existing SQLite, signed reservations, keys and referral/discount
  promises were retained.

## 2026-11-05 final verification supplement

- Fresh `/api/board` owner mapping is required per buyer order. Same-owner aliases (case insensitive), unknown owners and absent shops are excluded. If our owner cannot be verified, no purchase is attempted. Verified house shops remain eligible. Ownership regression tests pass.
- Full discovery: 101 tests, 98 passed, 3 legacy `test_market_strategy` failures. Isolated baseline f4e91c2 plus unchanged untracked legacy modules reproduces exactly the same three failures. No user legacy file was edited. See logs/independent-final-tests-venv-20261105.log and logs/independent-legacy-baseline-venv-20261105.log. An initial verification runner mistakenly resolved the venv symlink and lacked dependencies; its failed logs are retained, and the corrected runner used sys.executable.
- At 16:35 KST seller PID58591, procurement58594 and original buyer/seller parents remain alive. Latest buyer paid10989 for Gac0c1622 gradeC at16:34:54, official settlement2836088, factual accepted review2836107. Buyer code is loaded per order, with no parent restart. Procurement still waiting for select_at1793864341; no claim of new TOP10 purchase or profit improvement yet.
- Automatic acceptance remains unverified: workspace digest revalidation failed without identifying changed paths; automatic followup could not create a clean worktree due existing untracked files. Narrow owner fix and baseline verification were completed directly afterward. Unit test success is not automatic acceptance or measured profitability.
