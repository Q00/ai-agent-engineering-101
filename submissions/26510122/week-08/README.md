# Week 08 shop: model decisions with inventory calculations

제출 보고서: [REPORT.md](REPORT.md) · [구조 그림](architecture.pdf) · [LaTeX 원본](architecture.tex). 결과는 2026-11-17 19:34 KST 관측값이며 시장 마감 순위와 구분한다.


Windows 로컬에서 이어서 작업: [ops/LOCAL_HANDOFF.md](ops/LOCAL_HANDOFF.md).
현재 실행 구조·사용자 의도·확인 순서·개인 대시보드 실행법을 먼저 읽는다.

Current operation and Linux migration: [ops/HOME_SERVER.md](ops/HOME_SERVER.md).
This runbook describes the live plain buyer/seller, private state handoff, and
Linux user services. Historical policy changes and experiments remain below.

Supply-window execution now uses `procurement_scheduler.py`. A supervised worker
prepares a model-approved portfolio during the four minutes before a published
window; this never reserves cash or emits purchases. Approvals are bounded to
ten minutes and their intended window, with catalog/policy versions and a content
digest. The kit hook watches fresh local inventory every two seconds (the kit
refreshes it about every ten), instead of waiting for the next minute's callback.
When the window opens it performs fresh cash/exposure, pending, target/category,
catalog, rolling exploration budget and time checks, then returns the kit's normal
buy protocol without another model call. No market credentials are given to the
planner; authenticated supply and 429 handling stay in the existing kit.

Preparation and execution use different locks; a slow preparation cannot block
an already prepared execution. The window claim and pending reservations precede
emission, so process restarts/unknown supply are not retried. Changed/expired
approvals are invalidated with a reason. The portfolio now respects the kit's
actual ten-line limit rather than reserving twelve lines it cannot all execute.
Portfolio policy version 13 supersedes old prepared plans, retaining existing
inventory, unresolved intents and risk bounds. Logs distinguish preparation time,
approval age and fast execution time; the seller supervisor reports `procurement_pid`.

Decision audit (2026-11-06): seller quote/negotiation and buyer shop selection/
counteroffers now have per-turn `decision_id`s, grouped by seller task or buyer
issued order (`episode_id`). Buyer shop attempts also have separate `attempt_id`s;
follow-up decisions retain their parent decision. The configured model, scoring
contract, runtime policy and hash-verified frozen source commit accompany them.
`decision_proposed` preserves the model/fallback choice; `decision_guard` names a
correction; `decision_executed` records the applied selection, protocol emission,
successful HTTP offer, blocked action or failed execution. Seller protocol emission
means the task updater accepted it, not that a buyer received it or paid. No extra
model calls or price/eligibility policy changes are introduced by this audit.

Existing role JSONL files remain append-only. Seller payment imports link by an
unambiguous signed-receipt unit **and price**. Buyer payment/review imports link by
exact task or a unique order/shop/SKU/receipt-price attempt; ambiguous/legacy
evidence remains explicitly unlinked rather than borrowing the current decision.
Receipts, reservation expiry and transport failures never become paid trades.
Payment-time evidence is not mature warranty-adjusted Book/Saved. The buyer SQLite
index adds episode/decision/attempt columns without replacing old rows or cursors.
Read an episode without modifying market state:

```sh
.venv/bin/python ops/decision_report.py --runtime state/seller-runtime --role seller --limit 5
.venv/bin/python ops/decision_report.py --runtime state/buyer-runtime --role buyer --limit 5
```

The report groups every audited turn contributing to a transaction and retains
separate attempt/outcome links. Raw model inputs/answers in the harness trace use
the same IDs. Historical records are not backfilled with invented model choices.
Procurement approvals now use the same decision/episode IDs. `portfolio_approval`
retains all validated lines and quantities; `portfolio_execution` records every
approved SKU, emitted quantity, cut quantity, binding reason and the live cash,
unit/category/target and supply-line checks. Planning/execution budgets, remaining
window time and elapsed deliberation are kept separately. An open published window
inside the existing 15-second safety margin is labelled `supply_window_closing_buffer`,
separately from an actually closed window; the safety margin itself is unchanged.
A later receipt/sale
or inventory arrival links back through the durable intent ID; partial arrivals
are recorded without releasing missing reservations. Legacy intents stay unlinked.
The report labels procurement arrival/resale separately from buyer payment.
The sequential replenishment-before-exploration priority and all guards remain.
Forum retains its publication-specific evidence.
`meta_effects.py` checks policy edits on a fixed inventory snapshot before the
model sees change options, and again on refreshed inventory before a proposed
change/rollback is applied. The check distinguishes effective spending capacity,
recovery-forced exploration, and changes to model guidance. A soft target clipped
at the same absolute cap, an epsilon edit overridden by recovery, or a window
budget edit above the same available-capital ceiling is excluded. Guidance-only
changes are explicitly labelled and never presented as extra spending capacity.
No-effect proposals become holds with the original model proposal and computed
reason preserved in JSONL/SQLite; policy versions/baselines and hard limits are
not reset. The existing two-hour/twenty-mature-sale gate remains. Meta also sees
recent approval/emission cuts and a decomposition of the binding spend limit.
Meta market inputs interleave distinct volume/revenue top-20 candidates before
the 20-row cut, retain source ranks, and disclose omitted/catalog-missing SKUs and
displayed counts separately from full ranked-candidate eligibility totals.

Current scoring alignment (2026-11-06, Patch 3.5): seller ALL uses Book/revenue/stars
percentiles at 0.5/0.3/0.2; buyer ALL uses Saved/forum/deals/posts at 0.5/0.3/0.1/0.1.
Buyer forum uses its raw Wilson rating (0..1); the other components use percentiles.
The buyer selector/forum editor receive this mixed-scale contract and ranking
version. Paid dislike power costs unit*(N-1)^2 in total (only the difference on
an increase); the authoritative unit is inventory.dislike_power_unit and the fee
reduces shop cash/Book. Existing posting schemas do not create votes. Actual shop
balance is rechecked before procurement, including fees already charged by market.
`market_scoring.py` owns both contracts. Buyer candidate ordering and model inputs
use normalized savings, not currency saved. Saved-at-cap is conditional on a usable
settled purchase, not an actual offer or final score; later failures/refunds remain
the market's authority. Epsilon exploration and all checkout conditions remain.

Book includes unsold stock at acquisition cost. Procurement at cost is Book-neutral;
storage, expiry, sale spread and warranty refunds change it. Seller quotes compare
sale economics with retained stock credit. Quantity tools include retained goods
credit less future holding; food survival is not invented. The old automatic cash
taper, two-hour purchase stop and zero terminal-stock assumption are removed.
Buying stops at the actual round end, and cash/holding/refund reserves, unresolved
reservations, absolute exposure and quantity limits still apply. Old approvals are
invalidated by policy version; existing inventory and pending intents are retained.
The meta reviewer rebases old Cash-policy evidence onto a new Book baseline before
retuning, with the same two-hour/twenty-mature-sale requirement. Its logs and DB keep
the scoring version and observed Book change; historical evidence is not rewritten.

The 2026-11-05 seller meta release removes the old 500,000 per-unit
candidate filter. Growth permits up to the live cash/exposure capacity per supply
window (absolute envelope 12,000,000), with 12,000,000 rolling novel exploration
reservations. Existing intent history is retained. New-lot reservations include
maximum 50% supply surge: 0.75 × 1.5 × catalog list price. Cached plans from the
old cost policy are invalidated; unresolved old intents retain conservative
reservations. The 65% soft inventory target, 75% exposure ceiling, 12,000,000
absolute stock exposure, 180 units and cash/holding buffer remain. The terminal
taper described in older experiments is superseded by the Book policy above.
New SKUs are single-unit trials, including affordable high-ticket goods.

`seller_meta.py` independently reviews paid outcomes, effective constraints and
market candidates every 30 minutes. The model may change one bounded runtime
parameter: window budget, target capital share, epsilon, or allocation focus.
At least two hours and twenty mature, distinct paid ledgers are needed after a
change before retuning. Policies expire after four hours unless reaffirmed;
operator defaults then apply. Market/contract rules and reservations cannot be
changed. Model decisions, before/after configurations and evidence are saved in
append-only logs and `state/seller-meta.sqlite3`. Transaction models still approve
every purchase; the meta reviewer never buys directly or edits source code.

Seller forum separates 96 root posts and 144 replies per rolling day, checks
for opportunities every 330 seconds, and spaces roots by ten minutes. Repeated
SKU and thread guards remain. Full opening-event terms accompany explicit
opening announcements instead of every product post. Roots are eligible every ten minutes;
relevant unanswered replies can use the gaps, respecting 330-second market spacing.
Product ad cooldown is ninety minutes. Buyer paid-evidence posts are eligible every
ten minutes, capped at 96 per rolling day, with a two-hour same-SKU cooldown.
`buyer_forum.py` publishes
plain, tool-rendered purchase notes independently of the issued-order buyer,
at most 96/day and ten minutes apart. A model selects/writes, a second model checks
claims, and the kit's publication acknowledgement is recorded separately from
queueing. No roleplay or extra buyer orders are enabled. The seller supervisor
manages meta and both forum workers independently; buyer kit/order state is kept.
Live activation and probes are evidenced by the dated rollout/health JSON logs;
local tests alone are not evidence of market profit improvement.

This is the first Round 3 shop and buyer implementation. It does not silently
switch the running week-07 services. Live settlement evidence is still needed.

## Decision path

1. The kit v4 writes `.market/inventory.json` and `catalog.json`. The seller
   offers only an in-stock unit, and temporarily holds a unit after signing a
   receipt so that another negotiation cannot promise it immediately.
   `seller_state.py` persists active negotiations, per-unit leases, purchase
   contexts and owner-scoped A2A tasks in `state/seller.sqlite3` (ignored by Git).
   Receipt leases commit atomically BEFORE the signed artifact is exposed and
   last ten minutes unless a confirmed settlement releases them. Epoch deadlines
   survive process restarts. Recent unsettled receipt logs migrate on first
   upgrade; no keys are copied into the DB. An interrupted artifact emission can
   replay the exact persisted receipt, after rechecking current conditions,
   without changing its unit, price or signature. Protocol task IDs and their
   original context IDs survive restarts, including clients that send only taskId.
2. `newsvendor_tool.py quote` calculates transparent A/B/C price anchors from
   the actual unit cost, list price, grade, expiry and storage charge. The
   initial A=1.0, B=0.8, C=0.6 multipliers are hypotheses, not lecture values.
3. `market_memory.py` keeps append-only `logs/shop-events.jsonl` and imports
   confirmed `[acp] settled` lines from the kit log. A signed receipt stays
   `RECEIPT_PENDING` until the kit reports a ledger settlement. Each model
   prompt gets a bounded summary of first-hand outcomes, including failed and
   unresolved negotiations.
   `inventory_observation.py` separately attributes exact SKU visits and stockouts,
   deduplicates tasks, and measures stock exposure only between snapshots at most
   120 seconds apart. Missing snapshots and ambiguous product requests remain
   unknown. A stocked-shop visit rate is a selected observation, not total demand;
   the public `demand.json` lists categories without demand weights.
4. The Codex harness chooses the physical unit, opening price and follow-up
   action. The quote candidates and memory are evidence for that choice. The
   seller records the model decision separately from the emitted ask/counter.
   Code checks that the selected unit still exists and the price is positive.
   `seller_requirements.py` retains the original request and three bounded
   nonnumeric messages, alongside explicit minimum expiry, acceptable grades,
   required warranty and certificate fields. This purchase context is saved per
   task, passed to every model turn, and retained after completion for inspection.
   Numeric price-only replies do not erase terms. Common explicit English/Korean
   numeric terms are parsed conservatively; unrecognized wording remains visible
   to the model rather than becoming an invented hard constraint. Mixed offers
   such as `800, but only if it lasts 84 hours` preserve both the price and term;
   ambiguous prices require clarification. Conditions are checked before quoting
   and immediately before exposing a receipt, including after slow model calls.
   New goods quotes include a six-hour market warranty in their total price.
   Food has no goods warranty; requests longer than six hours fail explicitly.
   Saved negotiations and issued receipts keep their original coverage terms.
   Pricing remains a model decision with refund scenarios and condition guards.
5. Before ordinary replenishment, `inventory_exploration.py` asks the model for
   a startup basket of 5–8 distinct SKUs, one unit each, including existing stock.
   The finite 250,000-credit allowance includes existing stock cost and reserves
   75% of list price per new intent (the documented maximum cost). It is an
   operator-authorized experiment, so a zero newsvendor recommendation does not
   veto every new SKU. Food needs at least 48 listed shelf hours; affordable
   goods are also candidates, with daily storage cost shown. The tool validates
   the basket cardinality, categories, SKU membership, cash and spend limits.
   It prepares the basket while supply is closed, then emits only during a
   confirmed open window with time for the kit's sequential requests. Unknown
   or failed intents retain their reservation and are never automatically
   retried. Saved state survives restarts and prevents restarting the allowance.
   Actual arrivals are recorded separately from intents. The startup allowance
   bounds exploration, not all subsequent evidence-based replenishment.

   After startup, `inventory_portfolio.py` persists one epsilon draw per supply
   window: 30% exploration, otherwise exploitation. Fewer than fifty stocked SKUs
   or inventory acquisition cost below the dynamic 65% soft allocation target
   overrides the draw to build inventory. The target is a model planning goal,
   not a requirement to spend irrespective of demand or risk. This is an operator-controlled mixture
   around a model policy, not a learned Q-value table; percentages describe window
   decisions, not units or spend. Exploration samples food/goods categories and
   proposes at most eight unowned SKUs, one unit each. The tool calculates an
   affordable minimum of up to five SKUs across up to four categories, including
   the random anchor, before asking the model; a concrete feasible basket
   witnesses these floors. Limited cash, category availability or window time
   can reduce them. The model is asked to prefer the maximum affordable batch,
   and can still hold for a concrete risk. The bounded candidate pool alternates
   food/goods, samples price bands and admits unit cost ceilings within live investable capacity,
   so medium-priced furniture is no longer excluded by the old 50,000 cutoff.
   A randomly sampled anchor
   (preferably without recent evidence) must be included in a buy, so the model
   cannot silently turn exploration into its familiar best sellers; it can hold
   for a concrete risk and chooses the rest of the basket. The model still approves every
   basket. Its growth ceiling envelope is 12,000,000 per window, clipped to live capacity and 12,000,000 over a rolling
   24 hours; these are worst-case intent reservations, not actual spend. Unknown
   outcomes retain spend and slot reservations. The same window is never redrawn
   or retried after a model failure, hold or ambiguous supply. Arrival evidence
   is logged separately. If exploration has no budget or candidates, that window
   delegates to the bounded exploitation planner instead of stopping all restock.
   `supply_reconciliation.py` also recognizes a just-sold physical unit through
   first-hand receipt/settlement records, even when it has already disappeared
   from current stock. A receipt proves arrival, not payment; settlement needs
   a ledger ID. Old units, earlier events and ambiguous repeated-SKU evidence
   cannot free an unknown reservation. Confirmed arrival/settlement releases
   pending slot/cost exposure while keeping the 24-hour exploration spend used.
   Reconciliation runs even with supply closed and is shared by cached plans.
   Existing intent reservations survive the budget increase; the update does not
   restart the 24-hour allowance or repeat a previously attempted window.

   A window can now approve BOTH replenishment and exploration in one model
   call. `stock_policy.py` identifies up to twelve maintenance SKU candidates,
   requiring at least two positive acquisition-spread settlements in 12 hours
   and positive total known acquisition spread. This includes moderate sellers;
   there is no additional one-sale-in-the-last-hour gate that drops slow movers.
   Costs must link to first-hand signed receipts; unknown costs, pending receipts
   and public sales alone do not establish this status. The model can select up
   to eight such SKUs and forecast-derived targets, alongside new SKUs with the
   remaining supply slots. Targets are no longer fixed at four or six units.
   The tool combines 1h/3h/12h settlement windows and paired in-stock exposure;
   it never divides all sales by an unrelated short exposure interval. Sparse
   periods fall back to longer windows, so an out-of-stock or unobserved hour
   does not automatically erase demand. Where adequate paired exposure exists,
   the conditional rate is shrunk toward the full-window observed sales rate.
   Demand is forecast for time until the next procurement opportunity: the
   published next opening when closed, or the nominal next 20-minute cycle
   when open, plus an explicit three-minute timing/procurement buffer.
   Poisson low/base/high scenarios use demand multipliers .8/1/1.25 and
   service levels .90/.95/.975 to calculate target stocks. These are planning
   hypotheses, not measured service guarantees. A zero-continuation newsvendor
   diagnostic exposes the cost/profit tradeoff without pretending that all
   unsold stock is actually discarded at the end of this short horizon.
   The model approves one of the calculated, budget/capacity-limited targets
   or holds. The original demand target remains visible when a budget clips it.
   An invalid model choice receives at most one correction call with the exact
   tool-calculated validation error (including spend totals), only while there
   is still enough window time. No supply intent exists before validation; a
   failed/ambiguous external purchase is never retried by this mechanism.
   The growth shared limit is twelve supply requests, live cash/exposure-limited worst-case spend, 180 total
   units and the existing cash/exposure limits. New-SKU exploration alone counts
   against the rolling 12,000,000 allowance; established replacements still obey
   shared cash/exposure limits. Exploration is optional in a maintenance batch
   when budget or capacity favors existing sellers; any exploratory picks must
   still include the sampled anchor. When no established seller qualifies,
   the prior up-to-eight-new-SKU basket and its affordable minimum remain.
   Fresh stock growth can only REDUCE the emitted replacement quantity, never
   increase it past the approved target. Multi-unit intents track observed and
   settled physical units separately: one observed unit does not clear a whole
   multi-unit reservation. Old receipts cannot be attached to a later acquisition.

   `replenishment_plan.inventory_risk` computes spendable capacity from cash and
   acquisition-cost exposure for both ordinary replenishment and exploration.
   During growth it preserves at least 600,000 cash plus a goods holding-cost
   buffer and caps live inventory cost plus unresolved intent ceilings at the
   smaller of 12,000,000 or 75% of cash plus live acquisition cost.
   This is an exposure limit, not a resale-value estimate. Both paths recalculate
   capacity after model approval, and cached plans also check it on every issue.

   The ordinary `restock` hook offers the model a bounded set of food AND goods SKUs from its own
   observations, recent public Round 3 settlements and category-balanced samples.
   The model proposes a SKU and demand scenario. The newsvendor tool calculates
   quantity candidates; a second model decision approves one complete plan
   (quantity, target stock, validity and total unit budget) or holds. The tool
   calculates both a tight budget and a repeat-sales budget, so the model sees
   that replacement authorization is distinct from buying stock up front.
   Public sales are labeled separately:
   they do not reveal unfilled demand or buyers' private budgets.
   An unproven SKU is capped at two units. A maintenance SKU's current forecast
   bounds its target, with a separate safety ceiling of 20 units per SKU.
   Sparse plans remain capped at two total units unless positive-spread sales
   establish the SKU. Total live stock is capped at 60 units. Both
   paths enforce at most 20 units per SKU and 24 per catalog category when
   adding stock; existing excess stock is retained for sale, without adding to
   it. Old plans and scenario caches cannot bypass the new policy. Goods use
   the documented grade mixture A=.70/B=.20/C=.10 (expected cost=.59 of list),
   a 24-hour review horizon, and a full day of 1% storage on leftover inventory.
   This holding approximation omits sold-unit holding and unknown warranty
   losses, which remain model-side risks rather than measured profit forecasts.
   `replenishment_plan.py` reuses that approval for 180 or 600 seconds within the
   fixed target, per-order quantity and total authorized quantity (at most 20).
   It reserves emitted intents before returning them, waits for a newer
   inventory snapshot before another intent, and checks the cash reserve again.
   Expiry, catalog/grade changes, three newly observed stockouts or a substantial
   observed visit-rate change require reassessment. Budget exhaustion respects
   the three-minute model cooldown; new risk evidence can bypass it.
   For ordinary budget/lease renewal, an unchanged scenario may be reused for at
   most 30 minutes, while the model still approves newly calculated quantities
   against current evidence. The approver can request `replan`, which emits no
   purchase and asks the next hook for a fresh scenario. Risk changes bypass
   this shortcut. New planning takes two model calls, stable renewal one, and
   valid plan execution zero.
   Inventory older than 30 seconds and failed model approvals emit no purchases.
6. `market_buyer_r3.py` selects only stocked, acceptable SKUs from other shops.
   Normally the model chooses a shop, SKU and first offer. A 10% epsilon branch
   fixes a random eligible shop/SKU for the model to price. `buyer_policy.py` guards the
   private budget and a provisional surplus margin. Food requires a certificate
   and enough shelf life for the user's use window plus a buffer. Goods require
   a certificate and grade A or B; a six-hour warranty is preferred, not a
   blanket requirement before observing market availability.
7. `buyer_r3.py` negotiates through A2A without disclosing the private mandate
   cap. The model chooses later offers; the code caps each offer and checks the
   signed receipt against the selected SKU, final offer and task. Kit v4 still
   verifies signatures, ACP checkout, AP2 mandates and the payment ceiling.
8. `buyer_memory.py` imports kit payments separately from pending receipts and
   remembers goods breakage by product name without blaming a particular shop;
   shop identity is the registry `name`, with changing URLs recorded as aliases.
   Legacy URL-only events are reused only when their identity is unambiguous;
   an order's recorded shop identity takes priority over a reused address.
   Corrected payment outcomes and quality records are joined by order/ledger
   rather than counted as separate purchases. The model sees aggregate outcomes,
   grade-specific paid-price ranges and at most three linked cases. Selection
   receives a shorter evidence brief plus labeled public-review examples, so
   tool ordering never hides the reason a shop was included.
   `review_r3.py` records settled grade/value and can publish a ledger-backed
   buyer forum note only when explicitly enabled. The independent
   `buyer_forum.py` worker handles current publishing from paid evidence, with
   30-minute spacing and without a minimum discount requirement. Buyer price shares (food 0.85, goods 0.75 of list) are starting
   hypotheses and need live calibration.

The newsvendor rule is `F(Q*) >= (p-c)/(p-v)` for a discrete demand forecast,
where `p` is expected sale price, `c` estimated purchase cost and `v` is a
model-estimated continuation value per leftover unit after the planning horizon,
bounded between zero and expected cost. It represents uncertain later net sales,
not a market buyback; unsupported or missing estimates use zero. Actual expiry
still means zero value. Incremental-profit calculations use the same estimate. The forecast horizon is the smaller of 24 hours and
one quarter of the catalog shelf life. Before supply, actual grade and cost
are unknown: `restock.py` uses the market's grade frequencies and cost-range
midpoints, which imply expected food cost of 0.5 times list price. The real
cost and grade from inventory replace that estimate when a unit arrives.
The model sees grade frequencies, cost midpoints and unvalidated price anchors
separately; it must estimate the new lot's mixed-grade sale price instead of
copying a current C-grade unit quote. Old cached scenarios are invalidated by
the policy version. Ordinary replenishment also waits for an open supply window.
The forecast mean cap (1.5 with sparse visits, otherwise 10 units per horizon)
is our conservative policy, not a published market demand limit. Both the
effective forecast and this cap are shown to the final model approver.

## Forced buyer exploration

`buyer_exploration.py` provides an operator-controlled epsilon mixture around
the existing model selection policy. `agent.json` sets `--epsilon 0.1`: with
10% probability the harness samples one distinct eligible shop-name/SKU pair
uniformly and fixes that candidate. The remaining probability uses the normal
model policy and its ranked shortlist. This is not a learned Q-table or proof
that the model implements a numerical greedy optimum.

Exploration draws from the **whole eligible pool**, before the normal 16-pair
and two-pairs-per-shop limits. Duplicate offers do not create extra entries.
The prompt and schema expose only the forced candidate; the code also prevents
a model override and preserves the target during a model failure. The model
chooses its opening price and can later negotiate or walk away. Exploration
does not force a purchase or override private budgets, the existing provisional
price shares, certificate/quality requirements, or final kit checkout checks.
Eligibility uses known catalog/stock facts; actual unit quality is still checked
at checkout. Exploring cannot discover preferences outside the order's allowed
SKUs or the current eligibility policy.

An exploration draw can pick a familiar candidate by chance. It is not a promise
to choose a different shop from the model, nor exactly one exploration in every
ten orders. A shop with more eligible SKUs has more pairs in the uniform pool;
this is pair-level, not equal-shop sampling. `--epsilon 0` disables exploration,
and `--epsilon 1` is useful for controlled tests. Invalid probabilities fail
validation. A single candidate is labeled `single_candidate` without a draw.

Append-only `selection_opportunities` events preserve the full eligible pool,
displayed IDs, order conditions, epsilon and random draw. `buyer_policy_selection`
records the effective choice plus the model's original candidate/reason/fallback.
They share a `selection_id` with dialogue and negotiation events. Attempts at
an alternative shop are labeled `fallback`, separately from the exploration
target; deadline-skipped attempts are explicit. Receipt-pending remains distinct
from kit payment/settlement. Future audits must join outcomes by order, stable
shop identity and SKU, not label any later payment on the order as exploration
success. The logged `epsilon / pool_size` is only the uniform-exploration
component, not the full propensity of the stochastic model policy.

This records evidence for a later meta-harness review without storing a permanent
inferred taste rule. Epsilon does not adapt automatically yet. Comparisons need
actual paid results and usable value; missing alternative-shop outcomes are
unknown. Current order budgets and candidate conditions remain private logs.

All 70 tests pass, including forced selection outside the shortlist, resistance
to model overrides, model failure, unchanged price guards, payment attribution
and deadline skips. Two actual selector-model calls on synthetic candidates chose
the forced 18th pair versus a normal shortlist candidate; negotiation and
receipts were mocked, with no market payments. A seeded 10,000-draw RNG check
produced 999 exploration branches and covered all 18 pairs. This checks policy
mechanics, not profitability. See `logs/buyer-epsilon-model-probe-2026-11-03.log`
and `experiments/buyer_epsilon_probe.py`. Deployment remains paused as requested.

## Context construction

Each model call starts a fresh session. The application supplies current facts,
calculation results and a small historical view; it does not maintain an LLM
summary or a vector database. `context_compaction.py` makes deterministic views:

- Current inventory, cash, authorization caps and tool candidates remain separate
  from historical observations and temporary forecast assumptions.
- Older trades contribute exact outcome counts and paid-price ranges by grade.
  At most three relevant same-grade cases plus two other-grade cases retain
  opening/settled grade, cost, historical hours left, price and final outcome.
  Offer/counter arrays retain their last four values and original counts.
- Seller opening prompts share one memory per SKU/grade instead of repeating it
  per physical unit. Restock links and filters candidate-SKU trades before
  choosing cases, so inventory snapshots cannot push settlements out of view.
- A buyer sees a short first-hand seller evidence brief AND separately labeled
  public-review examples. Current negotiation context keeps four short messages;
  original messages are appended to the event log, including messages observed
  before a network failure. Model explanations are not promoted into rules.

Budgets are serialized UTF-8 bytes, not token estimates: 3,200 per buyer memory,
1,600 per buyer-selection brief, 3,600 per seller SKU/grade (a shared 12,000-byte
opening-memory allowance), and 3,000 for restock trade history. Buyer selection
keeps its full prompt within 32,000 bytes by removing the lowest-priority
optional candidates; every displayed candidate retains its payment cap.
The harness logs the actual serialized prompt byte count. Event files are not
rewritten or truncated by constructing these views.

On synthetic long-history fixtures, seller memory went from 12,032 to 1,951
bytes and buyer memory from 36,708 to 955 bytes. Two real model probes separated
40 paid trades from one pending receipt, retained the C-grade/two-hour discount
condition, and selected confirmed payment evidence over higher public stars.
These are context-size and evidence-reading checks, not market-profit results.

## Tentative buyer-specific opening prices

`counterparty_memory.py` calculates evidence, and the seller model chooses the
quote. A buyer is never permanently labeled by a model-generated rule. Eligible
evidence currently means a locally confirmed settlement within 14 days, the
same SKU and grade, and (for food) the same expiry bucket: at most three hours
remaining versus longer. Unknown expiry is excluded for food. One offer must
have paid at least 98% of the opening ask, with no seller counteroffer. A
"high" payment exceeds the median normalized price of at least three comparable
other-buyer settlements by 5%; without these, the tool explicitly uses the
unvalidated grade prior. These thresholds are experimental policy choices.

One such settlement suggests the historical normalized price and a 5% higher
new quote; repeated evidence also adds a 10% candidate. For example, a previous
120 payment on an unchanged list-price-100 A-grade item gives 120/126, then
120/126/132 after repetition. The model also sees bargained/lower payments and
the most recent probe outcome. A rejected/cancelled probe or a discounted probe
settlement blocks another uplift pending new comparable confirmed evidence;
a recent open/pending probe waits 30 minutes. Observations age out after 14
days. These are optional candidates under the existing twice-list-price guard,
and never change an already agreed receipt.

Anonymous A2A messages do not identify a buyer. `buyer_identity.py` offers
optional `params.metadata.buyer_identity`: the existing market buyer key signs
the recipient, message ID, task ID, full text and timestamp. The supplied public
key must also verify a past official `verified=true` result's buyer signature
under that name. A self-claimed name or self-signed message alone is insufficient.
The sender reuses `buyer_key.pem`; no private key is sent or logged. Other buyers
must send compatible metadata for personalized pricing to apply; our sender
support cannot identify anonymous clients automatically.

Only a public result matching our local signed receipt links its buyer name to
that task; a result alone does not establish Round 3 payment. The public-ledger
reader starts with the latest 500 rows, polls at most once a minute with a
three-second timeout, and caches 256 proofs/results in ignored `state/`. Cached
results can reconcile late receipts during an outage. Unknown ledger schemas
are ignored; missing historical coverage leaves identity/evidence unknown.

The 58-test suite covers signature/context tampering, replay, late linkage,
pending receipts, comparable conditions, bargaining and failed probes. Four
real model calls on synthetic seller prompts are recorded in
`logs/buyer-price-model-probe-2026-11-03.log`. They check evidence reading, not
live buyer elasticity or profit. This implementation has not been deployed to
the running shop or validated with live Round 3 personalized trades.

## Buyer life prototype — internal only

The current design is **one buyer, one persistent resident, one world**.
`buyer_world.py` supplies the surrounding fictional situation as external facts
only, for example "There is a lab meeting tomorrow." The resident life agent
alone interprets its significance, feelings, needs and response. The director
has no choice-tension, emotion, preference, recommendation or purchase-action
field. It sees identity/background and prior scene titles, not hunger, tastes,
owned-product opportunities or a desired shopping outcome.

Only one scene is active. Repeated calls reuse it; after expiry its facts are
archived and another scene may be introduced. Expiry is a narrative transition,
not proof that a meeting happened or a market order failed. A director failure
leaves ordinary life available, with a retry cooldown. Scenes cannot rewrite
identity or ownership and never enter commercial buying prompts. The optional
`--life-only` planner invokes this director separately from buying/restocking.
All scenes, reactions and draft posts remain internal.

Earlier three-person role samples are archived **alternative fixtures**, not
three running buyers or the requested world director. `buyer_role.py` now serves
only the initial resident background; a scene does not create a new character.
The original scene prototype included a choice-tension field. That field was
removed after the user's clarification that the director should provide facts
and leave judgment entirely to the resident.


Facts-only director check (November 4): 129 tests passed. An isolated world
with one saved resident received two sequential model-generated factual scenes;
both kept the same identity and zero invented possessions. For example, the
director stated only that a lab meeting was scheduled tomorrow afternoon, and
the resident independently chose its response. Exactly one scene remained
active and one expired scene was archived. No market API call, buyer activation,
forum publication or live configuration/persona change occurred. The earlier
choice-tension prototype and failed attempts are preserved separately. Results:
`logs/buyer-world-facts-only-probe-2026-11-04.json`; reproduction:

```bash
.venv/bin/python experiments/buyer_world_probe.py
```

Three-situation simulation (November 4): one saved resident received the
curated facts "first lab-meeting presenter", "lab meeting cancelled", and
"two classmates visiting Friday" in sequence. Starting food/furniture and
settlement IDs were explicitly synthetic fixtures using local catalog names.
The first run exposed a context bug: optional public/needs text was retained
while ready-to-cook tool output was removed. The life context now drops optional
forum/needs/history before current cooking/ownership information; draft cooldown
and recent topic IDs are visible so the planner can avoid repeated rejected
drafts. Regression checks bring the suite to 131 passing tests.

In the corrected trajectory, the resident cooked the existing oat/milk bowl and
placed its owned desk/sofa after the presentation-order fact (hunger 60 to 28).
After cancellation it drafted an oats request; after the visitor fact it drafted
a milk request. No actions were rejected in that corrected run. The last two
responses showed no clear situation-specific plan change; food depletion and
the original domestic goal still dominated. This is a useful limitation, not a
claim of rich situational understanding. One trajectory cannot isolate causal
scene effects or trading performance. No real purchase, publication, buyer
activation or live state change occurred. Raw initial and corrected runs are
both preserved. Reproduce with:

```bash
.venv/bin/python experiments/buyer_situation_sim.py \
  --output logs/buyer-situation-simulation-corrected-2026-11-04.json
```

Resident plan memory (November 4): the life agent now authors a short
interpretation, intention and up to three next steps in the SAME decision call.
It explicitly starts/keeps/revises/finishes/cancels its provisional plan and
references the current scene, previous plan ID and action indexes. A new scene
requires an explicit assessment, but keeping an unchanged plan is allowed.
The director still supplies facts only. No scene-specific action rule was added.

The current plan stays on disk; a maximum 1,400-byte planning view supplies it,
recent scene facts and accepted/rejected action feedback to the next life call.
Raw interpretations and revisions remain in the append-only event log, labeled
resident-authored hypotheses rather than external facts or permanent rules.
Tools check references, preserve intention/steps on `keep`, link selected actions
to actual tool outcomes and reject stale plans without undoing valid cooking or
placement. Finishing a plan is the resident's self-report, not proof of an
academic achievement. The default commercial observer never receives the plan.

139 unit tests passed. The same three-situation simulation used one resident,
the same synthetic starting inventory and three real model calls. It cooked and
placed a desk/sofa after the first-presentation fact; after cancellation it
revised its plan and waited; after the visitor fact it revised again and placed
the owned stool. All proposed physical actions passed tool validation. This is
one coherent trajectory, not a causal benchmark or evidence of better trading.
The separate planning view stayed inside the existing 7,500-byte life context.
No real purchase, public post, buyer activation or live configuration change
occurred. Results: `logs/buyer-situation-planning-2026-11-04.json`.

Extended internal rollout (November 4): one resident starts with no possessions
and receives nine scripted market orders over a fictional 33.5-hour Thursday /
Friday. Real buyer selection and counteroffer models negotiate against two local
rule-based shops. `experiments/offline_market.py` uses fresh in-memory Ed25519
keys plus the read-only official kit's receipt signature and checkout-quality
functions. It never uses live credentials or A2A/AP2/market network settlement.
Only successful simulated payments call the ordinary review/possession import.
Prices, available items, the order schedule and failure injections are fixtures,
not measured market demand. The character remains in commercial observer mode:
the story did not cause the scripted product orders.

Two full replays were collected. In the corrected replay all nine negotiations
produced receipts, seven paid and two were blocked (insufficient food shelf life
and a checkout price different from the signed receipt). Gross simulated spend
was 223,382 credits; a goods failure two hours after purchase refunded 145,800
under its six-hour warranty, leaving net spend 77,582. The owned sofa was marked
broken and its layout removed; the resident placed the subsequently acquired
stool. Milk, oats and caramel were actually consumed by the virtual recipe.
Tomato juice remained unused after the last step: recipe adaptation and social
interaction are still narrow, despite the meaningful purchase/use/failure loop.

The first replay exposed ambiguous tool feedback: `request: accepted` looked
like a real external request. Feedback now reports `internal_draft_only`, and
the prompt/draft state explicitly say that no person receives it and no order
or delivery is created. A regression test verifies this; 145 tests pass.
Two original/neutral replays of the same saved table-purchase state both placed
the table. This supports classifying furnishing as standing-goal/new-ownership
behavior; it does not establish that the academic event caused it. A single
stochastic pair is not a causal benchmark. There were 52 real model calls in
total across two full runs and that control pair; exact token billing was not
measured. Every protected live config/persona/outbox digest stayed unchanged.
No deployment, buyer activation, real purchase, public post or push occurred.

Collected evidence:

- `logs/buyer-life-rollout-2026-11-04.json`: first complete replay.
- `logs/buyer-life-rollout-corrected-2026-11-04.json`: corrected replay with state snapshots and full transaction/use/refund trace.
- `logs/buyer-life-rollout-corrected-2026-11-04.csv`: one row per order; refused checkouts charge zero.
- `logs/buyer-life-rollout-control-2026-11-04.json`: identical-state, current-fact-only control pair.
- `logs/buyer-life-rollout-review-2026-11-04.json`: measured outcomes and limits.
- Ignored personal `flow/simulation/buyer-life-rollout.html`: self-contained timeline and per-item use table. Browser QA checks filters, two blocked payments, one refund event and mobile layout; no external page resources.

```bash
.venv/bin/python experiments/buyer_life_rollout.py --output logs/my-new-rollout.json
.venv/bin/python experiments/buyer_rollout_control.py \
  --input logs/my-new-rollout.json --output logs/my-new-control.json
.venv/bin/python experiments/render_life_rollout.py \
  --input logs/my-new-rollout.json --output flow/simulation/my-rollout.html \
  --csv logs/my-new-rollout.csv
```

The fictional resident is a model-generated persona whose tastes may evolve
with recorded experience. This is an optional layer around issued market orders,
not a replacement ordering system. The default `agent.json` does not enable it;
the running seller uses its existing frozen release and has no buyer hooks.
No roleplay deployment, buyer activation, forum publication or Git push is part
of this internal check.

New personas share a **fictional graduate-student** background. `buyer_role.py`
is an initial-background helper: in the existing one-time persona model
call it selects master's/doctoral/integrated study, research area and stage,
housing, routines, one current academic pressure, a small domestic goal, its
observable success condition, and a personal tradeoff. An internal gallery can
pass up to three compact prior fictional designs so the model can contrast
program, housing, names and goals instead of repeating the same character. Food/decor preferences,
recipes and virtual rooms are generated coherently in the same response. It
does not add a second model call to initialization or to a market order.

The background initializer is not a live market participant and cannot grant purchase
permission, create possessions or publish. Graduate students are not forced
into a poverty/all-nighters/instant-noodles stereotype. Pressures such as a
seminar or paper deadline are narrative context, not simulated research output.
Domestic goals must be achievable through the meal/home game; a goal and its
success condition are saved and shown to the life planner, but automatic goal
completion scoring is not implemented. Existing personas are retained exactly;
the new default does not silently rewrite their identity as a student.

The later life planner sees a compact role brief along with actual possessions
and recorded experience. It chooses cooking, placement, waits or local drafts.
The full role stays on disk and survives restarts. Role/study/pressure fields
never enter commercial selection advice; the default observer mode still leaves
market purchasing unchanged. Tastes may evolve only through the existing
experience-linked revision mechanism.

Graduate-role check (November 4): 123 unit tests passed. The initial three
independent designs all converged on doctoral studio residents with dinner-area
goals; their raw samples are preserved. After adding compact prior-fiction
contrasts and removing the goal examples that anchored every design to dinner,
a second internal sample produced doctoral / master's / integrated programs,
different names and living arrangements, and distinct placement/cooking goals.
One role-aware life tick was also exercised with no possessions and no invented
ownership. All three roles persisted across restart; no market API request,
buyer activation, forum publication or live/root persona change occurred.
These three examples are not a diversity benchmark. See
`logs/buyer-graduate-role-diversity-probe-2026-11-04.json` and reproduce with:

```bash
.venv/bin/python experiments/buyer_role_probe.py \
  --output logs/buyer-graduate-role-diversity-probe-2026-11-04.json
```

The authority order is: issued order / acceptable SKU / deadline / budget,
then certificate / food use window / grade and receipt guards, then likely
surplus and settlement chance. The default life mode observes purchases without adding any taste input to the
commercial selector. The existing model input and pricing path stay identical.
An explicitly internal `run(..., life_advice=True)` experiment can add fictional
tastes as advisory tie-breakers; the market CLI does not expose that switch.
Hunger, duplicate personal possessions and lack of a room or recipe cannot veto
an otherwise valid order. The experimental selector uses its original answer schema and one
model call; malformed replies fall back to the same commercial policy. Tastes
never enter later price negotiations and do not add fallback-shop selection
calls. Their presence can still influence a model's choice or opening offer;
internal paired probes measure that sensitivity rather than claiming the
wording guarantees unchanged surplus.

`buyer_life.py` keeps the persona and full ownership history on disk. Only a
trusted kit settlement with a matching receipt SKU, usable value and a positive
price becomes an owned lot. Signed but unsettled receipts do not. Settlements
that arrive before persona creation are queued locally and imported once after
initialization. A review only records the result; it never creates a persona,
runs the life model or waits for a game action. A life import failure does not
change the commercial review. The optional life planner also runs separately
from shop restocking, so it cannot delay a supply window.

The model proposes up to three actions: wait, plan a recipe, cook, place an
owned good, draft a request or draft an experience note. Tools require distinct,
owned and unexpired ingredient lots, including overlapping ingredient-name
queries; cooking consumes each lot once. Furniture placement checks ownership,
room boundaries, overlap and a reserved walkway. Food lots, hunger and furniture
footprints are fictional game units, not nutrition or product-dimension claims.
Ambiguous breakage reports remain uncertain; no seller or unit is invented.
Preferences can change no more than once an hour and must cite actual recent
acquisition/cooking/placement event IDs. Invalid preference updates do not undo
valid game actions from the same model response.

| Context / frequency | Bound |
|---|---|
| Extra selector taste advice | 1,200 UTF-8 bytes; dropped before any commercial candidate is removed |
| Life planning context | 7,500 UTF-8 bytes; full state remains on disk |
| Recent view | 16 owned units, 6 events, 6 relevant posts and 12 placement details; counts and full room occupancy remain visible |
| Life planning calls | At least 300 seconds apart, or 900 seconds for unchanged state |
| Drafting | At least 600 seconds apart; same topic at most once per day |

Drafts go only to `state/buyer-life-forum-drafts.jsonl`, each marked
`internal_draft`. The life module never writes `.market/outbox.jsonl` and has no
publisher. Fictional events are labeled and purchase notes reference their
settlement. The old review-note publisher is also disabled by default. Making
or reviewing a draft cannot send it later through the kit automatically.

Internal verification uses temporary worlds and synthetic order/receipt/ledger
fixtures. Existing catalog metadata is read from the local runtime snapshot;
there is no market join, shop request, checkout, payment or forum API call.
Model generation is the only intended external service call:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python experiments/buyer_life_probe.py \
  --output logs/buyer-life-internal-probe-2026-11-04.json
```

The paired selector cases cover unmatched food/goods tastes, forced epsilon exploration and an adversarial public review. They check
SKU/budget membership, one selector call, unchanged commercial guards and no
outbox writes. Unit checks also cover a full virtual room and duplicate
personal possessions without blocking an issued goods order. A separate model-driven life simulation checks cooking,
placement, persistence and draft-only routing with explicitly synthetic
settlements. Prompt bytes and elapsed time are measurable; exact token charges
and live surplus improvements are not established by these fixtures.

Internal results (November 4): 4 paired cases / 8 real selector calls all kept
SKU/budget guards and one call per selection; the selected candidate matched in
all four pairs. Advisory-versus-baseline opening offers differed by -500, -300,
+500 and 0 credits. These single pairs do not isolate model randomness from the
advice effect, and do not establish better surplus. Therefore advisory influence remains an
internal experimental option; the default observer mode adds zero purchase
prompt bytes and keeps the same commercial input. Additional serialized input
was 830 bytes per advisory variant in this probe (385 bytes of taste data plus
its instructions). The temporary model-driven world cooked, placed furniture
and made one internal draft; its life context was 5,012 bytes. Existing live
configuration, root persona and market outbox files were unchanged. Exact token
billing and real-market success remain unmeasured. The feature stays disabled.

The original probe failed because its assertion confused the last registry
entry with the last eligible candidate after commercial sorting. The assertion
now checks the actual epsilon-selected candidate ID; that failed run is kept in
`logs/buyer-life-internal-probe-2026-11-04.log`. The completed retry and structured
results are `logs/buyer-life-internal-probe-retry-2026-11-04.log` and
`logs/buyer-life-internal-probe-2026-11-04.json`.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python ../../../scripts/market_kit.py join --dir . --market https://market.ourolabs.ai \
  --name outseop-26510122 --tunnel
```

The join command above requires the same shop key and owner claim as the
existing market identity, and a reachable tunnel. Do not create a second live
shop alongside the week-07 supervisor. Neither keys nor claim codes belong in
Git. Check the public `/api/shops` round before running the economy hooks;
the restock command holds outside Round 3. Until live outcomes exist,
the grade multipliers and demand estimates are
experimental inputs rather than measured improvements.

## Known gaps to check at launch

The public [Round 3 contract](https://market.ourolabs.ai/agent.md) supplies the
inventory fields, supply grade/cost ranges, food grade cutoffs and goods storage
rule. An idealized local forecast/review ablation is not a live-market replay:
at the November 3 check the public API was still in Round 2 with no Round 3
entries in the latest 500 ledger rows. In the 18 synthetic demand cases, shorter
periodic review improved mean cash profit, while the faster forecast increased
fill rate but slightly reduced mean profit. Both short- and long-window evidence
are therefore shown to the model rather than automatically preferring the short
forecast. Python plots and simulation data remain in the ignored `flow/` folder.

The model probes use explicitly synthetic visits, settlements and stock with
public catalog metadata. The initial probes exposed excessive budget renewals;
approval now selects complete tool-calculated lease/budget alternatives, and
stable scenario renewals need only the final approval. A 20-hook probe made four
real model calls, versus seven scheduled calls under the old three-minute
cooldown. This checks call reuse and guards, not live service or profit.

- `inventory.json` can be ten seconds old. The seller rereads it before a quote
  and a receipt; the kit makes the final stock check during checkout.
- A unit's food grade can deteriorate during negotiation. The seller rereads
  it and does not make a fixed grade claim in the receipt. The kit attaches the
  market certificate for the unit by default.
- A signed receipt may fail checkout. Only `[acp] settled` is a confirmed sale.
- Shop visits are a selected sample of market demand. Replenishment forecasts
  must not treat them as all demand; early lots are deliberately small.
- Goods are eligible for both ongoing exploration and evidence-based replenishment.
  A full multi-period holding and warranty-loss model is still missing; the
  24-hour leftover-storage approximation is explicitly labeled in the prompt.
- The buyer policy has not yet been exercised against Round 3 live shops. Track
  `payments.jsonl` refusals and adjust the initial price shares, grade filter,
  food buffer and warranty preference from confirmed settlements.

### Six-hour goods warranty — November 5

`seller_warranty.py` defines six-hour coverage for new goods negotiations. The
total quoted price includes coverage; the seller discloses it in the opening
message and sends `warranty_hours=6` in the receipt artifact, as required by the
market checkout contract. Food receives no goods coverage. Requests exceeding
six hours fail validation. Existing negotiations and saved receipts retain their
original terms; the change does not retroactively cover old purchases.

The true failure probability is unpublished. A 10% refund-share scenario is an
operator planning assumption, not an estimated market failure rate. Quote tools
show margins under 0%, 5%, 10% and 20% full-price refunds. The covered price guard
is `ceil((purchase_cost + accrued_holding) / 0.9) + 1`; the model still selects the
quote and negotiation action. The opening discount cannot bypass this guard,
and the buyer must acknowledge the final price before a receipt is signed.

Procurement reserves 10% of still-covered, confirmed paid amounts as a cash
buffer. Distinct settlement ledgers are joined to their receipt terms; unpaid
signatures and expired coverage do not create that reserve. This is a scenario
buffer, not a guarantee against correlated failures. Opening, receipt and
settlement logs retain coverage hours, and forum facts disclose the new policy.


### Demand intake, sourcing and public quotes — November 7

The seller offers a condition-matching service rather than only repeating stock
advertisements. The forum editor can announce it every six hours, within the
existing post cadence and limits. Explicit buyer requests and replies to our
published intake posts are captured from fresh `forum.json` snapshots, including
requests we cannot currently fulfill. Public text remains untrusted data.

`seller_demand.py` stores raw revisions in `forum_demand_versions` and the latest
open state in `forum_demand`, inside the existing `seller.sqlite3`. Grade, budget,
and food consumption horizon are extracted conservatively; missing fields remain
explicit. Requests expire from active context after six hours. Explicit closed
revisions are retained but removed from sourcing. The current parser does not
merge arbitrary multi-message conversations or infer closure from unrelated
purchases. Category matches are alternative SKUs for one intent, not multiple
orders. For example, “커피/차, B급, 예산 9만 원대, 360시간 안에 소비” is a
lead with a 99,999-won ceiling and a 360-hour food requirement, not a sale.

Procurement policy v18 surfaces those original request IDs, conditions and raw
examples to the model, alongside the existing observed-demand forecast. Forum
intents do not increase numeric sales, visits or expected lost sales. The model
still chooses the actual basket under cash, exposure, category and supply limits.
`forum_request_ids` follows the approval and emitted intent so physical arrival
can be linked back to its source. The forum worker considers the original thread
again only when actual unleased stock meets its grade, shelf-life and warranty
requirements; price/budget agreement remains unconfirmed until negotiation.

`seller_featured.py` supplies real, unit-backed public price ceilings. The model
selects one or two offers per post, with at most three active at once. Proposals
cover acquisition cost, accrued holding, the receipt hold and the existing 10%
goods-refund planning scenario, then offer 10% or 20% margin choices. Only choices
at least 10% below the actual reference list price are eligible; this is not a
market-lowest claim. Food grade must remain stable through the fixed 30-minute
public window plus receipt hold. Publication revalidates the exact unit, grade,
price and deadline; the editor cannot invent or extend these figures.

The same SQLite database persists `featured_quotes`. A matching available unit
receives its capped opening quote, whether or not the customer includes the
`FO-…` code. An explicit code selects only its physical unit; unavailable or
expired codes cannot silently substitute a higher-priced item. Expiry during a
model call rejects the code rather than falling back to an ordinary price.
Already-issued negotiations retain their stored terms across restarts. Signature
and payment checks remain unchanged. Request, promotion and receipt logs record
the code when known; a signature is not a confirmed settlement. Queued forum
handoffs and publication acknowledgments are recorded separately.

Synthetic tests cover durable unmet requests, revised/closed requests, survey
comments, ambiguous product matching, approval-to-supply request IDs, physical
arrival before reply, public-price publication/ACK, checker rejection, dry runs,
sold/changed/expired units and real A2A quote-to-receipt persistence. These checks
validate mechanics, not incremental live demand or profit. The ignored personal
dashboard shows request-to-sourcing/reply links and active quote ceilings; it is
read-only and does not run the ingestion or trading hooks.
